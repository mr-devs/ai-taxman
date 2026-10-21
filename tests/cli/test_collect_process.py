"""`taxman collect` as a real process: what `kill` does to it, and its pid file.

A test in this process cannot send a real collect a real signal. These start one
with `python -m ai_taxman`, against a provider registered the way a third-party
one is, through the `ai_taxman.providers` entry-point group.
"""

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from ai_taxman.core.discovery import Layout, write_marker
from ai_taxman.core.runs import PID_FILENAME

FLAT = Layout(data="data", audits="audits", messages="messages", prompts="prompts", logs="logs")

AUDIT = (
    "audit: probe\nprovider: echo\nmessages: messages/probe.txt\n"
    "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
    "execution:\n  max_concurrency: 1\n"
    "model:\n  name: echo-1\n"
)

ECHO_PROVIDER = '''
"""A provider that answers from memory, found through its entry point."""

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


@pytest.fixture
def project(tmp_path):
    """An `echo` audit of `count` messages, sent one at a time."""

    def make(count=3):
        write_marker(tmp_path, FLAT)
        (tmp_path / "messages").mkdir()
        (tmp_path / "messages" / "probe.txt").write_text(
            "".join(f"message {n}\n" for n in range(count)), encoding="utf-8"
        )
        (tmp_path / "audits").mkdir()
        (tmp_path / "audits" / "probe.yaml").write_text(AUDIT, encoding="utf-8")
        return tmp_path

    return make


def start_collect(project, plugin_path, **extra_env):
    """`taxman collect probe`, running. Its output goes to files: a pipe nobody
    reads fills up with the log and stops the run."""
    env = {
        **os.environ,
        "PYTHONPATH": str(plugin_path),
        "TAXMAN_HOME": str(project / "home"),
        **extra_env,
    }
    with (project / "out.txt").open("w") as out, (project / "err.txt").open("w") as err:
        return subprocess.Popen(
            [sys.executable, "-m", "ai_taxman", "collect", "probe"],
            cwd=project,
            env=env,
            stdout=out,
            stderr=err,
        )


def wait_for(predicate, timeout=30.0):
    """Poll until true. A file the run has not written yet is a 'not yet'."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except (OSError, ValueError, KeyError, StopIteration):
            pass
        time.sleep(0.05)
    return False


def run_directory(project):
    return next((project / "data" / "probe").iterdir())


def test_a_real_collect_finds_its_provider_and_collects(project, plugin_path):
    root = project()

    process = start_collect(root, plugin_path)

    assert process.wait(timeout=60) == 0, (root / "err.txt").read_text(encoding="utf-8")
    manifest = json.loads((run_directory(root) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["n_ok"] == 3


def test_the_pid_file_names_the_process_collecting_the_run(project, plugin_path):
    """So `kill $(cat .../collect.pid)` stops it, with no parsing."""
    root = project(count=200)
    process = start_collect(root, plugin_path, ECHO_DELAY="0.05")
    try:
        assert wait_for(lambda: (run_directory(root) / PID_FILENAME).is_file())
        pid_file = run_directory(root) / PID_FILENAME

        assert pid_file.read_text(encoding="utf-8") == f"{process.pid}\n"
    finally:
        process.kill()
        process.wait(timeout=30)


def test_kill_stops_a_collect_gracefully(project, plugin_path):
    """SIGTERM finishes what is in flight, finalises the manifest, and says why
    the run stopped: the responses already paid for are kept and described."""
    root = project(count=500)
    process = start_collect(root, plugin_path, ECHO_DELAY="0.05")
    try:
        assert wait_for(
            lambda: (
                (run_directory(root) / "responses.jsonl").read_text(encoding="utf-8").count("\n")
                > 1
            )
        ), (root / "err.txt").read_text(encoding="utf-8")

        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
    finally:
        process.kill()

    directory = run_directory(root)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "stopped_early"
    assert manifest["finished_at"] is not None
    assert 0 < manifest["n_ok"] < 500
    assert not (directory / PID_FILENAME).exists()
    assert "Run stopped on SIGTERM" in (root / "out.txt").read_text(encoding="utf-8")
