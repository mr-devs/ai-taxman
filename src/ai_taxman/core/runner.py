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

from ai_taxman.core.config import AuditConfig, load_audit, resolve_output_dir
from ai_taxman.core.credentials import resolve_api_key
from ai_taxman.core.errors import ConfigError, ProviderError, TaxmanError
from ai_taxman.core.messages import Message, read_messages
from ai_taxman.core.records import (
    RESPONSE_SCHEMA_VERSION,
    ResponseRecord,
    RunManifest,
    new_run_id,
    timestamp,
    utc_now,
)
from ai_taxman.core.registry import get_provider
from ai_taxman.core.writer import JsonlWriter
from ai_taxman.providers.base import Provider, Request

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
) -> RunResult:
    """Run an audit. The synchronous entry point for the Python API and CLI."""
    config = audit if isinstance(audit, AuditConfig) else load_audit(audit)
    return asyncio.run(run_audit_async(config, provider=provider, on_record=on_record))


async def run_audit_async(
    config: AuditConfig,
    *,
    provider: Provider | None = None,
    on_record: OnRecord | None = None,
    run_id: str | None = None,
    max_concurrency: int | None = None,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    seed: int | None = None,
) -> RunResult:
    """Run an audit and return what it produced.

    Everything that can fail cheaply — a missing message file, an invalid
    `model:` block, an unavailable provider — fails before a single request goes
    out.
    """
    provider = provider or get_provider(config.provider)
    messages = read_messages(config.messages_path)
    model = validate_model(provider, config)
    api_key = _resolve_key(provider, config)

    if config.execution.batch:
        raise NotImplementedError(
            f"Batch mode is not available yet for the {provider.name!r} provider. "
            "Set `execution.batch: false` in the audit."
        )

    run_id = run_id or new_run_id()
    limit = max_concurrency or config.execution.max_concurrency
    tasks = _expand(messages, config.execution.repeats)
    if config.execution.shuffle:
        random.Random(seed).shuffle(tasks)

    directory = resolve_output_dir(config, run_id=run_id)
    manifest = _new_manifest(config, provider, model, messages, run_id)

    state = _RunState(on_error=config.execution.on_error)

    # Start the provider before creating anything on disk, so a run that cannot
    # begin - a missing API key, most often - leaves no empty output behind.
    try:
        await _startup(provider, api_key)
        with JsonlWriter(
            directory / config.output.filename, compress=config.output.compress
        ) as out:
            await _dispatch(
                tasks,
                provider=provider,
                config=config,
                model=model,
                run_id=run_id,
                limit=limit,
                backoff_base=backoff_base,
                writer=out,
                state=state,
                on_record=on_record,
            )
    finally:
        await _shutdown(provider)

    manifest.finished_at = timestamp()
    manifest.n_ok = state.n_ok
    manifest.n_error = state.n_error
    manifest_path = directory / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return RunResult(
        run_id=run_id,
        output_path=out.path,
        manifest_path=manifest_path,
        n_ok=state.n_ok,
        n_error=state.n_error,
        stopped_early=state.stop.is_set(),
    )


def _resolve_key(provider: Provider, config: AuditConfig) -> str | None:
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
                Request(message=message, repeat=repeat, model=model),
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
            await asyncio.sleep(min(backoff_base * 2 ** (attempts - 1), MAX_BACKOFF))
            continue

        extracted = provider.extract(raw)
        return _record(
            request,
            model_name=provider.describe_model(request.model),
            config=config,
            run_id=run_id,
            started=started,
            attempts=attempts,
            status="ok",
            text=extracted.text,
            usage=extracted.usage,
            raw=raw,
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
    text: str | None = None,
    usage: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
    error: str | None = None,
) -> ResponseRecord:
    finished = utc_now()
    return ResponseRecord(
        audit=config.audit,
        run_id=run_id,
        message_id=request.message.id,
        message_hash=request.message.hash,
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
        text=text,
        usage=usage,
        raw=raw or {},
    )


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
) -> RunManifest:
    from ai_taxman import __version__

    return RunManifest(
        audit=config.audit,
        run_id=run_id,
        provider=provider.name,
        model=provider.describe_model(model),
        taxman_version=__version__,
        schema_version=RESPONSE_SCHEMA_VERSION,
        messages_path=str(config.messages_path),
        messages_hash=_file_hash(config.messages_path),
        n_messages=len(messages),
        repeats=config.execution.repeats,
        config=config.model_dump(mode="json"),
        started_at=timestamp(),
    )


def _file_hash(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
