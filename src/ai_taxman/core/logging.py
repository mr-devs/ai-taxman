"""What a run says about itself while it is running.

A collection can take hours, and until it finishes the only thing a user has to
go on is that the process has not exited. A background run does not even have a
terminal. So the runner narrates: what it is about to do, every response as it
lands, every retry, and how it ended.

Two rules shape this module.

**The library never configures logging for its caller.** Core modules only ever
ask for a logger and emit; handlers are attached here, and only when a command
asks for them. A `NullHandler` on the package logger keeps the Python API silent
by default, the way a library should be.

**The API key is never logged.** Not at debug, not in a request dump, not in an
error. Nothing in this package writes it, and a test asserts as much - a log is
a file users pipe into issues and share with colleagues.

Log records go to stderr, leaving stdout to the command's own summary, so
`taxman collect probe > summary.txt` separates the two and
`taxman collect probe > run.log 2>&1` keeps both.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

#: The package logger. Every logger in taxman is a child of this one.
LOGGER_NAME = "ai_taxman"

#: Level names `--log-level` accepts, quietest last in the CLI's help.
LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

DEFAULT_LEVEL = "info"

#: Timestamps match the record schema's format, so a log line and a JSONL row
#: can be lined up against each other.
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
LINE_FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)-7s %(name)s  %(message)s"


def describe_level(name: str) -> int:
    """Turn a `--log-level` name into a logging level, or say what was allowed."""
    try:
        return LEVELS[name.strip().lower()]
    except KeyError:
        allowed = " | ".join(LEVELS)
        raise ValueError(f"Unknown log level {name!r}. Choose one of: {allowed}.") from None


def setup_logging(level: str = DEFAULT_LEVEL, log_file: str | Path | None = None) -> None:
    """Send taxman's log to the terminal, or to `log_file` instead.

    Safe to call twice: the handlers this attached last time are replaced rather
    than added to, so a line is never printed twice.
    """
    resolved = describe_level(level)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(resolved)
    # A caller's root logger configuration is theirs; taxman does not post into it.
    logger.propagate = False

    for existing in logger.handlers[:]:
        if isinstance(existing, logging.NullHandler):
            continue
        logger.removeHandler(existing)
        existing.close()

    handler: logging.Handler
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Append, so a resumed or restarted run adds to the log rather than
        # erasing the evidence of the run before it.
        handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)

    handler.setLevel(resolved)
    handler.setFormatter(_Formatter(LINE_FORMAT, datefmt=TIME_FORMAT))
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """A logger under the package's own name, whatever module asks for it."""
    return logging.getLogger(name)


class _Formatter(logging.Formatter):
    """UTC timestamps, so a log written on a machine in another timezone still
    lines up with the `requested_at` in the records beside it."""

    converter = staticmethod(time.gmtime)
