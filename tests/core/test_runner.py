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
from ai_taxman.core.messages import message_id
from ai_taxman.core.runner import RunResult, run_audit_async
from ai_taxman.core.writer import read_jsonl

#: The ids of the three messages each test project starts with.
ONE, TWO, THREE = (message_id(text) for text in ("one", "two", "three"))


def write_audit(project, body):
    path = project / "audits" / "probe.yaml"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return load_audit(path)


async def test_writes_one_record_per_message(audit, fake_provider):
    result = await run_audit_async(audit())

    assert isinstance(result, RunResult)
    assert result.n_ok == 3
    assert len(list(read_jsonl(result.output_path))) == 3


async def test_every_message_and_repeat_pair_appears_exactly_once(audit, fake_provider):
    result = await run_audit_async(audit("execution:\n  repeats: 3"))

    pairs = [(r.message_id, r.repeat) for r in read_jsonl(result.output_path)]

    assert sorted(pairs) == sorted(
        (message_id, repeat) for message_id in (ONE, TWO, THREE) for repeat in range(3)
    )


async def test_repeats_of_one_message_are_sent_together_not_in_passes(audit, fake_provider):
    """The stated requirement: repeats go out concurrently, not as sequential passes."""
    await run_audit_async(audit("execution:\n  repeats: 3\n  max_concurrency: 3"))

    first_three = [(r.message.id, r.repeat) for r in fake_provider.sent[:3]]

    assert first_three == [(ONE, 0), (ONE, 1), (ONE, 2)]


async def test_concurrency_is_capped(audit, fake_provider):
    fake_provider.delay = 0.01

    await run_audit_async(audit("execution:\n  repeats: 5\n  max_concurrency: 2"))

    assert fake_provider.peak_concurrency <= 2


async def test_concurrency_is_actually_used(audit, fake_provider):
    fake_provider.delay = 0.01

    await run_audit_async(audit("execution:\n  repeats: 5\n  max_concurrency: 4"))

    assert fake_provider.peak_concurrency > 1


async def test_records_carry_the_audit_envelope(audit, fake_provider):
    result = await run_audit_async(audit())

    record = next(iter(read_jsonl(result.output_path)))

    assert record.audit == "probe"
    assert record.run_id == result.run_id
    assert record.provider == "fake"
    assert record.model == "fake-1"
    assert record.message in {"one", "two", "three"}
    assert record.status == "ok"
    assert record.latency_ms >= 0


async def test_records_carry_the_response_verbatim_and_nothing_derived(audit, fake_provider):
    """`raw` is the whole response. Nothing is parsed out of it at collection."""
    result = await run_audit_async(audit())

    record = next(iter(read_jsonl(result.output_path)))

    assert record.raw["echo"] == record.message
    assert record.raw["usage"] == {"input_tokens": 1, "output_tokens": 2}
    assert not hasattr(record, "text")


async def test_a_failure_is_recorded_as_an_error_row(audit, fake_provider):
    """And the run goes on: the other messages are still sent and answered."""
    fake_provider.failures = {TWO: [RuntimeError("nope")]}

    result = await run_audit_async(audit())

    rows = list(read_jsonl(result.output_path))
    failed = [r for r in rows if r.status == "error"]

    assert (result.n_ok, result.n_error, len(rows)) == (2, 1, 3)
    assert len(failed) == 1
    assert failed[0].message_id == TWO
    assert "nope" in failed[0].error
    assert failed[0].raw == {}


async def test_retryable_failures_are_retried_then_succeed(audit, fake_provider):
    fake_provider.retryable = (TimeoutError,)
    fake_provider.failures = {ONE: [TimeoutError("slow"), TimeoutError("slow")]}

    result = await run_audit_async(audit(), backoff_base=0)

    assert result.n_error == 0
    record = next(r for r in read_jsonl(result.output_path) if r.message_id == ONE)
    assert record.attempts == 3


