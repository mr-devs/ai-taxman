"""`taxman init` - scaffold an audit YAML for a provider.

Two names in, one file out. Nothing else is accepted: every setting lands at its
default with a comment explaining it, and the user edits the file. Parsing
`key=value` settings on the command line meant core had to route each one to its
owner and each provider had to validate arbitrary keys, all to save opening the
file that was written for exactly that purpose.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ai_taxman.cli.completion import complete_provider
from ai_taxman.cli.render import API_KEY_ENV_PLACEHOLDER, render_audit
from ai_taxman.cli.util import fail, handles_taxman_errors
from ai_taxman.core.discovery import AUDITS_DIR_NAME
from ai_taxman.core.registry import get_provider
from ai_taxman.providers.base import Provider


@handles_taxman_errors
def init(
    provider: Annotated[
        str,
        typer.Argument(help="Provider to audit, e.g. openai.", autocompletion=complete_provider),
    ],
    audit: Annotated[
        str,
        typer.Argument(help="Name for the audit, e.g. election-probe."),
    ],
) -> None:
    """Create an audit YAML file for a provider.

    Every setting is written at its default. Open the file and change what you
    need - it lists everything the provider accepts, with comments.
    """
    resolved = get_provider(provider)

    target = Path.cwd() / AUDITS_DIR_NAME / f"{audit}.yaml"
    if target.exists():
        fail(
            f"{target} already exists. Choose another name, or delete the file first "
            "if you meant to start over."
        )
        return

    text = render_audit(
        audit=audit,
        provider=resolved,
        messages=f"messages/{audit}.txt",
        api_key_env=_api_key_env(resolved),
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")

    typer.secho(f"Created {target}", fg=typer.colors.GREEN)
    typer.echo("")
    typer.echo("Next:")
    typer.echo(f"  1. Write your messages, one per line, in messages/{audit}.txt")
    typer.echo(f"  2. Review the settings in {target.name}, including `api_key_env`")
    typer.echo(f"  3. taxman collect {audit}")


def _api_key_env(provider: Provider) -> str | None:
    """What the audit's `api_key_env:` field should say.

    A placeholder, always: only the user knows which variable on their machine
    holds the key, and taxman guessing at one is how an audit ends up billing a
    key it never named. A provider that needs no key gets no field at all.
    """
    if not provider.requires_api_key:
        return None
    return API_KEY_ENV_PLACEHOLDER
