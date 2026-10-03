import asyncio
import json
import textwrap

import pytest

from ai_taxman.core.config import load_audit
from ai_taxman.core.errors import (
    ConfigError,
    MessageFileError,
    MissingApiKeyError,
    ProviderError,
)
from ai_taxman.core.runner import RunResult, run_audit_async
from ai_taxman.core.writer import read_jsonl


@pytest.fixture
def project(tmp_path):
    """A project directory with three messages and a `fake` provider audit."""
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "audits").mkdir()
    return tmp_path


def write_audit(project, body):
    path = project / "audits" / "probe.yaml"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return load_audit(path)


def audit(project, *blocks):
    """Build a `fake`-provider audit, appending whole YAML blocks verbatim."""
    body = "audit: probe\nprovider: fake\nmessages: messages/probe.txt\nmodel:\n  name: fake-1\n"
    return write_audit(project, body + "".join(block.rstrip() + "\n" for block in blocks))


async def test_writes_one_record_per_message(project, fake_provider):
    result = await run_audit_async(audit(project))

    assert isinstance(result, RunResult)
    assert result.n_ok == 3
    assert len(list(read_jsonl(result.output_path))) == 3


async def test_repeats_multiply_the_record_count(project, fake_provider):
    result = await run_audit_async(audit(project, "execution:\n  repeats: 4"))

    assert result.n_ok == 12
    assert len(list(read_jsonl(result.output_path))) == 12


async def test_every_message_and_repeat_pair_appears_exactly_once(project, fake_provider):
    result = await run_audit_async(audit(project, "execution:\n  repeats: 3"))

    pairs = [(r.message_id, r.repeat) for r in read_jsonl(result.output_path)]

    assert sorted(pairs) == sorted(
        (message_id, repeat) for message_id in ("m0000", "m0001", "m0002") for repeat in range(3)
    )


async def test_repeats_of_one_message_are_sent_together_not_in_passes(project, fake_provider):
    """The stated requirement: repeats go out concurrently, not as sequential passes."""
    await run_audit_async(audit(project, "execution:\n  repeats: 3\n  max_concurrency: 3"))

    first_three = [(r.message.id, r.repeat) for r in fake_provider.sent[:3]]

    assert first_three == [("m0000", 0), ("m0000", 1), ("m0000", 2)]


async def test_concurrency_is_capped(project, fake_provider):
    fake_provider.delay = 0.01

    await run_audit_async(audit(project, "execution:\n  repeats: 5\n  max_concurrency: 2"))

    assert fake_provider.peak_concurrency <= 2


async def test_concurrency_is_actually_used(project, fake_provider):
    fake_provider.delay = 0.01

    await run_audit_async(audit(project, "execution:\n  repeats: 5\n  max_concurrency: 4"))

    assert fake_provider.peak_concurrency > 1


async def test_shuffle_changes_the_dispatch_order(project, fake_provider):
    ordered = audit(project, "execution:\n  repeats: 20")
    await run_audit_async(ordered, max_concurrency=1)
    in_order = [(r.message.id, r.repeat) for r in fake_provider.sent]

    fake_provider.sent.clear()
    shuffled = audit(project, "execution:\n  repeats: 20\n  shuffle: true")
    await run_audit_async(shuffled, max_concurrency=1, seed=1234)
    after = [(r.message.id, r.repeat) for r in fake_provider.sent]

    assert sorted(after) == sorted(in_order)
    assert after != in_order


async def test_records_carry_the_audit_envelope(project, fake_provider):
    result = await run_audit_async(audit(project))

    record = next(iter(read_jsonl(result.output_path)))

    assert record.audit == "probe"
    assert record.run_id == result.run_id
    assert record.provider == "fake"
    assert record.model == "fake-1"
    assert record.message in {"one", "two", "three"}
    assert record.status == "ok"
    assert record.latency_ms >= 0


