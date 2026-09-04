"""The audit YAML: one file describes one audit.

The top-level blocks here (`audit`, `provider`, `messages`, `output`,
`execution`) are core-owned and identical for every provider. The `model:` block
is provider-owned and deliberately opaque to this module — it is handed to the
provider's own validator untouched. That split is what keeps providers isolated.

Relative paths resolve against the audit's *project root*: the parent of the
containing directory when the file lives in an `audits/` directory, and the
containing directory otherwise. So the documented layout works as it reads::

    my-research/
    |-- audits/election.yaml     messages: messages/election.txt
    |-- messages/election.txt
    `-- data/
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_taxman.core.discovery import AUDITS_DIR_NAME
from ai_taxman.core.errors import ConfigError

DEFAULT_OUTPUT_DIR = "data/{audit}/{run_id}"
DEFAULT_OUTPUT_FILENAME = "responses.jsonl"


class _Strict(BaseModel):
    """Reject unknown keys so a typo is an error, not a silently ignored setting."""

    model_config = ConfigDict(extra="forbid")


class OutputConfig(_Strict):
    """Where and how raw responses are written."""

    dir: str = DEFAULT_OUTPUT_DIR
    filename: str = DEFAULT_OUTPUT_FILENAME
    compress: bool = False


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
        if directory.name == AUDITS_DIR_NAME:
            return directory.parent
        return directory

    @property
    def messages_path(self) -> Path:
        """The `.txt` file of messages, as an absolute-ish resolved path."""
        return self._resolve(self.messages)

    def _resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.project_root / path


def load_audit(path: str | Path) -> AuditConfig:
    """Load and validate the audit YAML at `path`.

    Raises `ConfigError` — with the offending file named — for anything wrong.
    """
    path = Path(path)

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
    expanded = config.output.dir.format(audit=config.audit, run_id=run_id)
    return config._resolve(expanded)


def _format(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(top level)"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)
