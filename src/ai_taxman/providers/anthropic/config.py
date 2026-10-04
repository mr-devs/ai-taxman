"""The `model:` block of an Anthropic audit.

Every key here is Anthropic's business. Core never reads them - it hands the block
to `AnthropicProvider.validate_model_config`, which returns one of these.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ai_taxman.providers.anthropic.models import (
    DEFAULT_WEB_SEARCH_VERSION,
    Effort,
    ThinkingDisplay,
    ThinkingType,
    WebSearchCaller,
    WebSearchVersion,
)
from ai_taxman.providers.settings import SettingsBlock, check_extra

#: Request keys `build_request` sets itself, as dotted paths. `extra:` may add
#: anything else - a key inside one of these objects included, so `output_config`
#: can carry a `format` beside the audit's effort - but never one of these.
SET_BY_TAXMAN = frozenset(
    {
        "model",
        "max_tokens",
        "messages",
        "system",
        "temperature",
        "top_p",
        "top_k",
        "output_config.effort",
        "thinking",
        "tools",
        "tool_choice",
    }
)

#: Request keys taxman never sends, because `send` could not record the result.
NEVER_SENT = {"stream": "a stream is not a response, so there would be nothing whole to record"}


#: What `taxman audits new` writes for `max_tokens`. Room for a considered answer
#: and the thinking before it, without a runaway response running the bill up.
DEFAULT_MAX_TOKENS = 16000


#: The documented floor for an extended-thinking budget.
MIN_THINKING_BUDGET = 1024

#: Anthropic's pages, linked from the template. Each is listed, as its markdown
#: twin, in docs/provider-apis/anthropic.md.
MODELS_DOCS = "https://platform.claude.com/docs/en/models/overview"
CREATE_DOCS = "https://platform.claude.com/docs/en/api/messages/create"
EFFORT_DOCS = "https://platform.claude.com/docs/en/build-with-claude/effort"
THINKING_DOCS = "https://platform.claude.com/docs/en/build-with-claude/thinking"
WEB_SEARCH_DOCS = "https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool"

#: What a blank sampling setting does.
MODEL_DEFAULT = "model default"


class AnthropicThinking(SettingsBlock):
    """The `thinking:` block, sent as the `thinking` parameter when `type` is set.

    Only rules Anthropic documents for every model are checked here; which model
    takes which type is the API's call.
    """

    type: ThinkingType | None = Field(
        default=None,
        description="How Claude thinks before answering. Modes vary by model.",
        json_schema_extra={
            "options": {
                "adaptive": "Claude decides when and how much to think",
                "enabled": "think up to budget_tokens; older models",
                "disabled": "no thinking, where allowed",
                "between_tools": "no up-front thinking; Claude Sonnet 5.5 at effort high or below",
            },
            "blank": MODEL_DEFAULT,
        },
    )
    #: `type: enabled` only, the manual mode older models use.
    budget_tokens: int | None = Field(
        default=None,
        ge=MIN_THINKING_BUDGET,
        description="Max thinking tokens, for type: enabled only. Must be less than max_tokens.",
        json_schema_extra={"blank": "not sent"},
    )
    display: ThinkingDisplay | None = Field(
        default=None,
        description="Whether thinking text is returned. Not with type: disabled or between_tools.",
        json_schema_extra={
            "options": {
                "summarized": "a summary of the thinking",
                "omitted": "empty thinking blocks",
                "updates": "beta: empty thinking, plus notes between tool calls",
            },
            "blank": MODEL_DEFAULT,
        },
    )

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


class AnthropicUserLocation(SettingsBlock):
    """An approximate location to localise search results. Sent with `type: approximate`."""

    city: str | None = Field(
        default=None,
        description="City name.",
        examples=["Minneapolis"],
        json_schema_extra={"blank": "not sent"},
    )
    region: str | None = Field(
        default=None,
        description="Region name.",
        examples=["Minnesota"],
        json_schema_extra={"blank": "not sent"},
    )
    #: ISO 3166-1: `US`, not `us` or `USA`.
    country: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{2}$",
        description="Two-letter country code.",
        examples=["US"],
        json_schema_extra={"blank": "not sent"},
    )
    timezone: str | None = Field(
        default=None,
        description="IANA time zone.",
        examples=["America/Chicago"],
        json_schema_extra={"blank": "not sent"},
    )


class AnthropicSearchConfig(SettingsBlock):
    """The `search:` block: Claude's web search tool and its settings.

    Names are Anthropic's own, so each one can be looked up in the web search
    tool guide as written. Every setting but `web_search` is sent only when set.
    """

    web_search: bool = Field(default=False, description="Give Claude the web search tool.")

    #: Blank sends the newest.
    tool_version: WebSearchVersion | None = Field(
        default=None,
        description="Web search tool version.",
        json_schema_extra={
            "options": {
                "web_search_20250305": "basic search",
                "web_search_20260209": "adds dynamic filtering",
                "web_search_20260318": "adds response_inclusion",
            },
            "blank": f"{DEFAULT_WEB_SEARCH_VERSION}, the newest",
        },
    )

    max_uses: int | None = Field(
        default=None,
        ge=1,
        description="Max searches per message.",
        json_schema_extra={"blank": "no limit"},
    )

    #: One or the other, never both. Bare domains, optionally with a path:
    #: `example.com/blog`. Subdomains are included.
    allowed_domains: list[str] | None = Field(
        default=None,
        description="Only search these domains, subdomains included. Not with blocked_domains.",
        examples=[["cdc.gov", "who.int"]],
        json_schema_extra={"blank": "any domain"},
    )
    blocked_domains: list[str] | None = Field(
        default=None,
        description="Never search these domains. Not with allowed_domains.",
        examples=[["example.com"]],
        json_schema_extra={"blank": "none"},
    )

    user_location: AnthropicUserLocation | None = Field(
        default=None,
        description="Approximate location to localise results.",
    )

    #: Models without programmatic tool calling need `[direct]` on `_20260209` and
    #: later; Anthropic says so with a 400.
    allowed_callers: list[WebSearchCaller] | None = Field(
        default=None,
        min_length=1,
        description="Who may run searches. "
        "[direct] turns off dynamic filtering; models without programmatic tool calling need it.",
        examples=[["direct"]],
        json_schema_extra={"blank": "the tool version's default"},
    )

    response_inclusion: Literal["full", "excluded"] | None = Field(
        default=None,
        description="Whether results code execution consumed come back. web_search_20260318 only.",
        json_schema_extra={
            "options": {
                "full": "return them",
                "excluded": "drop them",
            },
            "blank": "full",
        },
    )

    #: Request-level, not on the tool.
    tool_choice: Literal["auto", "any"] | None = Field(
        default=None,
        description="Whether Claude must search.",
        json_schema_extra={
            "options": {
                "auto": "Claude decides",
                "any": "Claude must use a tool first; current models reject it",
            },
            "blank": "auto",
        },
    )

    @field_validator("allowed_domains", "blocked_domains")
    @classmethod
    def _bare_domains(cls, domains: list[str] | None) -> list[str] | None:
        """Anthropic wants `cdc.gov`, not `https://cdc.gov/`. Refused, not rewritten."""
        for domain in domains or []:
            if domain.lower().startswith(("http://", "https://")):
                raise ValueError(
                    f"{domain!r}: write the domain without the http:// or https:// prefix, "
                    "e.g. cdc.gov. Subdomains are included."
                )
        return domains

    @model_validator(mode="after")
    def _settings_need_web_search(self) -> AnthropicSearchConfig:
        """A setting for a tool that is not sent would be recorded but never used."""
        chosen = sorted(self.model_fields_set - {"web_search"})
        if chosen and not self.web_search:
            raise ValueError(
                f"{', '.join(chosen)} set in `search:`, but `web_search` is not true. "
                "Set `web_search: true`, or leave the other search settings blank."
            )
        if self.response_inclusion is not None and self.tool_version not in (
            None,
            "web_search_20260318",
        ):
            raise ValueError(
                f"response_inclusion needs web_search_20260318, not {self.tool_version}. "
                "Leave tool_version blank, or leave response_inclusion blank."
            )
        if self.allowed_domains is not None and self.blocked_domains is not None:
            raise ValueError(
                "allowed_domains and blocked_domains are both set in `search:`; Anthropic "
                "takes one or the other. Keep one and leave the other blank."
            )
        return self


class AnthropicModelConfig(SettingsBlock):
    """Validated Anthropic settings for one audit."""

    #: Any model name is allowed; `known_models()` is only a convenience list.
    name: str = Field(
        description="The model to send every message to.",
        json_schema_extra={"docs": MODELS_DOCS},
    )

    #: Required: the Messages API has no default, and rejects a request without it.
    max_tokens: int = Field(
        ge=1,
        description="Max tokens per response, thinking included. "
        "A response that hits it is cut off.",
        json_schema_extra={"docs": CREATE_DOCS},
    )

    #: Sent as `output_config.effort`. Blank leaves the model's own default.
    effort: Effort | None = Field(
        default=None,
        description="How much effort Claude puts into a response, thinking included. "
        "Levels vary by model.",
        json_schema_extra={
            "options": {
                "low": "fastest and cheapest",
                "medium": "balanced",
                "high": "thorough",
                "xhigh": "for long-horizon work",
                "max": "no limit on token spending",
            },
            "blank": MODEL_DEFAULT,
            "docs": EFFORT_DOCS,
        },
    )

    #: Sent only when set. Models after Claude Opus 4.6 refuse anything but
    #: temperature 1.0 and top_p >= 0.99, and refuse top_k outright; the API says so.
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Sampling randomness. Models after Claude Opus 4.6 accept only 1.0.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )
    top_p: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Nucleus sampling threshold. "
        "Models after Claude Opus 4.6 accept only 0.99 or more.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        description="Sample from only the top K tokens. Models after Claude Opus 4.6 reject it.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )

    #: Escape hatch for API parameters this config does not name yet. Merged into
    #: the request as written - but never over one it does name.
    extra: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"template": False})

    #: Last, as in the template: the nested blocks.
    thinking: AnthropicThinking = Field(
        default_factory=AnthropicThinking,
        description="Thinking: leave every key blank for the model's default.",
        json_schema_extra={"docs": THINKING_DOCS},
    )
    search: AnthropicSearchConfig = Field(
        default_factory=AnthropicSearchConfig,
        description="Web search: web_search must be true to use any other setting in this block.",
        json_schema_extra={"docs": WEB_SEARCH_DOCS},
    )

    @field_validator("extra")
    @classmethod
    def _extra_names_only_what_taxman_does_not(cls, extra: dict[str, Any]) -> dict[str, Any]:
        """Overriding a named setting would bypass its checks, and its defaults."""
        return check_extra(extra, set_by_taxman=SET_BY_TAXMAN, never_sent=NEVER_SENT)

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
