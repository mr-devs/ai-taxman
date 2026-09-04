"""`taxman init <provider> <audit>` - two names in, one file out.

The command deliberately takes nothing else. Every setting is written at its
default, with a comment saying what it does, and the user edits the file. That
keeps the provider-specific surface entirely inside the generated YAML rather
than spread across command-line parsing.
"""

import yaml

from ai_taxman.core.config import load_audit


def read(tmp_path, name):
    return yaml.safe_load((tmp_path / "audits" / f"{name}.yaml").read_text(encoding="utf-8"))


def test_creates_the_named_audit_in_the_audits_directory(invoke, tmp_path):
    result = invoke("init", "openai", "election-probe")

    assert result.exit_code == 0
    assert (tmp_path / "audits" / "election-probe.yaml").is_file()


def test_the_audit_is_named_by_the_second_argument(invoke, tmp_path):
    invoke("init", "openai", "election-probe")

    assert read(tmp_path, "election-probe")["audit"] == "election-probe"


def test_the_audit_names_its_provider(invoke, tmp_path):
    invoke("init", "openai", "probe")

    assert read(tmp_path, "probe")["provider"] == "openai"


def test_the_name_is_required(invoke):
    result = invoke("init", "openai")

    assert result.exit_code != 0


def test_the_model_defaults_to_the_providers_own(invoke, tmp_path):
    invoke("init", "openai", "probe")

    assert read(tmp_path, "probe")["model"]["name"] == "gpt-5"


def test_the_api_key_variable_is_left_for_the_user_to_fill_in(invoke, tmp_path):
    """taxman never guesses which variable holds the key; the user names it."""
    invoke("init", "openai", "probe")

    assert read(tmp_path, "probe")["api_key_env"] == "<insert_api_key_env_var_here>"


def test_the_comment_hints_at_the_providers_conventional_variable(invoke, tmp_path):
    text = (
        (tmp_path / "audits" / "probe.yaml").read_text(encoding="utf-8")
        if invoke("init", "openai", "probe")
        else ""
    )

    assert "OPENAI_API_KEY" in text
    hint = next(line for line in text.splitlines() if "OPENAI_API_KEY" in line)
    assert hint.lstrip().startswith("#"), "the hint belongs in a comment, not the value"


def test_the_scaffolded_file_still_parses_as_yaml(invoke, tmp_path):
    invoke("init", "openai", "probe")

    assert isinstance(read(tmp_path, "probe"), dict)


def test_it_says_the_key_variable_must_be_exported(invoke):
    result = invoke("init", "openai", "probe")

    assert "api_key_env" in result.output


def test_settings_are_written_at_their_defaults(invoke, tmp_path):
    """Nothing is pre-filled from the command line; the file carries the defaults."""
    audit = read(tmp_path, "probe") if invoke("init", "openai", "probe") else None

    assert audit["execution"]["repeats"] == 1
    assert audit["model"]["temperature"] is None


def test_it_writes_only_to_the_working_directory(invoke, tmp_path):
    invoke("init", "openai", "probe")

    assert (tmp_path / "audits" / "probe.yaml").is_file()
    assert not (tmp_path / "taxman-home" / "audits").exists()


def test_points_at_a_messages_file_named_after_the_audit(invoke, tmp_path):
    invoke("init", "openai", "election-probe")

    assert read(tmp_path, "election-probe")["messages"] == "messages/election-probe.txt"


def test_the_generated_file_loads_as_a_valid_audit(invoke, tmp_path):
    invoke("init", "openai", "probe")
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\n", encoding="utf-8")

    config = load_audit(tmp_path / "audits" / "probe.yaml")

    assert config.provider == "openai"


def test_the_generated_model_block_validates_against_the_provider(invoke, tmp_path):
    from ai_taxman.core.registry import get_provider

    invoke("init", "openai", "probe")
    block = read(tmp_path, "probe")["model"]

    get_provider("openai").validate_model_config(block)


def test_tells_the_user_what_to_do_next(invoke):
    result = invoke("init", "openai", "election-probe")

    assert "messages/election-probe.txt" in result.output
    assert "taxman collect election-probe" in result.output


def test_refuses_to_overwrite_an_existing_audit(invoke, tmp_path):
    invoke("init", "openai", "probe")
    (tmp_path / "audits" / "probe.yaml").write_text("edited by hand\n", encoding="utf-8")

    result = invoke("init", "openai", "probe")

    assert result.exit_code != 0
    assert (tmp_path / "audits" / "probe.yaml").read_text(encoding="utf-8") == "edited by hand\n"


def test_the_refusal_says_what_to_do(invoke, tmp_path):
    invoke("init", "openai", "probe")

    result = invoke("init", "openai", "probe")

    assert "already exists" in result.output
    # The flags that used to be suggested are gone; do not advertise them.
    assert "--force" not in result.output
    assert "--name" not in result.output


def test_unknown_provider_is_reported_with_the_available_ones(invoke):
    result = invoke("init", "nope", "probe")

    assert result.exit_code != 0
    assert "openai" in result.output


def test_it_takes_no_further_arguments(invoke):
    result = invoke("init", "openai", "probe", "temperature=1.5")

    assert result.exit_code != 0


def test_the_generated_file_carries_explanatory_comments(invoke, tmp_path):
    invoke("init", "openai", "probe")
    text = (tmp_path / "audits" / "probe.yaml").read_text(encoding="utf-8")

    assert "# Times to send EACH message" in text
    assert "taxman collect probe" in text


# --- init is also what makes a project ------------------------------------


def test_init_starts_a_project_where_there_is_none(invoke, leave_project, tmp_path):
    """There is no separate "init a project" step; the first audit makes one."""
    from ai_taxman.core.discovery import MARKER_FILENAME

    result = invoke("init", "openai", "probe")

    assert result.exit_code == 0
    assert (tmp_path / MARKER_FILENAME).is_file()
    assert (tmp_path / "audits" / "probe.yaml").is_file()


def test_it_says_it_started_a_project(invoke, leave_project):
    result = invoke("init", "openai", "probe")

    assert "project" in result.output.lower()


def test_an_unknown_provider_leaves_no_half_made_project(invoke, leave_project, tmp_path):
    from ai_taxman.core.discovery import MARKER_FILENAME

    result = invoke("init", "nope", "probe")

    assert result.exit_code != 0
    assert not (tmp_path / MARKER_FILENAME).exists()
    assert not (tmp_path / "audits").exists()


def test_a_second_audit_joins_the_existing_project(invoke, tmp_path, monkeypatch):
    """Run from a subdirectory, the audit still lands in the project's audits/."""
    invoke("init", "openai", "first")
    deep = tmp_path / "messages"
    deep.mkdir(exist_ok=True)
    monkeypatch.chdir(deep)

    result = invoke("init", "openai", "second")

    assert result.exit_code == 0
    assert (tmp_path / "audits" / "second.yaml").is_file()
    assert not (deep / "audits").exists()


def test_the_marker_is_not_rewritten_over_a_users_edits(invoke, tmp_path):
    from ai_taxman.core.discovery import MARKER_FILENAME

    (tmp_path / MARKER_FILENAME).write_text("# mine\ntaxman_project: 1\n", encoding="utf-8")

    invoke("init", "openai", "probe")

    assert "# mine" in (tmp_path / MARKER_FILENAME).read_text(encoding="utf-8")
