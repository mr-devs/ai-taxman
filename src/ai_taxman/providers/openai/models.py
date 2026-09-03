"""Known OpenAI model names and reasoning levels.

A convenience list for `taxman init` and shell completion only — an unlisted
model is still accepted, because OpenAI ships models faster than this file is
updated.
"""

from __future__ import annotations

from typing import Literal, get_args

#: Offered by `taxman init openai` and tab completion. First entry is the default.
KNOWN_MODELS: tuple[str, ...] = (
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "o4-mini",
    "o3",
)

DEFAULT_MODEL = KNOWN_MODELS[0]

#: The levels the Responses API documents for `reasoning.effort`, ascending. Not
#: every reasoning model accepts every one, and non-reasoning models accept
#: none of them - the API is the only thing that can say which, so taxman
#: validates the name and lets OpenAI rule on the pairing.
#: See docs/provider-apis/openai.md for the reference this tracks.
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]

#: The same levels as a tuple, for prompts, templates, and completion. Derived
#: so adding a level means editing `ReasoningEffort` and nothing else.
REASONING_EFFORTS: tuple[str, ...] = get_args(ReasoningEffort)
