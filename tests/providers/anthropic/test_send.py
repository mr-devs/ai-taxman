"""What `send()` puts on the wire, checked against a fake transport - no network.

`build_request` is what the audit says was asked for; these prove it is also
exactly what went to Anthropic, through the real SDK client `startup()` builds.
"""

import functools
import json

import pytest

from ai_taxman.core.messages import Message
from ai_taxman.providers.anthropic.provider import AnthropicProvider, build_request
from ai_taxman.providers.base import Request

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

MESSAGE = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Hi."}],
    "model": "claude-opus-5-5",
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 9, "output_tokens": 3},
}


class Wire:
    """A fake Anthropic: records every HTTP request, answers with `status`."""

    def __init__(self, status=200, body=None):
        self.status = status
        self.body = MESSAGE if body is None else body
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body)


@pytest.fixture
def wire(monkeypatch):
    """Route the client `startup()` builds through a `Wire`, keeping every other setting."""
    fake = Wire()
    transport = httpx2.MockTransport(fake)
    monkeypatch.setattr(
        anthropic,
        "AsyncAnthropic",
        functools.partial(
            anthropic.AsyncAnthropic,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=transport),
        ),
    )
    return fake


def a_request(block=None, system_prompt=None):
    config = AnthropicProvider().validate_model_config(
        block or {"name": "claude-opus-5-5", "max_tokens": 1024}
    )
    message = Message(id="m0000", text="hello", hash="sha256:x", line_number=1)
    return Request(message=message, repeat=0, model=config, system_prompt=system_prompt)


async def send(request, *, api_key="sk-ant-one", timeout_s=30.0):
    provider = AnthropicProvider()
    await provider.startup(api_key=api_key)
    try:
        return await provider.send(request, timeout_s=timeout_s)
    finally:
        await provider.shutdown()


async def test_the_body_on_the_wire_is_exactly_what_build_request_made(wire):
    request = a_request()

    await send(request)

    assert json.loads(wire.requests[0].content) == build_request(request)


async def test_only_the_key_startup_was_given_is_sent(wire, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ambient-must-not-be-used")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-token-must-not-be-used")

    await send(a_request(), api_key="sk-ant-one")

    headers = wire.requests[0].headers
    assert headers["x-api-key"] == "sk-ant-one"
    assert "authorization" not in headers


async def test_the_audit_timeout_is_the_request_timeout(wire):
    await send(a_request(), timeout_s=42.0)

    assert wire.requests[0].extensions["timeout"]["read"] == 42.0


async def test_a_failure_is_one_http_call_so_attempts_stay_true(wire):
    """The runner retries; an SDK retry underneath would hide extra calls from `attempts`."""
    wire.status = 429
    wire.body = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}

    with pytest.raises(anthropic.RateLimitError):
        await send(a_request())

    assert len(wire.requests) == 1


async def test_a_large_max_tokens_is_sent_rather_than_refused(wire):
    """Without an explicit timeout the SDK refuses this as 'may take over 10 minutes'."""
    await send(a_request({"name": "claude-opus-5-5", "max_tokens": 128000}))

    assert json.loads(wire.requests[0].content)["max_tokens"] == 128000


async def test_the_response_is_recorded_exactly_as_anthropic_sent_it(wire):
    """No field the SDK merely knows about is added: `raw` is the body that came back."""
    raw = await send(a_request())

    assert raw == MESSAGE


async def test_a_field_the_sdk_does_not_know_is_still_recorded(wire):
    """Anthropic adds fields before the SDK learns them; an audit must not lose them."""
    wire.body = {**MESSAGE, "brand_new_field": {"kept": True}}

    raw = await send(a_request())

    assert raw["brand_new_field"] == {"kept": True}


async def test_the_response_is_json_safe(wire):
    raw = await send(a_request())

    assert json.loads(json.dumps(raw)) == raw
