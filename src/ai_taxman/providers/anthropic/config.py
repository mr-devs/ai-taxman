"""The `model:` block of an Anthropic audit.

Every key here is Anthropic's business. Core never reads them - it hands the block
to `AnthropicProvider.validate_model_config`, which returns one of these.
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


#: What `taxman audits new` writes for `max_tokens`. Room for a considered answer
#: and the thinking before it, without a runaway response running the bill up.
DEFAULT_MAX_TOKENS = 16000


class AnthropicModelConfig(_Block):
    """Validated Anthropic settings for one audit."""

    #: Any model name is allowed; `known_models()` is only a convenience list.
    name: str

    #: Required: the Messages API has no default, and rejects a request without it.
    max_tokens: int = Field(ge=1)


#: Documented in the generated template, in this order, after `name` and `max_tokens`.
TEMPLATE_FIELDS: tuple[tuple[str, str], ...] = ()
