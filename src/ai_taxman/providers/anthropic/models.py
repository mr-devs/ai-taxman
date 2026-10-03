"""Known Claude model names.

A convenience list for `taxman audits new` and shell completion only - an unlisted
model is still accepted, because Anthropic ships models faster than this file is
updated.

Every entry is a pinned snapshot ID. For models before the 4.6 generation the
dateless name is an alias that moves, so the dated ID is listed instead: an audit
should name the exact model it ran against. See docs/provider-apis/anthropic.md.
"""

from __future__ import annotations

#: Offered by `taxman audits new anthropic` and tab completion. First entry is the
#: default, the model Anthropic's models overview says to start with.
KNOWN_MODELS: tuple[str, ...] = (
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-fable-5-1",
    "claude-haiku-4-5-20251001",
    "claude-fable-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-opus-4-5-20251101",
)

DEFAULT_MODEL = KNOWN_MODELS[0]
