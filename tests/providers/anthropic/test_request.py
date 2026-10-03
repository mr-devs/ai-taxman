"""Turning an audit's `model:` block into Messages API keyword arguments.

Pure function, no network.
"""

from typing import get_args

import pytest
from pydantic import ValidationError

from ai_taxman.core.messages import Message
from ai_taxman.providers.anthropic.models import EFFORTS, Effort
from ai_taxman.providers.anthropic.provider import AnthropicProvider, build_request
from ai_taxman.providers.base import Request

BASE = {"name": "claude-opus-5-5", "max_tokens": 1024}


def request_for(block, text="hello", system_prompt=None):
    config = AnthropicProvider().validate_model_config(block)
    message = Message(id="m0000", text=text, hash="sha256:x", line_number=1)
    return build_request(
        Request(message=message, repeat=0, model=config, system_prompt=system_prompt)
    )


def test_sends_the_message_as_one_user_turn():
    assert request_for(BASE, text="hi there")["messages"] == [
        {"role": "user", "content": "hi there"}
    ]


def test_sends_the_model_name():
    assert request_for(BASE)["model"] == "claude-opus-5-5"


def test_sends_max_tokens():
    assert request_for(BASE)["max_tokens"] == 1024


def test_omits_parameters_the_user_left_blank():
    assert set(request_for(BASE)) == {"model", "max_tokens", "messages"}


def test_model_name_is_required():
    with pytest.raises(ValidationError, match="name"):
        request_for({"max_tokens": 1024})


def test_max_tokens_is_required_because_anthropic_has_no_default():
    with pytest.raises(ValidationError, match="max_tokens"):
        request_for({"name": "claude-opus-5-5"})


def test_a_blank_max_tokens_is_still_missing():
    with pytest.raises(ValidationError, match="max_tokens"):
        request_for({"name": "claude-opus-5-5", "max_tokens": None})


def test_max_tokens_must_be_positive():
    with pytest.raises(ValidationError, match="max_tokens"):
        request_for({"name": "claude-opus-5-5", "max_tokens": 0})


def test_an_unknown_model_name_is_still_allowed():
    """Anthropic ships models faster than the known list is updated."""
    assert request_for({**BASE, "name": "claude-future-9"})["model"] == "claude-future-9"


def test_unknown_model_keys_are_rejected():
    with pytest.raises(ValidationError, match="bogus"):
        request_for({**BASE, "bogus": 1})


def test_the_system_prompt_becomes_the_top_level_system():
    """Anthropic takes it as a parameter, not as a turn in `messages`."""
    payload = request_for(BASE, system_prompt="Answer in one word.")

    assert payload["system"] == "Answer in one word."
    assert all(turn["role"] == "user" for turn in payload["messages"])


def test_no_system_prompt_sends_no_system():
    assert "system" not in request_for(BASE)


def test_effort_goes_in_output_config():
    assert request_for({**BASE, "effort": "low"})["output_config"] == {"effort": "low"}


@pytest.mark.parametrize("effort", EFFORTS)
def test_every_documented_effort_level_is_accepted(effort):
    """Which model takes which level is the API's call; taxman only checks the name."""
    assert request_for({**BASE, "effort": effort})["output_config"]["effort"] == effort


def test_adaptive_is_a_thinking_mode_not_an_effort_level():
    with pytest.raises(ValidationError, match="effort"):
        request_for({**BASE, "effort": "adaptive"})


def test_the_effort_list_and_the_validated_type_cannot_drift():
    assert get_args(Effort) == EFFORTS


# -- thinking ----------------------------------------------------------------


def thinking(**settings):
    return request_for({**BASE, "max_tokens": 16000, "thinking": settings})


def test_a_thinking_type_becomes_the_thinking_parameter():
    assert thinking(type="adaptive")["thinking"] == {"type": "adaptive"}


def test_enabled_thinking_carries_its_budget():
    payload = thinking(type="enabled", budget_tokens=4096)

    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 4096}


