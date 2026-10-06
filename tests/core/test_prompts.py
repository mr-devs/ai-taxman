"""Reading the file an audit names in `system_prompt:`."""

import pytest

from ai_taxman.core.errors import ConfigError
from ai_taxman.core.prompts import read_system_prompt


def test_reads_the_text_of_the_file(tmp_path):
    path = tmp_path / "neutral.txt"
    path.write_text("You are a helpful assistant.\nAnswer briefly.\n", encoding="utf-8")

    prompt = read_system_prompt(path)

    assert prompt.text == "You are a helpful assistant.\nAnswer briefly."


def test_the_trailing_newline_an_editor_adds_is_not_sent(tmp_path):
    path = tmp_path / "neutral.txt"
    path.write_text("  Be terse.\n\n", encoding="utf-8")

    assert read_system_prompt(path).text == "Be terse."


def test_a_byte_order_mark_is_not_sent(tmp_path):
    """Some Windows editors start a UTF-8 file with an invisible U+FEFF."""
    path = tmp_path / "neutral.txt"
    path.write_text("\ufeffBe terse.\n", encoding="utf-8")

    assert read_system_prompt(path).text == "Be terse."


def test_a_missing_file_names_the_key_to_fix(tmp_path):
    with pytest.raises(ConfigError, match="system_prompt"):
        read_system_prompt(tmp_path / "absent.txt")


def test_an_empty_file_is_refused(tmp_path):
    """An empty system prompt is almost certainly a file not yet written."""
    path = tmp_path / "empty.txt"
    path.write_text("\n  \n", encoding="utf-8")

    with pytest.raises(ConfigError, match="empty"):
        read_system_prompt(path)


def test_a_file_that_is_not_utf8_is_refused(tmp_path):
    path = tmp_path / "latin.txt"
    path.write_bytes("caf\xe9".encode("latin-1"))

    with pytest.raises(ConfigError, match="UTF-8"):
        read_system_prompt(path)
