import pytest

from ai_taxman.core import registry
from ai_taxman.core.errors import ProviderNotFoundError


def test_returns_a_registered_provider(fake_provider):
    assert registry.get_provider("fake") is fake_provider


def test_provider_names_are_case_insensitive(fake_provider):
    assert registry.get_provider("FAKE") is fake_provider


def test_unknown_provider_lists_what_is_available(fake_provider):
    with pytest.raises(ProviderNotFoundError) as exc:
        registry.get_provider("openia")

    message = str(exc.value)
    assert "openia" in message
    assert "openai" in message


def test_openai_is_a_built_in_provider():
    assert "openai" in registry.available_providers()


def test_available_providers_includes_registered_ones(fake_provider):
    assert "fake" in registry.available_providers()


def test_available_providers_is_sorted_and_deduplicated(fake_provider):
    names = registry.available_providers()

    assert names == sorted(set(names))


def test_registering_a_duplicate_name_requires_override(fake_provider):
    with pytest.raises(ValueError, match="fake"):
        registry.register_provider(fake_provider)


def test_unregistering_an_unknown_provider_is_harmless():
    registry.unregister_provider("never-registered")


def test_providers_are_loaded_lazily(fake_provider):
    """Listing providers must not import their SDKs."""
    import sys

    sys.modules.pop("ai_taxman.providers.openai", None)
    registry.available_providers()

    assert "ai_taxman.providers.openai" not in sys.modules
