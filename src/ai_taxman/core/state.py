"""Small remembered answers, so taxman never asks the same thing twice.

Nothing here is important enough to be worth an error if it goes missing or
gets corrupted - a lost state file just means one more prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from ai_taxman.core.discovery import taxman_home

STATE_FILENAME = "state.yaml"

HEADER = "# Answers taxman remembers, so it does not ask again. Safe to delete.\n"


@dataclass(frozen=True, slots=True)
class State:
    """What the user has told us not to bring up again."""

    skip_path_check: bool = False
    skip_completion_check: bool = False

    def with_skips(self, *, path: bool | None = None, completion: bool | None = None) -> State:
        return replace(
            self,
            skip_path_check=self.skip_path_check if path is None else path,
            skip_completion_check=(
                self.skip_completion_check if completion is None else completion
            ),
        )


def state_path() -> Path:
    return taxman_home() / STATE_FILENAME


def load_state() -> State:
    """Read the remembered answers. Never raises."""
    path = state_path()
    if not path.is_file():
        return State()

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return State()

    if not isinstance(data, dict):
        return State()

    return State(
        skip_path_check=bool(data.get("skip_path_check", False)),
        skip_completion_check=bool(data.get("skip_completion_check", False)),
    )


def save_state(state: State) -> Path:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        HEADER
        + yaml.safe_dump(
            {
                "skip_path_check": state.skip_path_check,
                "skip_completion_check": state.skip_completion_check,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path
