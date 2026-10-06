"""Finding an audit's runs on disk, and deciding what `collect` does next."""

import json
import textwrap

import pytest

from ai_taxman.core.config import load_audit
from ai_taxman.core.discovery import write_marker
from ai_taxman.core.records import RESPONSE_SCHEMA_VERSION, RunManifest
from ai_taxman.core.runs import MANIFEST_FILENAME, find_runs, latest_run


@pytest.fixture
def project(tmp_path, flat_layout):
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "audits").mkdir()
    write_marker(tmp_path, flat_layout)
    return tmp_path


def audit(project, output_dir="data/{audit}/{run_id}", name="probe"):
    path = project / "audits" / f"{name}.yaml"
    path.write_text(
        textwrap.dedent(
            f"""\
            audit: {name}
            provider: fake
            messages: messages/probe.txt
            output:
              dir: {output_dir}
              log_dir: logs/{{audit}}
            model:
              name: fake-1
            """
        ),
        encoding="utf-8",
    )
    return load_audit(path)


def fake_run(directory, run_id, started_at, audit_name="probe"):
    """A run directory as a collect leaves it: a manifest naming the run."""
    manifest = RunManifest(
        audit=audit_name,
        run_id=run_id,
        provider="fake",
        model="fake-1",
        taxman_version="0",
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


def test_an_audit_never_collected_has_no_runs(project):
    config = audit(project)

    assert find_runs(config) == []
    assert latest_run(config) is None


def test_the_latest_run_is_the_one_started_last(project):
    config = audit(project)
    fake_run(project / "data/probe/b-run", "b-run", "2026-10-01T00:00:00.000000Z")
    fake_run(project / "data/probe/a-run", "a-run", "2026-10-02T00:00:00.000000Z")

    latest = latest_run(config)

    assert [run.run_id for run in find_runs(config)] == ["b-run", "a-run"]
    assert latest is not None
    assert latest.run_id == "a-run"
    assert latest.directory == project / "data/probe/a-run"


def test_runs_of_another_audit_in_a_shared_folder_are_not_this_audits(project):
    config = audit(project, output_dir="data/{run_id}")
    fake_run(project / "data/mine", "mine", "2026-10-01T00:00:00.000000Z")
    fake_run(project / "data/theirs", "theirs", "2026-10-02T00:00:00.000000Z", "other")

    assert [run.run_id for run in find_runs(config)] == ["mine"]


def test_runs_are_found_wherever_the_run_id_sits_in_the_path(project):
    config = audit(project, output_dir="data/{run_id}/{audit}")
    fake_run(project / "data/r1/probe", "r1", "2026-10-01T00:00:00.000000Z")

    assert [run.run_id for run in find_runs(config)] == ["r1"]


def test_a_folder_without_a_manifest_is_not_a_run(project):
    """A background parent makes the directory before its child writes anything."""
    config = audit(project)
    (project / "data/probe/empty").mkdir(parents=True)

    assert find_runs(config) == []


def test_an_unreadable_manifest_is_an_error_not_a_missing_run(project):
    """Skipping it could resume an older run, or start over beside a broken one."""
    from ai_taxman.core.errors import ResponseFileError

    config = audit(project)
    broken = project / "data/probe/broken"
    broken.mkdir(parents=True)
    (broken / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")

    with pytest.raises(ResponseFileError, match="manifest"):
        find_runs(config)
