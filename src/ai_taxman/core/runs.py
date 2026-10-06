"""An audit's runs on disk.

Every `collect` of an audit is a run, written to the directory `output.dir`
names once `{audit}` and `{run_id}` are filled in. These are found again by
their manifests, which say which audit and run they belong to.
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ai_taxman.core.config import AuditConfig, resolve_output_dir
from ai_taxman.core.errors import ResponseFileError
from ai_taxman.core.records import RunManifest

MANIFEST_FILENAME = "manifest.json"

#: Stands in for a run id while `output.dir` is turned into a glob pattern. A
#: private-use character, so no real path contains it and `glob.escape` leaves it be.
_ANY_RUN = "run_id"


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
