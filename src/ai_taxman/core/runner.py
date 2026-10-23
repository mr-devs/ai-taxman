"""Running an audit: expand, send, record.

The engine is deliberately small. It expands the audit into a flat list of
`(message, repeat)` tasks, hands them to one concurrency-limited pool, and
writes each result as it arrives.

Task order is **message-major**: every repeat of a message is dispatched
together, so the repeats of a single message go out at the same time rather than
as sequential passes over the whole file. This is the behaviour an auditor
usually wants — it measures a model's variability at a moment, not across the
span of a long run.

Nothing here knows anything about any particular provider.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_taxman.core.config import (
    AuditConfig,
    check_output_paths,
    load_audit,
)
from ai_taxman.core.credentials import API_KEY_ENV_PLACEHOLDER, resolve_api_key
from ai_taxman.core.errors import ConfigError, ProviderError, TaxmanError
from ai_taxman.core.logging import get_logger
from ai_taxman.core.messages import Message, read_messages
from ai_taxman.core.prompts import SystemPrompt, read_system_prompt
from ai_taxman.core.records import (
    RESPONSE_SCHEMA_VERSION,
    ResponseRecord,
    RunManifest,
    timestamp,
    utc_now,
)
from ai_taxman.core.registry import get_provider
from ai_taxman.core.runs import (
    MANIFEST_FILENAME,
    RunPlan,
    hold_audit,
    hold_run,
    output_file,
    plan_run,
)
from ai_taxman.core.writer import JsonlWriter, repair_tail
from ai_taxman.providers.base import Provider, Request

#: Never logs a message body or an API key - ids and counts only. A log is a
#: file users pipe into issues; the message text is already in the JSONL.
log = get_logger(__name__)

DEFAULT_BACKOFF_BASE = 0.5
MAX_BACKOFF = 30.0

OnRecord = Callable[[ResponseRecord], None]
OnClaim = Callable[[RunPlan], None]


@dataclass(frozen=True, slots=True)
class RunResult:
    """What a finished run produced."""

    run_id: str
    output_path: Path
    manifest_path: Path
    #: Responses written by this call - on a resumed run, only the ones it sent.
    n_ok: int
    n_error: int
    #: The run's own counts, every session included: what its manifest says.
    run_n_ok: int = 0
    run_n_error: int = 0
    #: How many responses the whole run has: messages times repeats.
    expected: int = 0
    stopped_early: bool = False
    #: The signal that stopped the run early, or None if a signal did not.
    stop_signal: int | None = None
    #: True when this call finished an existing run rather than starting one.
    resumed: bool = False
    #: True when the run already had every response, so nothing was sent.
    already_complete: bool = False

    @property
    def total(self) -> int:
        return self.n_ok + self.n_error


def run_audit(
    audit: str | Path | AuditConfig,
    *,
    provider: Provider | None = None,
    on_record: OnRecord | None = None,
    new_run: bool = False,
    stop_signals: tuple[int, ...] = (),
) -> RunResult:
    """Run an audit. The synchronous entry point for the Python API.

    Collects the audit's latest run if it is unfinished, sending only what that
    run is missing, and starts a new run if the audit has none. `new_run=True`
    starts a new run whatever is on disk.
    """
    config = audit if isinstance(audit, AuditConfig) else load_audit(audit)
    return asyncio.run(
        run_audit_async(
            config,
            provider=provider,
            on_record=on_record,
            new_run=new_run,
            stop_signals=stop_signals,
        )
    )


async def run_audit_async(
    config: AuditConfig,
    *,
    provider: Provider | None = None,
    on_record: OnRecord | None = None,
    new_run: bool = False,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    stop_signals: tuple[int, ...] = (),
) -> RunResult:
    """Run an audit and return what it produced.

    Everything that can fail cheaply — a missing message file, an invalid
    `model:` block, an unavailable provider — fails before a single request goes
    out.

    `stop_signals` names signals that should end the run *gracefully*: requests
    already in flight are finished and written, and the manifest is finalised as
    `stopped_early`. It defaults to none, because a library does not take a
    caller's signal handlers; `taxman collect` passes SIGTERM so that `kill <pid>`
    keeps the responses the run already paid for.
    """
    provider = provider or get_provider(config.provider)
    checked = preflight(config, provider)
    return await run_checked_async(
        config,
        checked,
        provider=provider,
        new_run=new_run,
        on_record=on_record,
        backoff_base=backoff_base,
        stop_signals=stop_signals,
    )


def run_checked(
    config: AuditConfig,
    checked: Preflight,
    *,
    provider: Provider,
    new_run: bool = False,
    on_claim: OnClaim | None = None,
    stop_signals: tuple[int, ...] = (),
) -> RunResult:
    """`run_checked_async`, synchronously."""
    return asyncio.run(
        run_checked_async(
            config,
            checked,
            provider=provider,
            new_run=new_run,
            on_claim=on_claim,
            stop_signals=stop_signals,
        )
    )


async def run_checked_async(
    config: AuditConfig,
    checked: Preflight,
    *,
    provider: Provider,
    new_run: bool = False,
    on_claim: OnClaim | None = None,
    on_record: OnRecord | None = None,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    stop_signals: tuple[int, ...] = (),
) -> RunResult:
    """Collect an audit that has passed `preflight`: plan its run, claim it, send.

    The run is planned once, here, once the provider has started and nothing is
    left to wait for, under the audit's lock (`runs.hold_audit`). It is claimed -
    its own lock taken and its manifest written - before that lock is let go, so
    a second collect of the audit started at the same moment plans after this
    one has claimed, and finds this run: being collected, or complete.

    `on_claim` is called with the plan once the run is claimed, before the run's
    first log line: how `taxman collect` names its log after a run it only now
    knows, and announces it.
    """
    messages, system_prompt, model, api_key = (
        checked.messages,
        checked.system_prompt,
        checked.model,
        checked.api_key,
    )
    system_text = checked.system_text

    limit = config.execution.max_concurrency
    state = _RunState(on_error=config.execution.on_error)
    plan: RunPlan | None = None
    #: Set once the run is claimed: its manifest is on disk and its lock held.
    manifest: RunManifest | None = None

    # Start the provider first, so a run that cannot begin - a missing SDK, most
    # often - leaves no directory behind. Everything after that point is written
    # before the first request goes out, so the run is described from the moment
    # it can produce anything at all.
    #
    # The run's lock is held from before the manifest is written until after it
    # is finalised, so no second `collect` can resume this run while it is collected.
    with contextlib.ExitStack() as lock:
        try:
            _handle_stop_signals(stop_signals, state)
            await _startup(provider, api_key)
            with hold_audit(config):
                plan = plan_run(config, messages, system_prompt=system_text, new_run=new_run)
                if not plan.complete:
                    lock.enter_context(hold_run(plan.directory))
                    opened = (
                        _new_manifest(config, provider, model, messages, plan.run_id, system_prompt)
                        if plan.manifest is None
                        else _reopened(plan.manifest)
                    )
                    _write_manifest(opened, plan.directory / MANIFEST_FILENAME)
                    manifest = opened
            if manifest is None:
                return _already_complete(plan, config)
            if on_claim is not None:
                on_claim(plan)

            run_id = plan.run_id
            writer = JsonlWriter(
                plan.directory / config.output.filename, compress=config.output.compress
            )
            state.answered, state.failed = set(plan.answered), set(plan.failed)
            tasks = list(plan.tasks)
            if plan.needs_repair and repair_tail(writer.path):
                log.warning(
                    "repaired  the end of %s, which a killed run left unfinished", writer.path
                )
            if plan.left_unfinished is not None:
                log.warning(
                    "new run  run_id=%s leaving run_id=%s unfinished", run_id, plan.left_unfinished
                )
            if plan.resuming:
                log.warning(
                    "resuming  run_id=%s collected=%d expected=%d sending=%d retrying=%d",
                    run_id,
                    len(plan.answered),
                    plan.expected,
                    len(tasks),
                    len(plan.failed),
                )
            log.info(
                "run starting  run_id=%s audit=%s provider=%s model=%s "
                "messages=%d repeats=%d expected=%d sending=%d concurrency=%d output=%s",
                run_id,
                config.audit,
                provider.name,
                manifest.model,
                len(messages),
                config.execution.repeats,
                plan.expected,
                len(tasks),
                limit,
                writer.path,
            )
            with writer:
                await _dispatch(
                    tasks,
                    provider=provider,
                    config=config,
                    model=model,
                    system_prompt=system_text,
                    run_id=run_id,
                    limit=limit,
                    backoff_base=backoff_base,
                    writer=writer,
                    state=state,
                    on_record=on_record,
                )
        except (KeyboardInterrupt, asyncio.CancelledError):
            if manifest is not None:
                manifest.status = "interrupted"
            raise
        except BaseException:
            if manifest is not None:
                manifest.status = "failed"
            raise
        else:
            if manifest is not None:
                manifest.status = "stopped_early" if state.stop.is_set() else "complete"
        finally:
            _release_stop_signals(stop_signals)
            await _shutdown(provider)
            if manifest is not None and plan is not None:
                _finalise(manifest, plan, config, state)

    return RunResult(
        run_id=plan.run_id,
        output_path=output_file(config, plan.directory),
        manifest_path=plan.directory / MANIFEST_FILENAME,
        n_ok=state.n_ok,
        n_error=state.n_error,
        run_n_ok=len(state.answered),
        run_n_error=len(state.failed),
        expected=plan.expected,
        stopped_early=state.stop.is_set(),
        stop_signal=state.stop_signal,
        resumed=plan.resuming,
    )


def _finalise(manifest: RunManifest, plan: RunPlan, config: AuditConfig, state: _RunState) -> None:
    """Write the manifest as the run ended, however it ended."""
    manifest.finished_at = timestamp()
    # Per pair across every session, so a failure later answered is not counted
    # twice, and one retried in vain is counted once.
    manifest.n_ok = len(state.answered)
    manifest.n_error = len(state.failed)
    _write_manifest(manifest, plan.directory / MANIFEST_FILENAME)
    log.info(
        "run %s  run_id=%s ok=%d error=%d output=%s",
        manifest.status,
        plan.run_id,
        state.n_ok,
        state.n_error,
        output_file(config, plan.directory),
    )


def _already_complete(plan: RunPlan, config: AuditConfig) -> RunResult:
    """Send nothing, and make sure the manifest agrees the run is complete."""
    record_complete(plan)
    return RunResult(
        run_id=plan.run_id,
        output_path=output_file(config, plan.directory),
        manifest_path=plan.directory / MANIFEST_FILENAME,
        n_ok=0,
        n_error=0,
        run_n_ok=len(plan.answered),
        expected=plan.expected,
        already_complete=True,
    )


def record_complete(plan: RunPlan) -> None:
    """Make the manifest of a run with every response say that it is complete.

    A run killed after writing its last response never got to say so. Public
    because `collect` finds a run complete before it reaches the runner.
    """
    assert plan.complete and plan.manifest is not None
    if plan.manifest.status == "complete" and plan.manifest.n_ok == len(plan.answered):
        return
    _write_manifest(
        plan.manifest.model_copy(
            update={
                "status": "complete",
                "n_ok": len(plan.answered),
                "n_error": 0,
                "finished_at": plan.manifest.finished_at or timestamp(),
            }
        ),
        plan.directory / MANIFEST_FILENAME,
    )


def _handle_stop_signals(signals: tuple[int, ...], state: _RunState) -> None:
    """Turn the named signals into a request to wind the run down.

    `kill <pid>` - or a job scheduler's time limit - should not throw away work
    already paid for. Setting the stop flag lets in-flight requests finish and be written,
    then finalises the manifest, instead of the process vanishing mid-write.

    Signal handling is unavailable on some platforms and inside some event
    loops; being unable to stop gracefully must not stop the run from starting.
    """
    if not signals:
        return

    loop = asyncio.get_running_loop()
    for number in signals:
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError, OSError):
            loop.add_signal_handler(number, _request_stop, state, number)


def _request_stop(state: _RunState, number: int) -> None:
    log.warning("stopping on signal %d: finishing what is in flight", number)
    if not state.stop.is_set():
        state.stop_signal = number
    state.stop.set()


def _release_stop_signals(signals: tuple[int, ...]) -> None:
    """Give the signals back, so a second run in this process starts clean."""
    if not signals:
        return

    with contextlib.suppress(RuntimeError):
        loop = asyncio.get_running_loop()
        for number in signals:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError, OSError):
                loop.remove_signal_handler(number)


def _write_manifest(manifest: RunManifest, path: Path) -> None:
    """Write the manifest, replacing any earlier version of itself.

    Called before the first request and again when the run ends, so the run
    directory describes itself from the moment it exists. A run killed in
    between leaves `status: running`, which is how a partial audit is told from
    a complete one.

    Written beside it and renamed over it, so a crash mid-write - or a reader
    looking at that moment - never meets half a manifest. One that cannot be
    read stops every later collect of the audit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{os.getpid()}.partial")
    try:
        with partial.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def load_system_prompt(config: AuditConfig) -> SystemPrompt | None:
    """Read the audit's system prompt file, if it names one."""
    path = config.system_prompt_path
    return read_system_prompt(path) if path is not None else None


