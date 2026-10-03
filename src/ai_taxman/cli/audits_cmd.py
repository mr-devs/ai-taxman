"""`taxman audits` - list, show, and validate audit files."""

from __future__ import annotations

from typing import Annotated

import typer
import yaml

from ai_taxman.cli.completion import complete_audit
from ai_taxman.cli.util import display_path, fail, handles_taxman_errors
from ai_taxman.core.discovery import audits_dir, find_audit, list_audits, require_project_root
from ai_taxman.core.messages import read_messages
from ai_taxman.core.registry import get_provider

audits_app = typer.Typer(help="Inspect the audits visible from here.", no_args_is_help=True)

AuditName = Annotated[str, typer.Argument(help="Audit name.", autocompletion=complete_audit)]


@audits_app.command("list")
@handles_taxman_errors
def list_command() -> None:
    """List every audit in this project."""
    # Naming the root answers "am I in the right directory?" directly, which is
    # the question an unexpected listing always turns out to be.
    root = require_project_root()
    refs = list_audits(root=root)
    typer.echo(f"# project: {root}")

    if not refs:
        typer.echo(
            f"No audits in {display_path(audits_dir(root))}. "
            "Create one with `taxman init <provider> <audit>`."
        )
        return

    width = max(len(ref.name) for ref in refs)
    for ref in refs:
        typer.echo(f"{ref.name:<{width}}  {display_path(ref.path)}")


@audits_app.command("show")
@handles_taxman_errors
def show_command(audit: AuditName) -> None:
    """Print an audit's fully resolved settings, defaults included."""
    # Imported here, not at module scope: completion imports this module on every
    # Tab press and must not pay for the config parser.
    from ai_taxman.core.config import load_audit

    config = load_audit(find_audit(audit))
    typer.echo(f"# {config.source_path}")
    typer.echo(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False).rstrip())


@audits_app.command("validate")
@handles_taxman_errors
def validate_command(audit: AuditName) -> None:
    """Check an audit end to end without sending anything."""
    # Imported here, not at module scope: completion imports this module on every
    # Tab press and must not pay for the config parser.
    from ai_taxman.core.config import load_audit
    from ai_taxman.core.runner import resolve_key

    config = load_audit(find_audit(audit))
    provider = get_provider(config.provider)

    try:
        provider.validate_model_config(dict(config.model))
    except Exception as exc:  # noqa: BLE001 - providers raise their own validation errors
        fail(f"the `model:` block is not valid for {provider.name!r}:\n  {exc}")
        return

    messages = read_messages(config.messages_path)
    # The first thing `collect` checks, so a "valid" here must mean it passes.
    resolve_key(provider, config)
    total = len(messages) * config.execution.repeats
    typer.secho(f"{config.source_path} is valid.", fg=typer.colors.GREEN)
    typer.echo(
        f"{len(messages)} message(s) x {config.execution.repeats} repeat(s) = {total} request(s)."
    )
