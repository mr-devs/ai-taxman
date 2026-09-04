import json


def make_project(tmp_path, body=None, messages="one\ntwo\nthree\n"):
    (tmp_path / "messages").mkdir(exist_ok=True)
    (tmp_path / "messages" / "probe.txt").write_text(messages, encoding="utf-8")
    (tmp_path / "audits").mkdir(exist_ok=True)
    (tmp_path / "audits" / "probe.yaml").write_text(
        body
        or ("audit: probe\nprovider: fake\nmessages: messages/probe.txt\nmodel:\n  name: fake-1\n"),
        encoding="utf-8",
    )


def test_runs_an_audit_by_name(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe")

    assert result.exit_code == 0
    assert len(fake_provider.sent) == 3


def test_reports_where_the_output_went(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe")

    assert "responses.jsonl" in result.output


def test_reports_the_counts(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe")

    assert "3" in result.output


def test_writes_the_jsonl_and_manifest(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    invoke("collect", "probe")

    runs = list((tmp_path / "data" / "probe").iterdir())
    assert len(runs) == 1
    assert (runs[0] / "responses.jsonl").is_file()
    assert (runs[0] / "manifest.json").is_file()


def test_repeats_flag_overrides_the_audit(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    invoke("collect", "probe", "--repeats", "2")

    assert len(fake_provider.sent) == 6


def test_concurrency_flag_overrides_the_audit(invoke, tmp_path, fake_provider):
    make_project(tmp_path)
    fake_provider.delay = 0.01

    invoke("collect", "probe", "--concurrency", "1")

    assert fake_provider.peak_concurrency == 1


def test_accepts_a_path_to_an_audit_file(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "audits/probe.yaml")

    assert result.exit_code == 0


def test_unknown_audit_lists_the_available_ones(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "porbe")

    assert result.exit_code != 0
    assert "probe" in result.output


def test_a_missing_message_file_fails_before_sending_anything(invoke, tmp_path, fake_provider):
    make_project(tmp_path)
    (tmp_path / "messages" / "probe.txt").unlink()

    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert fake_provider.sent == []


def test_an_invalid_model_block_is_reported_against_the_file(invoke, tmp_path, fake_provider):
    make_project(
        tmp_path,
        body=("audit: probe\nprovider: fake\nmessages: messages/probe.txt\nmodel:\n  boom: true\n"),
    )

    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert "boom" in result.output


def test_errors_are_reported_but_the_run_still_finishes(invoke, tmp_path, fake_provider):
    make_project(tmp_path)
    fake_provider.failures = {"m0001": [RuntimeError("nope")]}

    result = invoke("collect", "probe")

    assert result.exit_code == 0
    assert "error" in result.output.lower()


def test_a_run_where_everything_fails_exits_nonzero(invoke, tmp_path, fake_provider):
    make_project(tmp_path)
    fake_provider.failures = {
        "m0000": [RuntimeError("a")],
        "m0001": [RuntimeError("b")],
        "m0002": [RuntimeError("c")],
    }

    result = invoke("collect", "probe")

    assert result.exit_code != 0


def test_manifest_records_the_run(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    invoke("collect", "probe")

    run_dir = next((tmp_path / "data" / "probe").iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["n_messages"] == 3
    assert manifest["n_ok"] == 3


def test_a_provider_that_cannot_start_is_reported_without_a_traceback(
    invoke, tmp_path, fake_provider
):
    make_project(tmp_path)
    fake_provider.startup_error = RuntimeError("Missing credentials.")

    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert "Missing credentials" in result.output
    assert "Traceback" not in result.output


def test_the_banner_names_the_model_via_the_provider(invoke, tmp_path, fake_provider):
    """The CLI must not read the provider's `model:` keys either."""
    make_project(tmp_path)
    fake_provider.describe_model = lambda model: "described-by-provider"

    result = invoke("collect", "probe")

    assert "described-by-provider" in result.output


def test_a_missing_api_key_is_reported_cleanly(invoke, tmp_path, fake_provider, monkeypatch):
    fake_provider.requires_api_key = True
    monkeypatch.delenv("TAXMAN_FAKE_API_KEY", raising=False)
    make_project(
        tmp_path,
        body=(
            "audit: probe\nprovider: fake\nmessages: messages/probe.txt\n"
            "api_key_env: TAXMAN_FAKE_API_KEY\nmodel:\n  name: fake-1\n"
        ),
    )

    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert "TAXMAN_FAKE_API_KEY" in result.output
    assert "Traceback" not in result.output


# --- logging -------------------------------------------------------------


def test_a_log_file_captures_the_run(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe", "--log-file", "run.log")

    assert result.exit_code == 0
    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "run starting" in log
    assert "m0000" in log


def test_the_log_file_can_go_anywhere(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    invoke("collect", "probe", "--log-file", "logs/nested/run.log")

    assert (tmp_path / "logs" / "nested" / "run.log").is_file()


def test_the_summary_still_prints_when_the_log_goes_to_a_file(invoke, tmp_path, fake_provider):
    """The log is for the run; the summary is for the person who ran it."""
    make_project(tmp_path)

    result = invoke("collect", "probe", "--log-file", "run.log")

    assert "3 ok" in result.output
    assert "run starting" not in result.output


def test_the_log_level_can_be_turned_down(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    invoke("collect", "probe", "--log-file", "run.log", "--log-level", "warning")

    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "run starting" not in log


def test_an_unknown_log_level_is_refused_by_name(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe", "--log-level", "loud")

    assert result.exit_code != 0
    assert "loud" in result.output
    assert len(fake_provider.sent) == 0


# --- run ids -------------------------------------------------------------


def test_a_named_run_goes_in_a_directory_of_that_name(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe", "--run-id", "pilot")

    assert result.exit_code == 0
    assert (tmp_path / "data" / "probe" / "pilot" / "responses.jsonl").is_file()


def test_a_run_id_that_would_escape_the_data_directory_is_refused(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe", "--run-id", "../../escaped")

    assert result.exit_code != 0
    assert "escaped" in result.output
    assert fake_provider.sent == []
    assert not (tmp_path.parent.parent / "escaped").exists()


def test_a_bad_run_id_is_refused_before_anything_is_sent(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe", "--run-id", "two words")

    assert result.exit_code != 0
    assert fake_provider.sent == []
    assert not (tmp_path / "data").exists()
