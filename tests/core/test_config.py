import textwrap

import pytest

from ai_taxman.core.config import AuditConfig, load_audit, resolve_output_dir
from ai_taxman.core.errors import ConfigError

MINIMAL = """
audit: my-audit
provider: openai
messages: messages/probe.txt
model:
  name: gpt-5
"""


def write_audit(tmp_path, body=MINIMAL, name="my-audit", in_audits_dir=True):
    directory = tmp_path / "audits" if in_audits_dir else tmp_path
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


def test_loads_the_required_keys(tmp_path):
    config = load_audit(write_audit(tmp_path))

    assert config.audit == "my-audit"
    assert config.provider == "openai"
    assert isinstance(config, AuditConfig)


def test_model_block_is_passed_through_untouched(tmp_path):
    path = write_audit(
        tmp_path,
        """
        audit: my-audit
        provider: openai
        messages: messages/probe.txt
        model:
          name: gpt-5
          temperature: 1.5
          web_search: true
          something_only_openai_knows: 7
        """,
    )

    config = load_audit(path)

    assert config.model == {
        "name": "gpt-5",
        "temperature": 1.5,
        "web_search": True,
        "something_only_openai_knows": 7,
    }


def test_execution_defaults(tmp_path):
    execution = load_audit(write_audit(tmp_path)).execution

    assert execution.repeats == 1
    assert execution.max_concurrency == 8
    assert execution.batch is False
    assert execution.on_error == "continue"
    assert execution.shuffle is False


def test_output_defaults(tmp_path):
    output = load_audit(write_audit(tmp_path)).output

    assert output.dir == "data/{audit}/{run_id}"
    assert output.compress is False


def test_relative_message_path_resolves_against_the_project_root(tmp_path):
    """An audit in ./audits/ resolves paths against its parent, not ./audits/."""
    path = write_audit(tmp_path)

    assert load_audit(path).messages_path == tmp_path / "messages" / "probe.txt"


def test_relative_message_path_resolves_against_the_audit_dir_when_not_in_audits(tmp_path):
    path = write_audit(tmp_path, in_audits_dir=False)

    assert load_audit(path).messages_path == tmp_path / "messages" / "probe.txt"


def test_absolute_message_path_is_left_alone(tmp_path):
    absolute = tmp_path / "elsewhere" / "probe.txt"
    path = write_audit(
        tmp_path,
        f"""
        audit: my-audit
        provider: openai
        messages: {absolute}
        model:
          name: gpt-5
        """,
    )

    assert load_audit(path).messages_path == absolute


def test_remembers_where_it_was_loaded_from(tmp_path):
    path = write_audit(tmp_path)

    assert load_audit(path).source_path == path


def test_output_dir_expands_audit_and_run_id(tmp_path):
    config = load_audit(write_audit(tmp_path))

    resolved = resolve_output_dir(config, run_id="20260830T142201Z-a1b2c3")

    assert resolved == tmp_path / "data" / "my-audit" / "20260830T142201Z-a1b2c3"


def test_output_dir_may_be_absolute(tmp_path):
    target = tmp_path / "archive"
    path = write_audit(
        tmp_path,
        f"""
        audit: my-audit
        provider: openai
        messages: messages/probe.txt
        output:
          dir: {target}/{{audit}}
        model:
          name: gpt-5
        """,
    )

    assert resolve_output_dir(load_audit(path), run_id="r1") == target / "my-audit"


@pytest.mark.parametrize("missing", ["audit", "provider", "messages"])
def test_missing_required_key_is_reported_by_name(tmp_path, missing):
    body = "\n".join(
        line
        for line in textwrap.dedent(MINIMAL).lstrip().splitlines()
        if not line.startswith(f"{missing}:")
    )
    path = write_audit(tmp_path, body)

    with pytest.raises(ConfigError, match=missing):
        load_audit(path)


def test_unknown_top_level_key_is_rejected(tmp_path):
    path = write_audit(
        tmp_path,
        """
        audit: my-audit
        provider: openai
        messages: messages/probe.txt
        tempurature: 1.5
        model:
          name: gpt-5
        """,
    )

    with pytest.raises(ConfigError, match="tempurature"):
        load_audit(path)


def test_unknown_execution_key_is_rejected(tmp_path):
    path = write_audit(
        tmp_path,
        """
        audit: my-audit
        provider: openai
        messages: messages/probe.txt
        execution:
          repeets: 3
        model:
          name: gpt-5
        """,
    )

    with pytest.raises(ConfigError, match="repeets"):
        load_audit(path)


@pytest.mark.parametrize(
    "block",
    [
        "execution:\n  repeats: 0",
        "execution:\n  max_concurrency: 0",
        "execution:\n  max_retries: -1",
        "execution:\n  timeout_s: 0",
        "execution:\n  on_error: explode",
    ],
)
def test_out_of_range_execution_values_are_rejected(tmp_path, block):
    path = write_audit(
        tmp_path,
        f"""
        audit: my-audit
        provider: openai
        messages: messages/probe.txt
        {block}
        model:
          name: gpt-5
        """,
    )

    with pytest.raises(ConfigError):
        load_audit(path)


def test_error_message_names_the_offending_file(tmp_path):
    path = write_audit(tmp_path, "audit: my-audit\n")

    with pytest.raises(ConfigError) as exc:
        load_audit(path)

    assert str(path) in str(exc.value)


def test_malformed_yaml_is_reported_as_a_config_error(tmp_path):
    path = write_audit(tmp_path, "audit: [unclosed\n")

    with pytest.raises(ConfigError, match="Could not parse"):
        load_audit(path)


def test_empty_file_is_reported_as_a_config_error(tmp_path):
    path = write_audit(tmp_path, "")

    with pytest.raises(ConfigError, match="empty"):
        load_audit(path)


def test_yaml_that_is_not_a_mapping_is_rejected(tmp_path):
    path = write_audit(tmp_path, "- just\n- a list\n")

    with pytest.raises(ConfigError, match="mapping"):
        load_audit(path)


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="nope.yaml"):
        load_audit(tmp_path / "nope.yaml")


def test_audit_name_must_match_the_filename(tmp_path):
    path = write_audit(tmp_path, MINIMAL, name="different-name")

    with pytest.raises(ConfigError, match="different-name"):
        load_audit(path)


def test_model_block_defaults_to_empty(tmp_path):
    path = write_audit(
        tmp_path,
        """
        audit: my-audit
        provider: openai
        messages: messages/probe.txt
        """,
    )

    assert load_audit(path).model == {}


def test_paths_resolve_against_the_marker_when_there_is_one(tmp_path):
    """The project root is the marker's directory, however deep the audit sits."""
    from ai_taxman.core.discovery import write_marker

    write_marker(tmp_path)
    path = write_audit(tmp_path / "nested" / "deeper")

    config = load_audit(path)

    assert config.project_root == tmp_path
    assert config.messages_path == tmp_path / "messages" / "probe.txt"


def test_output_dir_resolves_against_the_marker_too(tmp_path):
    from ai_taxman.core.discovery import write_marker

    write_marker(tmp_path)
    config = load_audit(write_audit(tmp_path / "nested" / "deeper"))

    resolved = resolve_output_dir(config, run_id="r1")

    assert resolved == tmp_path / "data" / "my-audit" / "r1"
