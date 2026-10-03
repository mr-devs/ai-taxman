"""Rendering a settings model as the commented YAML a user edits.

Every setting documents itself on its own pydantic field, and `render_block`
writes that down above the key. What the file says a setting takes is read from
the field that validates it, so the two cannot drift apart. Core renders its own
blocks with this, and each provider renders its `model:` block with it.

A field documents itself with `description=`, which every rendered field must
have.
"""

from __future__ import annotations

import textwrap
import types
from collections.abc import Mapping
from typing import Any, Union, get_args, get_origin

import yaml
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from ai_taxman.core.discovery import yaml_scalar

#: The widest a comment line gets, indentation included.
WIDTH = 80


def render_block(
    model: type[BaseModel],
    *,
    indent: int = 0,
    values: Mapping[str, Any] | None = None,
) -> list[str]:
    """Every setting in `model`, each under the comment that explains it.

    A setting is written with its value from `values` and left blank otherwise.
    A nested block takes its own values as a nested mapping.
    """
    values = values or {}
    settings = [
        _render_setting(key, field, indent=indent, value=values.get(key))
        for key, field in model.model_fields.items()
    ]
    return [line for i, setting in enumerate(settings) for line in ([""] if i else []) + setting]


def _render_setting(key: str, field: FieldInfo, *, indent: int, value: Any) -> list[str]:
    if not field.description:
        raise ValueError(f"`{key}` has no description to explain it in the audit file.")
    pad = " " * indent
    lines = _wrap(field.description, pad)
    block = _block_model(field.annotation)
    if block is not None:
        return [*lines, f"{pad}{key}:", *render_block(block, indent=indent + 2, values=value)]
    written = _yaml_value(value)
    return [*lines, f"{pad}{key}: {written}" if written else f"{pad}{key}:"]


def _wrap(text: str, pad: str) -> list[str]:
    prefix = f"{pad}# "
    return textwrap.wrap(
        text, WIDTH, initial_indent=prefix, subsequent_indent=prefix, break_on_hyphens=False
    )


def _block_model(annotation: Any) -> type[BaseModel] | None:
    """The settings model a field nests, if it is a block rather than a value."""
    inner = _strip_none(annotation)
    return inner if isinstance(inner, type) and issubclass(inner, BaseModel) else None


def _strip_none(annotation: Any) -> Any:
    """`X | None` as `X`: a blank setting is always allowed, so None says nothing."""
    if get_origin(annotation) in (Union, types.UnionType):
        rest = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(rest) == 1:
            return rest[0]
    return annotation


def _yaml_value(value: Any) -> str:
    """`value` as YAML, or "" for a blank."""
    if value is None:
        return ""
    if isinstance(value, str):
        return yaml_scalar(value)
    text = yaml.safe_dump(value, default_flow_style=True, width=float("inf"))
    return text.removesuffix("\n...\n").rstrip("\n")
