"""Real Messages API calls, end to end through `run_audit`.

Deselected by default. Run with `uv run pytest -m live -k anthropic`, with
ANTHROPIC_API_KEY exported. Each test sends one short message to a cheap model,
so a full pass costs a few cents.
"""

import json
import os
import textwrap

import pytest

from ai_taxman.core.config import load_audit
from ai_taxman.core.discovery import write_marker
from ai_taxman.core.runner import run_audit_async

KEY = "ANTHROPIC_API_KEY"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get(KEY), reason=f"{KEY} is not exported"),
]

#: The cheapest current model, for everything that does not need a newer one.
HAIKU = "claude-haiku-4-5-20251001"


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
        "audit: probe\nprovider: anthropic\nmessages: messages/probe.txt\n"
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


async def test_a_plain_message(tmp_path):
    record = await collect(tmp_path, f"model:\n  name: {HAIKU}\n  max_tokens: 64\n")

    assert record.raw["type"] == "message"
    assert record.model == HAIKU


async def test_a_system_prompt_is_accepted(tmp_path):
    record = await collect(
        tmp_path,
        f"model:\n  name: {HAIKU}\n  max_tokens: 64\n",
        system_prompt="Answer in exactly one word.",
    )

    assert record.raw["stop_reason"] == "end_turn"


async def test_running_out_of_tokens_is_recorded_not_failed(tmp_path):
    record = await collect(
        tmp_path,
        f"model:\n  name: {HAIKU}\n  max_tokens: 4\n",
        message="Write a paragraph about the sea.",
    )

    assert record.raw["stop_reason"] == "max_tokens"


async def test_effort_and_thinking_display_on_a_current_model(tmp_path):
    record = await collect(
        tmp_path,
        """\
        model:
          name: claude-sonnet-5-5
          max_tokens: 2048
          effort: low
          thinking:
            type: adaptive
            display: summarized
        """,
    )

    assert record.raw["type"] == "message"


async def test_web_search_called_directly(tmp_path):
    """Haiku 4.5 has no programmatic tool calling, so it needs `allowed_callers: [direct]`."""
    record = await collect(
        tmp_path,
        f"""\
        model:
          name: {HAIKU}
          max_tokens: 512
          search:
            web_search: true
            max_uses: 1
            allowed_callers: [direct]
        """,
        message="Search the web: what is the capital of Australia?",
    )

    assert any(block["type"] == "server_tool_use" for block in record.raw["content"])
