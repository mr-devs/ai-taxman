"""Logging: what a long run says about itself while it runs.

A collection can run for hours. Without a running commentary the only feedback
is a summary printed at the end, which is no use to someone watching a run they
started this morning - or to a run under `nohup`, which has no terminal at all.

Two rules matter more than the format. The library never configures logging for
its caller, and the API key never reaches a log record.
"""

import logging
import re

import pytest

from ai_taxman.core.logging import LOGGER_NAME, setup_logging
from ai_taxman.core.messages import message_id

#: The ids of the three messages each test project starts with.
ONE, TWO, THREE = (message_id(text) for text in ("one", "two", "three"))


@pytest.fixture(autouse=True)
def restore_logger():
    """Handlers are global; put the logger back the way the test found it."""
    logger = logging.getLogger(LOGGER_NAME)
    handlers, level, propagate = logger.handlers[:], logger.level, logger.propagate
    try:
        yield logger
    finally:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        for handler in handlers:
            logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate


def test_the_library_is_silent_until_someone_configures_it():
    """Importing taxman must not print. Only the CLI attaches handlers."""
    import ai_taxman  # noqa: F401

    logger = logging.getLogger(LOGGER_NAME)

    assert any(isinstance(handler, logging.NullHandler) for handler in logger.handlers)


def test_logs_go_to_stderr_by_default(capsys):
    """stdout stays the summary's, so `taxman collect x > out` separates them."""
    setup_logging()

    logging.getLogger(f"{LOGGER_NAME}.runner").info("hello")

    captured = capsys.readouterr()
    assert "hello" in captured.err
    assert "hello" not in captured.out


def test_a_log_file_takes_the_log_instead_of_the_terminal(tmp_path, capsys):
    target = tmp_path / "run.log"
    setup_logging(log_file=target)

    logging.getLogger(f"{LOGGER_NAME}.runner").info("hello")

    captured = capsys.readouterr()
    assert "hello" in target.read_text(encoding="utf-8")
    assert "hello" not in captured.err


def test_a_log_file_can_also_echo_to_the_terminal(tmp_path, capsys):
    """A foreground run keeps a log on disk and still narrates at the prompt."""
    target = tmp_path / "run.log"
    setup_logging(log_file=target, also_terminal=True)

    logging.getLogger(f"{LOGGER_NAME}.runner").info("hello")

    assert "hello" in target.read_text(encoding="utf-8")
    assert "hello" in capsys.readouterr().err


def test_the_log_file_is_created_with_its_parent_directory(tmp_path):
    target = tmp_path / "nested" / "deeper" / "run.log"

    setup_logging(log_file=target)
    logging.getLogger(LOGGER_NAME).info("hello")

    assert target.is_file()


def test_configuring_twice_does_not_double_every_line(capsys):
    setup_logging()
    setup_logging()

    logging.getLogger(LOGGER_NAME).info("once")

    assert capsys.readouterr().err.count("once") == 1


def test_records_carry_the_local_time_the_level_and_the_source(tmp_path, in_new_york):
    target = tmp_path / "run.log"
    setup_logging(log_file=target)

    logging.getLogger(f"{LOGGER_NAME}.runner").info("hello")

    line = target.read_text(encoding="utf-8")
    # The user's own time with its offset, written the way the records write theirs.
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}-0[45]:00 INFO ", line)
    assert f"{LOGGER_NAME}.runner" in line


def test_taxman_logs_do_not_leak_into_the_root_logger(capsys):
    """A caller's own logging configuration is theirs, not ours to inherit."""
    setup_logging()

    assert logging.getLogger(LOGGER_NAME).propagate is False


# --- what a run actually says --------------------------------------------


def read_log(path):
    return path.read_text(encoding="utf-8")


@pytest.fixture
def logged(tmp_path, restore_logger):
    """Capture a real run's log into a file, the way --log-file does."""
    target = tmp_path / "run.log"
    setup_logging(log_file=target)
    return target


