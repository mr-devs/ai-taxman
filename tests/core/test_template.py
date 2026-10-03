"""Rendering a settings model as the commented YAML a user edits.

Each setting documents itself on its own pydantic field, and the renderer writes
that down above the key. So what the file says a setting takes is read from the
same field that validates it, and the two cannot drift apart.
"""

from typing import Any, Literal

import pytest
import yaml
from pydantic import BaseModel, Field

from ai_taxman.core.template import render_block, render_setting


class Location(BaseModel):
    city: str | None = Field(default=None, description="A city to search from.")


class Settings(BaseModel):
    name: str = Field(description="The model to send every message to.")
    temperature: float | None = Field(default=None, description="How random sampling is.")
    location: Location | None = Field(default=None, description="Where to search from.")


def comment_above(lines, key):
    """The comment lines directly above `key:`, at any depth."""
    index = next(i for i, line in enumerate(lines) if line.lstrip().startswith(f"{key}:"))
    above = []
    for line in reversed(lines[:index]):
        if not line.lstrip().startswith("#"):
            break
        above.insert(0, line)
    return above


def test_each_setting_is_explained_by_the_comment_above_it():
    lines = render_block(Settings, values={"name": "gpt-5"})

    assert comment_above(lines, "name")[0] == "# The model to send every message to."
    assert comment_above(lines, "temperature")[0] == "# How random sampling is."


def test_a_given_value_is_written_and_the_rest_left_blank():
    lines = render_block(Settings, values={"name": "gpt-5"})

    assert "name: gpt-5" in lines
    assert "temperature:" in lines


def test_settings_are_separated_by_a_blank_line():
    lines = render_block(Settings, values={"name": "gpt-5"})

    assert lines[lines.index("name: gpt-5") + 1] == ""


def test_a_nested_block_is_indented_under_its_own_explanation():
    lines = render_block(Settings, values={"name": "gpt-5"})
    index = lines.index("location:")

    assert lines[index - 1] == "# Where to search from."
    assert lines[index + 1] == "  # A city to search from."
    assert "  city:" in lines[index:]


def test_a_block_can_be_indented_inside_another():
    lines = render_block(Settings, indent=2, values={"name": "gpt-5"})

    assert "  name: gpt-5" in lines
    assert "    city:" in lines


def test_a_value_inside_a_nested_block_is_given_by_a_nested_mapping():
    lines = render_block(Settings, values={"name": "gpt-5", "location": {"city": "Paris"}})

    assert "  city: Paris" in lines


def test_values_are_written_as_yaml_that_reads_back_the_same():
    lines = render_block(Settings, values={"name": "yes"})

    assert yaml.safe_load("\n".join(lines))["name"] == "yes"


def test_a_long_explanation_wraps_without_breaking_a_word():
    class Wordy(BaseModel):
        setting: int | None = Field(default=None, description="word " * 40)

    lines = comment_above(render_block(Wordy), "setting")

    assert len(lines) > 1
    assert all(len(line) <= 80 for line in lines)
    assert all(line.startswith("# ") for line in lines)


def test_no_line_ends_in_whitespace():
    lines = render_block(Settings, indent=2, values={"name": "gpt-5"})

    assert [line for line in lines if line != line.rstrip()] == []


def test_the_output_parses_back_to_the_settings_it_describes():
    parsed = yaml.safe_load("\n".join(render_block(Settings, values={"name": "gpt-5"})))

    assert parsed == {"name": "gpt-5", "temperature": None, "location": {"city": None}}


def test_a_setting_without_an_explanation_is_refused():
    """A key the user cannot read about is a key they will guess at."""

    class Undocumented(BaseModel):
        setting: int | None = None

    with pytest.raises(ValueError, match="setting"):
        render_block(Undocumented)


# --- what a setting takes ---------------------------------------------------


def label(lines, key, name):
    """The text after `name:` in the comment above `key`, or None."""
    for line in comment_above(lines, key):
        text = line.lstrip("# ")
        if text.startswith(f"{name}:"):
            return text.removeprefix(f"{name}:").strip()
    return None


def typed(annotation, **constraints):
    class One(BaseModel):
        setting: annotation = Field(default=None, description="A setting.", **constraints)

    return label(render_block(One), "setting", "Type")


@pytest.mark.parametrize(
    "annotation, constraints, expected",
    [
        (bool | None, {}, "true or false"),
        (str | None, {}, "text"),
        (int | None, {}, "whole number"),
        (float | None, {}, "number"),
        (int | None, {"ge": 1}, "whole number, 1 or more"),
        (float | None, {"gt": 0}, "number, more than 0"),
        (float | None, {"ge": 0, "le": 2}, "number, 0 to 2"),
        (float | None, {"le": 1.5}, "number, up to 1.5"),
        (list[str] | None, {}, "list of text"),
        (list[str] | None, {"max_length": 100}, "list of text, up to 100"),
        (list[int] | None, {"min_length": 1}, "list of whole numbers, at least 1"),
    ],
)
def test_the_type_says_what_the_setting_takes(annotation, constraints, expected):
    assert typed(annotation, **constraints) == expected


