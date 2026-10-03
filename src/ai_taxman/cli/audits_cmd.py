"""`taxman audits` - create, list, show, and validate audit files."""

from __future__ import annotations

from typing import Annotated

import typer
import yaml

from ai_taxman.cli.completion import complete_audit, complete_provider
from ai_taxman.cli.render import API_KEY_ENV_PLACEHOLDER, render_audit
from ai_taxman.cli.util import display_path, fail, handles_taxman_errors
from ai_taxman.core.discovery import (
    audits_dir,
    find_audit,
    list_audits,
    read_layout,
    require_project_root,
)
from ai_taxman.core.messages import read_messages
from ai_taxman.core.registry import get_provider
from ai_taxman.providers.base import Provider

audits_app = typer.Typer(
    help="Create and inspect the audits in this project.", no_args_is_help=True
)

AuditName = Annotated[str, typer.Argument(help="Audit name.", autocompletion=complete_audit)]


@audits_app.command("new")
@handles_taxman_errors
def new_command(
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

    Every setting is written at its default, and the folders come from the
    project's taxman.yaml. Open the file and change what you need - it lists
    everything the provider accepts, with comments.

    Two names in, one file out. Nothing else is accepted: parsing `key=value`
    settings here meant core had to route each one to its owner and each
    provider had to validate arbitrary keys, all to save opening the file that
    was written for exactly that purpose.
    """
    # Resolve the provider before touching the disk: an unknown name should not
    # leave a stray file behind.
    resolved = get_provider(provider)
    root = require_project_root()
    layout = read_layout(root)

    target = root / layout.audits / f"{audit}.yaml"
    if target.exists():
        fail(
            f"{display_path(target)} already exists. Choose another name, or delete the "
            "file first if you meant to start over."
        )
        return

    messages = f"{layout.messages}/{audit}.txt"
    text = render_audit(
        audit=audit,
        provider=resolved,
        messages=messages,
        output_dir=f"{layout.data}/{{audit}}/{{run_id}}",
        log_dir=f"{layout.logs}/{{audit}}",
        prompts=layout.prompts,
        api_key_env=_api_key_env(resolved),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")

    # Written from the project root, not the working directory: these are the
    # same strings the audit file holds, and they read the same from anywhere.
    audit_file = f"{layout.audits}/{audit}.yaml"
    typer.secho(f"Created {audit_file}", fg=typer.colors.GREEN)
    typer.echo("")
    typer.echo("Next:")
    typer.echo(f"  1. Add messages in {messages}")
    # The folder, not a file: the audit's own comment says how to name one.
    typer.echo(f"  2. (Optional) Add a system prompt in {layout.prompts}/")
    typer.echo(f"  3. Finalize details in {audit_file}")
    typer.echo(f"  4. Collect data by running `taxman collect {audit}`")


def _api_key_env(provider: Provider) -> str | None:
    """What the audit's `api_key_env:` field should say.

    A placeholder, always: only the user knows which variable on their machine
    holds the key, and taxman guessing at one is how an audit ends up billing a
    key it never named. A provider that needs no key gets no field at all.
    """
    if not provider.requires_api_key:
        return None
    return API_KEY_ENV_PLACEHOLDER


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
            "Create one with `taxman audits new <provider> <audit>`."
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
    typer.echo(f"# {display_path(config.source_path)}")
    typer.echo(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False).rstrip())


@audits_app.command("validate")
@handles_taxman_errors
def validate_command(audit: AuditName) -> None:
    """Check an audit end to end without sending anything."""
    # Imported here, not at module scope: completion imports this module on every
    # Tab press and must not pay for the config parser.
    from ai_taxman.core.config import load_audit
    from ai_taxman.core.runner import load_system_prompt, resolve_key

    config = load_audit(find_audit(audit))
    provider = get_provider(config.provider)

    try:
        provider.validate_model_config(dict(config.model))
    except Exception as exc:  # noqa: BLE001 - providers raise their own validation errors
        fail(f"the `model:` block is not valid for {provider.name!r}:\n  {exc}")
        return

    messages = read_messages(config.messages_path)
    load_system_prompt(config)
    # The first thing `collect` checks, so a "valid" here must mean it passes.
    resolve_key(provider, config)
    total = len(messages) * config.execution.repeats
    typer.secho(f"{display_path(config.source_path)} is valid.", fg=typer.colors.GREEN)
    typer.echo(
        f"{len(messages)} message(s) x {config.execution.repeats} repeat(s) = {total} request(s)."
    )
