"""Known Claude model names.

A convenience list for `taxman audits new` and shell completion only - an unlisted
model is still accepted, because Anthropic ships models faster than this file is
updated.

Every entry is a pinned snapshot ID. For models before the 4.6 generation the
dateless name is an alias that moves, so the dated ID is listed instead: an audit
should name the exact model it ran against. See docs/provider-apis/anthropic.md.
"""

from __future__ import annotations

from typing import Literal, get_args

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

#: The levels the Messages API documents for `output_config.effort`, ascending. Not
#: every model accepts every one - `xhigh` and `max` are newer, and Haiku 4.5 takes
#: none - so taxman validates the name and lets Anthropic rule on the pairing.
Effort = Literal["low", "medium", "high", "xhigh", "max"]

#: The same levels as a tuple, for templates and completion.
EFFORTS: tuple[str, ...] = get_args(Effort)

#: The documented `thinking.type` values. Which a model accepts varies - current
#: models reject `enabled`, older ones reject `adaptive`, only Sonnet 5.5 takes
#: `between_tools` - so, as with effort, the API rules on the pairing.
ThinkingType = Literal["adaptive", "enabled", "disabled", "between_tools"]

#: Whether thinking text comes back. `omitted` returns thinking blocks with an
#: empty `thinking` field; it is the default on current models. `updates` (beta)
#: does the same, but returns the progress notes some models write between tool
#: calls as text. It needs `DISPLAY_UPDATES_BETA`, or Anthropic rejects it.
ThinkingDisplay = Literal["summarized", "omitted", "updates"]

#: The `anthropic-beta` header `display: updates` needs.
DISPLAY_UPDATES_BETA = "thinking-display-updates-2026-08-18"

#: The web search tool versions, oldest first. `_20260209` added dynamic
#: filtering (search run from code execution); `_20260318` added
#: `response_inclusion`. See docs/provider-apis/anthropic.md.
WebSearchVersion = Literal["web_search_20250305", "web_search_20260209", "web_search_20260318"]

#: The version sent when the audit names none: the newest.
DEFAULT_WEB_SEARCH_VERSION: WebSearchVersion = "web_search_20260318"

#: Who may run a web search: Claude directly, or code Claude runs (dynamic
#: filtering). From `_20260209` the default is code execution only.
WebSearchCaller = Literal[
    "direct", "code_execution_20250825", "code_execution_20260120", "code_execution_20260521"
]
