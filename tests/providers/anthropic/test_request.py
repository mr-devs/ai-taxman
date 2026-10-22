"""Turning an audit's `model:` block into Messages API keyword arguments.

Pure function, no network.
"""

import pytest
from pydantic import ValidationError

from ai_taxman.core.messages import Message
from ai_taxman.providers.anthropic.provider import AnthropicProvider, build_request
from ai_taxman.providers.base import Request

BASE = {"name": "claude-opus-5-5", "max_tokens": 1024}


def request_for(block, text="hello", system_prompt=None):
    config = AnthropicProvider().validate_model_config(block)
    message = Message(id="m0000", text=text, line_number=1)
    return build_request(
        Request(message=message, repeat=0, model=config, system_prompt=system_prompt)
    )


def test_a_minimal_block_sends_only_what_anthropic_requires():
    """The message as one user turn; every setting left blank is left to Anthropic."""
    assert request_for(BASE, text="hi there") == {
        "model": "claude-opus-5-5",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "hi there"}],
    }


def test_the_system_prompt_becomes_the_top_level_system():
    """Anthropic takes it as a parameter, not as a turn in `messages`."""
    payload = request_for(BASE, system_prompt="Answer in one word.")

    assert payload["system"] == "Answer in one word."
    assert all(turn["role"] == "user" for turn in payload["messages"])


def test_no_system_prompt_sends_no_system():
    assert "system" not in request_for(BASE)


def test_effort_goes_in_output_config():
    assert request_for({**BASE, "effort": "low"})["output_config"] == {"effort": "low"}


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


def test_enabled_thinking_needs_a_budget():
    with pytest.raises(ValidationError, match="budget_tokens"):
        thinking(type="enabled")


def test_a_budget_needs_enabled_thinking():
    with pytest.raises(ValidationError, match="budget_tokens"):
        thinking(type="adaptive", budget_tokens=4096)


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


# -- sampling ----------------------------------------------------------------


@pytest.mark.parametrize("key, value", [("temperature", 0.2), ("top_p", 0.99), ("top_k", 40)])
def test_a_sampling_parameter_is_sent_when_set(key, value):
    assert request_for({**BASE, key: value})[key] == value


# -- extra -------------------------------------------------------------------


def test_extra_can_add_to_an_object_taxman_also_fills():
    """Effort and an output format share `output_config`; neither displaces the other."""
    fmt = {"type": "json_schema", "schema": {"type": "object"}}
    payload = request_for({**BASE, "effort": "low", "extra": {"output_config": {"format": fmt}}})

    assert payload["output_config"] == {"effort": "low", "format": fmt}


def test_extra_cannot_ask_for_a_stream():
    """A stream is not a response; there would be nothing whole to record."""
    with pytest.raises(ValidationError, match="stream"):
        request_for({**BASE, "extra": {"stream": True}})


def test_a_request_never_shares_the_audits_search_lists():
    """Every request in a run is built from one config; none may write back into it."""
    search = {"web_search": True, "allowed_domains": ["cdc.gov"], "allowed_callers": ["direct"]}
    config = AnthropicProvider().validate_model_config({**BASE, "search": search})
    message = Message(id="m0000", text="hello", line_number=1)
    first = build_request(Request(message=message, repeat=0, model=config))
    first["tools"][0]["allowed_domains"].append("example.com")
    first["tools"][0]["allowed_callers"].clear()

    second = build_request(Request(message=message, repeat=1, model=config))

    assert second["tools"][0]["allowed_domains"] == ["cdc.gov"]
    assert second["tools"][0]["allowed_callers"] == ["direct"]


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
        "search": {"web_search": True, "tool_choice": "any"},
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
    "settings, sent",
    [
        ({"tool_version": "web_search_20250305"}, {"type": "web_search_20250305"}),
        ({"max_uses": 3}, {"max_uses": 3}),
        ({"allowed_domains": ["cdc.gov", "who.int"]}, {"allowed_domains": ["cdc.gov", "who.int"]}),
        ({"blocked_domains": ["example.com/blog"]}, {"blocked_domains": ["example.com/blog"]}),
        ({"allowed_callers": ["direct"]}, {"allowed_callers": ["direct"]}),
        ({"response_inclusion": "excluded"}, {"response_inclusion": "excluded"}),
    ],
)
def test_a_search_setting_goes_on_the_tool(settings, sent):
    assert tool(**settings) == {"type": "web_search_20260318", "name": "web_search", **sent}


def test_a_search_setting_without_web_search_is_refused():
    """A setting for a tool that is not sent would be recorded but never used."""
    with pytest.raises(ValidationError, match="web_search"):
        request_for({**BASE, "search": {"tool_version": "web_search_20250305"}})


def test_allowed_and_blocked_domains_cannot_both_be_set():
    """Anthropic refuses the pair with a 400; better to say so before the run."""
    with pytest.raises(ValidationError, match="one or the other"):
        search(allowed_domains=["cdc.gov"], blocked_domains=["example.com"])


@pytest.mark.parametrize("domain", ["https://cdc.gov", "http://who.int/"])
def test_a_domain_with_a_scheme_is_refused_rather_than_rewritten(domain):
    with pytest.raises(ValidationError, match="without the http"):
        search(allowed_domains=[domain])


def test_every_user_location_field_goes_through():
    fields = {
        "city": "Paris",
        "region": "Ile-de-France",
        "country": "FR",
        "timezone": "Europe/Paris",
    }

    assert tool(user_location=fields)["user_location"] == {"type": "approximate", **fields}


@pytest.mark.parametrize("version", ["web_search_20250305", "web_search_20260209"])
def test_response_inclusion_needs_a_tool_version_that_has_it(version):
    with pytest.raises(ValidationError, match="web_search_20260318"):
        search(tool_version=version, response_inclusion="excluded")


def test_tool_choice_any_makes_claude_use_a_tool_first():
    assert search(tool_choice="any")["tool_choice"] == {"type": "any"}


def test_tool_choice_is_left_to_anthropic_unless_set():
    assert "tool_choice" not in search()


# -- what validation refuses -------------------------------------------------


def searching(**settings):
    return {"search": {"web_search": True, **settings}}


@pytest.mark.parametrize(
    "block, match",
    [
        ({"max_tokens": 0}, "max_tokens"),
        ({"effort": "adaptive"}, "effort"),  # a thinking mode, not an effort level
        ({"temperature": 1.5}, "temperature"),
        ({"temperature": -0.1}, "temperature"),
        ({"top_p": 1.1}, "top_p"),
        ({"top_k": 0}, "top_k"),
        ({"thinking": {"type": "extended"}}, "thinking.type"),
        ({"thinking": {"type": "enabled", "budget_tokens": 1023}}, "budget_tokens"),
        (searching(tool_version="web_search_2099"), "tool_version"),
        (searching(max_uses=0), "max_uses"),
        (searching(user_location={"country": "USA"}), "user_location.country"),
        (searching(allowed_callers=["anyone"]), r"allowed_callers\.0"),
        (searching(allowed_callers=[]), "at least 1"),
        (searching(response_inclusion="partial"), "response_inclusion"),
        (searching(tool_choice="required"), "search.tool_choice"),
    ],
)
def test_a_value_the_field_does_not_take_is_refused(block, match):
    """One row for each bound and each fixed set of values a field takes."""
    with pytest.raises(ValidationError, match=match):
        request_for({**BASE, "max_tokens": 16000, **block})
