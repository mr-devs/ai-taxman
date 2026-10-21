"""An audit's runs on disk, and what collecting the audit does next.

Every `collect` of an audit is a run, written to the directory `output.dir`
names once `{audit}` and `{run_id}` are filled in. These are found again by
their manifests, which say which audit and run they belong to.

Collecting an audit finishes its latest run rather than starting another: a run
is the full set of `(message, repeat)` pairs, and collecting again sends only
the pairs that have no successful response yet - those never sent, and those
that failed. Only an unchanged audit is resumed: its settings, its message ids
and its system prompt text must all be what the run's manifest recorded.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from pydantic import ValidationError

from ai_taxman.core.config import AuditConfig, resolve_output_dir
from ai_taxman.core.errors import (
    AuditChangedError,
    ConfigError,
    ResponseFileError,
    RunInProgressError,
)
from ai_taxman.core.logging import get_logger
from ai_taxman.core.messages import Message
from ai_taxman.core.records import RunManifest, new_run_id
from ai_taxman.core.writer import JsonlReader, target_path

try:
    import fcntl
except ImportError:  # pragma: no cover - the test machines are POSIX
    fcntl = None  # type: ignore[assignment]

#: Ids and counts only, like every core logger.
log = get_logger(__name__)

MANIFEST_FILENAME = "manifest.json"

#: Held, with `flock`, by the process collecting the run, for as long as it is.
#: The OS releases it when that process ends, however it ends, so a killed run
#: never leaves a stale lock behind.
LOCK_FILENAME = "collect.lock"

#: The pid of the process collecting the run, written once it holds the lock and
#: removed when it lets go, so `kill $(cat data/probe/<run>/collect.pid)` stops
#: the collector, whichever process that is, with no parsing.
PID_FILENAME = "collect.pid"

#: How long a collect waits for a run's lock before calling it taken: far longer
#: than a check holds it, far shorter than any collector does.
_CHECK_OUTLASTED_S = 0.5

#: Stands in for a run id while `output.dir` is turned into a glob pattern. A
#: private-use character, so no real path contains it and `glob.escape` leaves it be.
_ANY_RUN = "\ue000run_id\ue000"


@dataclass(frozen=True, slots=True)
class ExistingRun:
    """A run of an audit that is already on disk."""

    run_id: str
    directory: Path
    manifest: RunManifest


def find_runs(config: AuditConfig) -> list[ExistingRun]:
    """Every run of this audit on disk, oldest first.

    A directory counts when its manifest names this audit and `output.dir` would
    put that run id exactly there; a folder `output.dir` shares with another
    audit's runs is therefore read without confusing the two.
    """
    pattern = glob.escape(str(_output_dir(config, _ANY_RUN))).replace(_ANY_RUN, "*")
    runs = []
    for found in glob.glob(str(Path(pattern) / MANIFEST_FILENAME)):
        path = Path(found)
        manifest = _read_manifest(path)
        if manifest.audit != config.audit:
            continue
        if _output_dir(config, manifest.run_id) != path.parent:
            continue
        runs.append(ExistingRun(manifest.run_id, path.parent, manifest))
    return sorted(runs, key=lambda run: (run.manifest.started_at or "", run.run_id))


def latest_run(config: AuditConfig) -> ExistingRun | None:
    """The run of this audit started last, or None if it has never been collected."""
    runs = find_runs(config)
    return runs[-1] if runs else None


#: A `(message_id, repeat)` pair: one response the run expects.
Pair = tuple[str, int]

#: Enough of a file's `stat` to tell that it was written to since: see `_fingerprint`.
Fingerprint = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class RunPlan:
    """What collecting an audit will do: which run, and which pairs to send."""

    run_id: str
    directory: Path
    #: The pairs still to send, message-major.
    tasks: list[tuple[Message, int]]
    #: How many pairs the whole run has.
    expected: int
    #: Pairs that already have a successful response.
    answered: frozenset[Pair]
    #: Pairs whose every response so far has failed.
    failed: frozenset[Pair]
    #: The run's manifest when an existing run is being resumed, else None.
    manifest: RunManifest | None
    #: An unfinished run a new run was asked for in place of, left as it is.
    left_unfinished: str | None = None
    #: The run's responses end in a tail a killed run left, which
    #: `writer.repair_tail` drops before anything is appended. Known from reading
    #: them to plan, so a whole file is not read again only to find that out.
    needs_repair: bool = False
    #: The responses file as it was when this plan read it, or None if it had none.
    read_from: Fingerprint | None = None

    @property
    def resuming(self) -> bool:
        return self.manifest is not None

    @property
    def complete(self) -> bool:
        """The run already has a successful response for every pair."""
        return self.resuming and not self.tasks


def plan_run(
    config: AuditConfig,
    messages: list[Message],
    *,
    system_prompt: str | None,
    new_run: bool = False,
    run_id: str | None = None,
    earlier: RunPlan | None = None,
) -> RunPlan:
    """Decide which run collecting this audit continues, and what it still has to send.

    By default, the audit's latest run, or a new run if the audit has none. With
    `new_run`, a new run whatever is on disk.

    `run_id` holds the plan to a run an earlier plan chose: how the runner, when
    it plans again before sending, keeps to the run `collect` announced - or a
    background parent picked for its child. That run is resumed if it is on
    disk. If not, it is started only the way the earlier plan would have started
    it - with `new_run`, or while the audit still has no run - so a run another
    collect started in between is never doubled.

    `earlier` is a plan this process made before this one. Its reading of the
    run's responses is used again if the file has not been written to since, so
    a run is not read twice - a large compressed one takes a while - to find
    what nothing has changed.

    Raises `AuditChangedError` rather than resume a run the audit no longer
    describes. `system_prompt` is the text that would be sent, or None.
    """
    pairs = expand(messages, config.execution.repeats)

    if run_id is None:
        if new_run:
            return _new_plan(config, pairs, new_run_id(), beside_the_last=True)
        latest = latest_run(config)
        if latest is None:
            return _new_plan(config, pairs, new_run_id())
        return _resume(config, messages, system_prompt, pairs, latest, earlier)

    existing = _run_on_disk(config, run_id)
    if existing is not None:
        if new_run:
            raise ConfigError(
                f"Run {run_id} of {config.audit} is already on disk, so it cannot be "
                "started as a new run."
            )
        return _resume(config, messages, system_prompt, pairs, existing, earlier)
    if not new_run:
        _refuse_a_second_run(config, run_id)
    return _new_plan(config, pairs, run_id, beside_the_last=new_run)


def _resume(
    config: AuditConfig,
    messages: list[Message],
    system_prompt: str | None,
    pairs: list[tuple[Message, int]],
    existing: ExistingRun,
    earlier: RunPlan | None,
) -> RunPlan:
    _check_not_in_progress(existing)
    _check_unchanged(config, messages, system_prompt, existing)

    expected = {(message.id, repeat) for message, repeat in pairs}
    path = output_file(config, existing.directory)
    # Before reading, so a row written while it is read shows as a change.
    seen = _fingerprint(path)
    if (
        earlier is not None
        and earlier.resuming
        and earlier.run_id == existing.run_id
        and earlier.read_from == seen
    ):
        answered, failed = set(earlier.answered), set(earlier.failed)
        needs_repair = earlier.needs_repair
    else:
        answered, failed, needs_repair = _responses_so_far(path)
    return RunPlan(
        run_id=existing.run_id,
        directory=existing.directory,
        tasks=[
            (message, repeat) for message, repeat in pairs if (message.id, repeat) not in answered
        ],
        expected=len(pairs),
        answered=frozenset(answered & expected),
        failed=frozenset(failed & expected),
        manifest=existing.manifest,
        needs_repair=needs_repair,
        read_from=seen,
    )


def _run_on_disk(config: AuditConfig, run_id: str) -> ExistingRun | None:
    directory = _output_dir(config, run_id)
    _guard_run_directory(directory, run_id=run_id)
    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    return ExistingRun(run_id, directory, _read_manifest(manifest_path))


def _refuse_a_second_run(config: AuditConfig, run_id: str) -> None:
    """Raise if the audit has a run already, so starting `run_id` would double it."""
    latest = latest_run(config)
    if latest is None:
        return
    _check_not_in_progress(latest)
    raise RunInProgressError(
        f"{config.audit} already has run {latest.run_id}, so starting run {run_id} would "
        f"make a second run of it. Collect again to finish run {latest.run_id}, or start "
        f"a new run with `taxman collect {config.audit} --new-run`."
    )


@contextlib.contextmanager
def hold_audit(config: AuditConfig) -> Iterator[None]:
    """Hold the audit while a collect plans its run and claims it.

    Two collects of one audit started together each plan before either has
    claimed anything, so both would send the same work. Planned again under this
    lock, which is kept until the run's own lock is taken and its manifest
    written, the second sees the run the first claimed.

    An `flock` on the audit file itself, so it leaves nothing behind. It is
    waited for, not refused: no one holds it for longer than it takes to plan.
    """
    path = config.source_path
    if not path.is_file():  # a config built in code, from no file
        yield
        return

    with _open_to_lock(path) as handle:
        _flock(
            handle,
            wait=True,
            about=path,
            unlocked="two collects of it started at once could both run",
        )
        yield


def _open_to_lock(path: Path) -> IO[bytes]:
    """`path`, opened to be locked but never written to.

    Opened to append where it can be: NFS emulates `flock` with byte-range
    locks, and an exclusive one needs a file open for writing. Opened to read
    where it cannot, as in a read-only checkout; a local filesystem locks that
    just as well.
    """
    try:
        return path.open("ab")
    except OSError:
        return path.open("rb")


@contextlib.contextmanager
def hold_run(directory: Path) -> Iterator[None]:
    """Hold a run's lock while it is collected; raise if another process holds it.

    The pid file is written only once the lock is held, so it always names the
    process collecting the run: a collect that is refused never touches it.
    """
    with _lock(directory):
        pid_file = directory / PID_FILENAME
        pid = os.getpid()
        pid_file.write_text(f"{pid}\n", encoding="utf-8")
        try:
            yield
        finally:
            _clear_pid_file(pid_file, pid)


def _clear_pid_file(path: Path, pid: int) -> None:
    """Remove the pid file if it is still this process's."""
    try:
        if path.read_text(encoding="utf-8").strip() == str(pid):
            path.unlink()
    except (OSError, ValueError):  # gone, or overwritten with what is not text
        return


