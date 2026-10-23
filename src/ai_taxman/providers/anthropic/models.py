"""The fixed sets of values Anthropic's settings take, and the versions it sends."""

from __future__ import annotations

from typing import Literal

#: The levels the Messages API documents for `output_config.effort`, ascending. Not
#: every model accepts every one - `xhigh` and `max` are newer, and Haiku 4.5 takes
#: none - so taxman validates the name and lets Anthropic rule on the pairing.
Effort = Literal["low", "medium", "high", "xhigh", "max"]

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
