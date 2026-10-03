import asyncio
import sys

import pytest
import yaml

from ai_taxman.core import registry
from ai_taxman.core.errors import ProviderDependencyError, ProviderError
from ai_taxman.providers.anthropic.config import (
    DEFAULT_MAX_TOKENS,
    AnthropicModelConfig,
    AnthropicSearchConfig,
    AnthropicThinking,
)
from ai_taxman.providers.anthropic.models import DEFAULT_MODEL
from ai_taxman.providers.anthropic.provider import PROVIDER, AnthropicProvider
from ai_taxman.providers.base import Provider


def test_registry_resolves_anthropic():
    assert isinstance(registry.get_provider("anthropic"), Provider)


def test_module_exports_a_provider_instance():
    assert isinstance(PROVIDER, AnthropicProvider)


def test_is_named_anthropic():
    assert PROVIDER.name == "anthropic"


def test_declares_the_conventional_anthropic_variable():
    assert PROVIDER.default_api_key_env == "ANTHROPIC_API_KEY"


def test_lists_known_models_with_the_default_first():
    models = PROVIDER.known_models()

    assert models[0] == DEFAULT_MODEL
    assert "claude-sonnet-5-5" in models


def test_known_models_are_pinned_snapshots_not_moving_aliases():
    """`claude-haiku-4-5` resolves to whatever is current; an audit wants the snapshot."""
    assert "claude-haiku-4-5-20251001" in PROVIDER.known_models()
    assert "claude-haiku-4-5" not in PROVIDER.known_models()


def test_does_not_claim_batch_support_yet():
    assert PROVIDER.supports_batch is False


def test_template_names_the_default_model():
    parsed = yaml.safe_load(PROVIDER.render_template())

    assert parsed["model"]["name"] == DEFAULT_MODEL


def test_template_fills_in_max_tokens_because_anthropic_requires_it():
    parsed = yaml.safe_load(PROVIDER.render_template())

    assert parsed["model"]["max_tokens"] == DEFAULT_MAX_TOKENS


def test_template_leaves_every_other_parameter_blank_as_documentation():
    parsed = yaml.safe_load(PROVIDER.render_template())["model"]

    for key in set(AnthropicModelConfig.model_fields) - {
        "name",
        "max_tokens",
        "extra",
        "thinking",
        "search",
    }:
        assert key in parsed, f"{key} is missing from the template"
        assert parsed[key] is None, f"{key} should be blank"


def test_every_search_setting_is_in_the_template():
    parsed = yaml.safe_load(PROVIDER.render_template())["model"]["search"]

    assert set(parsed) == set(AnthropicSearchConfig.model_fields)


def test_the_extra_escape_hatch_stays_out_of_the_template():
    assert "extra" not in yaml.safe_load(PROVIDER.render_template())["model"]


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


@pytest.mark.parametrize(
    "key",
    [key for key in AnthropicModelConfig.model_fields if key not in ("extra", "thinking", "search")]
    + list(AnthropicThinking.model_fields)
    + [key for key in AnthropicSearchConfig.model_fields if key != "user_location"],
)
def test_every_setting_says_what_it_is_when_left_alone(key):
    above = comment_above(key)

    assert any(line.startswith(("Default:", "Required:")) for line in above), above


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
    assert "whole number, 1024 or more" in above


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


async def test_shutdown_clears_the_client():
    provider = AnthropicProvider()

    await provider.startup(api_key="sk-ant-one")
    await provider.shutdown()

    with pytest.raises(ProviderError):
        provider._require_client()


async def test_two_concurrent_runs_do_not_share_a_client():
    """`get_provider` hands back one instance, so per-run state must not live on it."""
    provider = AnthropicProvider()
    seen: list[str] = []

    async def run(key: str) -> None:
        await provider.startup(api_key=key)
        await asyncio.sleep(0)  # give the other run a chance to clobber us
        seen.append(provider._require_client().api_key)
        await provider.shutdown()

    await asyncio.gather(run("sk-ant-alpha"), run("sk-ant-beta"))

    assert sorted(seen) == ["sk-ant-alpha", "sk-ant-beta"]


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


def test_thinking_is_set_in_a_block_inside_the_model_block():
    parsed = yaml.safe_load(PROVIDER.render_template())["model"]

    assert parsed["thinking"] == {"type": None, "budget_tokens": None, "display": None}


def test_the_thinking_block_says_blank_means_the_models_default():
    assert "model's default" in " ".join(comment_above("thinking"))


def test_web_search_is_set_in_a_search_block_inside_the_model_block():
    parsed = yaml.safe_load(PROVIDER.render_template())["model"]

    assert "web_search" not in parsed
    assert parsed["search"]["web_search"] is None


def test_the_search_block_says_its_settings_need_web_search():
    above = " ".join(comment_above("search"))

    assert "web_search must be true to use any other setting in this block" in above
