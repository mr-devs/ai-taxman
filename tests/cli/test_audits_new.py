"""`taxman audits new <provider> <audit>` - two names in, one file out.

The command deliberately takes nothing else. Every setting is written at its
default, with a comment saying what it does, and the user edits the file. That
keeps the provider-specific surface entirely inside the generated YAML rather
than spread across command-line parsing.

The folders it writes into the audit come from the project's marker.
"""

import yaml

from ai_taxman.core.config import load_audit


def read(tmp_path, name):
    return yaml.safe_load((tmp_path / "audits" / f"{name}.yaml").read_text(encoding="utf-8"))


def test_creates_the_named_audit_in_the_audits_directory(invoke, tmp_path):
    result = invoke("audits", "new", "openai", "election-probe")

    assert result.exit_code == 0
    assert (tmp_path / "audits" / "election-probe.yaml").is_file()


def test_the_audit_is_named_by_the_second_argument(invoke, tmp_path):
    invoke("audits", "new", "openai", "election-probe")

    assert read(tmp_path, "election-probe")["audit"] == "election-probe"


def test_the_audit_names_its_provider(invoke, tmp_path):
    invoke("audits", "new", "openai", "probe")

    assert read(tmp_path, "probe")["provider"] == "openai"


def test_the_name_is_required(invoke):
    result = invoke("audits", "new", "openai")

    assert result.exit_code != 0


def test_the_model_defaults_to_the_providers_own(invoke, tmp_path):
    invoke("audits", "new", "openai", "probe")

    assert read(tmp_path, "probe")["model"]["name"] == "gpt-5"


def test_the_api_key_variable_is_left_for_the_user_to_fill_in(invoke, tmp_path):
    """taxman never guesses which variable holds the key; the user names it."""
    invoke("audits", "new", "openai", "probe")

    assert read(tmp_path, "probe")["api_key_env"] == "<insert_api_key_env_var_here>"


def test_the_comment_hints_at_the_providers_conventional_variable(invoke, tmp_path):
    text = (
        (tmp_path / "audits" / "probe.yaml").read_text(encoding="utf-8")
        if invoke("audits", "new", "openai", "probe")
        else ""
    )

    assert "OPENAI_API_KEY" in text
    hint = next(line for line in text.splitlines() if "OPENAI_API_KEY" in line)
    assert hint.lstrip().startswith("#"), "the hint belongs in a comment, not the value"


def test_the_scaffolded_file_still_parses_as_yaml(invoke, tmp_path):
    invoke("audits", "new", "openai", "probe")

    assert isinstance(read(tmp_path, "probe"), dict)


def test_settings_are_written_at_their_defaults(invoke, tmp_path):
    """Nothing is pre-filled from the command line; the file carries the defaults."""
    audit = read(tmp_path, "probe") if invoke("audits", "new", "openai", "probe") else None

    assert audit["execution"]["repeats"] == 1
    assert audit["model"]["temperature"] is None


def test_it_writes_only_to_the_working_directory(invoke, tmp_path):
    invoke("audits", "new", "openai", "probe")

    assert (tmp_path / "audits" / "probe.yaml").is_file()
    assert not (tmp_path / "taxman-home" / "audits").exists()


def test_points_at_a_messages_file_named_after_the_audit(invoke, tmp_path):
    invoke("audits", "new", "openai", "election-probe")

    assert read(tmp_path, "election-probe")["messages"] == "messages/election-probe.txt"


def test_the_generated_file_loads_as_a_valid_audit(invoke, tmp_path):
    invoke("audits", "new", "openai", "probe")
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\n", encoding="utf-8")

    config = load_audit(tmp_path / "audits" / "probe.yaml")

    assert config.provider == "openai"


def test_the_generated_model_block_validates_against_the_provider(invoke, tmp_path):
    from ai_taxman.core.registry import get_provider

    invoke("audits", "new", "openai", "probe")
    block = read(tmp_path, "probe")["model"]

    get_provider("openai").validate_model_config(block)


def next_steps(output):
    return output[output.index("Next:") :].rstrip("\n").splitlines()


def test_tells_the_user_what_to_do_next(invoke):
    result = invoke("audits", "new", "openai", "election-probe")

    assert next_steps(result.output) == [
        "Next:",
        "  1. Add messages in messages/election-probe.txt",
        "  2. (Optional) Add a system prompt in prompts/",
        "  3. Finalize details in audits/election-probe.yaml",
        "  4. Collect data by running `taxman collect election-probe`",
    ]


