import pytest

from ai_taxman.core.errors import MessageFileError
from ai_taxman.core.messages import Message, read_messages


def write(tmp_path, text, name="messages.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_one_message_per_line(tmp_path):
    path = write(tmp_path, "first\nsecond\nthird\n")

    messages = read_messages(path)

    assert [m.text for m in messages] == ["first", "second", "third"]
    assert all(isinstance(m, Message) for m in messages)


def test_assigns_stable_zero_padded_ids_in_file_order(tmp_path):
    path = write(tmp_path, "a\nb\nc\n")

    messages = read_messages(path)

    assert [m.id for m in messages] == ["m0000", "m0001", "m0002"]


def test_ids_are_stable_across_reads(tmp_path):
    path = write(tmp_path, "a\nb\n")

    assert [m.id for m in read_messages(path)] == [m.id for m in read_messages(path)]


def test_skips_blank_lines_and_comments_without_shifting_earlier_ids(tmp_path):
    path = write(tmp_path, "first\n\n# a comment\n   \nsecond\n")

    messages = read_messages(path)

    assert [(m.id, m.text) for m in messages] == [("m0000", "first"), ("m0001", "second")]


def test_records_the_original_line_number(tmp_path):
    path = write(tmp_path, "first\n\n# comment\nsecond\n")

    messages = read_messages(path)

    assert [m.line_number for m in messages] == [1, 4]


def test_strips_surrounding_whitespace(tmp_path):
    path = write(tmp_path, "  padded message  \n")

    assert read_messages(path)[0].text == "padded message"


def test_escaped_hash_is_a_message_not_a_comment(tmp_path):
    path = write(tmp_path, "\\# not a comment\n")

    assert read_messages(path)[0].text == "# not a comment"


def test_hashes_the_message_text(tmp_path):
    import hashlib

    path = write(tmp_path, "hello\n")
    expected = hashlib.sha256(b"hello").hexdigest()

    assert read_messages(path)[0].hash == f"sha256:{expected}"


def test_reads_utf8(tmp_path):
    path = write(tmp_path, "¿qué tal? 🧾\n")

    assert read_messages(path)[0].text == "¿qué tal? 🧾"


def test_duplicate_messages_are_kept_with_distinct_ids(tmp_path):
    path = write(tmp_path, "same\nsame\n")

    messages = read_messages(path)

    assert [m.id for m in messages] == ["m0000", "m0001"]
    assert messages[0].hash == messages[1].hash


def test_missing_file_raises_a_friendly_error(tmp_path):
    missing = tmp_path / "nope.txt"

    with pytest.raises(MessageFileError) as exc:
        read_messages(missing)

    assert str(missing) in str(exc.value)


def test_file_with_no_usable_messages_raises(tmp_path):
    path = write(tmp_path, "\n# only comments\n\n")

    with pytest.raises(MessageFileError, match="no messages"):
        read_messages(path)


def test_id_width_grows_past_ten_thousand_messages(tmp_path):
    path = write(tmp_path, "\n".join(str(i) for i in range(10_001)) + "\n")

    messages = read_messages(path)

    assert messages[0].id == "m00000"
    assert messages[-1].id == "m10000"