def resolve_key(provider: Provider, config: AuditConfig) -> str | None:
    """Look up the one variable this audit names, if the provider needs a key.

    There is no fallback chain: the audit names a variable, and that variable
    either holds a key or the run stops here - before anything is sent, and
    before any output directory is created.
    """
    if not provider.requires_api_key:
        return None

    if not config.api_key_env:
        raise ConfigError(
            f"{config.source_path} has no `api_key_env:`, and the "
            f"{provider.name!r} provider needs an API key. Add the name of the "
            "environment variable holding it, and export that variable in your shell."
        )
    if config.api_key_env.strip() == API_KEY_ENV_PLACEHOLDER:
        raise ConfigError(
            f"`api_key_env:` in {config.source_path} is still {API_KEY_ENV_PLACEHOLDER}. "
            f"Replace it with the name of the environment variable that holds your "
            f"{provider.name} API key{_for_example(provider)}, and export that variable "
            "in your shell."
        )

    return resolve_api_key(config.api_key_env)


def _for_example(provider: Provider) -> str:
    """The variable this provider's SDK conventionally reads, as an example name."""
    usual = provider.default_api_key_env
    return f" (for example {usual})" if usual else ""


async def _startup(provider: Provider, api_key: str | None) -> None:
    """Prepare the provider, turning its own failures into a clean error."""
    try:
        await provider.startup(api_key=api_key)
    except TaxmanError:
        raise
    except Exception as exc:
        raise ProviderError(f"The {provider.name!r} provider could not be started: {exc}") from exc


