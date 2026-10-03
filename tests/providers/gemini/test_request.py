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
    message = Message(id="m0000", text=text, hash="sha256:x", line_number=1)
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
