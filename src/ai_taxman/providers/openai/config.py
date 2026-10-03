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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_taxman.providers.openai.models import ReasoningEffort


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
MODEL_DEFAULT = "not sent, so the model's own default applies"

#: The web-search data `include` can ask for: every URL consulted, and the raw
#: results (which is where image results arrive).
WebSearchInclude = Literal["web_search_call.action.sources", "web_search_call.results"]
SOURCES: WebSearchInclude = "web_search_call.action.sources"


class OpenAIUserLocation(_Block):
    """An approximate location to localise search results. Sent with `type: approximate`."""

    #: ISO 3166-1, as OpenAI takes it: `US`, not `us` or `USA`.
    country: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{2}$",
        description="A two-letter country code, in capitals.",
        examples=["US"],
        json_schema_extra={"blank": "not sent"},
    )
    region: str | None = Field(
        default=None,
        description="A region, in free text.",
        examples=["Minnesota"],
        json_schema_extra={"blank": "not sent"},
    )
    city: str | None = Field(
        default=None,
        description="A city, in free text.",
        examples=["Minneapolis"],
        json_schema_extra={"blank": "not sent"},
    )
    timezone: str | None = Field(
        default=None,
        description="An IANA time zone.",
        examples=["America/Chicago"],
        json_schema_extra={"blank": "not sent"},
    )


class OpenAIImageSettings(_Block):
    """Image results, when `search_content_types` asks for them."""

    max_results: int | None = Field(
        default=None,
        ge=1,
        description="How many image results to ask for.",
        json_schema_extra={"blank": "not sent, so OpenAI's default applies"},
    )
    caption: bool | None = Field(
        default=None,
        description="Ask for a short description of each image, where one is available.",
        json_schema_extra={"blank": "not sent"},
    )


class OpenAISearchConfig(_Block):
    """The `search:` block: the web-search tool and its settings.

    Names are OpenAI's own, so each one can be looked up in the web-search guide
    as written. Every setting but `web_search` is sent only when set.
    """

    web_search: bool = Field(
        default=False,
        description="Give the model the web search tool. Whether it searches is up to "
        "the model, unless tool_choice is required.",
    )

    search_context_size: Literal["low", "medium", "high"] | None = Field(
        default=None,
        description="How much context from search results the model sees before it "
        "answers. Not an exact token count, nor a number of sources.",
        json_schema_extra={"blank": "not sent, so OpenAI's default applies"},
    )
    external_web_access: bool | None = Field(
        default=None,
        description="Whether search fetches live pages. false limits it to cached and "
        "indexed results.",
        json_schema_extra={"blank": "not sent, so search is live"},
    )
    #: GPT-5+ reasoning web search only.
    return_token_budget: Literal["default", "unlimited"] | None = Field(
        default=None,
        description="How much search-result content the tool may return in one run. "
        "GPT-5 and later reasoning models only. unlimited can raise latency and cost: "
        "keep it for high-effort research or evaluation runs.",
        json_schema_extra={
            "options": {
                "default": "the standard budget, the same as leaving this blank",
                "unlimited": "no budget",
            },
            "blank": "not sent, so the standard budget applies",
        },
    )

    #: Sent together as the tool's `filters`.
    allowed_domains: list[str] | None = Field(
        default=None,
        max_length=MAX_DOMAINS,
        description="Search only these domains, subdomains included. Write each without "
        "http:// or https://.",
        examples=[["cdc.gov", "who.int"]],
        json_schema_extra={"blank": "any domain"},
    )
    blocked_domains: list[str] | None = Field(
        default=None,
        max_length=MAX_DOMAINS,
        description="Never search these domains, subdomains included. Write each "
        "without http:// or https://.",
        examples=[["example.com"]],
        json_schema_extra={"blank": "none blocked"},
    )

    #: Not supported for deep-research models; OpenAI rejects it there.
    user_location: OpenAIUserLocation | None = Field(
        default=None,
        description="An approximate location to localise search results. Leave every "
        "key blank for none. Deep-research models reject it.",
    )

    #: Request-level, not on the tool.
    tool_choice: Literal["auto", "required"] | None = Field(
        default=None,
        description="Whether the model must search.",
        json_schema_extra={
            "options": {
                "auto": "the model decides, and may not search at all",
                "required": "the model searches before answering",
            },
            "blank": "not sent, so auto",
        },
    )

    #: Request-level. Sources are on by default: every URL the model consulted,
    #: not only those it cited, is what an audit of search needs.
    include: list[WebSearchInclude] = Field(
        default_factory=lambda: [SOURCES],
        description="Which search data each response records. Write [] to record neither.",
        json_schema_extra={
            "options": {
                SOURCES: "every URL the model consulted, not only those it cited",
                "web_search_call.results": "the raw search results, where image results arrive",
            }
        },
    )

    #: Last, as in the template.
    search_content_types: list[Literal["text", "image"]] | None = Field(
        default=None,
        min_length=1,
        description="Which kinds of search result to ask for. Image results arrive in "
        "web_search_call.results, so add that to include to record them.",
        examples=[["image", "text"]],
        json_schema_extra={"blank": "not sent, so OpenAI's default applies"},
    )
    image_settings: OpenAIImageSettings | None = Field(
        default=None,
        description="Image results only: needs image in search_content_types.",
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


class OpenAIModelConfig(_Block):
    """Validated OpenAI settings for one audit."""

    #: Any model name is allowed; `known_models()` is only a convenience list.
    name: str = Field(
        description="The model every message is sent to. Any name OpenAI accepts works.",
        json_schema_extra={"docs": MODELS_DOCS},
    )

    #: Sent only when set, so the API's own default applies otherwise.
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="How random the sampling is: lower is more focused and "
        "deterministic, higher more varied. OpenAI recommends changing this or top_p, "
        "not both.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )
    top_p: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Nucleus sampling: the model considers only the most likely tokens "
        "that make up this share of the probability. 0.1 means the top 10%.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description="The most tokens a response may use, reasoning tokens included. A "
        "response that reaches it stops short.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": CREATE_DOCS},
    )

    #: Reasoning models only. Other models reject it.
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description="How much the model reasons before answering. Reasoning models only: "
        "other models reject it, and not every reasoning model takes every level.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": REASONING_DOCS},
    )

    #: Off by default: an audit should not leave a trail in the account it is
    #: auditing from.
    store: bool = Field(
        default=False,
        description="Let OpenAI keep the response on its servers. Off, so an audit "
        "leaves no trail in the account it runs from.",
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
        taken = sorted(set(extra) & SET_BY_TAXMAN)
        if taken:
            raise ValueError(
                f"`extra:` cannot set {', '.join(taken)}: taxman sets "
                f"{'it' if len(taken) == 1 else 'them'} from this audit. Use the named "
                "setting in `model:` or `search:` instead (the system prompt is the "
                "audit's `system_prompt:`)."
            )
        return extra

    #: Last, as it is in the template: it is the one nested block.
    search: OpenAISearchConfig = Field(
        default_factory=OpenAISearchConfig,
        description="Web search. web_search must be true to use any other setting in this block.",
        json_schema_extra={"docs": WEB_SEARCH_DOCS},
    )
