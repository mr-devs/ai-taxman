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
import hashlib
import json
import random
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
    resolve_output_dir,
)
from ai_taxman.core.credentials import resolve_api_key
from ai_taxman.core.errors import ConfigError, ProviderError, TaxmanError
from ai_taxman.core.logging import get_logger
from ai_taxman.core.messages import Message, hash_message, read_messages
from ai_taxman.core.prompts import SystemPrompt, read_system_prompt
from ai_taxman.core.records import (
    RESPONSE_SCHEMA_VERSION,
    ResponseRecord,
    RunManifest,
    new_run_id,
    timestamp,
    utc_now,
    validate_run_id,
)
from ai_taxman.core.registry import get_provider
from ai_taxman.core.writer import JsonlWriter
from ai_taxman.providers.base import Provider, Request

#: Never logs a message body or an API key - ids and counts only. A log is a
#: file users pipe into issues; the message text is already in the JSONL.
log = get_logger(__name__)

MANIFEST_FILENAME = "manifest.json"
DEFAULT_BACKOFF_BASE = 0.5
MAX_BACKOFF = 30.0

OnRecord = Callable[[ResponseRecord], None]


@dataclass(frozen=True, slots=True)
class RunResult:
    """What a finished run produced."""

    run_id: str
    output_path: Path
    manifest_path: Path
    n_ok: int
    n_error: int
    stopped_early: bool = False

    @property
    def total(self) -> int:
        return self.n_ok + self.n_error


def run_audit(
    audit: str | Path | AuditConfig,
    *,
    provider: Provider | None = None,
    on_record: OnRecord | None = None,
    run_id: str | None = None,
    stop_signals: tuple[int, ...] = (),
) -> RunResult:
    """Run an audit. The synchronous entry point for the Python API and CLI."""
    config = audit if isinstance(audit, AuditConfig) else load_audit(audit)
    return asyncio.run(
        run_audit_async(
            config,
            provider=provider,
            on_record=on_record,
            run_id=run_id,
            stop_signals=stop_signals,
        )
    )


async def run_audit_async(
    config: AuditConfig,
    *,
    provider: Provider | None = None,
    on_record: OnRecord | None = None,
    run_id: str | None = None,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    seed: int | None = None,
    stop_signals: tuple[int, ...] = (),
) -> RunResult:
    """Run an audit and return what it produced.

    Everything that can fail cheaply — a missing message file, an invalid
    `model:` block, an unavailable provider — fails before a single request goes
    out.

    `stop_signals` names signals that should end the run *gracefully*: requests
    already in flight are finished and written, and the manifest is finalised as
    `stopped_early`. It defaults to none, because a library does not take a
    caller's signal handlers; `taxman collect` passes SIGTERM so that killing a
    background run keeps the responses it already paid for.
    """
    provider = provider or get_provider(config.provider)
    checked = preflight(config, provider)
    messages, system_prompt, model, api_key = (
        checked.messages,
        checked.system_prompt,
        checked.model,
        checked.api_key,
    )

    run_id = validate_run_id(run_id) if run_id is not None else new_run_id()
    limit = config.execution.max_concurrency
    tasks = _expand(messages, config.execution.repeats)
    if config.execution.shuffle:
        random.Random(seed).shuffle(tasks)

    directory = resolve_output_dir(config, run_id=run_id)
    _guard_run_directory(directory, run_id=run_id)

    manifest = _new_manifest(config, provider, model, messages, run_id, system_prompt)
    manifest_path = directory / MANIFEST_FILENAME

    state = _RunState(on_error=config.execution.on_error)
    # Constructed here but not opened: nothing touches the disk until the `with`.
    writer = JsonlWriter(directory / config.output.filename, compress=config.output.compress)
    on_disk = False

    # Start the provider first, so a run that cannot begin - a missing SDK, most
    # often - leaves no directory behind. Everything after that point is written
    # before the first request goes out, so the run is described from the moment
    # it can produce anything at all.
    try:
        _handle_stop_signals(stop_signals, state)
        await _startup(provider, api_key)
        _write_manifest(manifest, manifest_path)
        on_disk = True
        log.info(
            "run starting  run_id=%s audit=%s provider=%s model=%s "
            "messages=%d repeats=%d expected=%d concurrency=%d output=%s",
            run_id,
            config.audit,
            provider.name,
            manifest.model,
            len(messages),
            config.execution.repeats,
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
                system_prompt=system_prompt.text if system_prompt else None,
                run_id=run_id,
                limit=limit,
                backoff_base=backoff_base,
                writer=writer,
                state=state,
                on_record=on_record,
            )
    except (KeyboardInterrupt, asyncio.CancelledError):
        manifest.status = "interrupted"
        raise
    except BaseException:
        manifest.status = "failed"
        raise
    else:
        manifest.status = "stopped_early" if state.stop.is_set() else "complete"
    finally:
        _release_stop_signals(stop_signals)
        await _shutdown(provider)
        if on_disk:
            manifest.finished_at = timestamp()
            manifest.n_ok = state.n_ok
            manifest.n_error = state.n_error
            _write_manifest(manifest, manifest_path)
            log.info(
                "run %s  run_id=%s ok=%d error=%d output=%s",
                manifest.status,
                run_id,
                state.n_ok,
                state.n_error,
                writer.path,
            )

    return RunResult(
        run_id=run_id,
        output_path=writer.path,
        manifest_path=manifest_path,
        n_ok=state.n_ok,
        n_error=state.n_error,
        stopped_early=state.stop.is_set(),
    )


