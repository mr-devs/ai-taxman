"""An audit's runs on disk, and what collecting the audit does next.

Every `collect` of an audit is a run, written to the directory `output.dir`
names once `{audit}` and `{run_id}` are filled in. These are found again by
their manifests, which say which audit and run they belong to.

Collecting an audit finishes its latest run rather than starting another: a run
is the full set of `(message, repeat)` pairs, and collecting again sends only
the pairs that have no successful response yet - those never sent, and those
that failed.
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ai_taxman.core.config import AuditConfig, resolve_output_dir
from ai_taxman.core.errors import ConfigError, ResponseFileError
from ai_taxman.core.messages import Message
from ai_taxman.core.records import RunManifest, new_run_id
from ai_taxman.core.writer import read_jsonl, target_path

MANIFEST_FILENAME = "manifest.json"

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

    @property
    def resuming(self) -> bool:
        return self.manifest is not None


def plan_run(
    config: AuditConfig,
    messages: list[Message],
    *,
    new_run: bool = False,
    run_id: str | None = None,
) -> RunPlan:
    """Decide which run collecting this audit continues, and what it still has to send.

    By default, the audit's latest run, or a new run if the audit has none. With
    `new_run`, a new run whatever is on disk. With `run_id`, that run: resumed if
    it is on disk, started if not - how a background parent hands its child the
    run it chose.
    """
    if new_run and run_id is not None:
        raise ValueError("Pass `new_run` or `run_id`, not both.")

    pairs = expand(messages, config.execution.repeats)

    if new_run:
        return _new_run_beside_the_last(config, pairs)

    existing: ExistingRun | None
    if run_id is not None:
        directory = _output_dir(config, run_id)
        manifest_path = directory / MANIFEST_FILENAME
        existing = (
            ExistingRun(run_id, directory, _read_manifest(manifest_path))
            if manifest_path.is_file()
            else None
        )
    else:
        existing = latest_run(config)

    if existing is None:
        run_id = run_id or new_run_id()
        return RunPlan(
            run_id=run_id,
            directory=_output_dir(config, run_id),
            tasks=pairs,
            expected=len(pairs),
            answered=frozenset(),
            failed=frozenset(),
            manifest=None,
        )

    expected = {(message.id, repeat) for message, repeat in pairs}
    answered, failed = _responses_so_far(output_file(config, existing.directory))
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
    )


def _new_run_beside_the_last(config: AuditConfig, pairs: list[tuple[Message, int]]) -> RunPlan:
    if "{run_id}" not in config.output.dir:
        raise ConfigError(
            f"`output.dir: {config.output.dir}` in {config.source_path} has no "
            "`{run_id}`, so it has room for one run, which collecting again "
            "finishes. Add `{run_id}` to `output.dir` to keep more than one run."
        )

    last = latest_run(config)
    run_id = new_run_id()
    return RunPlan(
        run_id=run_id,
        directory=_output_dir(config, run_id),
        tasks=pairs,
        expected=len(pairs),
        answered=frozenset(),
        failed=frozenset(),
        manifest=None,
        left_unfinished=last.run_id if last is not None and _unfinished(last.manifest) else None,
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


def _responses_so_far(path: Path) -> tuple[set[Pair], set[Pair]]:
    """The pairs answered successfully, and those that have only failed."""
    answered: set[Pair] = set()
    attempted: set[Pair] = set()
    for row in read_jsonl(path):
        pair = (row.message_id, row.repeat)
        attempted.add(pair)
        if row.status == "ok":
            answered.add(pair)
    return answered, attempted - answered


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
