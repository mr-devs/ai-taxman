"""The parts of the Provider contract core and the runner rely on.

None of this may require core to know a single provider-specific key name.
"""

import inspect

from ai_taxman.providers.base import Provider


def test_a_provider_declares_its_conventional_key_variable(fake_provider):
    assert fake_provider.default_api_key_env == "FAKE_API_KEY"


def test_openai_declares_the_conventional_openai_variable():
    from ai_taxman.providers.openai.provider import PROVIDER

    assert PROVIDER.default_api_key_env == "OPENAI_API_KEY"


def test_providers_require_a_key_by_default():
    assert Provider.requires_api_key is True


def test_a_provider_can_opt_out_of_needing_a_key(fake_provider):
    """Local models and test doubles have nothing to authenticate with."""
    assert fake_provider.requires_api_key is False


def test_taxman_stores_no_key_so_there_is_no_prefixed_variable(fake_provider):
    """Keys come from the user's environment; taxman has no variable of its own."""
    assert not hasattr(fake_provider, "taxman_api_key_env")


def test_the_contract_asks_no_setup_questions(fake_provider):
    """Settings are edited in the audit YAML, never collected by a prompt."""
    assert not hasattr(fake_provider, "setup_questions")


def test_render_template_takes_no_arguments():
    """There are no saved defaults to render; the template is always blank."""
    parameters = inspect.signature(Provider.render_template).parameters

    assert list(parameters) == ["self"]


def test_a_request_carries_no_system_prompt_unless_the_audit_names_one():
    from ai_taxman.core.messages import Message
    from ai_taxman.providers.base import Request

    message = Message(id="m0000", text="hi", line_number=1)

    assert Request(message=message, repeat=0, model={}).system_prompt is None


def test_describe_model_names_the_model_for_the_record(fake_provider):
    """Core needs a model name for every record without knowing the block's shape."""
    config = fake_provider.validate_model_config({"name": "fake-2"})

    assert fake_provider.describe_model(config) == "fake-2"


def test_openai_describe_model():
    from ai_taxman.providers.openai.provider import PROVIDER

    config = PROVIDER.validate_model_config({"name": "gpt-5"})

    assert PROVIDER.describe_model(config) == "gpt-5"


def test_startup_receives_the_resolved_api_key(fake_provider):
    import asyncio

    asyncio.run(fake_provider.startup(api_key="sk-resolved"))

    assert fake_provider.api_key == "sk-resolved"
