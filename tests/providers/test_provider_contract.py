"""The parts of the Provider contract core and the runner rely on.

None of this may require core to know a single provider-specific key name.
"""

import inspect

from ai_taxman.providers.base import Provider


def test_providers_require_a_key_by_default():
    assert Provider.requires_api_key is True


def test_render_template_takes_no_arguments():
    """There are no saved defaults to render; the template is always blank."""
    parameters = inspect.signature(Provider.render_template).parameters

    assert list(parameters) == ["self"]
