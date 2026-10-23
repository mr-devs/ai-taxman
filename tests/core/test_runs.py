"""Finding an audit's runs on disk, and deciding what `collect` does next."""

import json

import pytest

from ai_taxman.core.records import RESPONSE_SCHEMA_VERSION, RunManifest
from ai_taxman.core.runs import MANIFEST_FILENAME, find_runs, latest_run


def fake_run(directory, run_id, started_at, audit_name="probe"):
    """A run directory as a collect leaves it: a manifest naming the run."""
    manifest = RunManifest(
        audit=audit_name,
        run_id=run_id,
        provider="fake",
        model="fake-1",
        taxman_versions=["0"],
        schema_version=RESPONSE_SCHEMA_VERSION,
        messages_path="messages/probe.txt",
        message_ids=[],
        n_messages=3,
        repeats=1,
        started_at=started_at,
    )
    directory.mkdir(parents=True)
    (directory / MANIFEST_FILENAME).write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    return directory


def test_an_audit_never_collected_has_no_runs(audit):
    config = audit()

    assert find_runs(config) == []
    assert latest_run(config) is None


def test_the_latest_run_is_the_one_started_last(project, audit):
    """By the moment each started, not by its text: local times either side of a
    clock change sort the wrong way as strings (01:10 EST comes after 01:30 EDT)."""
    config = audit()
    fake_run(project / "data/probe/b-run", "b-run", "2026-11-01T01:30:00.000000-04:00")
    fake_run(project / "data/probe/a-run", "a-run", "2026-11-01T01:10:00.000000-05:00")

    latest = latest_run(config)

    assert [run.run_id for run in find_runs(config)] == ["b-run", "a-run"]
    assert latest is not None
    assert latest.run_id == "a-run"
    assert latest.directory == project / "data/probe/a-run"


def test_runs_of_another_audit_in_a_shared_folder_are_not_this_audits(project, audit):
    config = audit("output:\n  dir: data/{run_id}\n  log_dir: logs/{audit}")
    fake_run(project / "data/mine", "mine", "2026-10-01T00:00:00.000000-04:00")
    fake_run(project / "data/theirs", "theirs", "2026-10-02T00:00:00.000000-04:00", "other")

    assert [run.run_id for run in find_runs(config)] == ["mine"]


def test_runs_are_found_wherever_the_run_id_sits_in_the_path(project, audit):
    config = audit("output:\n  dir: data/{run_id}/{audit}\n  log_dir: logs/{audit}")
    fake_run(project / "data/r1/probe", "r1", "2026-10-01T00:00:00.000000-04:00")

    assert [run.run_id for run in find_runs(config)] == ["r1"]


def test_a_folder_without_a_manifest_is_not_a_run(project, audit):
    """A collect killed after making the directory, before writing the manifest."""
    config = audit()
    (project / "data/probe/empty").mkdir(parents=True)

    assert find_runs(config) == []


@pytest.mark.parametrize(
    ("run_id", "folder"), [("x/../odd", "odd"), ("../probe/odd", "odd"), ("my run", "my run")]
)
def test_a_manifest_whose_run_id_taxman_would_never_make_is_an_error(
    project, audit, run_id, folder
):
    """A run id goes into paths - the log's name among them - and into every row, so
    one that only resolves to its own folder by way of `..` is not taken on trust."""
    from ai_taxman.core.errors import ResponseFileError

    config = audit()
    fake_run(project / "data/probe" / folder, run_id, "2026-10-01T00:00:00.000000-04:00")

    with pytest.raises(ResponseFileError, match="run id"):
        find_runs(config)


def test_an_unreadable_manifest_is_an_error_not_a_missing_run(project, audit):
    """Skipping it could resume an older run, or start over beside a broken one."""
    from ai_taxman.core.errors import ResponseFileError

    config = audit()
    broken = project / "data/probe/broken"
    broken.mkdir(parents=True)
    (broken / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")

    with pytest.raises(ResponseFileError, match="manifest"):
        find_runs(config)
