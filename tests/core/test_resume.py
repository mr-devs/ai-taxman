"""`collect` on an audit already collected: finish its latest run, never repeat it."""

import json
import textwrap

import pytest

from ai_taxman.core.config import load_audit
from ai_taxman.core.discovery import write_marker
from ai_taxman.core.messages import message_id
from ai_taxman.core.runner import run_audit_async
from ai_taxman.core.writer import read_jsonl

#: The ids of the three messages each test project starts with.
ONE, TWO, THREE = (message_id(text) for text in ("one", "two", "three"))


@pytest.fixture
def project(tmp_path, flat_layout):
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "audits").mkdir()
    write_marker(tmp_path, flat_layout)
    return tmp_path


def audit(project, *blocks):
    """A `fake`-provider audit, with whole YAML blocks appended verbatim."""
    path = project / "audits" / "probe.yaml"
    body = textwrap.dedent(
        """\
        audit: probe
        provider: fake
        messages: messages/probe.txt
        output:
          dir: data/{audit}/{run_id}
          log_dir: logs/{audit}
        model:
          name: fake-1
        """
    )
    path.write_text(body + "".join(block.rstrip() + "\n" for block in blocks), encoding="utf-8")
    return load_audit(path)


def keep_only(result, *pairs):
    """Cut a run back to some of its rows, as if it had been killed after them."""
    rows = [
        row for row in read_jsonl(result.output_path) if (row.message_id, row.repeat) in set(pairs)
    ]
    result.output_path.write_text(
        "".join(json.dumps(row.to_dict()) + "\n" for row in rows), encoding="utf-8"
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "interrupted"
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def sent(fake_provider):
    return sorted((request.message.id, request.repeat) for request in fake_provider.sent)


async def test_collecting_again_sends_only_what_the_run_is_missing(project, fake_provider):
    first = await run_audit_async(audit(project))
    keep_only(first, (ONE, 0))
    fake_provider.sent.clear()

    second = await run_audit_async(audit(project))

    assert sent(fake_provider) == sorted([(TWO, 0), (THREE, 0)])
    assert second.run_id == first.run_id
    assert second.output_path == first.output_path
    assert second.resumed is True
    assert first.resumed is False


async def test_a_resumed_run_is_resumed_per_repeat(project, fake_provider):
    config = "execution:\n  repeats: 2"
    first = await run_audit_async(audit(project, config))
    keep_only(first, (ONE, 0), (ONE, 1), (TWO, 0), (THREE, 0), (THREE, 1))
    fake_provider.sent.clear()

    await run_audit_async(audit(project, config))

    assert sent(fake_provider) == [(TWO, 1)]


async def test_responses_that_failed_are_sent_again(project, fake_provider):
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    first = await run_audit_async(audit(project))
    assert first.n_error == 1
    fake_provider.sent.clear()

    second = await run_audit_async(audit(project))

    assert sent(fake_provider) == [(TWO, 0)]
    assert (second.n_ok, second.n_error) == (1, 0)


async def test_a_resumed_runs_rows_are_added_to_what_was_there(project, fake_provider):
    """The failed row stays: it is what happened. The new one follows it."""
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    first = await run_audit_async(audit(project))

    await run_audit_async(audit(project))

    statuses = [(row.message_id, row.status) for row in read_jsonl(first.output_path)]
    assert len(statuses) == 4
    assert statuses[-1] == (TWO, "ok")


async def test_the_manifest_counts_each_response_once_across_sessions(project, fake_provider):
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    first = await run_audit_async(audit(project))

    await run_audit_async(audit(project))

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert (manifest["n_ok"], manifest["n_error"]) == (3, 0)
    assert manifest["status"] == "complete"


async def test_a_resumed_run_keeps_when_it_started(project, fake_provider):
    first = await run_audit_async(audit(project))
    started = json.loads(first.manifest_path.read_text(encoding="utf-8"))["started_at"]
    keep_only(first, (ONE, 0))

    await run_audit_async(audit(project))

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["started_at"] == started
