"""Real Interactions API calls, end to end through `run_audit`.

Deselected by default. Run with `uv run pytest -m live -k gemini`, with the
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
from ai_taxman.providers.gemini import PROVIDER

#: The variable the SDK reads by convention, or that name behind the prefix in
#: TAXMAN_LIVE_KEY_PREFIX: `MY_` reads MY_GEMINI_API_KEY. Set the prefix in your
#: shell, never in the repo; taxman itself never reads it.
KEY = os.environ.get("TAXMAN_LIVE_KEY_PREFIX", "") + PROVIDER.default_api_key_env

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get(KEY), reason=f"{KEY} is not exported"),
]

#: A cheap current Gemini 3 model, so thinking_level applies.
LITE = "gemini-3.1-flash-lite"


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
        "audit: probe\nprovider: gemini\nmessages: messages/probe.txt\n"
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


def step_types(record):
    return [step["type"] for step in record.raw["steps"]]


async def test_a_plain_message(tmp_path):
    record = await collect(tmp_path, f"model:\n  name: {LITE}\n")

    assert record.raw["status"] == "completed"
    assert "model_output" in step_types(record)
    assert record.model == LITE


async def test_a_system_prompt_is_accepted(tmp_path):
    record = await collect(
        tmp_path, f"model:\n  name: {LITE}\n", system_prompt="Answer in exactly one word."
    )

    assert record.raw["status"] == "completed"


async def test_sampling_settings_are_accepted(tmp_path):
    """The API reference leaves these out of GenerationConfig; the guides use them."""
    record = await collect(tmp_path, f"model:\n  name: {LITE}\n  temperature: 0.2\n  top_p: 0.9\n")

    assert record.raw["status"] == "completed"


async def test_thinking_settings_are_accepted(tmp_path):
    record = await collect(
        tmp_path,
        f"model:\n  name: {LITE}\n  thinking_level: low\n  thinking_summaries: auto\n",
    )

    assert record.raw["status"] == "completed"


async def test_running_out_of_tokens_is_recorded_not_failed(tmp_path):
    record = await collect(
        tmp_path,
        f"model:\n  name: {LITE}\n  max_output_tokens: 4\n",
        message="Write a paragraph about the sea.",
    )

    assert record.raw["status"] in {"incomplete", "completed"}


async def test_google_search(tmp_path):
    record = await collect(
        tmp_path,
        f"""\
        model:
          name: {LITE}
          search:
            web_search: true
        """,
        message="Search the web: what is the capital of Australia?",
    )

    assert step_types(record)[:2] == ["google_search_call", "google_search_result"]
