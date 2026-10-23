import pytest

from ai_taxman.providers.base import Provider, parse_retry_after


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

        def validate_model_config(self, block):
            return block

        def render_template(self, defaults):
            return ""

        async def send(self, request, *, timeout_s):
            return {}

    assert Minimal().is_retryable(RuntimeError("boom")) is False


@pytest.mark.parametrize("value", ["0", "0.0", "-1"])
def test_a_retry_after_of_no_time_is_no_answer(value):
    """A wait of nothing would send every retry at once, with no backoff between
    them; the OpenAI and Anthropic SDKs read it as no answer too."""
    assert parse_retry_after(value) is None
