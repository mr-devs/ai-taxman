"""Turning an audit's `model:` block into Responses API keyword arguments.

Pure function, no network.
"""

from typing import get_args

import pytest
from pydantic import ValidationError

from ai_taxman.core.messages import Message
from ai_taxman.providers.base import Request
from ai_taxman.providers.openai.config import OpenAIModelConfig
from ai_taxman.providers.openai.models import REASONING_EFFORTS, ReasoningEffort
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


@pytest.mark.parametrize("effort", REASONING_EFFORTS)
def test_every_documented_effort_level_is_accepted(provider, effort):
    """The Responses API documents none | minimal | low | medium | high | xhigh | max.

    Rejecting one taxman has not heard of turns a valid audit into a hard error
    before anything is sent.
    """
    payload = request_for({"name": "gpt-5", "reasoning_effort": effort})

    assert payload["reasoning"] == {"effort": effort}


def test_the_effort_list_and_the_validated_type_cannot_drift():
    """One source of truth, so a new level only has to be added once."""
    assert set(REASONING_EFFORTS) == set(get_args(ReasoningEffort))


def test_web_search_becomes_a_tool(provider):
    payload = request_for({"name": "gpt-5", "web_search": True})

    assert payload["tools"] == [{"type": "web_search"}]


def test_web_search_false_adds_no_tools(provider):
    assert "tools" not in request_for({"name": "gpt-5", "web_search": False})


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
    """`taxman init` writes keys with no value; YAML reads those as None."""
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
