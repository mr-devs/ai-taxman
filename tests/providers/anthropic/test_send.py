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
    message = Message(id="m0000", text="hello", line_number=1)
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


async def test_every_setting_reaches_the_wire_whichever_way_the_sdk_takes_it(wire):
    """`send` passes what the installed SDK names as keywords and the rest in
    `extra_body`, so which is which moves with the SDK version. The body must not."""
    block = {
        "name": "claude-opus-5-5",
        "max_tokens": 4096,
        "effort": "low",
        "temperature": 0.5,
        "top_p": 0.9,
        "top_k": 40,
        "thinking": {"type": "enabled", "budget_tokens": 2048, "display": "summarized"},
        "search": {
            "web_search": True,
            "tool_version": "web_search_20260318",
            "max_uses": 2,
            "allowed_domains": ["cdc.gov"],
            "user_location": {"city": "Minneapolis", "country": "US"},
            "allowed_callers": ["direct"],
            "response_inclusion": "full",
            "tool_choice": "auto",
        },
        "extra": {
            "metadata": {"user_id": "audit"},
            "output_config": {"format": {"type": "json_schema", "schema": {"type": "object"}}},
            "brand_new": {"x": 1},
        },
    }
    request = a_request(block, system_prompt="Answer in one word.")

    await send(request)

    assert json.loads(wire.requests[0].content) == build_request(request)


async def test_only_the_key_startup_was_given_is_sent(wire, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ambient-must-not-be-used")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-token-must-not-be-used")

    await send(a_request(), api_key="sk-ant-one")

    headers = wire.requests[0].headers
    assert headers["x-api-key"] == "sk-ant-one"
    assert "authorization" not in headers


async def test_progress_updates_send_the_beta_header_they_need(wire):
    """Without it Anthropic rejects `display: updates` as an unknown value."""
    block = {
        "name": "claude-opus-5-5",
        "max_tokens": 1024,
        "thinking": {"type": "adaptive", "display": "updates"},
    }

    await send(a_request(block))

    assert wire.requests[0].headers["anthropic-beta"] == "thinking-display-updates-2026-08-18"


async def test_no_beta_header_is_sent_for_a_display_out_of_beta(wire):
    block = {
        "name": "claude-opus-5-5",
        "max_tokens": 1024,
        "thinking": {"type": "adaptive", "display": "summarized"},
    }

    await send(a_request(block))

    assert "anthropic-beta" not in wire.requests[0].headers


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


#: Anthropic's own words, from its rate limits page.
SPEND_CAP = {
    "type": "rate_limit_error",
    "message": "You have reached your API usage limits: your organization has crossed its "
    "monthly API usage threshold, set based on your organization's API tier. You will "
    "regain access on 2026-09-01 at 00:00 UTC.",
    "details": {"error_code": "enforced_spend_limit_reached"},
}
RATE_LIMIT = {
    "type": "rate_limit_error",
    "message": "Number of requests has exceeded your rate limit",
}
BILLING = {"type": "billing_error", "message": "There is an issue with your billing."}
#: A limit set in the Console, below the tier's cap: a 400 that only its message
#: tells from a bad request. The docs give how it starts, and that it says when.
OWN_LIMIT = {
    "type": "invalid_request_error",
    "message": "You have reached your specified API usage limits. You will regain access "
    "on 2026-11-01 at 00:00 UTC.",
}
OWN_WORKSPACE_LIMIT = {
    "type": "invalid_request_error",
    "message": "You have reached your specified workspace API usage limits. You will "
    "regain access on 2026-11-01 at 00:00 UTC.",
}
BAD_REQUEST = {"type": "invalid_request_error", "message": "max_tokens: Field required"}


@pytest.mark.parametrize(
    "status, error, used_up",
    [
        (429, SPEND_CAP, True),
        (429, RATE_LIMIT, False),
        (402, BILLING, True),
        (400, OWN_LIMIT, True),
        (400, OWN_WORKSPACE_LIMIT, True),
        (400, BAD_REQUEST, False),
    ],
    ids=["spend-cap", "rate-limit", "billing", "own-limit", "own-workspace-limit", "bad-request"],
)
async def test_what_will_fail_until_it_is_resolved_stops_the_run(wire, status, error, used_up):
    """The tier's monthly spend cap is a 429 like a rate limit, without a
    `retry-after`, and "keeps failing until access resumes" - the 1st of the
    next month. Retried, it fails again, as does every other request. So does
    every request after a 402, until the billing details are fixed, and after
    a spend limit the organization set itself, until it is raised."""
    wire.status = status
    wire.body = {"type": "error", "error": error, "request_id": "req_01"}

    with pytest.raises(anthropic.APIStatusError) as caught:
        await send(a_request())

    assert AnthropicProvider().is_quota_exhausted(caught.value) is used_up
    assert AnthropicProvider().is_retryable(caught.value) is (status == 429 and not used_up)


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


async def test_a_parameter_newer_than_the_sdk_still_reaches_anthropic(wire):
    """`extra:` exists for exactly this; the SDK's own signature must not refuse it."""
    request = a_request(
        {"name": "claude-opus-5-5", "max_tokens": 1024, "extra": {"brand_new": {"x": 1}}}
    )

    await send(request)

    assert json.loads(wire.requests[0].content) == build_request(request)


async def test_extra_cannot_reach_the_sdk_own_request_options(wire):
    """`extra:` describes the request body; it never changes the audit's timeout."""
    request = a_request({"name": "claude-opus-5-5", "max_tokens": 1024, "extra": {"timeout": 1}})

    await send(request, timeout_s=42.0)

    assert wire.requests[0].extensions["timeout"]["read"] == 42.0
    assert json.loads(wire.requests[0].content)["timeout"] == 1
