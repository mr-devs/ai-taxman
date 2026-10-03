"""The audit YAML: one file describes one audit.

The top-level blocks here (`audit`, `provider`, `messages`, `output`,
`execution`) are core-owned and identical for every provider. The `model:` block
is provider-owned and deliberately opaque to this module — it is handed to the
provider's own validator untouched. That split is what keeps providers isolated.

Relative paths resolve against the audit's *project root*: the nearest
directory at or above the file holding a `taxman.yaml` marker. So the documented
layout works as it reads, from anywhere inside it::

    my-research/
    |-- taxman.yaml              <- the project root
    |-- audits/election.yaml     messages: messages/election.txt
    |-- messages/election.txt
    `-- data/

A file handed to `load_audit()` directly with no marker above it falls back to
its own location - the parent of an `audits/` directory, else the containing
directory. That is a fact about where the file sits, not a guess at the working
directory, so it stays deterministic wherever it is loaded from. The CLI never
reaches it: `find_audit()` requires a project first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_taxman.core.discovery import (
    YAML_SUFFIXES,
    find_audit,
    find_project_root,
)
from ai_taxman.core.errors import ConfigError

#: A loose file with no marker above it, sitting in a directory of this name,
#: resolves against that directory's parent.
_LOOSE_AUDITS_DIR = "audits"

DEFAULT_OUTPUT_DIR = "data/{audit}/{run_id}"
DEFAULT_OUTPUT_FILENAME = "responses.jsonl"
DEFAULT_LOG_DIR = "logs/{audit}"


class _Strict(BaseModel):
    """Reject unknown keys so a typo is an error, not a silently ignored setting."""

    model_config = ConfigDict(extra="forbid")


class OutputConfig(_Strict):
    """Where and how raw responses are written."""

    dir: str = DEFAULT_OUTPUT_DIR
    filename: str = DEFAULT_OUTPUT_FILENAME
    compress: bool = False
    #: Each run's log is `<log_dir>/<run_id>.log`.
    log_dir: str = DEFAULT_LOG_DIR


class ExecutionConfig(_Strict):
    """How the messages are sent."""

    repeats: int = Field(default=1, ge=1)
    max_concurrency: int = Field(default=8, ge=1)
    batch: bool = False
    timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=5, ge=0)
    on_error: Literal["continue", "stop"] = "continue"
    shuffle: bool = False


class AuditConfig(_Strict):
    """One audit, as loaded from its YAML file."""

    audit: str
    provider: str
    messages: str

    #: A file of system prompt text, sent with every message. None sends none.
    system_prompt: str | None = None

    #: The single environment variable holding this audit's API key. There is no
    #: fallback: this name, or nothing. Required unless the provider needs no key.
    api_key_env: str | None = None

    output: OutputConfig = Field(default_factory=OutputConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    #: Provider-owned. Core never inspects these keys.
    model: dict[str, Any] = Field(default_factory=dict)

    #: Set after loading; not read from the YAML.
    source_path: Path = Field(default=Path(), exclude=True)

    @property
    def project_root(self) -> Path:
        """The directory relative paths in this file resolve against."""
        directory = self.source_path.parent
        marked = find_project_root(directory)
        if marked is not None:
            return marked
        if directory.name == _LOOSE_AUDITS_DIR:
            return directory.parent
        return directory

    @property
    def messages_path(self) -> Path:
        """The `.txt` file of messages, as an absolute-ish resolved path."""
        return self._resolve(self.messages)

    @property
    def system_prompt_path(self) -> Path | None:
        """The system prompt file, resolved, or None when the audit names none."""
        return self._resolve(self.system_prompt) if self.system_prompt else None

    def _resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.project_root / path


def load_audit(path: str | Path) -> AuditConfig:
    """Load and validate an audit, by name or by the path to its YAML.

    A name (`"probe"`) resolves the way `taxman collect probe` does, in the
    project around the working directory. Anything ending in `.yaml` or `.yml`
    is read as a path.

    Raises `ConfigError` — with the offending file named — for anything wrong.
    """
    path = Path(path)
    if path.suffix not in YAML_SUFFIXES:
        path = find_audit(str(path))

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"No audit file at {path}.") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read the audit file at {path}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse the audit file at {path} as YAML: {exc}") from exc

    if data is None:
        raise ConfigError(f"The audit file at {path} is empty.")
    if not isinstance(data, dict):
        raise ConfigError(
            f"The audit file at {path} must be a mapping of settings, "
            f"but it contains {type(data).__name__}."
        )

    try:
        config = AuditConfig(**data, source_path=path)
    except ValidationError as exc:
        raise ConfigError(f"Invalid audit file at {path}:\n{_format(exc)}") from exc

    if config.audit != path.stem:
        raise ConfigError(
            f"The audit file at {path} declares `audit: {config.audit}`, "
            f"but `taxman collect` matches on the filename, so it must be "
            f"`audit: {path.stem}` (or rename the file to {config.audit}.yaml)."
        )

    return config


def resolve_output_dir(config: AuditConfig, *, run_id: str) -> Path:
    """Expand `{audit}` and `{run_id}` in `output.dir` and resolve it."""
    return config._resolve(_expand(config, "dir", config.output.dir, run_id))


def resolve_log_file(config: AuditConfig, *, run_id: str) -> Path:
    """The log file for one run: `<output.log_dir>/<run_id>.log`, resolved."""
    return config._resolve(_expand(config, "log_dir", config.output.log_dir, run_id)) / (
        f"{run_id}.log"
    )


def check_output_paths(config: AuditConfig) -> None:
    """Fail now, not mid-run, if `output.dir` or `output.log_dir` cannot be filled in."""
    resolve_output_dir(config, run_id="check")
    resolve_log_file(config, run_id="check")


def _expand(config: AuditConfig, key: str, template: str, run_id: str) -> str:
    try:
        return template.format(audit=config.audit, run_id=run_id)
    except (KeyError, IndexError, ValueError) as exc:
        raise ConfigError(
            f"`output.{key}: {template}` in {config.source_path} cannot be filled in "
            f"({exc!s}). Only {{audit}} and {{run_id}} are filled in; any other brace "
            "has to go."
        ) from exc


def _format(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(top level)"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)
