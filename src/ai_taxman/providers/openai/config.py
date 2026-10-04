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

from pydantic import Field, field_validator, model_validator

from ai_taxman.providers.openai.models import ReasoningEffort
from ai_taxman.providers.settings import SettingsBlock, check_extra

#: Request keys `build_request` sets itself. `extra:` may not touch them.
SET_BY_TAXMAN = frozenset(
    {
        "model",
        "input",
        "instructions",
        "store",
        "temperature",
        "top_p",
        "max_output_tokens",
        "reasoning",
        "tools",
        "tool_choice",
        "include",
    }
)

#: Request keys taxman never sends, because `send` could not record the result.
NEVER_SENT = {
    "stream": "a stream is not a response, so there would be nothing whole to record",
    "background": "a background response comes back before it has an answer to record",
}

#: The web-search guide's cap on each domain list.
MAX_DOMAINS = 100

#: OpenAI's pages, linked from the template. Each is listed, as its markdown twin,
#: in docs/provider-apis/openai.md.
MODELS_DOCS = "https://developers.openai.com/api/docs/models"
CREATE_DOCS = "https://developers.openai.com/api/reference/resources/responses/methods/create"
REASONING_DOCS = "https://developers.openai.com/api/docs/guides/reasoning"
DATA_DOCS = "https://developers.openai.com/api/docs/guides/your-data"
WEB_SEARCH_DOCS = "https://developers.openai.com/api/docs/guides/tools-web-search"

#: What a blank sampling or length setting does.
MODEL_DEFAULT = "model default"

#: The web-search data `include` can ask for: every URL consulted, and the raw
#: results (which is where image results arrive).
WebSearchInclude = Literal["web_search_call.action.sources", "web_search_call.results"]
SOURCES: WebSearchInclude = "web_search_call.action.sources"


class OpenAIUserLocation(SettingsBlock):
    """An approximate location to localise search results. Sent with `type: approximate`."""

    #: ISO 3166-1, as OpenAI takes it: `US`, not `us` or `USA`.
    country: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{2}$",
        description="Two-letter country code.",
        examples=["US"],
        json_schema_extra={"blank": "not sent"},
    )
    region: str | None = Field(
        default=None,
        description="Region name.",
        examples=["Minnesota"],
        json_schema_extra={"blank": "not sent"},
    )
    city: str | None = Field(
        default=None,
        description="City name.",
        examples=["Minneapolis"],
        json_schema_extra={"blank": "not sent"},
    )
    timezone: str | None = Field(
        default=None,
        description="IANA time zone.",
        examples=["America/Chicago"],
        json_schema_extra={"blank": "not sent"},
    )


class OpenAIImageSettings(SettingsBlock):
    """Image results, when `search_content_types` asks for them."""

    max_results: int | None = Field(
        default=None,
        ge=1,
        description="Number of image results to request.",
        json_schema_extra={"blank": "OpenAI default"},
    )
    caption: bool | None = Field(
        default=None,
        description="Request a short caption for each image.",
        json_schema_extra={"blank": "not sent"},
    )


