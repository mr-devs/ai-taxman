"""The `model:` block of an OpenAI audit.

Every key here is OpenAI's business. Core never reads them — it hands the block
to `OpenAIProvider.validate_model_config`, which returns one of these.

Keys are named after the audit parameters a researcher thinks in
(`reasoning_effort`, `web_search`) rather than the exact API payload shape;
`build_request` does the translation.

Web search has its own `search:` block inside `model:`, because the web-search
tool carries its own settings and none of them mean anything without it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_taxman.providers.openai.models import REASONING_EFFORTS, ReasoningEffort


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
            return {key: value for key, value in block.items() if value is not None}
        return block


class OpenAISearchConfig(_Block):
    """The `search:` block: the web-search tool and its settings."""

    #: Give the model the web-search tool.
    web_search: bool = False


class OpenAIModelConfig(_Block):
    """Validated OpenAI settings for one audit."""

    #: Any model name is allowed; `known_models()` is only a convenience list.
    name: str

    #: Sent only when set, so the API's own default applies otherwise.
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_output_tokens: int | None = Field(default=None, ge=1)

    #: Reasoning models only. Other models reject it.
    reasoning_effort: ReasoningEffort | None = None

    #: Whether OpenAI retains the response server-side. Off by default: an audit
    #: should not leave a trail in the account it is auditing from.
    store: bool = False

    #: Escape hatch for API parameters this config does not name yet. Merged
    #: into the request as-is.
    extra: dict[str, Any] = Field(default_factory=dict)

    #: Last, as it is in the template: it is the one nested block.
    search: OpenAISearchConfig = Field(default_factory=OpenAISearchConfig)


#: Documented in the generated template, in this order.
TEMPLATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("temperature", "0.0 - 2.0. Leave blank for the model default."),
    ("top_p", "0.0 - 1.0. Leave blank for the model default."),
    ("max_output_tokens", "Maximum tokens per response."),
    ("reasoning_effort", f"Reasoning models only: {' | '.join(REASONING_EFFORTS)}."),
    ("store", "true to let OpenAI retain the response server-side."),
)

#: The `search:` block, in this order, below a comment saying it needs web_search.
SEARCH_TEMPLATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("web_search", "true to give the model the web-search tool."),
)
