"""The pieces every provider builds its `model:` block from."""

from typing import Any

import pytest
from pydantic import Field, ValidationError

from ai_taxman.providers.settings import SettingsBlock, check_extra, merge_extra


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


# --- extra: ---------------------------------------------------------------

SET_BY_TAXMAN = frozenset({"model", "output_config.effort"})
NEVER_SENT = {"stream": "a stream is not a response"}


def check(extra):
    return check_extra(extra, set_by_taxman=SET_BY_TAXMAN, never_sent=NEVER_SENT)


def test_extra_may_add_anything_taxman_does_not_set():
    extra = {"service_tier": "flex", "output_config": {"format": {"type": "json_schema"}}}

    assert check(extra) == extra


@pytest.mark.parametrize(
    "extra",
    [{"model": "x"}, {"output_config": {"effort": "low"}}, {"output_config": "replaced"}],
    ids=["the same key", "inside it", "replacing what holds it"],
)
def test_extra_cannot_change_a_setting_taxman_sets(extra):
    """Overriding a named setting would bypass its checks, and its defaults."""
    with pytest.raises(ValueError, match="taxman sets"):
        check(extra)


def test_extra_cannot_set_what_taxman_never_sends():
    with pytest.raises(ValueError, match="cannot set stream: a stream is not a response"):
        check({"stream": True})


def test_merging_extra_fills_in_an_object_rather_than_replacing_it():
    payload = {"output_config": {"effort": "low"}}

    merge_extra(payload, {"output_config": {"format": {"type": "json_schema"}}})

    assert payload == {"output_config": {"effort": "low", "format": {"type": "json_schema"}}}


def test_merging_extra_copies_what_it_writes():
    """Every request in a run shares the audit's one `extra`; none may write back into it."""
    extra = {"metadata": {"user_id": "audit"}}
    payload: dict[str, Any] = {}

    merge_extra(payload, extra)
    payload["metadata"]["user_id"] = "changed"

    assert extra == {"metadata": {"user_id": "audit"}}
