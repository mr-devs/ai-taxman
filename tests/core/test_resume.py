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


# --- a new run, on request ---------------------------------------------------------


async def test_a_new_run_starts_over_beside_the_last_one(project, fake_provider):
    first = await run_audit_async(audit(project))
    keep_only(first, (ONE, 0))
    fake_provider.sent.clear()

    second = await run_audit_async(audit(project), new_run=True)

    assert sent(fake_provider) == sorted([(ONE, 0), (TWO, 0), (THREE, 0)])
    assert second.run_id != first.run_id
    assert second.output_path.parent.parent == first.output_path.parent.parent
    assert second.resumed is False


async def test_collecting_after_a_new_run_finishes_the_new_one(project, fake_provider):
    await run_audit_async(audit(project))
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    second = await run_audit_async(audit(project), new_run=True)
    fake_provider.sent.clear()

    third = await run_audit_async(audit(project))

    assert third.run_id == second.run_id
    assert sent(fake_provider) == [(TWO, 0)]


async def test_a_new_run_needs_run_id_in_the_output_directory(project, fake_provider):
    """Without {run_id}, `output.dir` has room for one run, so a second has nowhere to go."""
    from ai_taxman.core.errors import ConfigError

    single = "output:\n  dir: data/{audit}\n  log_dir: logs/{audit}"
    await run_audit_async(audit(project, single))

    with pytest.raises(ConfigError, match=r"\{run_id\}"):
        await run_audit_async(audit(project, single), new_run=True)


# --- an audit that has changed is never resumed ----------------------------------


async def unfinished_run(project, fake_provider, *blocks):
    """A run of the audit with ONE answered and the rest missing."""
    first = await run_audit_async(audit(project, *blocks))
    keep_only(first, (ONE, 0))
    fake_provider.sent.clear()
    return first


async def test_a_changed_setting_stops_the_run_being_resumed(project, fake_provider):
    from ai_taxman.core.errors import AuditChangedError

    await unfinished_run(project, fake_provider)

    with pytest.raises(AuditChangedError) as caught:
        await run_audit_async(audit(project, "execution:\n  max_concurrency: 2"))

    assert "execution.max_concurrency: 8 -> 2" in str(caught.value)
    assert fake_provider.sent == []


async def test_a_changed_model_setting_is_named(project, fake_provider):
    from ai_taxman.core.errors import AuditChangedError

    await unfinished_run(project, fake_provider)
    changed = audit(project)
    changed.model["temperature"] = 0.7

    with pytest.raises(AuditChangedError, match=r"model\.temperature: \(not set\) -> 0\.7"):
        await run_audit_async(changed)


async def test_changed_messages_stop_the_run_being_resumed(project, fake_provider):
    from ai_taxman.core.errors import AuditChangedError

    await unfinished_run(project, fake_provider)
    (project / "messages" / "probe.txt").write_text("one\ntwo\nfour\nfive\n", encoding="utf-8")

    with pytest.raises(AuditChangedError, match="messages: 2 added, 1 removed"):
        await run_audit_async(audit(project))


async def test_a_changed_system_prompt_stops_the_run_being_resumed(project, fake_provider):
    from ai_taxman.core.errors import AuditChangedError

    (project / "prompts").mkdir()
    prompt = project / "prompts" / "neutral.txt"
    prompt.write_text("Be terse.", encoding="utf-8")
    await unfinished_run(project, fake_provider, "system_prompt: prompts/neutral.txt")
    prompt.write_text("Be thorough.", encoding="utf-8")

    with pytest.raises(AuditChangedError, match="system prompt"):
        await run_audit_async(audit(project, "system_prompt: prompts/neutral.txt"))


async def test_the_refusal_says_how_to_start_over(project, fake_provider):
    from ai_taxman.core.errors import AuditChangedError

    first = await unfinished_run(project, fake_provider)

    with pytest.raises(AuditChangedError) as caught:
        await run_audit_async(audit(project, "execution:\n  repeats: 2"))

    assert first.run_id in str(caught.value)
    assert "--new-run" in str(caught.value)


