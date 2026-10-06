"""The shape of the data an audit produces.

`ResponseRecord` is one line of the output JSONL, and it is identical for every
provider: shared envelope fields naming what was asked and how it went, plus the
provider's response verbatim in `raw`. Nothing is derived from `raw` — taxman
collects, and parsing happens downstream from the data on disk.

This schema is a promise to whoever analyses the data later. Changes are
**additive** — adding a field is fine; removing or renaming one, or changing
what it means, is a break that costs a `RESPONSE_SCHEMA_VERSION` bump and a
note in the README.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_taxman.core.errors import ConfigError

#: Bumped when a field is removed, renamed, or comes to mean something else -
#: once taxman is released. Until then it stays at 1 (see CLAUDE.md).
RESPONSE_SCHEMA_VERSION = 1

#: Fields an older schema version wrote that this one no longer has. Version 2
#: dropped `message_hash` once `message_id` was derived from the text.
RETIRED_FIELDS: dict[int, tuple[str, ...]] = {1: ("message_hash",)}

Status = Literal["ok", "error"]

#: How a run ended, or that it has not. `running` is what a killed run leaves
#: behind, and is the signal that a directory holds a partial audit.
RunStatus = Literal["running", "complete", "stopped_early", "interrupted", "failed"]

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
RUN_ID_FORMAT = "%Y%m%dT%H%M%SZ"


class ResponseRecord(BaseModel):
    """One response to one message, on one repeat."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = RESPONSE_SCHEMA_VERSION

    audit: str
    run_id: str

    message_id: str
    message: str
    repeat: int

    #: The line of the message file the message is on, so rows that arrived out
    #: of order can be put back in file order. None in rows written before it.
    message_line: int | None = None

    #: The hash of the system prompt sent with the message, or None for none.
    system_prompt_hash: str | None = None

    provider: str
    model: str

    requested_at: str
    received_at: str
    latency_ms: int

    status: Status
    error: str | None = None
    attempts: int = 1

    #: The provider's response, exactly as it came back.
    raw: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-ready row, including fields that are empty."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResponseRecord:
        """Rebuild a record from a parsed JSONL row, from this schema or an older one.

        Fields a later version dropped are set aside, so an old row still reads.
        """
        retired = RETIRED_FIELDS.get(data.get("schema_version", RESPONSE_SCHEMA_VERSION), ())
        return cls(**{key: value for key, value in data.items() if key not in retired})


class RunManifest(BaseModel):
    """What was run, with what, and how it went.

    Written beside the JSONL so a run can be understood without the config that
    produced it - and written *before* the first response, so a run that is
    killed still says what it was doing, with which settings, and how many
    responses it expected. Without that denominator, a run cut off at 40% is
    indistinguishable from a complete run over a shorter message file.
    """

    model_config = ConfigDict(extra="forbid")

    audit: str
    run_id: str
    provider: str
    model: str

    taxman_version: str
    schema_version: int

    messages_path: str
    messages_hash: str

    #: The system prompt file, relative to the project, the hash of its text, and
    #: the text itself - the file can change after the run; what was sent cannot.
    system_prompt_path: str | None = None
    system_prompt_hash: str | None = None
    system_prompt_text: str | None = None
    n_messages: int
    repeats: int

    #: The fully resolved audit config, defaults included.
    config: dict[str, Any] = Field(default_factory=dict)

    started_at: str | None = None
    finished_at: str | None = None

    #: Written as `running` before the first request goes out and rewritten when
    #: the run ends, so an interrupted run is never mistaken for a complete one.
    status: RunStatus = "running"

    n_ok: int = 0
    n_error: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunManifest:
        return cls(**data)


def new_run_id() -> str:
    """A fresh run id: a sortable UTC timestamp plus a short random suffix.

    The suffix keeps two runs started in the same second distinct.
    """
    return f"{utc_now().strftime(RUN_ID_FORMAT)}-{secrets.token_hex(3)}"


#: A run id becomes a directory name and is written into every row, so it is
#: held to what is safe in both places: letters, digits, dot, dash, underscore.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Long enough for a timestamp and a description, short enough for a path.
MAX_RUN_ID_LENGTH = 128


def validate_run_id(value: str) -> str:
    """Return `value` as a usable run id, or say why it is not one.

    The id is interpolated into `output.dir` and then resolved as a path, so an
    id containing `/` or `..` does not name a run - it moves the run somewhere
    else on disk, quietly, while every record still claims the id. It is also
    written into every JSONL row, where a space or a quote makes the data
    awkward to work with later.

    Surrounding whitespace is trimmed rather than refused: ids get copied out of
    logs and manifests, and picking up a trailing newline should not be an
    error.
    """
    cleaned = value.strip()

    if not cleaned:
        raise ConfigError(
            "The run id is empty. Give one like `--run-id pilot-2`, or leave "
            "`--run-id` off entirely and taxman will generate a timestamped one."
        )

    if len(cleaned) > MAX_RUN_ID_LENGTH:
        raise ConfigError(
            f"The run id is {len(cleaned)} characters, longer than the "
            f"{MAX_RUN_ID_LENGTH} allowed. It has to work as a directory name."
        )

    if not RUN_ID_PATTERN.match(cleaned):
        raise ConfigError(
            f"{cleaned!r} cannot be used as a run id. A run id names the "
            "directory the run is written to and is recorded in every response, "
            "so it may contain only letters, digits, dots, dashes and "
            "underscores, and must start with a letter or a digit. An id "
            "containing `/` or `..` would move the data somewhere else on disk "
            "while every record still claimed this id."
        )

    return cleaned


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(moment: datetime | None = None) -> str:
    """Format a moment the way every timestamp field in the schema is formatted."""
    return (moment or utc_now()).strftime(TIMESTAMP_FORMAT)
