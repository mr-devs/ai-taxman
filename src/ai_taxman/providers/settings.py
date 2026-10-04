"""The pieces every provider builds its `model:` block from.

Shared, like `base.py`, but kept apart from it: `base.py` is imported on every
shell completion, and this module needs pydantic, which a completion must not
pay for. Only a provider's own config and request code imports it, and the
registry imports those lazily.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class SettingsBlock(BaseModel):
    """A block of settings where a key left blank takes its default."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, block: Any) -> Any:
        """`taxman audits new` writes keys with no value; YAML reads those as None.

        Dropping them here lets the field defaults apply, so a freshly generated
        template validates as-is.
        """
        if isinstance(block, dict):
            return {
                key: value
                for key, value in block.items()
                # `extra:` is sent as written, so only a wholly blank one is dropped.
                if not (value is None if key == "extra" else _blank(value))
            }
        return block


def _blank(value: Any) -> bool:
    """None, or a nested block whose every key was left blank."""
    if isinstance(value, dict):
        return all(_blank(inner) for inner in value.values())
    return value is None
