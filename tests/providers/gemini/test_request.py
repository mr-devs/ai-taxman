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


def test_a_minimal_block_sends_only_what_gemini_requires():
    """The message as the input; every setting left blank is left to Google."""
    assert request_for(BASE, text="hi there") == {
        "model": "gemini-3.8-flash",
        "input": "hi there",
        "store": False,
    }


def test_store_defaults_to_false_so_audits_leave_no_server_side_trace():
    """The Interactions API keeps every interaction for 55 days unless told not to."""
    assert request_for(BASE)["store"] is False


def test_store_can_be_turned_on():
    assert request_for({**BASE, "store": True})["store"] is True


def test_the_system_prompt_becomes_the_system_instruction():
    payload = request_for(BASE, system_prompt="Answer in one word.")

    assert payload["system_instruction"] == "Answer in one word."


def test_no_system_prompt_sends_no_system_instruction():
    assert "system_instruction" not in request_for(BASE)


# -- generation config -------------------------------------------------------


@pytest.mark.parametrize(
    "key, value",
    [("max_output_tokens", 256), ("thinking_level", "minimal"), ("thinking_summaries", "none")],
)
def test_a_generation_setting_goes_in_generation_config(key, value):
    assert request_for({**BASE, key: value})["generation_config"] == {key: value}


def test_settings_share_one_generation_config():
    payload = request_for({**BASE, "thinking_level": "low", "max_output_tokens": 64})

    assert payload["generation_config"] == {"thinking_level": "low", "max_output_tokens": 64}


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


def test_there_is_no_thinking_budget():
    """Google is retiring it: upcoming models refuse it with a 400 rather than map it to a level."""
    with pytest.raises(ValidationError, match="thinking_budget"):
        request_for({**BASE, "thinking_budget": 1024})


@pytest.mark.parametrize(
    "block, match",
    [
        ({"max_output_tokens": 0}, "max_output_tokens"),
        ({"thinking_level": "max"}, "thinking_level"),
        ({"thinking_summaries": "full"}, "thinking_summaries"),
    ],
)
def test_a_value_the_field_does_not_take_is_refused(block, match):
    """One row for each bound and each fixed set of values a field takes."""
    with pytest.raises(ValidationError, match=match):
        request_for({**BASE, **block})


# -- extra -------------------------------------------------------------------


def test_extra_can_add_to_generation_config_beside_named_settings():
    """seed has no key of its own; it must not displace the audit's max_output_tokens."""
    block = {**BASE, "max_output_tokens": 64, "extra": {"generation_config": {"seed": 7}}}

    assert request_for(block)["generation_config"] == {"max_output_tokens": 64, "seed": 7}


@pytest.mark.parametrize("key", ["stream", "background"])
def test_extra_cannot_ask_for_a_response_send_could_not_record(key):
    with pytest.raises(ValidationError, match=f"cannot set {key}"):
        request_for({**BASE, "extra": {key: True}})


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
