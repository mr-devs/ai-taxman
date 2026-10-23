"""What every provider must do, checked against every provider that exists.

Parametrized over the registry, so a new `providers/<name>/` folder is held to
this the moment it exists - there is no list here to remember to extend. Each
provider's own tests cover what its API does; these cover what core relies on.

The `extra:` tests also call the provider module's own `build_request`, which
CLAUDE.md's pure / IO split requires every provider to have: what `extra:` may
add to a request is shared code, so it is checked here once for all of them.
"""

import asyncio
import copy
import inspect
import sys
from typing import get_args

import pytest
import yaml
from pydantic import BaseModel

from ai_taxman.core import registry
from ai_taxman.core.errors import ProviderError
from ai_taxman.core.messages import Message
from ai_taxman.core.template import SENTENCE_END
from ai_taxman.providers.base import Provider, Request
from ai_taxman.providers.settings import MODEL_NAME_PLACEHOLDER

PROVIDERS = registry.available_providers()

#: The model the tests name in each template, where the user would name theirs.
MODEL = "a-model-taxman-never-heard-of"


@pytest.fixture(params=PROVIDERS)
def provider(request):
    return registry.get_provider(request.param)


def as_written(provider):
    """The `model:` block exactly as `audits new` writes it."""
    return yaml.safe_load(provider.render_template())["model"]


def template_block(provider):
    """The template's `model:` block once the user has named a model, the one
    setting `audits new` leaves for them. Found by its placeholder, not its key."""
    return {
        key: MODEL if value == MODEL_NAME_PLACEHOLDER else value
        for key, value in as_written(provider).items()
    }


def test_every_provider_is_checked():
    assert "openai" in PROVIDERS


def test_is_a_provider_named_after_its_folder(provider, request):
    assert isinstance(provider, Provider)
    assert provider.name == request.node.callspec.params["provider"]


def test_names_the_variable_its_sdk_conventionally_reads(provider):
    """The hint `audits new` writes into a comment; core never resolves a key from it."""
    if provider.requires_api_key:
        assert provider.default_api_key_env


def test_render_template_takes_no_arguments(provider):
    parameters = inspect.signature(type(provider).render_template).parameters

    assert list(parameters) == ["self"]


def test_template_is_one_model_block(provider):
    parsed = yaml.safe_load(provider.render_template())

    assert list(parsed) == ["model"]


def test_the_template_leaves_the_model_for_the_user_to_name(provider):
    """Which model is current changes faster than taxman could ship a default, so
    an unedited template must stop before anything is sent, and say why."""
    with pytest.raises(ValueError, match=MODEL_NAME_PLACEHOLDER):
        provider.validate_model_config(as_written(provider))


def test_the_template_validates_once_the_model_is_named(provider):
    config = provider.validate_model_config(template_block(provider))

    assert provider.describe_model(config) == MODEL


def nested_block(annotation):
    """The settings model a field holds, if it is a nested block rather than a value."""
    for candidate in (annotation, *get_args(annotation)):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def settings_tree(model):
    """What the template must show for `model`: every setting, filled in only if required."""
    tree = {}
    for key, field in model.model_fields.items():
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        if extra.get("template", True) is False:
            continue
        block = nested_block(field.annotation)
        if block is not None:
            tree[key] = settings_tree(block)
        else:
            tree[key] = "filled in" if field.is_required() else "blank"
    return tree


def template_tree(block):
    """What a parsed template shows: each setting blank, filled in, or a nested block."""
    return {
        key: template_tree(value)
        if isinstance(value, dict)
        else ("blank" if value is None else "filled in")
        for key, value in block.items()
    }


def test_the_template_shows_every_setting_and_fills_in_only_the_required(provider):
    """The file is the documentation: a setting left out of it is one nobody finds."""
    block = template_block(provider)
    model = type(provider.validate_model_config(block))

    assert template_tree(block) == settings_tree(model)


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


def test_a_model_it_does_not_know_is_accepted(provider):
    """Providers ship models faster than taxman could list them."""
    template = template_block(provider)
    named = provider.describe_model(provider.validate_model_config(template))
    key = next(key for key, value in template.items() if value == named)

    config = provider.validate_model_config({**template, key: "a-model-taxman-never-heard-of"})

    assert provider.describe_model(config) == "a-model-taxman-never-heard-of"


def nested_paths(block, path=()):
    """The path to every block of settings in `block`, itself included."""
    yield path
    for key, value in block.items():
        if isinstance(value, dict):
            yield from nested_paths(value, (*path, key))


def with_setting(block, path, key, value):
    """A copy of `block` with `key: value` added inside the block at `path`."""
    changed = copy.deepcopy(block)
    inner = changed
    for step in path:
        inner = inner[step]
    inner[key] = value
    return changed


def refuses_by_name(provider, block, name):
    """Whether validating `block` fails with an error that says `name`."""
    try:
        provider.validate_model_config(block)
    except ValueError as error:
        return name in str(error)
    return False


