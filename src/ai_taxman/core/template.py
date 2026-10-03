"""Rendering a settings model as the commented YAML a user edits.

Every setting documents itself on its own pydantic field, and `render_block`
writes that down above the key. What the file says a setting takes is read from
the field that validates it, so the two cannot drift apart. Core renders its own
blocks with this, and each provider renders its `model:` block with it.

A field documents itself with `description=`, which every rendered field must
have, and may say more in `json_schema_extra`:

- `options`: what each value of a `Literal` means, keyed by value. It must name
  exactly the values the field accepts.
- `blank`: what leaving the setting blank does, for a field whose default is
  None - "no limit", say, or "not sent, so the model's own default applies".
- `required`: True for a setting validation lets through blank but a run
  refuses, so the file says it is required rather than naming a default.
- `docs`: a link to the provider's documentation for the setting.
- `template`: False to leave the setting out of the file altogether.

pydantic's own `examples=` are written in as the user would type them.
"""

from __future__ import annotations

import re
import textwrap
import types
from collections.abc import Mapping
from typing import Any, Literal, Union, cast, get_args, get_origin

import yaml
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from ai_taxman.core.discovery import yaml_scalar

#: The widest a comment line gets, indentation included: the project's line length.
WIDTH = 100

#: Where one sentence of an explanation ends and the next begins.
SENTENCE_END = re.compile(r"(?<=\.)\s+")

#: What a value of each type is called, alone and in a list.
NOUNS: dict[type, tuple[str, str]] = {
    bool: ("boolean", "booleans"),
    str: ("string", "strings"),
    int: ("integer", "integers"),
    float: ("float", "floats"),
}

#: What a field may say about itself in `json_schema_extra`. See the module docstring.
DOC_KEYS = frozenset({"options", "blank", "required", "docs", "template"})


def render_block(
    model: type[BaseModel],
    *,
    indent: int = 0,
    values: Mapping[str, Any] | None = None,
    defaults: bool = False,
) -> list[str]:
    """Every setting in `model`, each under the comment that explains it.

    A setting is written with its value from `values`, or with its default when
    `defaults` is true, and left blank otherwise. A nested block takes its own
    values as a nested mapping.
    """
    values = values or {}
    settings = [
        render_setting(key, field, indent=indent, value=values.get(key), defaults=defaults)
        for key, field in model.model_fields.items()
        if _doc(key, field).get("template", True)
    ]
    return [line for i, setting in enumerate(settings) for line in ([""] if i else []) + setting]


def render_setting(
    key: str,
    field: FieldInfo,
    *,
    indent: int = 0,
    value: Any = None,
    defaults: bool = False,
    example: Any = None,
) -> list[str]:
    """One setting under the comment that explains it, as `render_block` writes each.

    `example` replaces the field's own examples, for one only the caller knows.
    """
    if not field.description:
        raise ValueError(f"`{key}` has no description to explain it in the audit file.")
    doc = _doc(key, field)
    pad = " " * indent
    lines = _wrap(field.description, pad)
    block = _block_model(field.annotation)
    if block is not None:
        inner = render_block(block, indent=indent + 2, values=value, defaults=defaults)
        return [*lines, *_docs_line(pad, doc), f"{pad}{key}:", *inner]
    allowed = _options(field)
    if allowed:
        lines += _option_lines(key, pad, allowed, doc.get("options"))
    kind = _describe_type(field)
    if kind:
        lines += _wrapped(pad, "Type", kind)
    lines += _wrapped(pad, *_default(key, field, doc))
    examples = [example] if example is not None else field.examples or []
    for i, shown in enumerate(examples):
        lines += _wrapped(pad, "" if i else "Example", _yaml_value(shown))
    lines += _docs_line(pad, doc)
    if value is None and defaults and not field.is_required():
        value = field.get_default(call_default_factory=True)
    written = _yaml_value(value)
    return [*lines, f"{pad}{key}: {written}" if written else f"{pad}{key}:"]


def _doc(key: str, field: FieldInfo) -> dict[str, Any]:
    """What the field says about itself beyond its description."""
    extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
    unknown = sorted(set(extra) - DOC_KEYS)
    if unknown:
        raise ValueError(f"`{key}` documents itself with unknown keys: {', '.join(unknown)}.")
    return extra


def _default(key: str, field: FieldInfo, doc: dict[str, Any]) -> tuple[str, str]:
    """What the setting is when the user leaves it alone, or that they cannot."""
    if field.is_required() or doc.get("required"):
        return "Required", "yes"
    blank = doc.get("blank")
    default = field.get_default(call_default_factory=True)
    if default is not None:
        if blank is not None:
            raise ValueError(
                f"`{key}` explains what blank means, but blank is its default, "
                f"{_yaml_value(default)}."
            )
        return "Default", _yaml_value(default)
    return "Default", f"blank ({blank})" if blank else "blank"