@contextlib.contextmanager
def _lock(directory: Path) -> Iterator[None]:
    """`flock` the run's lock file, or raise `RunInProgressError` if it is taken.

    Taken is told from checked by waiting: a collector holds the lock for the
    whole run, but planning only takes it for an instant to see whether anyone
    holds it - see `_check_not_in_progress` - and a collect that met that
    instant would refuse a run no one is collecting.
    """
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / LOCK_FILENAME).open("a") as handle:
        deadline = time.monotonic() + _CHECK_OUTLASTED_S
        while True:
            try:
                _flock(
                    handle,
                    wait=False,
                    about=directory,
                    unlocked="a second collect of this run at the same time would not be refused",
                )
            except BlockingIOError as exc:
                if time.monotonic() < deadline:
                    time.sleep(0.01)
                    continue
                raise RunInProgressError(_in_progress_message(directory)) from exc
            break
        yield


def _flock(
    handle: IO[Any], *, wait: bool, about: Path, unlocked: str | None, shared: bool = False
) -> None:
    """Take an `flock` on `handle`, exclusive unless `shared`, held until it is closed.

    Without `wait`, a lock someone else holds raises `BlockingIOError`. Where
    `flock` does not exist (Windows), or the filesystem cannot lock (NFS without
    lockd, many SMB mounts), nothing is held, and `unlocked` - what that puts at
    risk - is logged if given. Returning, rather than going on inside the
    `except`, keeps that error out of the traceback of anything raised later.
    """
    if fcntl is None:  # pragma: no cover - the test machines are POSIX
        return
    try:
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(handle.fileno(), operation if wait else operation | fcntl.LOCK_NB)
    except BlockingIOError:
        raise
    except OSError as exc:
        if unlocked is not None:
            log.warning(
                "unlocked  %s cannot be locked here (%s), so %s",
                about,
                exc.strerror or exc,
                unlocked,
            )


