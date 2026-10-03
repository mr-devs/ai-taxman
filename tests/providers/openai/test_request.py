"""Turning an audit's `model:` block into Responses API keyword arguments.

Pure function, no network.
"""

from typing import get_args

import pytest
from pydantic import ValidationError

from ai_taxman.core.messages import Message
from ai_taxman.providers.base import Request
from ai_taxman.providers.openai.config import OpenAIModelConfig
from ai_taxman.providers.openai.models import ReasoningEffort
from ai_taxman.providers.openai.provider import OpenAIProvider, build_request


@pytest.fixture
def provider():
    return OpenAIProvider()


def request_for(block, text="hello", system_prompt=None):
    config = OpenAIProvider().validate_model_config(block)
    message = Message(id="m0000", text=text, hash="sha256:x", line_number=1)
    return build_request(
        Request(message=message, repeat=0, model=config, system_prompt=system_prompt)
    )


def test_sends_the_message_as_the_input(provider):
    assert request_for({"name": "gpt-5"})["input"] == "hello"


def test_sends_the_model_name(provider):
    assert request_for({"name": "gpt-5"})["model"] == "gpt-5"


def test_omits_parameters_the_user_left_blank(provider):
    payload = request_for({"name": "gpt-5"})

    for absent in ("temperature", "max_output_tokens", "top_p", "reasoning", "tools"):
        assert absent not in payload


def test_includes_temperature_when_set(provider):
    assert request_for({"name": "gpt-5", "temperature": 1.5})["temperature"] == 1.5


def test_includes_max_output_tokens_when_set(provider):
    assert request_for({"name": "gpt-5", "max_output_tokens": 512})["max_output_tokens"] == 512


def test_reasoning_effort_becomes_the_reasoning_block(provider):
    payload = request_for({"name": "gpt-5", "reasoning_effort": "high"})

    assert payload["reasoning"] == {"effort": "high"}


@pytest.mark.parametrize("effort", get_args(ReasoningEffort))
def test_every_documented_effort_level_is_accepted(provider, effort):
    """The Responses API documents none | minimal | low | medium | high | xhigh | max.

    Rejecting one taxman has not heard of turns a valid audit into a hard error
    before anything is sent.
    """
    payload = request_for({"name": "gpt-5", "reasoning_effort": effort})

    assert payload["reasoning"] == {"effort": effort}


def test_web_search_becomes_a_tool(provider):
    payload = request_for({"name": "gpt-5", "search": {"web_search": True}})

    assert payload["tools"] == [{"type": "web_search"}]


def test_web_search_false_adds_no_tools(provider):
    assert "tools" not in request_for({"name": "gpt-5", "search": {"web_search": False}})


def test_a_blank_search_block_adds_no_tools(provider):
    """`audits new` writes the block with every key blank."""
    assert "tools" not in request_for({"name": "gpt-5", "search": {"web_search": None}})
    assert "tools" not in request_for({"name": "gpt-5", "search": None})


def test_web_search_lives_in_the_search_block(provider):
    with pytest.raises(ValidationError):
        request_for({"name": "gpt-5", "web_search": True})


def test_the_search_block_refuses_a_setting_it_does_not_know(provider):
    with pytest.raises(ValidationError):
        request_for({"name": "gpt-5", "search": {"web_serch": True}})


def test_the_system_prompt_becomes_instructions(provider):
    payload = request_for({"name": "gpt-5"}, system_prompt="Be terse.")

    assert payload["instructions"] == "Be terse."


def test_no_system_prompt_sends_no_instructions(provider):
    assert "instructions" not in request_for({"name": "gpt-5"})


def test_the_model_block_takes_no_system_prompt(provider):
    """It is set once, as a file, in the audit's top-level `system_prompt:`."""
    with pytest.raises(ValidationError):
        request_for({"name": "gpt-5", "system_prompt": "Be terse."})


def test_store_defaults_to_false_so_audits_leave_no_server_side_trace(provider):
    assert request_for({"name": "gpt-5"})["store"] is False


def test_extra_body_passes_through_unknown_api_parameters(provider):
    payload = request_for({"name": "gpt-5", "extra": {"service_tier": "flex"}})

    assert payload["service_tier"] == "flex"


def test_model_name_is_required(provider):
    with pytest.raises(ValidationError, match="name"):
        provider.validate_model_config({})


def test_unknown_model_keys_are_rejected(provider):
    with pytest.raises(ValidationError, match="temprature"):
        provider.validate_model_config({"name": "gpt-5", "temprature": 1.5})


def test_blank_values_are_treated_as_unset(provider):
    """`taxman audits new` writes keys with no value; YAML reads those as None."""
    config = provider.validate_model_config(
        {"name": "gpt-5", "temperature": None, "reasoning_effort": None}
    )

    assert config.temperature is None
    assert config.reasoning_effort is None


