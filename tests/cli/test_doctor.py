"""`taxman doctor` - check PATH and completion, and offer to fix them."""

import pytest

from ai_taxman.core.environment import CheckStatus
from ai_taxman.core.state import load_state, save_state


@pytest.fixture
def env(monkeypatch):
    """Control what doctor sees, without touching the real machine."""
    import ai_taxman.cli.doctor_cmd as module

    state = {
        "on_path": False,
        "uv_tool": True,
        "shell": "zsh",
        "completion": False,
        "uv": True,
    }

    monkeypatch.setattr(module, "on_path", lambda: state["on_path"])
    monkeypatch.setattr(module, "installed_as_uv_tool", lambda: state["uv_tool"])
    monkeypatch.setattr(module, "shell_name", lambda: state["shell"])
    monkeypatch.setattr(module, "completion_installed", lambda shell: state["completion"])
    monkeypatch.setattr(module, "uv_available", lambda: state["uv"])
    return state


@pytest.fixture
def actions(monkeypatch):
    """Record the fixes doctor would run, without running them."""
    import ai_taxman.cli.doctor_cmd as module

    done = []
    monkeypatch.setattr(module, "run_update_shell", lambda: (done.append("path"), True)[1])
    monkeypatch.setattr(
        module, "run_install_completion", lambda shell: (done.append("completion"), True)[1]
    )
    return done


@pytest.fixture
def confirm(monkeypatch):
    """Answer doctor's yes/no prompts from a script."""

    def install(*replies):
        import ai_taxman.cli.doctor_cmd as module

        answers = list(replies)
        monkeypatch.setattr(module, "ask_yes_no", lambda prompt: answers.pop(0))

    return install


def test_reports_a_healthy_machine(invoke, env, actions, confirm):
    env.update(on_path=True, completion=True)

    result = invoke("doctor")

    assert result.exit_code == 0
    assert "ready" in result.output.lower() or "ok" in result.output.lower()
    assert actions == []


def test_reports_that_taxman_is_not_on_the_path(invoke, env, actions, confirm):
    confirm(False, False)

    result = invoke("doctor")

    assert "PATH" in result.output


def test_offers_to_fix_the_path_and_does_so_when_accepted(invoke, env, actions, confirm):
    confirm(True, False)

    invoke("doctor")

    assert "path" in actions


def test_does_not_touch_anything_when_declined(invoke, env, actions, confirm):
    confirm(False, False)

    invoke("doctor")

    assert actions == []


def test_offers_to_install_completion(invoke, env, actions, confirm):
    env.update(on_path=True)
    confirm(True)

    invoke("doctor")

    assert "completion" in actions


def test_says_to_restart_the_shell_after_fixing(invoke, env, actions, confirm):
    confirm(True, True)

    result = invoke("doctor")

    assert "restart" in result.output.lower()


def test_stays_quiet_about_the_path_when_not_a_uv_tool_install(invoke, env, actions, confirm):
    """A `uv run` or `uvx` binary is deliberately not on PATH."""
    env.update(uv_tool=False)
    confirm(False)

    result = invoke("doctor")

    assert "not on your PATH" not in result.output


def test_says_how_to_fix_the_path_when_uv_is_missing(invoke, env, actions, confirm):
    env.update(uv=False)
    confirm(False)

    result = invoke("doctor")

    assert "export PATH" in result.output
    assert actions == []


def test_skips_completion_for_an_unrecognised_shell(invoke, env, actions, confirm):
    env.update(on_path=True, completion=CheckStatus.UNKNOWN)

    result = invoke("doctor")

    assert result.exit_code == 0
    assert actions == []


def test_declining_permanently_is_remembered(invoke, env, actions, confirm):
    confirm("never", "never")

    invoke("doctor")

    state = load_state()
    assert state.skip_path_check is True
    assert state.skip_completion_check is True


def test_a_remembered_decline_stops_the_prompt(invoke, env, actions, confirm):
    save_state(load_state().with_skips(path=True, completion=True))
    confirm()  # any prompt would raise IndexError

    result = invoke("doctor")

    assert result.exit_code == 0
    assert actions == []


def test_doctor_always_reports_even_when_prompts_are_skipped(invoke, env, actions, confirm):
    save_state(load_state().with_skips(path=True, completion=True))
    confirm()

    result = invoke("doctor")

    assert "PATH" in result.output