async def _shutdown(provider: Provider) -> None:
    """Always give the provider a chance to close its client.

    A failure here must not mask whatever actually ended the run.
    """
    with contextlib.suppress(Exception):
        await provider.shutdown()


@dataclass
class _RunState:
    """Counters and the stop flag shared by every worker.

    `n_ok` and `n_error` count what this call wrote. `answered` and `failed` are
    the run's pairs, starting from what earlier sessions left on disk.
    """

    on_error: str
    answered: set[tuple[str, int]] = dataclass_field(default_factory=set)
    failed: set[tuple[str, int]] = dataclass_field(default_factory=set)
    n_ok: int = 0
    n_error: int = 0
    stop: asyncio.Event = dataclass_field(default_factory=asyncio.Event)
    #: The signal that set `stop`, if one did before an error could.
    stop_signal: int | None = None

    def record(self, record: ResponseRecord) -> None:
        pair = (record.message_id, record.repeat)
        if record.status == "ok":
            self.n_ok += 1
            self.answered.add(pair)
            self.failed.discard(pair)
        else:
            self.n_error += 1
            self.failed.add(pair)


async def _dispatch(
    tasks: list[tuple[Message, int]],
    *,
    provider: Provider,
    config: AuditConfig,
    model: object,
    system_prompt: str | None,
    run_id: str,
    limit: int,
    backoff_base: float,
    writer: JsonlWriter,
    state: _RunState,
    on_record: OnRecord | None,
) -> None:
    """Run every task through a semaphore, writing results as they land.

    Writes happen in the calling task, so the JSONL file is never written from
    two places at once and no lock is needed.
    """
    semaphore = asyncio.Semaphore(limit)

    async def one(message: Message, repeat: int) -> ResponseRecord | None:
        if state.stop.is_set():
            return None
        async with semaphore:
            if state.stop.is_set():
                return None
            record = await _attempt(
                Request(message=message, repeat=repeat, model=model, system_prompt=system_prompt),
                provider=provider,
                config=config,
                run_id=run_id,
                backoff_base=backoff_base,
            )
            # Here, before the slot is given up, and not when the record is
            # written: by then the next task has the slot and has sent its request.
            if record.status != "ok" and state.on_error == "stop":
                state.stop.set()
            return record

    pending = [asyncio.create_task(one(message, repeat)) for message, repeat in tasks]
    try:
        for finished in asyncio.as_completed(pending):
            record = await finished
            if record is None:
                continue
            writer.write(record)
            state.record(record)
            log.info(
                "%s  message=%s repeat=%d attempts=%d latency_ms=%d",
                record.status,
                record.message_id,
                record.repeat,
                record.attempts,
                record.latency_ms,
            )
            if on_record is not None:
                on_record(record)
    except BaseException:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        raise