@pytest.mark.parametrize(
    "block",
    [
        {"name": "gpt-5", "temperature": -1},
        {"name": "gpt-5", "temperature": 3},
        {"name": "gpt-5", "top_p": 1.5},
        {"name": "gpt-5", "max_output_tokens": 0},
        {"name": "gpt-5", "reasoning_effort": "extreme"},
    ],
)
def test_out_of_range_values_are_rejected(provider, block):
    with pytest.raises(ValidationError):
        provider.validate_model_config(block)


def test_an_unknown_model_name_is_still_allowed(provider):
    """Providers ship models faster than we can list them."""
    config = provider.validate_model_config({"name": "gpt-6-turbo-unreleased"})

    assert isinstance(config, OpenAIModelConfig)


# --- the search block -----------------------------------------------------


def search(**settings):
    return {"name": "gpt-5", "search": settings}


def test_a_search_setting_without_web_search_is_refused(provider):
    """An audit must not record settings that were never sent."""
    with pytest.raises(ValidationError, match="web_search"):
        request_for(search(search_context_size="low"))


def test_a_search_setting_with_web_search_false_is_refused(provider):
    with pytest.raises(ValidationError, match="web_search"):
        request_for(search(web_search=False, search_context_size="low"))


def test_blank_search_settings_need_no_web_search(provider):
    """A fresh template leaves every key blank."""
    block = search(web_search=None, search_context_size=None, return_token_budget=None)

    assert "tools" not in request_for(block)


def tool(**settings):
    return request_for(search(web_search=True, **settings))["tools"][0]


def test_search_context_size_goes_on_the_tool(provider):
    assert tool(search_context_size="high")["search_context_size"] == "high"


def test_search_context_size_is_low_medium_or_high(provider):
    with pytest.raises(ValidationError):
        tool(search_context_size="huge")


def test_external_web_access_goes_on_the_tool(provider):
    assert tool(external_web_access=False)["external_web_access"] is False


def test_return_token_budget_goes_on_the_tool(provider):
    assert tool(return_token_budget="unlimited")["return_token_budget"] == "unlimited"


def test_return_token_budget_is_default_or_unlimited(provider):
    """The docs: `null`, numbers, and other strings are rejected."""
    with pytest.raises(ValidationError):
        tool(return_token_budget=10_000)


def test_unset_tool_settings_are_left_to_openai(provider):
    assert tool() == {"type": "web_search"}


def test_allowed_domains_become_a_filter(provider):
    filters = tool(allowed_domains=["cdc.gov", "who.int"])["filters"]

    assert filters == {"allowed_domains": ["cdc.gov", "who.int"]}


def test_blocked_domains_become_a_filter(provider):
    filters = tool(blocked_domains=["reddit.com"])["filters"]

    assert filters == {"blocked_domains": ["reddit.com"]}


def test_both_domain_lists_share_one_filter(provider):
    filters = tool(allowed_domains=["cdc.gov"], blocked_domains=["reddit.com"])["filters"]

    assert filters == {"allowed_domains": ["cdc.gov"], "blocked_domains": ["reddit.com"]}


def test_at_most_100_domains_per_list(provider):
    with pytest.raises(ValidationError, match="100"):
        tool(blocked_domains=[f"site{n}.com" for n in range(101)])


@pytest.mark.parametrize("domain", ["https://cdc.gov", "http://who.int/"])
def test_a_domain_with_a_scheme_is_refused_rather_than_rewritten(provider, domain):
    """The docs: omit the HTTP or HTTPS prefix. taxman does not edit what was written."""
    with pytest.raises(ValidationError, match="prefix"):
        tool(allowed_domains=[domain])


def test_tool_choice_required_makes_the_model_search(provider):
    payload = request_for(search(web_search=True, tool_choice="required"))

    assert payload["tool_choice"] == "required"


def test_tool_choice_is_left_to_openai_unless_set(provider):
    assert "tool_choice" not in request_for(search(web_search=True))


def test_tool_choice_is_auto_or_required(provider):
    with pytest.raises(ValidationError):
        request_for(search(web_search=True, tool_choice="none"))


def test_web_search_records_every_source_by_default(provider):
    """The full list of URLs consulted, not only those cited, is the audit's evidence."""
    payload = request_for(search(web_search=True))

    assert payload["include"] == ["web_search_call.action.sources"]


def test_include_can_be_turned_off(provider):
    assert "include" not in request_for(search(web_search=True, include=[]))


def test_include_takes_the_search_results_too(provider):
    payload = request_for(
        search(
            web_search=True, include=["web_search_call.action.sources", "web_search_call.results"]
        )
    )

    assert payload["include"] == ["web_search_call.action.sources", "web_search_call.results"]


def test_include_takes_only_web_search_data(provider):
    with pytest.raises(ValidationError):
        request_for(search(web_search=True, include=["reasoning.encrypted_content"]))


