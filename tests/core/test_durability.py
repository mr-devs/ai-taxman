"""A run is on disk while it runs, not after it finishes.

An audit can take hours and can be killed at any point in them - by a timeout, a
laptop lid, a `kill` from the background flag. Whatever came back before that
moment is data the user paid for, so it has to survive, and the run directory has
to describe itself even when nothing finished.
"""

import asyncio
import json
import textwrap

import pytest

from ai_taxman.core.config import load_audit
from ai_taxman.core.discovery import write_marker
from ai_taxman.core.errors import ConfigError
from ai_taxman.core.messages import message_id
from ai_taxman.core.records import RunManifest
from ai_taxman.core.runner import MANIFEST_FILENAME, collect_run_async, preflight, run_audit_async
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
    body = (
        "audit: probe\nprovider: fake\nmessages: messages/probe.txt\n"
        "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
        "model:\n  name: fake-1\n"
    )
    path = project / "audits" / "probe.yaml"
    path.write_text(
        textwrap.dedent(body + "".join(block.rstrip() + "\n" for block in blocks)).lstrip(),
        encoding="utf-8",
    )
    return load_audit(path)


def manifest_at(path):
    return RunManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))


def manifests(project):
    return list((project / "data" / "probe").glob(f"*/{MANIFEST_FILENAME}"))


# --- the manifest describes a run that has not finished -------------------


async def test_the_manifest_is_on_disk_before_the_first_response_lands(
    project, fake_provider, until
):
    """A killed run still says what it was running, and with what."""
    fake_provider.gate = asyncio.Event()
    task = asyncio.create_task(run_audit_async(audit(project)))
    await until(lambda: manifests(project))

    (path,) = manifests(project)
    manifest = manifest_at(path)

    assert list(read_jsonl(path.parent / "responses.jsonl")) == []
    assert manifest.status == "running"
    assert manifest.started_at is not None
    assert manifest.finished_at is None
    assert manifest.n_messages == 3
    assert manifest.config  # the settings this run actually used, frozen

    fake_provider.gate.set()
    await task


async def test_the_manifest_says_how_many_responses_to_expect(project, fake_provider, until):
    """Without the denominator, a truncated run looks like a complete short one."""
    fake_provider.gate = asyncio.Event()
    task = asyncio.create_task(run_audit_async(audit(project, "execution:\n  repeats: 4")))
    await until(lambda: manifests(project))

    (path,) = manifests(project)
    manifest = manifest_at(path)

    assert manifest.n_messages * manifest.repeats == 12

    fake_provider.gate.set()
    await task


async def test_a_finished_run_is_marked_complete(project, fake_provider):
    result = await run_audit_async(audit(project))

    manifest = manifest_at(result.manifest_path)

    assert manifest.status == "complete"
    assert manifest.finished_at is not None
    assert manifest.n_ok == 3


async def test_a_run_stopped_by_an_error_says_so(project, fake_provider):
    fake_provider.failures = {TWO: [RuntimeError("nope")]}

    result = await run_audit_async(audit(project, "execution:\n  on_error: stop"))

    manifest = manifest_at(result.manifest_path)

    assert manifest.status == "stopped_early"
    assert manifest.n_error == 1


async def test_a_run_that_raises_still_finalises_its_manifest(project, fake_provider, monkeypatch):
    """The manifest is the record of what happened, a crash included.

    A failure mid-run - a full disk, a bug - must not leave the manifest saying
    `running` forever next to data that is going nowhere.
    """
    config = audit(project)

    async def explode(*args, **kwargs):
        raise RuntimeError("the run fell over")

    monkeypatch.setattr("ai_taxman.core.runner._dispatch", explode)

    with pytest.raises(RuntimeError):
        await run_audit_async(config)

    directory = next((project / "data" / "probe").iterdir())
    manifest = manifest_at(directory / MANIFEST_FILENAME)

    assert manifest.status == "failed"
    assert manifest.finished_at is not None


