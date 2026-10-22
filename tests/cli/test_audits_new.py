"""`taxman audits new <provider> <audit>` - two names in, one file out.

The command deliberately takes nothing else. Every setting is written at its
default, with a comment saying what it does, and the user edits the file. That
keeps the provider-specific surface entirely inside the generated YAML rather
than spread across command-line parsing.

The folders it writes into the audit come from the project's marker.
"""

import pytest
import yaml

from ai_taxman.core.config import load_audit


def read(tmp_path, name):
    return yaml.safe_load((tmp_path / "audits" / f"{name}.yaml").read_text(encoding="utf-8"))


def test_writes_the_audit_from_the_projects_folders(invoke, tmp_path):
    """Named by its two arguments, with every path read from the project's marker."""
    custom_project(tmp_path)

    result = invoke("audits", "new", "openai", "election-probe")

    assert result.exit_code == 0
    text = (tmp_path / "study" / "audits" / "election-probe.yaml").read_text(encoding="utf-8")
    audit = yaml.safe_load(text)
    assert audit["audit"] == "election-probe"
    assert audit["provider"] == "openai"
    # taxman never guesses which variable holds the key; the user names it.
    assert audit["api_key_env"] == "<insert_api_key_env_var_here>"
    assert audit["messages"] == "study/msgs/election-probe.txt"
    assert audit["system_prompt"] is None
    assert "#   Example:  taxman/prompts/neutral.txt" in comment_above(text, "system_prompt")
    assert audit["output"]["dir"] == "out/{audit}/{run_id}"
    assert audit["output"]["log_dir"] == "taxman/logs/{audit}"
    assert not (tmp_path / "taxman-home").exists()


def test_the_file_loads_and_validates_as_written(invoke, tmp_path):
    """`audits new` output must load before the user edits a thing, model block included."""
    from ai_taxman.core.registry import get_provider

    invoke("audits", "new", "openai", "probe")
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "probe.txt").write_text("one\n", encoding="utf-8")

    config = load_audit(tmp_path / "audits" / "probe.yaml")

    assert config.provider == "openai"
    get_provider("openai").validate_model_config(read(tmp_path, "probe")["model"])


def test_the_name_is_required(invoke):
    result = invoke("audits", "new", "openai")

    assert result.exit_code != 0


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
    # Neither flag exists; do not advertise them.
    assert "--force" not in result.output
    assert "--name" not in result.output


def test_unknown_provider_is_reported_with_the_available_ones(invoke):
    result = invoke("audits", "new", "nope", "probe")

    assert result.exit_code != 0
    assert "openai" in result.output


def test_it_takes_no_further_arguments(invoke):
    result = invoke("audits", "new", "openai", "probe", "temperature=1.5")

    assert result.exit_code != 0


# --- the project's folders --------------------------------------------------


def custom_project(tmp_path):
    from ai_taxman.core.discovery import MARKER_FILENAME, Layout, write_marker

    (tmp_path / MARKER_FILENAME).unlink()
    write_marker(tmp_path, Layout(audits="study/audits", messages="study/msgs", data="out"))


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


def test_the_audit_name_says_it_must_match_the_file(invoke, tmp_path):
    above = " ".join(nested_comment_above(audit_text(invoke, tmp_path), "audit"))

    assert "must match this file's name" in above


def test_the_api_key_variable_is_required_and_hints_at_the_usual_name(invoke, tmp_path):
    above = nested_comment_above(audit_text(invoke, tmp_path), "api_key_env")

    assert "Required: yes" in above
    assert "Example:  OPENAI_API_KEY" in above


def explanation(text, key):
    """The sentences above `key:`, joined: `# ...` lines, not the `#   Label:` ones."""
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.lstrip().startswith(f"{key}:"))
    sentences = []
    for line in reversed(lines[:index]):
        if not line.lstrip().startswith("#"):
            break
        if not line.lstrip().startswith("#  "):
            sentences.insert(0, line.lstrip().removeprefix("# "))
    return " ".join(sentences)


#: The wording a user settled on by editing a generated file by hand.
CONCISE = {
    "audit": "This audit's name, which must match this file's name.",
    "provider": "The AI provider this audit sends messages to.",
    "api_key_env": "The name of the environment variable that holds the API key to use for "
    "this audit.",
    "messages": "A filepath, relative to the project root, of messages to send.",
    "system_prompt": "A filepath, relative to the project root, of a system prompt to send "
    "with every message.",
    "output": "Where output files are written.",
    "dir": "Where responses and manifest are written to, relative to the project root. "
    "{audit} and {run_id} are filled in at run time.",
    "filename": "Filename for the responses file in the `output` directory.",
    "log_dir": "Path to directory where the log file is written, saved as <run_id>.log, and "
    "relative to the project root. {audit} and {run_id} are filled in at run time.",
    "execution": "How the messages are sent.",
    "repeats": "How many times each message is sent.",
    "max_concurrency": "Max number of simultaneous requests.",
}


