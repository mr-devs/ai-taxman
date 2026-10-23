"""The fixed sets of values Gemini's settings take."""

from __future__ import annotations

from typing import Literal

#: The documented `generation_config.thinking_level` values, ascending. Gemini 3
#: and 2.5 models both take it, but not every model takes every level - 2.5 models
#: take low, medium, and high - so taxman validates the name and lets Google rule
#: on the pairing. It is the Interactions API's only thinking control: there is no
#: `thinking_budget`.
ThinkingLevel = Literal["minimal", "low", "medium", "high"]