def _handle_stop_signals(signals: tuple[int, ...], state: _RunState) -> None:
    """Turn the named signals into a request to wind the run down.

    `kill <pid>` on a background run should not throw away work already paid
    for. Setting the stop flag lets in-flight requests finish and be written,
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
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _guard_run_directory(directory: Path, *, run_id: str) -> None:
    """Refuse to write a second run into another run's directory.

    `output.dir` is the user's to set, and dropping `{run_id}` from it points
    every run at one place. The JSONL is opened in append mode and the manifest
    is overwritten, so the result is one file holding two runs described by a
    manifest that accounts for half of it. Appending under the same run id is
    deliberate - that is what `--run-id` is for - so only a different one is an
    error.
    """
    existing = _existing_run_id(directory / MANIFEST_FILENAME)
    if existing is None or existing == run_id:
        return

    raise ConfigError(
        f"{directory} already holds run {existing}, and this run is {run_id}. "
        "Two runs cannot share a directory: the responses would be appended to "
        "one file and the manifest would describe only the newer run. Keep "
        "`{run_id}` in the audit's `output.dir`, or pass `--run-id "
        f"{existing}` to add to that run on purpose."
    )


def _existing_run_id(path: Path) -> str | None:
    """The run id a directory already claims, or None if it claims none.

    Unreadable is treated as absent: this guard exists to catch a misconfigured
    `output.dir`, not to police a directory the user has been editing.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    run_id = data.get("run_id") if isinstance(data, dict) else None
    return run_id if isinstance(run_id, str) else None


def load_system_prompt(config: AuditConfig) -> SystemPrompt | None:
    """Read the audit's system prompt file, if it names one.

    Public because `audits validate` and `collect --background` make the same
    check before anything is sent.
    """
    path = config.system_prompt_path
    return read_system_prompt(path) if path is not None else None


def resolve_key(provider: Provider, config: AuditConfig) -> str | None:
    """Look up the one variable this audit names, if the provider needs a key.

    There is no fallback chain: the audit names a variable, and that variable
    either holds a key or the run stops here - before anything is sent, and
    before any output directory is created.

    Public because `collect --background` has to make the same check, with the
    same message, before it hands back a pid for a run that could never work.
    """
    if not provider.requires_api_key:
        return None

    if not config.api_key_env:
        raise ConfigError(
            f"{config.source_path} has no `api_key_env:`, and the "
            f"{provider.name!r} provider needs an API key. Add the name of the "
            "environment variable holding it, and export that variable in your shell."
        )

    return resolve_api_key(config.api_key_env)


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
    """Counters and the stop flag shared by every worker."""

    on_error: str
    n_ok: int = 0
    n_error: int = 0
    stop: asyncio.Event = dataclass_field(default_factory=asyncio.Event)

    def record(self, status: str) -> None:
        if status == "ok":
            self.n_ok += 1
        else:
            self.n_error += 1
            if self.on_error == "stop":
                self.stop.set()


def _expand(messages: list[Message], repeats: int) -> list[tuple[Message, int]]:
    """Flatten to `(message, repeat)` pairs, message-major.

    Message-major means all repeats of one message are adjacent, and therefore
    dispatched together by the pool.
    """
    return [(message, repeat) for message in messages for repeat in range(repeats)]


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
            return await _attempt(
                Request(message=message, repeat=repeat, model=model, system_prompt=system_prompt),
                provider=provider,
                config=config,
                run_id=run_id,
                backoff_base=backoff_base,
            )

    pending = [asyncio.create_task(one(message, repeat)) for message, repeat in tasks]
    try:
        for finished in asyncio.as_completed(pending):
            record = await finished
            if record is None:
                continue
            writer.write(record)
            state.record(record.status)
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
        system_prompt_hash=hash_message(request.system_prompt) if request.system_prompt else None,
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


def preflight(config: AuditConfig, provider: Provider) -> Preflight:
    """Everything that can be checked without sending anything, in one place.

    `audits validate`, `collect` (foreground and background) and the runner all
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
        taxman_version=__version__,
        schema_version=RESPONSE_SCHEMA_VERSION,
        messages_path=_project_relative(config.messages_path, config.project_root),
        messages_hash=_file_hash(config.messages_path),
        system_prompt_path=(
            _project_relative(system_prompt.path, config.project_root) if system_prompt else None
        ),
        system_prompt_hash=system_prompt.hash if system_prompt else None,
        system_prompt_text=system_prompt.text if system_prompt else None,
        n_messages=len(messages),
        repeats=config.execution.repeats,
        config=config.model_dump(mode="json"),
        started_at=timestamp(),
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


def _file_hash(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
