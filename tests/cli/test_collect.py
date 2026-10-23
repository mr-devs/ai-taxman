import pytest

from ai_taxman.core.messages import message_id

#: The ids of the three messages `make_project` writes by default.
ONE, TWO, THREE = (message_id(text) for text in ("one", "two", "three"))

#: The run a test standing in for the runner says it collected.
RUN = "20261008T000000Z-000000"


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

    result = invoke("collect", "probe")

    assert result.exit_code == 0
    assert len(fake_provider.sent) == 3
    summary = result.output.strip().splitlines()[-1]
    assert summary.startswith("3 ok  ->  data/probe/")
    assert summary.endswith("responses.jsonl")


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

    result = invoke("collect", "probe")

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
    """Stand in for a run stopped early, by SIGTERM or by an error, with these
    counts and anything else its result says."""
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


def test_a_run_that_stopped_itself_says_why(invoke, tmp_path, fake_provider, monkeypatch):
    """Not a signal, and not `on_error: stop`: the provider's quota is used up."""
    make_project(tmp_path)
    reason = "fake asked to wait 3600s, over the 300s limit"
    stopped(
        tmp_path, monkeypatch, by_signal=False, stop_reason=reason, n_ok=1, n_error=0, run_n_ok=1
    )

    result = invoke("collect", "probe")

    assert reason in result.output
    assert "on_error" not in result.output


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


def test_every_run_keeps_its_log_in_the_logs_folder_and_says_where(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe")

    log = f"logs/probe/{run_dir(tmp_path).name}.log"
    assert "run starting" in (tmp_path / log).read_text(encoding="utf-8")
    assert log in result.output


def test_a_progress_bar_counts_the_responses_and_how_they_went(
    invoke, tmp_path, fake_provider, monkeypatch
):
    """What the terminal shows while a run goes. Forced on: tqdm turns itself off
    when stderr is not a terminal, as it is not under a test runner."""
    from ai_taxman.cli import collect_cmd

    start = collect_cmd._Progress.start
    monkeypatch.setattr(
        collect_cmd._Progress, "start", lambda self, total: start(self, total, disable=False)
    )
    make_project(tmp_path)
    fake_provider.failures = {TWO: [RuntimeError("nope")]}

    result = invoke("collect", "probe")

    assert "3/3" in result.output
    assert "ok=2" in result.output
    assert "error=1" in result.output


def test_the_log_stays_out_of_the_terminal(invoke, tmp_path, fake_provider):
    make_project(tmp_path)

    result = invoke("collect", "probe")

    assert "run starting" not in result.output


# --- what collect takes --------------------------------------------------


def test_collect_takes_only_its_own_options():
    """Every setting comes from the audit file, so a resumed run can be checked against it.

    So `--repeats`, `--run-id` and `--background` stay gone under any name, and
    nothing is hidden for a "did you mean" to point a user at.
    """
    from typer.main import get_command

    from ai_taxman.cli import app

    params = get_command(app).commands["collect"].params
    taken = {(p.param_type_name, *p.opts, *p.secondary_opts) for p in params}

    assert taken == {
        ("argument", "audit"),
        ("option", "--new-run"),
    }
    assert [p.name for p in params if getattr(p, "hidden", False)] == []


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
