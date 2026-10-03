"""The Anthropic provider.

Nothing outside this package imports the `anthropic` SDK, and the SDK itself is
imported lazily inside `_new_client()` so that listing providers, generating a
template, or running an OpenAI audit all work with it uninstalled.

Requests go through the Messages API (`POST /v1/messages`).

Split, per the project's provider rules:
  pure  - `build_request`, `validate_model_config`, `render_template`
  I/O   - `send`, `startup`, `shutdown`

Nothing here reads a response. `send` returns what Anthropic sent, as a dict, and
the runner writes it verbatim.
"""

from __future__ import annotations

import copy
import inspect
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from ai_taxman.core.errors import ProviderDependencyError, ProviderError
from ai_taxman.providers.anthropic.config import (
    DEFAULT_MAX_TOKENS,
    TEMPLATE_FIELDS,
    THINKING_TEMPLATE_FIELDS,
    AnthropicModelConfig,
)
from ai_taxman.providers.anthropic.models import DEFAULT_MODEL, KNOWN_MODELS
from ai_taxman.providers.base import Provider, Request

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

INSTALL_HINT = (
    "The Anthropic SDK is not installed. Add it with `uv add ai-taxman[anthropic]` "
    "(or `uv sync --all-extras` when working on ai-taxman itself)."
)

#: SDK error class names worth retrying. Matched by name so this module still
#: imports cleanly when the SDK is absent. 529 (`OverloadedError`), 503 and 504
#: are siblings of `InternalServerError`, not subclasses, so each is listed.
RETRYABLE_ERRORS = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "ConflictError",
        "DeadlineExceededError",
        "InternalServerError",
        "OverloadedError",
        "RateLimitError",
        "ServiceUnavailableError",
    }
)


class AnthropicProvider(Provider):
    """Audit Claude models through the Messages API."""

    name = "anthropic"
    supports_batch = False
    default_api_key_env = "ANTHROPIC_API_KEY"

    # -- pure ---------------------------------------------------------------

    def known_models(self) -> list[str]:
        return list(KNOWN_MODELS)

    def validate_model_config(self, block: dict[str, Any]) -> AnthropicModelConfig:
        return AnthropicModelConfig(**block)

    def render_template(self) -> str:
        return render_template()

    def describe_model(self, model: AnthropicModelConfig) -> str:
        return model.name

    def is_retryable(self, exc: BaseException) -> bool:
        return type(exc).__name__ in RETRYABLE_ERRORS and _is_anthropic_error(exc)

    # -- I/O ----------------------------------------------------------------

    async def send(self, request: Request, *, timeout_s: float) -> dict[str, Any]:
        client = self._require_client()
        # An explicit timeout also stops the SDK refusing a large `max_tokens` as
        # "may take longer than 10 minutes": the audit's timeout_s is the limit.
        named, unnamed = _split_by_sdk_signature(build_request(request), client)
        response = await client.messages.create(
            **named, extra_body=unnamed or None, timeout=timeout_s
        )
        # `to_dict` keeps only the fields Anthropic actually sent, under the API's
        # own names. `model_dump` would add a null for every field the SDK merely
        # knows about, so `raw` would no longer be the body that came back.
        return response.to_dict(mode="json")  # type: ignore[no-any-return]

    async def startup(self, *, api_key: str | None = None) -> None:
        _CLIENT.set(_new_client(api_key))

    async def shutdown(self) -> None:
        client = _CLIENT.get()
        if client is not None:
            await client.close()
            _CLIENT.set(None)

    def _require_client(self) -> AsyncAnthropic:
        """The client `startup()` built, never one improvised here.

        Building a client without a key would let the SDK fall back to whatever
        `ANTHROPIC_API_KEY` happens to be exported - the fallback chain the project
        forbids, and one that could silently bill a key the audit never named.
        """
        client = _CLIENT.get()
        if client is None:
            raise ProviderError(
                "The anthropic provider was used before startup(), so no API key was supplied. "
                "Run the audit through `taxman collect`, or call `await provider.startup"
                "(api_key=...)` first."
            )
        return client


