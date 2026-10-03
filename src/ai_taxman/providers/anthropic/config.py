"""The `model:` block of an Anthropic audit.

Every key here is Anthropic's business. Core never reads them - it hands the block
to `AnthropicProvider.validate_model_config`, which returns one of these.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_taxman.providers.anthropic.models import (
    EFFORTS,
    Effort,
    ThinkingDisplay,
    ThinkingType,
)


class _Block(BaseModel):
    """A block of settings where a key left blank takes its default."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, block: Any) -> Any:
        """`taxman audits new` writes keys with no value; YAML reads those as None.

        Dropping them here lets the field defaults apply, so a freshly generated
        template validates as-is.
        """
        if isinstance(block, dict):
            return {key: value for key, value in block.items() if not _blank(value)}
        return block


def _blank(value: Any) -> bool:
    """None, or a nested block whose every key was left blank."""
    if isinstance(value, dict):
        return all(_blank(inner) for inner in value.values())
    return value is None


#: What `taxman audits new` writes for `max_tokens`. Room for a considered answer
#: and the thinking before it, without a runaway response running the bill up.
DEFAULT_MAX_TOKENS = 16000


#: The documented floor for an extended-thinking budget.
MIN_THINKING_BUDGET = 1024


class AnthropicThinking(_Block):
    """The `thinking:` block, sent as the `thinking` parameter when `type` is set.

    Only rules Anthropic documents for every model are checked here; which model
    takes which type is the API's call.
    """

    type: ThinkingType | None = None
    #: `type: enabled` only, the manual mode older models use.
    budget_tokens: int | None = Field(default=None, ge=MIN_THINKING_BUDGET)
    display: ThinkingDisplay | None = None

    @model_validator(mode="after")
    def _settings_fit_the_type(self) -> AnthropicThinking:
        chosen = sorted(self.model_fields_set - {"type"})
        if chosen and self.type is None:
            raise ValueError(
                f"{', '.join(chosen)} set in `thinking:`, but `type` is blank. Anthropic "
                "takes no thinking settings without a type: set one, or leave them blank."
            )
        if self.type == "enabled" and self.budget_tokens is None:
            raise ValueError("`type: enabled` needs budget_tokens, at least 1024.")
        if self.budget_tokens is not None and self.type != "enabled":
            raise ValueError(
                f"budget_tokens applies only to `type: enabled`, not {self.type!r}. "
                "Leave it blank, or use effort to steer adaptive thinking."
            )
        if self.display is not None and self.type in ("disabled", "between_tools"):
            raise ValueError(f"display has nothing to show with `type: {self.type}`.")
        return self


class AnthropicModelConfig(_Block):
    """Validated Anthropic settings for one audit."""

    #: Any model name is allowed; `known_models()` is only a convenience list.
    name: str

    #: Required: the Messages API has no default, and rejects a request without it.
    max_tokens: int = Field(ge=1)

    #: Sent as `output_config.effort`. Blank leaves the model's own default.
    effort: Effort | None = None

    #: Sent only when set. Models after Claude Opus 4.6 refuse anything but
    #: temperature 1.0 and top_p >= 0.99, and refuse top_k outright; the API says so.
    temperature: float | None = Field(default=None, ge=0, le=1)
    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1)

    #: Last, as in the template: it is a nested block.
    thinking: AnthropicThinking = Field(default_factory=AnthropicThinking)

    @model_validator(mode="after")
    def _thinking_leaves_room_to_answer(self) -> AnthropicModelConfig:
        """Thinking counts toward max_tokens, so a budget that fills it leaves no answer."""
        budget = self.thinking.budget_tokens
        if budget is not None and budget >= self.max_tokens:
            raise ValueError(
                f"thinking.budget_tokens ({budget}) must be less than max_tokens "
                f"({self.max_tokens}): thinking and the answer share it."
            )
        return self


#: Documented in the generated template, in this order, after `name` and `max_tokens`.
TEMPLATE_FIELDS: tuple[tuple[str, str], ...] = (
    (
        "effort",
        f"{' | '.join(EFFORTS)}. How much work Claude puts in, thinking included. "
        "Blank = the model's default. Not every model takes every level.",
    ),
    (
        "temperature",
        "0.0 - 1.0. Leave blank: models after Claude Opus 4.6 accept only 1.0.",
    ),
    ("top_p", "0.0 - 1.0. Leave blank: models after Claude Opus 4.6 accept only 0.99 or more."),
    ("top_k", "Sample from the top K tokens. Models after Claude Opus 4.6 reject it."),
)

#: The `thinking:` block, in this order, below a comment saying blank is the default.
THINKING_TEMPLATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("type", "adaptive | enabled | disabled | between_tools. Which a model accepts varies."),
    ("budget_tokens", "type: enabled only. At least 1024, and less than max_tokens."),
    (
        "display",
        "summarized | omitted. Whether thinking text comes back; current models "
        "default to omitted.",
    ),
)
