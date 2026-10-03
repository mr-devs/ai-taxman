"""Generating the audit YAML that `taxman audits new` writes.

The file is written as text, not dumped from a dict, so it can carry the
comments that make it self-documenting. Core owns everything above the `model:`
block; the provider renders that block itself.

Every value is a default and the file is the only place any of them is chosen.
`taxman audits new` accepts no settings and there is no configuration command, so
there is nothing to route or merge here.

The `api_key_env:` field is the one thing taxman cannot fill in: it names the
environment variable holding the key, which only the user knows. It is written
as a placeholder, with the provider's conventional variable named in the comment
above it as a hint.
"""

from __future__ import annotations

from ai_taxman.providers.base import Provider

#: What `api_key_env:` says until the user replaces it. Deliberately not a valid
#: variable name, so an unedited audit fails loudly instead of reading something.
API_KEY_ENV_PLACEHOLDER = "<insert_api_key_env_var_here>"

HEADER = """\
# {audit} - an ai-taxman audit.
#
# Run it with:  taxman collect {audit}
# Messages are read one per line from the file below; blank lines and lines
# starting with `#` are ignored.
"""

EXECUTION = {
    "repeats": ("1", "Times to send EACH message. All repeats go out together."),
    "max_concurrency": ("8", "Requests in flight at once."),
    "batch": ("false", "true to use the provider's batch API (cheaper, much slower)."),
    "timeout_s": ("120", "Per-request timeout in seconds."),
    "max_retries": ("5", "Retries for transient failures (rate limits, timeouts)."),
    "on_error": ("continue", "continue | stop"),
    "shuffle": ("false", "true to randomise dispatch order."),
}


def render_audit(
    *,
    audit: str,
    provider: Provider,
    messages: str,
    output_dir: str,
    log_dir: str,
    prompts: str,
    api_key_env: str | None = None,
) -> str:
    """Return the full text of a new audit file.

    `api_key_env` is omitted entirely for a provider that needs no key.
    """
    output = {
        "dir": (output_dir, "{audit} and {run_id} are filled in at run time."),
        "filename": ("responses.jsonl", "Raw responses, one JSON object per line."),
        "compress": ("false", "true to gzip the output."),
        "log_dir": (log_dir, "Each run's log is written here as <run_id>.log."),
    }

    lines = [
        HEADER.format(audit=audit),
        f"audit: {audit}",
        f"provider: {provider.name}",
        f"messages: {messages}",
        "",
        "# A file of system prompt text sent with every message, e.g.",
        f"# {prompts}/neutral.txt. Leave blank to send none.",
        "system_prompt:",
        *(_api_key_block(provider, api_key_env) if api_key_env else []),
        "",
        "output:",
        *_block(output),
        "",
        "execution:",
        *_block(EXECUTION),
        "",
        f"# Settings below are specific to the {provider.name!r} provider.",
        provider.render_template().rstrip(),
        "",
    ]
    return "\n".join(lines)


def _api_key_block(provider: Provider, api_key_env: str) -> list[str]:
    """The `api_key_env:` field, with instructions the user needs at that moment."""
    lines = [
        "",
        "# The one environment variable holding this audit's API key.",
        "# taxman reads this name and no other. Export the variable in your",
        "# shell, then put its name here.",
    ]
    if provider.default_api_key_env:
        lines.append(f"# For {provider.name} this is usually {provider.default_api_key_env}.")
    lines.append(f"api_key_env: {api_key_env}")
    return lines


def _block(fields: dict[str, tuple[str, str]]) -> list[str]:
    return [f"  {key}: {default}  # {comment}" for key, (default, comment) in fields.items()]
