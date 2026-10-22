import pytest

from ai_taxman.core.messages import message_id

#: The ids of the three messages `make_project` writes by default.
ONE, TWO, THREE = (message_id(text) for text in ("one", "two", "three"))

#: The run a test standing in for the runner says it collected.
RUN = "20261008T000000Z-000000"


def hidden_options(command):
    """The option names a command keeps out of its help: plumbing, not for users."""
    from typer.main import get_command

    from ai_taxman.cli import app

    params = get_command(app).commands[command].params  # type: ignore[attr-defined]
    return [name for param in params if getattr(param, "hidden", False) for name in param.opts]


def make_project(tmp_path, body=None, messages="one\ntwo\nthree\n"):
    (tmp_path / "messages").mkdir(exist_ok=True)
    (tmp_path / "messages" / "probe.txt").write_text(messages, encoding="utf-8")
    (tmp_path / "audits").mkdir(exist_ok=True)
    (tmp_path / "audits" / "probe.yaml").write_text(
        body
        or (
            "audit: probe\nprovider: fake\nmessages: messages/probe.txt\n"
            "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
            "model:\n  name: fake-1\n"
        ),
        encoding="utf-8",
    )


def test_collects_an_audit_by_name_and_summarises_it(invoke, tmp_path, fake_provider):
    """The counts, and where the responses went, from where the user stands."""
    make_project(tmp_path)

    result = invoke("collect", "probe", "--quiet")

    assert result.exit_code == 0
    assert len(fake_provider.sent) == 3
    summary = result.output.strip().splitlines()[-1]
    assert summary.startswith("3 ok  ->  data/probe/")
    assert summary.endswith("responses.jsonl")


@pytest.mark.parametrize("flag", ["--repeats", "-r", "--concurrency", "-c"])
def test_settings_come_only_from_the_audit_file(invoke, tmp_path, fake_provider, flag):
    """A run is what its audit file says, so a resumed run can be checked against it."""
    make_project(tmp_path)

    result = invoke("collect", "probe", flag, "2")

    assert result.exit_code != 0
    assert fake_provider.sent == []


def test_new_run_starts_another_run_of_the_audit(invoke, tmp_path, fake_provider):
    make_project(tmp_path)
    invoke("collect", "probe")

    result = invoke("collect", "probe", "--new-run")

    assert result.exit_code == 0, result.output
    assert len(list((tmp_path / "data" / "probe").iterdir())) == 2
    assert len(fake_provider.sent) == 6


def test_new_run_says_when_it_leaves_a_run_unfinished(invoke, tmp_path, fake_provider):
    make_project(tmp_path)
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    invoke("collect", "probe")
    (first,) = (tmp_path / "data" / "probe").iterdir()

    result = invoke("collect", "probe", "--new-run")

    assert "unfinished" in result.output
    assert first.name in result.output


def test_a_changed_audit_is_not_resumed(invoke, tmp_path, fake_provider):
    make_project(tmp_path)
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    invoke("collect", "probe")
    audit_file = tmp_path / "audits" / "probe.yaml"
    audit_file.write_text(
        audit_file.read_text(encoding="utf-8") + "execution:\n  repeats: 2\n", encoding="utf-8"
    )
    fake_provider.sent.clear()

    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert "execution.repeats: 1 -> 2" in result.output
    assert fake_provider.sent == []


def test_a_complete_one_run_folder_is_told_to_move_the_run(invoke, tmp_path, fake_provider):
    make_project(
        tmp_path,
        "audit: probe\nprovider: fake\nmessages: messages/probe.txt\n"
        "output:\n  dir: data/{audit}\n  log_dir: logs/{audit}\nmodel:\n  name: fake-1\n",
    )
    invoke("collect", "probe")

    result = invoke("collect", "probe")

    assert "already complete" in result.output
    assert "--new-run" not in result.output
    assert "move data/probe elsewhere and collect again" in result.output


def test_collecting_a_complete_audit_says_so_and_sends_nothing(invoke, tmp_path, fake_provider):
    make_project(tmp_path)
    invoke("collect", "probe")
    fake_provider.sent.clear()

    result = invoke("collect", "probe")

    assert result.exit_code == 0
    assert "already complete" in result.output
    assert "--new-run" in result.output
    assert fake_provider.sent == []