async def test_retries_stop_at_max_retries(audit, fake_provider):
    fake_provider.retryable = (TimeoutError,)
    fake_provider.failures = {ONE: [TimeoutError("slow")] * 10}

    result = await run_audit_async(audit("execution:\n  max_retries: 2"), backoff_base=0)

    record = next(r for r in read_jsonl(result.output_path) if r.message_id == ONE)
    assert record.status == "error"
    assert record.attempts == 3
    assert result.n_error == 1


async def test_non_retryable_failures_are_not_retried(audit, fake_provider):
    fake_provider.retryable = (TimeoutError,)
    fake_provider.failures = {ONE: [ValueError("bad request")] * 5}

    await run_audit_async(audit(), backoff_base=0)

    attempts = [r for r in fake_provider.sent if r.message.id == ONE]
    assert len(attempts) == 1


async def test_on_error_stop_sends_nothing_after_the_failure(audit, fake_provider):
    """One request at a time, so the one that failed is the last one sent: the
    slot it frees must not go to the next message before the stop is seen."""
    fake_provider.failures = {ONE: [RuntimeError("nope")]}

    result = await run_audit_async(
        audit("execution:\n  max_concurrency: 1\n  on_error: stop"),
        backoff_base=0,
    )

    assert [request.message.id for request in fake_provider.sent] == [ONE]
    assert result.stopped_early is True
    assert result.stop_signal is None
    assert (result.n_ok, result.n_error) == (0, 1)


async def test_a_run_stopped_by_a_signal_says_which(audit, fake_provider):
    """`kill <pid>` is not `on_error: stop`, and the user is told which it was."""
    import os
    import signal

    def interrupt():
        # Only once the runner has the signal: unhandled, it would end pytest.
        assert signal.getsignal(signal.SIGUSR1) is not signal.SIG_DFL
        os.kill(os.getpid(), signal.SIGUSR1)

    fake_provider.delay = 0.05
    asyncio.get_running_loop().call_later(0.1, interrupt)

    result = await run_audit_async(
        audit("execution:\n  repeats: 20\n  max_concurrency: 1"),
        stop_signals=(signal.SIGUSR1,),
    )

    assert result.stopped_early is True
    assert result.stop_signal == signal.SIGUSR1
    assert result.n_ok < 60


async def test_provider_lifecycle_hooks_run_once(audit, fake_provider):
    await run_audit_async(audit())

    assert fake_provider.started == 1
    assert fake_provider.stopped == 1


async def test_provider_is_shut_down_even_when_the_run_blows_up(audit, fake_provider):
    """Cancellation propagates, but the provider still gets to close its client."""
    fake_provider.failures = {ONE: [asyncio.CancelledError()]}

    with pytest.raises(asyncio.CancelledError):
        await run_audit_async(audit())

    assert fake_provider.stopped == 1


async def test_the_manifest_names_a_linked_message_file_relative_to_the_project(
    project, audit, fake_provider, tmp_path_factory
):
    """A messages folder linked in from elsewhere is still named as the project names it."""
    storage = tmp_path_factory.mktemp("storage")
    (project / "messages").rename(storage / "messages")
    (project / "messages").symlink_to(storage / "messages", target_is_directory=True)

    result = await run_audit_async(audit())
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert manifest["messages_path"] == "messages/probe.txt"


async def test_writes_a_manifest_beside_the_jsonl(audit, fake_provider):
    result = await run_audit_async(audit("execution:\n  repeats: 2"))

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
    # The messages a resumed run is checked against: ids, not the file's bytes,
    # so editing a comment does not count as changing the audit.
    assert manifest["message_ids"] == [ONE, TWO, THREE]
    assert "messages_hash" not in manifest


async def test_output_goes_to_the_configured_directory(project, audit, fake_provider):
    result = await run_audit_async(audit())

    assert result.output_path.parent == project / "data" / "probe" / result.run_id
    assert result.output_path.name == "responses.jsonl"


