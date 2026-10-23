import sys

import pytest

from ai_taxman.core.errors import ProviderDependencyError
from ai_taxman.providers.anthropic.provider import PROVIDER, AnthropicProvider


def test_declares_the_conventional_anthropic_variable():
    assert PROVIDER.default_api_key_env == "ANTHROPIC_API_KEY"


def comment_above(key):
    """The comment above `key:` in the template, at any depth, without its `#`s."""
    lines = PROVIDER.render_template().splitlines()
    index = next(i for i, line in enumerate(lines) if line.lstrip().startswith(f"{key}:"))
    above = []
    for line in reversed(lines[:index]):
        if not line.lstrip().startswith("#"):
            break
        above.insert(0, line.strip().removeprefix("#").strip())
    return above


def test_the_newest_search_tool_is_the_default():
    """Wording, kept on purpose: the only test of what the file says a blank one means."""
    assert "Default:  blank (web_search_20260318, the newest)" in comment_above("tool_version")


# -- the key comes from core, never from the environment -------------------


async def test_a_missing_sdk_says_how_to_add_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(ProviderDependencyError, match=r"uv add ai-taxman\[anthropic\]"):
        await AnthropicProvider().startup(api_key="sk-ant-one")


# -- retries ---------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_name, retryable",
    [
        ("InternalServerError", True),
        ("ConflictError", True),
        ("APITimeoutError", True),
        ("APIConnectionError", True),
        ("BadRequestError", False),
        ("AuthenticationError", False),
        ("PermissionDeniedError", False),
        ("NotFoundError", False),
        ("RequestTooLargeError", False),
    ],
)
def test_retryability_matches_the_sdk_error_class(exc_name, retryable):
    assert PROVIDER.is_retryable(_make_anthropic_error(exc_name)) is retryable


def test_every_rate_limit_or_server_error_class_is_retried():
    """529 `overloaded_error` is a sibling of InternalServerError, not a subclass.

    So is every other 5xx class the SDK has grown; a new one must not slip
    through as "not retryable" and fail a row that a retry would have saved.
    """
    anthropic = pytest.importorskip("anthropic")
    for name, cls in vars(anthropic).items():
        if not (isinstance(cls, type) and issubclass(cls, anthropic.APIStatusError)):
            continue
        status = getattr(cls, "status_code", None)
        if status == 429 or (isinstance(status, int) and status >= 500):
            assert PROVIDER.is_retryable(cls.__new__(cls)), name


def test_a_same_named_error_from_elsewhere_is_not_retried():
    class RateLimitError(Exception):
        pass

    assert PROVIDER.is_retryable(RateLimitError()) is False


def _make_anthropic_error(name):
    """An instance of a real SDK error class, built without an HTTP round trip."""
    anthropic = pytest.importorskip("anthropic")
    cls = getattr(anthropic, name)
    return cls.__new__(cls)


def rate_limited(headers):
    """A 429 as the SDK raises it, around a response that never crossed a network."""
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(429, headers=headers, request=request)
    return anthropic.RateLimitError("rate_limit_error", response=response, body=None)


def test_an_error_gives_its_status_and_the_request_id_support_asks_for():
    assert PROVIDER.error_details(rate_limited({"request-id": "req_123"})) == {
        "status": "429",
        "request_id": "req_123",
    }
