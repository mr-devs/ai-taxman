"""Turning an audit's `model:` block into Responses API keyword arguments.

Pure function, no network.
"""

import pytest
from pydantic import ValidationError

from ai_taxman.core.messages import Message
from ai_taxman.providers.base import Request
from ai_taxman.providers.openai.provider import OpenAIProvider, build_request


@pytest.fixture
def provider():
    return OpenAIProvider()


def request_for(block, text="hello", system_prompt=None):
    config = OpenAIProvider().validate_model_config(block)
    message = Message(id="m0000", text=text, line_number=1)
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


def test_web_search_becomes_a_tool(provider):
    payload = request_for({"name": "gpt-5", "search": {"web_search": True}})

    assert payload["tools"] == [{"type": "web_search"}]


def test_the_system_prompt_becomes_instructions(provider):
    payload = request_for({"name": "gpt-5"}, system_prompt="Be terse.")

    assert payload["instructions"] == "Be terse."


def test_no_system_prompt_sends_no_instructions(provider):
    assert "instructions" not in request_for({"name": "gpt-5"})


def test_store_defaults_to_false_so_audits_leave_no_server_side_trace(provider):
    assert request_for({"name": "gpt-5"})["store"] is False


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


def tool(**settings):
    return request_for(search(web_search=True, **settings))["tools"][0]


def test_search_context_size_goes_on_the_tool(provider):
    assert tool(search_context_size="high")["search_context_size"] == "high"


def test_external_web_access_goes_on_the_tool(provider):
    assert tool(external_web_access=False)["external_web_access"] is False


def test_return_token_budget_goes_on_the_tool(provider):
    assert tool(return_token_budget="unlimited")["return_token_budget"] == "unlimited"


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


def test_web_search_records_every_source_by_default(provider):
    """The full list of URLs consulted, not only those cited, is the audit's evidence."""
    payload = request_for(search(web_search=True))

    assert payload["include"] == ["web_search_call.action.sources"]


def test_include_can_be_turned_off(provider):
    assert "include" not in request_for(search(web_search=True, include=[]))


def test_without_web_search_nothing_is_included(provider):
    assert "include" not in request_for(search())


def test_user_location_is_sent_as_an_approximate_location(provider):
    location = tool(user_location={"country": "US", "city": "Minneapolis"})["user_location"]

    assert location == {"type": "approximate", "country": "US", "city": "Minneapolis"}


def test_every_user_location_field_goes_through(provider):
    fields = {"country": "GB", "region": "London", "city": "London", "timezone": "Europe/London"}

    assert tool(user_location=fields)["user_location"] == {"type": "approximate", **fields}


def test_a_user_location_without_web_search_is_refused(provider):
    with pytest.raises(ValidationError, match="web_search"):
        request_for(search(user_location={"country": "US"}))


def test_search_content_types_go_on_the_tool(provider):
    assert tool(search_content_types=["image", "text"])["search_content_types"] == [
        "image",
        "text",
    ]


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


def test_image_settings_without_image_results_are_refused(provider):
    """Settings for images the search was never asked for would be recorded but unused."""
    with pytest.raises(ValidationError, match="image"):
        tool(image_settings={"max_results": 3})


@pytest.mark.parametrize(
    "block, match",
    [
        ({"name": "gpt-5", "temperature": -1}, "temperature"),
        ({"name": "gpt-5", "temperature": 3}, "temperature"),
        ({"name": "gpt-5", "top_p": 1.5}, "top_p"),
        ({"name": "gpt-5", "max_output_tokens": 0}, "max_output_tokens"),
        ({"name": "gpt-5", "reasoning_effort": "extreme"}, "reasoning_effort"),
        (search(web_search=True, search_context_size="huge"), "search_context_size"),
        (search(web_search=True, return_token_budget=10_000), "return_token_budget"),
        (search(web_search=True, tool_choice="none"), "tool_choice"),
        (search(web_search=True, include=["reasoning.encrypted_content"]), "include"),
        (search(web_search=True, search_content_types=["video"]), "search_content_types"),
        (
            search(
                web_search=True, search_content_types=["image"], image_settings={"max_results": 0}
            ),
            "max_results",
        ),
        (
            search(web_search=True, blocked_domains=[f"site{n}.com" for n in range(101)]),
            "at most 100",
        ),
        (search(web_search=True, user_location={"country": "USA"}), "user_location.country"),
    ],
)
def test_out_of_range_values_are_rejected(provider, block, match):
    """One row for each bound and each fixed set of values a field takes."""
    with pytest.raises(ValidationError, match=match):
        provider.validate_model_config(block)


@pytest.mark.parametrize("key", ["stream", "background"])
def test_extra_cannot_ask_for_a_response_send_could_not_record(provider, key):
    with pytest.raises(ValidationError, match=f"cannot set {key}"):
        request_for({"name": "gpt-5", "extra": {key: True}})


def test_a_request_never_shares_the_audits_search_lists(provider):
    """Every request in a run is built from one config; none may write back into it."""
    search = {"web_search": True, "allowed_domains": ["cdc.gov"], "blocked_domains": ["x.com"]}
    config = OpenAIProvider().validate_model_config({"name": "gpt-5", "search": search})
    message = Message(id="m0000", text="hello", line_number=1)
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