async def test_records_carry_the_response_verbatim_and_nothing_derived(project, fake_provider):
    """`raw` is the whole response. Nothing is parsed out of it at collection."""
    result = await run_audit_async(audit(project))

    record = next(iter(read_jsonl(result.output_path)))

    assert record.raw["echo"] == record.message
    assert record.raw["usage"] == {"input_tokens": 1, "output_tokens": 2}
    assert not hasattr(record, "text")


async def test_a_failing_message_does_not_stop_the_others(project, fake_provider):
    fake_provider.failures = {"m0001": [RuntimeError("nope")]}

    result = await run_audit_async(audit(project))

    assert result.n_ok == 2
    assert result.n_error == 1
    assert len(list(read_jsonl(result.output_path))) == 3


async def test_a_failure_is_recorded_as_an_error_row(project, fake_provider):
    fake_provider.failures = {"m0001": [RuntimeError("nope")]}

    result = await run_audit_async(audit(project))

    failed = [r for r in read_jsonl(result.output_path) if r.status == "error"]

    assert len(failed) == 1
    assert failed[0].message_id == "m0001"
    assert "nope" in failed[0].error
    assert failed[0].raw == {}


async def test_retryable_failures_are_retried_then_succeed(project, fake_provider):
    fake_provider.retryable = (TimeoutError,)
    fake_provider.failures = {"m0000": [TimeoutError("slow"), TimeoutError("slow")]}

    result = await run_audit_async(audit(project), backoff_base=0)

    assert result.n_error == 0
    record = next(r for r in read_jsonl(result.output_path) if r.message_id == "m0000")
    assert record.attempts == 3


async def test_retries_stop_at_max_retries(project, fake_provider):
    fake_provider.retryable = (TimeoutError,)
    fake_provider.failures = {"m0000": [TimeoutError("slow")] * 10}

    result = await run_audit_async(audit(project, "execution:\n  max_retries: 2"), backoff_base=0)

    record = next(r for r in read_jsonl(result.output_path) if r.message_id == "m0000")
    assert record.status == "error"
    assert record.attempts == 3
    assert result.n_error == 1


async def test_non_retryable_failures_are_not_retried(project, fake_provider):
    fake_provider.retryable = (TimeoutError,)
    fake_provider.failures = {"m0000": [ValueError("bad request")] * 5}

    await run_audit_async(audit(project), backoff_base=0)

    attempts = [r for r in fake_provider.sent if r.message.id == "m0000"]
    assert len(attempts) == 1


async def test_on_error_stop_halts_the_run(project, fake_provider):
    fake_provider.delay = 0.01
    fake_provider.failures = {"m0000": [RuntimeError("nope")]}

    result = await run_audit_async(
        audit(project, "execution:\n  repeats: 20\n  max_concurrency: 1\n  on_error: stop"),
        backoff_base=0,
    )

    assert result.stopped_early is True
    assert result.n_ok + result.n_error < 60


async def test_provider_lifecycle_hooks_run_once(project, fake_provider):
    await run_audit_async(audit(project))

    assert fake_provider.started == 1
    assert fake_provider.stopped == 1


async def test_provider_is_shut_down_even_when_the_run_blows_up(project, fake_provider):
    """Cancellation propagates, but the provider still gets to close its client."""
    fake_provider.failures = {"m0000": [asyncio.CancelledError()]}

    with pytest.raises(asyncio.CancelledError):
        await run_audit_async(audit(project))

    assert fake_provider.stopped == 1


async def test_the_manifest_names_the_message_file_relative_to_the_project(project, fake_provider):
    """A manifest is committed with the data; it must not carry the author's home."""
    result = await run_audit_async(audit(project))
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert manifest["messages_path"] == "messages/probe.txt"


