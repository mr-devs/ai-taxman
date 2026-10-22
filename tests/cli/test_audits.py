import pytest


def make_audit(directory, name, provider="fake"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.yaml").write_text(
        f"audit: {name}\n"
        f"provider: {provider}\n"
        f"messages: messages/{name}.txt\n"
        f"output:\n  dir: data/{{audit}}/{{run_id}}\n  log_dir: logs/{{audit}}\n"
        "execution:\n  repeats: 3\n"
        "model:\n  name: fake-1\n",
        encoding="utf-8",
    )


def test_lists_local_audits(invoke, tmp_path):
    make_audit(tmp_path / "audits", "probe")

    result = invoke("audits", "list")

    assert result.exit_code == 0
    assert "probe" in result.output


def test_listing_outside_a_project_says_so(invoke, leave_project):
    result = invoke("audits", "list")

    assert result.exit_code != 0
    assert "not a taxman project" in result.output.lower()
    assert "taxman init" in result.output


def test_collecting_outside_a_project_says_so(invoke, leave_project):
    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert "not a taxman project" in result.output.lower()


def test_says_so_when_there_are_no_audits(invoke):
    result = invoke("audits", "list")

    assert result.exit_code == 0
    assert "taxman audits new" in result.output


def test_shows_an_audits_resolved_settings(invoke, tmp_path):
    make_audit(tmp_path / "audits", "probe")

    result = invoke("audits", "show", "probe")

    assert result.exit_code == 0
    assert "probe" in result.output
    assert "repeats" in result.output


def test_show_reports_an_unknown_audit(invoke, tmp_path):
    result = invoke("audits", "show", "nope")

    assert result.exit_code != 0


def test_validate_accepts_a_good_audit(invoke, tmp_path, fake_provider):
    make_audit(tmp_path / "audits", "probe")
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\ntwo\n", encoding="utf-8")

    result = invoke("audits", "validate", "probe")

    assert result.exit_code == 0
    assert "2 message(s) x 3 repeat(s) = 6" in result.output
    assert "Collecting it starts a new run." in result.output


# --- validate says what collect would do with the audit's runs ------------------


def collected(invoke, tmp_path, fake_provider, *, failing=()):
    """A project whose `probe` audit has been collected once, with `failing` failed."""
    from ai_taxman.core.messages import message_id

    make_audit(tmp_path / "audits", "probe")
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\ntwo\n", encoding="utf-8")
    fake_provider.failures = {message_id(text): [RuntimeError("no")] * 3 for text in failing}
    assert invoke("collect", "probe").exit_code == 0
    (run,) = (tmp_path / "data" / "probe").iterdir()
    return run


def tree(root):
    return sorted((path.relative_to(root), path.stat().st_mtime_ns) for path in root.rglob("*"))


def test_validate_says_which_run_collect_would_resume(invoke, tmp_path, fake_provider):
    run = collected(invoke, tmp_path, fake_provider, failing=["two"])

    result = invoke("audits", "validate", "probe")

    assert result.exit_code == 0
    assert f"Collecting it resumes run {run.name}: 3 of 6 responses collected, 3 to send." in (
        result.output
    )


def test_validate_says_a_complete_run_sends_nothing(invoke, tmp_path, fake_provider):
    run = collected(invoke, tmp_path, fake_provider)

    result = invoke("audits", "validate", "probe")

    assert result.exit_code == 0
    assert f"Run {run.name} is complete, so collecting it sends nothing." in result.output


def test_validate_refuses_an_audit_changed_since_its_run_began(invoke, tmp_path, fake_provider):
    """`collect` would refuse to resume it, so "valid" would be a promise it breaks."""
    collected(invoke, tmp_path, fake_provider, failing=["two"])
    audit_file = tmp_path / "audits" / "probe.yaml"
    audit_file.write_text(
        audit_file.read_text(encoding="utf-8").replace("repeats: 3", "repeats: 2"),
        encoding="utf-8",
    )

    result = invoke("audits", "validate", "probe")

    assert result.exit_code != 0
    assert "has changed since its run" in result.output
    assert "is valid" not in result.output


