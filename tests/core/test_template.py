"""Rendering a settings model as the commented YAML a user edits.

Each setting documents itself on its own pydantic field, and the renderer writes
that down above the key. So what the file says a setting takes is read from the
same field that validates it, and the two cannot drift apart.
"""

import pytest
import yaml
from pydantic import BaseModel, Field

from ai_taxman.core.template import render_block


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

    assert lines == ["# How random sampling is.", "#   Type:     number"]


def test_a_nested_block_has_no_type_line():
    lines = comment_above(render_block(Settings, values={"name": "gpt-5"}), "location")

    assert lines == ["# Where to search from."]
