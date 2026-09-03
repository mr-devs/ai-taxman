"""Finding the API key an audit asks for.

Every audit names exactly one environment variable in its `api_key_env:` field,
and that is the only name taxman ever looks up. There is no chain of fallback
variable names to reason about, and no store of taxman's own: if the field is
missing, or the variable it names holds nothing, the run stops with an error
saying so.

Exporting the variable is the user's job - their shell profile, `direnv`, or a
CI secret. taxman never writes a key to disk, so there is no file to protect and
no copy to go stale.
"""

from __future__ import annotations

import os

from ai_taxman.core.errors import MissingApiKeyError


def resolve_api_key(env_name: str) -> str:
    """Return the API key held by `env_name`.

    Raises `MissingApiKeyError` naming the variable if it holds nothing.
    """
    value = os.environ.get(env_name)
    if value and value.strip():
        return value

    raise MissingApiKeyError(
        f"{env_name} is not set. Export it in your shell, or edit the audit's "
        "`api_key_env:` field to name a variable that holds your key."
    )
