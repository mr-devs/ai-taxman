import gzip
import json

from ai_taxman.core.records import ResponseRecord
from ai_taxman.core.writer import JsonlWriter, read_jsonl


def make_record(message_id="m0000", repeat=0):
    return ResponseRecord(
        audit="a",
        run_id="r",
        message_id=message_id,
        message_hash="sha256:x",
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


def test_read_jsonl_returns_nothing_for_a_missing_file(tmp_path):
    assert list(read_jsonl(tmp_path / "nope.jsonl")) == []
