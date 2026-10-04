"""The pieces every provider builds its `model:` block from."""

from typing import Any

import pytest
from pydantic import Field, ValidationError

from ai_taxman.providers.settings import SettingsBlock


class Location(SettingsBlock):
    city: str | None = None


class Settings(SettingsBlock):
    temperature: float = 1.0
    location: Location = Field(default_factory=Location)
    extra: dict[str, Any] = Field(default_factory=dict)


def test_a_setting_left_blank_takes_its_default():
    """`taxman audits new` writes keys with no value, and YAML reads those as None."""
    assert Settings(temperature=None).temperature == 1.0


def test_a_nested_block_left_wholly_blank_takes_its_default():
    assert Settings(location={"city": None}).location == Location()


def test_extra_keeps_the_nulls_written_in_it():
    """`extra:` is sent as written, so only a wholly blank one is dropped."""
    assert Settings(extra={"service_tier": None}).extra == {"service_tier": None}
    assert Settings(extra=None).extra == {}


def test_an_unknown_setting_is_refused():
    with pytest.raises(ValidationError, match="tempreature"):
        Settings(tempreature=0.5)
