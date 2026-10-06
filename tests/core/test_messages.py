import pytest

from ai_taxman.core.errors import MessageFileError
from ai_taxman.core.messages import Message, message_id, read_messages


def write(tmp_path, text, name="messages.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_one_message_per_line(tmp_path):
    path = write(tmp_path, "first\nsecond\nthird\n")

    messages = read_messages(path)

    assert [m.text for m in messages] == ["first", "second", "third"]
    assert all(isinstance(m, Message) for m in messages)


def test_the_id_is_a_uuid5_of_the_text(tmp_path):
    """Pinned: changing the namespace would change every id ever recorded."""
    path = write(tmp_path, "When is the next US federal election?\n")

    assert read_messages(path)[0].id == "ccc7ca1d-0eea-5dd5-aadd-58c750f7bcd7"


def test_message_id_gives_the_id_read_messages_assigns(tmp_path):
    path = write(tmp_path, "first\nsecond\n")

    assert [m.id for m in read_messages(path)] == [message_id("first"), message_id("second")]


def test_ids_are_stable_across_reads(tmp_path):
    path = write(tmp_path, "a\nb\n")

    assert [m.id for m in read_messages(path)] == [m.id for m in read_messages(path)]


def test_skips_blank_lines_and_comments(tmp_path):
    path = write(tmp_path, "first\n\n# a comment\n   \nsecond\n")

    assert [m.text for m in read_messages(path)] == ["first", "second"]


def test_an_id_does_not_depend_on_where_the_message_sits(tmp_path):
    """Adding, removing, or reordering lines leaves every other message's id alone."""
    before = write(tmp_path, "keep\nother\n", name="before.txt")
    after = write(tmp_path, "# new header\nadded\nother\n\nkeep\n", name="after.txt")

    ids_before = {m.text: m.id for m in read_messages(before)}
    ids_after = {m.text: m.id for m in read_messages(after)}

    assert ids_after["keep"] == ids_before["keep"]
    assert ids_after["other"] == ids_before["other"]


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


def test_a_repeated_message_is_refused_naming_both_lines(tmp_path):
    path = write(tmp_path, "same\nother\n\nsame\n")

    with pytest.raises(MessageFileError) as exc:
        read_messages(path)

    message = str(exc.value)
    assert "lines 1 and 4" in message
    assert "execution.repeats" in message


def test_a_repeated_message_error_counts_the_other_repeats(tmp_path):
    path = write(tmp_path, "a\nb\na\nb\na\n")

    with pytest.raises(MessageFileError, match="lines 1 and 3. 2 more lines repeat"):
        read_messages(path)


def test_lines_that_differ_only_in_surrounding_whitespace_are_the_same_message(tmp_path):
    path = write(tmp_path, "same\n   same  \n")

    with pytest.raises(MessageFileError, match="lines 1 and 2"):
        read_messages(path)


def test_missing_file_raises_a_friendly_error(tmp_path):
    missing = tmp_path / "nope.txt"

    with pytest.raises(MessageFileError) as exc:
        read_messages(missing)

    assert str(missing) in str(exc.value)


def test_file_with_no_usable_messages_raises(tmp_path):
    path = write(tmp_path, "\n# only comments\n\n")

    with pytest.raises(MessageFileError, match="no messages"):
        read_messages(path)
