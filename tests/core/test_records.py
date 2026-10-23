import json
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

from ai_taxman.core.errors import ConfigError
from ai_taxman.core.records import (
    RESPONSE_SCHEMA_VERSION,
    ResponseRecord,
    RunManifest,
    new_run_id,
    timestamp,
    validate_run_id,
)


@pytest.fixture
def in_new_york(monkeypatch):
    """Run the test in a known time zone, then put the machine's back."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def make_record(**overrides):
    fields = dict(
        audit="my-audit",
        run_id="20260830T142201Z-a1b2c3",
        message_id="m0007",
        message="hello",
        repeat=2,
        provider="openai",
        model="gpt-5",
        requested_at="2026-08-30T14:22:01.000000-04:00",
        received_at="2026-08-30T14:22:01.812000-04:00",
        latency_ms=812,
        status="ok",
        raw={"id": "resp_1", "usage": {"input_tokens": 42, "output_tokens": 310}},
    )
    fields.update(overrides)
    return ResponseRecord(**fields)


def test_round_trips_through_json():
    record = make_record()

    restored = ResponseRecord.from_dict(json.loads(json.dumps(record.to_dict())))

    assert restored == record


def test_a_row_has_exactly_the_schema_fields():
    """Auditors read old data by these names, so none is added, dropped or renamed by accident.

    Every one is written, even when empty. A message is named by its text and id
    only: not where it sat in its file, not a hash of a text the id already derives
    from, and not the system prompt, which the manifest holds.
    """
    assert set(make_record(raw={}).to_dict()) == {
        "schema_version",
        "audit",
        "run_id",
        "message_id",
        "message",
        "repeat",
        "provider",
        "model",
        "requested_at",
        "received_at",
        "latency_ms",
        "status",
        "error",
        "attempts",
        "raw",
    }


def test_carries_the_schema_version():
    assert make_record().to_dict()["schema_version"] == RESPONSE_SCHEMA_VERSION


def test_the_schema_is_version_1():
    """Held at 1 until the first release; after that, bumped only for a break."""
    assert RESPONSE_SCHEMA_VERSION == 1


def test_defaults_to_one_attempt():
    assert make_record().attempts == 1


def test_status_is_constrained():
    with pytest.raises(ValueError):
        make_record(status="maybe")


def test_run_ids_are_unique_and_sortable():
    ids = sorted(new_run_id() for _ in range(50))

    assert len(set(ids)) == 50
    assert all(len(run_id) == len(ids[0]) for run_id in ids)


def test_times_are_written_in_local_time_with_their_offset(in_new_york):
    """Times read in the user's own zone; the offset keeps each one exact across
    zones and clock changes."""
    four_pm_in_london = datetime(2026, 7, 1, 16, 0, tzinfo=timezone(timedelta(hours=1)))

    assert timestamp(four_pm_in_london) == "2026-07-01T11:00:00.000000-04:00"
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}-0[45]:00$", timestamp())


def test_run_id_starts_with_a_utc_timestamp():
    assert re.match(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$", new_run_id())


def test_manifest_round_trips():
    manifest = RunManifest(
        audit="my-audit",
        run_id="r1",
        provider="openai",
        model="gpt-5",
        taxman_versions=["0.1.0"],
        schema_version=RESPONSE_SCHEMA_VERSION,
        messages_path="messages/probe.txt",
        message_ids=["a", "b"],
        n_messages=10,
        repeats=3,
        config={"execution": {"repeats": 3}},
    )

    assert RunManifest.from_dict(json.loads(json.dumps(manifest.to_dict()))) == manifest


def test_manifest_counts_default_to_zero_before_the_run_finishes():
    manifest = RunManifest(
        audit="a",
        run_id="r",
        provider="p",
        model="m",
        taxman_versions=["0"],
        schema_version=RESPONSE_SCHEMA_VERSION,
        messages_path="m.txt",
        message_ids=["a"],
        n_messages=1,
        repeats=1,
        config={},
    )

    assert manifest.n_ok == 0
    assert manifest.n_error == 0
    assert manifest.finished_at is None


# --- run ids ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["my-run", "run_2", "pilot.1", "20260830T142201Z-a1b2c3", "A", "0"],
)
def test_a_run_id_may_be_any_plain_name(value):
    assert validate_run_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "../escape",
        "../../escape",
        "nested/run",
        "/absolute",
        "..",
        ".",
        "",
        "   ",
        "two words",
        "quote'd",
        "star*",
        "~/home",
    ],
)
def test_a_run_id_that_is_not_a_plain_name_is_refused(value):
    """The id becomes a directory name and is recorded in every row."""
    with pytest.raises(ConfigError):
        validate_run_id(value)


def test_a_traversing_run_id_says_what_is_allowed():
    with pytest.raises(ConfigError) as caught:
        validate_run_id("../escaped")

    message = str(caught.value)
    assert "../escaped" in message
    assert "run id" in message.lower()


def test_a_run_id_is_not_allowed_to_be_endless():
    with pytest.raises(ConfigError):
        validate_run_id("r" * 200)


def test_surrounding_whitespace_is_trimmed_rather_than_refused():
    """Copy-pasting an id out of a log should not be a syntax error."""
    assert validate_run_id("  my-run\n") == "my-run"
