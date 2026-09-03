"""The shape of the data an audit produces.

`ResponseRecord` is one line of the output JSONL, and it is identical for every
provider: shared envelope fields naming what was asked and how it went, plus the
provider's response verbatim in `raw`. Nothing is derived from `raw` — taxman
collects, and parsing happens downstream from the data on disk.

This schema is a promise to whoever analyses the data later. Changes are
**additive** — adding a field is fine, removing or renaming one is a break that
costs a `RESPONSE_SCHEMA_VERSION` bump and a note in the README.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Bumped when the shape changes. v1 carried `text` and `usage` alongside `raw`;
#: v2 carries `raw` alone, because taxman no longer parses a response.
RESPONSE_SCHEMA_VERSION = 2

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
    message_hash: str
    message: str
    repeat: int

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
        """Rebuild a record from a parsed JSONL row."""
        return cls(**data)


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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(moment: datetime | None = None) -> str:
    """Format a moment the way every timestamp field in the schema is formatted."""
    return (moment or utc_now()).strftime(TIMESTAMP_FORMAT)
