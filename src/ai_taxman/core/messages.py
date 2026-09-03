"""Reading the user's `.txt` file of messages.

The file format is deliberately the simplest thing that works: one message per
line. Blank lines and lines starting with `#` are ignored, so users can annotate
and group their messages. A line whose first non-whitespace characters are `\\#`
is an escaped literal `#`, not a comment.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ai_taxman.core.errors import MessageFileError

#: Ids are zero padded to at least this width so they sort lexicographically.
MIN_ID_WIDTH = 4

COMMENT_PREFIX = "#"
ESCAPED_COMMENT_PREFIX = "\\#"


@dataclass(frozen=True, slots=True)
class Message:
    """One message to send to a provider.

    `id` is stable for a given file: it follows the order messages appear, and
    is unaffected by the blank lines and comments between them.
    """

    id: str
    text: str
    hash: str
    line_number: int


def read_messages(path: str | Path) -> list[Message]:
    """Read `path` and return its messages in file order.

    Raises `MessageFileError` if the file is missing, unreadable, or contains no
    messages once blanks and comments are removed.
    """
    path = Path(path)

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise MessageFileError(
            f"No message file at {path}. "
            "Point the audit's `messages:` key at a .txt file with one message per line."
        ) from exc
    except OSError as exc:
        raise MessageFileError(f"Could not read the message file at {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise MessageFileError(
            f"The message file at {path} is not valid UTF-8. Re-save it as UTF-8 text."
        ) from exc

    kept = [
        (line_number, _unescape(stripped))
        for line_number, line in enumerate(raw.splitlines(), start=1)
        if (stripped := line.strip()) and not stripped.startswith(COMMENT_PREFIX)
    ]

    if not kept:
        raise MessageFileError(
            f"The message file at {path} contains no messages "
            "(every line is blank or a `#` comment)."
        )

    width = max(MIN_ID_WIDTH, len(str(len(kept) - 1)))
    return [
        Message(
            id=f"m{index:0{width}d}",
            text=text,
            hash=hash_message(text),
            line_number=line_number,
        )
        for index, (line_number, text) in enumerate(kept)
    ]


def hash_message(text: str) -> str:
    """Return the stable content hash recorded alongside every response."""
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _unescape(line: str) -> str:
    if line.startswith(ESCAPED_COMMENT_PREFIX):
        return line[1:]
    return line
