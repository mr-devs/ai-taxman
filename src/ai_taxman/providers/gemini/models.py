"""Known Gemini model names.

A convenience list for `taxman audits new` and shell completion only - an unlisted
model is still accepted, because Google ships models faster than this file is
updated.

Only models the Interactions API serves are listed. See
docs/provider-apis/gemini.md.
"""

from __future__ import annotations

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
