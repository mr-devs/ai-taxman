"""The pieces every provider builds its `model:` block from.

Shared, like `base.py`, but kept apart from it: `base.py` is imported on every
shell completion, and this module needs pydantic, which a completion must not
pay for. Only a provider's own config and request code imports it, and the
registry imports those lazily.
"""

from __future__ import annotations

import copy
from collections.abc import Collection, Iterator, Mapping
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


def check_extra(
    extra: dict[str, Any], *, set_by_taxman: Collection[str], never_sent: Mapping[str, str]
) -> dict[str, Any]:
    """Return `extra` if it only adds to the request, and refuse it otherwise.

    `set_by_taxman` holds the dotted paths `build_request` writes, which `extra`
    may neither override nor reach inside; `never_sent` maps each key `send`
    could not record a response to onto the reason why.
    """
    paths = list(_leaf_paths(extra))
    for path in paths:
        if path in never_sent:
            raise ValueError(f"`extra:` cannot set {path}: {never_sent[path]}.")
    taken = sorted({key for key in set_by_taxman for path in paths if _overlaps(path, key)})
    if taken:
        raise ValueError(
            f"`extra:` cannot set {', '.join(taken)}: taxman sets "
            f"{'it' if len(taken) == 1 else 'them'} from this audit. Use the named "
            "setting in `model:` instead (the system prompt is the audit's "
            "`system_prompt:`)."
        )
    return extra


def merge_extra(payload: dict[str, Any], extra: dict[str, Any]) -> None:
    """Write `extra` into `payload`, filling in objects rather than replacing them.

    `check_extra` has already refused any path that would touch a setting taxman
    names, so this only adds. Values are copied: every request in a run shares
    the audit's one `extra`, and none may write back into it.
    """
    for key, value in extra.items():
        if isinstance(value, dict) and value and isinstance(payload.get(key), dict):
            merge_extra(payload[key], value)
        else:
            payload[key] = copy.deepcopy(value)


def _leaf_paths(block: dict[str, Any], prefix: str = "") -> Iterator[str]:
    """Every dotted path in `block` that ends in a value rather than a further object."""
    for key, value in block.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            yield from _leaf_paths(value, f"{path}.")
        else:
            yield path


def _overlaps(path: str, key: str) -> bool:
    """Whether writing `path` would change `key`: the same, inside it, or replacing it."""
    return path == key or path.startswith(f"{key}.") or key.startswith(f"{path}.")
