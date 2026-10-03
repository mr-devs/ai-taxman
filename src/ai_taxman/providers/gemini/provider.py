"""The Gemini provider.

Nothing outside this package imports the `google-genai` SDK, and the SDK itself is
imported lazily inside `_new_client()` so that listing providers, generating a
template, or running another provider's audit all work with it uninstalled.

Requests go through the Interactions API (`POST /v1beta/interactions`), Google's
recommended API for new work.

Split, per the project's provider rules:
  pure  - `build_request`, `validate_model_config`, `render_template`
  I/O   - `send`, `startup`, `shutdown`

Nothing here reads a response. `send` returns the JSON body Google sent, and the
runner writes it verbatim.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from ai_taxman.core.errors import ProviderDependencyError, ProviderError
from ai_taxman.providers.base import Provider, Request
from ai_taxman.providers.gemini.config import TEMPLATE_FIELDS, GeminiModelConfig
from ai_taxman.providers.gemini.models import DEFAULT_MODEL, KNOWN_MODELS

if TYPE_CHECKING:
    from google.genai import Client

INSTALL_HINT = (
    "The Gemini SDK is not installed. Add it with `uv add ai-taxman[gemini]` "
    "(or `uv sync --all-extras` when working on ai-taxman itself)."
)

#: SDK error class names worth retrying. Matched by name so this module still
#: imports cleanly when the SDK is absent. The Interactions client raises these
#: from `google.genai._gaos.lib.compat_errors`; every 5xx is an `InternalServerError`.
RETRYABLE_ERRORS = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "ConflictError",
        "InternalServerError",
        "RateLimitError",
    }
)


class GeminiProvider(Provider):
    """Audit Gemini models through the Interactions API."""

    name = "gemini"
    supports_batch = False
    default_api_key_env = "GEMINI_API_KEY"

    # -- pure ---------------------------------------------------------------

    def known_models(self) -> list[str]:
        return list(KNOWN_MODELS)

    def validate_model_config(self, block: dict[str, Any]) -> GeminiModelConfig:
        return GeminiModelConfig(**block)

    def render_template(self) -> str:
        return render_template()

    def describe_model(self, model: GeminiModelConfig) -> str:
        return model.name

    def is_retryable(self, exc: BaseException) -> bool:
        return type(exc).__name__ in RETRYABLE_ERRORS and _is_genai_error(exc)

    # -- I/O ----------------------------------------------------------------

    async def send(self, request: Request, *, timeout_s: float) -> dict[str, Any]:
        client = self._require_client()
        body = build_request(request)
        # Everything but model and input travels as `extra_body`, which the SDK
        # adds to the JSON untouched. Passed as keywords, it would be run through
        # the SDK's own models, which silently drop any field they do not know.
        response = await client.aio.interactions.with_raw_response.create(
            model=body.pop("model"),
            input=body.pop("input"),
            extra_body=body,
            timeout=timeout_s,
        )
        # The JSON Google sent. The SDK's parsed object adds conveniences of its
        # own (`output_text`), so it is not the response as it came back.
        return await response.json()  # type: ignore[no-any-return]

    async def startup(self, *, api_key: str | None = None) -> None:
        _CLIENT.set(_new_client(api_key))

    async def shutdown(self) -> None:
        client = _CLIENT.get()
        if client is not None:
            await client.aio.aclose()
            client.close()
            _CLIENT.set(None)

    def _require_client(self) -> Client:
        """The client `startup()` built, never one improvised here.

        Building a client without a key would let the SDK fall back to whatever
        `GEMINI_API_KEY` or `GOOGLE_API_KEY` happens to be exported - the fallback
        chain the project forbids, and one that could silently bill a key the
        audit never named.
        """
        client = _CLIENT.get()
        if client is None:
            raise ProviderError(
                "The gemini provider was used before startup(), so no API key was supplied. "
                "Run the audit through `taxman collect`, or call `await provider.startup"
                "(api_key=...)` first."
            )
        return client


def build_request(request: Request) -> dict[str, Any]:
    """Turn one `Request` into the Interactions API's JSON body.

    Parameters the user left blank are omitted entirely, so the API's own
    defaults apply and the audit record shows what was actually asked for.
    """
    config: GeminiModelConfig = request.model
    payload: dict[str, Any] = {
        "model": config.name,
        "input": request.message.text,
        "store": config.store,
    }
    if request.system_prompt:
        payload["system_instruction"] = request.system_prompt
    return payload


def render_template() -> str:
    """Return the `model:` block for a new Gemini audit.

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
_CLIENT: ContextVar[Client | None] = ContextVar("ai_taxman_gemini_client", default=None)


def _new_client(api_key: str | None) -> Client:
    """Build the client from the key core resolved for this audit.

    `enterprise=False` keeps the audit on the Gemini Developer API whatever
    `GOOGLE_GENAI_USE_ENTERPRISE` or `GOOGLE_GENAI_USE_VERTEXAI` say; an explicit
    `api_key` already wins over `GEMINI_API_KEY` and `GOOGLE_API_KEY`.

    The Interactions client retries by default, and no `HttpRetryOptions` value
    turns that off: the parent client raises `attempts=0` to 1, which this client
    reads as one *retry*. Its own `retry_config = None` is what sends exactly one
    request. ai-taxman runs its own retry loop; letting the SDK retry as well
    would multiply the attempts and make the `attempts` field in every record a lie.
    """
    try:
        from google import genai
    except ImportError as exc:
        raise ProviderDependencyError(INSTALL_HINT) from exc

    client = genai.Client(api_key=api_key, enterprise=False)
    client.aio.interactions.sdk_configuration.retry_config = None
    return client


def _is_genai_error(exc: BaseException) -> bool:
    """Confirm `exc` really came from the SDK, not a same-named class elsewhere.

    `google` is a namespace many packages share, so the check is on `google.genai`.
    """
    return type(exc).__module__.split(".")[:2] == ["google", "genai"]


PROVIDER = GeminiProvider()
