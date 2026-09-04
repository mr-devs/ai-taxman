"""The project root, and resolving an audit name to the file that defines it.

taxman is project-scoped from top to bottom. A project is any directory holding
a `taxman.yaml` marker, and its audits live in exactly one place:
`<root>/audits/<name>.yaml`. There is no user-global audit directory, no search
fallback, and no precedence rule — one name resolves to one file, or to an error
naming the directory that was searched.

The root is found by walking *up* from the working directory to the nearest
marker, the way git finds `.git`, so every command works from anywhere inside a
project. The marker is a visible file rather than the presence of `audits/`:
`audits/` is a common directory name in exactly the repositories taxman's users
keep, and a walk-up matching it would adopt an unrelated folder as a project
root and write collected data into it.

`taxman.yaml` is a marker, not a config file. The only key read from it is its
schema version, which exists so a project written by a newer taxman is refused
rather than misread. Settings live in the audit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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

#: Home for `doctor`'s remembered answers - a fact about the machine, never
#: about an audit. Nothing project-scoped may be stored here.
HOME_ENV_VAR = "TAXMAN_HOME"
HOME_DEFAULT = Path("~/.taxman")


@dataclass(frozen=True, slots=True)
class AuditRef:
    """An audit file found in the project."""

    name: str
    path: Path


def taxman_home() -> Path:
    """The user-global taxman directory, honouring `TAXMAN_HOME`.

    Holds `doctor`'s `state.yaml` and nothing else.
    """
    root = os.environ.get(HOME_ENV_VAR)
    return Path(root) if root else HOME_DEFAULT.expanduser()


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


def find_audit(
    name: str,
    *,
    root: Path | None = None,
    start: Path | None = None,
) -> Path:
    """Return the YAML file defining the audit called `name`.

    `name` may also be a path to a YAML file, for a one-off run — but it must be
    inside the project, since that is what its relative paths resolve against.
    """
    root = root if root is not None else require_project_root(start)
    directory = audits_dir(root)

    direct = Path(name)
    if direct.suffix in YAML_SUFFIXES:
        return _resolve_direct_path(direct, root)

    for suffix in YAML_SUFFIXES:
        candidate = directory / f"{name}{suffix}"
        if candidate.is_file():
            return candidate

    raise AuditNotFoundError(_not_found_message(name, directory, list_audits(root=root)))


def list_audits(
    *,
    root: Path | None = None,
    start: Path | None = None,
) -> list[AuditRef]:
    """Every audit in the project, sorted by name.

    Used by `taxman audits list` and by shell completion, so a missing `audits/`
    directory is an empty list rather than an error. Being outside a project is
    still an error — completion swallows it.
    """
    root = root if root is not None else require_project_root(start)
    return sorted(
        (AuditRef(name=path.stem, path=path) for path in _yaml_files(audits_dir(root))),
        key=lambda ref: ref.name,
    )


def _resolve_direct_path(path: Path, root: Path) -> Path:
    if not path.is_file():
        raise AuditNotFoundError(f"No audit file at {path}.")

    try:
        inside = path.resolve().is_relative_to(root.resolve())
    except OSError:
        inside = False

    if not inside:
        raise AuditNotFoundError(
            f"{path} is outside the taxman project at {root}. An audit's relative "
            "paths resolve against the project root, so it has to live inside one — "
            f"move it into {audits_dir(root)}."
        )
    return path


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


def _yaml_files(directory: Path) -> list[Path]:
    try:
        entries = sorted(directory.iterdir())
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return []

    # Reverse suffix order so `.yaml` is scanned last and wins over `.yml`.
    seen: dict[str, Path] = {}
    for suffix in reversed(YAML_SUFFIXES):
        for path in entries:
            if path.suffix == suffix and path.is_file():
                seen[path.stem] = path
    return list(seen.values())


def _not_found_message(name: str, directory: Path, available: list[AuditRef]) -> str:
    if not available:
        return (
            f"No audit named {name!r}, and there are no audits in {directory}. "
            "Create one with `taxman init <provider> <audit>`."
        )
    names = ", ".join(ref.name for ref in available)
    return f"No audit named {name!r} in {directory}. Available audits: {names}."
