import asyncio
import sys

import pytest
import yaml

from ai_taxman.core import registry
from ai_taxman.core.errors import ProviderDependencyError, ProviderError
from ai_taxman.providers.base import Provider
from ai_taxman.providers.gemini.config import GeminiModelConfig
from ai_taxman.providers.gemini.models import DEFAULT_MODEL
from ai_taxman.providers.gemini.provider import PROVIDER, GeminiProvider


def test_registry_resolves_gemini():
    assert isinstance(registry.get_provider("gemini"), Provider)


def test_module_exports_a_provider_instance():
    assert isinstance(PROVIDER, GeminiProvider)


def test_is_named_gemini():
    assert PROVIDER.name == "gemini"


def test_declares_the_conventional_gemini_variable():
    assert PROVIDER.default_api_key_env == "GEMINI_API_KEY"


def test_lists_known_models_with_the_default_first():
    models = PROVIDER.known_models()

    assert models[0] == DEFAULT_MODEL
    assert "gemini-2.5-flash" in models


def test_does_not_claim_batch_support_yet():
    assert PROVIDER.supports_batch is False


def test_template_names_the_default_model():
    parsed = yaml.safe_load(PROVIDER.render_template())

    assert parsed["model"]["name"] == DEFAULT_MODEL


def test_template_leaves_every_other_parameter_blank_as_documentation():
    parsed = yaml.safe_load(PROVIDER.render_template())["model"]

    for key in set(GeminiModelConfig.model_fields) - {"name", "extra", "search"}:
        assert key in parsed, f"{key} is missing from the template"
        assert parsed[key] is None, f"{key} should be blank"


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


def test_max_output_tokens_says_thinking_counts_toward_it():
    assert "thinking included" in " ".join(comment_above("max_output_tokens"))


def test_store_says_how_long_google_would_keep_it():
    above = " ".join(comment_above("store"))

    assert "55 days" in above
    assert "Default:  false" in comment_above("store")


def test_thinking_level_lists_every_documented_level():
    assert "Options:  minimal | low | medium | high" in comment_above("thinking_level")


# -- the key comes from core, never from the environment -------------------


@pytest.fixture
def ambient(monkeypatch):
    """Everything the Gemini SDK would read from the environment on its own."""
    monkeypatch.setenv("GEMINI_API_KEY", "ambient-gemini-key-must-not-be-used")
    monkeypatch.setenv("GOOGLE_API_KEY", "ambient-google-key-must-not-be-used")
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "true")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")


async def test_the_client_is_the_one_startup_was_given(ambient):
    provider = GeminiProvider()

    await provider.startup(api_key="gm-one")
    try:
        assert provider._require_client()._api_client.api_key == "gm-one"
    finally:
        await provider.shutdown()


async def test_an_ambient_switch_never_sends_the_audit_to_vertex(ambient):
    """GOOGLE_GENAI_USE_ENTERPRISE would move the audit to another API and another bill."""
    provider = GeminiProvider()

    await provider.startup(api_key="gm-one")
    try:
        assert provider._require_client()._api_client.vertexai is False
    finally:
        await provider.shutdown()


async def test_shutdown_clears_the_client():
    provider = GeminiProvider()

    await provider.startup(api_key="gm-one")
    await provider.shutdown()

    with pytest.raises(ProviderError):
        provider._require_client()


async def test_two_concurrent_runs_do_not_share_a_client():
    """`get_provider` hands back one instance, so per-run state must not live on it."""
    provider = GeminiProvider()
    seen: list[str] = []

    async def run(key: str) -> None:
        await provider.startup(api_key=key)
        await asyncio.sleep(0)  # give the other run a chance to clobber us
        seen.append(provider._require_client()._api_client.api_key)
        await provider.shutdown()

    await asyncio.gather(run("gm-alpha"), run("gm-beta"))

    assert sorted(seen) == ["gm-alpha", "gm-beta"]


async def test_a_missing_sdk_says_how_to_add_it(monkeypatch):
    """`google` is a namespace other packages share, so only `genai` is taken away."""
    monkeypatch.setitem(sys.modules, "google.genai", None)
    if "google" in sys.modules:
        monkeypatch.delattr(sys.modules["google"], "genai", raising=False)

    with pytest.raises(ProviderDependencyError, match=r"uv add ai-taxman\[gemini\]"):
        await GeminiProvider().startup(api_key="gm-one")


# -- retries ---------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_name, retryable",
    [
        ("RateLimitError", True),
        ("InternalServerError", True),
        ("ConflictError", True),
        ("APITimeoutError", True),
        ("APIConnectionError", True),
        ("BadRequestError", False),
        ("AuthenticationError", False),
        ("PermissionDeniedError", False),
        ("NotFoundError", False),
        ("UnprocessableEntityError", False),
    ],
)
def test_retryability_matches_the_sdk_error_class(exc_name, retryable):
    assert PROVIDER.is_retryable(_make_gemini_error(exc_name)) is retryable


def test_a_same_named_error_from_another_google_package_is_not_retried():
    """`google` is a namespace many packages share; only `google.genai` counts."""

    class RateLimitError(Exception):
        pass

    RateLimitError.__module__ = "google.api_core.exceptions"

    assert PROVIDER.is_retryable(RateLimitError()) is False


def _make_gemini_error(name):
    """An instance of a real SDK error class, built without an HTTP round trip."""
    compat_errors = pytest.importorskip("google.genai._gaos.lib.compat_errors")
    cls = getattr(compat_errors, name)
    return cls.__new__(cls)


def test_web_search_is_set_in_a_search_block_inside_the_model_block():
    parsed = yaml.safe_load(PROVIDER.render_template())["model"]

    assert "web_search" not in parsed
    assert parsed["search"]["web_search"] is None


def test_the_search_block_says_where_its_missing_settings_went():
    assert "docs/provider-apis/gemini.md" in " ".join(comment_above("search"))
