"""What `send()` puts on the wire, checked against a fake transport - no network.

`build_request` is what the audit says was asked for; these prove it is also
exactly what went to Google, through the real SDK client `startup()` builds.
"""

import json

import pytest

from ai_taxman.core.messages import Message
from ai_taxman.providers.base import Request
from ai_taxman.providers.gemini.provider import GeminiProvider, build_request

httpx = pytest.importorskip("httpx")
api_client = pytest.importorskip("google.genai._api_client")

INTERACTION = {
    "id": "int_01",
    "status": "completed",
    "model": "gemini-3.8-flash",
    "role": "model",
    "created": "2026-10-03T00:00:00Z",
    "updated": "2026-10-03T00:00:00Z",
    "steps": [{"type": "model_output", "content": [{"type": "text", "text": "Hi."}]}],
    "usage": {"total_input_tokens": 3, "total_output_tokens": 2},
}


class Wire:
    """A fake Google: records every HTTP request, answers with `status`, or raises `error`."""

    def __init__(self):
        self.status = 200
        self.body = INTERACTION
        self.error = None
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error(f"{self.error.__name__} from the fake", request=request)
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def wire(monkeypatch):
    """Route the client `startup()` builds through a `Wire`, keeping every other setting."""
    fake = Wire()

    class FakeTransportClient(api_client.AsyncHttpxClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(fake)
            super().__init__(**kwargs)

    monkeypatch.setattr(api_client, "AsyncHttpxClient", FakeTransportClient)
    return fake


def a_request(block=None, system_prompt=None):
    config = GeminiProvider().validate_model_config(block or {"name": "gemini-3.8-flash"})
    message = Message(id="m0000", text="hello", hash="sha256:x", line_number=1)
    return Request(message=message, repeat=0, model=config, system_prompt=system_prompt)


async def send(request, *, api_key="gm-one", timeout_s=30.0):
    provider = GeminiProvider()
    await provider.startup(api_key=api_key)
    try:
        return await provider.send(request, timeout_s=timeout_s)
    finally:
        await provider.shutdown()


async def test_the_body_on_the_wire_is_exactly_what_build_request_made(wire):
    request = a_request()

    await send(request)

    assert json.loads(wire.requests[0].content) == build_request(request)


async def test_it_goes_to_the_interactions_endpoint(wire):
    await send(a_request())

    assert wire.requests[0].url.path.endswith("/interactions")


async def test_only_the_key_startup_was_given_is_sent(wire, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "ambient-gemini-key-must-not-be-used")
    monkeypatch.setenv("GOOGLE_API_KEY", "ambient-google-key-must-not-be-used")

    await send(a_request(), api_key="gm-one")

    assert wire.requests[0].headers["x-goog-api-key"] == "gm-one"


async def test_the_audit_timeout_is_the_request_timeout(wire):
    await send(a_request(), timeout_s=42.0)

    assert wire.requests[0].extensions["timeout"]["read"] == 42.0


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_an_error_status_is_one_http_call_so_attempts_stay_true(wire, status):
    """The SDK retries these by default; the runner retries too, and counts."""
    wire.status = status
    wire.body = {"error": {"code": status, "message": "no", "status": "UNAVAILABLE"}}

    with pytest.raises(Exception) as caught:
        await send(a_request())

    assert getattr(caught.value, "status_code", None) == status
    assert len(wire.requests) == 1


async def test_a_dropped_connection_is_one_http_call_too(wire):
    wire.error = httpx.ConnectError

    with pytest.raises(Exception, match="Connect"):
        await send(a_request())

    assert len(wire.requests) == 1


async def test_the_response_is_recorded_exactly_as_google_sent_it(wire):
    """The SDK's own object adds conveniences (`output_text`); `raw` must not."""
    raw = await send(a_request())

    assert raw == INTERACTION


async def test_a_field_the_sdk_does_not_know_is_still_recorded(wire):
    wire.body = {**INTERACTION, "brand_new_field": {"kept": True}}

    raw = await send(a_request())

    assert raw["brand_new_field"] == {"kept": True}


async def test_the_response_is_json_safe(wire):
    raw = await send(a_request())

    assert json.loads(json.dumps(raw)) == raw


@pytest.mark.parametrize(
    "status, retryable", [(429, True), (500, True), (503, True), (400, False), (403, False)]
)
async def test_what_send_raises_is_classified_by_status(wire, status, retryable):
    """The real exception, not a stand-in: the classes the SDK raises today."""
    wire.status = status
    wire.body = {"error": {"code": status, "message": "no", "status": "X"}}

    with pytest.raises(Exception) as caught:
        await send(a_request())

    assert GeminiProvider().is_retryable(caught.value) is retryable


@pytest.mark.parametrize("error", ["ConnectError", "ReadTimeout"])
async def test_a_dropped_or_slow_connection_is_retryable(wire, error):
    wire.error = getattr(httpx, error)

    with pytest.raises(Exception) as caught:
        await send(a_request())

    assert GeminiProvider().is_retryable(caught.value) is True