@pytest.mark.parametrize("key", CONCISE)
def test_each_core_setting_is_explained_in_one_or_two_short_sentences(invoke, tmp_path, key):
    assert explanation(audit_text(invoke, tmp_path), key) == CONCISE[key]


def unexplained_settings(text):
    """Each setting in `text` that is not explained in full by the comment above it.

    In full means the comment starts by saying what the setting does and, unless
    the setting is a block of further settings, says what it is when left alone.
    """
    lines = text.splitlines()
    missing = []
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        above = []
        for previous in reversed(lines[:index]):
            if not previous.lstrip().startswith("#"):
                break
            above.insert(0, previous.lstrip())
        indent = len(line) - len(line.lstrip())
        following = next((later for later in lines[index + 1 :] if later.strip()), "")
        is_block = following.startswith(" " * (indent + 2))
        says_default = any(text.startswith(("#   Default:", "#   Required:")) for text in above)
        if not above or above[0].startswith("#   ") or not (is_block or says_default):
            missing.append(line.strip())
    return missing


def test_every_setting_in_the_file_explains_itself(invoke, tmp_path):
    """What it does, then what it is when left alone, above every key in the file."""
    assert unexplained_settings(audit_text(invoke, tmp_path)) == []


def test_no_line_ends_in_whitespace(invoke, tmp_path):
    text = audit_text(invoke, tmp_path)

    assert [line for line in text.splitlines() if line != line.rstrip()] == []


def nested_comment_above(text, key):
    """The comment lines directly above `key:`, at any depth, without their `#`."""
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.lstrip().startswith(f"{key}:"))
    above = []
    for line in reversed(lines[:index]):
        if not line.lstrip().startswith("#"):
            break
        above.insert(0, line.strip().removeprefix("#").strip())
    return above


def test_the_batch_comment_says_it_is_not_available_yet(invoke, tmp_path):
    above = nested_comment_above(audit_text(invoke, tmp_path), "batch")

    assert "Not available yet" in " ".join(above)


CORE_SETTINGS = (
    *("dir", "filename", "compress", "log_dir"),
    *("repeats", "max_concurrency", "batch", "timeout_s", "max_retries", "on_error", "shuffle"),
)


def test_execution_and_output_are_written_at_their_defaults(invoke, tmp_path):
    invoke("audits", "new", "openai", "probe")
    audit = read(tmp_path, "probe")

    assert audit["execution"] == {
        "repeats": 1,
        "max_concurrency": 8,
        "batch": False,
        "timeout_s": 120,
        "max_retries": 5,
        "on_error": "continue",
        "shuffle": False,
    }
    assert (audit["output"]["filename"], audit["output"]["compress"]) == (
        "responses.jsonl",
        False,
    )


# --- what an audit may be called -------------------------------------------


def test_a_name_that_cannot_be_an_audit_is_refused(invoke, tmp_path):
    """Before anything is written; each rule is tested where it lives, in discovery."""
    result = invoke("audits", "new", "openai", "sub/bar")

    assert result.exit_code != 0
    assert "letters, digits" in result.output
    assert not list(tmp_path.glob("audits/**/*.yaml"))


def test_a_name_yaml_would_misread_still_loads(invoke, tmp_path):
    """`audit: yes` is a boolean to YAML; the file must say the name as a string."""
    name = "yes"
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / f"{name}.txt").write_text("one\n", encoding="utf-8")

    invoke("audits", "new", "openai", name)
    result = invoke("audits", "show", name)

    assert result.exit_code == 0, result.output
    assert read(tmp_path, name)["audit"] == name


def test_an_existing_yml_audit_is_not_shadowed(invoke, tmp_path):
    """`foo.yaml` beside `foo.yml` would quietly replace it when collecting `foo`."""
    (tmp_path / "audits").mkdir()
    existing = tmp_path / "audits" / "probe.yml"
    existing.write_text("audit: probe\n", encoding="utf-8")

    result = invoke("audits", "new", "openai", "probe")

    assert result.exit_code != 0
    assert "probe.yml already exists" in result.output
    assert not (tmp_path / "audits" / "probe.yaml").exists()
