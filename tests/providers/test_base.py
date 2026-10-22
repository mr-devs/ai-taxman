import pytest

from ai_taxman.providers.base import Provider


def test_provider_cannot_be_instantiated_without_the_contract():
    class Incomplete(Provider):
        name = "incomplete"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


def test_batch_methods_are_optional(fake_provider):
    assert fake_provider.supports_batch is False


async def test_unsupported_batch_raises_a_clear_error(fake_provider):
    with pytest.raises(NotImplementedError, match="fake"):
        await fake_provider.submit_batch([], timeout_s=1.0)


def test_retryability_defaults_to_false():
    class Minimal(Provider):
        name = "minimal"

        def known_models(self):
            return []

        def validate_model_config(self, block):
            return block

        def render_template(self, defaults):
            return ""

        async def send(self, request, *, timeout_s):
            return {}

    assert Minimal().is_retryable(RuntimeError("boom")) is False
