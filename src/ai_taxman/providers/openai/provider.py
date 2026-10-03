"""The OpenAI provider.

Nothing outside this package imports the `openai` SDK, and the SDK itself is
imported lazily inside `_client()` so that listing providers, generating a
template, or running an Anthropic audit all work with it uninstalled.

Requests go through the Responses API, which is the one endpoint that covers
reasoning effort and the web-search tool alongside ordinary text generation.

Split, per the project's provider rules:
  pure  - `build_request`, `validate_model_config`, `render_template`
  I/O   - `send`, `startup`, `shutdown`

Nothing here reads a response. `send` returns what OpenAI sent, as a dict, and
the runner writes it verbatim.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from ai_taxman.core.errors import ProviderDependencyError, ProviderError
from ai_taxman.providers.base import Provider, Request
from ai_taxman.providers.openai.config import (
    SEARCH_TEMPLATE_FIELDS,
    TEMPLATE_FIELDS,
    OpenAIModelConfig,
)
from ai_taxman.providers.openai.models import DEFAULT_MODEL, KNOWN_MODELS

if TYPE_CHECKING:
    from openai import AsyncOpenAI

INSTALL_HINT = (
    "The OpenAI SDK is not installed. Add it with `uv add ai-taxman[openai]` "
    "(or `uv sync --all-extras` when working on ai-taxman itself)."
)

#: SDK error class names worth retrying. Matched by name so this module still
#: imports cleanly when the SDK is absent.
RETRYABLE_ERRORS = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "ConflictError",
        "InternalServerError",
        "RateLimitError",
    }
)


class OpenAIProvider(Provider):
    """Audit OpenAI models through the Responses API."""

    name = "openai"
    supports_batch = False
    default_api_key_env = "OPENAI_API_KEY"

    # -- pure ---------------------------------------------------------------

    def known_models(self) -> list[str]:
        return list(KNOWN_MODELS)

    def validate_model_config(self, block: dict[str, Any]) -> OpenAIModelConfig:
        return OpenAIModelConfig(**block)

    def render_template(self) -> str:
        return render_template()

    def describe_model(self, model: OpenAIModelConfig) -> str:
        return model.name

    def is_retryable(self, exc: BaseException) -> bool:
        return type(exc).__name__ in RETRYABLE_ERRORS and _is_openai_error(exc)

    # -- I/O ----------------------------------------------------------------

    async def send(self, request: Request, *, timeout_s: float) -> dict[str, Any]:
        client = self._require_client()
        response = await client.responses.create(**build_request(request), timeout=timeout_s)
        return response.model_dump(mode="json")  # type: ignore[no-any-return]

    async def startup(self, *, api_key: str | None = None) -> None:
        _CLIENT.set(_new_client(api_key))

    async def shutdown(self) -> None:
        client = _CLIENT.get()
        if client is not None:
            await client.close()
            _CLIENT.set(None)

    def _require_client(self) -> AsyncOpenAI:
        """The client `startup()` built, never one improvised here.

        Building a client without a key would let the SDK fall back to whatever
        `OPENAI_API_KEY` happens to be exported - the fallback chain the project
        forbids, and one that could silently bill a key the audit never named.
        """
        client = _CLIENT.get()
        if client is None:
            raise ProviderError(
                "The openai provider was used before startup(), so no API key was supplied. "
                "Run the audit through `taxman collect`, or call `await provider.startup"
                "(api_key=...)` first."
            )
        return client


def build_request(request: Request) -> dict[str, Any]:
    """Turn one `Request` into Responses API keyword arguments.

    Parameters the user left blank are omitted entirely, so the API's own
    defaults apply and the audit record shows what was actually asked for.
    """
    config: OpenAIModelConfig = request.model
    payload: dict[str, Any] = {
        "model": config.name,
        "input": request.message.text,
        "store": config.store,
    }

    if config.temperature is not None:
        payload["temperature"] = config.temperature
    if config.top_p is not None:
        payload["top_p"] = config.top_p
    if config.max_output_tokens is not None:
        payload["max_output_tokens"] = config.max_output_tokens
    if config.reasoning_effort is not None:
        payload["reasoning"] = {"effort": config.reasoning_effort}
    if config.search.web_search:
        payload["tools"] = [{"type": "web_search"}]
    if request.system_prompt:
        payload["instructions"] = request.system_prompt

    payload.update(config.extra)
    return payload


def render_template() -> str:
    """Return the `model:` block for a new OpenAI audit.

    Every parameter appears with a comment saying what it does, blank apart from
    the model name, so the generated file is the documentation the user edits.
    """
    lines = [
        "model:",
        f"  name: {DEFAULT_MODEL}  # The model to send every message to.",
    ]
    for key, comment in TEMPLATE_FIELDS:
        lines.append(f"  {key}:  # {comment}")
    lines += [
        "",
        "  # Web search. If web_search is not true, the other search settings are ignored.",
        "  search:",
    ]
    for key, comment in SEARCH_TEMPLATE_FIELDS:
        lines.append(f"    {key}:  # {comment}")
    return "\n".join(lines) + "\n"


#: One client per run, so concurrent runs in one process never share a key.
_CLIENT: ContextVar[AsyncOpenAI | None] = ContextVar("ai_taxman_openai_client", default=None)


def _new_client(api_key: str | None) -> AsyncOpenAI:
    """Build the client from the key core resolved for this audit.

    `max_retries=0` because ai-taxman runs its own retry loop; letting the SDK
    retry as well would multiply the attempts and make the `attempts` field in
    every record a lie.
    """
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise ProviderDependencyError(INSTALL_HINT) from exc

    return AsyncOpenAI(api_key=api_key, max_retries=0)


def _is_openai_error(exc: BaseException) -> bool:
    """Confirm `exc` really came from the SDK, not a same-named class elsewhere."""
    return type(exc).__module__.split(".")[0] == "openai"


PROVIDER = OpenAIProvider()