async def test_writes_a_manifest_beside_the_jsonl(project, fake_provider):
    result = await run_audit_async(audit(project, "execution:\n  repeats: 2"))

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert result.manifest_path.parent == result.output_path.parent
    assert manifest["audit"] == "probe"
    assert manifest["run_id"] == result.run_id
    assert manifest["n_messages"] == 3
    assert manifest["repeats"] == 2
    assert manifest["n_ok"] == 6
    assert manifest["n_error"] == 0
    assert manifest["started_at"] and manifest["finished_at"]
    assert manifest["config"]["execution"]["repeats"] == 2
    assert manifest["messages_hash"].startswith("sha256:")


async def test_output_goes_to_the_configured_directory(project, fake_provider):
    result = await run_audit_async(audit(project))

    assert result.output_path.parent == project / "data" / "probe" / result.run_id
    assert result.output_path.name == "responses.jsonl"


async def test_compression_is_honoured(project, fake_provider):
    result = await run_audit_async(audit(project, "output:\n  compress: true"))

    assert result.output_path.name == "responses.jsonl.gz"
    assert len(list(read_jsonl(result.output_path))) == 3


async def test_the_model_block_is_validated_by_the_provider(project, fake_provider):
    config = write_audit(
        project,
        """
        audit: probe
        provider: fake
        messages: messages/probe.txt
        model:
          boom: true
        """,
    )

    with pytest.raises(ConfigError, match="boom"):
        await run_audit_async(config)


async def test_a_missing_message_file_is_reported_before_anything_is_sent(project, fake_provider):
    config = write_audit(
        project,
        """
        audit: probe
        provider: fake
        messages: messages/absent.txt
        model:
          name: fake-1
        """,
    )

    with pytest.raises(MessageFileError):
        await run_audit_async(config)

    assert fake_provider.sent == []


def with_system_prompt(project, text="Be terse.\n"):
    (project / "prompts").mkdir(exist_ok=True)
    (project / "prompts" / "neutral.txt").write_text(text, encoding="utf-8")
    body = (
        "audit: probe\nprovider: fake\nmessages: messages/probe.txt\n"
        "system_prompt: prompts/neutral.txt\nmodel:\n  name: fake-1\n"
    )
    return write_audit(project, body)


async def test_the_system_prompt_goes_with_every_request(project, fake_provider):
    await run_audit_async(with_system_prompt(project))

    assert [r.system_prompt for r in fake_provider.sent] == ["Be terse."] * 3


async def test_without_a_system_prompt_none_is_sent(project, fake_provider):
    await run_audit_async(audit(project))

    assert all(r.system_prompt is None for r in fake_provider.sent)


async def test_the_manifest_records_which_system_prompt_was_sent(project, fake_provider):
    from ai_taxman.core.messages import hash_message

    result = await run_audit_async(with_system_prompt(project))
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert manifest["system_prompt_path"] == "prompts/neutral.txt"
    assert manifest["system_prompt_hash"] == hash_message("Be terse.")


async def test_every_record_carries_the_system_prompt_hash(project, fake_provider):
    from ai_taxman.core.messages import hash_message

    result = await run_audit_async(with_system_prompt(project))

    rows = list(read_jsonl(result.output_path))
    assert {row.system_prompt_hash for row in rows} == {hash_message("Be terse.")}


async def test_a_missing_system_prompt_is_reported_before_anything_is_sent(project, fake_provider):
    config = with_system_prompt(project)
    (project / "prompts" / "neutral.txt").unlink()

    with pytest.raises(ConfigError, match="system_prompt"):
        await run_audit_async(config)

    assert fake_provider.sent == []
    assert not (project / "data").exists()


async def test_progress_callback_sees_every_record(project, fake_provider):
    seen = []

    await run_audit_async(audit(project, "execution:\n  repeats: 2"), on_record=seen.append)

    assert len(seen) == 6


async def test_batch_mode_is_rejected_when_the_provider_cannot_do_it(project, fake_provider):
    with pytest.raises(NotImplementedError, match="batch"):
        await run_audit_async(audit(project, "execution:\n  batch: true"))


