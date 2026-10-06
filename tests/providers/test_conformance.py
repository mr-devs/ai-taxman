"""What every provider must do, checked against every provider that exists.

Parametrized over the registry, so a new `providers/<name>/` folder is held to
this the moment it exists - there is no list here to remember to extend. Each
provider's own tests cover what its API does; these cover what core relies on.
"""

import inspect

import pytest
import yaml

from ai_taxman.core import registry
from ai_taxman.core.errors import ProviderError
from ai_taxman.core.messages import Message
from ai_taxman.core.template import SENTENCE_END
from ai_taxman.providers.base import Provider, Request

PROVIDERS = registry.available_providers()


@pytest.fixture(params=PROVIDERS)
def provider(request):
    return registry.get_provider(request.param)


def template_block(provider):
    return yaml.safe_load(provider.render_template())["model"]


def test_every_provider_is_checked():
    assert "openai" in PROVIDERS


def test_is_a_provider_named_after_its_folder(provider, request):
    assert isinstance(provider, Provider)
    assert provider.name == request.node.callspec.params["provider"]


def test_names_the_variable_its_sdk_conventionally_reads(provider):
    """The hint `audits new` writes into a comment; core never resolves a key from it."""
    if provider.requires_api_key:
        assert provider.default_api_key_env


def test_offers_some_model_names(provider):
    models = provider.known_models()

    assert models
    assert all(isinstance(name, str) and name for name in models)


def test_render_template_takes_no_arguments(provider):
    parameters = inspect.signature(type(provider).render_template).parameters

    assert list(parameters) == ["self"]


def test_template_is_one_model_block(provider):
    parsed = yaml.safe_load(provider.render_template())

    assert list(parsed) == ["model"]


def test_template_validates_as_written(provider):
    """`audits new` output must run as-is, before the user edits a thing."""
    config = provider.validate_model_config(template_block(provider))

    assert provider.describe_model(config) in provider.known_models()


def unexplained_settings(template):
    """Each setting in `template` that is not explained in full by the comment above it.

    In full means the comment says what the setting does and, unless it is a block
    of further settings, what it is when left alone.
    """
    lines = template.splitlines()[1:]
    missing = []
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        above = []
        for previous in reversed(lines[:index]):
            if not previous.lstrip().startswith("#"):
                break
            above.insert(0, previous.strip().removeprefix("#").strip())
        following = next((later for later in lines[index + 1 :] if later.strip()), "")
        is_block = following.startswith(" " * (len(line) - len(line.lstrip()) + 2))
        says_default = any(text.startswith(("Default:", "Required:")) for text in above)
        if not above or "#" in line or not (is_block or says_default):
            missing.append(line.strip())
    return missing


def test_every_template_setting_explains_itself_in_full(provider):
    """`render_block` writes this; a hand-written template would have to match it."""
    assert unexplained_settings(provider.render_template()) == []


def long_explanations(template):
    """Each setting whose explanation, the `# ...` lines above it, runs past two sentences."""
    lines = template.splitlines()
    long = []
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        sentences = []
        for previous in reversed(lines[:index]):
            if not previous.lstrip().startswith("#"):
                break
            if not previous.lstrip().startswith("#  "):
                sentences.insert(0, previous.lstrip().removeprefix("# "))
        if len(SENTENCE_END.split(" ".join(sentences))) > 2:
            long.append(line.strip())
    return long


def test_every_template_setting_is_explained_in_two_sentences_at_most(provider):
    """Short enough to read at a glance; the docs link carries the rest."""
    assert long_explanations(provider.render_template()) == []


def test_the_template_points_only_to_what_an_installed_user_has(provider):
    """`uv tool install` ships no repo, so a template must not send its reader into one."""
    assert "docs/provider-apis" not in provider.render_template()


def test_an_inline_comment_is_not_an_explanation():
    """The old style: a one-line hint beside the key says nothing about blank."""
    assert unexplained_settings("model:\n  temperature:  # 0.0 - 2.0\n") == [
        "temperature:  # 0.0 - 2.0"
    ]


def test_an_unknown_setting_is_refused(provider):
    """A typo must fail at validation, not be silently ignored for a whole run."""
    block = {**template_block(provider), "not_a_real_setting": 1}

    with pytest.raises(ValueError):
        provider.validate_model_config(block)


def test_the_model_block_takes_no_system_prompt(provider):
    """The system prompt is the audit's own `system_prompt:`, read by core."""
    block = {**template_block(provider), "system_prompt": "Be terse."}

    with pytest.raises(ValueError):
        provider.validate_model_config(block)


def test_an_unrelated_exception_is_not_retried(provider):
    assert provider.is_retryable(ValueError("nope")) is False


async def test_send_before_startup_never_uses_an_ambient_key(provider, monkeypatch):
    """One named variable, no fallback chain: the key only ever arrives via startup()."""
    if provider.default_api_key_env:
        monkeypatch.setenv(provider.default_api_key_env, "ambient-key-must-not-be-used")
    fresh = type(provider)()
    config = fresh.validate_model_config(template_block(fresh))
    message = Message(id="m0000", text="hello", line_number=1)

    with pytest.raises(ProviderError, match="startup"):
        await fresh.send(Request(message=message, repeat=0, model=config), timeout_s=1.0)