async def test_comments_and_line_order_are_not_changes(project, fake_provider):
    """What is sent is the same, so the run is the same run."""
    first = await unfinished_run(project, fake_provider)
    (project / "messages" / "probe.txt").write_text(
        "# reordered\nthree\n\ntwo\none\n", encoding="utf-8"
    )

    second = await run_audit_async(audit(project))

    assert second.run_id == first.run_id
    assert sent(fake_provider) == sorted([(TWO, 0), (THREE, 0)])


# --- a complete run is left alone ----------------------------------------------------


async def test_a_complete_run_is_not_collected_again(project, fake_provider):
    first = await run_audit_async(audit(project))
    fake_provider.sent.clear()

    second = await run_audit_async(audit(project))

    assert fake_provider.sent == []
    assert second.already_complete is True
    assert second.run_id == first.run_id
    assert first.already_complete is False


async def test_a_run_killed_after_its_last_response_is_marked_complete(project, fake_provider):
    """Every response is on disk, so the manifest stops saying otherwise."""
    first = await run_audit_async(audit(project))
    keep_only(first, (ONE, 0), (TWO, 0), (THREE, 0))

    await run_audit_async(audit(project))

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["n_ok"] == 3


# --- what a killed run left behind is made whole before it is added to -------------


async def test_a_half_written_last_line_does_not_break_the_resumed_file(project, fake_provider):
    first = await run_audit_async(audit(project))
    keep_only(first, (ONE, 0))
    with first.output_path.open("a", encoding="utf-8") as handle:
        handle.write('{"partial": ')

    await run_audit_async(audit(project))

    rows = list(read_jsonl(first.output_path))
    assert sorted(row.message_id for row in rows) == sorted([ONE, TWO, THREE])


async def test_a_killed_compressed_run_is_resumed_into_a_readable_file(project, fake_provider):
    from ai_taxman.core.writer import JsonlWriter

    compressed = "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n  compress: true"
    first = await run_audit_async(audit(project, compressed))
    (kept,) = [row for row in read_jsonl(first.output_path) if row.message_id == ONE]
    # What a kill leaves: the row flushed, the gzip stream never closed.
    writer = JsonlWriter(first.output_path, compress=True)
    first.output_path.unlink()
    with writer:
        writer.write(kept)
        left_behind = writer.path.read_bytes()
    writer.path.write_bytes(left_behind)

    await run_audit_async(audit(project, compressed))

    rows = list(read_jsonl(first.output_path))
    assert sorted(row.message_id for row in rows) == sorted([ONE, TWO, THREE])


# --- one collector per run at a time ------------------------------------------------


async def test_a_run_being_collected_elsewhere_is_not_resumed(project, fake_provider):
    from ai_taxman.core.errors import RunInProgressError
    from ai_taxman.core.runs import hold_run

    first = await unfinished_run(project, fake_provider)

    with hold_run(first.output_path.parent), pytest.raises(RunInProgressError) as caught:
        await run_audit_async(audit(project))

    assert first.run_id in str(caught.value)
    assert fake_provider.sent == []


async def test_the_refusal_names_a_background_runs_pid(project, fake_provider):
    from ai_taxman.core.errors import RunInProgressError
    from ai_taxman.core.runs import hold_run

    first = await unfinished_run(project, fake_provider)
    (first.output_path.parent / "collect.pid").write_text("4242\n", encoding="utf-8")

    with hold_run(first.output_path.parent), pytest.raises(RunInProgressError, match="kill 4242"):
        await run_audit_async(audit(project))


async def test_a_run_holds_its_lock_while_it_is_collected(project, fake_provider):
    import asyncio

    from ai_taxman.core.errors import RunInProgressError
    from ai_taxman.core.runs import hold_run

    fake_provider.delay = 0.2
    task = asyncio.create_task(run_audit_async(audit(project, "execution:\n  max_concurrency: 1")))
    await asyncio.sleep(0.1)
    (directory,) = (project / "data" / "probe").iterdir()

    with pytest.raises(RunInProgressError), hold_run(directory):
        pass

    await task
    with hold_run(directory):  # released once the run is over
        pass
