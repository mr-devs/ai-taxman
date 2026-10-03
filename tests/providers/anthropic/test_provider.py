import asyncio
import sys

import pytest
import yaml

from ai_taxman.core import registry
from ai_taxman.core.errors import ProviderDependencyError, ProviderError
from ai_taxman.providers.anthropic.config import DEFAULT_MAX_TOKENS, TEMPLATE_FIELDS
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

    for key, _ in TEMPLATE_FIELDS:
        assert key in parsed, f"{key} is missing from the template"
        assert parsed[key] is None, f"{key} should be blank"


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