class OpenAISearchConfig(SettingsBlock):
    """The `search:` block: the web-search tool and its settings.

    Names are OpenAI's own, so each one can be looked up in the web-search guide
    as written. Every setting but `web_search` is sent only when set.
    """

    web_search: bool = Field(
        default=False,
        description="Give the model the web search tool.",
    )

    search_context_size: Literal["low", "medium", "high"] | None = Field(
        default=None,
        description="How much search-result context the model sees.",
        json_schema_extra={"blank": "OpenAI default"},
    )
    external_web_access: bool | None = Field(
        default=None,
        description="Fetch live pages; false limits search to cached results.",
        json_schema_extra={"blank": "live"},
    )
    #: GPT-5+ reasoning web search only.
    return_token_budget: Literal["default", "unlimited"] | None = Field(
        default=None,
        description="How much content one search run may return. GPT-5+ reasoning models only.",
        json_schema_extra={
            "options": {
                "default": "the standard budget",
                "unlimited": "no budget; raises latency and cost",
            },
            "blank": "the standard budget",
        },
    )

    #: Sent together as the tool's `filters`.
    allowed_domains: list[str] | None = Field(
        default=None,
        max_length=MAX_DOMAINS,
        description="Only search these domains, subdomains included.",
        examples=[["cdc.gov", "who.int"]],
        json_schema_extra={"blank": "any domain"},
    )
    blocked_domains: list[str] | None = Field(
        default=None,
        max_length=MAX_DOMAINS,
        description="Never search these domains, subdomains included.",
        examples=[["example.com"]],
        json_schema_extra={"blank": "none"},
    )

    #: Not supported for deep-research models; OpenAI rejects it there.
    user_location: OpenAIUserLocation | None = Field(
        default=None,
        description="Approximate location to localise results. Deep-research models reject it.",
    )

    #: Request-level, not on the tool.
    tool_choice: Literal["auto", "required"] | None = Field(
        default=None,
        description="Whether the model must search.",
        json_schema_extra={
            "options": {
                "auto": "the model decides",
                "required": "the model searches before answering",
            },
            "blank": "auto",
        },
    )

    #: Request-level. Sources are on by default: every URL the model consulted,
    #: not only those it cited, is what an audit of search needs.
    include: list[WebSearchInclude] = Field(
        default_factory=lambda: [SOURCES],
        description="Search data to record; [] records neither.",
        json_schema_extra={
            "options": {
                SOURCES: "every URL consulted, not only those cited",
                "web_search_call.results": "raw results, including image results",
            }
        },
    )

    #: Last, as in the template.
    search_content_types: list[Literal["text", "image"]] | None = Field(
        default=None,
        min_length=1,
        description="Kinds of search result to request. "
        "To record image results, add web_search_call.results to include.",
        examples=[["image", "text"]],
        json_schema_extra={"blank": "OpenAI default"},
    )
    image_settings: OpenAIImageSettings | None = Field(
        default=None,
        description="Image result settings, which need image in search_content_types.",
    )

    @field_validator("allowed_domains", "blocked_domains")
    @classmethod
    def _bare_domains(cls, domains: list[str] | None) -> list[str] | None:
        """OpenAI wants `openai.com`, not `https://openai.com/`. Refused, not rewritten."""
        for domain in domains or []:
            if domain.lower().startswith(("http://", "https://")):
                raise ValueError(
                    f"{domain!r}: write the domain without the http:// or https:// prefix, "
                    "e.g. cdc.gov. Subdomains are included."
                )
        return domains

    @model_validator(mode="after")
    def _settings_need_web_search(self) -> OpenAISearchConfig:
        """A setting for a tool that is not sent would be recorded but never used."""
        chosen = sorted(self.model_fields_set - {"web_search"})
        if chosen and not self.web_search:
            raise ValueError(
                f"{', '.join(chosen)} set in `search:`, but `web_search` is not true. "
                "Set `web_search: true`, or leave the other search settings blank."
            )
        if self.image_settings is not None and "image" not in (self.search_content_types or []):
            raise ValueError(
                "image_settings set in `search:`, but search_content_types does not ask "
                "for image results. Add image to search_content_types, e.g. [image, text], "
                "or leave image_settings blank."
            )
        return self


class OpenAIModelConfig(SettingsBlock):
    """Validated OpenAI settings for one audit."""

    #: Any model name is allowed; `known_models()` is only a convenience list.
    name: str = Field(
        description="The model to send every message to.",
        json_schema_extra={"docs": MODELS_DOCS},
    )

    #: Sent only when set, so the API's own default applies otherwise.
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Sampling randomness, from focused to varied. "
        "OpenAI recommends changing this or top_p, not both.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )
    top_p: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Nucleus sampling: 0.1 means only the top 10% of probability mass "
        "is considered.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Max tokens per response, reasoning tokens included.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )

    #: Reasoning models only. Other models reject it.
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description="How much the model reasons before answering. Reasoning models only.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": REASONING_DOCS},
    )

    #: Off by default: an audit should not leave a trail in the account it is
    #: auditing from.
    store: bool = Field(
        default=False,
        description="Let OpenAI keep responses on its servers.",
        json_schema_extra={"docs": DATA_DOCS},
    )

    #: Escape hatch for API parameters this config does not name yet. Merged
    #: into the request as-is - but never over one it does name.
    extra: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"template": False})

    @field_validator("extra")
    @classmethod
    def _extra_names_only_what_taxman_does_not(cls, extra: dict[str, Any]) -> dict[str, Any]:
        """Overriding a named setting would bypass its checks, and its defaults.

        `extra: {include: [...]}` with web search on would quietly drop the
        default sources, for one.
        """
        return check_extra(extra, set_by_taxman=SET_BY_TAXMAN, never_sent=NEVER_SENT)

    #: Last, as it is in the template: it is the one nested block.
    search: OpenAISearchConfig = Field(
        default_factory=OpenAISearchConfig,
        description="Web search: web_search must be true to use any other setting in this block.",
        json_schema_extra={"docs": WEB_SEARCH_DOCS},
    )
