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
# This is an `ai-taxman` audit file.
#
# Collect data based on this audit file by running:
#   taxman collect {audit}
"""


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
    # Here, not at the top: they import pydantic, which completion must not pay for.
    from ai_taxman.core.config import AuditConfig
    from ai_taxman.core.template import render_setting

    fields = AuditConfig.model_fields
    settings = [
        render_setting("audit", fields["audit"], value=audit),
        render_setting("provider", fields["provider"], value=provider.name),
        *(
            [
                render_setting(
                    "api_key_env",
                    fields["api_key_env"],
                    value=api_key_env,
                    example=provider.default_api_key_env or None,
                )
            ]
            if api_key_env
            else []
        ),
        render_setting("messages", fields["messages"], value=messages),
        render_setting("system_prompt", fields["system_prompt"], example=f"{prompts}/neutral.txt"),
        render_setting(
            "output", fields["output"], value={"dir": output_dir, "log_dir": log_dir}, defaults=True
        ),
        render_setting("execution", fields["execution"], defaults=True),
    ]
    lines = [
        HEADER.format(audit=audit),
        *(line for i, setting in enumerate(settings) for line in ([""] if i else []) + setting),
        "",
        f"# Settings below are specific to the {provider.name!r} provider.",
        provider.render_template().rstrip(),
        "",
    ]
    return "\n".join(lines)
