import pytest
from typer.testing import CliRunner

from ai_taxman.cli import app
from ai_taxman.core.discovery import MARKER_FILENAME, write_marker


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def invoke(runner, tmp_path, monkeypatch):
    """Run the CLI inside an isolated taxman project with an isolated TAXMAN_HOME.

    `tmp_path` is marked as a project root, because that is where almost every
    command is run from. `leave_project` undoes it for the few tests that check
    what happens outside one.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAXMAN_HOME", str(tmp_path / "taxman-home"))
    write_marker(tmp_path)

    def run(*args):
        return runner.invoke(app, list(args))

    return run


@pytest.fixture
def leave_project(tmp_path):
    """Remove the project marker, leaving the working directory project-less."""
    (tmp_path / MARKER_FILENAME).unlink()
    return tmp_path
