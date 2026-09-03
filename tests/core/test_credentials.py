"""Resolving the API key an audit names.

One variable name, looked up once, in the process environment and nowhere else.
taxman never stores a key: the user exports the variable and names it in the
audit's `api_key_env:` field.
"""

import pytest

from ai_taxman.core.credentials import resolve_api_key
from ai_taxman.core.errors import MissingApiKeyError


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_reads_an_exported_environment_variable(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    assert resolve_api_key("OPENAI_API_KEY") == "sk-from-env"


def test_each_audit_gets_the_variable_it_named(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")

    assert resolve_api_key("OPENAI_API_KEY") == "sk-openai"
    assert resolve_api_key("ANTHROPIC_API_KEY") == "sk-anthropic"


def test_keys_with_awkward_characters_come_back_unchanged(monkeypatch):
    awkward = "sk-proj-a#b$c'd\"e f=g"
    monkeypatch.setenv("OPENAI_API_KEY", awkward)

    assert resolve_api_key("OPENAI_API_KEY") == awkward


def test_an_unset_variable_is_an_error_naming_the_variable():
    with pytest.raises(MissingApiKeyError) as exc:
        resolve_api_key("OPENAI_API_KEY")

    assert "OPENAI_API_KEY" in str(exc.value)


def test_the_error_says_how_to_fix_it():
    """The user is at a prompt: tell them to export it and where the name lives."""
    with pytest.raises(MissingApiKeyError) as exc:
        resolve_api_key("OPENAI_API_KEY")

    message = str(exc.value)
    assert "export" in message.lower()
    assert "api_key_env" in message


def test_an_empty_variable_counts_as_unset(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")

    with pytest.raises(MissingApiKeyError):
        resolve_api_key("OPENAI_API_KEY")


def test_a_whitespace_only_variable_counts_as_unset(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "   ")

    with pytest.raises(MissingApiKeyError):
        resolve_api_key("OPENAI_API_KEY")


def test_the_placeholder_init_writes_is_reported_as_unset():
    """An unedited scaffold must fail before anything is sent."""
    with pytest.raises(MissingApiKeyError, match="insert_api_key_env_var_here"):
        resolve_api_key("<insert_api_key_env_var_here>")


def test_taxman_stores_no_keys_of_its_own():
    """The module offers no way to write a key to disk."""
    import ai_taxman.core.credentials as module

    assert not hasattr(module, "store_api_key")
    assert not hasattr(module, "env_file_path")