def test_the_type_line_follows_the_explanation():
    lines = comment_above(render_block(Settings, values={"name": "gpt-5"}), "temperature")

    assert lines[:2] == ["# How random sampling is.", "#   Type:     number"]


def test_a_nested_block_has_no_type_line():
    lines = comment_above(render_block(Settings, values={"name": "gpt-5"}), "location")

    assert lines == ["# Where to search from."]


# --- a fixed set of options -------------------------------------------------


def test_a_fixed_set_is_listed_as_options_instead_of_a_type():
    class One(BaseModel):
        setting: Literal["low", "medium", "high"] | None = Field(default=None, description="A.")

    lines = render_block(One)

    assert label(lines, "setting", "Options") == "low | medium | high"
    assert label(lines, "setting", "Type") is None


def test_each_option_can_say_what_it_means():
    class One(BaseModel):
        on_error: Literal["continue", "stop"] = Field(
            default="continue",
            description="What to do.",
            json_schema_extra={
                "options": {"continue": "keep going", "stop": "end the run"},
            },
        )

    lines = comment_above(render_block(One), "on_error")

    assert lines[1:3] == [
        "#   Options:  continue  keep going",
        "#             stop      end the run",
    ]


def test_options_are_listed_in_the_order_validation_declares_them():
    class One(BaseModel):
        # Values no other test uses: typing caches `X | None` across equal
        # Literals, and `Literal["b", "a"] == Literal["a", "b"]`.
        setting: Literal["zulu", "yankee"] | None = Field(
            default=None,
            description="A.",
            json_schema_extra={"options": {"yankee": "first", "zulu": "second"}},
        )

    lines = comment_above(render_block(One), "setting")

    assert lines[1].endswith("zulu    second")


def test_a_long_meaning_wraps_under_its_own_column():
    class One(BaseModel):
        setting: Literal["a", "b"] | None = Field(
            default=None,
            description="A.",
            json_schema_extra={"options": {"a": "word " * 30, "b": "short"}},
        )

    lines = comment_above(render_block(One), "setting")
    column = lines[1].index("word")

    assert all(len(line) <= 80 for line in lines)
    assert lines[2][:column].strip() == "#"
    assert lines[2][column:].startswith("word")


def test_meanings_must_cover_exactly_the_options_validation_takes():
    """A documented option the field rejects, or an accepted one left out, is a lie."""

    class One(BaseModel):
        setting: Literal["a", "b"] | None = Field(
            default=None, description="A.", json_schema_extra={"options": {"a": "only a"}}
        )

    with pytest.raises(ValueError, match="setting"):
        render_block(One)


def test_a_list_of_options_says_it_takes_a_list():
    class One(BaseModel):
        setting: list[Literal["text", "image"]] | None = Field(
            default=None, min_length=1, description="A."
        )

    lines = render_block(One)

    assert label(lines, "setting", "Options") == "text | image"
    assert label(lines, "setting", "Type") == "list of the options above, at least 1"


def test_an_unknown_documentation_key_is_refused():
    """A misspelt key would drop its text from the file without a word."""

    class One(BaseModel):
        setting: int | None = Field(
            default=None, description="A.", json_schema_extra={"optoins": {}}
        )

    with pytest.raises(ValueError, match="optoins"):
        render_block(One)


# --- what happens when it is left alone ------------------------------------


def defaulted(default=None, **field):
    class One(BaseModel):
        setting: Any = Field(default=default, description="A setting.", **field)

    return label(render_block(One), "setting", "Default")


def test_a_required_setting_says_so_instead_of_naming_a_default():
    lines = render_block(Settings, values={"name": "gpt-5"})

    assert label(lines, "name", "Required") == "yes"
    assert label(lines, "name", "Default") is None


def test_a_blank_setting_says_what_blank_means():
    assert defaulted(json_schema_extra={"blank": "no limit"}) == "blank (no limit)"


def test_a_blank_setting_with_nothing_more_to_say_is_called_blank():
    assert defaulted() == "blank"


@pytest.mark.parametrize(
    "default, written", [(False, "false"), (1, "1"), (120.0, "120"), ("continue", "continue")]
)
def test_a_default_is_named_as_it_would_be_written(default, written):
    assert defaulted(default) == written


def test_a_default_from_a_factory_is_named_too():
    class One(BaseModel):
        setting: list[str] = Field(default_factory=lambda: ["sources"], description="A.")

    assert label(render_block(One), "setting", "Default") == "[sources]"


def test_blank_is_explained_only_where_blank_is_the_default():
    """Blank means the default; a second explanation could only contradict it."""

    class One(BaseModel):
        setting: bool = Field(default=False, description="A.", json_schema_extra={"blank": "x"})

    with pytest.raises(ValueError, match="`setting` explains what blank means"):
        render_block(One)


def test_the_default_comes_after_the_type():
    lines = comment_above(render_block(Settings, values={"name": "gpt-5"}), "temperature")

    assert lines == [
        "# How random sampling is.",
        "#   Type:     number",
        "#   Default:  blank",
    ]