def test_an_unknown_setting_is_refused(provider):
    """A typo must fail at validation, not be silently ignored for a whole run.

    In every block the template shows, and by name, so the user can find it.
    """
    template = template_block(provider)
    let_through = [
        ".".join(path) or "model"
        for path in nested_paths(template)
        if not refuses_by_name(
            provider, with_setting(template, path, "not_a_real_setting", 1), "not_a_real_setting"
        )
    ]

    assert let_through == []


def built_request(provider, config, *, repeat=0, system_prompt=None):
    """What the provider's own `build_request` makes of one message under `config`."""
    build_request = sys.modules[type(provider).__module__].build_request
    message = Message(id="m0000", text="hello", line_number=1)
    return build_request(
        Request(message=message, repeat=repeat, model=config, system_prompt=system_prompt)
    )


def test_extra_cannot_set_what_taxman_sends(provider):
    """Otherwise `extra:` could quietly bypass a named setting's checks and defaults."""
    template = template_block(provider)
    config = provider.validate_model_config(template)
    sent = built_request(provider, config, system_prompt="Be terse.")
    let_through = [
        key
        for key in sent
        if not refuses_by_name(provider, {**template, "extra": {key: "x"}}, f"cannot set {key}")
    ]

    assert let_through == []


def probe():
    """An `extra:` naming settings no provider has, so it may send them."""
    return {"taxman_probe": {"nested": [1]}, "taxman_null": None}


def test_extra_is_sent_as_written(provider):
    """`extra:` is the escape hatch: what is written is what is sent, nulls included."""
    config = provider.validate_model_config({**template_block(provider), "extra": probe()})
    sent = built_request(provider, config)

    assert {key: sent[key] for key in probe() if key in sent} == probe()


def test_a_request_never_carries_changes_from_the_one_before(provider):
    """Every request in a run shares the audit's one `extra:`; none may write back into it."""
    config = provider.validate_model_config({**template_block(provider), "extra": probe()})
    first = built_request(provider, config)
    first["taxman_probe"]["nested"].append(2)

    second = built_request(provider, config, repeat=1)

    assert second["taxman_probe"] == {"nested": [1]}


def test_the_model_block_takes_no_system_prompt(provider):
    """The system prompt is the audit's own `system_prompt:`, read by core."""
    block = {**template_block(provider), "system_prompt": "Be terse."}

    with pytest.raises(ValueError):
        provider.validate_model_config(block)


def test_an_unrelated_exception_is_not_retried(provider):
    assert provider.is_retryable(ValueError("nope")) is False


def test_an_unrelated_exception_has_no_details_to_log_and_no_wait(provider):
    """Only the provider's own SDK errors carry a status, a request id or a wait."""
    unrelated = RuntimeError("Request timed out.")

    assert provider.error_details(unrelated) == {}
    assert provider.retry_after(unrelated) is None


def test_claims_batch_support_only_if_it_implements_it(provider):
    """`execution.batch` trusts the claim; without the methods a run would fail at its first."""
    implements = type(provider).submit_batch is not Provider.submit_batch

    assert provider.supports_batch == implements


def a_request(provider):
    """One message under the provider's own template."""
    config = provider.validate_model_config(template_block(provider))
    message = Message(id="m0000", text="hello", line_number=1)
    return Request(message=message, repeat=0, model=config)


async def test_send_before_startup_never_uses_an_ambient_key(provider, monkeypatch):
    """One named variable, no fallback chain: the key only ever arrives via startup()."""
    if provider.default_api_key_env:
        monkeypatch.setenv(provider.default_api_key_env, "ambient-key-must-not-be-used")
    fresh = type(provider)()

    with pytest.raises(ProviderError, match="startup"):
        await fresh.send(a_request(fresh), timeout_s=1.0)


async def test_send_after_shutdown_is_refused(provider):
    """A run's client goes with its shutdown; nothing may send on it afterwards."""
    fresh = type(provider)()
    await fresh.startup(api_key="a-key")
    await fresh.shutdown()

    with pytest.raises(ProviderError, match="startup"):
        await fresh.send(a_request(fresh), timeout_s=1.0)


async def test_a_run_never_borrows_another_runs_client(provider):
    """`get_provider` hands back one instance, so per-run state must not live on it.

    Two audits gathered in one process would otherwise bill whichever key started
    last, and the first to finish would close the client the other still used.
    """
    shared = type(provider)()
    started, finished = asyncio.Event(), asyncio.Event()

    async def another_run():
        await shared.startup(api_key="another-runs-key")
        started.set()
        await finished.wait()
        await shared.shutdown()

    other = asyncio.create_task(another_run())
    await started.wait()
    try:
        with pytest.raises(ProviderError, match="startup"):
            await shared.send(a_request(shared), timeout_s=1.0)
    finally:
        finished.set()
        await other
