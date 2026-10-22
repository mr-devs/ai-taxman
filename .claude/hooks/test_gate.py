"""Keep a turn from ending on a broken suite: Claude Code hooks for this repo.

`prompt` (UserPromptSubmit) records what `src/` and `tests/` hold when a turn
starts. `stop` (Stop) compares, and only if either changed runs the suite; a
failure blocks the turn from ending and hands Claude the output.

The record lives in the temp directory, keyed by session, never in the repo.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WATCHED = ("src", "tests")
SUITE = ["uv", "run", "pytest", "-x", "-q"]


def fingerprint(root: Path) -> str:
    """A hash of every file under the watched folders: names and contents."""
    digest = hashlib.sha256()
    for folder in WATCHED:
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(root)).encode())
                digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def record_for(session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_") or "unknown"
    return Path(tempfile.gettempdir()) / "ai-taxman-claude-hooks" / f"{safe}.fingerprint"


def save(record: Path, fingerprint: str) -> None:
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(fingerprint, encoding="utf-8")


def main() -> int:
    event = json.load(sys.stdin)
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".")
    record = record_for(str(event.get("session_id", "")))
    now = fingerprint(root)

    if sys.argv[1:] == ["prompt"]:
        save(record, now)
        return 0

    before = record.read_text(encoding="utf-8") if record.is_file() else None
    if before == now:
        return 0  # nothing under src/ or tests/ changed this turn

    result = subprocess.run(SUITE, cwd=root, capture_output=True, text=True)
    if result.returncode == 0:
        save(record, now)
        return 0

    tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-40:])
    if event.get("stop_hook_active"):
        # Already blocked once this turn: say so, but do not loop forever.
        print(json.dumps({"systemMessage": f"`uv run pytest -x -q` still fails:\n{tail}"}))
        return 0
    print(f"`uv run pytest -x -q` fails after this turn's changes:\n{tail}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