# --- writing the defaults in -------------------------------------------------


class Execution(BaseModel):
    repeats: int = Field(default=1, description="Times to send each message.")
    timeout_s: float = Field(default=120.0, description="Seconds to wait.")
    note: str | None = Field(default=None, description="A note.")
    inner: Location = Field(default_factory=Location, description="A block.")


def test_settings_are_left_blank_unless_asked_to_write_their_defaults():
    lines = render_block(Execution)

    assert "repeats:" in lines
    assert "timeout_s:" in lines


def test_defaults_can_be_written_in_as_the_values():
    lines = render_block(Execution, defaults=True)

    assert "repeats: 1" in lines
    assert "timeout_s: 120" in lines


def test_a_blank_default_stays_blank_when_defaults_are_written():
    assert "note:" in render_block(Execution, defaults=True)


def test_a_given_value_wins_over_the_default():
    assert "repeats: 3" in render_block(Execution, defaults=True, values={"repeats": 3})


def test_writing_defaults_reaches_into_nested_blocks():
    class Outer(BaseModel):
        execution: Execution = Field(default_factory=Execution, description="A block.")

    assert "  repeats: 1" in render_block(Outer, defaults=True)


# --- examples, links, and settings left out ----------------------------------


def test_an_example_is_written_as_the_user_would_write_it():
    class One(BaseModel):
        domains: list[str] | None = Field(
            default=None, description="A.", examples=[["cdc.gov", "who.int"]]
        )

    assert label(render_block(One), "domains", "Example") == "[cdc.gov, who.int]"


def test_further_examples_line_up_under_the_first():
    class One(BaseModel):
        country: str | None = Field(default=None, description="A.", examples=["US", "GB"])

    lines = comment_above(render_block(One), "country")

    assert lines[-2:] == ["#   Example:  US", "#             GB"]


def test_a_link_to_the_providers_documentation_comes_last():
    class One(BaseModel):
        temperature: float | None = Field(
            default=None,
            description="A.",
            examples=[0.2],
            json_schema_extra={"docs": "https://example.com/temperature"},
        )

    lines = comment_above(render_block(One), "temperature")

    assert lines[-2:] == ["#   Example:  0.2", "#   Docs:     https://example.com/temperature"]


def test_a_long_link_is_never_broken():
    url = "https://example.com/" + "a" * 100

    class One(BaseModel):
        setting: int | None = Field(default=None, description="A.", json_schema_extra={"docs": url})

    assert label(render_block(One), "setting", "Docs") == url


def test_a_block_can_link_to_its_documentation():
    class Outer(BaseModel):
        search: Location = Field(
            default_factory=Location,
            description="Web search.",
            json_schema_extra={"docs": "https://example.com/search"},
        )

    assert comment_above(render_block(Outer), "search") == [
        "# Web search.",
        "#   Docs:     https://example.com/search",
    ]


def test_a_setting_can_be_left_out_of_the_file():
    class One(BaseModel):
        shown: int | None = Field(default=None, description="A.")
        extra: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"template": False})

    assert not any(line.startswith("extra") for line in render_block(One))


def test_the_last_setting_is_not_followed_by_a_blank_line():
    class One(BaseModel):
        shown: int | None = Field(default=None, description="A.")
        extra: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"template": False})

    assert render_block(One)[-1] == "shown:"


# --- one setting on its own --------------------------------------------------


def test_one_setting_can_be_rendered_on_its_own():
    class Outer(BaseModel):
        execution: Execution = Field(default_factory=Execution, description="How it is sent.")

    lines = render_setting("execution", Outer.model_fields["execution"], defaults=True)

    assert lines[:2] == ["# How it is sent.", "execution:"]
    assert "  repeats: 1" in lines


def test_an_example_can_be_given_where_only_the_caller_knows_it():
    """The project's prompts folder, say: no field could know it in advance."""

    class One(BaseModel):
        path: str | None = Field(default=None, description="A.", examples=["a.txt"])

    lines = render_setting("path", One.model_fields["path"], example="prompts/neutral.txt")

    assert label(lines, "path", "Example") == "prompts/neutral.txt"


def test_a_setting_a_run_refuses_blank_says_it_is_required():
    """Validation may let it through blank while a run still stops without it."""

    class One(BaseModel):
        key_env: str | None = Field(
            default=None, description="A.", json_schema_extra={"required": True}
        )

    lines = render_block(One)

    assert label(lines, "key_env", "Required") == "yes"
    assert label(lines, "key_env", "Default") is None


def test_a_long_default_wraps_under_its_own_column():
    class One(BaseModel):
        setting: int | None = Field(
            default=None, description="A.", json_schema_extra={"blank": "word " * 20}
        )

    lines = comment_above(render_block(One, indent=4), "setting")
    default = next(i for i, line in enumerate(lines) if "Default:" in line)
    column = lines[default].index("blank")

    assert all(len(line) <= 80 for line in lines)
    assert lines[default + 1][:column].strip() == "#"
    assert lines[default + 1][column:].startswith("word")