def test_without_web_search_nothing_is_included(provider):
    assert "include" not in request_for(search())


def test_user_location_is_sent_as_an_approximate_location(provider):
    location = tool(user_location={"country": "US", "city": "Minneapolis"})["user_location"]

    assert location == {"type": "approximate", "country": "US", "city": "Minneapolis"}


def test_every_user_location_field_goes_through(provider):
    fields = {"country": "GB", "region": "London", "city": "London", "timezone": "Europe/London"}

    assert tool(user_location=fields)["user_location"] == {"type": "approximate", **fields}


def test_a_blank_user_location_sends_none(provider):
    assert "user_location" not in tool(user_location={"country": None, "city": None})


@pytest.mark.parametrize("country", ["USA", "U", "us", "1A"])
def test_country_is_a_two_letter_iso_code(provider, country):
    with pytest.raises(ValidationError, match="country"):
        tool(user_location={"country": country})


def test_user_location_refuses_a_field_it_does_not_know(provider):
    with pytest.raises(ValidationError):
        tool(user_location={"zip": "55401"})


def test_a_user_location_without_web_search_is_refused(provider):
    with pytest.raises(ValidationError, match="web_search"):
        request_for(search(user_location={"country": "US"}))


def test_search_content_types_go_on_the_tool(provider):
    assert tool(search_content_types=["image", "text"])["search_content_types"] == [
        "image",
        "text",
    ]


def test_search_content_types_are_text_or_image(provider):
    with pytest.raises(ValidationError):
        tool(search_content_types=["video"])


def test_image_settings_go_on_the_tool(provider):
    settings = tool(
        search_content_types=["image"], image_settings={"max_results": 3, "caption": True}
    )

    assert settings["image_settings"] == {"max_results": 3, "caption": True}


def test_image_settings_send_only_what_was_set(provider):
    settings = tool(
        search_content_types=["image"], image_settings={"max_results": 3, "caption": None}
    )

    assert settings["image_settings"] == {"max_results": 3}


def test_max_results_is_a_positive_number(provider):
    with pytest.raises(ValidationError):
        tool(search_content_types=["image"], image_settings={"max_results": 0})


def test_image_settings_without_image_results_are_refused(provider):
    """Settings for images the search was never asked for would be recorded but unused."""
    with pytest.raises(ValidationError, match="image"):
        tool(image_settings={"max_results": 3})


def test_extra_passes_a_null_through_as_is(provider):
    """`extra:` is the escape hatch: what is written is what is sent, nulls included."""
    payload = request_for({"name": "gpt-5", "extra": {"service_tier": None}})

    assert "service_tier" in payload
    assert payload["service_tier"] is None


@pytest.mark.parametrize(
    "key", ["include", "tool_choice", "tools", "instructions", "temperature", "model", "input"]
)
def test_extra_cannot_override_a_setting_taxman_names(provider, key):
    """Otherwise `extra: {include: [...]}` would quietly drop the default sources."""
    with pytest.raises(ValidationError, match=key):
        request_for({"name": "gpt-5", "extra": {key: "anything"}})


@pytest.mark.parametrize("key", ["stream", "background"])
def test_extra_cannot_ask_for_a_response_send_could_not_record(provider, key):
    with pytest.raises(ValidationError, match=f"cannot set {key}"):
        request_for({"name": "gpt-5", "extra": {key: True}})


def test_a_request_never_shares_the_audits_search_lists(provider):
    """Every request in a run is built from one config; none may write back into it."""
    search = {"web_search": True, "allowed_domains": ["cdc.gov"], "blocked_domains": ["x.com"]}
    config = OpenAIProvider().validate_model_config({"name": "gpt-5", "search": search})
    message = Message(id="m0000", text="hello", hash="sha256:x", line_number=1)
    first = build_request(Request(message=message, repeat=0, model=config))
    first["tools"][0]["filters"]["allowed_domains"].append("example.com")
    first["tools"][0]["filters"]["blocked_domains"].clear()

    second = build_request(Request(message=message, repeat=1, model=config))

    assert second["tools"][0]["filters"] == {
        "allowed_domains": ["cdc.gov"],
        "blocked_domains": ["x.com"],
    }


def test_every_key_taxman_sends_is_protected_from_extra(provider):
    """A new named setting must join SET_BY_TAXMAN, or `extra:` could override it."""
    from ai_taxman.providers.openai.config import SET_BY_TAXMAN

    block = {
        "name": "gpt-5",
        "temperature": 1.0,
        "top_p": 0.5,
        "max_output_tokens": 100,
        "reasoning_effort": "low",
        "store": True,
        "search": {"web_search": True, "tool_choice": "required"},
    }

    assert set(request_for(block, system_prompt="Be terse.")) <= SET_BY_TAXMAN
