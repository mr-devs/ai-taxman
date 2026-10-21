"""Turning an audit's `model:` block into an Interactions API request body.

Pure function, no network.
"""

import pytest
from pydantic import ValidationError

from ai_taxman.core.messages import Message
from ai_taxman.providers.base import Request
from ai_taxman.providers.gemini.provider import GeminiProvider, build_request

BASE = {"name": "gemini-3.8-flash"}


def request_for(block, text="hello", system_prompt=None):
    config = GeminiProvider().validate_model_config(block)
    message = Message(id="m0000", text=text, line_number=1)
    return build_request(
        Request(message=message, repeat=0, model=config, system_prompt=system_prompt)
    )


def test_sends_the_message_as_the_input():
    assert request_for(BASE, text="hi there")["input"] == "hi there"


def test_sends_the_model_name():
    assert request_for(BASE)["model"] == "gemini-3.8-flash"


def test_store_defaults_to_false_so_audits_leave_no_server_side_trace():
    """The Interactions API keeps every interaction for 55 days unless told not to."""
    assert request_for(BASE)["store"] is False


def test_store_can_be_turned_on():
    assert request_for({**BASE, "store": True})["store"] is True


def test_omits_parameters_the_user_left_blank():
    assert set(request_for(BASE)) == {"model", "input", "store"}


def test_model_name_is_required():
    with pytest.raises(ValidationError, match="name"):
        request_for({})


def test_an_unknown_model_name_is_still_allowed():
    """Google ships models faster than the known list is updated."""
    assert request_for({"name": "gemini-9-ultra"})["model"] == "gemini-9-ultra"


def test_unknown_model_keys_are_rejected():
    with pytest.raises(ValidationError, match="bogus"):
        request_for({**BASE, "bogus": 1})


def test_the_system_prompt_becomes_the_system_instruction():
    payload = request_for(BASE, system_prompt="Answer in one word.")

    assert payload["system_instruction"] == "Answer in one word."


def test_no_system_prompt_sends_no_system_instruction():
    assert "system_instruction" not in request_for(BASE)


# -- generation config -------------------------------------------------------


def test_max_output_tokens_goes_in_generation_config():
    assert request_for({**BASE, "max_output_tokens": 256})["generation_config"] == {
        "max_output_tokens": 256
    }


def test_settings_share_one_generation_config():
    payload = request_for({**BASE, "thinking_level": "low", "max_output_tokens": 64})

    assert payload["generation_config"] == {"thinking_level": "low", "max_output_tokens": 64}


def test_max_output_tokens_must_be_positive():
    with pytest.raises(ValidationError, match="max_output_tokens"):
        request_for({**BASE, "max_output_tokens": 0})


@pytest.mark.parametrize("key", ["temperature", "top_p", "top_k"])
def test_deprecated_sampling_settings_are_not_offered(key):
    """Google deprecated them on 2026-07-21: Gemini 3.6 Flash, 3.5 Flash-Lite and
    later models ignore them, and future models will refuse them with a 400."""
    with pytest.raises(ValidationError, match=key):
        request_for({**BASE, key: 0.5})


def test_extra_can_still_send_sampling_settings_to_an_older_model():
    """Gemini 2.5, and the models before 3.6 Flash and 3.5 Flash-Lite, still honour them."""
    block = {"name": "gemini-2.5-flash", "extra": {"generation_config": {"temperature": 0.2}}}

    assert request_for(block)["generation_config"] == {"temperature": 0.2}


# -- thinking ----------------------------------------------------------------


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high"])
def test_every_documented_thinking_level_goes_in_generation_config(level):
    """Gemini 3 models take it; which model takes which level is Google's call."""
    assert request_for({**BASE, "thinking_level": level})["generation_config"] == {
        "thinking_level": level
    }


def test_an_undocumented_thinking_level_is_refused():
    with pytest.raises(ValidationError, match="thinking_level"):
        request_for({**BASE, "thinking_level": "max"})


def test_there_is_no_thinking_budget():
    """Google is retiring it: upcoming models refuse it with a 400 rather than map it to a level."""
    with pytest.raises(ValidationError, match="thinking_budget"):
        request_for({**BASE, "thinking_budget": 1024})


@pytest.mark.parametrize("summaries", ["auto", "none"])
def test_thinking_summaries_go_in_generation_config(summaries):
    assert request_for({**BASE, "thinking_summaries": summaries})["generation_config"] == {
        "thinking_summaries": summaries
    }


def test_thinking_summaries_are_auto_or_none():
    with pytest.raises(ValidationError, match="thinking_summaries"):
        request_for({**BASE, "thinking_summaries": "full"})


# -- extra -------------------------------------------------------------------


def test_extra_passes_through_unknown_api_parameters():
    assert request_for({**BASE, "extra": {"service_tier": "flex"}})["service_tier"] == "flex"


