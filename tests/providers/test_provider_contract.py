"""The parts of the Provider contract core and the runner rely on.

None of this may require core to know a single provider-specific key name.
"""

import inspect

from ai_taxman.providers.base import Provider


def test_providers_require_a_key_by_default():
    assert Provider.requires_api_key is True


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
