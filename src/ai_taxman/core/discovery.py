"""The project root, the folders it uses, and finding an audit inside it.

taxman is project-scoped from top to bottom. A project is any directory holding
a `taxman.yaml` marker, written by `taxman init`, and its audits live in exactly
one place: the audits folder that marker names. There is no user-global audit
directory, no search fallback, and no precedence rule — one name resolves to one
file, or to an error naming the directory that was searched.

The root is found by walking *up* from the working directory to the nearest
marker, the way git finds `.git`, so every command works from anywhere inside a
project. The marker is a visible file rather than the presence of `audits/`:
`audits/` is a common directory name in exactly the repositories taxman's users
keep, and a walk-up matching it would adopt an unrelated folder as a project
root and write collected data into it.

The marker records the project's folders (`Layout`) and nothing else. Only the
audits folder is read at run time, because finding an audit by name needs it.
The rest are written into each new audit by `taxman audits new`, so an audit
still names every path it uses and a run never depends on a setting outside
the file that describes it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath

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

MARKER_VERSION_KEY = "taxman_project"
MARKER_VERSION = 2

#: The marker's block of folders.
MARKER_PATHS_KEY = "paths"

#: What each folder holds, in the order `taxman init` asks about them.
FOLDER_PURPOSES = {
    "data": "Collected data",
    "audits": "Audit files",
    "messages": "Message files",
    "prompts": "System prompts",
    "logs": "Run logs",
}


@dataclass(frozen=True, slots=True)
class Layout:
    """The folders a project uses, each relative to its root.

    Every folder is checked on construction: a relative path that stays inside
    the project, not the root itself, and neither shared with nor nested inside
    another folder. A layout that exists is a layout that is safe to write to.
    """

    data: str = "taxman/data"
    audits: str = "taxman/audits"
    messages: str = "taxman/messages"
    prompts: str = "taxman/prompts"
    logs: str = "taxman/logs"

    def __post_init__(self) -> None:
        for purpose in FOLDER_PURPOSES:
            object.__setattr__(self, purpose, check_folder(getattr(self, purpose), purpose))

        chosen = {purpose: PurePosixPath(getattr(self, purpose)) for purpose in FOLDER_PURPOSES}
        for purpose, path in chosen.items():
            for other, other_path in chosen.items():
                if other == purpose:
                    continue
                if path == other_path:
                    raise ConfigError(
                        f"`{purpose}` and `{other}` are both {path}. Give each its own folder."
                    )
                if other_path in path.parents:
                    raise ConfigError(
                        f"`{purpose}` ({path}) is inside `{other}` ({other_path}). "
                        "Give each its own folder."
                    )

    def as_dict(self) -> dict[str, str]:
        return {purpose: getattr(self, purpose) for purpose in FOLDER_PURPOSES}


def check_folder(value: str, purpose: str) -> str:
    """Return `value` as a clean relative folder, or say why it cannot be one."""
    text = str(value).strip()
    if not text:
        raise ConfigError(f"The `{purpose}` folder is empty. Give a path like taxman/{purpose}.")

    path = PurePosixPath(text.replace("\\", "/"))
    if path.is_absolute() or text.startswith("~"):
        raise ConfigError(
            f"The `{purpose}` folder {text!r} is not relative. Folders are written "
            "relative to the project root, so the project still works after it is "
            "moved or cloned."
        )
    if ".." in path.parts:
        raise ConfigError(
            f"The `{purpose}` folder {text!r} leads outside the project. Every folder "
            "has to be inside it."
        )
    if not path.parts or path == PurePosixPath("."):
        raise ConfigError(
            f"The `{purpose}` folder cannot be the project root itself. "
            f"Give a folder like taxman/{purpose}."
        )
    return path.as_posix()


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
            f"{searched} or any parent directory. Start one with `taxman init`, "
            "or change to a directory inside an existing project."
        )

    version = _marker_version(root / MARKER_FILENAME)
    if version < MARKER_VERSION:
        raise ConfigError(
            f"{root / MARKER_FILENAME} was written by an earlier version of taxman, "
            "before a project recorded its folders. Delete it and run `taxman init` "
            "in that directory to set the project up again."
        )
    if version > MARKER_VERSION:
        raise ConfigError(
            f"{root / MARKER_FILENAME} was written by a newer version of taxman "
            f"({MARKER_VERSION_KEY}: {version}; this taxman understands "
            f"{MARKER_VERSION}). Upgrade taxman to use this project."
        )
    return root


def render_marker(layout: Layout) -> str:
    """The text of a marker recording `layout`, commented for the person editing it."""
    width = max(len(purpose) for purpose in FOLDER_PURPOSES) + 1
    folder_width = max(len(folder) for folder in layout.as_dict().values())
    folders = [
        f"  {purpose + ':':<{width}} {getattr(layout, purpose):<{folder_width}}  # {label}"
        for purpose, label in FOLDER_PURPOSES.items()
    ]
    return "\n".join(
        [
            "# Marks the root of a taxman project: every taxman command run in this",
            "# directory or below it works on this project.",
            "#",
            "# The folders taxman uses, relative to this directory. `taxman audits new`",
            "# writes them into each audit it creates, so a change here applies to",
            "# audits created afterwards. Audits themselves are always looked up in",
            "# `audits`.",
            f"{MARKER_VERSION_KEY}: {MARKER_VERSION}",
            f"{MARKER_PATHS_KEY}:",
            *folders,
            "",
        ]
    )


def write_marker(root: Path, layout: Layout | None = None) -> Path:
    """Make `root` a taxman project, leaving an existing marker untouched."""
    path = Path(root) / MARKER_FILENAME
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_marker(layout or Layout()), encoding="utf-8")
    return path


def read_layout(root: Path) -> Layout:
    """The folders the marker at `root` records.

    A folder the marker leaves out takes its default, so a hand-edited marker
    only has to name what it changes.
    """
    path = Path(root) / MARKER_FILENAME
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Could not read the project's folders from {path}: {exc}") from exc

    folders = data.get(MARKER_PATHS_KEY) if isinstance(data, dict) else None
    if folders is None:
        folders = {}
    if not isinstance(folders, dict):
        raise ConfigError(f"`{MARKER_PATHS_KEY}:` in {path} must be a mapping of folders.")

    known = {field.name for field in fields(Layout)}
    unknown = sorted(set(folders) - known)
    if unknown:
        raise ConfigError(
            f"{path} names folders taxman does not use: {', '.join(unknown)}. "
            f"The folders are: {', '.join(FOLDER_PURPOSES)}."
        )

    try:
        return Layout(**{key: str(value) for key, value in folders.items() if value is not None})
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def audits_dir(root: Path) -> Path:
    """The one directory a project's audits live in."""
    return Path(root) / read_layout(root).audits


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
            "Create one with `taxman audits new <provider> <audit>`."
        )
    names = ", ".join(ref.name for ref in available)
    return f"No audit named {name!r} in {directory}. Available audits: {names}."