def _check_not_in_progress(existing: ExistingRun) -> None:
    """Raise if another process is collecting `existing` right now.

    A shared lock, on the lock file opened only to read: it is refused while a
    collector holds the run, and it needs no write access to the run, so
    `audits validate` can check a read-only copy of the data. A lock file that
    cannot be read cannot say, so nothing is refused.
    """
    try:
        handle = (existing.directory / LOCK_FILENAME).open("rb")
    except OSError:
        return
    with handle:
        try:
            _flock(handle, shared=True, wait=False, about=existing.directory, unlocked=None)
        except BlockingIOError as exc:
            raise RunInProgressError(_in_progress_message(existing.directory)) from exc


def _in_progress_message(directory: Path) -> str:
    # The run's own id, from its manifest: the folder is named after it only
    # when `output.dir` has `{run_id}` in it.
    run_id = _existing_run_id(directory / MANIFEST_FILENAME)
    run = f"Run {run_id}" if run_id is not None else "The run"
    try:
        pid = int((directory / PID_FILENAME).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        stop = "or stop the taxman process collecting it"
    else:
        stop = f"or stop it with `kill {pid}`"
    return (
        f"{run} in {directory} is being collected right now, by another taxman "
        f"process. Wait for that to finish, {stop}; collecting again then picks up "
        "where it left off."
    )


def _check_unchanged(
    config: AuditConfig,
    messages: list[Message],
    system_prompt: str | None,
    existing: ExistingRun,
) -> None:
    """Raise unless the audit is still the one `existing` was started from.

    Messages are compared by id, not by the file's bytes: reordering lines or
    editing a comment changes nothing that is sent.
    """
    recorded = existing.manifest
    changes = _setting_changes(recorded.config, config.model_dump(mode="json"))

    before, after = set(recorded.message_ids), {message.id for message in messages}
    if before != after:
        changes.append(f"messages: {len(after - before)} added, {len(before - after)} removed")
    if recorded.system_prompt_text != system_prompt:
        changes.append("system prompt: the text sent with each message has changed")

    if changes:
        raise AuditChangedError(
            f"{config.audit} has changed since its run {existing.run_id} began, so "
            "that run cannot be finished:\n"
            + "".join(f"  {change}\n" for change in changes)
            + "Put the audit back as it was to finish that run. To start a new run, "
            + start_over(config, existing.directory)
            + "."
        )


def start_over(config: AuditConfig, directory: Path | str) -> str:
    """How to start a new run of the audit, given the run in `directory`.

    `--new-run` needs `{run_id}` in `output.dir`; without it there is room for
    one run, and the way to a new one is to move the old one out of the way.
    """
    if "{run_id}" in config.output.dir:
        return f"use `taxman collect {config.audit} --new-run`"
    return (
        f"move {directory} elsewhere and collect again (`output.dir` has no "
        "`{run_id}`, so it holds one run)"
    )


#: Marks a setting one side of a comparison does not have.
_NOT_SET = object()


def _setting_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Each setting that differs, as `dotted.key: old -> new`."""
    old, new = _flatten(before), _flatten(after)
    return [
        f"{key}: {_show(old.get(key, _NOT_SET))} -> {_show(new.get(key, _NOT_SET))}"
        for key in sorted(old.keys() | new.keys())
        if old.get(key, _NOT_SET) != new.get(key, _NOT_SET)
    ]


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    flat: dict[str, Any] = {}
    for key, item in value.items():
        flat.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
    return flat


def _show(value: Any) -> str:
    return "(not set)" if value is _NOT_SET else json.dumps(value)


def _new_plan(
    config: AuditConfig,
    pairs: list[tuple[Message, int]],
    run_id: str,
    *,
    beside_the_last: bool = False,
) -> RunPlan:
    """A run started from nothing; `beside_the_last` when the audit may have runs already."""
    directory = _output_dir(config, run_id)
    left_unfinished = None
    if beside_the_last:
        if "{run_id}" not in config.output.dir:
            raise ConfigError(
                f"`output.dir: {config.output.dir}` in {config.source_path} has no "
                "`{run_id}`, so it has room for one run, which collecting again "
                f"finishes. To start a new run, move {directory} elsewhere and "
                "collect again, or add `{run_id}` to `output.dir` to keep more than one."
            )
        last = latest_run(config)
        if last is not None and _unfinished(last.manifest):
            left_unfinished = last.run_id

    _guard_run_directory(directory, run_id=run_id)
    return RunPlan(
        run_id=run_id,
        directory=directory,
        tasks=pairs,
        expected=len(pairs),
        answered=frozenset(),
        failed=frozenset(),
        manifest=None,
        left_unfinished=left_unfinished,
    )


def _unfinished(manifest: RunManifest) -> bool:
    """Whether a run, by its manifest's own account, is missing any response."""
    expected = manifest.n_messages * manifest.repeats
    return manifest.status != "complete" or manifest.n_ok < expected


def expand(messages: list[Message], repeats: int) -> list[tuple[Message, int]]:
    """Flatten to `(message, repeat)` pairs, message-major.

    Message-major means all repeats of one message are adjacent, and therefore
    dispatched together by the pool.
    """
    return [(message, repeat) for message in messages for repeat in range(repeats)]


def output_file(config: AuditConfig, directory: Path) -> Path:
    """The responses file of the run in `directory`."""
    return target_path(directory / config.output.filename, compress=config.output.compress)


def _responses_so_far(path: Path) -> tuple[set[Pair], set[Pair], bool]:
    """The pairs answered successfully, those that have only failed, and whether
    the file needs repair before it is appended to."""
    answered: set[Pair] = set()
    attempted: set[Pair] = set()
    reader = JsonlReader(path)
    for row in reader:
        pair = (row.message_id, row.repeat)
        attempted.add(pair)
        if row.status == "ok":
            answered.add(pair)
    return answered, attempted - answered, bool(reader.needs_repair)


def _fingerprint(path: Path) -> Fingerprint | None:
    """Enough of a file's `stat` to tell that it was written to since, or None if it is missing.

    Rows are only appended, and a repair truncates the file or replaces it, so
    its contents never change without its size, inode or modification time.
    Taken from the file opened, not by its path: over NFS that is what makes
    the client ask the server, where another host's rows land, rather than
    answer from its cache.
    """
    try:
        with path.open("rb") as handle:
            found = os.fstat(handle.fileno())
    except FileNotFoundError:
        return None
    return (found.st_dev, found.st_ino, found.st_size, found.st_mtime_ns)


def _guard_run_directory(directory: Path, *, run_id: str) -> None:
    """Refuse to start a run in a directory another run already holds.

    `output.dir` is the user's to set. Without `{run_id}` it has room for one
    run, which collecting again resumes; without `{audit}` as well, two audits
    land in one place. The JSONL is opened in append mode and the manifest is
    overwritten, so writing a second run there would leave one file holding two
    runs, described by a manifest that accounts for half of it.
    """
    existing = _existing_run_id(directory / MANIFEST_FILENAME)
    if existing is None or existing == run_id:
        return

    raise ConfigError(
        f"{directory} already holds run {existing}, and this run is {run_id}. "
        "Two runs cannot share a directory: the responses would be appended to "
        "one file and the manifest would describe only the newer run. Put "
        "`{audit}` and `{run_id}` in the audit's `output.dir`, so each run has "
        "its own."
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


def _output_dir(config: AuditConfig, run_id: str) -> Path:
    return resolve_output_dir(config, run_id=run_id)


def _read_manifest(path: Path) -> RunManifest:
    """Read a manifest, or say which one could not be read.

    Never skipped: passing over a broken manifest could resume an older run, or
    start a new one beside a run that only looks missing.
    """
    try:
        return RunManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, ValidationError) as exc:
        raise ResponseFileError(f"The run manifest at {path} cannot be read: {exc}") from exc
