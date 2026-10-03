"""`taxman init` - set up a taxman project.

The first command in every project, run once. It asks where each folder taxman
uses should go, offering a default for each, writes the answers into the
`taxman.yaml` marker, and creates the folders. Audits come afterwards, one per
provider, with `taxman audits new`.

The folders are a fact about the project, not about a run: `audits new` writes
them into each audit it creates, so an audit still names every path it uses.

Nothing is asked without a terminal. A script passes `--yes` for the defaults,
rather than having taxman guess what an empty stdin meant.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from ai_taxman.cli.util import display_path, fail, handles_taxman_errors
from ai_taxman.core.discovery import (
    FOLDER_PURPOSES,
    MARKER_FILENAME,
    Layout,
    check_folder,
    find_project_root,
    write_marker,
)
from ai_taxman.core.errors import ConfigError


def can_ask() -> bool:
    """Whether a person is at the prompt to answer."""
    return sys.stdin.isatty()


@handles_taxman_errors
def init(
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Accept the default for every folder without asking."),
    ] = False,
) -> None:
    """Set up a taxman project in this directory.

    Asks where audits, messages, system prompts, collected data, and logs should
    go, relative to this directory. Press Enter to keep a default.
    """
    here = Path.cwd()
    existing = find_project_root(here)
    if existing is not None:
        where = "This directory is" if existing == here.resolve() else "This directory is inside"
        fail(
            f"{where} the taxman project at {existing}. A project is set up once; "
            f"its folders are in {existing / MARKER_FILENAME}."
        )
        return

    if yes:
        layout = Layout()
    elif can_ask():
        layout = _ask(here)
    else:
        fail(
            "taxman init asks where each folder should go, and there is no terminal to "
            "ask at. Run `taxman init --yes` to accept the defaults."
        )
        return

    marker = write_marker(here, layout)
    for folder in layout.as_dict().values():
        (here / folder).mkdir(parents=True, exist_ok=True)

    # The root is named in full: "." would not tell the user which project.
    typer.secho(f"Started a taxman project at {here}", fg=typer.colors.GREEN)
    typer.echo(f"  {display_path(marker)} marks the root and records its folders:")
    width = max(len(label) for label in FOLDER_PURPOSES.values())
    for purpose, label in FOLDER_PURPOSES.items():
        typer.echo(f"    {label:<{width}}  {getattr(layout, purpose)}/")
    typer.echo("")
    typer.echo("Next, create an audit for each provider you want to audit:")
    typer.echo("  taxman audits new <provider> <audit>")


def _ask(root: Path) -> Layout:
    """Ask for each folder until the answers make a usable layout."""
    typer.echo(f"Setting up a taxman project in {root}")
    typer.echo("Each folder is relative to this directory. Press Enter to keep the default.")
    typer.echo("")

    answers = Layout().as_dict()
    width = max(len(label) for label in FOLDER_PURPOSES.values())
    while True:
        for purpose, label in FOLDER_PURPOSES.items():
            answers[purpose] = _ask_one(f"  {label:<{width}}", purpose, answers[purpose])
        try:
            return Layout(**answers)
        except ConfigError as exc:
            # A clash between two folders only shows once both are answered.
            typer.secho(f"  {exc}", fg=typer.colors.RED)
            typer.echo("  Starting again, with your answers as the defaults.")
            typer.echo("")


def _ask_one(prompt: str, purpose: str, default: str) -> str:
    while True:
        value = typer.prompt(prompt, default=default)
        try:
            return check_folder(value, purpose)
        except ConfigError as exc:
            typer.secho(f"  {exc}", fg=typer.colors.RED)