def test_display_goes_with_the_type():
    payload = thinking(type="adaptive", display="summarized")

    assert payload["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_a_blank_thinking_block_sends_nothing():
    """Blank leaves the model's default, which differs from model to model."""
    payload = thinking(type=None, budget_tokens=None, display=None)

    assert "thinking" not in payload


@pytest.mark.parametrize("kind", ["adaptive", "disabled", "between_tools"])
def test_every_documented_thinking_type_without_a_budget_is_accepted(kind):
    """Which model takes which type is the API's call; taxman only checks the name."""
    assert thinking(type=kind)["thinking"] == {"type": kind}


def test_an_unknown_thinking_type_is_refused():
    with pytest.raises(ValidationError, match="type"):
        thinking(type="extended")


def test_enabled_thinking_needs_a_budget():
    with pytest.raises(ValidationError, match="budget_tokens"):
        thinking(type="enabled")


def test_a_budget_needs_enabled_thinking():
    with pytest.raises(ValidationError, match="budget_tokens"):
        thinking(type="adaptive", budget_tokens=4096)


def test_the_budget_is_at_least_1024():
    with pytest.raises(ValidationError, match="budget_tokens"):
        thinking(type="enabled", budget_tokens=1023)


def test_the_budget_must_leave_room_for_an_answer():
    """Thinking counts toward max_tokens, so the budget must be below it."""
    with pytest.raises(ValidationError, match="max_tokens"):
        thinking(type="enabled", budget_tokens=16000)


@pytest.mark.parametrize("kind", ["disabled", "between_tools"])
def test_display_needs_thinking_that_has_something_to_show(kind):
    with pytest.raises(ValidationError, match="display"):
        thinking(type=kind, display="summarized")


def test_display_without_a_type_is_refused():
    """The API takes no thinking object without a type, so display alone is never sent."""
    with pytest.raises(ValidationError, match="type"):
        thinking(display="summarized")


def test_the_thinking_block_refuses_a_setting_it_does_not_know():
    with pytest.raises(ValidationError, match="bogus"):
        thinking(type="adaptive", bogus=1)


# -- sampling ----------------------------------------------------------------


@pytest.mark.parametrize("key, value", [("temperature", 0.2), ("top_p", 0.99), ("top_k", 40)])
def test_a_sampling_parameter_is_sent_when_set(key, value):
    assert request_for({**BASE, key: value})[key] == value


@pytest.mark.parametrize(
    "block",
    [
        {"temperature": 1.5},
        {"temperature": -0.1},
        {"top_p": 1.1},
        {"top_k": 0},
    ],
)
def test_out_of_range_sampling_values_are_refused(block):
    with pytest.raises(ValidationError):
        request_for({**BASE, **block})


# -- extra -------------------------------------------------------------------


def test_extra_passes_through_unknown_api_parameters():
    assert request_for({**BASE, "extra": {"service_tier": "standard_only"}})["service_tier"] == (
        "standard_only"
    )


def test_extra_passes_a_null_through_as_is():
    """`extra:` is the escape hatch: what is written is what is sent, nulls included."""
    payload = request_for({**BASE, "extra": {"inference_geo": None}})

    assert "inference_geo" in payload
    assert payload["inference_geo"] is None


def test_extra_can_add_to_an_object_taxman_also_fills():
    """Effort and an output format share `output_config`; neither displaces the other."""
    fmt = {"type": "json_schema", "schema": {"type": "object"}}
    payload = request_for({**BASE, "effort": "low", "extra": {"output_config": {"format": fmt}}})

    assert payload["output_config"] == {"effort": "low", "format": fmt}


def test_extra_can_fill_an_object_taxman_left_out():
    fmt = {"type": "json_schema", "schema": {"type": "object"}}

    assert request_for({**BASE, "extra": {"output_config": {"format": fmt}}})["output_config"] == {
        "format": fmt
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"model": "claude-other"},
        {"max_tokens": 5},
        {"messages": []},
        {"system": "sneaky"},
        {"temperature": 0.5},
        {"thinking": {"type": "adaptive"}},
        {"thinking": {"display": "summarized"}},
        {"output_config": {"effort": "max"}},
        {"output_config": None},
    ],
)
def test_extra_cannot_override_a_setting_taxman_names(extra):
    """Otherwise `extra:` could quietly bypass a named setting's checks and defaults."""
    with pytest.raises(ValidationError, match="extra"):
        request_for({**BASE, "extra": extra})


def test_extra_cannot_ask_for_a_stream():
    """A stream is not a response; there would be nothing whole to record."""
    with pytest.raises(ValidationError, match="stream"):
        request_for({**BASE, "extra": {"stream": True}})


def test_a_request_never_carries_changes_from_the_one_before():
    """Two requests from one audit share its `extra:`; merging must not write back into it."""
    block = {**BASE, "effort": "low", "extra": {"output_config": {"format": {"type": "x"}}}}
    config = AnthropicProvider().validate_model_config(block)
    message = Message(id="m0000", text="hello", hash="sha256:x", line_number=1)
    first = build_request(Request(message=message, repeat=0, model=config))
    first["output_config"]["format"]["type"] = "changed"

    second = build_request(Request(message=message, repeat=1, model=config))

    assert second["output_config"]["format"] == {"type": "x"}


