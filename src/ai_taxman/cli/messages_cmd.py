"""`taxman messages`: the id taxman gives each message.

An id is a UUID5 of the message's text (see `core.messages.message_id`), so it
can be computed from text but never turned back into it.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Annotated

import typer

from ai_taxman.cli.util import handles_taxman_errors

messages_app = typer.Typer(
    help="Look up the ids taxman gives messages.",
    no_args_is_help=True,
)


@messages_app.command("ids")
@handles_taxman_errors
def ids_command(
    file: Annotated[Path, typer.Argument(help="A message file, one message per line.")],
) -> None:
    """Print each message in a file with its id, as CSV."""
    from ai_taxman.core.messages import read_messages

    pairs = [(message.id, message.text) for message in read_messages(file)]
    typer.echo(_csv([("message_id", "message"), *pairs]), nl=False)


def _csv(rows: list[tuple[str, str]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue()
