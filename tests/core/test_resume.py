"""`collect` on an audit already collected: finish its latest run, never repeat it."""

import json

import pytest

from ai_taxman.core.messages import message_id
from ai_taxman.core.runner import run_audit_async
from ai_taxman.core.writer import read_jsonl

#: The ids of the three messages each test project starts with.
ONE, TWO, THREE = (message_id(text) for text in ("one", "two", "three"))


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


async def test_collecting_again_sends_only_what_the_run_is_missing(audit, fake_provider):
    first = await run_audit_async(audit())
    keep_only(first, (ONE, 0))
    fake_provider.sent.clear()

    second = await run_audit_async(audit())

    assert sent(fake_provider) == sorted([(TWO, 0), (THREE, 0)])
    assert second.run_id == first.run_id
    assert second.output_path == first.output_path
    assert second.resumed is True
    assert first.resumed is False


async def test_a_resumed_run_is_resumed_per_repeat(audit, fake_provider):
    config = "execution:\n  repeats: 2"
    first = await run_audit_async(audit(config))
    keep_only(first, (ONE, 0), (ONE, 1), (TWO, 0), (THREE, 0), (THREE, 1))
    fake_provider.sent.clear()

    await run_audit_async(audit(config))

    assert sent(fake_provider) == [(TWO, 1)]


async def test_responses_that_failed_are_sent_again(audit, fake_provider):
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    first = await run_audit_async(audit())
    assert first.n_error == 1
    fake_provider.sent.clear()

    second = await run_audit_async(audit())

    assert sent(fake_provider) == [(TWO, 0)]
    assert (second.n_ok, second.n_error) == (1, 0)


async def test_a_resumed_runs_rows_are_added_to_what_was_there(audit, fake_provider):
    """The failed row stays: it is what happened. The new one follows it."""
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    first = await run_audit_async(audit())

    await run_audit_async(audit())

    statuses = [(row.message_id, row.status) for row in read_jsonl(first.output_path)]
    assert len(statuses) == 4
    assert statuses[-1] == (TWO, "ok")


async def test_the_result_counts_the_session_and_the_run(audit, fake_provider):
    await unfinished_run(audit, fake_provider)

    second = await run_audit_async(audit())

    assert (second.n_ok, second.run_n_ok) == (2, 3)


async def test_the_manifest_counts_each_response_once_across_sessions(audit, fake_provider):
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    first = await run_audit_async(audit())

    await run_audit_async(audit())

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert (manifest["n_ok"], manifest["n_error"]) == (3, 0)
    assert manifest["status"] == "complete"


async def test_a_resumed_run_keeps_when_it_started(audit, fake_provider):
    first = await run_audit_async(audit())
    started = json.loads(first.manifest_path.read_text(encoding="utf-8"))["started_at"]
    keep_only(first, (ONE, 0))

    await run_audit_async(audit())

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["started_at"] == started


# --- a new run, on request ---------------------------------------------------------


async def test_a_run_records_every_taxman_version_that_collected_it(
    audit, fake_provider, monkeypatch
):
    """A newer taxman may have collected the later rows; the manifest must say so."""
    import ai_taxman

    monkeypatch.setattr(ai_taxman, "__version__", "0.1.0")
    first = await unfinished_run(audit, fake_provider)
    monkeypatch.setattr(ai_taxman, "__version__", "0.2.0")
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    await run_audit_async(audit())

    await run_audit_async(audit())

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["taxman_versions"] == ["0.1.0", "0.2.0"]


async def test_a_new_run_starts_over_beside_the_last_one(audit, fake_provider):
    first = await run_audit_async(audit())
    keep_only(first, (ONE, 0))
    fake_provider.sent.clear()

    second = await run_audit_async(audit(), new_run=True)

    assert sent(fake_provider) == sorted([(ONE, 0), (TWO, 0), (THREE, 0)])
    assert second.run_id != first.run_id
    assert second.output_path.parent.parent == first.output_path.parent.parent
    assert second.resumed is False


async def test_collecting_after_a_new_run_finishes_the_new_one(audit, fake_provider):
    await run_audit_async(audit())
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    second = await run_audit_async(audit(), new_run=True)
    fake_provider.sent.clear()

    third = await run_audit_async(audit())

    assert third.run_id == second.run_id
    assert sent(fake_provider) == [(TWO, 0)]


