"""Resolving an audit name to the YAML file that defines it.

`taxman collect <name>` takes a bare name, not a path. Names resolve against the
project-local `./audits/` directory first, then the user-global
`~/.taxman/audits/`, so an audit committed alongside a research project always
shadows a personal one of the same name.

A path to a YAML file is also accepted anywhere a name is, for one-off runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_taxman.core.errors import AuditNotFoundError

#: Suffixes recognised as audit files, in precedence order.
YAML_SUFFIXES = (".yaml", ".yml")

LOCAL_DIR_NAME = "audits"
GLOBAL_DIR_ENV_VAR = "TAXMAN_HOME"
GLOBAL_DIR_DEFAULT = Path("~/.taxman")

Source = Literal["local", "global"]


@dataclass(frozen=True, slots=True)
class AuditRef:
    """An audit file found on disk, with where it was found."""

    name: str
    path: Path
    source: Source


def default_local_dir() -> Path:
    """The project-local audit directory: `./audits`."""
    return Path.cwd() / LOCAL_DIR_NAME


def taxman_home() -> Path:
    """The user-global taxman directory, honouring `TAXMAN_HOME`.

    Holds user-global audits and the `doctor` state file.
    """
    root = os.environ.get(GLOBAL_DIR_ENV_VAR)
    return Path(root) if root else GLOBAL_DIR_DEFAULT.expanduser()


def default_global_dir() -> Path:
    """The user-global audit directory."""
    return taxman_home() / LOCAL_DIR_NAME


def find_audit(
    name: str,
    *,
    local_dir: Path | None = None,
    global_dir: Path | None = None,
) -> Path:
    """Return the YAML file defining the audit called `name`.

    `name` may also be a path to a YAML file, which is used as-is. Raises
    `AuditNotFoundError` naming the audits that *are* available.
    """
    direct = Path(name)
    if direct.suffix in YAML_SUFFIXES and direct.is_file():
        return direct

    local_dir = local_dir if local_dir is not None else default_local_dir()
    global_dir = global_dir if global_dir is not None else default_global_dir()

    for directory in (local_dir, global_dir):
        for suffix in YAML_SUFFIXES:
            candidate = directory / f"{name}{suffix}"
            if candidate.is_file():
                return candidate

    raise AuditNotFoundError(
        _not_found_message(name, list_audits(local_dir=local_dir, global_dir=global_dir))
    )


def list_audits(
    *,
    local_dir: Path | None = None,
    global_dir: Path | None = None,
) -> list[AuditRef]:
    """Return every audit visible from here, sorted by name.

    Local audits shadow global ones of the same name. Used by `taxman audits
    list` and by shell completion, so it never raises on a missing directory.
    """
    local_dir = local_dir if local_dir is not None else default_local_dir()
    global_dir = global_dir if global_dir is not None else default_global_dir()

    found: dict[str, AuditRef] = {}
    scan: tuple[tuple[Path, Source], ...] = ((global_dir, "global"), (local_dir, "local"))
    for directory, source in scan:
        for path in _yaml_files(directory):
            # Local is scanned second, so it overwrites the global entry.
            found[path.stem] = AuditRef(name=path.stem, path=path, source=source)

    return sorted(found.values(), key=lambda ref: ref.name)


def _yaml_files(directory: Path) -> list[Path]:
    try:
        entries = sorted(directory.iterdir())
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return []

    # Reverse suffix order so `.yaml` is scanned last and wins over `.yml`.
    return [
        path
        for suffix in reversed(YAML_SUFFIXES)
        for path in entries
        if path.suffix == suffix and path.is_file()
    ]


def _not_found_message(name: str, available: list[AuditRef]) -> str:
    if not available:
        return (
            f"No audit named {name!r}, and no audits were found in ./audits or "
            "~/.taxman/audits. Create one with `taxman init <provider>`."
        )
    names = ", ".join(ref.name for ref in available)
    return f"No audit named {name!r}. Available audits: {names}."
