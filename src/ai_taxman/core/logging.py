"""What a run says about itself while it is running.

A collection can take hours, and until it finishes the only thing a user has to
go on is that the process has not exited, and a run left going under `nohup` or
a job scheduler does not even have a terminal. So the runner narrates: what it
is about to do, every response as it lands, every retry, and how it ended.

Two rules shape this module.

**The library never configures logging for its caller.** Core modules only ever
ask for a logger and emit; handlers are attached here, and only when a command
asks for them. A `NullHandler` on the package logger keeps the Python API silent
by default, the way a library should be.

**The API key is never logged.** Not at debug, not in a request dump, not in an
error. Nothing in this package writes it, and a test asserts as much - a log is
a file users pipe into issues and share with colleagues.

Log records go to a file, to stderr, or to both - never to stdout, which is the
command's own summary. `taxman collect` keeps every run's log in the audit's
`output.log_dir` and echoes it to the terminal, so `taxman collect probe >
summary.txt` still separates the two.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

#: The package logger. Every logger in taxman is a child of this one.
LOGGER_NAME = "ai_taxman"

LINE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s  %(message)s"


def setup_logging(
    log_file: str | Path | None = None,
    *,
    also_terminal: bool = False,
) -> None:
    """Send taxman's log to the terminal, or to `log_file` instead.

    `also_terminal` keeps the terminal as well as the file, for a run with a
    person watching it.

    Safe to call twice: the handlers this attached last time are replaced rather
    than added to, so a line is never printed twice.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    # A caller's root logger configuration is theirs; taxman does not post into it.
    logger.propagate = False

    for existing in logger.handlers[:]:
        if isinstance(existing, logging.NullHandler):
            continue
        logger.removeHandler(existing)
        existing.close()

    handlers: list[logging.Handler] = []
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Append, so a resumed or restarted run adds to the log rather than
        # erasing the evidence of the run before it. Delayed, so a run that fails
        # before its first line leaves no empty log claiming it existed.
        handlers.append(logging.FileHandler(path, mode="a", encoding="utf-8", delay=True))
    if log_file is None or also_terminal:
        handlers.append(logging.StreamHandler(sys.stderr))

    for handler in handlers:
        handler.setFormatter(_Formatter(LINE_FORMAT))
        logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """A logger under the package's own name, whatever module asks for it."""
    return logging.getLogger(name)


class _Formatter(logging.Formatter):
    """Times written the way the records write theirs - the user's local time,
    with its offset - to the millisecond, so a log line and a JSONL row line up."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        moment = datetime.fromtimestamp(record.created).astimezone()
        return moment.isoformat(timespec="milliseconds")
