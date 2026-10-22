import sys

import pytest

from ai_taxman.core.errors import ProviderDependencyError
from ai_taxman.providers.gemini.provider import PROVIDER, GeminiProvider


def test_declares_the_conventional_gemini_variable():
    assert PROVIDER.default_api_key_env == "GEMINI_API_KEY"


# -- the key comes from core, never from the environment -------------------


@pytest.fixture
def ambient(monkeypatch):
    """Everything the Gemini SDK would read from the environment on its own."""
    monkeypatch.setenv("GEMINI_API_KEY", "ambient-gemini-key-must-not-be-used")
    monkeypatch.setenv("GOOGLE_API_KEY", "ambient-google-key-must-not-be-used")
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "true")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")


async def test_an_ambient_switch_never_sends_the_audit_to_vertex(ambient):
    """GOOGLE_GENAI_USE_ENTERPRISE would move the audit to another API and another bill."""
    provider = GeminiProvider()

    await provider.startup(api_key="gm-one")
    try:
        assert provider._require_client()._api_client.vertexai is False
    finally:
        await provider.shutdown()


async def test_a_missing_sdk_says_how_to_add_it(monkeypatch):
    """`google` is a namespace other packages share, so only `genai` is taken away."""
    monkeypatch.setitem(sys.modules, "google.genai", None)
    if "google" in sys.modules:
        monkeypatch.delattr(sys.modules["google"], "genai", raising=False)

    with pytest.raises(ProviderDependencyError, match=r"uv add ai-taxman\[gemini\]"):
        await GeminiProvider().startup(api_key="gm-one")


# -- retries ---------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_name, retryable",
    [
        ("ConflictError", True),
        ("AuthenticationError", False),
        ("NotFoundError", False),
        ("UnprocessableEntityError", False),
    ],
)
def test_retryability_matches_the_sdk_error_class(exc_name, retryable):
    assert PROVIDER.is_retryable(_make_gemini_error(exc_name)) is retryable


def test_a_same_named_error_from_another_google_package_is_not_retried():
    """`google` is a namespace many packages share; only `google.genai` counts."""

    class RateLimitError(Exception):
        pass

    RateLimitError.__module__ = "google.api_core.exceptions"

    assert PROVIDER.is_retryable(RateLimitError()) is False


def _make_gemini_error(name):
    """An instance of a real SDK error class, built without an HTTP round trip."""
    compat_errors = pytest.importorskip("google.genai._gaos.lib.compat_errors")
    cls = getattr(compat_errors, name)
    return cls.__new__(cls)