async def test_a_new_run_needs_run_id_in_the_output_directory(project, audit, fake_provider):
    """Without {run_id}, `output.dir` has room for one run, so a second has nowhere to go."""
    from ai_taxman.core.errors import ConfigError

    single = "output:\n  dir: data/{audit}\n  log_dir: logs/{audit}"
    await run_audit_async(audit(single))

    with pytest.raises(ConfigError, match=r"\{run_id\}") as caught:
        await run_audit_async(audit(single), new_run=True)

    assert f"move {project / 'data' / 'probe'} elsewhere" in str(caught.value)


# --- an audit that has changed is never resumed ----------------------------------


async def unfinished_run(audit, fake_provider, *blocks):
    """A run of the audit with ONE answered and the rest missing."""
    first = await run_audit_async(audit(*blocks))
    keep_only(first, (ONE, 0))
    fake_provider.sent.clear()
    return first


async def test_a_changed_setting_stops_the_run_being_resumed(audit, fake_provider):
    from ai_taxman.core.errors import AuditChangedError

    await unfinished_run(audit, fake_provider)

    with pytest.raises(AuditChangedError) as caught:
        await run_audit_async(audit("execution:\n  max_concurrency: 2"))

    assert "execution.max_concurrency: 8 -> 2" in str(caught.value)
    assert fake_provider.sent == []


async def test_a_changed_model_setting_is_named(audit, fake_provider):
    from ai_taxman.core.errors import AuditChangedError

    await unfinished_run(audit, fake_provider)
    changed = audit()
    changed.model["temperature"] = 0.7

    with pytest.raises(AuditChangedError, match=r"model\.temperature: \(not set\) -> 0\.7"):
        await run_audit_async(changed)


async def test_changed_messages_stop_the_run_being_resumed(project, audit, fake_provider):
    from ai_taxman.core.errors import AuditChangedError

    await unfinished_run(audit, fake_provider)
    (project / "messages" / "probe.txt").write_text("one\ntwo\nfour\nfive\n", encoding="utf-8")

    with pytest.raises(AuditChangedError, match="messages: 2 added, 1 removed"):
        await run_audit_async(audit())


async def test_a_changed_system_prompt_stops_the_run_being_resumed(project, audit, fake_provider):
    from ai_taxman.core.errors import AuditChangedError

    (project / "prompts").mkdir()
    prompt = project / "prompts" / "neutral.txt"
    prompt.write_text("Be terse.", encoding="utf-8")
    await unfinished_run(audit, fake_provider, "system_prompt: prompts/neutral.txt")
    prompt.write_text("Be thorough.", encoding="utf-8")

    with pytest.raises(AuditChangedError, match="system prompt"):
        await run_audit_async(audit("system_prompt: prompts/neutral.txt"))


async def test_the_refusal_says_how_to_start_over(audit, fake_provider):
    from ai_taxman.core.errors import AuditChangedError

    first = await unfinished_run(audit, fake_provider)

    with pytest.raises(AuditChangedError) as caught:
        await run_audit_async(audit("execution:\n  repeats: 2"))

    assert first.run_id in str(caught.value)
    assert "--new-run" in str(caught.value)


async def test_a_one_run_folder_is_told_to_move_the_run_not_to_use_new_run(
    project, audit, fake_provider
):
    """`--new-run` always refuses where `output.dir` has no {run_id}."""
    from ai_taxman.core.errors import AuditChangedError

    single = "output:\n  dir: data/{audit}\n  log_dir: logs/{audit}"
    await unfinished_run(audit, fake_provider, single)

    with pytest.raises(AuditChangedError) as caught:
        await run_audit_async(audit(single, "execution:\n  repeats: 2"))

    assert "--new-run" not in str(caught.value)
    assert f"move {project / 'data' / 'probe'} elsewhere and collect again" in str(caught.value)


async def test_comments_and_line_order_are_not_changes(project, audit, fake_provider):
    """What is sent is the same, so the run is the same run."""
    first = await unfinished_run(audit, fake_provider)
    (project / "messages" / "probe.txt").write_text(
        "# reordered\nthree\n\ntwo\none\n", encoding="utf-8"
    )

    second = await run_audit_async(audit())

    assert second.run_id == first.run_id
    assert sent(fake_provider) == sorted([(TWO, 0), (THREE, 0)])


# --- a complete run is left alone ----------------------------------------------------


async def test_a_complete_run_is_not_collected_again(audit, fake_provider):
    first = await run_audit_async(audit())
    fake_provider.sent.clear()

    second = await run_audit_async(audit())

    assert fake_provider.sent == []
    assert second.already_complete is True
    assert second.run_id == first.run_id
    assert first.already_complete is False