async def _attempt(
    request: Request,
    *,
    provider: Provider,
    config: AuditConfig,
    run_id: str,
    backoff_base: float,
) -> ResponseRecord:
    """Send one request, retrying transient failures, and build its record."""
    started = utc_now()
    attempts = 0
    last_error: BaseException | None = None

    while attempts <= config.execution.max_retries:
        attempts += 1
        try:
            raw = await provider.send(request, timeout_s=config.execution.timeout_s)
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            raise
        except BaseException as exc:  # noqa: BLE001 - one bad response must not end the run
            last_error = exc
            if not provider.is_retryable(exc) or attempts > config.execution.max_retries:
                break
            delay = min(backoff_base * 2 ** (attempts - 1), MAX_BACKOFF)
            log.warning(
                "retrying  message=%s repeat=%d attempt=%d/%d in=%.1fs %s: %s",
                request.message.id,
                request.repeat,
                attempts,
                config.execution.max_retries,
                delay,
                type(exc).__name__,
                exc,
            )
            await asyncio.sleep(delay)
            continue

        return _record(
            request,
            model_name=provider.describe_model(request.model),
            config=config,
            run_id=run_id,
            started=started,
            attempts=attempts,
            status="ok",
            raw=raw,
        )

    log.error(
        "giving up  message=%s repeat=%d attempts=%d %s: %s",
        request.message.id,
        request.repeat,
        attempts,
        type(last_error).__name__,
        last_error,
    )
    return _record(
        request,
        model_name=provider.describe_model(request.model),
        config=config,
        run_id=run_id,
        started=started,
        attempts=attempts,
        status="error",
        error=f"{type(last_error).__name__}: {last_error}",
    )