def build_request(request: Request) -> dict[str, Any]:
    """Turn one `Request` into Messages API keyword arguments.

    Parameters the user left blank are omitted entirely, so the API's own
    defaults apply and the audit record shows what was actually asked for.
    """
    config: AnthropicModelConfig = request.model
    request_messages = [{"role": "user", "content": request.message.text}]
    payload: dict[str, Any] = {
        "model": config.name,
        "max_tokens": config.max_tokens,
        "messages": request_messages,
    }
    for key in ("temperature", "top_p", "top_k"):
        value = getattr(config, key)
        if value is not None:
            payload[key] = value
    if config.effort is not None:
        payload["output_config"] = {"effort": config.effort}
    if config.thinking.type is not None:
        payload["thinking"] = config.thinking.model_dump(exclude_none=True)
    if request.system_prompt:
        payload["system"] = request.system_prompt

    _merge(payload, config.extra)
    return payload


def _merge(payload: dict[str, Any], extra: dict[str, Any]) -> None:
    """Write `extra` into `payload`, filling in objects rather than replacing them.

    Validation has already refused any path that would touch a setting taxman
    names, so this only adds. Values are copied: every request in a run shares
    the audit's one `extra`, and none may write back into it.
    """
    for key, value in extra.items():
        if isinstance(value, dict) and value and isinstance(payload.get(key), dict):
            _merge(payload[key], value)
        else:
            payload[key] = copy.deepcopy(value)


def render_template() -> str:
    """Return the `model:` block for a new Anthropic audit.

    Every parameter appears with a comment saying what it does, blank apart from
    the model name and `max_tokens`, which Anthropic requires.
    """
    lines = [
        "model:",
        f"  name: {DEFAULT_MODEL}  # The model to send every message to.",
        f"  max_tokens: {DEFAULT_MAX_TOKENS}  # Required. Most tokens per response, thinking "
        "included; a response that reaches it stops mid-answer. Raise execution.timeout_s "
        "with it.",
    ]
    for key, comment in TEMPLATE_FIELDS:
        lines.append(f"  {key}:  # {comment}")
    lines += [
        "",
        "  # Thinking. Leave every key blank for the model's default, which varies by model.",
        "  thinking:",
    ]
    for key, comment in THINKING_TEMPLATE_FIELDS:
        lines.append(f"    {key}:  # {comment}")
    return "\n".join(lines) + "\n"


#: One client per run, so concurrent runs in one process never share a key.
_CLIENT: ContextVar[AsyncAnthropic | None] = ContextVar("ai_taxman_anthropic_client", default=None)


def _new_client(api_key: str | None) -> AsyncAnthropic:
    """Build the client from the key core resolved for this audit.

    Passing `api_key` explicitly also stops the SDK reading any credential from
    the environment - `ANTHROPIC_AUTH_TOKEN` included, which it would otherwise
    send as a second credential alongside the key.

    `max_retries=0` because ai-taxman runs its own retry loop; letting the SDK
    retry as well would multiply the attempts and make the `attempts` field in
    every record a lie.
    """
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise ProviderDependencyError(INSTALL_HINT) from exc

    return AsyncAnthropic(api_key=api_key, max_retries=0)


#: Keywords `messages.create` takes about the HTTP call rather than the request
#: body. A body field of the same name must not be mistaken for one of these.
_SDK_REQUEST_OPTIONS = frozenset({"extra_headers", "extra_query", "extra_body", "timeout"})


def _split_by_sdk_signature(
    payload: dict[str, Any], client: AsyncAnthropic
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate the parameters `messages.create` names from those it does not.

    The SDK refuses a keyword it has never heard of, but `extra:` exists for
    parameters newer than the installed SDK. Those travel in `extra_body`, which
    the SDK adds to the JSON body untouched.
    """
    known = set(inspect.signature(client.messages.create).parameters) - _SDK_REQUEST_OPTIONS
    named = {key: value for key, value in payload.items() if key in known}
    unnamed = {key: value for key, value in payload.items() if key not in known}
    return named, unnamed


def _is_anthropic_error(exc: BaseException) -> bool:
    """Confirm `exc` really came from the SDK, not a same-named class elsewhere."""
    return type(exc).__module__.split(".")[0] == "anthropic"


PROVIDER = AnthropicProvider()
