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
