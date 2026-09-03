"""Remembered answers, so taxman never nags twice."""

import pytest

from ai_taxman.core.state import State, load_state, save_state, state_path


@pytest.fixture(autouse=True)
def taxman_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXMAN_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_state_starts_empty():
    state = load_state()

    assert state.skip_path_check is False
    assert state.skip_completion_check is False


def test_round_trips(taxman_home):
    save_state(State(skip_path_check=True))

    assert load_state().skip_path_check is True
    assert state_path() == taxman_home / "state.yaml"


def test_with_skips_returns_a_new_state():
    state = State()

    updated = state.with_skips(path=True, completion=False)

    assert updated.skip_path_check is True
    assert state.skip_path_check is False


def test_a_corrupt_state_file_is_ignored(taxman_home):
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text("not: [valid", encoding="utf-8")

    assert load_state().skip_path_check is False
