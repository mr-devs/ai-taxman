"""The one contract every provider implements.

This module is the *only* thing providers share. Anything that would need to be
added here to make a single provider work probably belongs inside that
provider's own package instead — the point of this seam is that adding or
breaking one provider cannot touch another.

Each provider splits into pure functions (`validate_model_config`,
`render_template`, `build_request`) that are unit-tested against recorded
fixtures, and one thin I/O method (`send`) that touches the network.

Note what is *not* here: nothing parses a response. `send` returns the provider's
answer as a JSON-safe dict and the runner writes it verbatim. Deriving text,
token counts, or citations from that is a separate tool's job, working from the
data on disk.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ai_taxman.core.messages import Message


@dataclass(frozen=True, slots=True)
class Request:
    """One message, on one repeat, ready to send."""

    message: Message
    repeat: int
    #: The audit's validated `model:` block, as returned by `validate_model_config`.
    model: Any
    #: The audit's system prompt text, or None. Each provider sends it the way its
    #: own API takes one.
    system_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class BatchHandle:
    """A submitted batch job, as the provider identifies it."""

    id: str
    provider: str
    details: dict[str, Any] = field(default_factory=dict)


class Provider(ABC):
    """Implement this, export it as `PROVIDER`, and the registry will find it."""

    #: The name used in `provider:` and on the command line. Lowercase.
    name: str

    #: Whether the batch methods below are implemented.
    supports_batch: bool = False

    #: Whether audits for this provider need an API key at all.
    requires_api_key: bool = True

    #: The variable this provider's own SDK conventionally reads, e.g.
    #: "OPENAI_API_KEY". A documentation hint only: `taxman audits new` names it in a
    #: comment so the user knows what to put in `api_key_env:`. Core never
    #: resolves a key from it - the audit's own field is the only name read.
    default_api_key_env: str = ""

    def describe_model(self, model: Any) -> str:
        """The model name to record, given this provider's validated config.

        Core needs a name for every response record and manifest but must not
        assume the `model:` block has a `name` key - that is this provider's
        business, and another provider may call it something else entirely.
        """
        return str(getattr(model, "name", "") or "")

    # -- pure ---------------------------------------------------------------

    @abstractmethod
    def known_models(self) -> list[str]:
        """Model names offered by `taxman audits new` and shell completion.

        A best-effort list for convenience — an unknown model is still allowed
        through, because providers ship new ones faster than we can.
        """

    @abstractmethod
    def validate_model_config(self, block: dict[str, Any]) -> Any:
        """Validate the audit's `model:` block and return the provider's own config.

        Raise `ValueError` (or a pydantic `ValidationError`) to reject it; the
        CLI turns that into a `ConfigError` naming the file.
        """

    @abstractmethod
    def render_template(self) -> str:
        """Return the YAML text of the `model:` block for `taxman audits new`.

        Every parameter this provider accepts should be present but blank, under
        the comment `core.template.render_block` writes from its own field, so the
        scaffolded file doubles as the documentation the user edits. There are no
        saved values to merge in: `audits new` writes defaults, and the user fills
        in the rest by hand.
        """

    # -- I/O ----------------------------------------------------------------

    @abstractmethod
    async def send(self, request: Request, *, timeout_s: float) -> dict[str, Any]:
        """Send one request and return the raw response as a JSON-safe dict."""

    async def startup(self, *, api_key: str | None = None) -> None:  # noqa: B027
        """Called once before a run, with the key the audit's variable held.

        Core resolves the key - it owns "read the variable this audit names" -
        and hands over the value. `api_key` is None only when
        `requires_api_key` is False.
        """

    async def shutdown(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Called once after a run, even if it failed. Close clients here."""

    def is_retryable(self, exc: BaseException) -> bool:
        """Whether `exc` from `send` is transient and worth retrying.

        Providers know their own SDK's exception types; core does not, so the
        decision lives here. Defaults to "no" so a new provider fails loudly
        rather than hammering an endpoint.
        """
        return False

    # -- batch (opt-in) -----------------------------------------------------

    async def submit_batch(self, requests: list[Request], *, timeout_s: float) -> BatchHandle:
        """Submit many requests to the provider's batch endpoint."""
        raise NotImplementedError(self._no_batch())

    async def poll_batch(self, handle: BatchHandle, *, timeout_s: float) -> str:
        """Return the batch's status: one of `pending`, `done`, `failed`."""
        raise NotImplementedError(self._no_batch())

    async def fetch_batch(
        self, handle: BatchHandle, *, timeout_s: float
    ) -> list[tuple[str, dict[str, Any]]]:
        """Return `(custom_id, raw_response)` pairs for a finished batch."""
        raise NotImplementedError(self._no_batch())

    def _no_batch(self) -> str:
        return (
            f"The {self.name!r} provider does not support batch mode. "
            "Set `execution.batch: false` in the audit."
        )
