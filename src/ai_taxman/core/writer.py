"""Writing responses to JSONL, optionally gzipped.

Rows are flushed as they are written, so a run that is interrupted still leaves
usable data behind. The file is opened in append mode for the same reason: a
resumed run adds to what is already there rather than replacing it.
"""

from __future__ import annotations

import gzip
import json
import zlib
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import IO

from pydantic import ValidationError

from ai_taxman.core.errors import ResponseFileError
from ai_taxman.core.records import ResponseRecord

GZIP_SUFFIX = ".gz"


class JsonlWriter:
    """Append `ResponseRecord`s to a JSONL file, as a context manager."""

    def __init__(self, path: str | Path, *, compress: bool = False) -> None:
        self.compress = compress
        self.path = target_path(Path(path), compress=compress)
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

    A missing file yields nothing, and a truncated tail — the signature of an
    interrupted run, whether that is a half-written last line or a gzip stream
    with no end-of-stream marker — ends the iteration rather than raising.

    Anything else that cannot be read raises `ResponseFileError`: a broken line
    with rows after it, or a whole JSON object that is not a record. Dropping
    those quietly would hand back fewer responses than were collected.
    """
    path = Path(path)
    if not path.is_file():
        return

    opener = gzip.open if path.suffix == GZIP_SUFFIX else open
    with opener(path, "rt", encoding="utf-8") as handle:
        cut_short: int | None = None
        for number, line in enumerate(_lines(handle), start=1):
            line = line.strip()
            if not line:
                continue
            if cut_short is not None:
                raise ResponseFileError(
                    f"Line {cut_short} of {path} is not complete JSON, and more rows "
                    "follow it. Only the last line can be cut short, by a run that was "
                    "killed while writing it."
                )
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                cut_short = number
                continue
            try:
                yield ResponseRecord.from_dict(data)
            except ValidationError as exc:
                fields = ", ".join(sorted({str(error["loc"][0]) for error in exc.errors()}))
                raise ResponseFileError(
                    f"Line {number} of {path} is not a response record. "
                    f"These fields are missing or wrong: {fields}."
                ) from exc


def _lines(handle: IO[str]) -> Iterator[str]:
    """Yield lines, stopping where a killed run stopped writing.

    A gzip stream from an interrupted run has no end-of-stream marker, and the
    decompressor raises rather than handing back the data it already has. Every
    line before that point is intact, and is what the user paid for - so the
    error ends the iteration instead of the read.
    """
    while True:
        try:
            line = handle.readline()
        except (EOFError, OSError, zlib.error):
            return
        if not line:
            return
        yield line


def target_path(path: Path, *, compress: bool) -> Path:
    """The file a writer for `path` writes to: `path`, plus `.gz` when compressing."""
    if compress and path.suffix != GZIP_SUFFIX:
        return path.with_suffix(path.suffix + GZIP_SUFFIX)
    return path