async def test_a_run_announces_what_it_is_about_to_do(audit, fake_provider, logged):
    """The first lines answer 'what is this process doing?' without the audit file."""
    from ai_taxman.core.runner import run_audit_async

    result = await run_audit_async(audit("execution:\n  repeats: 2"))

    log = read_log(logged)
    assert result.run_id in log
    assert "probe" in log
    assert "fake" in log
    assert "6" in log  # 3 messages x 2 repeats, the number of responses to expect


async def test_every_response_is_logged_as_it_lands(audit, fake_provider, logged):
    from ai_taxman.core.runner import run_audit_async

    await run_audit_async(audit())

    log = read_log(logged)
    for expected in (ONE, TWO, THREE):
        assert expected in log


async def test_a_retry_is_logged_as_a_warning(audit, fake_provider, logged):
    from ai_taxman.core.runner import run_audit_async

    fake_provider.retryable = (TimeoutError,)
    fake_provider.failures = {TWO: [TimeoutError("slow")]}

    await run_audit_async(audit("execution:\n  max_retries: 2"))

    log = read_log(logged)
    assert "WARNING" in log
    assert "TimeoutError" in log


def failure_with_a_cause():
    """An error as SDKs raise them: a vague one of their own, over the real cause."""
    error = TimeoutError("Request timed out.")
    error.__cause__ = ConnectionResetError("the server hung up")
    return error


async def test_the_cause_under_an_error_is_logged(audit, fake_provider, logged):
    """An SDK's "Request timed out." says little; what it was raised from says what
    went wrong. Both the retry and the giving up say it."""
    from ai_taxman.core.runner import run_audit_async

    fake_provider.retryable = (TimeoutError,)
    fake_provider.failures = {TWO: [failure_with_a_cause(), failure_with_a_cause()]}

    await run_audit_async(audit("execution:\n  max_retries: 1"))

    log = read_log(logged)
    assert log.count("ConnectionResetError: the server hung up") == 2


async def test_a_failed_message_is_logged_as_an_error(audit, fake_provider, logged):
    from ai_taxman.core.runner import run_audit_async

    fake_provider.failures = {TWO: [RuntimeError("nope")]}

    await run_audit_async(audit())

    log = read_log(logged)
    assert "ERROR" in log
    assert TWO in log


async def test_the_end_of_the_run_is_logged_with_the_counts(audit, fake_provider, logged):
    from ai_taxman.core.runner import run_audit_async

    await run_audit_async(audit())

    log = read_log(logged)
    assert "complete" in log
    assert "responses.jsonl" in log


async def test_the_api_key_never_reaches_the_log(audit, fake_provider, logged, monkeypatch):
    """The one thing that must never be written down, at any level."""
    from ai_taxman.core.runner import run_audit_async

    monkeypatch.setenv("PROBE_KEY", "sk-do-not-log-me")
    fake_provider.requires_api_key = True

    await run_audit_async(audit("api_key_env: PROBE_KEY"))

    assert "sk-do-not-log-me" not in read_log(logged)
    assert fake_provider.api_key == "sk-do-not-log-me"


async def test_each_session_opens_its_log_with_the_variable_it_read_the_key_from(
    audit, fake_provider, logged, monkeypatch
):
    """Which key a run billed is the first thing each session of it says, before
    any word about resuming - by the variable's name, never its value."""
    from ai_taxman.core.runner import run_audit_async

    monkeypatch.setenv("PROBE_KEY", "sk-do-not-log-me")
    fake_provider.requires_api_key = True
    fake_provider.failures = {TWO: [RuntimeError("nope")]}
    await run_audit_async(audit("api_key_env: PROBE_KEY"))
    await run_audit_async(audit("api_key_env: PROBE_KEY"))  # resumes, to retry TWO

    lines = read_log(logged).splitlines()
    opened = [index for index, line in enumerate(lines) if "api_key_env=PROBE_KEY" in line]
    resumed = next(index for index, line in enumerate(lines) if "resuming" in line)
    assert opened[0] == 0
    assert len(opened) == 2
    assert opened[1] < resumed


async def test_message_text_is_not_logged(audit, fake_provider, logged):
    """Ids, not bodies: the text is already in the JSONL, and a log of it is unusable."""
    from ai_taxman.core.runner import run_audit_async

    await run_audit_async(audit())

    assert "two" not in read_log(logged).split("messages")[-1]
