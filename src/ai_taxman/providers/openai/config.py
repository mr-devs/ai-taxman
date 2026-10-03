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

#: The web-search data `include` can ask for: every URL consulted, and the raw
#: results (which is where image results arrive).
WebSearchInclude = Literal["web_search_call.action.sources", "web_search_call.results"]
SOURCES: WebSearchInclude = "web_search_call.action.sources"


class OpenAIUserLocation(_Block):
    """An approximate location to localise search results. Sent with `type: approximate`."""

    #: A two-letter ISO 3166-1 code, as OpenAI takes it: `US`, not `us` or `USA`.
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = None
    city: str | None = None
    #: An IANA timezone, e.g. `America/Chicago`.
    timezone: str | None = None


class OpenAIImageSettings(_Block):
    """Image results, when `search_content_types` asks for them."""

    max_results: int | None = Field(default=None, ge=1)
    caption: bool | None = None


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

    #: Sent together as the tool's `filters`.
    allowed_domains: list[str] | None = Field(default=None, max_length=MAX_DOMAINS)
    blocked_domains: list[str] | None = Field(default=None, max_length=MAX_DOMAINS)

    #: Not supported for deep-research models; OpenAI rejects it there.
    user_location: OpenAIUserLocation | None = None

    #: Request-level, not on the tool. `required` makes the model search before
    #: answering; with `auto` it may not search at all.
    tool_choice: Literal["auto", "required"] | None = None

    #: Request-level. Sources are on by default: every URL the model consulted,
    #: not only those it cited, is what an audit of search needs. `[]` turns it off.
    include: list[WebSearchInclude] = Field(default_factory=lambda: [SOURCES])

    #: Last, as in the template. `image` asks for image results, which arrive in
    #: `web_search_call.results` - so `include` needs that too to record them.
    search_content_types: list[Literal["text", "image"]] | None = Field(default=None, min_length=1)
    image_settings: OpenAIImageSettings | None = None

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
    #: into the request as-is - but never over one it does name.
    extra: dict[str, Any] = Field(default_factory=dict)

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
#: A nested block lists its own fields as a third element.
TemplateField = tuple[str, str] | tuple[str, str, tuple[tuple[str, str], ...]]
SEARCH_TEMPLATE_FIELDS: tuple[TemplateField, ...] = (
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
    (
        "allowed_domains",
        "Only search these domains, e.g. [cdc.gov, who.int]. Up to 100; subdomains included.",
    ),
    ("blocked_domains", "Never search these domains. Up to 100."),
    (
        "tool_choice",
        "auto | required. required makes the model search before answering. Blank = auto.",
    ),
    (
        "include",
        "Search data to return. Blank = [web_search_call.action.sources], every URL "
        "consulted. Add web_search_call.results for raw results; [] for none.",
    ),
    (
        "user_location",
        "Approximate location to localise results. Leave all blank for none.",
        (
            ("country", "Two-letter ISO code, e.g. US."),
            ("region", "Free text, e.g. Minnesota."),
            ("city", "Free text, e.g. Minneapolis."),
            ("timezone", "IANA timezone, e.g. America/Chicago."),
        ),
    ),
    (
        "search_content_types",
        "[text], [image], or [image, text]. Blank = OpenAI's default. Image results "
        "arrive in web_search_call.results; add it to include to record them.",
    ),
    (
        "image_settings",
        "Image results only; needs image in search_content_types.",
        (
            ("max_results", "Number of image results to request."),
            ("caption", "true to ask for short image descriptions."),
        ),
    ),
)
