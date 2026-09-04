"""The project root, and resolving an audit name to the file that defines it.

A taxman project is any directory holding a `taxman.yaml` marker. The root is
found by walking *up* from the working directory to the nearest one, the way git
finds `.git`, so a command works from anywhere inside a project.

The marker is a visible file rather than the presence of `audits/`: `audits/` is
a common directory name in exactly the repositories taxman's users keep, and a
walk-up matching it would adopt an unrelated folder as a project root.

`taxman.yaml` is a marker, not a config file. The only key read from it is its
schema version, which exists so a project written by a newer taxman is refused
rather than misread. Settings live in the audit.

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

import yaml

from ai_taxman.core.errors import (
    AuditNotFoundError,
    ConfigError,
    NotATaxmanProjectError,
)

#: Suffixes recognised as audit files, in precedence order.
YAML_SUFFIXES = (".yaml", ".yml")

#: The file whose presence makes a directory a taxman project.
MARKER_FILENAME = "taxman.yaml"

#: The only key core reads out of the marker. See the module docstring.
MARKER_VERSION_KEY = "taxman_project"
MARKER_VERSION = 1

MARKER_TEXT = f"""\
# Marks the root of a taxman project. Audits live in ./audits, and the relative
# paths inside them (messages, output.dir) resolve against this directory.
#
# This is a marker, not a config file: settings belong in the audit YAML, which
# `taxman init` writes fully commented.
{MARKER_VERSION_KEY}: {MARKER_VERSION}
"""

AUDITS_DIR_NAME = "audits"
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
    return Path.cwd() / AUDITS_DIR_NAME


def taxman_home() -> Path:
    """The user-global taxman directory, honouring `TAXMAN_HOME`.

    Holds user-global audits and the `doctor` state file.
    """
    root = os.environ.get(GLOBAL_DIR_ENV_VAR)
    return Path(root) if root else GLOBAL_DIR_DEFAULT.expanduser()


def default_global_dir() -> Path:
    """The user-global audit directory."""
    return taxman_home() / AUDITS_DIR_NAME


def find_project_root(start: Path | None = None) -> Path | None:
    """The nearest directory at or above `start` holding a marker, or None."""
    current = Path(start) if start is not None else Path.cwd()
    try:
        current = current.resolve()
    except OSError:
        return None

    for directory in (current, *current.parents):
        if (directory / MARKER_FILENAME).is_file():
            return directory
    return None


def require_project_root(start: Path | None = None) -> Path:
    """The project root, or a `NotATaxmanProjectError` explaining how to get one.

    Also refuses a project written by a newer taxman, which is the only reason
    the marker carries a version at all.
    """
    root = find_project_root(start)
    if root is None:
        searched = Path(start) if start is not None else Path.cwd()
        raise NotATaxmanProjectError(
            f"this directory is not a taxman project: no {MARKER_FILENAME} in "
            f"{searched} or any parent directory. Start one with "
            "`taxman init <provider> <audit>`, or change to a directory inside "
            "an existing project."
        )

    version = _marker_version(root / MARKER_FILENAME)
    if version > MARKER_VERSION:
        raise ConfigError(
            f"{root / MARKER_FILENAME} was written by a newer version of taxman "
            f"({MARKER_VERSION_KEY}: {version}; this taxman understands "
            f"{MARKER_VERSION}). Upgrade taxman to use this project."
        )
    return root


def write_marker(root: Path) -> Path:
    """Make `root` a taxman project, leaving an existing marker untouched."""
    path = Path(root) / MARKER_FILENAME
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(MARKER_TEXT, encoding="utf-8")
    return path


def audits_dir(root: Path) -> Path:
    """The one directory a project's audits live in."""
    return Path(root) / AUDITS_DIR_NAME


def _marker_version(path: Path) -> int:
    """The marker's schema version.

    A marker is a marker: an unreadable or unversioned one is treated as the
    current version rather than failing a run the user was in the middle of.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return MARKER_VERSION

    if not isinstance(data, dict):
        return MARKER_VERSION

    version = data.get(MARKER_VERSION_KEY, MARKER_VERSION)
    return version if isinstance(version, int) else MARKER_VERSION


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
