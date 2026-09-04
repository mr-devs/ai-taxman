"""Paths in command output are written from where the user is standing.

A project can be found from any directory inside it, so output has to say
*which* file it means. Inside the working directory a relative path is the
shortest thing that does that; anywhere else only an absolute one will do.
"""

from ai_taxman.cli.util import display_path


def test_a_path_under_the_working_directory_is_shown_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert display_path(tmp_path / "audits" / "probe.yaml") == "audits/probe.yaml"


def test_a_path_outside_the_working_directory_stays_absolute(tmp_path, monkeypatch):
    deep = tmp_path / "messages"
    deep.mkdir()
    monkeypatch.chdir(deep)

    shown = display_path(tmp_path / "audits" / "probe.yaml")

    assert shown == str(tmp_path / "audits" / "probe.yaml")


def test_the_working_directory_itself_is_shown_as_a_dot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert display_path(tmp_path) == "."
