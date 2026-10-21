import sys

import pytest

from ai_taxman.core.errors import ProviderDependencyError
from ai_taxman.providers.anthropic.provider import PROVIDER, AnthropicProvider


def test_declares_the_conventional_anthropic_variable():
    assert PROVIDER.default_api_key_env == "ANTHROPIC_API_KEY"


def test_known_models_are_pinned_snapshots_not_moving_aliases():
    """`claude-haiku-4-5` resolves to whatever is current; an audit wants the snapshot."""
    assert "claude-haiku-4-5-20251001" in PROVIDER.known_models()
    assert "claude-haiku-4-5" not in PROVIDER.known_models()


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


def test_max_tokens_says_thinking_counts_toward_it():
    above = " ".join(comment_above("max_tokens"))

    assert "thinking included" in above
    assert "Required: yes" in comment_above("max_tokens")


def test_effort_says_what_each_level_does():
    above = comment_above("effort")

    assert any(line.startswith("Options:  low ") for line in above), above
    assert any(line.startswith("max ") for line in above), above


def test_thinking_type_says_what_each_mode_does():
    above = comment_above("type")

    assert any(line.startswith("Options:  adaptive ") for line in above), above
    assert any(line.startswith("between_tools ") for line in above), above


def test_budget_tokens_says_it_needs_room_to_answer():
    above = " ".join(comment_above("budget_tokens"))

    assert "less than max_tokens" in above
    assert "integer, 1024 or more" in above


def test_the_newest_search_tool_is_the_default():
    assert "Default:  blank (web_search_20260318, the newest)" in comment_above("tool_version")


# -- the key comes from core, never from the environment -------------------


async def test_the_client_is_the_one_startup_was_given():
    provider = AnthropicProvider()

    await provider.startup(api_key="sk-ant-one")
    try:
        assert provider._require_client().api_key == "sk-ant-one"
    finally:
        await provider.shutdown()


async def test_an_ambient_auth_token_is_never_sent_alongside_the_key(monkeypatch):
    """The SDK reads ANTHROPIC_AUTH_TOKEN by default and sends it as a second credential."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ambient-must-not-be-used")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-token-must-not-be-used")
    provider = AnthropicProvider()

    await provider.startup(api_key="sk-ant-one")
    try:
        client = provider._require_client()
        assert client.api_key == "sk-ant-one"
        assert client.auth_token is None
    finally:
        await provider.shutdown()


async def test_a_missing_sdk_says_how_to_add_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(ProviderDependencyError, match=r"uv add ai-taxman\[anthropic\]"):
        await AnthropicProvider().startup(api_key="sk-ant-one")


# -- retries ---------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_name, retryable",
    [
        ("RateLimitError", True),
        ("OverloadedError", True),
        ("InternalServerError", True),
        ("ServiceUnavailableError", True),
        ("DeadlineExceededError", True),
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


def test_the_thinking_block_says_blank_means_the_models_default():
    assert "model's default" in " ".join(comment_above("thinking"))


def test_the_search_block_says_its_settings_need_web_search():
    above = " ".join(comment_above("search"))

    assert "web_search must be true to use any other setting in this block" in above