def test_paths_are_relative_to_the_project_wherever_the_user_stands(invoke, tmp_path, monkeypatch):
    """Run from inside the audits folder, every path still reads from the root."""
    (tmp_path / "audits").mkdir()
    monkeypatch.chdir(tmp_path / "audits")

    result = invoke("audits", "new", "openai", "probe")

    assert result.output.splitlines()[0] == "Created audits/probe.yaml"
    assert next_steps(result.output)[1:4] == [
        "  1. Add messages in messages/probe.txt",
        "  2. (Optional) Add a system prompt in prompts/",
        "  3. Finalize details in audits/probe.yaml",
    ]
    assert str(tmp_path) not in result.output


def test_next_steps_name_the_projects_own_folders(invoke, tmp_path):
    custom_project(tmp_path)

    result = invoke("audits", "new", "openai", "probe")

    assert next_steps(result.output)[1:4] == [
        "  1. Add messages in study/msgs/probe.txt",
        "  2. (Optional) Add a system prompt in taxman/prompts/",
        "  3. Finalize details in study/audits/probe.yaml",
    ]


def test_refuses_to_overwrite_an_existing_audit(invoke, tmp_path):
    invoke("audits", "new", "openai", "probe")
    (tmp_path / "audits" / "probe.yaml").write_text("edited by hand\n", encoding="utf-8")

    result = invoke("audits", "new", "openai", "probe")

    assert result.exit_code != 0
    assert (tmp_path / "audits" / "probe.yaml").read_text(encoding="utf-8") == "edited by hand\n"


def test_the_refusal_says_what_to_do(invoke, tmp_path):
    invoke("audits", "new", "openai", "probe")

    result = invoke("audits", "new", "openai", "probe")

    assert "already exists" in result.output
    # The flags that used to be suggested are gone; do not advertise them.
    assert "--force" not in result.output
    assert "--name" not in result.output


def test_unknown_provider_is_reported_with_the_available_ones(invoke):
    result = invoke("audits", "new", "nope", "probe")

    assert result.exit_code != 0
    assert "openai" in result.output


def test_it_takes_no_further_arguments(invoke):
    result = invoke("audits", "new", "openai", "probe", "temperature=1.5")

    assert result.exit_code != 0


def test_the_generated_file_carries_explanatory_comments(invoke, tmp_path):
    invoke("audits", "new", "openai", "probe")
    text = (tmp_path / "audits" / "probe.yaml").read_text(encoding="utf-8")

    assert "# Times to send EACH message" in text
    assert "taxman collect probe" in text


# --- the project's folders --------------------------------------------------


def custom_project(tmp_path):
    from ai_taxman.core.discovery import MARKER_FILENAME, Layout, write_marker

    (tmp_path / MARKER_FILENAME).unlink()
    write_marker(tmp_path, Layout(audits="study/audits", messages="study/msgs", data="out"))


def test_the_audit_lands_in_the_projects_audits_folder(invoke, tmp_path):
    custom_project(tmp_path)

    result = invoke("audits", "new", "openai", "probe")

    assert result.exit_code == 0
    assert (tmp_path / "study" / "audits" / "probe.yaml").is_file()


def test_the_messages_file_is_in_the_projects_messages_folder(invoke, tmp_path):
    custom_project(tmp_path)
    invoke("audits", "new", "openai", "probe")

    audit = yaml.safe_load((tmp_path / "study" / "audits" / "probe.yaml").read_text("utf-8"))

    assert audit["messages"] == "study/msgs/probe.txt"


def test_output_goes_to_the_projects_data_folder(invoke, tmp_path):
    custom_project(tmp_path)
    invoke("audits", "new", "openai", "probe")

    audit = yaml.safe_load((tmp_path / "study" / "audits" / "probe.yaml").read_text("utf-8"))

    assert audit["output"]["dir"] == "out/{audit}/{run_id}"


def test_logs_go_to_the_projects_logs_folder(invoke, tmp_path):
    custom_project(tmp_path)
    invoke("audits", "new", "openai", "probe")

    audit = yaml.safe_load((tmp_path / "study" / "audits" / "probe.yaml").read_text("utf-8"))

    assert audit["output"]["log_dir"] == "taxman/logs/{audit}"


