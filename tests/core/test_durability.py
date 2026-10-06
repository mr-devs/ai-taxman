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
from ai_taxman.core.runner import MANIFEST_FILENAME, run_audit_async
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


# --- the manifest describes a run that has not finished -------------------


async def test_the_manifest_is_on_disk_before_the_first_response_lands(project, fake_provider):
    """A killed run still says what it was running, and with what."""
    fake_provider.delay = 0.2
    config = audit(project)

    task = asyncio.create_task(run_audit_async(config))
    await asyncio.sleep(0.05)

    written = list((project / "data" / "probe").iterdir())
    assert len(written) == 1
    manifest = manifest_at(written[0] / MANIFEST_FILENAME)

    assert manifest.status == "running"
    assert manifest.started_at is not None
    assert manifest.finished_at is None
    assert manifest.n_messages == 3
    assert manifest.config  # the settings this run actually used, frozen

    await task


async def test_the_manifest_says_how_many_responses_to_expect(project, fake_provider):
    """Without the denominator, a truncated run looks like a complete short one."""
    fake_provider.delay = 0.2
    config = audit(project, "execution:\n  repeats: 4")

    task = asyncio.create_task(run_audit_async(config))
    await asyncio.sleep(0.05)

    directory = next((project / "data" / "probe").iterdir())
    manifest = manifest_at(directory / MANIFEST_FILENAME)

    assert manifest.n_messages * manifest.repeats == 12

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


async def test_a_cancelled_run_leaves_a_manifest_that_is_not_complete(project, fake_provider):
    fake_provider.delay = 0.5
    task = asyncio.create_task(run_audit_async(audit(project)))
    await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    directory = next((project / "data" / "probe").iterdir())
    manifest = manifest_at(directory / MANIFEST_FILENAME)

    assert manifest.status != "complete"


# --- responses land as they arrive ---------------------------------------


async def test_responses_are_readable_while_the_run_is_still_going(project, fake_provider):
    """Not buffered in memory and flushed at the end - written as they land."""
    fake_provider.delay = 0.15
    config = audit(project, "execution:\n  max_concurrency: 1")

    task = asyncio.create_task(run_audit_async(config))
    await asyncio.sleep(0.25)

    directory = next((project / "data" / "probe").iterdir())
    landed = list(read_jsonl(directory / "responses.jsonl"))

    assert 0 < len(landed) < 3

    await task


async def test_responses_land_as_they_arrive_when_compressed(project, fake_provider):
    """A gzip stream is flushed too, so a killed run is not an unreadable file."""
    fake_provider.delay = 0.15
    config = audit(
        project,
        "execution:\n  max_concurrency: 1",
        "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n  compress: true",
    )

    task = asyncio.create_task(run_audit_async(config))
    await asyncio.sleep(0.25)

    directory = next((project / "data" / "probe").iterdir())
    landed = list(read_jsonl(directory / "responses.jsonl.gz"))

    assert 0 < len(landed) < 3

    await task


# --- one directory, one run -------------------------------------------------


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
