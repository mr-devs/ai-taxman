"""The audit YAML: one file describes one audit.

The top-level blocks here (`audit`, `provider`, `messages`, `output`,
`execution`) are core-owned and identical for every provider. The `model:` block
is provider-owned and deliberately opaque to this module — it is handed to the
provider's own validator untouched. That split is what keeps providers isolated.

Relative paths resolve against the audit's *project root*: the nearest
directory at or above the file holding a `taxman.yaml` marker. So the layout
`taxman init` makes works as it reads, from anywhere inside it::

    my-research/
    |-- taxman.yaml                      <- the project root
    `-- taxman/
        |-- audits/election.yaml         messages: taxman/messages/election.txt
        |-- messages/election.txt
        |-- prompts/
        |-- data/
        `-- logs/

An audit with no marker above it is refused, by `load_audit()` as by the CLI:
without a root there is nothing to resolve its paths against, and a guess is
how collected data ends up somewhere nobody looks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_taxman.core.discovery import (
    YAML_SUFFIXES,
    find_audit,
    require_project_root,
)
from ai_taxman.core.errors import ConfigError

DEFAULT_OUTPUT_FILENAME = "responses.jsonl"


class _Strict(BaseModel):
    """Reject unknown keys so a typo is an error, not a silently ignored setting."""

    model_config = ConfigDict(extra="forbid")


class OutputConfig(_Strict):
    """Where and how raw responses are written.

    `dir` and `log_dir` have no default: a default could only be a guess at the
    project's folders, and a wrong guess writes data outside them. `audits new`
    writes both from `taxman.yaml`.
    """

    dir: str = Field(
        description="The folder each run writes its responses and manifest to, relative "
        "to the project root. {audit} and {run_id} are filled in at run time. Keep "
        "{run_id}: a folder holds one run, so without it the next run is refused."
    )
    filename: str = Field(
        default=DEFAULT_OUTPUT_FILENAME,
        description="The file in that folder that holds the raw responses, one JSON "
        "object per line.",
    )
    compress: bool = Field(
        default=False, description="Gzip the responses file as it is written, adding .gz."
    )
    log_dir: str = Field(
        description="The folder each run's log is written to, as <run_id>.log, relative "
        "to the project root. {audit} and {run_id} are filled in at run time."
    )


class ExecutionConfig(_Strict):
    """How the messages are sent."""

    repeats: int = Field(
        default=1,
        ge=1,
        description="How many times each message is sent. All repeats share one pool and "
        "go out interleaved, not as separate passes over the file.",
    )
    max_concurrency: int = Field(
        default=8,
        ge=1,
        description="How many requests are in flight at once. Lower it if the provider "
        "rate-limits you; raise it to finish sooner.",
    )
    batch: bool = Field(
        default=False,
        description="Send the messages through the provider's batch API instead of one "
        "request at a time. Not available yet: true is refused before anything is sent.",
    )
    timeout_s: float = Field(
        default=120.0,
        gt=0,
        description="Seconds to wait for one attempt at a response before giving up on "
        "it. Raise it for long answers or slow models.",
    )
    max_retries: int = Field(
        default=5,
        ge=0,
        description="How many times to retry a request that failed for a passing reason, "
        "such as a rate limit, a timeout, or a server error, waiting longer each time. "
        "Any other failure is recorded at once.",
    )
    on_error: Literal["continue", "stop"] = Field(
        default="continue",
        description="What to do when a request still fails after its retries.",
        json_schema_extra={
            "options": {
                "continue": "record the failure and keep sending",
                "stop": "send nothing more; requests already sent finish, and the run "
                "ends as stopped_early",
            }
        },
    )
    shuffle: bool = Field(
        default=False,
        description="Send the requests in a random order rather than in file order.",
    )


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

    output: OutputConfig = Field(
        description="Where each run's responses, manifest, and log are written."
    )
    execution: ExecutionConfig = Field(
        default_factory=ExecutionConfig, description="How the messages are sent."
    )
    #: Provider-owned. Core never inspects these keys.
    model: dict[str, Any] = Field(default_factory=dict)

    #: Set after loading; not read from the YAML.
    source_path: Path = Field(default=Path(), exclude=True)

    @property
    def project_root(self) -> Path:
        """The directory relative paths in this file resolve against: its project's."""
        return require_project_root(self.source_path.parent)

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

    Raises `ConfigError`, with the offending file named, for anything wrong with
    the file. Looking up a name can also raise `NotATaxmanProjectError` (no
    project around the working directory) or `AuditNotFoundError` (no audit by
    that name). All three are `TaxmanError`s, so catch that to handle any of them.
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

    # Outside a project there is no root to resolve its paths against, and
    # guessing one is how data lands somewhere nobody looks.
    require_project_root(path.parent)

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
