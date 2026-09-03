"""Writing responses to JSONL, optionally gzipped.

Rows are flushed as they are written, so a run that is interrupted still leaves
usable data behind. The file is opened in append mode for the same reason: a
resumed run adds to what is already there rather than replacing it.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import IO

from ai_taxman.core.records import ResponseRecord

GZIP_SUFFIX = ".gz"


class JsonlWriter:
    """Append `ResponseRecord`s to a JSONL file, as a context manager."""

    def __init__(self, path: str | Path, *, compress: bool = False) -> None:
        self.compress = compress
        self.path = _target_path(Path(path), compress=compress)
        self.count = 0
        self._handle: IO[str] | None = None

    def __enter__(self) -> JsonlWriter:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # noqa SIM115: this class *is* the context manager; close() owns the handle.
        if self.compress:
            self._handle = gzip.open(self.path, "at", encoding="utf-8")  # noqa: SIM115
        else:
            self._handle = self.path.open("a", encoding="utf-8")  # noqa: SIM115

    def write(self, record: ResponseRecord) -> None:
        """Append one record and flush it to disk."""
        if self._handle is None:
            raise RuntimeError("JsonlWriter is not open; use it as a context manager.")
        self._handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        self._handle.flush()
        self.count += 1

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def read_jsonl(path: str | Path) -> Iterator[ResponseRecord]:
    """Yield the records in a JSONL file, gzipped or not.

    A missing file yields nothing, and a truncated final line — the signature of
    an interrupted run — is skipped rather than raising.
    """
    path = Path(path)
    if not path.is_file():
        return

    opener = gzip.open if path.suffix == GZIP_SUFFIX else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield ResponseRecord.from_dict(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                # A half-written final line from a killed run; everything before it stands.
                continue


def _target_path(path: Path, *, compress: bool) -> Path:
    if compress and path.suffix != GZIP_SUFFIX:
        return path.with_suffix(path.suffix + GZIP_SUFFIX)
    return path
