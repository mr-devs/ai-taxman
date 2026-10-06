"""The `taxman` command line interface.

Four commands carry the whole workflow:

    taxman init                                      set up the project, once
    taxman audits new <provider> <audit>             scaffold an audit
    taxman collect <audit>                           run it
    taxman audits list | show | validate             inspect audits

There is no configuration command. Every audit setting - the `api_key_env:`
variable name included - is chosen by editing the file `audits new` wrote.

Errors raised deliberately by the library (`TaxmanError`) are printed as a
message and an exit code, never a traceback — the user is a researcher at a
prompt, not a debugger.
"""

from __future__ import annotations

import typer

from ai_taxman import __version__
from ai_taxman.cli.audits_cmd import audits_app
from ai_taxman.cli.collect_cmd import collect
from ai_taxman.cli.doctor_cmd import doctor
from ai_taxman.cli.init_cmd import init
from ai_taxman.cli.messages_cmd import messages_app
from ai_taxman.core.registry import available_providers

app = typer.Typer(
    name="taxman",
    help="Audit popular AI systems. Write messages in a .txt file, describe the "
    "audit in a YAML file, and collect the results.",
    no_args_is_help=True,
    add_completion=True,
)

app.command()(doctor)
app.command()(init)
app.command()(collect)
app.add_typer(audits_app, name="audits")
app.add_typer(messages_app, name="messages")


@app.command()
def providers() -> None:
    """List the providers this installation can audit."""
    for name in available_providers():
        typer.echo(name)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"taxman {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Audit popular AI systems."""


__all__ = ["app"]
