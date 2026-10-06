"""`taxman messages ids` and `taxman messages text`."""

import csv
import io

from ai_taxman.core.messages import message_id


def write_messages(tmp_path, text, name="probe.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def rows(output):
    return list(csv.reader(io.StringIO(output)))


def test_ids_prints_every_message_in_a_file_with_its_id_as_csv(invoke, tmp_path):
    path = write_messages(tmp_path, "# header\nfirst\nsecond, with a comma\n")

    result = invoke("messages", "ids", str(path))

    assert result.exit_code == 0
    assert rows(result.stdout) == [
        ["message_id", "message"],
        [message_id("first"), "first"],
        [message_id("second, with a comma"), "second, with a comma"],
    ]


def test_ids_reports_a_message_file_collect_would_refuse(invoke, tmp_path):
    path = write_messages(tmp_path, "same\nsame\n")

    result = invoke("messages", "ids", str(path))

    assert result.exit_code != 0
    assert "lines 1 and 2" in result.output


def test_ids_works_outside_a_project(invoke, tmp_path, leave_project):
    """Reading a file needs no audit, so it needs no project either."""
    path = write_messages(tmp_path, "first\n")

    assert invoke("messages", "ids", str(path)).exit_code == 0


def test_ids_gives_the_ids_of_messages_typed_with_t(invoke):
    """Trimmed like a line of a file, so each gets the id that line would."""
    result = invoke("messages", "ids", "-t", "first", "--text", "  second  ")

    assert result.exit_code == 0
    assert rows(result.stdout) == [
        ["message_id", "message"],
        [message_id("first"), "first"],
        [message_id("second"), "second"],
    ]


def test_ids_refuses_a_file_and_t_together(invoke, tmp_path):
    path = write_messages(tmp_path, "first\n")

    result = invoke("messages", "ids", str(path), "-t", "second")

    assert result.exit_code != 0
    assert "not both" in result.output


def test_ids_needs_a_file_or_t(invoke):
    result = invoke("messages", "ids")

    assert result.exit_code != 0
    assert "-t" in result.output


def test_ids_refuses_an_empty_t_message(invoke):
    result = invoke("messages", "ids", "-t", "   ")

    assert result.exit_code != 0
    assert "empty" in result.output


def test_text_prints_only_the_text_of_the_message_with_that_id(invoke, tmp_path):
    path = write_messages(tmp_path, "first\nsecond\n")

    result = invoke("messages", "text", message_id("second"), str(path))

    assert result.exit_code == 0
    assert result.stdout == "second\n"


def test_text_says_when_no_message_in_the_file_has_that_id(invoke, tmp_path):
    path = write_messages(tmp_path, "first\n")

    result = invoke("messages", "text", message_id("absent"), str(path))

    assert result.exit_code != 0
    assert message_id("absent") in result.output
    assert "probe.txt" in result.output