def _docs_line(pad: str, doc: dict[str, Any]) -> list[str]:
    """The link, whole: a URL broken across lines cannot be followed."""
    return [_label(pad, "Docs", doc["docs"])] if "docs" in doc else []


def _wrapped(pad: str, name: str, text: str) -> list[str]:
    """A labelled line, wrapped under its own column when it runs long."""
    return textwrap.wrap(
        text,
        WIDTH,
        initial_indent=_label(pad, name, ""),
        subsequent_indent=_label(pad, "", ""),
        break_on_hyphens=False,
        break_long_words=False,
    )


def _label(pad: str, name: str, text: str) -> str:
    """One labelled line, e.g. `#   Type:     number`. No name continues the one above."""
    heading = f"{name}:" if name else ""
    return f"{pad}#   {heading:<10}{text}"


def _options(field: FieldInfo) -> tuple[str, ...]:
    """The values a `Literal` setting, or a list of them, accepts, in declared order."""
    kind = _strip_none(field.annotation)
    if get_origin(kind) is list:
        (kind,) = get_args(kind)
    return get_args(kind) if get_origin(kind) is Literal else ()


def _option_lines(key: str, pad: str, allowed: tuple[str, ...], meanings: Any) -> list[str]:
    if meanings is None:
        return textwrap.wrap(
            " | ".join(allowed),
            WIDTH,
            initial_indent=_label(pad, "Options", ""),
            subsequent_indent=_label(pad, "", ""),
            break_on_hyphens=False,
        )
    meanings = cast(dict[str, str], meanings)
    if set(meanings) != set(allowed):
        raise ValueError(
            f"`{key}` explains the options {', '.join(sorted(meanings))}, "
            f"but accepts {', '.join(allowed)}."
        )
    width = max(len(option) for option in allowed) + 2
    lines = []
    for i, option in enumerate(allowed):
        lines += textwrap.wrap(
            meanings[option],
            WIDTH,
            initial_indent=_label(pad, "" if i else "Options", f"{option:<{width}}"),
            subsequent_indent=_label(pad, "", " " * width),
            break_on_hyphens=False,
        )
    return lines


def _describe_type(field: FieldInfo) -> str | None:
    """What the setting takes, with its bounds, e.g. `number, 0 to 2`.

    None for a single option from a fixed set: its options line says it all.
    """
    kind = _strip_none(field.annotation)
    if get_origin(kind) is list:
        (item,) = get_args(kind)
        if get_origin(item) is Literal:
            return f"list of the options above{_length_bounds(field)}"
        if item not in NOUNS:
            return None
        return f"list of {NOUNS[item][1]}{_length_bounds(field)}"
    if kind not in NOUNS:
        return None
    return f"{NOUNS[kind][0]}{_number_bounds(field)}"


def _number_bounds(field: FieldInfo) -> str:
    ge, gt = _constraint(field, "ge"), _constraint(field, "gt")
    le, lt = _constraint(field, "le"), _constraint(field, "lt")
    if ge is not None and le is not None:
        return f", {_number(ge)} to {_number(le)}"
    parts = [
        *([f"{_number(ge)} or more"] if ge is not None else []),
        *([f"more than {_number(gt)}"] if gt is not None else []),
        *([f"up to {_number(le)}"] if le is not None else []),
        *([f"less than {_number(lt)}"] if lt is not None else []),
    ]
    return "".join(f", {part}" for part in parts)


def _length_bounds(field: FieldInfo) -> str:
    least, most = _constraint(field, "min_length"), _constraint(field, "max_length")
    if least is not None and most is not None:
        return f", {least} to {most}"
    if least is not None:
        return f", at least {least}"
    if most is not None:
        return f", up to {most}"
    return ""


def _constraint(field: FieldInfo, name: str) -> Any:
    """The bound `Field(ge=1)` and the like set, by that name, or None without one."""
    return next((getattr(c, name) for c in field.metadata if hasattr(c, name)), None)


def _number(value: float) -> str:
    """`2.0` as `2`: a bound reads as the user would write it."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _wrap(text: str, pad: str) -> list[str]:
    """An explanation as comment lines, each sentence starting a line of its own."""
    prefix = f"{pad}# "
    return [
        line
        for sentence in SENTENCE_END.split(text.strip())
        for line in textwrap.wrap(
            sentence, WIDTH, initial_indent=prefix, subsequent_indent=prefix, break_on_hyphens=False
        )
    ]


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
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # `120`, as the user would write it, not `120.0`
    text = yaml.safe_dump(value, default_flow_style=True, width=float("inf"))
    return text.removesuffix("\n...\n").rstrip("\n")
