"""`taxman collect` - run an audit and write the raw responses."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import IO, TYPE_CHECKING, Annotated, Any

import typer

from ai_taxman.cli.completion import complete_audit
from ai_taxman.cli.util import display_path, fail, handles_taxman_errors
from ai_taxman.core.discovery import find_audit
from ai_taxman.core.registry import get_provider

if TYPE_CHECKING:  # pragma: no cover - annotations only, never imported at runtime
    from datetime import datetime

    from tqdm import tqdm

    from ai_taxman.core.config import AuditConfig
    from ai_taxman.core.records import ResponseRecord
    from ai_taxman.core.runner import OnClaim, OnPause, OnRecord, Preflight, RunResult
    from ai_taxman.core.runs import RunPlan
    from ai_taxman.providers.base import Provider


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
) -> None:
    """Send every message in an audit to its provider and record the responses.

    Collecting an audit again finishes its latest run: only the responses that
    run is missing, or that failed, are sent. --new-run starts a new run instead.

    The run's log - one line per response, every retry and why - is written to
    the audit's log folder, as <run_id>.log.

    kill <pid> stops a run gracefully: what is in flight finishes, and
    collecting again picks up where it stopped. The pid is in the run's
    collect.pid while it runs.
    """
    # Imported here, not at module scope: shell completion imports this module on
    # every Tab press and must not pay for the config parser or the runner.
    from ai_taxman.core.config import load_audit, resolve_log_file
    from ai_taxman.core.logging import release_held, setup_logging
    from ai_taxman.core.runner import preflight

    config = load_audit(find_audit(audit))
    provider = get_provider(config.provider)

    # Checked before the banner and the log: announcing a run that cannot start
    # reads as if it started.
    checked = preflight(config, provider)
    model = provider.describe_model(checked.model)

    # The log is named after the run, which is known only once the runner has
    # claimed it. Until then, anything logged is held, and written to it then.
    setup_logging()

    def announce(plan: RunPlan) -> None:
        """The run is claimed: open its log, and say which run it is."""
        log_file = resolve_log_file(config, run_id=plan.run_id)
        setup_logging(log_file)
        # The provider names its own model; core has no business reading that block.
        typer.echo(
            f"Collecting {config.audit}: {config.provider} "
            f"({model or 'unspecified'}), {config.execution.repeats} repeat(s) per message."
        )
        typer.echo(f"Log: {display_path(log_file)}")
        if plan.left_unfinished is not None:
            typer.secho(
                f"Leaving run {plan.left_unfinished} unfinished; starting run {plan.run_id}.",
                fg=typer.colors.YELLOW,
            )
        if plan.resuming:
            typer.secho(_resuming(plan), fg=typer.colors.YELLOW)
        progress.start(len(plan.tasks))

    progress = _Progress()
    try:
        result = _run(
            config,
            checked,
            provider=provider,
            new_run=new_run,
            on_claim=announce,
            on_record=progress.record,
            on_pause=progress.paused,
        )
    finally:
        progress.close()
        # Only if no run was claimed - none needed collecting, or it was
        # refused - is anything still held: there is no log to put it in.
        release_held()
    if result.already_complete:
        _say_complete(
            config,
            run_id=result.run_id,
            expected=result.expected,
            directory=result.manifest_path.parent,
        )
        return

    typer.echo("")
    typer.secho(f"{result.n_ok} ok", fg=typer.colors.GREEN, nl=False)
    if result.n_error:
        typer.echo(", ", nl=False)
        typer.secho(f"{result.n_error} error", fg=typer.colors.RED, nl=False)
    typer.echo(f"  ->  {display_path(result.output_path)}")
    if result.resumed:
        typer.echo(f"Run {result.run_id} now has {result.run_n_ok} of {result.expected} responses.")

    if result.stop_signal is not None:
        import signal

        typer.secho(
            f"Run stopped on {signal.Signals(result.stop_signal).name}; "
            "collect again to finish it.",
            fg=typer.colors.YELLOW,
        )
    elif result.stop_reason is not None:
        typer.secho(
            f"Run stopped: {result.stop_reason}. Collect again later to finish it.",
            fg=typer.colors.YELLOW,
        )
    elif result.stopped_early:
        typer.secho(
            "Run stopped early because execution.on_error is `stop`.",
            fg=typer.colors.YELLOW,
        )

    # The whole run, not this session: a resumed run that only retried a message
    # that keeps failing has still collected everything else.
    if result.run_n_ok == 0:
        fail(_nothing_collected(result))


class _Progress:
    """What the terminal shows while a run goes: a bar of the responses this
    session sends, with how many came back and how many failed.

    A bar that cannot be drawn - a terminal gone, a pipe closed early - is
    dropped, and the run goes on: the terminal is never a reason to stop
    collecting, or to lose requests already sent."""

    def __init__(self) -> None:
        self._bar: tqdm[Any] | None = None
        self._ok = 0
        self._error = 0

    def start(
        self, total: int, *, disable: bool | None = None, file: IO[str] | None = None
    ) -> None:
        """Show the bar on stderr. `disable=None` leaves it off when that is not
        a terminal - under `nohup`, or redirected to a file - where it would only
        write a line per update."""
        # Imported here: shell completion imports this module on every Tab press.
        from tqdm import tqdm

        with self._or_drop():
            self._bar = tqdm(
                total=total,
                unit="response",
                file=file or sys.stderr,
                disable=disable,
                dynamic_ncols=True,
            )

    def record(self, record: ResponseRecord) -> None:
        if record.status == "ok":
            self._ok += 1
        else:
            self._error += 1
        if self._bar is None:
            return
        with self._or_drop():
            self._bar.set_postfix(ok=self._ok, error=self._error, refresh=False)
            self._bar.update(1)

    def paused(self, until: datetime) -> None:
        """Say why the bar has stopped: a pause, until the next response clears it."""
        if self._bar is None:
            return
        with self._or_drop():
            self._bar.set_postfix(ok=self._ok, error=self._error, paused=f"until {until:%H:%M:%S}")

    def close(self) -> None:
        if self._bar is not None:
            with self._or_drop():
                self._bar.close()

    @contextlib.contextmanager
    def _or_drop(self) -> Iterator[None]:
        try:
            yield
        except (OSError, ValueError):  # ValueError: a write to a closed file
            self._bar = None


def _nothing_collected(result: RunResult) -> str:
    """Why a run has no response at all: failures, a stop, or both."""
    failed = result.run_n_error
    if not failed:
        return "nothing was collected: the run stopped before any response came back."
    if failed < result.expected:
        return (
            f"no response was collected: {failed} of {result.expected} failed, and the run "
            "stopped before sending the rest. See the error rows in the output for why."
        )
    return "every message failed; see the error rows in the output for why."


def _resuming(plan: RunPlan) -> str:
    """Which run is being picked up, how far it got, and what is left to send."""
    sending, retrying = len(plan.tasks), len(plan.failed)
    if retrying and retrying == sending:
        again = ", after it failed before" if sending == 1 else ", all after failing before"
    elif retrying:
        again = f", {retrying} of them after failing before"
    else:
        again = ""
    return (
        f"Resuming run {plan.run_id}: {len(plan.answered)} of {plan.expected} responses "
        f"already collected; sending {sending}{again}."
    )


def _say_complete(config: AuditConfig, *, run_id: str, expected: int, directory: Path) -> None:
    """Say there is nothing to collect, and how to collect the audit again."""
    from ai_taxman.core.runs import output_file, start_over

    output = display_path(output_file(config, directory))
    typer.echo(
        f"{config.audit} is already complete: run {run_id} has all "
        f"{expected} responses, in {output}."
    )
    typer.echo(f"To start a new run, {start_over(config, display_path(directory))}.")


def _run(
    config: AuditConfig,
    checked: Preflight,
    *,
    provider: Provider,
    new_run: bool,
    on_claim: OnClaim,
    on_record: OnRecord,
    on_pause: OnPause,
) -> RunResult:
    """Run in this process, stopping gracefully on SIGTERM.

    The runner records our pid in the run directory while it holds the run.
    """
    import signal

    from ai_taxman.core.runner import run_checked

    # SIGTERM is what `kill <pid>` sends, and what a job scheduler sends at a time
    # limit. It winds the run down rather than killing it mid-write, so the
    # responses already paid for are kept and the manifest is finalised.
    stop_signals = (signal.SIGTERM,) if hasattr(signal, "SIGTERM") else ()

    return run_checked(
        config,
        checked,
        provider=provider,
        new_run=new_run,
        on_claim=on_claim,
        on_record=on_record,
        on_pause=on_pause,
        stop_signals=stop_signals,
    )