def test_no_system_prompt_is_set_until_the_user_names_one(invoke, tmp_path):
    invoke("audits", "new", "openai", "probe")

    assert read(tmp_path, "probe")["system_prompt"] is None


def test_the_system_prompt_comment_points_at_the_prompts_folder(invoke, tmp_path):
    custom_project(tmp_path)
    invoke("audits", "new", "openai", "probe")
    text = (tmp_path / "study" / "audits" / "probe.yaml").read_text("utf-8")

    assert any("taxman/prompts/" in line for line in comment_above(text, "system_prompt"))


def test_the_model_block_no_longer_carries_a_system_prompt(invoke, tmp_path):
    invoke("audits", "new", "openai", "probe")

    assert "system_prompt" not in read(tmp_path, "probe")["model"]


def test_a_second_audit_lands_in_the_project_from_a_subdirectory(invoke, tmp_path, monkeypatch):
    deep = tmp_path / "notes" / "deeper"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    result = invoke("audits", "new", "openai", "second")

    assert result.exit_code == 0
    assert (tmp_path / "audits" / "second.yaml").is_file()
    assert not (deep / "audits").exists()


# --- a project comes first ------------------------------------------------


def test_outside_a_project_it_says_to_run_init_first(invoke, leave_project, tmp_path):
    from ai_taxman.core.discovery import MARKER_FILENAME

    result = invoke("audits", "new", "openai", "probe")

    assert result.exit_code != 0
    assert "taxman init" in result.output
    assert not (tmp_path / MARKER_FILENAME).exists()
    assert not (tmp_path / "audits").exists()


def test_the_marker_is_left_as_the_user_wrote_it(invoke, tmp_path):
    from ai_taxman.core.discovery import MARKER_FILENAME

    marker = tmp_path / MARKER_FILENAME
    before = marker.read_text(encoding="utf-8")

    invoke("audits", "new", "openai", "probe")

    assert marker.read_text(encoding="utf-8") == before


# --- how the file reads ---------------------------------------------------


def audit_text(invoke, tmp_path, name="probe"):
    invoke("audits", "new", "openai", name)
    return (tmp_path / "audits" / f"{name}.yaml").read_text(encoding="utf-8")


def comment_above(text, key):
    """The comment lines directly above a top-level `key:`."""
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith(f"{key}:"))
    above = []
    for line in reversed(lines[:index]):
        if not line.startswith("#"):
            break
        above.insert(0, line)
    return above


def test_the_header_says_what_the_file_is_and_how_to_run_it(invoke, tmp_path):
    text = audit_text(invoke, tmp_path)

    assert text.splitlines()[:4] == [
        "# This is an `ai-taxman` audit file.",
        "#",
        "# Collect data based on this audit file by running:",
        "#   taxman collect probe",
    ]


def test_settings_are_in_the_order_a_user_fills_them_in(invoke, tmp_path):
    keys = list(yaml.safe_load(audit_text(invoke, tmp_path)))

    assert keys == [
        "audit",
        "provider",
        "api_key_env",
        "messages",
        "system_prompt",
        "output",
        "execution",
        "model",
    ]


def test_every_top_level_setting_explains_itself(invoke, tmp_path):
    text = audit_text(invoke, tmp_path)

    assert comment_above(text, "audit") == ["# Audit name"]
    assert comment_above(text, "provider") == ["# AI provider"]
    assert comment_above(text, "api_key_env") == [
        "# The name of an environment variable that holds an API key for the provider. "
        "This is required.",
        "# For openai this is usually OPENAI_API_KEY.",
    ]
    assert comment_above(text, "messages") == [
        "# Path to the file containing the messages to send. Each line is a separate message;",
        "# blank lines and lines starting with `#` are skipped.",
    ]
    assert comment_above(text, "system_prompt") == [
        "# Path, relative to the project root, to a file containing the system prompt to send",
        "# with every message.",
        "#  - e.g. prompts/neutral.txt",
        "# Leave blank to exclude a system prompt.",
    ]


def test_no_line_ends_in_whitespace(invoke, tmp_path):
    text = audit_text(invoke, tmp_path)

    assert [line for line in text.splitlines() if line != line.rstrip()] == []


def test_the_batch_comment_says_it_is_not_available_yet(invoke, tmp_path):
    line = next(line for line in audit_text(invoke, tmp_path).splitlines() if "batch:" in line)

    assert line == "  batch: false  # true to use the provider's batch API (not available yet)."
