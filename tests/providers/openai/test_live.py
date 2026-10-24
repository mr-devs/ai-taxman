"""Real Responses API calls, end to end through `run_audit`.

Deselected by default. Run with `uv run pytest -m live -k openai`, with the
variable `KEY` names exported. Each test sends one short message to a cheap model,
so a full pass costs a few cents.
"""

import json
import os
import textwrap

import pytest

from ai_taxman.core.config import load_audit
from ai_taxman.core.discovery import write_marker
from ai_taxman.core.runner import run_audit_async
from ai_taxman.providers.openai import PROVIDER

#: The variable the SDK reads by convention, or that name behind the prefix in
#: TAXMAN_LIVE_KEY_PREFIX: `MY_` reads MY_OPENAI_API_KEY. Set the prefix in your
#: shell, never in the repo; taxman itself never reads it.
KEY = os.environ.get("TAXMAN_LIVE_KEY_PREFIX", "") + PROVIDER.default_api_key_env

pytestmark = [
    pytest.mark.live,
    pytest.mark.enable_socket,
    pytest.mark.skipif(not os.environ.get(KEY), reason=f"{KEY} is not exported"),
]

#: OpenAI's model for cost-sensitive work: a reasoning model that can search.
LUNA = "gpt-6-luna"


async def collect(tmp_path, model_block, *, system_prompt=None, message="Name one primary color."):
    """Run a one-message audit and return its single record."""
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text(message + "\n", encoding="utf-8")
    (tmp_path / "audits").mkdir()
    prompt_line = ""
    if system_prompt is not None:
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "terse.txt").write_text(system_prompt, encoding="utf-8")
        prompt_line = "system_prompt: prompts/terse.txt\n"
    write_marker(tmp_path)
    path = tmp_path / "audits" / "probe.yaml"
    path.write_text(
        "audit: probe\nprovider: openai\nmessages: messages/probe.txt\n"
        f"api_key_env: {KEY}\n{prompt_line}"
        "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
        "execution:\n  max_retries: 2\n" + textwrap.dedent(model_block),
        encoding="utf-8",
    )
    records = []
    await run_audit_async(load_audit(path), on_record=records.append)
    (record,) = records
    assert record.status == "ok", record.error
    assert json.loads(json.dumps(record.raw)) == record.raw
    return record


def output_types(record):
    return [item["type"] for item in record.raw["output"]]


async def test_a_plain_message(tmp_path):
    record = await collect(tmp_path, f"model:\n  name: {LUNA}\n")

    assert record.raw["status"] == "completed"
    assert "message" in output_types(record)
    assert record.model == LUNA


async def test_a_system_prompt_is_sent_as_instructions(tmp_path):
    record = await collect(
        tmp_path, f"model:\n  name: {LUNA}\n", system_prompt="Answer in exactly one word."
    )

    assert record.raw["status"] == "completed"
    assert record.raw["instructions"] == "Answer in exactly one word."


async def test_reasoning_effort_is_accepted(tmp_path):
    record = await collect(tmp_path, f"model:\n  name: {LUNA}\n  reasoning_effort: low\n")

    assert record.raw["status"] == "completed"
    assert record.raw["reasoning"]["effort"] == "low"


async def test_running_out_of_tokens_is_recorded_not_failed(tmp_path):
    record = await collect(
        tmp_path,
        f"model:\n  name: {LUNA}\n  max_output_tokens: 16\n",
        message="Write a paragraph about the sea.",
    )

    assert record.raw["status"] == "incomplete"
    assert record.raw["incomplete_details"]["reason"] == "max_output_tokens"


async def test_web_search(tmp_path):
    """The model decides whether to search, and answers what it already knows
    without one, so the message asks for something only a search can tell it."""
    record = await collect(
        tmp_path,
        f"""\
        model:
          name: {LUNA}
          search:
            web_search: true
        """,
        message="Search the web: what is the top story on bbc.com right now?",
    )

    assert "web_search_call" in output_types(record)