def test_a_resumed_run_says_so_before_it_starts(invoke, tmp_path, fake_provider):
    make_project(tmp_path)
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    invoke("collect", "probe")
    (run,) = (tmp_path / "data" / "probe").iterdir()

    result = invoke("collect", "probe", "--quiet")

    assert f"Resuming run {run.name}" in result.output
    assert "2 of 3 responses already collected" in result.output
    assert "sending 1, after it failed before" in result.output


def test_a_resumed_run_is_judged_on_the_whole_run(invoke, tmp_path, fake_provider):
    """One message that always fails must not fail every later collect of a run
    that is otherwise answered."""
    make_project(tmp_path)
    fake_provider.failures = {TWO: [RuntimeError("refused")] * 2}
    assert invoke("collect", "probe").exit_code == 0

    result = invoke("collect", "probe")

    assert result.exit_code == 0, result.output
    assert "0 ok, 1 error" in result.output
    assert "now has 2 of 3 responses" in result.output


def stopped(tmp_path, monkeypatch, *, by_signal=True, **counts):
    """Stand in for a run stopped early, by SIGTERM or by an error, with these counts."""
    import signal

    from ai_taxman.core import runner
    from ai_taxman.core.runner import RunResult

    def stopped_run(config, checked, **kwargs):
        directory = tmp_path / "data" / "probe" / RUN
        return RunResult(
            run_id=RUN,
            output_path=directory / "responses.jsonl",
            manifest_path=directory / "manifest.json",
            expected=3,
            stopped_early=True,
            stop_signal=signal.SIGTERM if by_signal else None,
            **counts,
        )

    monkeypatch.setattr(runner, "run_checked", stopped_run)


def test_a_run_stopped_by_kill_says_so(invoke, tmp_path, fake_provider, monkeypatch):
    make_project(tmp_path)
    stopped(tmp_path, monkeypatch, n_ok=1, n_error=0, run_n_ok=1)

    result = invoke("collect", "probe")

    assert result.exit_code == 0, result.output
    assert "Run stopped on SIGTERM; collect again to finish it." in result.output
    assert "on_error" not in result.output


def test_a_run_killed_before_any_response_does_not_say_they_failed(
    invoke, tmp_path, fake_provider, monkeypatch
):
    """Nothing failed: nothing came back. It still exits non-zero, having collected nothing."""
    make_project(tmp_path)
    stopped(tmp_path, monkeypatch, n_ok=0, n_error=0, run_n_ok=0)

    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert "the run stopped before any response came back" in result.output
    assert "failed" not in result.output


def test_a_run_stopped_by_its_first_error_does_not_say_every_message_failed(
    invoke, tmp_path, fake_provider, monkeypatch
):
    """`on_error: stop` after one failure: the others were never sent."""
    make_project(tmp_path)
    stopped(tmp_path, monkeypatch, by_signal=False, n_ok=0, n_error=1, run_n_ok=0, run_n_error=1)

    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert "every message failed" not in result.output
    assert "1 of 3 failed, and the run stopped before sending the rest" in result.output


def test_a_run_whose_every_message_failed_says_so(invoke, tmp_path, fake_provider):
    make_project(tmp_path)
    fake_provider.failures = {key: [RuntimeError("nope")] for key in (ONE, TWO, THREE)}

    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert "every message failed" in result.output


@pytest.fixture
def reads(monkeypatch):
    """How many times the run's responses are read to plan it."""
    from ai_taxman.core import runs

    counted = []
    read = runs._responses_so_far
    monkeypatch.setattr(runs, "_responses_so_far", lambda path: counted.append(path) or read(path))
    return counted


def unfinished(invoke, tmp_path, fake_provider):
    make_project(tmp_path)
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    invoke("collect", "probe")
    (run,) = (tmp_path / "data" / "probe").iterdir()
    return run


def test_collect_reads_a_resumed_run_once(invoke, tmp_path, fake_provider, reads):
    """Planned once, by the runner: a large compressed run takes a while to read."""
    unfinished(invoke, tmp_path, fake_provider)
    reads.clear()

    invoke("collect", "probe")

    assert len(reads) == 1


def test_a_provider_that_cannot_start_is_reported_without_a_traceback(
    invoke, tmp_path, fake_provider
):
    """And leaves no log: a log file is a record of a run, and there was none."""
    make_project(tmp_path)
    fake_provider.startup_error = RuntimeError("Missing credentials.")

    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert "Missing credentials" in result.output
    assert "Traceback" not in result.output
    assert not list(tmp_path.glob("logs/**/*.log"))


