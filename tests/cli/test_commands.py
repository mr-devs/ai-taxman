"""The commands the CLI offers.

There is no interactive configuration command. `taxman audits new` writes a fully
commented audit file, and the user edits it - that is the only place any setting
is chosen, the `api_key_env:` variable name included. So the list is closed: a
command added under any name, `setup` or another, has to be added here as well.
"""

from typer.main import get_command

from ai_taxman.cli import app


def test_the_cli_offers_only_these_commands():
    commands = get_command(app).commands

    assert set(commands) == {"audits", "collect", "doctor", "init", "messages", "providers"}