async def test_compression_is_honoured(audit, fake_provider):
    result = await run_audit_async(
        audit(
            "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n  compress: true",
        )
    )

    assert result.output_path.name == "responses.jsonl.gz"
    assert len(list(read_jsonl(result.output_path))) == 3


async def test_the_model_block_is_validated_by_the_provider(project, fake_provider):
    config = write_audit(
        project,
        """
        audit: probe
        provider: fake
        messages: messages/probe.txt
        output:
          dir: data/{audit}/{run_id}
          log_dir: logs/{audit}
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
        output:
          dir: data/{audit}/{run_id}
          log_dir: logs/{audit}
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
        "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
        "system_prompt: prompts/neutral.txt\nmodel:\n  name: fake-1\n"
    )
    return write_audit(project, body)


async def test_the_system_prompt_goes_with_every_request(project, fake_provider):
    await run_audit_async(with_system_prompt(project))

    assert [r.system_prompt for r in fake_provider.sent] == ["Be terse."] * 3


async def test_without_a_system_prompt_none_is_sent(audit, fake_provider):
    result = await run_audit_async(audit())
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert all(r.system_prompt is None for r in fake_provider.sent)
    assert manifest["system_prompt_text"] is None


async def test_the_manifest_records_which_system_prompt_was_sent(project, fake_provider):
    """By its path and its text; the text is what a resumed run is checked against."""
    result = await run_audit_async(with_system_prompt(project))
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert manifest["system_prompt_path"] == "prompts/neutral.txt"
    assert manifest["system_prompt_text"] == "Be terse."
    assert "system_prompt_hash" not in manifest


async def test_a_missing_system_prompt_is_reported_before_anything_is_sent(project, fake_provider):
    config = with_system_prompt(project)
    (project / "prompts" / "neutral.txt").unlink()

    with pytest.raises(ConfigError, match="system_prompt"):
        await run_audit_async(config)

    assert fake_provider.sent == []
    assert not (project / "data").exists()


async def test_progress_callback_sees_every_record(audit, fake_provider):
    seen = []

    await run_audit_async(audit("execution:\n  repeats: 2"), on_record=seen.append)

    assert len(seen) == 6


async def test_batch_mode_is_rejected_when_the_provider_cannot_do_it(audit, fake_provider):
    with pytest.raises(ConfigError, match="batch"):
        await run_audit_async(audit("execution:\n  batch: true"))


@pytest.mark.parametrize("key", ["dir", "log_dir"])
async def test_an_unknown_placeholder_in_a_path_is_refused_before_anything_is_sent(
    project, audit, fake_provider, key
):
    with pytest.raises(ConfigError, match="date"):
        paths = {"dir": "data/{audit}/{run_id}", "log_dir": "logs/{audit}", key: "out/{date}"}
        block = "output:\n" + "".join(f"  {name}: {value}\n" for name, value in paths.items())
        await run_audit_async(audit(block))

    assert fake_provider.sent == []
    assert not (project / "out").exists()


async def test_a_provider_that_cannot_start_fails_cleanly(project, audit, fake_provider):
    """A missing API key is a user error, not a traceback.

    The error names the provider, the provider is still shut down, and nothing is
    created until the run can actually produce data.
    """
    fake_provider.startup_error = RuntimeError("Missing credentials.")

    with pytest.raises(ProviderError, match="Missing credentials") as caught:
        await run_audit_async(audit())

    assert "fake" in str(caught.value)
    assert fake_provider.stopped == 1
    assert not (project / "data").exists()


async def test_the_model_name_comes_from_the_provider_not_the_yaml(audit, fake_provider):
    """Core must not assume the `model:` block has a `name` key."""
    calls = []
    original = fake_provider.describe_model
    fake_provider.describe_model = lambda model: (calls.append(model), "described")[1]
    try:
        result = await run_audit_async(audit())
    finally:
        fake_provider.describe_model = original

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert calls
    assert all(r.model == "described" for r in read_jsonl(result.output_path))
    assert manifest["model"] == "described"


async def test_a_provider_needing_a_key_gets_the_resolved_one(audit, fake_provider, monkeypatch):
    fake_provider.requires_api_key = True
    monkeypatch.setenv("TAXMAN_FAKE_API_KEY", "sk-resolved")

    await run_audit_async(audit("api_key_env: TAXMAN_FAKE_API_KEY"))

    assert fake_provider.api_key == "sk-resolved"


async def test_a_missing_api_key_stops_the_run_before_anything_is_sent(
    project, audit, fake_provider, monkeypatch
):
    fake_provider.requires_api_key = True
    monkeypatch.delenv("TAXMAN_FAKE_API_KEY", raising=False)

    with pytest.raises(MissingApiKeyError, match="TAXMAN_FAKE_API_KEY"):
        await run_audit_async(audit("api_key_env: TAXMAN_FAKE_API_KEY"))

    assert fake_provider.sent == []
    assert not (project / "data").exists()


async def test_an_audit_without_api_key_env_is_rejected_when_the_provider_needs_one(
    audit, fake_provider
):
    """There is no configuration command to send the user to; the fix is the file."""
    fake_provider.requires_api_key = True

    with pytest.raises(ConfigError, match="api_key_env") as caught:
        await run_audit_async(audit())

    message = str(caught.value)
    assert "setup" not in message
    assert "export" in message.lower()


async def test_the_unedited_placeholder_stops_the_run(audit, fake_provider):
    """A scaffold nobody filled in must fail before anything is sent, and say
    what to put there rather than call the placeholder an unset variable."""
    fake_provider.requires_api_key = True

    with pytest.raises(ConfigError, match="insert_api_key_env_var_here") as caught:
        await run_audit_async(audit("api_key_env: <insert_api_key_env_var_here>"))

    assert "FAKE_API_KEY" in str(caught.value)
    assert fake_provider.sent == []


async def test_a_key_pasted_into_api_key_env_is_never_printed_back(audit, fake_provider):
    """`api_key_env:` names a variable. A value that cannot be one is most often
    the key itself, and an error repeating it puts the key on screen and in CI logs."""
    fake_provider.requires_api_key = True

    with pytest.raises(ConfigError, match="api_key_env") as caught:
        await run_audit_async(audit("api_key_env: sk-proj-abc123"))

    assert "sk-proj-abc123" not in str(caught.value)
    assert fake_provider.sent == []


async def test_a_provider_that_needs_no_key_does_not_require_the_field(audit, fake_provider):
    """The fake provider authenticates against nothing."""
    result = await run_audit_async(audit())

    assert result.n_ok == 3
    assert fake_provider.api_key is None


@pytest.mark.parametrize("text", ["Answer briefly.", None])
def test_a_preflight_gives_the_system_prompt_text_it_would_send(text, tmp_path):
    from ai_taxman.core.prompts import SystemPrompt
    from ai_taxman.core.runner import Preflight

    prompt = SystemPrompt(text=text, path=tmp_path / "p.txt") if text is not None else None
    checked = Preflight(messages=[], system_prompt=prompt, model={}, api_key=None)

    assert checked.system_text == text


async def test_the_caller_hears_of_its_run_once_it_is_claimed(audit, fake_provider):
    """Its manifest written, nothing sent yet - and never for a run with nothing to send."""
    from ai_taxman.core.runner import preflight, run_checked_async

    config = audit()
    claimed = []

    def on_claim(plan):
        written = (plan.directory / "manifest.json").is_file()
        claimed.append((plan.run_id, written, len(fake_provider.sent)))

    first = await run_checked_async(
        config, preflight(config, fake_provider), provider=fake_provider, on_claim=on_claim
    )
    again = await run_checked_async(
        config, preflight(config, fake_provider), provider=fake_provider, on_claim=on_claim
    )

    assert claimed == [(first.run_id, True, 0)]
    assert again.already_complete is True