def test_extra_passes_a_null_through_as_is():
    """`extra:` is the escape hatch: what is written is what is sent, nulls included."""
    payload = request_for({**BASE, "extra": {"response_format": None}})

    assert "response_format" in payload
    assert payload["response_format"] is None


def test_extra_can_add_to_generation_config_beside_named_settings():
    """seed has no key of its own; it must not displace the audit's max_output_tokens."""
    block = {**BASE, "max_output_tokens": 64, "extra": {"generation_config": {"seed": 7}}}

    assert request_for(block)["generation_config"] == {"max_output_tokens": 64, "seed": 7}


def test_extra_can_fill_a_generation_config_taxman_left_out():
    payload = request_for({**BASE, "extra": {"generation_config": {"stop_sequences": ["END"]}}})

    assert payload["generation_config"] == {"stop_sequences": ["END"]}


@pytest.mark.parametrize(
    "extra",
    [
        {"model": "gemini-other"},
        {"input": "sneaky"},
        {"store": True},
        {"system_instruction": "sneaky"},
        {"generation_config": {"max_output_tokens": 64}},
        {"generation_config": {"thinking_level": "high"}},
        {"generation_config": None},
    ],
)
def test_extra_cannot_override_a_setting_taxman_names(extra):
    """Otherwise `extra: {store: true}` would quietly undo the audit's own setting."""
    with pytest.raises(ValidationError, match="taxman sets"):
        request_for({**BASE, "extra": extra})


@pytest.mark.parametrize("key", ["stream", "background"])
def test_extra_cannot_ask_for_a_response_send_could_not_record(key):
    with pytest.raises(ValidationError, match=f"cannot set {key}"):
        request_for({**BASE, "extra": {key: True}})


def test_a_request_never_carries_changes_from_the_one_before():
    """Two requests from one audit share its `extra:`; merging must not write back into it."""
    block = {**BASE, "extra": {"generation_config": {"stop_sequences": ["X"]}}}
    config = GeminiProvider().validate_model_config(block)
    message = Message(id="m0000", text="hello", line_number=1)
    first = build_request(Request(message=message, repeat=0, model=config))
    first["generation_config"]["stop_sequences"].append("changed")

    second = build_request(Request(message=message, repeat=1, model=config))

    assert second["generation_config"]["stop_sequences"] == ["X"]


def test_every_key_taxman_sends_is_protected_from_extra():
    """A new named setting must join SET_BY_TAXMAN, or `extra:` could override it."""
    from ai_taxman.providers.gemini.config import SET_BY_TAXMAN

    block = {
        **BASE,
        "max_output_tokens": 64,
        "thinking_level": "low",
        "thinking_summaries": "auto",
        "store": True,
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


# -- Google Search -----------------------------------------------------------


def search(**settings):
    return request_for({**BASE, "search": {"web_search": True, **settings}})


def test_web_search_gives_the_model_the_google_search_tool():
    assert search()["tools"] == [{"type": "google_search"}]


def test_the_search_block_offers_no_search_types():
    """Naming a search type stops the search on the Interactions API.

    Live, on 2026-10-03, `search_types: [web_search]` on the `google_search` tool
    - the shape Google documents - made gemini-3.1-flash-lite and gemini-3.8-flash
    return `requires_action` with a client-side `function_call` step every time:
    no search, no answer. Without it, every run searched and answered.
    """
    with pytest.raises(ValidationError, match="search_types"):
        search(search_types=["web_search"])


def test_web_search_false_adds_no_tools():
    assert "tools" not in request_for({**BASE, "search": {"web_search": False}})


def test_a_blank_search_block_adds_no_tools():
    assert "tools" not in request_for({**BASE, "search": {"web_search": None}})


def test_web_search_lives_in_the_search_block():
    with pytest.raises(ValidationError, match="web_search"):
        request_for({**BASE, "web_search": True})


def test_extra_cannot_add_tools_of_its_own():
    with pytest.raises(ValidationError, match="cannot set tools"):
        request_for({**BASE, "extra": {"tools": []}})


def test_the_search_block_offers_no_tool_choice():
    """Forcing a tool does not force a Google Search on the Interactions API.

    Live, on 2026-10-03, `generation_config.tool_choice: any` with the built-in
    `google_search` tool made gemini-3.1-flash-lite either return a client-side
    `function_call` step for `google_search` - no search, no answer - or fail with
    a 400, "Model generated too many tool calls". Without it, the search runs. So
    it is not offered as a search setting; `extra:` can still send it.
    """
    with pytest.raises(ValidationError, match="tool_choice"):
        search(tool_choice="any")


def test_extra_can_still_send_a_tool_choice():
    payload = request_for({**BASE, "extra": {"generation_config": {"tool_choice": "any"}}})

    assert payload["generation_config"] == {"tool_choice": "any"}
