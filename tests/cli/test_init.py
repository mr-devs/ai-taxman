"""`taxman init` - set up a project, before any audit.

It asks where each folder taxman uses should go, offering a default for each,
and records the answers in `taxman.yaml`. Audits for each provider are created
afterwards with `taxman audits new`.
"""

import pytest

from ai_taxman.core.discovery import MARKER_FILENAME, Layout, read_layout

DEFAULT_FOLDERS = [
    "taxman/data",
    "taxman/audits",
    "taxman/messages",
    "taxman/prompts",
    "taxman/logs",
]


@pytest.fixture
def terminal(monkeypatch):
    """Pretend a person is at the prompt, so `init` asks its questions."""
    monkeypatch.setattr("ai_taxman.cli.init_cmd.can_ask", lambda: True)


def answer(invoke, runner, *lines):
    from ai_taxman.cli import app

    return runner.invoke(app, ["init"], input="".join(f"{line}\n" for line in lines))


# --- accepting the defaults -----------------------------------------------


def test_yes_sets_up_a_project_with_the_default_folders(invoke, leave_project, tmp_path):
    result = invoke("init", "--yes")

    assert result.exit_code == 0
    assert read_layout(tmp_path) == Layout()


def test_the_folders_are_created(invoke, leave_project, tmp_path):
    invoke("init", "--yes")

    for folder in DEFAULT_FOLDERS:
        assert (tmp_path / folder).is_dir(), folder


def test_the_marker_holds_relative_folders(invoke, leave_project, tmp_path):
    invoke("init", "--yes")
    text = (tmp_path / MARKER_FILENAME).read_text(encoding="utf-8")

    assert "taxman/data" in text
    assert str(tmp_path) not in text


def test_it_says_where_the_project_is_and_what_comes_next(invoke, leave_project, tmp_path):
    result = invoke("init", "--yes")

    assert str(tmp_path) in result.output
    assert "taxman audits new" in result.output


# --- asking ---------------------------------------------------------------


def test_it_asks_about_every_folder(invoke, runner, leave_project, terminal):
    result = answer(invoke, runner, "", "", "", "", "")

    for label in ("Collected data", "Audit files", "Message files", "System prompts", "Run logs"):
        assert label in result.output


def test_it_asks_in_the_order_the_folders_are_used(invoke, runner, leave_project, terminal):
    output = answer(invoke, runner, "", "", "", "", "").output
    labels = ["Audit files", "Message files", "System prompts", "Collected data", "Run logs"]

    positions = [output.index(label) for label in labels]

    assert positions == sorted(positions)


def test_the_summary_lists_the_folders_in_the_same_order(invoke, leave_project):
    output = invoke("init", "--yes").output
    folders = [
        "taxman/audits/",
        "taxman/messages/",
        "taxman/prompts/",
        "taxman/data/",
        "taxman/logs/",
    ]

    positions = [output.index(folder) for folder in folders]

    assert positions == sorted(positions)


def test_enter_keeps_each_default(invoke, runner, leave_project, tmp_path, terminal):
    result = answer(invoke, runner, "", "", "", "", "")

    assert result.exit_code == 0
    assert read_layout(tmp_path) == Layout()


def test_an_answer_replaces_the_default(invoke, runner, leave_project, tmp_path, terminal):
    answer(invoke, runner, "", "", "", "results", "")

    assert read_layout(tmp_path).data == "results"
    assert (tmp_path / "results").is_dir()


def test_a_folder_outside_the_project_is_asked_again(
    invoke, runner, leave_project, tmp_path, terminal
):
    result = answer(invoke, runner, "", "", "", "../elsewhere", "results", "")

    assert "outside the project" in result.output
    assert read_layout(tmp_path).data == "results"


def test_folders_that_collide_are_asked_again(invoke, runner, leave_project, tmp_path, terminal):
    """A clash only shows once every answer is in, so the round starts over."""
    result = answer(invoke, runner, "", "", "", "shared", "shared", "", "", "", "", "own-logs")

    assert "Give each its own folder" in result.output
    layout = read_layout(tmp_path)
    assert layout.data == "shared"
    assert layout.logs == "own-logs"


def test_without_a_terminal_it_asks_for_yes(invoke, leave_project, tmp_path):
    result = invoke("init")

    assert result.exit_code != 0
    assert "--yes" in result.output
    assert not (tmp_path / MARKER_FILENAME).exists()


# --- one project, once ----------------------------------------------------


def test_refuses_to_set_up_a_project_twice(invoke, tmp_path):
    before = (tmp_path / MARKER_FILENAME).read_text(encoding="utf-8")

    result = invoke("init", "--yes")

    assert result.exit_code != 0
    assert str(tmp_path) in result.output
    assert (tmp_path / MARKER_FILENAME).read_text(encoding="utf-8") == before


def test_refuses_to_nest_a_project_inside_another(invoke, tmp_path, monkeypatch):
    inner = tmp_path / "sub" / "project"
    inner.mkdir(parents=True)
    monkeypatch.chdir(inner)

    result = invoke("init", "--yes")

    assert result.exit_code != 0
    assert "inside" in result.output
    assert not (inner / MARKER_FILENAME).exists()


def test_it_takes_no_provider_or_audit(invoke, leave_project, tmp_path):
    result = invoke("init", "openai", "probe")

    assert result.exit_code != 0
    assert not (tmp_path / MARKER_FILENAME).exists()