async def test_a_cancelled_run_leaves_a_manifest_that_is_not_complete(
    project, fake_provider, until
):
    fake_provider.gate = asyncio.Event()  # never set: cancelled with everything in flight
    task = asyncio.create_task(run_audit_async(audit(project)))
    await until(lambda: manifests(project))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    (path,) = manifests(project)
    assert manifest_at(path).status == "interrupted"


# --- responses land as they arrive ---------------------------------------


@pytest.mark.parametrize(
    ("compress", "filename"),
    [(False, "responses.jsonl"), (True, "responses.jsonl.gz")],
)
async def test_each_response_is_on_disk_as_soon_as_it_is_recorded(
    project, fake_provider, compress, filename
):
    """Not buffered in memory and flushed at the end - written as they land, a gzip
    stream included, so a killed run is not an unreadable file. Read from disk as
    each response is handed back, while the run is still writing."""
    config = audit(
        project,
        "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
        f"  compress: {compress}",
    )
    on_disk = []

    def read_back(record):
        (path,) = manifests(project)
        landed = [row.message_id for row in read_jsonl(path.parent / filename)]
        on_disk.append((record.message_id in landed, len(landed)))

    await run_audit_async(config, on_record=read_back)

    assert on_disk == [(True, 1), (True, 2), (True, 3)]


# --- one directory, one run -------------------------------------------------


async def test_a_manifest_write_cut_short_leaves_the_last_one_whole(
    project, fake_provider, monkeypatch
):
    """A manifest that cannot be read stops every later collect of the audit, so a
    crash while one is written must leave the one before it, not half of each."""
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    first = await run_audit_async(audit(project))
    before = first.manifest_path.read_bytes()

    def crash(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr("os.replace", crash)
    with pytest.raises(OSError, match="No space"):
        await run_audit_async(audit(project))

    assert first.manifest_path.read_bytes() == before
    assert sorted(path.name for path in first.manifest_path.parent.iterdir()) == [
        "collect.lock",
        MANIFEST_FILENAME,
        "responses.jsonl",
    ]


async def test_two_audits_never_share_a_run_directory(project, fake_provider):
    """An `output.dir` naming neither {audit} nor {run_id} points both at one place,
    which would leave two runs' data described by a manifest naming only one."""
    shared = "output:\n  dir: data/shared\n  log_dir: logs/{audit}"
    await run_audit_async(audit(project, shared))
    other = project / "audits" / "other.yaml"
    other.write_text(
        (project / "audits" / "probe.yaml")
        .read_text(encoding="utf-8")
        .replace("audit: probe", "audit: other"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as caught:
        await run_audit_async(load_audit(other))

    message = str(caught.value)
    assert "run_id" in message
    assert "output.dir" in message


async def test_a_run_id_is_never_given_another_runs_directory(project, fake_provider):
    """Rows labelled with one run id must not land under a manifest naming another."""
    single = "output:\n  dir: data/{audit}\n  log_dir: logs/{audit}"
    first = await run_audit_async(audit(project, single))
    fake_provider.sent.clear()

    config = audit(project, single)
    checked = preflight(config, fake_provider)

    with pytest.raises(ConfigError, match=f"already holds run {first.run_id}"):
        await collect_run_async(
            config, checked, provider=fake_provider, run_id="20261008T000000Z-000000"
        )

    assert fake_provider.sent == []


async def test_an_audit_with_one_run_directory_is_resumed_in_place(project, fake_provider):
    """Without {run_id} in `output.dir` there is room for one run, and it is finished."""
    single = "output:\n  dir: data/{audit}\n  log_dir: logs/{audit}"
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    first = await run_audit_async(audit(project, single))
    fake_provider.sent.clear()

    second = await run_audit_async(audit(project, single))

    assert [request.message.id for request in fake_provider.sent] == [TWO]
    assert second.run_id == first.run_id
    assert len(list(read_jsonl(second.output_path))) == 4
