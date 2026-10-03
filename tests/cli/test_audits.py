def make_audit(directory, name, provider="fake"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.yaml").write_text(
        f"audit: {name}\n"
        f"provider: {provider}\n"
        f"messages: messages/{name}.txt\n"
        "execution:\n  repeats: 3\n"
        "model:\n  name: fake-1\n",
        encoding="utf-8",
    )


def test_lists_local_audits(invoke, tmp_path):
    make_audit(tmp_path / "audits", "probe")

    result = invoke("audits", "list")

    assert result.exit_code == 0
    assert "probe" in result.output


def test_lists_audits_from_a_subdirectory_of_the_project(invoke, tmp_path, monkeypatch):
    """The root is found by walking up, so any directory inside a project works."""
    make_audit(tmp_path / "audits", "probe")
    deep = tmp_path / "messages" / "nested"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    result = invoke("audits", "list")

    assert result.exit_code == 0
    assert "probe" in result.output


def test_listing_outside_a_project_says_so(invoke, leave_project):
    result = invoke("audits", "list")

    assert result.exit_code != 0
    assert "not a taxman project" in result.output.lower()
    assert "taxman init" in result.output


def test_a_stray_audits_directory_does_not_make_a_project(invoke, leave_project, tmp_path):
    """An unrelated `audits/` folder must never be adopted as a project root."""
    make_audit(tmp_path / "audits", "unrelated")

    result = invoke("audits", "list")

    assert result.exit_code != 0
    assert "unrelated" not in result.output


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


def test_validate_reports_a_missing_message_file(invoke, tmp_path, fake_provider):
    make_audit(tmp_path / "audits", "probe")

    result = invoke("audits", "validate", "probe")

    assert result.exit_code != 0
    assert "probe.txt" in result.output


def write_keyed_audit(tmp_path):
    (tmp_path / "audits").mkdir()
    (tmp_path / "audits" / "probe.yaml").write_text(
        "audit: probe\nprovider: fake\nmessages: messages/probe.txt\n"
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


def test_validate_rejects_a_bad_model_block(invoke, tmp_path, fake_provider):
    (tmp_path / "audits").mkdir()
    (tmp_path / "audits" / "probe.yaml").write_text(
        "audit: probe\nprovider: fake\nmessages: m.txt\nmodel:\n  boom: true\n",
        encoding="utf-8",
    )

    result = invoke("audits", "validate", "probe")

    assert result.exit_code != 0
    assert "boom" in result.output


def test_lists_providers(invoke):
    result = invoke("providers")

    assert result.exit_code == 0
    assert "openai" in result.output


def test_reports_the_version(invoke):
    from ai_taxman import __version__

    result = invoke("--version")

    assert result.exit_code == 0
    assert __version__ in result.output
