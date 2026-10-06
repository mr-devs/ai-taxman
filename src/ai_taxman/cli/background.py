"""`taxman collect --background`: start a run and hand the prompt back.

The case this exists for is a shell script that starts two providers at once and
lets them run:

    taxman collect openai-probe -b
    taxman collect anthropic-probe -b

So a backgrounded run has to outlive the terminal that started it, say where its
log is, and leave behind something a script can stop it with.

How it works: the parent does not fork the work, it re-runs taxman as a detached
child. The parent validates the audit, picks the run id, creates the run
directory, spawns `python -m ai_taxman collect ... --run-id ...` in a new
session, prints where everything went, and exits.

Two consequences of that design are deliberate:

- **The parent picks the run id.** Otherwise only the child would know where its
  own output landed, and there would be nothing to print.
- **The parent validates first.** Everything that can be checked without doing
  the run - the `model:` block, the API key variable, the message file - is
  checked before the fork. Handing back a pid for a run that was already doomed
  is worse than failing at the prompt.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ai_taxman.core.runs import PID_FILENAME

__all__ = ["PID_FILENAME", "build_child_command", "clear_pid_file", "spawn", "write_pid_file"]

#: Windows' spelling of "do not die with the terminal". POSIX uses its own
#: session instead; this constant is unused there.
_WINDOWS_DETACH = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
)


def build_child_command(
    audit: str,
    *,
    run_id: str,
    log_file: Path,
    pid_file: Path,
    log_level: str | None = None,
    quiet: bool = False,
) -> list[str]:
    """The argv for the detached child.

    `-m ai_taxman` rather than the `taxman` script: it works the same whether
    taxman was installed with `uv tool install`, run through `uv run`, or is a
    checkout on the path, and it cannot pick up a different `taxman` from PATH
    than the one the user just invoked.

    `--background` is not forwarded, for the obvious reason.
    """
    argv = [
        sys.executable,
        "-m",
        "ai_taxman",
        "collect",
        audit,
        "--run-id",
        run_id,
        "--log-file",
        str(log_file),
        "--pid-file",
        str(pid_file),
    ]
    if log_level is not None:
        argv += ["--log-level", log_level]
    if quiet:
        argv.append("--quiet")
    return argv


def spawn(argv: list[str], *, log_file: Path, cwd: Path) -> int:
    """Start `argv` detached from this terminal and return its pid.

    The child gets its own session, so a Ctrl-C in the terminal that started it,
    or closing that terminal, does not take the run down with it. Its output
    goes to the log file - anything the logger does not catch, a traceback from
    a crash most of all, has to land somewhere the user was told about.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_file, "a", encoding="utf-8")  # noqa: SIM115 - owned by the child
    try:
        if os.name == "nt":  # pragma: no cover - the test machines are POSIX
            process = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                creationflags=_WINDOWS_DETACH,
            )
        else:
            process = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    finally:
        # The child holds its own duplicate of the descriptor from here on.
        handle.close()
    return int(process.pid)


def write_pid_file(path: Path, pid: int) -> None:
    """Record the running process, as one integer and nothing else."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def clear_pid_file(path: Path, pid: int) -> None:
    """Remove the pid file, but only if it is still ours.

    A run that is killed with `kill -9` leaves its pid file behind; the manifest
    still saying `running` is the honest record of that. Never delete a file
    another run has since claimed.
    """
    try:
        if path.read_text(encoding="utf-8").strip() == str(pid):
            path.unlink()
    except (OSError, ValueError):
        return
