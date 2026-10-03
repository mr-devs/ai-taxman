import asyncio

import pytest
import yaml

from ai_taxman.core import registry
from ai_taxman.core.errors import ProviderError
from ai_taxman.providers.base import Provider
from ai_taxman.providers.openai.config import TEMPLATE_FIELDS
from ai_taxman.providers.openai.models import DEFAULT_MODEL
from ai_taxman.providers.openai.provider import PROVIDER, OpenAIProvider


def test_registry_resolves_openai():
    assert isinstance(registry.get_provider("openai"), Provider)


def test_module_exports_a_provider_instance():
    assert isinstance(PROVIDER, OpenAIProvider)


def test_is_named_openai():
    assert PROVIDER.name == "openai"


def test_lists_known_models():
    models = PROVIDER.known_models()

    assert models
    assert any(name.startswith("gpt-5") for name in models)


def test_does_not_claim_batch_support_yet():
    assert PROVIDER.supports_batch is False


def test_template_is_valid_yaml_with_a_model_block():
    parsed = yaml.safe_load(PROVIDER.render_template())

    assert parsed["model"]["name"] == DEFAULT_MODEL


def test_template_names_a_model_this_provider_knows():
    parsed = yaml.safe_load(PROVIDER.render_template())

    assert parsed["model"]["name"] in PROVIDER.known_models()


def test_template_leaves_every_other_parameter_blank_as_documentation():
    """The scaffolded file is the documentation; the user fills in the blanks."""
    parsed = yaml.safe_load(PROVIDER.render_template())["model"]

    for key, _ in TEMPLATE_FIELDS:
        assert key in parsed, f"{key} is missing from the template"
        assert parsed[key] is None, f"{key} should be blank"


def test_every_template_line_carries_a_comment():
    body = PROVIDER.render_template().splitlines()[1:]

    assert all("#" in line for line in body if line.strip())


def test_max_output_tokens_says_what_it_counts():
    assert "  max_output_tokens:  # Maximum tokens per response." in PROVIDER.render_template()


def test_template_takes_no_arguments():
    with pytest.raises(TypeError):
        PROVIDER.render_template({"name": "gpt-4o"})


def test_template_output_validates_against_the_config_model():
    parsed = yaml.safe_load(PROVIDER.render_template())

    PROVIDER.validate_model_config(parsed["model"])


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


def test_unrelated_exceptions_are_not_retryable():
    assert PROVIDER.is_retryable(ValueError("nope")) is False


def _make_openai_error(name):
    """An instance of a real SDK error class, built without an HTTP round trip.

    `__new__` skips the constructors, which want live request/response objects
    from whichever HTTP library the SDK currently vendors.
    """
    import openai

    cls = getattr(openai, name)
    return cls.__new__(cls)


# -- the key comes from core, never from the environment -------------------


def test_a_client_is_never_built_from_an_ambient_key(monkeypatch):
    """CLAUDE.md: one named variable, no fallback chain.

    Without this, an audit naming TAXMAN_OPENAI_API_KEY could quietly bill
    whatever OPENAI_API_KEY happened to be exported, with nothing in the record
    to show which key paid.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient-must-not-be-used")
    provider = OpenAIProvider()

    with pytest.raises(ProviderError, match="startup"):
        provider._require_client()


async def test_the_client_is_the_one_startup_was_given():
    pytest.importorskip("openai")
    provider = OpenAIProvider()

    await provider.startup(api_key="sk-one")
    try:
        assert provider._require_client().api_key == "sk-one"
    finally:
        await provider.shutdown()


async def test_shutdown_clears_the_client():
    pytest.importorskip("openai")
    provider = OpenAIProvider()

    await provider.startup(api_key="sk-one")
    await provider.shutdown()

    with pytest.raises(ProviderError):
        provider._require_client()


async def test_two_concurrent_runs_do_not_share_a_client():
    """`get_provider` hands back one instance, so per-run state must not live on it.

    Two audits started together in one process - `asyncio.gather(run_audit_async(a),
    run_audit_async(b))` - would otherwise overwrite each other's client, so both
    would bill whichever key won the race, and the first to finish would close the
    client the other was still using.
    """
    pytest.importorskip("openai")
    provider = OpenAIProvider()
    seen: list[str] = []

    async def run(key: str) -> None:
        await provider.startup(api_key=key)
        await asyncio.sleep(0)  # give the other run a chance to clobber us
        seen.append(provider._require_client().api_key)
        await provider.shutdown()

    await asyncio.gather(run("sk-alpha"), run("sk-beta"))

    assert sorted(seen) == ["sk-alpha", "sk-beta"]
