"""Detecting whether the machine is set up to run taxman comfortably.

Two independent things can be wrong after `uv tool install`:
the executable's directory may not be on PATH, and shell completion may not be
installed. Neither is taxman's fault, and neither can be fixed before taxman
runs - but once it does run, it can say so and offer to fix them.
"""

from pathlib import Path

import pytest

from ai_taxman.core.environment import (
    CheckStatus,
    completion_installed,
    executable_dir,
    installed_as_uv_tool,
    on_path,
    shell_name,
)


def test_reports_the_directory_holding_the_running_executable(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", [str(tmp_path / "bin" / "taxman")])

    assert executable_dir() == tmp_path / "bin"


def test_on_path_is_true_when_the_shell_finds_our_executable(monkeypatch, tmp_path):
    binary = tmp_path / "bin" / "taxman"
    binary.parent.mkdir(parents=True)
    binary.touch(mode=0o755)
    monkeypatch.setenv("PATH", str(binary.parent))
    monkeypatch.setattr("sys.argv", [str(binary)])

    assert on_path() is True


def test_on_path_is_false_when_the_directory_is_not_listed(monkeypatch, tmp_path):
    binary = tmp_path / "bin" / "taxman"
    binary.parent.mkdir(parents=True)
    binary.touch(mode=0o755)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr("sys.argv", [str(binary)])

    assert on_path() is False


def test_installed_as_uv_tool_recognises_the_uv_tools_directory(monkeypatch, tmp_path):
    binary = tmp_path / "share" / "uv" / "tools" / "ai-taxman" / "bin" / "taxman"
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.setattr("sys.argv", [str(binary)])

    assert installed_as_uv_tool() is True


@pytest.mark.parametrize(
    "location",
    [
        "project/.venv/bin/taxman",  # uv run, during development
        "cache/uv/archive-v0/xyz/bin/taxman",  # uvx, ephemeral
        "usr/local/bin/taxman",
    ],
)
def test_other_locations_are_not_a_uv_tool_install(monkeypatch, tmp_path, location):
    binary = tmp_path / location
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.setattr("sys.argv", [str(binary)])

    assert installed_as_uv_tool() is False


def test_completion_is_detected_from_the_shells_own_files(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    zfunc = tmp_path / ".zfunc"
    zfunc.mkdir()
    (zfunc / "_taxman").write_text("_TAXMAN_COMPLETE=complete_zsh", encoding="utf-8")

    assert completion_installed("zsh") is True


def test_completion_is_absent_when_nothing_was_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert completion_installed("zsh") is False


def test_completion_check_tolerates_an_unknown_shell(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert completion_installed("some-exotic-shell") is CheckStatus.UNKNOWN


def test_shell_name_falls_back_to_the_shell_variable(monkeypatch):
    monkeypatch.setattr("ai_taxman.core.environment._detect_shell", lambda: None)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    assert shell_name() == "zsh"


def test_shell_name_is_none_when_nothing_can_be_determined(monkeypatch):
    """Detection must never raise; an unknown shell is just unknown."""
    monkeypatch.setattr("ai_taxman.core.environment._detect_shell", lambda: None)
    monkeypatch.delenv("SHELL", raising=False)

    assert shell_name() is None
