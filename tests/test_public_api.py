"""The Python API: the same workflow as the CLI, in three functions."""

import ai_taxman
from ai_taxman import AuditConfig, ResponseRecord, load_audit, read_messages, run_audit


def make_project(tmp_path):
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "audits").mkdir()
    path = tmp_path / "audits" / "probe.yaml"
    path.write_text(
        "audit: probe\nprovider: fake\nmessages: messages/probe.txt\nmodel:\n  name: fake-1\n",
        encoding="utf-8",
    )
    return path


def test_version_is_exposed():
    assert ai_taxman.__version__


def test_the_public_names_are_importable_from_the_top_level():
    for name in ("run_audit", "load_audit", "read_messages", "AuditConfig", "ResponseRecord"):
        assert name in ai_taxman.__all__


def test_read_messages_returns_the_messages(tmp_path):
    make_project(tmp_path)

    messages = read_messages(tmp_path / "messages" / "probe.txt")

    assert [m.text for m in messages] == ["one", "two"]


def test_load_audit_returns_a_config(tmp_path):
    config = load_audit(make_project(tmp_path))

    assert isinstance(config, AuditConfig)
    assert config.provider == "fake"


def test_run_audit_takes_a_path_and_runs_synchronously(tmp_path, fake_provider):
    result = run_audit(make_project(tmp_path))

    assert result.n_ok == 2
    assert result.output_path.is_file()


def test_run_audit_takes_a_loaded_config(tmp_path, fake_provider):
    config = load_audit(make_project(tmp_path))
    config.execution.repeats = 3

    assert run_audit(config).n_ok == 6


def test_run_audit_reports_progress(tmp_path, fake_provider):
    seen = []

    run_audit(make_project(tmp_path), on_record=seen.append)

    assert len(seen) == 2
    assert all(isinstance(record, ResponseRecord) for record in seen)
