"""The Python API: the same workflow as the CLI, in three functions."""

import ai_taxman
from ai_taxman import AuditConfig, ResponseRecord, load_audit, read_messages, run_audit


def make_project(tmp_path):
    from ai_taxman.core.discovery import Layout, write_marker

    write_marker(
        tmp_path,
        Layout(data="data", audits="audits", messages="messages", prompts="prompts", logs="logs"),
    )
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "audits").mkdir()
    path = tmp_path / "audits" / "probe.yaml"
    path.write_text(
        "audit: probe\nprovider: fake\nmessages: messages/probe.txt\n"
        "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
        "model:\n  name: fake-1\n",
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


def test_load_audit_takes_an_audit_name_like_the_cli(tmp_path, monkeypatch, flat_layout):
    from ai_taxman.core.discovery import write_marker

    make_project(tmp_path)
    write_marker(tmp_path, flat_layout)
    (tmp_path / "messages" / "nested").mkdir()
    monkeypatch.chdir(tmp_path / "messages" / "nested")

    config = load_audit("probe")

    assert config.source_path == tmp_path / "audits" / "probe.yaml"


def test_run_audit_takes_an_audit_name(tmp_path, monkeypatch, fake_provider, flat_layout):
    from ai_taxman.core.discovery import write_marker

    make_project(tmp_path)
    write_marker(tmp_path, flat_layout)
    monkeypatch.chdir(tmp_path)

    assert run_audit("probe").n_ok == 2


def test_load_audit_by_name_outside_a_project_says_so(tmp_path, monkeypatch):
    import pytest

    from ai_taxman import TaxmanError

    monkeypatch.chdir(tmp_path)

    with pytest.raises(TaxmanError, match="not a taxman project"):
        load_audit("probe")


def test_every_error_load_audit_raises_by_name_is_importable(tmp_path, monkeypatch):
    """A caller has to be able to catch what the API documents it raises."""
    import pytest

    from ai_taxman import AuditNotFoundError, NotATaxmanProjectError
    from ai_taxman.core.discovery import write_marker

    monkeypatch.chdir(tmp_path)
    with pytest.raises(NotATaxmanProjectError):
        load_audit("probe")

    write_marker(tmp_path)
    with pytest.raises(AuditNotFoundError):
        load_audit("probe")

    assert "NotATaxmanProjectError" in ai_taxman.__all__
    assert "AuditNotFoundError" in ai_taxman.__all__
