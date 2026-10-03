"""`taxman collect --background`: start a run and get the prompt back.

The point is a shell script that starts two providers at once:

    taxman collect openai-probe -b
    taxman collect anthropic-probe -b

Each run needs to survive the terminal that started it, say where its log is,
and leave something the script can use to stop it.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_taxman.cli.background import PID_FILENAME, build_child_command
from ai_taxman.core.discovery import Layout, write_marker

FLAT = Layout(data="data", audits="audits", messages="messages", prompts="prompts", logs="logs")

AUDIT = "audit: probe\nprovider: fake\nmessages: messages/probe.txt\nmodel:\n  name: fake-1\n"


def make_project(tmp_path, body=AUDIT, messages="one\ntwo\nthree\n"):
    write_marker(tmp_path, FLAT)
    (tmp_path / "messages").mkdir(exist_ok=True)
    (tmp_path / "messages" / "probe.txt").write_text(messages, encoding="utf-8")
    (tmp_path / "audits").mkdir(exist_ok=True)
    (tmp_path / "audits" / "probe.yaml").write_text(body, encoding="utf-8")


# --- the command the child is given --------------------------------------


def test_the_child_runs_the_same_audit_without_backgrounding_again():
    """A child that re-backgrounded itself would fork forever."""
    argv = build_child_command("probe", run_id="r1", log_file=Path("a.log"), pid_file=Path("a.pid"))

    assert "--background" not in argv
    assert "-b" not in argv
    assert argv[:4] == [sys.executable, "-m", "ai_taxman", "collect"]
    assert "probe" in argv


def test_the_child_is_told_which_run_it_is():
    """The parent picks the run id so it can name the directory before the run."""
    argv = build_child_command("probe", run_id="r1", log_file=Path("a.log"), pid_file=Path("a.pid"))

    assert argv[argv.index("--run-id") + 1] == "r1"


def test_the_child_writes_its_log_where_the_parent_said():
    argv = build_child_command(
        "probe", run_id="r1", log_file=Path("/tmp/a.log"), pid_file=Path("a.pid")
    )

    assert argv[argv.index("--log-file") + 1] == "/tmp/a.log"


def test_the_overrides_the_user_typed_are_carried_across():
    argv = build_child_command(
        "probe",
        run_id="r1",
        log_file=Path("a.log"),
        pid_file=Path("a.pid"),
        repeats=5,
        concurrency=2,
        log_level="debug",
    )

    assert argv[argv.index("--repeats") + 1] == "5"
    assert argv[argv.index("--concurrency") + 1] == "2"
    assert argv[argv.index("--log-level") + 1] == "debug"


def test_overrides_that_were_not_given_are_not_invented():
    argv = build_child_command("probe", run_id="r1", log_file=Path("a.log"), pid_file=Path("a.pid"))

    assert "--repeats" not in argv
    assert "--concurrency" not in argv


# --- what the parent does before letting go -------------------------------


def test_the_parent_reports_everything_needed_to_follow_or_stop_the_run(
    invoke, tmp_path, fake_provider
):
    make_project(tmp_path)

    result = invoke("collect", "probe", "--background")

    assert result.exit_code == 0
    for expected in ("run id", "pid", "log", "output", "kill"):
        assert expected in result.output.lower()


def test_the_pid_is_the_only_thing_on_stdout(echo_project, plugin_path):
    """`PID=$(taxman collect probe -b)` has to be all a script needs."""
    result = run_taxman(echo_project, plugin_path, "--background")

    assert result.stdout.strip().isdigit()
    assert "run id" in result.stderr


def test_a_script_can_wait_for_the_pid_it_was_given(echo_project, plugin_path):
    """The use case: start runs, then wait on them."""
    result = run_taxman(echo_project, plugin_path, "--background", ECHO_DELAY="0.05")
    pid = int(result.stdout.strip())

    assert wait_for(lambda: not _alive(pid))

    directory = next((echo_project / "data" / "probe").iterdir())
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_the_parent_does_not_send_anything_itself(invoke, tmp_path, fake_provider):
    """The parent hands off and exits; the child does the run."""
    make_project(tmp_path)

    invoke("collect", "probe", "--background")

    assert fake_provider.sent == []


def test_a_broken_audit_is_refused_before_a_background_run_is_promised(
    invoke, tmp_path, fake_provider
):
    """Handing back a pid for a run that was doomed is worse than failing here."""
    make_project(tmp_path, body=AUDIT.replace("name: fake-1", "name: fake-1\n  boom: yes"))

    result = invoke("collect", "probe", "--background")

    assert result.exit_code != 0
    assert "pid" not in result.output.lower()


@pytest.mark.parametrize(
    "extra", ["execution:\n  batch: true\n", "output:\n  log_dir: out/{date}\n"]
)
def test_a_run_that_cannot_start_gets_no_pid_and_no_directory(
    invoke, tmp_path, fake_provider, extra
):
    make_project(tmp_path, body=AUDIT + extra)

    result = invoke("collect", "probe", "--background")

    assert result.exit_code != 0
    assert "pid" not in result.output.lower()
    assert not (tmp_path / "data").exists()


def test_a_missing_api_key_is_refused_before_the_run_is_promised(invoke, tmp_path, fake_provider):
    make_project(tmp_path, body=AUDIT + "api_key_env: DEFINITELY_NOT_SET_ANYWHERE\n")
    fake_provider.requires_api_key = True

    result = invoke("collect", "probe", "--background")

    assert result.exit_code != 0
    assert "DEFINITELY_NOT_SET_ANYWHERE" in result.output


# --- a real detached run --------------------------------------------------


ECHO_PROVIDER = '''
"""A provider that answers from memory, registered through the documented
`ai_taxman.providers` entry-point group so a real child process can find it."""

import asyncio
import os

from ai_taxman.providers.base import Provider


class EchoProvider(Provider):
    name = "echo"
    requires_api_key = False

    def known_models(self):
        return ["echo-1"]

    def validate_model_config(self, block):
        return {"name": block.get("name", "echo-1")}

    def describe_model(self, model):
        return model["name"]

    def render_template(self):
        return "model:\\n  name: echo-1\\n"

    async def send(self, request, *, timeout_s):
        # A test that needs to catch a run in progress sets this.
        delay = float(os.environ.get("ECHO_DELAY", "0"))
        if delay:
            await asyncio.sleep(delay)
        return {"echo": request.message.text}


PROVIDER = EchoProvider()
'''


@pytest.fixture
def plugin_path(tmp_path_factory):
    """A real installable provider on the import path, entry point and all."""
    root = tmp_path_factory.mktemp("plugin")
    (root / "echo_provider.py").write_text(ECHO_PROVIDER, encoding="utf-8")

    dist = root / "echo_provider-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: echo-provider\nVersion: 0.1\n", encoding="utf-8"
    )
    (dist / "entry_points.txt").write_text(
        "[ai_taxman.providers]\necho = echo_provider:PROVIDER\n", encoding="utf-8"
    )
    return root


def wait_for(predicate, timeout=30.0):
    """Poll until true. A file the child has not written yet is a 'not yet'."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.05)
    return False