def _record(
    request: Request,
    *,
    model_name: str,
    config: AuditConfig,
    run_id: str,
    started: datetime,
    attempts: int,
    status: str,
    raw: dict[str, Any] | None = None,
    error: str | None = None,
) -> ResponseRecord:
    finished = utc_now()
    return ResponseRecord(
        audit=config.audit,
        run_id=run_id,
        message_id=request.message.id,
        message=request.message.text,
        repeat=request.repeat,
        provider=config.provider,
        model=model_name,
        requested_at=timestamp(started),
        received_at=timestamp(finished),
        latency_ms=int((finished - started).total_seconds() * 1000),
        status=status,  # type: ignore[arg-type]
        error=error,
        attempts=attempts,
        raw=raw or {},
    )


@dataclass(frozen=True, slots=True)
class Preflight:
    """What an audit turned out to be, once checked."""

    messages: list[Message]
    system_prompt: SystemPrompt | None
    model: object
    api_key: str | None

    @property
    def system_text(self) -> str | None:
        """The system prompt's text, as sent with each message, or None if there is none."""
        return self.system_prompt.text if self.system_prompt is not None else None


def preflight(config: AuditConfig, provider: Provider) -> Preflight:
    """Everything that can be checked without sending anything, in one place.

    `audits validate`, `collect` and the runner all
    call this, so "valid" means the same thing everywhere: if it passes, the run
    can start. Add a check here, never to one caller - a separate list per caller
    drifts apart.
    """
    model = validate_model(provider, config)
    messages = read_messages(config.messages_path)
    system_prompt = load_system_prompt(config)
    api_key = resolve_key(provider, config)
    check_output_paths(config)

    if config.execution.batch:
        raise ConfigError(
            f"Batch mode is not available yet for the {provider.name!r} provider. "
            f"Set `execution.batch: false` in {config.source_path}."
        )

    return Preflight(messages, system_prompt, model, api_key)


