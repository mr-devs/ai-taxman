import pytest
from typer.testing import CliRunner

from ai_taxman.cli import app


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def invoke(runner, tmp_path, monkeypatch):
    """Run the CLI inside an isolated project directory with an isolated TAXMAN_HOME."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAXMAN_HOME", str(tmp_path / "taxman-home"))

    def run(*args):
        return runner.invoke(app, list(args))

    return run