async def test_a_run_killed_after_its_last_response_is_marked_complete(audit, fake_provider):
    """Every response is on disk, so the manifest stops saying otherwise."""
    first = await run_audit_async(audit())
    keep_only(first, (ONE, 0), (TWO, 0), (THREE, 0))
    killed = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    killed.update(status="running", n_ok=0, finished_at=None)
    first.manifest_path.write_text(json.dumps(killed), encoding="utf-8")

    await run_audit_async(audit())

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["n_ok"] == 3
    assert manifest["finished_at"] is not None


# --- what a killed run left behind is made whole before it is added to -------------


@pytest.mark.parametrize("compress", [False, True])
async def test_a_run_that_ends_whole_is_not_read_again_to_repair_it(
    audit, fake_provider, monkeypatch, compress
):
    """Planning has just read it to the end, so it knows: a compressed run would
    otherwise be decompressed all over again to find nothing to repair."""
    from ai_taxman.core import runner

    output = (
        "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
        f"  compress: {compress}"
    )
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    await run_audit_async(audit(output))
    repaired = []
    monkeypatch.setattr(runner, "repair_tail", lambda path: repaired.append(path) or False)

    resumed = await run_audit_async(audit(output))

    assert resumed.resumed is True
    assert resumed.output_path.suffix == (".gz" if compress else ".jsonl")
    assert repaired == []


async def test_a_whole_last_row_that_lost_its_newline_is_kept(audit, fake_provider):
    """It was counted as answered, so it is not sent again - and must not be dropped."""
    first = await run_audit_async(audit())
    keep_only(first, (ONE, 0), (TWO, 0))
    first.output_path.write_bytes(first.output_path.read_bytes()[:-1])
    fake_provider.sent.clear()

    await run_audit_async(audit())

    assert sent(fake_provider) == [(THREE, 0)]
    rows = list(read_jsonl(first.output_path))
    assert sorted(row.message_id for row in rows) == sorted([ONE, TWO, THREE])


# --- one collector per run at a time ------------------------------------------------


async def test_a_run_being_collected_elsewhere_is_not_resumed(audit, fake_provider):
    from ai_taxman.core.errors import RunInProgressError
    from ai_taxman.core.runs import hold_run

    first = await unfinished_run(audit, fake_provider)

    with hold_run(first.output_path.parent), pytest.raises(RunInProgressError) as caught:
        await run_audit_async(audit())

    assert first.run_id in str(caught.value)
    assert fake_provider.sent == []


async def test_the_refusal_names_the_run_not_its_folder(audit, fake_provider):
    """Without {run_id} in `output.dir`, the folder is named after the audit."""
    from ai_taxman.core.errors import RunInProgressError
    from ai_taxman.core.runs import hold_run

    single = "output:\n  dir: data/{audit}\n  log_dir: logs/{audit}"
    first = await unfinished_run(audit, fake_provider, single)

    with hold_run(first.output_path.parent), pytest.raises(RunInProgressError) as caught:
        await run_audit_async(audit(single))

    assert str(caught.value).startswith(f"Run {first.run_id} in ")


async def test_the_refusal_names_the_process_collecting_the_run(audit, fake_provider):
    """The one holding the run - never a stale pid a killed run left behind,
    which the OS may since have given to anything."""
    import os

    from ai_taxman.core.errors import RunInProgressError
    from ai_taxman.core.runs import hold_run

    first = await unfinished_run(audit, fake_provider)
    (first.output_path.parent / "collect.pid").write_text("4242\n", encoding="utf-8")

    with (
        hold_run(first.output_path.parent),
        pytest.raises(RunInProgressError, match=f"kill {os.getpid()}`"),
    ):
        await run_audit_async(audit())


