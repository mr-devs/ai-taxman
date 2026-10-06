"""`taxman messages`: the id taxman gives each message.

An id is a UUID5 of the message's text (see `core.messages.message_id`), so it
can be computed from text but never turned back into it. `text` therefore finds
an id's message by computing the id of every message in a file.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Annotated

import typer

from ai_taxman.cli.util import display_path, fail, handles_taxman_errors

messages_app = typer.Typer(
    help="Look up the ids taxman gives messages.",
    no_args_is_help=True,
)


@messages_app.command("ids")
@handles_taxman_errors
def ids_command(
    file: Annotated[
        Path | None, typer.Argument(help="A message file, one message per line.")
    ] = None,
    texts: Annotated[
        list[str] | None,
        typer.Option("--text", "-t", help="A message to give the id of. Repeat for more."),
    ] = None,
) -> None:
    """Print each message in a file, or each `-t` message, with its id, as CSV."""
    from ai_taxman.core.messages import message_id, read_messages

    if file is not None and texts:
        fail("Give a message file or `-t` messages, not both.")
    if file is None and not texts:
        fail("Give a message file, or messages with `-t`.")

    if file is not None:
        pairs = [(message.id, message.text) for message in read_messages(file)]
    else:
        # Trimmed as a line of a file is, so each gets the id that line would.
        stripped = [text.strip() for text in texts or []]
        if not all(stripped):
            fail("A `-t` message is empty.")
        pairs = [(message_id(text), text) for text in stripped]

    typer.echo(_csv([("message_id", "message"), *pairs]), nl=False)


@messages_app.command("text")
@handles_taxman_errors
def text_command(
    id: Annotated[str, typer.Argument(help="The message id to look up.")],
    file: Annotated[Path, typer.Argument(help="The message file to look in.")],
) -> None:
    """Print the text of the message in a file that has this id."""
    from ai_taxman.core.messages import read_messages

    wanted = id.strip()
    for message in read_messages(file):
        if message.id == wanted:
            typer.echo(message.text)
            return
    fail(f"No message in {display_path(file)} has the id {wanted}.")


def _csv(rows: list[tuple[str, str]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue()
