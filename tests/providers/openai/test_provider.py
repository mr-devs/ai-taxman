import asyncio

import pytest
import yaml

from ai_taxman.core import registry
from ai_taxman.core.errors import ProviderError
from ai_taxman.providers.base import Provider
from ai_taxman.providers.openai.config import OpenAIModelConfig, OpenAISearchConfig
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


def comment_above(key, text=None):
    """The comment above `key:` in the template, at any depth, without its `#`s."""
    lines = (text or PROVIDER.render_template()).splitlines()
    index = next(i for i, line in enumerate(lines) if line.lstrip().startswith(f"{key}:"))
    above = []
    for line in reversed(lines[:index]):
        if not line.lstrip().startswith("#"):
            break
        above.insert(0, line.strip().removeprefix("#").strip())
    return above


def test_template_leaves_every_other_parameter_blank_as_documentation():
    """The scaffolded file is the documentation; the user fills in the blanks."""
    parsed = yaml.safe_load(PROVIDER.render_template())["model"]

    for key in set(OpenAIModelConfig.model_fields) - {"name", "extra", "search"}:
        assert key in parsed, f"{key} is missing from the template"
        assert parsed[key] is None, f"{key} should be blank"


def test_every_search_setting_is_in_the_template():
    parsed = yaml.safe_load(PROVIDER.render_template())["model"]["search"]

    assert set(parsed) == set(OpenAISearchConfig.model_fields)


def test_the_extra_escape_hatch_stays_out_of_the_template():
    assert "extra" not in yaml.safe_load(PROVIDER.render_template())["model"]


@pytest.mark.parametrize(
    "key",
    [key for key in OpenAIModelConfig.model_fields if key not in ("extra", "search")]
    + [k for k in OpenAISearchConfig.model_fields if k not in ("user_location", "image_settings")],
)
def test_every_setting_says_what_it_is_when_left_alone(key):
    above = comment_above(key)

    assert any(line.startswith(("Default:", "Required:")) for line in above), above


def test_max_output_tokens_says_what_it_counts():
    assert "reasoning tokens included" in " ".join(comment_above("max_output_tokens"))


def test_a_blank_sampling_setting_leaves_the_models_default():
    assert "Default:  blank (not sent, so the model's own default applies)" in comment_above(
        "temperature"
    )


def test_reasoning_effort_lists_every_level_openai_documents():
    assert "Options:  none | minimal | low | medium | high | xhigh | max" in comment_above(
        "reasoning_effort"
    )


def test_include_says_what_each_option_records():
    above = comment_above("include")

    assert any(line.startswith("Options:  web_search_call.action.sources") for line in above)
    assert "Default:  [web_search_call.action.sources]" in above


def test_store_is_off_unless_the_user_turns_it_on():
    assert "Default:  false" in comment_above("store")


def test_web_search_is_set_in_a_search_block_inside_the_model_block():
    parsed = yaml.safe_load(PROVIDER.render_template())["model"]

    assert "web_search" not in parsed
    assert parsed["search"]["web_search"] is None


def test_the_search_block_says_its_settings_need_web_search():
    above = " ".join(comment_above("search"))

    assert "web_search must be true to use any other setting in this block" in above


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
