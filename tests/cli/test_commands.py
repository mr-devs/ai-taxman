"""The commands the CLI offers.

There is no interactive configuration command. `taxman init` writes a fully
commented audit file, and the user edits it - that is the only place any setting
is chosen, the `api_key_env:` variable name included.
"""

import pytest

SURVIVING = ("init", "collect", "audits", "doctor", "providers")


@pytest.mark.parametrize("name", SURVIVING)
def test_the_workflow_commands_are_registered(invoke, name):
    assert invoke(name, "--help").exit_code == 0


@pytest.mark.parametrize("name", ("setup", "set-key"))
def test_there_is_no_interactive_configuration_command(invoke, name):
    assert invoke(name, "openai").exit_code != 0


def test_the_help_does_not_advertise_setup(invoke):
    result = invoke("--help")

    assert "setup" not in result.output
    assert "init" in result.output


def test_no_prompt_module_remains():
    """`prompts.py` existed only to render setup's questions."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("ai_taxman.cli.prompts")
