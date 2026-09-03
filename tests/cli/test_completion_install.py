"""Installing completion must leave a trace the user can find later.

Typer appends its lines to the shell rc with nothing to say where they came
from, so months later they read as junk somebody's tool left behind. We label
them, which also tells a reader exactly what to delete.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_taxman.cli.doctor_cmd import MARKER, run_install_completion


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def marker_index(lines: list[str]) -> int:
    return next(i for i, line in enumerate(lines) if line.startswith(MARKER))


def test_the_additions_are_labelled(home):
    rc = home / ".zshrc"
    rc.write_text("# my own config\nexport FOO=1\n", encoding="utf-8")

    assert run_install_completion("zsh") is True

    lines = rc.read_text(encoding="utf-8").splitlines()
    assert any("fpath+=~/.zfunc" in line for line in lines[marker_index(lines) :])


def test_the_label_says_how_to_uninstall(home):
    (home / ".zshrc").write_text("export FOO=1\n", encoding="utf-8")

    run_install_completion("zsh")

    lines = (home / ".zshrc").read_text(encoding="utf-8").splitlines()
    label = lines[marker_index(lines)]
    assert "~/.zfunc/_taxman" in label
    assert "uninstall" in label


def test_the_label_names_the_file_this_shell_actually_uses(home):
    """bash keeps its script somewhere else, so the hint must not say ~/.zfunc."""
    (home / ".bashrc").write_text("export FOO=1\n", encoding="utf-8")

    run_install_completion("bash")

    lines = (home / ".bashrc").read_text(encoding="utf-8").splitlines()
    label = lines[marker_index(lines)]
    assert "~/.bash_completions/taxman.sh" in label
    assert ".zfunc" not in label


def test_the_users_own_lines_are_left_above_it(home):
    rc = home / ".zshrc"
    rc.write_text("# my own config\nexport FOO=1\n", encoding="utf-8")

    run_install_completion("zsh")

    lines = rc.read_text(encoding="utf-8").splitlines()
    assert lines[: marker_index(lines)][:2] == ["# my own config", "export FOO=1"]


def test_installing_twice_does_not_repeat_the_label(home):
    (home / ".zshrc").write_text("export FOO=1\n", encoding="utf-8")

    run_install_completion("zsh")
    run_install_completion("zsh")

    assert (home / ".zshrc").read_text(encoding="utf-8").count(MARKER) == 1


def test_it_still_writes_the_completion_script(home):
    (home / ".zshrc").write_text("export FOO=1\n", encoding="utf-8")

    run_install_completion("zsh")

    assert "#compdef taxman" in (home / ".zfunc" / "_taxman").read_text(encoding="utf-8")


def test_a_shell_that_needs_no_rc_edit_is_fine(home):
    """fish gets a completions file and no rc change - nothing to label."""
    assert run_install_completion("fish") is True
    assert (home / ".config" / "fish" / "completions" / "taxman.fish").is_file()


# -- the startup file is the user's, and must survive being labelled -------


def test_the_users_own_formatting_above_is_preserved(home):
    """Typer rewrites the file stripped; labelling it must not compound that."""
    rc = home / ".zshrc"
    original = "\n\n#!/usr/bin/env zsh\n\n# leading blank lines are deliberate\nexport FOO=1\n"
    rc.write_text(original, encoding="utf-8")

    run_install_completion("zsh")

    assert rc.read_text(encoding="utf-8").startswith(original.rstrip("\n"))


def test_no_temporary_file_is_left_beside_it(home):
    (home / ".zshrc").write_text("export FOO=1\n", encoding="utf-8")

    run_install_completion("zsh")

    assert [
        p.name for p in home.iterdir() if p.name.startswith(".zshrc") and p.name != ".zshrc"
    ] == []


def test_the_startup_file_is_never_empty_partway_through(home, monkeypatch):
    """The rewrite must be atomic: a crash mid-write must not truncate the file."""
    rc = home / ".zshrc"
    rc.write_text("export FOO=1\n", encoding="utf-8")

    import os

    def boom(*args, **kwargs):
        raise OSError("interrupted")

    monkeypatch.setattr(os, "replace", boom)
    run_install_completion("zsh")

    assert "export FOO=1" in rc.read_text(encoding="utf-8")
