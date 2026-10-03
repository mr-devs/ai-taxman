"""The `model:` block of a Gemini audit.

Every key here is Google's business. Core never reads them - it hands the block
to `GeminiProvider.validate_model_config`, which returns one of these.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class GeminiModelConfig(_Block):
    """Validated Gemini settings for one audit."""

    #: Any model name is allowed; `known_models()` is only a convenience list.
    name: str

    #: Sent in `generation_config`, and only when set, so the model's own
    #: defaults apply otherwise.
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    #: Thinking counts toward it, so a small cap can leave no room for an answer.
    max_output_tokens: int | None = Field(default=None, ge=1)

    #: Whether Google keeps the interaction server-side. Off by default, and always
    #: sent: the Interactions API keeps everything for 55 days unless told not to,
    #: and an audit should not leave a trail in the account it is auditing from.
    store: bool = False


#: The `model:` keys sent, under the same names, inside `generation_config`.
GENERATION_CONFIG_KEYS: tuple[str, ...] = ("temperature", "top_p", "max_output_tokens")


#: Documented in the generated template, in this order.
TEMPLATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("temperature", "0.0 - 2.0. Leave blank for the model default."),
    ("top_p", "0.0 - 1.0. Leave blank for the model default."),
    ("max_output_tokens", "Maximum tokens per response, thinking included."),
    (
        "store",
        "true to let Google keep the interaction (55 days paid tier, 1 day free). Blank = false.",
    ),
)
