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

Log records go to one file only: the run's own, in the audit's
`output.log_dir`. The terminal is the command's - a progress bar on stderr and
the summary on stdout - and a log line printed between them would only break
the bar.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path

#: The package logger. Every logger in taxman is a child of this one.
LOGGER_NAME = "ai_taxman"

LINE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s  %(message)s"

#: How many records are held while the log file is not yet known. A collect
#: logs a line or two before its run is claimed, so this is never reached.
_HELD = 1000


def setup_logging(log_file: str | Path | None = None) -> None:
    """Send taxman's log to `log_file`, or hold it until there is one.

    A run's log is named after the run, which `collect` learns only once the
    run is claimed, and a warning from before then - a lock that cannot be
    taken - belongs in that log too. So, called without a file, this holds what
    is logged; called with one, it writes what it held, then everything after.

    Safe to call twice: the handlers this attached last time are replaced rather
    than added to, so a line is never written twice.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    # A caller's root logger configuration is theirs; taxman does not post into it.
    logger.propagate = False

    handler: logging.Handler
    if log_file is None:
        handler = logging.handlers.MemoryHandler(_HELD, flushLevel=logging.CRITICAL + 1)
    else:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Append, so a resumed or restarted run adds to the log rather than
        # erasing the evidence of the run before it. Delayed, so a run that fails
        # before its first line leaves no empty log claiming it existed.
        handler = logging.FileHandler(path, mode="a", encoding="utf-8", delay=True)
        handler.setFormatter(_Formatter(LINE_FORMAT))

    for existing in logger.handlers[:]:
        if isinstance(existing, logging.NullHandler):
            continue
        if isinstance(existing, logging.handlers.MemoryHandler) and log_file is not None:
            existing.setTarget(handler)
            existing.flush()
        logger.removeHandler(existing)
        existing.close()

    logger.addHandler(handler)


def release_held() -> None:
    """Write any warning still held to stderr, and stop holding.

    For a collect that ended before it claimed a run - the audit complete
    already, or the run refused - and so never had a log to put them in. A
    warning from then - a lock that could not be taken - must still reach the
    user; what was only news for the log is dropped with it.
    """
    logger = logging.getLogger(LOGGER_NAME)
    for held in logger.handlers[:]:
        if not isinstance(held, logging.handlers.MemoryHandler):
            continue
        terminal = logging.StreamHandler(sys.stderr)
        terminal.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        # A level on the handler is not enough: a flush hands records straight to it.
        terminal.addFilter(lambda record: record.levelno >= logging.WARNING)
        held.setTarget(terminal)
        logger.removeHandler(held)
        held.close()


def get_logger(name: str) -> logging.Logger:
    """A logger under the package's own name, whatever module asks for it."""
    return logging.getLogger(name)


class _Formatter(logging.Formatter):
    """Times written the way the records write theirs - the user's local time,
    with its offset - to the millisecond, so a log line and a JSONL row line up."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        moment = datetime.fromtimestamp(record.created).astimezone()
        return moment.isoformat(timespec="milliseconds")
