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
