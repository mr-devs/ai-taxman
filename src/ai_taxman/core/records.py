"""The shape of the data an audit produces.

`ResponseRecord` is one line of the output JSONL, and it is identical for every
provider: shared envelope fields, the provider's response verbatim in `raw`, and
two convenience fields (`text`, `usage`) that the provider's `extract()` pulls
out of `raw`.

This schema is a promise to whoever analyses the data later. Changes must be
**additive** — adding a field is fine, removing or renaming one is not.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Bumped only when fields are added. See the additive-only rule above.
RESPONSE_SCHEMA_VERSION = 1

Status = Literal["ok", "error"]

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

    #: Extracted by the provider for convenience; `raw` remains the source of truth.
    text: str | None = None
    usage: dict[str, Any] | None = None

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
    produced it.
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
