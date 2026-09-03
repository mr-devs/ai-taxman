"""`taxman collect` - run an audit and write the raw responses."""

from __future__ import annotations

from typing import Annotated

import typer

from ai_taxman.cli.completion import complete_audit
from ai_taxman.cli.util import fail, handles_taxman_errors
from ai_taxman.core.discovery import find_audit
from ai_taxman.core.registry import get_provider


@handles_taxman_errors
def collect(
    audit: Annotated[
        str,
        typer.Argument(
            help="Audit name (or a path to an audit YAML).", autocompletion=complete_audit
        ),
    ],
    repeats: Annotated[
        int | None,
        typer.Option("--repeats", "-r", min=1, help="Override execution.repeats."),
    ] = None,
    concurrency: Annotated[
        int | None,
        typer.Option("--concurrency", "-c", min=1, help="Override execution.max_concurrency."),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only print the summary.")] = False,
) -> None:
    """Send every message in an audit to its provider and record the responses."""
    # Imported here, not at module scope: shell completion imports this module on
    # every Tab press and must not pay for the config parser or the runner.
    from ai_taxman.core.config import load_audit
    from ai_taxman.core.runner import run_audit, validate_model

    config = load_audit(find_audit(audit))
    provider = get_provider(config.provider)

    if repeats is not None:
        config.execution.repeats = repeats
    if concurrency is not None:
        config.execution.max_concurrency = concurrency

    expected = config.execution.repeats
    if not quiet:
        # The provider names its own model; core has no business reading that block.
        model = provider.describe_model(validate_model(provider, config))
        typer.echo(
            f"Collecting {config.audit}: {config.provider} "
            f"({model or 'unspecified'}), {expected} repeat(s) per message."
        )

    result = run_audit(config, provider=provider)

    typer.echo("")
    typer.secho(f"{result.n_ok} ok", fg=typer.colors.GREEN, nl=False)
    if result.n_error:
        typer.echo(", ", nl=False)
        typer.secho(f"{result.n_error} error", fg=typer.colors.RED, nl=False)
    typer.echo(f"  ->  {result.output_path}")

    if result.stopped_early:
        typer.secho(
            "Run stopped early because execution.on_error is `stop`.",
            fg=typer.colors.YELLOW,
        )

    if result.n_ok == 0:
        fail("every message failed; see the error rows in the output for why.")
