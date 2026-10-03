"""The `model:` block of an Anthropic audit.

Every key here is Anthropic's business. Core never reads them - it hands the block
to `AnthropicProvider.validate_model_config`, which returns one of these.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_taxman.providers.anthropic.models import (
    DEFAULT_WEB_SEARCH_VERSION,
    Effort,
    ThinkingDisplay,
    ThinkingType,
    WebSearchCaller,
    WebSearchVersion,
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
            return {
                key: value
                for key, value in block.items()
                # `extra:` is sent as written, so only a wholly blank one is dropped.
                if not (value is None if key == "extra" else _blank(value))
            }
        return block


def _blank(value: Any) -> bool:
    """None, or a nested block whose every key was left blank."""
    if isinstance(value, dict):
        return all(_blank(inner) for inner in value.values())
    return value is None


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
MODEL_DEFAULT = "not sent, so the model's own default applies"


class AnthropicThinking(_Block):
    """The `thinking:` block, sent as the `thinking` parameter when `type` is set.

    Only rules Anthropic documents for every model are checked here; which model
    takes which type is the API's call.
    """

    type: ThinkingType | None = Field(
        default=None,
        description="How Claude thinks before answering. Which modes a model accepts varies.",
        json_schema_extra={
            "options": {
                "adaptive": "Claude decides when and how deeply to think",
                "enabled": "think up to budget_tokens; older models only",
                "disabled": "no thinking, on models that allow it",
                "between_tools": "no up-front thinking; Claude Sonnet 5.5 only, at "
                "effort high or below",
            },
            "blank": "not sent, so the model's default applies",
        },
    )
    #: `type: enabled` only, the manual mode older models use.
    budget_tokens: int | None = Field(
        default=None,
        ge=MIN_THINKING_BUDGET,
        description="The most tokens Claude may spend thinking, with type: enabled "
        "only. It must be less than max_tokens, which thinking counts toward.",
        json_schema_extra={"blank": "not sent; type: enabled needs it"},
    )
    display: ThinkingDisplay | None = Field(
        default=None,
        description="Whether thinking text comes back. Not with type: disabled or between_tools.",
        json_schema_extra={
            "options": {
                "summarized": "a readable summary of Claude's thinking",
                "omitted": "thinking blocks come back with their text empty",
                "updates": "beta: as omitted, but notes between tool calls come back",
            },
            "blank": "not sent, so the model's default applies: omitted on current models",
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


class AnthropicUserLocation(_Block):
    """An approximate location to localise search results. Sent with `type: approximate`."""

    city: str | None = Field(
        default=None,
        description="A city, in free text.",
        examples=["Minneapolis"],
        json_schema_extra={"blank": "not sent"},
    )
    region: str | None = Field(
        default=None,
        description="A region, in free text.",
        examples=["Minnesota"],
        json_schema_extra={"blank": "not sent"},
    )
    #: ISO 3166-1: `US`, not `us` or `USA`.
    country: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{2}$",
        description="A two-letter country code, in capitals.",
        examples=["US"],
        json_schema_extra={"blank": "not sent"},
    )
    timezone: str | None = Field(
        default=None,
        description="An IANA time zone.",
        examples=["America/Chicago"],
        json_schema_extra={"blank": "not sent"},
    )


class AnthropicSearchConfig(_Block):
    """The `search:` block: Claude's web search tool and its settings.

    Names are Anthropic's own, so each one can be looked up in the web search
    tool guide as written. Every setting but `web_search` is sent only when set.
    """

    web_search: bool = Field(default=False, description="Give Claude the web search tool.")

    #: Blank sends the newest.
    tool_version: WebSearchVersion | None = Field(
        default=None,
        description="Which version of the web search tool to send.",
        json_schema_extra={
            "options": {
                "web_search_20250305": "basic web search",
                "web_search_20260209": "adds dynamic filtering: Claude filters results "
                "with code before reading them",
                "web_search_20260318": "adds response_inclusion",
            },
            "blank": f"{DEFAULT_WEB_SEARCH_VERSION}, the newest",
        },
    )

    max_uses: int | None = Field(
        default=None,
        ge=1,
        description="The most searches Claude may run for one message.",
        json_schema_extra={"blank": "no limit"},
    )

    #: One or the other, never both. Bare domains, optionally with a path:
    #: `example.com/blog`. Subdomains are included.
    allowed_domains: list[str] | None = Field(
        default=None,
        description="Search only these domains, subdomains included. Write each without "
        "http:// or https://; a path, as in example.com/blog, is allowed. Not with "
        "blocked_domains.",
        examples=[["cdc.gov", "who.int"]],
        json_schema_extra={"blank": "any domain"},
    )
    blocked_domains: list[str] | None = Field(
        default=None,
        description="Never search these domains, written as for allowed_domains. Not "
        "with allowed_domains.",
        examples=[["example.com"]],
        json_schema_extra={"blank": "none blocked"},
    )

    user_location: AnthropicUserLocation | None = Field(
        default=None,
        description="An approximate location to localise search results. Leave every "
        "key blank for none.",
    )

    #: Models without programmatic tool calling need `[direct]` on `_20260209` and
    #: later; Anthropic says so with a 400.
    allowed_callers: list[WebSearchCaller] | None = Field(
        default=None,
        min_length=1,
        description="Who may run a search: Claude directly, or code Claude runs to "
        "filter the results first. [direct] turns dynamic filtering off; models without "
        "programmatic tool calling need it.",
        examples=[["direct"]],
        json_schema_extra={"blank": "not sent, so the tool version's default applies"},
    )

    response_inclusion: Literal["full", "excluded"] | None = Field(
        default=None,
        description="Whether result blocks that code execution already used come back "
        "in the response. web_search_20260318 only.",
        json_schema_extra={
            "options": {
                "full": "every result block comes back",
                "excluded": "drop result blocks a finished code execution call consumed",
            },
            "blank": "not sent, so full",
        },
    )

    #: Request-level, not on the tool.
    tool_choice: Literal["auto", "any"] | None = Field(
        default=None,
        description="Whether Claude must search.",
        json_schema_extra={
            "options": {
                "auto": "Claude decides whether to search",
                "any": "Claude uses a tool before answering; current models reject it",
            },
            "blank": "not sent, so auto",
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


class AnthropicModelConfig(_Block):
    """Validated Anthropic settings for one audit."""

    #: Any model name is allowed; `known_models()` is only a convenience list.
    name: str = Field(
        description="The model every message is sent to. Any name Anthropic accepts works.",
        json_schema_extra={"docs": MODELS_DOCS},
    )

    #: Required: the Messages API has no default, and rejects a request without it.
    max_tokens: int = Field(
        ge=1,
        description="The most tokens a response may use, thinking included. Anthropic "
        "requires it, and a response that reaches it stops mid-answer. Raise "
        "execution.timeout_s with it.",
        json_schema_extra={"docs": CREATE_DOCS},
    )

    #: Sent as `output_config.effort`. Blank leaves the model's own default.
    effort: Effort | None = Field(
        default=None,
        description="How much work Claude puts into a response, thinking included. Not "
        "every model takes every level.",
        json_schema_extra={
            "options": {
                "low": "the most efficient: big token savings, some loss of capability",
                "medium": "balanced, with moderate token savings",
                "high": "as many tokens as the task needs",
                "xhigh": "extended capability for long-horizon work",
                "max": "the most capability, with no limit on token spending",
            },
            "blank": "not sent, so the model's own default applies: high on most models",
            "docs": EFFORT_DOCS,
        },
    )

    #: Sent only when set. Models after Claude Opus 4.6 refuse anything but
    #: temperature 1.0 and top_p >= 0.99, and refuse top_k outright; the API says so.
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="How random the sampling is. Models after Claude Opus 4.6 accept "
        "only 1.0, so leave it blank for them.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )
    top_p: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Nucleus sampling: Claude considers only the most likely tokens "
        "that make up this share of the probability. Models after Claude Opus 4.6 "
        "accept only 0.99 or more.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        description="Sample from only the K most likely tokens. Models after Claude "
        "Opus 4.6 reject it.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )

    #: Escape hatch for API parameters this config does not name yet. Merged into
    #: the request as written - but never over one it does name.
    extra: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"template": False})

    #: Last, as in the template: the nested blocks.
    thinking: AnthropicThinking = Field(
        default_factory=AnthropicThinking,
        description="Thinking. Leave every key blank for the model's default, which "
        "varies by model.",
        json_schema_extra={"docs": THINKING_DOCS},
    )
    search: AnthropicSearchConfig = Field(
        default_factory=AnthropicSearchConfig,
        description="Web search. web_search must be true to use any other setting in this block.",
        json_schema_extra={"docs": WEB_SEARCH_DOCS},
    )

    @field_validator("extra")
    @classmethod
    def _extra_names_only_what_taxman_does_not(cls, extra: dict[str, Any]) -> dict[str, Any]:
        """Overriding a named setting would bypass its checks, and its defaults."""
        paths = list(leaf_paths(extra))
        for path in paths:
            if path in NEVER_SENT:
                raise ValueError(f"`extra:` cannot set {path}: {NEVER_SENT[path]}.")
        taken = sorted({key for key in SET_BY_TAXMAN for path in paths if _overlaps(path, key)})
        if taken:
            raise ValueError(
                f"`extra:` cannot set {', '.join(taken)}: taxman sets "
                f"{'it' if len(taken) == 1 else 'them'} from this audit. Use the named "
                "setting in `model:` instead (the system prompt is the audit's "
                "`system_prompt:`)."
            )
        return extra

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


def leaf_paths(block: dict[str, Any], prefix: str = "") -> Iterator[str]:
    """Every dotted path in `block` that ends in a value rather than a further object."""
    for key, value in block.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            yield from leaf_paths(value, f"{path}.")
        else:
            yield path


def _overlaps(path: str, key: str) -> bool:
    """Whether writing `path` would change `key`: the same, inside it, or replacing it."""
    return path == key or path.startswith(f"{key}.") or key.startswith(f"{path}.")
