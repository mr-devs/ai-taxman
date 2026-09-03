"""`taxman doctor` - check the machine is set up, and offer to fix what isn't.

Two things commonly need doing after `uv tool install ai-taxman`, and neither is
something taxman can do for itself before it runs:

* put the executable's directory on `PATH` (`uv tool update-shell`), and
* install tab completion (`taxman --install-completion`).

Nothing here edits a shell startup file without an explicit yes, and a "never"
is remembered so the question is not asked again.
"""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

import typer

from ai_taxman.cli.util import handles_taxman_errors
from ai_taxman.core.environment import (
    CheckStatus,
    completion_installed,
    executable_dir,
    installed_as_uv_tool,
    on_path,
    shell_name,
    uv_available,
)
from ai_taxman.core.state import load_state, save_state

NEVER = "never"

#: Opens the comment written above whatever the installer appends to a startup
#: file. Kept stable and matched by prefix - the rest of the line names the
#: script for the shell in hand, which differs between zsh and bash.
MARKER = "# taxman additions"


@handles_taxman_errors
def doctor() -> None:
    """Check that `taxman` is on your PATH and tab completion is installed."""
    _run_checks()


def _run_checks() -> None:
    state = load_state()
    shell = shell_name()
    completion = completion_installed(shell)

    path_ok = on_path()
    completion_ok = completion is True
    # A `uv run` or `uvx` binary is deliberately not on PATH; do not nag about it.
    path_relevant = installed_as_uv_tool()
    completion_relevant = completion is not CheckStatus.UNKNOWN

    healthy = (path_ok or not path_relevant) and (completion_ok or not completion_relevant)
    if healthy:
        typer.secho("taxman is ready: on your PATH, with tab completion.", fg=typer.colors.GREEN)
        return

    fixed = False

    if path_relevant and not path_ok:
        typer.secho(
            f"! `taxman` is not on your PATH, so it only works by full path.\n"
            f"  It lives in {executable_dir()}",
            fg=typer.colors.YELLOW,
        )
        if not uv_available():
            typer.echo(f'  Fix it with:  export PATH="{executable_dir()}:$PATH"')
        elif state.skip_path_check:
            typer.echo("  (You asked not to be prompted about this.)")
        else:
            answer = ask_yes_no("  Run `uv tool update-shell` to fix it?")
            if answer is True:
                fixed = run_update_shell() or fixed
            elif answer == NEVER:
                state = state.with_skips(path=True)
                save_state(state)

    if completion_relevant and not completion_ok:
        typer.secho(f"! Tab completion is not installed for {shell}.", fg=typer.colors.YELLOW)
        if state.skip_completion_check:
            typer.echo("  (You asked not to be prompted about this.)")
        else:
            answer = ask_yes_no("  Install it now?")
            if answer is True:
                fixed = run_install_completion(shell) or fixed
            elif answer == NEVER:
                state = state.with_skips(completion=True)
                save_state(state)

    if fixed:
        typer.echo("")
        typer.secho("Restart your shell to apply the changes.", fg=typer.colors.GREEN)


def ask_yes_no(prompt: str) -> bool | str:
    """Yes, no, or never-ask-again. Falls back to plain input without a terminal."""
    if not sys.stdin.isatty():
        return False

    # Below the check on purpose: questionary pulls in prompt_toolkit, the most
    # expensive import in the CLI, and a run with no terminal never asks.
    import questionary

    answer = questionary.select(
        prompt,
        choices=[
            questionary.Choice("Yes", value=True),
            questionary.Choice("Not now", value=False),
            questionary.Choice("No, and don't ask again", value=NEVER),
        ],
        instruction="(use the arrow keys, then Enter)",
    ).ask()
    return False if answer is None else answer


def run_update_shell() -> bool:
    """Shell out to uv, which owns PATH setup - taxman does not edit dotfiles itself."""
    typer.echo("  Running `uv tool update-shell`...")
    try:
        result = subprocess.run(
            ["uv", "tool", "update-shell"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        typer.secho(f"  Could not run uv: {exc}", fg=typer.colors.RED)
        return False

    if result.returncode != 0:
        typer.secho(f"  uv reported: {result.stderr.strip()}", fg=typer.colors.RED)
        return False

    typer.secho("  PATH updated.", fg=typer.colors.GREEN)
    return True


def run_install_completion(shell: str | None) -> bool:
    """Install completion using Typer's own installer, and label what it appended.

    Typer writes its lines to the shell rc unannounced. We note what the file
    looked like beforehand and mark whatever appeared, so a reader months later
    can see where the lines came from and what to remove.
    """
    rc = _startup_file(shell)
    before = _read(rc)

    try:
        from typer._completion_shared import install

        _, path = install(shell=shell, prog_name="taxman", complete_var="_TAXMAN_COMPLETE")
    except Exception as exc:  # noqa: BLE001 - a failed convenience must not crash
        typer.secho(f"  Could not install completion: {exc}", fg=typer.colors.RED)
        typer.echo("  Try:  taxman --install-completion")
        return False

    _label_additions(rc, before, path)
    typer.secho(f"  Completion installed at {path}.", fg=typer.colors.GREEN)
    return True


def _startup_file(shell: str | None) -> Path | None:
    """The file this shell's installer appends to, if it appends to one at all."""
    if shell == "zsh":
        return Path.home() / ".zshrc"
    if shell == "bash":
        return Path.home() / ".bashrc"
    return None  # fish and powershell write a completions file and nothing else


def _read(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _label_additions(rc: Path | None, before: str, script: Path | str) -> None:
    """Put a labelled comment above whatever the installer appended to `rc`.

    Compared on stripped text because Typer rewrites the file stripped - and the
    user's own text is restored from `before` rather than from that stripped
    copy, so leading blank lines and a shebang survive being labelled.

    Anything unexpected - the file shrank, the old content is no longer a prefix,
    the marker is already there - is left well alone. A startup file is the
    user's, and no comment is worth damaging one over.
    """
    if rc is None:
        return

    after = _read(rc)
    head, tail = before.strip(), after.strip()
    if not tail.startswith(head) or len(tail) <= len(head):
        return

    added = tail[len(head) :].strip()
    if not added or MARKER in before:
        return

    keep = before.rstrip("\n")
    body = f"{keep}\n\n" if keep.strip() else ""
    _write_atomically(rc, f"{body}{_label(script)}\n{added}\n")


def _write_atomically(path: Path, text: str) -> None:
    """Replace `path` in one step, so an interrupted write cannot truncate it.

    Writing in place would leave an empty startup file if anything failed between
    truncating and writing - a shell that no longer starts, in exchange for a
    comment. The temporary file is cleaned up on any failure.
    """
    temporary = path.with_name(f"{path.name}.taxman-tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        with suppress(OSError):  # keep the original mode where there is one
            temporary.chmod(path.stat().st_mode & 0o7777)
        os.replace(temporary, path)
    except OSError:  # a label is never worth failing an install over
        with suppress(OSError):
            temporary.unlink()


def _label(script: Path | str) -> str:
    """The comment written above the additions, naming what an uninstall removes."""
    return f"{MARKER} - delete these lines and {_shorten(script)} to uninstall"


def _shorten(script: Path | str) -> str:
    """`~/.zfunc/_taxman` reads better in a dotfile than an absolute path."""
    try:
        return f"~/{Path(script).relative_to(Path.home())}"
    except ValueError:
        return str(script)
