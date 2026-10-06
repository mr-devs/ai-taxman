"""Reading the system prompt an audit names.

A system prompt is a file, kept in the project's prompts folder and named by the
audit's top-level `system_prompt:` key. Core reads it, once per run, and hands
the text to the provider on every request; each provider decides how its API
takes it (OpenAI's `instructions`, Anthropic's `system`, and so on).

It is core's, not the provider's, because every provider has one and because a
file has to be resolved against the project root, which a provider never sees.

Surrounding whitespace is dropped: the newline an editor adds at the end of a
file was never meant to be sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_taxman.core.errors import ConfigError


@dataclass(frozen=True, slots=True)
class SystemPrompt:
    """The system prompt sent with every message in a run."""

    text: str
    path: Path


def read_system_prompt(path: str | Path) -> SystemPrompt:
    """Read the system prompt at `path`, or say why it cannot be sent."""
    path = Path(path)

    try:
        # utf-8-sig drops the byte-order mark some Windows editors write first.
        text = path.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError as exc:
        raise ConfigError(
            f"No system prompt file at {path}. Write it, or point the audit's "
            "`system_prompt:` at an existing file (or leave it blank to send none)."
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"The system prompt at {path} is not valid UTF-8. Re-save it as UTF-8 text."
        ) from exc
    except OSError as exc:
        raise ConfigError(f"Could not read the system prompt at {path}: {exc}") from exc

    if not text:
        raise ConfigError(
            f"The system prompt at {path} is empty. Write the prompt into it, or leave "
            "the audit's `system_prompt:` blank to send none."
        )

    return SystemPrompt(text=text, path=path)
