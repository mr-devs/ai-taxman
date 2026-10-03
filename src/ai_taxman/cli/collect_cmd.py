"""`taxman collect` - run an audit and write the raw responses."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from ai_taxman.cli.completion import complete_audit
from ai_taxman.cli.util import display_path, fail, handles_taxman_errors
from ai_taxman.core.discovery import find_audit
from ai_taxman.core.registry import get_provider

if TYPE_CHECKING:  # pragma: no cover - annotations only, never imported at runtime
    from ai_taxman.core.config import AuditConfig
    from ai_taxman.core.runner import RunResult
    from ai_taxman.providers.base import Provider

#: Duplicated from `core.logging` as a plain string so that importing this module
#: - which shell completion does on every Tab press - stays free.
DEFAULT_LEVEL = "info"


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
    log_file: Annotated[
        Path | None,
        typer.Option(
            "--log-file",
            help="Write the run log here instead of the terminal. Parent directories are created.",
        ),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="debug | info | warning | error."),
    ] = DEFAULT_LEVEL,
    background: Annotated[
        bool,
        typer.Option(
            "--background",
            "-b",
            help="Start the run detached and return. Prints its pid, log, and output path.",
        ),
    ] = False,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Run under this id, adding to that run's directory."),
    ] = None,
    pid_file: Annotated[
        Path | None,
        typer.Option("--pid-file", hidden=True, help="Where a background run records its pid."),
    ] = None,
) -> None:
    """Send every message in an audit to its provider and record the responses.

    Progress is logged as the run happens - one line per response - to the
    terminal and to the audit's log folder, as <run_id>.log. --log-file sends it
    to that file alone instead.

    With --background the run is detached and the prompt comes straight back, so
    a script can start several providers at once and stop them by pid.
    """
    # Imported here, not at module scope: shell completion imports this module on
    # every Tab press and must not pay for the config parser or the runner.
    from ai_taxman.core.config import load_audit, resolve_log_file
    from ai_taxman.core.logging import describe_level, setup_logging
    from ai_taxman.core.records import new_run_id, validate_run_id
    from ai_taxman.core.runner import preflight

    # Before anything else, so a typo in the level is not discovered an hour into
    # a run.
    try:
        describe_level(log_level)
    except ValueError as exc:
        fail(str(exc))

    config = load_audit(find_audit(audit))
    provider = get_provider(config.provider)

    if repeats is not None:
        config.execution.repeats = repeats
    if concurrency is not None:
        config.execution.max_concurrency = concurrency

    if background:
        _start_in_background(
            audit,
            config=config,
            provider=provider,
            run_id=run_id,
            log_file=log_file,
            repeats=repeats,
            concurrency=concurrency,
            log_level=log_level,
            quiet=quiet,
        )
        return

    # Checked before the banner and the log: announcing a run that cannot start
    # reads as if it started. The runner checks again, for the Python API.
    model = provider.describe_model(preflight(config, provider).model)

    # Picked here rather than by the runner, because the log is named after it
    # and has to be open before the run's first line.
    run_id = validate_run_id(run_id) if run_id is not None else new_run_id()
    if log_file is None:
        setup_logging(
            level=log_level,
            log_file=resolve_log_file(config, run_id=run_id),
            also_terminal=True,
        )
    else:
        setup_logging(level=log_level, log_file=log_file)

    expected = config.execution.repeats
    if not quiet:
        # The provider names its own model; core has no business reading that block.
        typer.echo(
            f"Collecting {config.audit}: {config.provider} "
            f"({model or 'unspecified'}), {expected} repeat(s) per message."
        )

    result = _run(config, provider=provider, run_id=run_id, pid_file=pid_file)

    typer.echo("")
    typer.secho(f"{result.n_ok} ok", fg=typer.colors.GREEN, nl=False)
    if result.n_error:
        typer.echo(", ", nl=False)
        typer.secho(f"{result.n_error} error", fg=typer.colors.RED, nl=False)
    typer.echo(f"  ->  {display_path(result.output_path)}")

    if result.stopped_early:
        typer.secho(
            "Run stopped early because execution.on_error is `stop`.",
            fg=typer.colors.YELLOW,
        )

    if result.n_ok == 0:
        fail("every message failed; see the error rows in the output for why.")


def _run(
    config: AuditConfig,
    *,
    provider: Provider,
    run_id: str | None,
    pid_file: Path | None,
) -> RunResult:
    """Run in this process, recording our pid for as long as we are running.

    Only a backgrounded child is given a --pid-file: a foreground run is already
    in front of the person who started it.
    """
    import os
    import signal

    from ai_taxman.cli.background import clear_pid_file, write_pid_file
    from ai_taxman.core.runner import run_audit

    pid = os.getpid()
    if pid_file is not None:
        write_pid_file(pid_file, pid)

    # SIGTERM is what `kill <pid>` sends, and what stops a background run. It
    # winds the run down rather than killing it mid-write, so the responses
    # already paid for are kept and the manifest is finalised.
    stop_signals = (signal.SIGTERM,) if hasattr(signal, "SIGTERM") else ()

    try:
        return run_audit(config, provider=provider, run_id=run_id, stop_signals=stop_signals)
    finally:
        if pid_file is not None:
            clear_pid_file(pid_file, pid)


def _start_in_background(
    audit: str,
    *,
    config: AuditConfig,
    provider: Provider,
    run_id: str | None,
    log_file: Path | None,
    repeats: int | None,
    concurrency: int | None,
    log_level: str,
    quiet: bool,
) -> None:
    """Validate, spawn a detached child, and say how to follow or stop it."""
    from pathlib import Path as _Path

    from ai_taxman.cli.background import PID_FILENAME, build_child_command, spawn
    from ai_taxman.core.config import resolve_log_file, resolve_output_dir
    from ai_taxman.core.records import new_run_id, validate_run_id
    from ai_taxman.core.runner import preflight

    # Everything checkable without doing the run, checked before the fork: a pid
    # for a run that could never have worked is worse than an error here.
    model = provider.describe_model(preflight(config, provider).model)

    # The user's id if they named one, so `-b` behaves like the foreground run.
    # Checked here as well as in the runner: the parent builds the run directory
    # from it, and that happens before the child is ever started.
    run_id = validate_run_id(run_id) if run_id is not None else new_run_id()
    directory = resolve_output_dir(config, run_id=run_id)
    directory.mkdir(parents=True, exist_ok=True)

    # The user's --log-file if they gave one, as in the foreground. Made absolute
    # here so the path the child writes is the one printed below.
    log_file = (
        log_file.resolve() if log_file is not None else resolve_log_file(config, run_id=run_id)
    )
    pid_file = directory / PID_FILENAME
    argv = build_child_command(
        audit,
        run_id=run_id,
        log_file=log_file,
        pid_file=pid_file,
        repeats=repeats,
        concurrency=concurrency,
        log_level=log_level,
        quiet=quiet,
    )

    try:
        pid = spawn(argv, log_file=log_file, cwd=_Path.cwd())
    except OSError as exc:
        fail(f"could not start a background run: {exc}")
        return

    output = directory / config.output.filename

    # The block is for the person; stdout is for the script. A bare pid on
    # stdout makes `PID=$(taxman collect probe -b)` work, the way `docker run
    # -d` prints a container id - grepping this block for it is fiddly, and the
    # `pid` line and the `collect.pid` path both match the obvious pattern.
    typer.echo(
        f"Collecting {config.audit} in the background: {config.provider} "
        f"({model or 'unspecified'}), {config.execution.repeats} repeat(s) per message.",
        err=True,
    )
    typer.echo("", err=True)
    typer.echo(f"  run id   {run_id}", err=True)
    typer.echo(f"  pid      {pid}", err=True)
    typer.echo(f"  log      {log_file}", err=True)
    typer.echo(f"  output   {output}", err=True)
    typer.echo(f"  stop     kill {pid}   (or: kill $(cat {pid_file}))", err=True)
    typer.echo(pid)
