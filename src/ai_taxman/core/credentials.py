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
import re

from ai_taxman.core.errors import MissingApiKeyError

#: What `api_key_env:` says until the user replaces it. Deliberately not a valid
#: variable name, so an unedited audit fails loudly instead of reading something.
API_KEY_ENV_PLACEHOLDER = "<insert_api_key_env_var_here>"

#: A variable name as they are written: capitals, digits and underscores.
#: Anything else in `api_key_env:` is most often the key itself, pasted in - and
#: every provider's keys have a lowercase letter or a `-`, even the Google keys a
#: shell would take for a name.
VARIABLE_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def resolve_api_key(env_name: str) -> str:
    """Return the API key held by `env_name`.

    Raises `MissingApiKeyError` naming the variable if it holds nothing.
    """
    value = os.environ.get(env_name)
    if value and value.strip():
        return value

    if value is None:
        raise MissingApiKeyError(
            f"{env_name} is not set in this shell. Export it (`export {env_name}=...`) "
            "and run again, or edit the audit's `api_key_env:` field to name a variable "
            "that is set."
        )
    raise MissingApiKeyError(
        f"{env_name} is set but empty. Put your key in it (`export {env_name}=...`) and run again."
    )