async def test_a_provider_that_cannot_start_fails_cleanly(project, fake_provider):
    """A missing API key is a user error, not a traceback."""
    fake_provider.startup_error = RuntimeError("Missing credentials.")

    with pytest.raises(ProviderError, match="Missing credentials"):
        await run_audit_async(audit(project))


async def test_a_provider_that_cannot_start_is_still_shut_down(project, fake_provider):
    fake_provider.startup_error = RuntimeError("Missing credentials.")

    with pytest.raises(ProviderError):
        await run_audit_async(audit(project))

    assert fake_provider.stopped == 1


async def test_a_provider_that_cannot_start_leaves_no_output_behind(project, fake_provider):
    """Nothing is created until the run can actually produce data."""
    fake_provider.startup_error = RuntimeError("Missing credentials.")

    with pytest.raises(ProviderError):
        await run_audit_async(audit(project))

    assert not (project / "data").exists()


async def test_the_provider_name_is_named_in_the_startup_error(project, fake_provider):
    fake_provider.startup_error = RuntimeError("Missing credentials.")

    with pytest.raises(ProviderError, match="fake"):
        await run_audit_async(audit(project))


async def test_the_model_name_comes_from_the_provider_not_the_yaml(project, fake_provider):
    """Core must not assume the `model:` block has a `name` key."""
    calls = []
    original = fake_provider.describe_model
    fake_provider.describe_model = lambda model: (calls.append(model), "described")[1]
    try:
        result = await run_audit_async(audit(project))
    finally:
        fake_provider.describe_model = original

    assert calls
    assert all(r.model == "described" for r in read_jsonl(result.output_path))


async def test_the_manifest_model_also_comes_from_the_provider(project, fake_provider):
    fake_provider.describe_model = lambda model: "described"

    result = await run_audit_async(audit(project))

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["model"] == "described"


async def test_a_provider_needing_a_key_gets_the_resolved_one(project, fake_provider, monkeypatch):
    fake_provider.requires_api_key = True
    monkeypatch.setenv("TAXMAN_FAKE_API_KEY", "sk-resolved")

    await run_audit_async(audit(project, "api_key_env: TAXMAN_FAKE_API_KEY"))

    assert fake_provider.api_key == "sk-resolved"


async def test_a_missing_api_key_stops_the_run_before_anything_is_sent(
    project, fake_provider, monkeypatch
):
    fake_provider.requires_api_key = True
    monkeypatch.delenv("TAXMAN_FAKE_API_KEY", raising=False)

    with pytest.raises(MissingApiKeyError, match="TAXMAN_FAKE_API_KEY"):
        await run_audit_async(audit(project, "api_key_env: TAXMAN_FAKE_API_KEY"))

    assert fake_provider.sent == []
    assert not (project / "data").exists()


async def test_an_audit_without_api_key_env_is_rejected_when_the_provider_needs_one(
    project, fake_provider
):
    fake_provider.requires_api_key = True

    with pytest.raises(ConfigError, match="api_key_env"):
        await run_audit_async(audit(project))


async def test_the_missing_field_error_points_at_the_audit_not_a_command(project, fake_provider):
    """There is no configuration command to send the user to; the fix is the file."""
    fake_provider.requires_api_key = True

    with pytest.raises(ConfigError) as exc:
        await run_audit_async(audit(project))

    message = str(exc.value)
    assert "setup" not in message
    assert "export" in message.lower()


async def test_the_unedited_placeholder_stops_the_run(project, fake_provider):
    """A scaffold nobody filled in must fail before anything is sent."""
    fake_provider.requires_api_key = True

    with pytest.raises(MissingApiKeyError, match="insert_api_key_env_var_here"):
        await run_audit_async(audit(project, "api_key_env: <insert_api_key_env_var_here>"))

    assert fake_provider.sent == []


async def test_a_provider_that_needs_no_key_does_not_require_the_field(project, fake_provider):
    """The fake provider authenticates against nothing."""
    result = await run_audit_async(audit(project))

    assert result.n_ok == 3
    assert fake_provider.api_key is None
