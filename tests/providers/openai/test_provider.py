import pytest

from ai_taxman.providers.openai.provider import PROVIDER, OpenAIProvider


def test_declares_the_conventional_openai_variable():
    assert PROVIDER.default_api_key_env == "OPENAI_API_KEY"


@pytest.mark.parametrize(
    "exc_name, retryable",
    [
        ("RateLimitError", True),
        ("APITimeoutError", True),
        ("APIConnectionError", True),
        ("InternalServerError", True),
        ("BadRequestError", False),
        ("AuthenticationError", False),
    ],
)
def test_retryability_matches_the_sdk_error_class(exc_name, retryable):
    exc = _make_openai_error(exc_name)

    assert PROVIDER.is_retryable(exc) is retryable


def _make_openai_error(name):
    """An instance of a real SDK error class, built without an HTTP round trip.

    `__new__` skips the constructors, which want live request/response objects
    from whichever HTTP library the SDK currently vendors.
    """
    import openai

    cls = getattr(openai, name)
    return cls.__new__(cls)


# -- the key comes from core, never from the environment -------------------


async def test_the_client_is_the_one_startup_was_given():
    pytest.importorskip("openai")
    provider = OpenAIProvider()

    await provider.startup(api_key="sk-one")
    try:
        assert provider._require_client().api_key == "sk-one"
    finally:
        await provider.shutdown()


def rate_limited(headers):
    """A 429 as the SDK raises it, around a response that never crossed a network."""
    import httpx2
    import openai

    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx2.Response(429, headers=headers, request=request)
    return openai.RateLimitError("Rate limit reached", response=response, body=None)


def test_an_error_gives_its_status_and_the_request_id_support_asks_for():
    assert PROVIDER.error_details(rate_limited({"x-request-id": "req_123"})) == {
        "status": "429",
        "request_id": "req_123",
    }


@pytest.mark.parametrize(
    "headers, wait",
    [
        ({"retry-after": "7"}, 7.0),
        ({"retry-after": "Fri, 31 Dec 1999 23:59:59 GMT"}, None),
        ({}, None),
    ],
    ids=["seconds", "not-seconds", "none"],
)
def test_a_retry_after_is_read_in_the_seconds_the_docs_promise(headers, wait):
    assert PROVIDER.retry_after(rate_limited(headers)) == wait