def test_the_banner_names_the_model_via_the_provider(invoke, tmp_path, fake_provider):
    """The CLI must not read the provider's `model:` keys either."""
    make_project(tmp_path)
    fake_provider.describe_model = lambda model: "described-by-provider"

    result = invoke("collect", "probe")

    assert "described-by-provider" in result.output


def test_a_missing_api_key_is_reported_before_the_banner(
    invoke, tmp_path, fake_provider, monkeypatch
):
    """Announcing a run that cannot start reads as if it started."""
    fake_provider.requires_api_key = True
    monkeypatch.delenv("TAXMAN_FAKE_API_KEY", raising=False)
    make_project(
        tmp_path,
        body=(
            "audit: probe\nprovider: fake\nmessages: messages/probe.txt\n"
            "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
            "api_key_env: TAXMAN_FAKE_API_KEY\nmodel:\n  name: fake-1\n"
        ),
    )

    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert "TAXMAN_FAKE_API_KEY" in result.output
    assert "Traceback" not in result.output
    assert "Collecting" not in result.output


# --- logging -------------------------------------------------------------


def run_dir(tmp_path):
    return next((tmp_path / "data" / "probe").iterdir())


def test_every_run_keeps_its_log_in_the_logs_folder(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    invoke("collect", "probe")

    log = tmp_path / "logs" / "probe" / f"{run_dir(tmp_path).name}.log"
    assert "run starting" in log.read_text(encoding="utf-8")


def test_a_foreground_run_still_logs_to_the_terminal(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe")

    assert "run starting" in result.output


def test_the_summary_still_prints_when_the_log_goes_to_a_file(invoke, tmp_path, fake_provider):
    """The log is for the run; the summary is for the person who ran it."""
    make_project(tmp_path)

    result = invoke("collect", "probe", "--log-file", "run.log")

    assert result.exit_code == 0
    assert "3 ok" in result.output
    assert "run starting" not in result.output
    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "run starting" in log
    assert ONE in log


def test_the_log_level_can_be_turned_down(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    invoke("collect", "probe", "--log-file", "run.log", "--log-level", "warning")

    # Nothing reached the level, so nothing was written - not even an empty file.
    log = tmp_path / "run.log"
    assert not log.exists() or "run starting" not in log.read_text(encoding="utf-8")


def test_an_unknown_log_level_is_refused_by_name(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe", "--log-level", "loud")

    assert result.exit_code != 0
    assert "loud" in result.output
    assert len(fake_provider.sent) == 0


# --- run ids -------------------------------------------------------------


def test_a_run_id_cannot_be_chosen(invoke, tmp_path, fake_provider):
    """The audit names the run: collecting it again finishes it, --new-run starts another."""
    make_project(tmp_path)

    result = invoke("collect", "probe", "--run-id", "pilot")

    assert result.exit_code != 0
    assert fake_provider.sent == []


def test_collect_has_no_hidden_options():
    """None a "did you mean" could point a user at: everything comes from the audit."""
    assert hidden_options("collect") == []


@pytest.mark.parametrize("flag", ["--background", "-b"])
def test_a_collect_is_not_detached_by_taxman(invoke, tmp_path, fake_provider, flag):
    """The shell already does that: `&`, `nohup`, tmux."""
    make_project(tmp_path)

    result = invoke("collect", "probe", flag)

    assert result.exit_code != 0
    assert "No such option" in result.output
    assert fake_provider.sent == []
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    "extra, cause",
    [
        ("execution:\n  batch: true\n", "batch"),
        ("output:\n  dir: data/{audit}/{run_id}\n  log_dir: out/{date}\n", "{date}"),
    ],
)
def test_a_run_that_cannot_start_fails_cleanly(invoke, tmp_path, fake_provider, extra, cause):
    make_project(
        tmp_path,
        body="audit: probe\nprovider: fake\nmessages: messages/probe.txt\n"
        "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
        "model:\n  name: fake-1\n" + extra,
    )

    result = invoke("collect", "probe")

    assert result.exit_code != 0
    assert cause in result.output
    assert "Traceback" not in result.output
    assert "Collecting" not in result.output
    assert fake_provider.sent == []
