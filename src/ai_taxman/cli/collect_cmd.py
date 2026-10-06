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
    new_run: Annotated[
        bool,
        typer.Option(
            "--new-run",
            help="Start a new run of the audit, even if its latest run is unfinished.",
        ),
    ] = False,
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

    Collecting an audit again finishes its latest run: only the responses that
    run is missing, or that failed, are sent. --new-run starts a new run instead.

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
    from ai_taxman.core.records import validate_run_id
    from ai_taxman.core.runner import preflight
    from ai_taxman.core.runs import plan_run

    # Before anything else, so a typo in the level is not discovered an hour into
    # a run.
    try:
        describe_level(log_level)
    except ValueError as exc:
        fail(str(exc))

    if new_run and run_id is not None:
        fail("--new-run starts a run of its own; it cannot be combined with --run-id.")

    config = load_audit(find_audit(audit))
    provider = get_provider(config.provider)

    if background:
        _start_in_background(
            audit,
            config=config,
            provider=provider,
            new_run=new_run,
            run_id=run_id,
            log_file=log_file,
            log_level=log_level,
            quiet=quiet,
        )
        return

    # Checked before the banner and the log: announcing a run that cannot start
    # reads as if it started. The runner checks again, for the Python API.
    checked = preflight(config, provider)
    model = provider.describe_model(checked.model)

    # Planned here rather than by the runner, because the log is named after the
    # run and has to be open before the run's first line.
    plan = plan_run(
        config,
        checked.messages,
        system_prompt=checked.system_prompt.text if checked.system_prompt else None,
        new_run=new_run,
        run_id=validate_run_id(run_id) if run_id is not None else None,
    )
    run_id = plan.run_id
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
    if plan.left_unfinished is not None:
        typer.secho(
            f"Leaving run {plan.left_unfinished} unfinished; starting run {run_id}.",
            fg=typer.colors.YELLOW,
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
    new_run: bool,
    run_id: str | None,
    log_file: Path | None,
    log_level: str,
    quiet: bool,
) -> None:
    """Validate, spawn a detached child, and say how to follow or stop it."""
    from pathlib import Path as _Path

    from ai_taxman.cli.background import PID_FILENAME, build_child_command, spawn
    from ai_taxman.core.config import resolve_log_file
    from ai_taxman.core.records import validate_run_id
    from ai_taxman.core.runner import preflight
    from ai_taxman.core.runs import plan_run

    # Everything checkable without doing the run, checked before the fork: a pid
    # for a run that could never have worked is worse than an error here.
    checked = preflight(config, provider)
    model = provider.describe_model(checked.model)

    # The parent decides which run the child collects, so it can name the run's
    # directory and log before the child starts. Checked here as well as in the
    # runner: a user's id becomes a path before the child ever sees it.
    plan = plan_run(
        config,
        checked.messages,
        system_prompt=checked.system_prompt.text if checked.system_prompt else None,
        new_run=new_run,
        run_id=validate_run_id(run_id) if run_id is not None else None,
    )
    run_id = plan.run_id
    directory = plan.directory
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
    if plan.left_unfinished is not None:
        typer.echo(f"Leaving run {plan.left_unfinished} unfinished.", err=True)
    typer.echo("", err=True)
    typer.echo(f"  run id   {run_id}", err=True)
    typer.echo(f"  pid      {pid}", err=True)
    typer.echo(f"  log      {log_file}", err=True)
    typer.echo(f"  output   {output}", err=True)
    typer.echo(f"  stop     kill {pid}   (or: kill $(cat {pid_file}))", err=True)
    typer.echo(pid)
