"""Logging: what a long run says about itself while it runs.

A collection can run for hours. Without a running commentary the only feedback
is a summary printed at the end, which is no use to someone watching a run they
started this morning - or to a background run, which has no terminal at all.

Two rules matter more than the format. The library never configures logging for
its caller, and the API key never reaches a log record.
"""

import logging
import textwrap

import pytest

from ai_taxman.core.config import load_audit
from ai_taxman.core.logging import LOGGER_NAME, describe_level, setup_logging


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


@pytest.fixture
def project(tmp_path):
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "audits").mkdir()
    return tmp_path


def audit(project, *blocks):
    body = "audit: probe\nprovider: fake\nmessages: messages/probe.txt\nmodel:\n  name: fake-1\n"
    path = project / "audits" / "probe.yaml"
    path.write_text(
        textwrap.dedent(body + "".join(block.rstrip() + "\n" for block in blocks)).lstrip(),
        encoding="utf-8",
    )
    return load_audit(path)


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


def test_the_log_file_is_created_with_its_parent_directory(tmp_path):
    target = tmp_path / "nested" / "deeper" / "run.log"

    setup_logging(log_file=target)
    logging.getLogger(LOGGER_NAME).info("hello")

    assert target.is_file()


def test_lines_are_written_as_they_happen(tmp_path):
    """A log nobody can tail until the process exits is not a log."""
    target = tmp_path / "run.log"
    setup_logging(log_file=target)

    logging.getLogger(LOGGER_NAME).info("first")

    assert "first" in target.read_text(encoding="utf-8")


def test_the_level_filters_quieter_records(capsys):
    setup_logging(level="warning")

    logging.getLogger(LOGGER_NAME).info("chatter")
    logging.getLogger(LOGGER_NAME).warning("trouble")

    captured = capsys.readouterr()
    assert "chatter" not in captured.err
    assert "trouble" in captured.err


def test_an_unknown_level_is_rejected_by_name():
    with pytest.raises(ValueError, match="loud"):
        setup_logging(level="loud")


def test_configuring_twice_does_not_double_every_line(capsys):
    setup_logging()
    setup_logging()

    logging.getLogger(LOGGER_NAME).info("once")

    assert capsys.readouterr().err.count("once") == 1


def test_records_carry_the_time_the_level_and_the_source(capsys):
    setup_logging()

    logging.getLogger(f"{LOGGER_NAME}.runner").info("hello")

    line = capsys.readouterr().err
    assert "INFO" in line
    assert f"{LOGGER_NAME}.runner" in line
    assert line.startswith("20")  # an ISO timestamp, matching the record schema


def test_taxman_logs_do_not_leak_into_the_root_logger(capsys):
    """A caller's own logging configuration is theirs, not ours to inherit."""
    setup_logging()

    assert logging.getLogger(LOGGER_NAME).propagate is False


def test_the_level_names_are_the_ones_the_cli_offers():
    assert describe_level("info") == logging.INFO
    assert describe_level("DEBUG") == logging.DEBUG


# --- what a run actually says --------------------------------------------


def read_log(path):
    return path.read_text(encoding="utf-8")


@pytest.fixture
def logged(tmp_path, restore_logger):
    """Capture a real run's log into a file, the way --log-file does."""
    target = tmp_path / "run.log"
    setup_logging(level="debug", log_file=target)
    return target


async def test_a_run_announces_what_it_is_about_to_do(project, fake_provider, logged):
    """The first lines answer 'what is this process doing?' without the audit file."""
    from ai_taxman.core.runner import run_audit_async

    result = await run_audit_async(audit(project, "execution:\n  repeats: 2"))

    log = read_log(logged)
    assert result.run_id in log
    assert "probe" in log
    assert "fake" in log
    assert "6" in log  # 3 messages x 2 repeats, the number of responses to expect


async def test_every_response_is_logged_as_it_lands(project, fake_provider, logged):
    from ai_taxman.core.runner import run_audit_async

    await run_audit_async(audit(project))

    log = read_log(logged)
    for message_id in ("m0000", "m0001", "m0002"):
        assert message_id in log


async def test_a_retry_is_logged_as_a_warning(project, fake_provider, logged):
    from ai_taxman.core.runner import run_audit_async

    fake_provider.retryable = (TimeoutError,)
    fake_provider.failures = {"m0001": [TimeoutError("slow")]}

    await run_audit_async(audit(project, "execution:\n  max_retries: 2"))

    log = read_log(logged)
    assert "WARNING" in log
    assert "TimeoutError" in log


async def test_a_failed_message_is_logged_as_an_error(project, fake_provider, logged):
    from ai_taxman.core.runner import run_audit_async

    fake_provider.failures = {"m0001": [RuntimeError("nope")]}

    await run_audit_async(audit(project))

    log = read_log(logged)
    assert "ERROR" in log
    assert "m0001" in log


async def test_the_end_of_the_run_is_logged_with_the_counts(project, fake_provider, logged):
    from ai_taxman.core.runner import run_audit_async

    await run_audit_async(audit(project))

    log = read_log(logged)
    assert "complete" in log
    assert "responses.jsonl" in log


async def test_the_api_key_never_reaches_the_log(project, fake_provider, logged, monkeypatch):
    """The one thing that must never be written down, at any level."""
    from ai_taxman.core.runner import run_audit_async

    monkeypatch.setenv("PROBE_KEY", "sk-do-not-log-me")
    fake_provider.requires_api_key = True

    await run_audit_async(audit(project, "api_key_env: PROBE_KEY"))

    assert "sk-do-not-log-me" not in read_log(logged)
    assert fake_provider.api_key == "sk-do-not-log-me"


async def test_message_text_is_not_logged(project, fake_provider, logged):
    """Ids, not bodies: the text is already in the JSONL, and a log of it is unusable."""
    from ai_taxman.core.runner import run_audit_async

    await run_audit_async(audit(project))

    assert "two" not in read_log(logged).split("messages")[-1]