def test_a_refused_collect_leaves_the_collectors_pid_file_alone(project):
    """It never held the run, so the pid file is not its to write, or to remove."""
    import fcntl

    from ai_taxman.core.errors import RunInProgressError
    from ai_taxman.core.runs import hold_run

    directory = project / "data" / "probe" / "run"
    directory.mkdir(parents=True)
    pid_file = directory / "collect.pid"
    with (directory / "collect.lock").open("a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        pid_file.write_text("4242\n", encoding="utf-8")

        with pytest.raises(RunInProgressError, match="kill 4242"), hold_run(directory):
            pass

    assert pid_file.read_text(encoding="utf-8") == "4242\n"


def test_a_check_as_a_run_is_claimed_does_not_refuse_it(project):
    """Planning anywhere - `validate` included - checks for a collector by taking
    the run's lock for an instant. A collect claiming the run at that instant
    waits it out, rather than call the run taken and name a stale pid."""
    import fcntl
    import threading

    from ai_taxman.core.runs import hold_run

    directory = project / "data" / "probe" / "run"
    directory.mkdir(parents=True)
    (directory / "collect.pid").write_text("4242\n", encoding="utf-8")
    check = (directory / "collect.lock").open("a")
    fcntl.flock(check.fileno(), fcntl.LOCK_SH)
    threading.Timer(0.1, check.close).start()

    with hold_run(directory):
        pass


def test_a_pid_file_that_is_not_text_does_not_break_the_end_of_a_run(project):
    """Overwritten by something else; it is not this run's, so it is left alone,
    and whatever ended the run is not masked by an error in tidying up."""
    from ai_taxman.core.runs import hold_run

    directory = project / "data" / "probe" / "run"
    with hold_run(directory):
        (directory / "collect.pid").write_bytes(b"\xff\xfe")

    assert (directory / "collect.pid").read_bytes() == b"\xff\xfe"


async def test_a_finished_run_leaves_no_pid_file(audit, fake_provider):
    result = await run_audit_async(audit())

    assert not (result.output_path.parent / "collect.pid").exists()


@pytest.mark.parametrize("error", ["ENOLCK", "ENOTSUP"])
async def test_a_filesystem_that_cannot_lock_still_collects(
    audit, fake_provider, monkeypatch, caplog, error
):
    """NFS without lockd, or an SMB mount: one collector per run cannot be enforced
    there, which is no reason to refuse every collect, the first included."""
    import errno
    import os

    def unsupported(fd, operation):
        raise OSError(getattr(errno, error), os.strerror(getattr(errno, error)))

    first = await unfinished_run(audit, fake_provider)
    fake_provider.sent.clear()
    monkeypatch.setattr("fcntl.flock", unsupported)

    second = await run_audit_async(audit())

    assert second.run_id == first.run_id
    assert sent(fake_provider) == sorted([(TWO, 0), (THREE, 0)])
    assert "cannot be locked" in caplog.text


@pytest.fixture
def nfs_locks(monkeypatch):
    """`flock` as Linux emulates it on NFS, with byte-range locks: an exclusive
    lock needs the file open for writing, and a shared one open for reading."""
    import errno
    import fcntl
    import os

    real = fcntl.flock

    def flock(fd, operation):
        mode = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
        needs = os.O_RDONLY if operation & fcntl.LOCK_EX else os.O_WRONLY
        if mode == needs:
            raise OSError(errno.EBADF, os.strerror(errno.EBADF))
        return real(fd, operation)

    monkeypatch.setattr("fcntl.flock", flock)


async def test_an_nfs_mount_locks_the_audit_and_its_run(audit, fake_provider, nfs_locks, caplog):
    await unfinished_run(audit, fake_provider)

    await run_audit_async(audit())

    assert "cannot be locked" not in caplog.text


def test_the_audit_lock_holds_on_nfs(audit, nfs_locks):
    import fcntl

    from ai_taxman.core.runs import hold_audit

    config = audit()
    with (
        hold_audit(config),
        config.source_path.open("a") as other,
        pytest.raises(BlockingIOError),
    ):
        fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_an_audit_that_cannot_be_written_is_still_locked(audit):
    """A read-only checkout: a local filesystem locks a file opened only to read."""
    import fcntl

    from ai_taxman.core.runs import hold_audit

    config = audit()
    config.source_path.chmod(0o444)
    with (
        hold_audit(config),
        config.source_path.open("rb") as other,
        pytest.raises(BlockingIOError),
    ):
        fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("held", ["run", "audit"])
def test_an_error_while_unlocked_is_not_blamed_on_the_lock(project, audit, monkeypatch, held):
    """The collect goes on unlocked, and a crash later in it is its own: a
    traceback that opens with the lock's error sends the user after the lock."""
    import errno
    import os

    from ai_taxman.core.runs import hold_audit, hold_run

    def unsupported(fd, operation):
        raise OSError(errno.ENOLCK, os.strerror(errno.ENOLCK))

    monkeypatch.setattr("fcntl.flock", unsupported)
    hold = hold_run(project / "data" / "run") if held == "run" else hold_audit(audit())

    with pytest.raises(RuntimeError) as caught, hold:
        raise RuntimeError("the provider fell over")

    assert caught.value.__context__ is None


async def test_two_collects_started_together_send_the_audit_once(project, audit, fake_provider):
    """Each plans while the other is still starting; only one may claim the work."""
    import asyncio

    from ai_taxman.core.errors import RunInProgressError

    fake_provider.startup_delay = 0.05

    results = await asyncio.gather(
        run_audit_async(audit()), run_audit_async(audit()), return_exceptions=True
    )

    assert sent(fake_provider) == sorted([(ONE, 0), (TWO, 0), (THREE, 0)])
    assert [type(result) for result in results].count(RunInProgressError) == 1
    assert len(list((project / "data" / "probe").iterdir())) == 1


async def test_a_collect_that_loses_a_one_run_folder_is_told_the_run_is_taken(audit, fake_provider):
    """Not that `output.dir` is wrong: it is fine, and the other collect has the run."""
    import asyncio

    single = "output:\n  dir: data/{audit}\n  log_dir: logs/{audit}"
    fake_provider.startup_delay = 0.05
    fake_provider.delay = 0.05

    results = await asyncio.gather(
        run_audit_async(audit(single)),
        run_audit_async(audit(single)),
        return_exceptions=True,
    )

    assert sorted(type(result).__name__ for result in results) == [
        "RunInProgressError",
        "RunResult",
    ]
    assert sent(fake_provider) == sorted([(ONE, 0), (TWO, 0), (THREE, 0)])


async def test_a_collect_that_starts_after_another_finished_the_audit_says_it_is_complete(
    audit, fake_provider
):
    """Rather than refuse to start a second run no one asked for."""
    import asyncio

    finished = asyncio.Event()
    slow = type(fake_provider)(startup_gate=finished)

    async def collect_then_let_the_slow_one_start():
        result = await run_audit_async(audit())
        finished.set()
        return result

    late, prompt = await asyncio.gather(
        run_audit_async(audit(), provider=slow), collect_then_let_the_slow_one_start()
    )

    assert slow.sent == []
    assert late.already_complete is True
    assert late.run_id == prompt.run_id
    assert prompt.n_ok == 3


async def test_a_collect_that_was_slow_to_start_does_not_resend_what_another_sent(
    audit, fake_provider
):
    """Its plan is made again once it holds the run, not trusted from before."""
    import asyncio

    first = await unfinished_run(audit, fake_provider)
    finished = asyncio.Event()
    slow = type(fake_provider)(startup_gate=finished)

    async def collect_then_let_the_slow_one_start():
        result = await run_audit_async(audit())
        finished.set()
        return result

    late, prompt = await asyncio.gather(
        run_audit_async(audit(), provider=slow), collect_then_let_the_slow_one_start()
    )

    assert slow.sent == []
    assert late.already_complete is True
    assert prompt.n_ok == 2
    pairs = [(row.message_id, row.repeat) for row in read_jsonl(first.output_path)]
    assert sorted(pairs) == sorted([(ONE, 0), (TWO, 0), (THREE, 0)])


@pytest.fixture
def reads(monkeypatch):
    """How many times the run's responses are read to plan it."""
    from ai_taxman.core import runs

    counted = []
    read = runs._responses_so_far
    monkeypatch.setattr(runs, "_responses_so_far", lambda path: counted.append(path) or read(path))
    return counted


async def test_a_resumed_run_is_read_from_disk_once(audit, fake_provider, reads):
    """Planned once, under the audit's lock: a large compressed run's file takes a
    while to read."""
    await unfinished_run(audit, fake_provider)

    await run_audit_async(audit())

    assert len(reads) == 1


async def test_a_run_holds_its_lock_while_it_is_collected(project, audit, fake_provider, until):
    import asyncio

    from ai_taxman.core.errors import RunInProgressError
    from ai_taxman.core.runs import hold_run

    fake_provider.gate = asyncio.Event()
    task = asyncio.create_task(run_audit_async(audit()))
    # Written once the run holds its lock.
    await until(lambda: list((project / "data" / "probe").glob("*/manifest.json")))
    (directory,) = (project / "data" / "probe").iterdir()

    with pytest.raises(RunInProgressError), hold_run(directory):
        pass

    fake_provider.gate.set()
    await task
    with hold_run(directory):  # released once the run is over
        pass