def child_log(directory):
    log = directory.parent.parent.parent / "logs" / "probe" / f"{directory.name}.log"
    return log.read_text(encoding="utf-8") if log.is_file() else "(no log written)"


def run_taxman(project, plugin_path, *args, **extra_env):
    env = {
        **os.environ,
        "PYTHONPATH": str(plugin_path),
        "TAXMAN_HOME": str(project / "home"),
        **extra_env,
    }
    return subprocess.run(
        [sys.executable, "-m", "ai_taxman", "collect", "probe", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def echo_project(tmp_path, plugin_path):
    make_project(
        tmp_path,
        body=AUDIT.replace("provider: fake", "provider: echo").replace(
            "name: fake-1", "name: echo-1"
        ),
    )
    return tmp_path


def test_a_real_background_run_outlives_the_command_that_started_it(echo_project, plugin_path):
    """The whole feature, for real: parent returns, child finishes the work."""
    result = run_taxman(echo_project, plugin_path, "--background")

    assert result.returncode == 0, result.stderr

    runs = list((echo_project / "data" / "probe").iterdir())
    assert len(runs) == 1
    directory = runs[0]

    manifest_path = directory / "manifest.json"
    assert wait_for(
        lambda: json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "complete"
    ), (result.stdout, child_log(directory))

    responses = (directory / "responses.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(responses) == 3


def test_a_real_background_run_logs_to_the_logs_folder(echo_project, plugin_path):
    result = run_taxman(echo_project, plugin_path, "--background")

    directory = next((echo_project / "data" / "probe").iterdir())
    log = echo_project / "logs" / "probe" / f"{directory.name}.log"

    assert wait_for(lambda: log.is_file() and "run complete" in log.read_text(encoding="utf-8")), (
        result.stderr
    )
    assert str(log) in result.stderr


def test_a_real_background_run_cleans_up_its_pid_file_when_it_finishes(echo_project, plugin_path):
    run_taxman(echo_project, plugin_path, "--background")

    directory = next((echo_project / "data" / "probe").iterdir())
    manifest_path = directory / "manifest.json"

    assert wait_for(
        lambda: json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "complete"
    )
    assert wait_for(lambda: not (directory / PID_FILENAME).is_file())


def test_the_pid_file_holds_a_pid_a_shell_script_can_kill(echo_project, plugin_path):
    """`kill $(cat .../collect.pid)` has to work with no parsing."""
    slow = "\n".join(f"message {n}" for n in range(200)) + "\n"
    (echo_project / "messages" / "probe.txt").write_text(slow, encoding="utf-8")

    run_taxman(echo_project, plugin_path, "--background", "--concurrency", "1", ECHO_DELAY="0.05")

    directory = next((echo_project / "data" / "probe").iterdir())
    pid_file = directory / PID_FILENAME

    assert wait_for(lambda: pid_file.is_file())
    pid = pid_file.read_text(encoding="utf-8").strip()
    assert pid.isdigit()
    os.kill(int(pid), 0)  # raises if there is no such process


def test_a_backgrounded_run_stops_gracefully_when_it_is_killed(echo_project, plugin_path):
    """SIGTERM finishes what is in flight and finalises the manifest."""
    many = "\n".join(f"message {n}" for n in range(500)) + "\n"
    (echo_project / "messages" / "probe.txt").write_text(many, encoding="utf-8")

    run_taxman(echo_project, plugin_path, "--background", "--concurrency", "1", ECHO_DELAY="0.05")

    directory = next((echo_project / "data" / "probe").iterdir())
    pid_file = directory / PID_FILENAME
    manifest_path = directory / "manifest.json"

    assert wait_for(lambda: pid_file.is_file())
    assert wait_for(
        lambda: (
            (directory / "responses.jsonl").is_file()
            and (directory / "responses.jsonl").read_text(encoding="utf-8").count("\n") > 1
        )
    )

    os.kill(int(pid_file.read_text(encoding="utf-8").strip()), 15)

    assert wait_for(
        lambda: (
            json.loads(manifest_path.read_text(encoding="utf-8"))["status"]
            in {"stopped_early", "interrupted"}
        )
    ), manifest_path.read_text(encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["finished_at"] is not None
    assert manifest["n_ok"] < 500  # it stopped, rather than running to the end


def test_a_bad_run_id_is_refused_before_a_background_run_is_started(
    invoke, tmp_path, fake_provider
):
    """The parent builds the run directory from the id, so it checks it first."""
    make_project(tmp_path)

    result = invoke("collect", "probe", "--background", "--run-id", "../../escaped")

    assert result.exit_code != 0
    assert "pid" not in result.output.lower()
    assert not (tmp_path / "data").exists()


def test_a_background_run_uses_the_run_id_it_was_given(echo_project, plugin_path):
    """--run-id names the run in the foreground; -b must not quietly ignore it."""
    result = run_taxman(echo_project, plugin_path, "--background", "--run-id", "i-asked-for-this")

    assert "i-asked-for-this" in result.stderr
    directory = echo_project / "data" / "probe" / "i-asked-for-this"
    assert wait_for(
        lambda: (
            json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["status"]
            == "complete"
        )
    ), child_log(directory)
