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

from typing import Any, Literal

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
            return {key: value for key, value in block.items() if not _blank(value)}
        return block


def _blank(value: Any) -> bool:
    """None, or a nested block whose every key was left blank."""
    if isinstance(value, dict):
        return all(_blank(inner) for inner in value.values())
    return value is None


class OpenAISearchConfig(_Block):
    """The `search:` block: the web-search tool and its settings.

    Names are OpenAI's own, so each one can be looked up in the web-search guide
    as written. Every setting but `web_search` is sent only when set.
    """

    #: Give the model the web-search tool.
    web_search: bool = False

    search_context_size: Literal["low", "medium", "high"] | None = None
    external_web_access: bool | None = None
    #: GPT-5+ reasoning web search only.
    return_token_budget: Literal["default", "unlimited"] | None = None

    @model_validator(mode="after")
    def _settings_need_web_search(self) -> OpenAISearchConfig:
        """A setting for a tool that is not sent would be recorded but never used."""
        chosen = sorted(self.model_fields_set - {"web_search"})
        if chosen and not self.web_search:
            raise ValueError(
                f"{', '.join(chosen)} set in `search:`, but `web_search` is not true. "
                "Set `web_search: true`, or leave the other search settings blank."
            )
        return self


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
    (
        "search_context_size",
        "low | medium | high. How much search-result text the model sees. "
        "Blank = OpenAI's default.",
    ),
    ("external_web_access", "false to use only cached/indexed results. Blank = live."),
    (
        "return_token_budget",
        "default | unlimited. GPT-5+ reasoning models only. Use unlimited only for "
        "high-effort research or evaluation runs.",
    ),
)
