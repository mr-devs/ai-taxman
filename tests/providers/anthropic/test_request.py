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
