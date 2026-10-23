"""The fixed sets of values OpenAI's settings take."""

from __future__ import annotations

from typing import Literal

#: The levels the Responses API documents for `reasoning.effort`, ascending. Not
#: every reasoning model accepts every one, and non-reasoning models accept
#: none of them - the API is the only thing that can say which, so taxman
#: validates the name and lets OpenAI rule on the pairing.
#: See docs/provider-apis/openai.md for the reference this tracks.
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