def test_every_key_taxman_sends_is_protected_from_extra():
    """A new named setting must join SET_BY_TAXMAN, or `extra:` could override it."""
    from ai_taxman.providers.anthropic.config import SET_BY_TAXMAN

    block = {
        **BASE,
        "max_tokens": 16000,
        "effort": "low",
        "temperature": 1.0,
        "top_p": 0.99,
        "top_k": 5,
        "thinking": {"type": "enabled", "budget_tokens": 2048, "display": "summarized"},
        "search": {"web_search": True},
    }
    payload = request_for(block, system_prompt="Be terse.")

    for path in leaf_paths(payload):
        assert any(path == key or path.startswith(f"{key}.") for key in SET_BY_TAXMAN), path


def leaf_paths(payload, prefix=""):
    for key, value in payload.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            yield from leaf_paths(value, f"{path}.")
        else:
            yield path


# -- web search --------------------------------------------------------------


def search(**settings):
    return request_for({**BASE, "search": {"web_search": True, **settings}})


def tool(**settings):
    return search(**settings)["tools"][0]


def test_web_search_gives_claude_the_latest_web_search_tool():
    assert search()["tools"] == [{"type": "web_search_20260318", "name": "web_search"}]


@pytest.mark.parametrize(
    "version", ["web_search_20250305", "web_search_20260209", "web_search_20260318"]
)
def test_a_tool_version_can_be_pinned(version):
    assert tool(tool_version=version)["type"] == version


def test_an_unknown_tool_version_is_refused():
    with pytest.raises(ValidationError, match="tool_version"):
        search(tool_version="web_search_2099")


def test_web_search_false_adds_no_tools():
    assert "tools" not in request_for({**BASE, "search": {"web_search": False}})


def test_a_blank_search_block_adds_no_tools():
    assert "tools" not in request_for({**BASE, "search": {"web_search": None}})


def test_web_search_lives_in_the_search_block():
    with pytest.raises(ValidationError, match="web_search"):
        request_for({**BASE, "web_search": True})


def test_the_search_block_refuses_a_setting_it_does_not_know():
    with pytest.raises(ValidationError, match="bogus"):
        search(bogus=1)


def test_a_search_setting_without_web_search_is_refused():
    """A setting for a tool that is not sent would be recorded but never used."""
    with pytest.raises(ValidationError, match="web_search"):
        request_for({**BASE, "search": {"tool_version": "web_search_20250305"}})


def test_extra_cannot_add_tools_of_its_own():
    with pytest.raises(ValidationError, match="tools"):
        request_for({**BASE, "extra": {"tools": []}})


def test_max_uses_goes_on_the_tool():
    assert tool(max_uses=3)["max_uses"] == 3


def test_max_uses_is_a_positive_number():
    with pytest.raises(ValidationError, match="max_uses"):
        search(max_uses=0)


def test_unset_tool_settings_are_left_to_anthropic():
    assert set(tool()) == {"type", "name"}


def test_allowed_domains_go_on_the_tool():
    assert tool(allowed_domains=["cdc.gov", "who.int"])["allowed_domains"] == ["cdc.gov", "who.int"]


def test_blocked_domains_go_on_the_tool():
    assert tool(blocked_domains=["example.com/blog"])["blocked_domains"] == ["example.com/blog"]


def test_allowed_and_blocked_domains_cannot_both_be_set():
    """Anthropic refuses the pair with a 400; better to say so before the run."""
    with pytest.raises(ValidationError, match="one or the other"):
        search(allowed_domains=["cdc.gov"], blocked_domains=["example.com"])


@pytest.mark.parametrize("domain", ["https://cdc.gov", "http://who.int/"])
def test_a_domain_with_a_scheme_is_refused_rather_than_rewritten(domain):
    with pytest.raises(ValidationError, match="without the http"):
        search(allowed_domains=[domain])


def test_user_location_is_sent_as_an_approximate_location():
    location = tool(user_location={"country": "US", "city": "Minneapolis"})["user_location"]

    assert location == {"type": "approximate", "country": "US", "city": "Minneapolis"}


def test_every_user_location_field_goes_through():
    fields = {
        "city": "Paris",
        "region": "Ile-de-France",
        "country": "FR",
        "timezone": "Europe/Paris",
    }

    assert tool(user_location=fields)["user_location"] == {"type": "approximate", **fields}


def test_a_blank_user_location_sends_none():
    assert "user_location" not in tool(user_location={"city": None, "country": None})


@pytest.mark.parametrize("country", ["USA", "U", "us", "1A"])
def test_country_is_a_two_letter_iso_code(country):
    with pytest.raises(ValidationError, match="user_location.country"):
        search(user_location={"country": country})


def test_user_location_refuses_a_field_it_does_not_know():
    with pytest.raises(ValidationError, match="user_location.street"):
        search(user_location={"street": "Main St"})
