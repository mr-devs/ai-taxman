"""Pulling `text` and `usage` out of a raw Responses API payload.

Pure function, recorded fixtures, no network.
"""

import json
from pathlib import Path

import pytest

from ai_taxman.providers.openai.provider import OpenAIProvider

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def provider():
    return OpenAIProvider()


def test_extracts_the_assistant_text(provider):
    assert provider.extract(load("response_basic")).text == "Paris is the capital of France."


def test_extracts_usage(provider):
    usage = provider.extract(load("response_basic")).usage

    assert usage["input_tokens"] == 14
    assert usage["output_tokens"] == 231
    assert usage["reasoning_tokens"] == 192


def test_skips_reasoning_items_when_finding_text(provider):
    """Reasoning items come first in the output list and carry no text."""
    assert "reasoning" not in (provider.extract(load("response_basic")).text or "")


def test_extracts_a_refusal_as_the_text(provider):
    assert provider.extract(load("response_refusal")).text == "I can't help with that."


def test_incomplete_response_has_no_text_but_still_has_usage(provider):
    extracted = provider.extract(load("response_incomplete"))

    assert extracted.text is None
    assert extracted.usage["output_tokens"] == 64


def test_extracts_text_alongside_a_web_search_call(provider):
    assert provider.extract(load("response_web_search")).text == "The election is on 3 November."


def test_joins_multiple_text_parts(provider):
    raw = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "first"},
                    {"type": "output_text", "text": "second"},
                ],
            }
        ]
    }

    assert provider.extract(raw).text == "first\nsecond"


def test_falls_back_to_output_text_when_present(provider):
    assert provider.extract({"output_text": "shortcut"}).text == "shortcut"


@pytest.mark.parametrize("raw", [{}, {"output": []}, {"output": "not a list"}, {"usage": None}])
def test_never_raises_on_an_unexpected_shape(provider, raw):
    extracted = provider.extract(raw)

    assert extracted.text is None or isinstance(extracted.text, str)


def test_missing_usage_is_none(provider):
    assert provider.extract({"output": []}).usage is None
