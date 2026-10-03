"""Known Gemini model names.

A convenience list for `taxman audits new` and shell completion only - an unlisted
model is still accepted, because Google ships models faster than this file is
updated.

Only models the Interactions API serves are listed. See
docs/provider-apis/gemini.md.
"""

from __future__ import annotations

from typing import Literal, get_args

#: Offered by `taxman audits new gemini` and tab completion. First entry is the default.
KNOWN_MODELS: tuple[str, ...] = (
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)

DEFAULT_MODEL = KNOWN_MODELS[0]

#: The documented `generation_config.thinking_level` values, ascending. Gemini 3
#: and 2.5 models both take it, but not every model takes every level - 2.5 models
#: take low, medium, and high - so taxman validates the name and lets Google rule
#: on the pairing. It is the Interactions API's only thinking control: there is no
#: `thinking_budget`.
ThinkingLevel = Literal["minimal", "low", "medium", "high"]

#: The same levels as a tuple, for templates and completion.
THINKING_LEVELS: tuple[str, ...] = get_args(ThinkingLevel)