def test_validate_refuses_a_run_being_collected(invoke, tmp_path, fake_provider):
    from ai_taxman.core.runs import hold_run

    run = collected(invoke, tmp_path, fake_provider, failing=["two"])

    with hold_run(run):
        result = invoke("audits", "validate", "probe")

    assert result.exit_code != 0
    assert "being collected right now" in result.output


def test_validate_checks_a_run_it_cannot_write(invoke, tmp_path, fake_provider):
    """A collaborator's read-only copy of the data: looking for a collector of
    the run must not need to write to it."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root writes to read-only files")
    run = collected(invoke, tmp_path, fake_provider, failing=["two"])
    (run / "collect.lock").chmod(0o444)
    run.chmod(0o555)
    try:
        result = invoke("audits", "validate", "probe")
    finally:
        run.chmod(0o755)

    assert result.exit_code == 0, result.output
    assert f"Collecting it resumes run {run.name}" in result.output


def test_validate_changes_nothing_on_disk(invoke, tmp_path, fake_provider):
    collected(invoke, tmp_path, fake_provider, failing=["two"])
    before = tree(tmp_path / "data")

    invoke("audits", "validate", "probe")

    assert tree(tmp_path / "data") == before


def write_keyed_audit(tmp_path):
    (tmp_path / "audits").mkdir()
    (tmp_path / "audits" / "probe.yaml").write_text(
        "audit: probe\nprovider: fake\nmessages: messages/probe.txt\n"
        "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
        "api_key_env: TAXMAN_FAKE_API_KEY\nmodel:\n  name: fake-1\n",
        encoding="utf-8",
    )
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\n", encoding="utf-8")


def test_validate_rejects_an_unset_key_variable(invoke, tmp_path, fake_provider, monkeypatch):
    """`collect` would stop on this at once, so `validate` must not call it valid."""
    fake_provider.requires_api_key = True
    monkeypatch.delenv("TAXMAN_FAKE_API_KEY", raising=False)
    write_keyed_audit(tmp_path)

    result = invoke("audits", "validate", "probe")

    assert result.exit_code != 0
    assert "TAXMAN_FAKE_API_KEY is not set" in result.output
    assert "is valid" not in result.output


def test_validate_never_prints_the_key(invoke, tmp_path, fake_provider, monkeypatch):
    fake_provider.requires_api_key = True
    monkeypatch.setenv("TAXMAN_FAKE_API_KEY", "sk-secret-value")
    write_keyed_audit(tmp_path)

    result = invoke("audits", "validate", "probe")

    assert result.exit_code == 0
    assert "sk-secret-value" not in result.output


def test_validate_names_the_file_from_where_the_user_stands(invoke, tmp_path, fake_provider):
    make_audit(tmp_path / "audits", "probe")
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\n", encoding="utf-8")

    result = invoke("audits", "validate", "probe")

    assert result.output.startswith("audits/probe.yaml is valid.")


def test_show_names_the_file_from_where_the_user_stands(invoke, tmp_path):
    make_audit(tmp_path / "audits", "probe")

    result = invoke("audits", "show", "probe")

    assert result.output.startswith("# audits/probe.yaml\n")


def test_lists_providers(invoke):
    result = invoke("providers")

    assert result.exit_code == 0
    assert "openai" in result.output


def test_reports_the_version(invoke):
    from ai_taxman import __version__

    result = invoke("--version")

    assert result.exit_code == 0
    assert __version__ in result.output


def checkable_audit(tmp_path, extra):
    (tmp_path / "audits").mkdir()
    (tmp_path / "audits" / "probe.yaml").write_text(
        "audit: probe\nprovider: fake\nmessages: messages/probe.txt\n"
        "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
        "model:\n  name: fake-1\n" + extra,
        encoding="utf-8",
    )
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\n", encoding="utf-8")


def test_validate_refuses_batch_mode_collect_cannot_run(invoke, tmp_path, fake_provider):
    """A "valid" here must mean `collect` will start."""
    checkable_audit(tmp_path, "execution:\n  batch: true\n")

    result = invoke("audits", "validate", "probe")

    assert result.exit_code != 0
    assert "batch" in result.output
    assert "is valid" not in result.output
