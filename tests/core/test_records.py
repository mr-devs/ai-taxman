import json

import pytest

from ai_taxman.core.records import RESPONSE_SCHEMA_VERSION, ResponseRecord, RunManifest, new_run_id


def make_record(**overrides):
    fields = dict(
        audit="my-audit",
        run_id="20260830T142201Z-a1b2c3",
        message_id="m0007",
        message_hash="sha256:abc",
        message="hello",
        repeat=2,
        provider="openai",
        model="gpt-5",
        requested_at="2026-08-30T14:22:01.000000Z",
        received_at="2026-08-30T14:22:01.812000Z",
        latency_ms=812,
        status="ok",
        text="hi there",
        usage={"input_tokens": 42, "output_tokens": 310},
        raw={"id": "resp_1"},
    )
    fields.update(overrides)
    return ResponseRecord(**fields)


def test_round_trips_through_json():
    record = make_record()

    restored = ResponseRecord.from_dict(json.loads(json.dumps(record.to_dict())))

    assert restored == record


def test_serialises_every_schema_field_even_when_empty():
    row = make_record(text=None, usage=None).to_dict()

    for field in ("error", "text", "usage", "attempts", "schema_version"):
        assert field in row


def test_carries_the_schema_version():
    assert make_record().to_dict()["schema_version"] == RESPONSE_SCHEMA_VERSION


def test_defaults_to_one_attempt():
    assert make_record().attempts == 1


def test_error_records_carry_the_failure_and_no_text():
    record = make_record(status="error", error="rate limited after 5 attempts", text=None, raw={})

    assert record.status == "error"
    assert record.error == "rate limited after 5 attempts"
    assert record.text is None


def test_status_is_constrained():
    with pytest.raises(ValueError):
        make_record(status="maybe")


def test_raw_provider_payload_is_preserved_verbatim():
    payload = {"nested": {"list": [1, 2, {"deep": True}]}, "unicode": "🧾"}

    assert make_record(raw=payload).to_dict()["raw"] == payload


def test_run_ids_are_unique_and_sortable():
    ids = sorted(new_run_id() for _ in range(50))

    assert len(set(ids)) == 50
    assert all(len(run_id) == len(ids[0]) for run_id in ids)


def test_run_id_starts_with_a_utc_timestamp():
    import re

    assert re.match(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$", new_run_id())


def test_manifest_round_trips():
    manifest = RunManifest(
        audit="my-audit",
        run_id="r1",
        provider="openai",
        model="gpt-5",
        taxman_version="0.0.2",
        schema_version=RESPONSE_SCHEMA_VERSION,
        messages_path="messages/probe.txt",
        messages_hash="sha256:def",
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
        taxman_version="0",
        schema_version=RESPONSE_SCHEMA_VERSION,
        messages_path="m.txt",
        messages_hash="sha256:x",
        n_messages=1,
        repeats=1,
        config={},
    )

    assert manifest.n_ok == 0
    assert manifest.n_error == 0
    assert manifest.finished_at is None
