import gzip
import json

import pytest

from ai_taxman.core.errors import ResponseFileError
from ai_taxman.core.records import ResponseRecord
from ai_taxman.core.writer import JsonlWriter, read_jsonl, repair_tail


def make_record(message_id="m0000", repeat=0):
    return ResponseRecord(
        audit="a",
        run_id="r",
        message_id=message_id,
        message="hello",
        repeat=repeat,
        provider="fake",
        model="fake-1",
        requested_at="2026-08-30T00:00:00.000000Z",
        received_at="2026-08-30T00:00:01.000000Z",
        latency_ms=1000,
        status="ok",
        raw={},
    )


def test_writes_one_json_object_per_line(tmp_path):
    target = tmp_path / "responses.jsonl"

    with JsonlWriter(target) as writer:
        writer.write(make_record("m0000"))
        writer.write(make_record("m0001"))

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["message_id"] for line in lines] == ["m0000", "m0001"]


def test_creates_missing_parent_directories(tmp_path):
    target = tmp_path / "deep" / "nested" / "responses.jsonl"

    with JsonlWriter(target) as writer:
        writer.write(make_record())

    assert target.exists()


def test_compress_appends_the_gz_suffix_and_writes_gzip(tmp_path):
    target = tmp_path / "responses.jsonl"

    with JsonlWriter(target, compress=True) as writer:
        writer.write(make_record())

    expected = tmp_path / "responses.jsonl.gz"
    assert writer.path == expected
    assert not target.exists()
    with gzip.open(expected, "rt", encoding="utf-8") as handle:
        assert json.loads(handle.read())["message_id"] == "m0000"


def test_does_not_double_the_gz_suffix(tmp_path):
    with JsonlWriter(tmp_path / "responses.jsonl.gz", compress=True) as writer:
        writer.write(make_record())

    assert writer.path == tmp_path / "responses.jsonl.gz"


def test_appends_rather_than_truncating(tmp_path):
    target = tmp_path / "responses.jsonl"

    with JsonlWriter(target) as writer:
        writer.write(make_record("m0000"))
    with JsonlWriter(target) as writer:
        writer.write(make_record("m0001"))

    assert len(target.read_text(encoding="utf-8").splitlines()) == 2


def test_rows_are_readable_before_the_writer_closes(tmp_path):
    """A long audit must not lose everything if the process is killed."""
    target = tmp_path / "responses.jsonl"

    with JsonlWriter(target) as writer:
        writer.write(make_record())
        assert len(target.read_text(encoding="utf-8").splitlines()) == 1


def test_keeps_unicode_unescaped(tmp_path):
    target = tmp_path / "responses.jsonl"
    record = make_record()
    record.raw = {"text": "¿qué tal? 🧾"}

    with JsonlWriter(target) as writer:
        writer.write(record)

    assert "🧾" in target.read_text(encoding="utf-8")


def test_counts_what_it_wrote(tmp_path):
    with JsonlWriter(tmp_path / "responses.jsonl") as writer:
        writer.write(make_record("m0000"))
        writer.write(make_record("m0001"))

        assert writer.count == 2


def test_read_jsonl_round_trips_plain_files(tmp_path):
    target = tmp_path / "responses.jsonl"
    records = [make_record("m0000"), make_record("m0001", repeat=1)]

    with JsonlWriter(target) as writer:
        for record in records:
            writer.write(record)

    assert list(read_jsonl(target)) == records


def test_read_jsonl_round_trips_compressed_files(tmp_path):
    with JsonlWriter(tmp_path / "responses.jsonl", compress=True) as writer:
        writer.write(make_record())

    assert len(list(read_jsonl(tmp_path / "responses.jsonl.gz"))) == 1


def test_read_jsonl_skips_a_truncated_final_line(tmp_path):
    """A killed run can leave half a line behind; the rest is still good data."""
    target = tmp_path / "responses.jsonl"
    with JsonlWriter(target) as writer:
        writer.write(make_record())
    with target.open("a", encoding="utf-8") as handle:
        handle.write('{"partial": ')

    assert len(list(read_jsonl(target))) == 1


def test_read_jsonl_refuses_a_broken_line_with_rows_after_it(tmp_path):
    """Only a killed run's last line may be cut short; a broken row mid-file is not dropped."""
    target = tmp_path / "responses.jsonl"
    target.write_text(
        json.dumps(make_record().to_dict()) + "\n"
        '{"partial": \n' + json.dumps(make_record(repeat=1).to_dict()) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ResponseFileError, match="Line 2 of"):
        list(read_jsonl(target))


def test_read_jsonl_refuses_a_complete_row_that_is_not_a_record(tmp_path):
    """A whole JSON object was not cut short by a kill, even on the last line."""
    target = tmp_path / "responses.jsonl"
    target.write_text(
        json.dumps(make_record().to_dict()) + "\n" + json.dumps({"message": "hi"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ResponseFileError, match="Line 2 of"):
        list(read_jsonl(target))


def test_read_jsonl_returns_nothing_for_a_missing_file(tmp_path):
    assert list(read_jsonl(tmp_path / "nope.jsonl")) == []


# --- repairing what a killed run left, before a resumed run appends to it ----------


def killed_gzip(path, records):
    """The file a compressed run leaves when killed: flushed rows, no end-of-stream marker."""
    writer = JsonlWriter(path, compress=True)
    with writer:
        for record in records:
            writer.write(record)
        left_behind = writer.path.read_bytes()
    writer.path.write_bytes(left_behind)
    return writer.path


def test_repair_drops_a_half_written_last_line(tmp_path):
    target = tmp_path / "responses.jsonl"
    with JsonlWriter(target) as writer:
        writer.write(make_record("m0"))
    whole = target.read_bytes()
    with target.open("a", encoding="utf-8") as handle:
        handle.write('{"partial": ')

    assert repair_tail(target) is True
    assert target.read_bytes() == whole


def test_repair_leaves_a_whole_file_alone(tmp_path):
    target = tmp_path / "responses.jsonl"
    with JsonlWriter(target) as writer:
        writer.write(make_record("m0"))
    whole = target.read_bytes()

    assert repair_tail(target) is False
    assert target.read_bytes() == whole


def test_repair_of_a_missing_file_does_nothing(tmp_path):
    assert repair_tail(tmp_path / "responses.jsonl") is False


def test_repair_closes_a_gzip_stream_a_kill_left_open(tmp_path):
    """Otherwise rows appended after it could never be read: reading stops at the break."""
    path = killed_gzip(tmp_path / "responses.jsonl", [make_record("m0"), make_record("m1")])

    assert repair_tail(path) is True
    with JsonlWriter(path, compress=True) as writer:
        writer.write(make_record("m2"))

    assert [r.message_id for r in read_jsonl(path)] == ["m0", "m1", "m2"]


def test_repair_keeps_each_gzip_row_exactly_as_written(tmp_path):
    """Rows already paid for are copied, never parsed and written out again."""
    path = killed_gzip(tmp_path / "responses.jsonl", [make_record("m0")])
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        before = handle.readline()

    repair_tail(path)

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        assert handle.read() == before


def test_repair_leaves_a_whole_gzip_file_alone(tmp_path):
    with JsonlWriter(tmp_path / "responses.jsonl", compress=True) as writer:
        writer.write(make_record("m0"))
    whole = writer.path.read_bytes()

    assert repair_tail(writer.path) is False
    assert writer.path.read_bytes() == whole
