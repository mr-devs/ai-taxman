"""Shell completion for provider, model, and audit names.

Completers run on every Tab press, so they must be fast and must never import a
provider SDK. Provider and audit names both come from metadata and filenames;
model names are the one case that loads a provider module, and only once the
user has already named it.

Nothing here may raise: a completer that throws breaks the user's shell prompt.
"""

from __future__ import annotations

from typing import Any

from ai_taxman.core.discovery import list_audits
from ai_taxman.core.registry import available_providers, get_provider


def complete_provider(incomplete: str) -> list[str]:
    """Provider names starting with `incomplete`."""
    try:
        return [name for name in available_providers() if name.startswith(incomplete)]
    except Exception:  # noqa: BLE001 - a broken completer breaks the user's shell
        return []


def complete_audit(incomplete: str) -> list[str]:
    """Audit names visible from here, local and global."""
    try:
        return [ref.name for ref in list_audits() if ref.name.startswith(incomplete)]
    except Exception:  # noqa: BLE001
        return []


def complete_model(ctx: Any, incomplete: str) -> list[str]:
    """Model names for whichever provider is already on the command line."""
    try:
        provider_name = ctx.params.get("provider")
        if not provider_name:
            return []
        models = get_provider(provider_name).known_models()
        return [name for name in models if name.startswith(incomplete)]
    except Exception:  # noqa: BLE001
        return []