def validate_model(provider: Provider, config: AuditConfig) -> object:
    """Hand the audit's `model:` block to its provider, as a `ConfigError` on failure.

    Public because the CLI needs the same validated object - and the same error
    message - before a run starts.
    """
    try:
        return provider.validate_model_config(dict(config.model))
    except Exception as exc:
        raise ConfigError(
            f"The `model:` block in {config.source_path} is not valid for the "
            f"{provider.name!r} provider:\n  {exc}"
        ) from exc


def _new_manifest(
    config: AuditConfig,
    provider: Provider,
    model: object,
    messages: list[Message],
    run_id: str,
    system_prompt: SystemPrompt | None,
) -> RunManifest:
    from ai_taxman import __version__

    return RunManifest(
        audit=config.audit,
        run_id=run_id,
        provider=provider.name,
        model=provider.describe_model(model),
        taxman_versions=[__version__],
        schema_version=RESPONSE_SCHEMA_VERSION,
        messages_path=_project_relative(config.messages_path, config.project_root),
        message_ids=[message.id for message in messages],
        system_prompt_path=(
            _project_relative(system_prompt.path, config.project_root) if system_prompt else None
        ),
        system_prompt_text=system_prompt.text if system_prompt else None,
        n_messages=len(messages),
        repeats=config.execution.repeats,
        config=config.model_dump(mode="json"),
        started_at=timestamp(),
    )


def _reopened(manifest: RunManifest) -> RunManifest:
    """The run as it was first described, running again: same settings, same start."""
    from ai_taxman import __version__

    versions = manifest.taxman_versions
    return manifest.model_copy(
        update={
            "status": "running",
            "finished_at": None,
            "taxman_versions": versions if __version__ in versions else [*versions, __version__],
        }
    )


def _project_relative(path: Path, root: Path) -> str:
    """`path` as the project names it, so a committed manifest leaks no home directory.

    A message file outside the project can only be named absolutely. One in a
    folder linked in from elsewhere is named as written, through the link.
    """
    for written, base in ((path, root), (path.resolve(), root.resolve())):
        try:
            return written.relative_to(base).as_posix()
        except ValueError:
            continue
    return str(path)
