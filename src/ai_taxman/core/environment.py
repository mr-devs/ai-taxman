"""Is this machine set up to run taxman comfortably?

Two things can be wrong after `uv tool install ai-taxman`, and neither is
taxman's doing:

* the directory holding the executable is not on `PATH`, so `taxman` only works
  by full path, and
* shell completion was never installed.

Neither can be fixed *before* taxman runs - by the time this module executes,
the shell has already found the binary somehow. What we can do is notice, say
so plainly, and offer to run the two commands that fix it.

Only a `uv tool` installation is worth warning about. A `uv run` during
development, or an ephemeral `uvx`, is deliberately not on `PATH` and nagging
about it would be noise.
"""

from __future__ import annotations

import os
import shutil
import sys
from enum import Enum
from pathlib import Path

PROGRAM = "taxman"

#: Marks a `uv tool install` layout: .../uv/tools/<name>/bin/<program>
UV_TOOLS_MARKER = ("uv", "tools")

#: Where each shell keeps the completion script Typer installs.
COMPLETION_FILES = {
    "zsh": (".zfunc/_taxman",),
    "bash": (".bash_completions/taxman.sh", ".bashrc"),
    "fish": (".config/fish/completions/taxman.fish",),
}

#: Text Typer's generated script always contains.
COMPLETION_MARKER = "_TAXMAN_COMPLETE"


class CheckStatus(Enum):
    """A check that could not be answered either way."""

    UNKNOWN = "unknown"


def executable_dir() -> Path:
    """The directory holding the running `taxman` executable."""
    return Path(sys.argv[0]).resolve().parent


def on_path() -> bool:
    """Whether typing `taxman` in a fresh shell would find this executable."""
    found = shutil.which(PROGRAM)
    if not found:
        return False
    try:
        return Path(found).resolve().parent == executable_dir()
    except OSError:
        return False


def installed_as_uv_tool() -> bool:
    """Whether this is a `uv tool install`, as opposed to `uv run` or `uvx`.

    Only a tool install is *meant* to be on `PATH`, so it is the only case where
    a missing `PATH` entry is worth mentioning.
    """
    parts = executable_dir().parts
    return any(parts[index : index + 2] == UV_TOOLS_MARKER for index in range(len(parts) - 1))


def shell_name() -> str | None:
    """The user's shell, or None if it cannot be determined."""
    detected = _detect_shell()
    if detected:
        return detected

    shell = os.environ.get("SHELL")
    return Path(shell).name if shell else None


def completion_installed(shell: str | None) -> bool | CheckStatus:
    """Whether tab completion is already installed for `shell`.

    Returns `CheckStatus.UNKNOWN` for a shell we have no answer for, so the
    caller can stay quiet rather than guess wrong.
    """
    if not shell or shell not in COMPLETION_FILES:
        return CheckStatus.UNKNOWN

    for relative in COMPLETION_FILES[shell]:
        path = Path.home() / relative
        try:
            if path.is_file() and COMPLETION_MARKER in path.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def uv_available() -> bool:
    """Whether `uv` is on PATH, so we can offer to run `uv tool update-shell`."""
    return shutil.which("uv") is not None


def _detect_shell() -> str | None:
    """Ask shellingham, which reads the parent process rather than `$SHELL`."""
    try:
        import shellingham

        name, _ = shellingham.detect_shell()
        return str(name)
    except Exception:  # noqa: BLE001 - detection is best effort, never fatal
        return None
