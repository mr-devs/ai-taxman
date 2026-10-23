"""Shell completion for provider and audit names.

Completers run on every Tab press, so they must be fast and must never import a
provider SDK. Provider and audit names both come from metadata and filenames.

Nothing here may raise: a completer that throws breaks the user's shell prompt.
"""

from __future__ import annotations

from ai_taxman.core.discovery import list_audits
from ai_taxman.core.registry import available_providers


def complete_provider(incomplete: str) -> list[str]:
    """Provider names starting with `incomplete`."""
    try:
        return [name for name in available_providers() if name.startswith(incomplete)]
    except Exception:  # noqa: BLE001 - a broken completer breaks the user's shell
        return []


def complete_audit(incomplete: str) -> list[str]:
    """Audit names in the project the shell is standing in.

    Outside a project there is nothing to complete, and the blanket `except`
    below is what turns that into an empty list rather than a broken prompt.
    """
    try:
        return [ref.name for ref in list_audits() if ref.name.startswith(incomplete)]
    except Exception:  # noqa: BLE001
        return []
