"""The OpenAI provider.

Nothing outside this package imports the `openai` SDK, and the SDK itself is
imported lazily inside `_client()` so that listing providers, generating a
template, or running an Anthropic audit all work with it uninstalled.

Requests go through the Responses API, which is the one endpoint that covers
reasoning effort and the web-search tool alongside ordinary text generation.

Split, per the project's provider rules:
  pure  - `build_request`, `extract`, `validate_model_config`, `render_template`
  I/O   - `send`, `startup`, `shutdown`
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from ai_taxman.core.errors import ProviderDependencyError, ProviderError
from ai_taxman.providers.base import Extracted, Provider, Request
from ai_taxman.providers.openai.config import TEMPLATE_FIELDS, OpenAIModelConfig
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

    def extract(self, raw: dict[str, Any]) -> Extracted:
        return Extracted(text=extract_text(raw), usage=extract_usage(raw))

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
    if config.web_search:
        payload["tools"] = [{"type": "web_search"}]
    if config.system_prompt:
        payload["instructions"] = config.system_prompt

    payload.update(config.extra)
    return payload


def extract_text(raw: dict[str, Any]) -> str | None:
    """Return the assistant's text, or the refusal if it refused.

    Tolerant by design: an unexpected payload yields `None` rather than raising,
    because `raw` is already safely on disk and losing the whole record to a
    parsing surprise would be worse than losing the convenience field.
    """
    shortcut = raw.get("output_text")
    if isinstance(shortcut, str) and shortcut:
        return shortcut

    output = raw.get("output")
    if not isinstance(output, list):
        return None

    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue  # reasoning items, tool calls
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text") or part.get("refusal")
            if isinstance(text, str) and text:
                parts.append(text)

    return "\n".join(parts) if parts else None


def extract_usage(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten the usage block, lifting reasoning and cached token counts up."""
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return None

    flat: dict[str, Any] = {
        key: usage[key] for key in ("input_tokens", "output_tokens", "total_tokens") if key in usage
    }

    details = usage.get("output_tokens_details")
    if isinstance(details, dict) and "reasoning_tokens" in details:
        flat["reasoning_tokens"] = details["reasoning_tokens"]

    details = usage.get("input_tokens_details")
    if isinstance(details, dict) and "cached_tokens" in details:
        flat["cached_tokens"] = details["cached_tokens"]

    return flat


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
