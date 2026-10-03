"""The `model:` block of a Gemini audit.

Every key here is Google's business. Core never reads them - it hands the block
to `GeminiProvider.validate_model_config`, which returns one of these.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_taxman.providers.gemini.models import ThinkingLevel


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
#: anything else - a key inside `generation_config` included, so a `seed` can sit
#: beside the audit's temperature - but never one of these.
SET_BY_TAXMAN = frozenset(
    {
        "model",
        "input",
        "store",
        "system_instruction",
        "generation_config.temperature",
        "generation_config.top_p",
        "generation_config.max_output_tokens",
        "generation_config.thinking_level",
        "generation_config.thinking_summaries",
        "tools",
    }
)

#: Google's pages, linked from the template. Each is listed, as its markdown twin,
#: in docs/provider-apis/gemini.md.
MODELS_DOCS = "https://ai.google.dev/gemini-api/docs/models"
TEXT_DOCS = "https://ai.google.dev/gemini-api/docs/text-generation"
THINKING_DOCS = "https://ai.google.dev/gemini-api/docs/thinking"
INTERACTIONS_DOCS = "https://ai.google.dev/gemini-api/docs/interactions-overview"
SEARCH_DOCS = "https://ai.google.dev/gemini-api/docs/google-search"

#: What a blank sampling or thinking setting does.
MODEL_DEFAULT = "model default"

#: Request keys taxman never sends, because `send` could not record the result.
NEVER_SENT = {
    "stream": "a stream is not a response, so there would be nothing whole to record",
    "background": "a background interaction comes back before it has an answer to record",
}


class GeminiSearchConfig(_Block):
    """The `search:` block: grounding with Google Search.

    `web_search` is its only setting. The Interactions API's `google_search` tool
    documents `search_types`, and `generation_config.tool_choice` would seem to
    force a search, but live, either one turns the built-in search into a
    client-side function call (or a 400) - no search, no answer. Neither is
    offered; see docs/provider-apis/gemini.md for what was seen.
    """

    #: Gives the model the `google_search` tool.
    web_search: bool = Field(default=False, description="Ground answers with Google Search.")


class GeminiModelConfig(_Block):
    """Validated Gemini settings for one audit."""

    #: Any model name is allowed; `known_models()` is only a convenience list.
    name: str = Field(
        description="The model to send every message to.",
        json_schema_extra={"docs": MODELS_DOCS},
    )

    #: Sent in `generation_config`, and only when set, so the model's own
    #: defaults apply otherwise.
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Sampling randomness, from focused to varied.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": TEXT_DOCS},
    )
    top_p: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Nucleus sampling threshold.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": TEXT_DOCS},
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Max tokens per response, thinking included.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": TEXT_DOCS},
    )
    thinking_level: ThinkingLevel | None = Field(
        default=None,
        description="How much the model thinks before answering. Levels vary by model.",
        json_schema_extra={"blank": MODEL_DEFAULT, "docs": THINKING_DOCS},
    )
    #: `auto` returns the summary in a `thought` step.
    thinking_summaries: Literal["auto", "none"] | None = Field(
        default=None,
        description="Whether to return a summary of the model's thinking.",
        json_schema_extra={
            "options": {
                "auto": "return a summary",
                "none": "no summary",
            },
            "blank": "none",
            "docs": THINKING_DOCS,
        },
    )

    #: Off by default, and always sent: the Interactions API keeps everything for
    #: 55 days unless told not to, and an audit should not leave a trail in the
    #: account it is auditing from.
    store: bool = Field(
        default=False,
        description="Let Google keep the interaction: "
        "55 days on the paid tier, 1 day on the free tier.",
        json_schema_extra={"docs": INTERACTIONS_DOCS},
    )

    #: Escape hatch for API parameters this config does not name yet. Merged into
    #: the request as written - but never over one it does name.
    extra: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"template": False})

    #: Last, as in the template: it is the one nested block.
    search: GeminiSearchConfig = Field(
        default_factory=GeminiSearchConfig,
        description="Grounding with Google Search. Google's search_types and tool_choice "
        "are not offered: tested live, both stopped the search.",
        json_schema_extra={"docs": SEARCH_DOCS},
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


#: The `model:` keys sent, under the same names, inside `generation_config`.
GENERATION_CONFIG_KEYS: tuple[str, ...] = (
    "temperature",
    "top_p",
    "max_output_tokens",
    "thinking_level",
    "thinking_summaries",
)
