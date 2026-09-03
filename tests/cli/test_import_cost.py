"""The CLI must stay cheap to import.

Shell completion runs `taxman` on every Tab press, so the cost of importing
`ai_taxman.cli` is paid each time the user asks for a completion. Anything only
a running command needs - the config parser, the async runner, the prompt
library - must be imported when that command runs, not when the module loads.

These run in a subprocess because the test session has already imported
everything.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

#: Heavy, and needed by a command that is doing work - never by a completion.
DEFERRED = [
    "questionary",
    "pydantic",
    "asyncio",
    "ai_taxman.core.config",
    "ai_taxman.core.runner",
]


def modules_after(statement: str) -> set[str]:
    """The interesting modules present in a fresh interpreter after `statement`."""
    code = f"import sys; {statement}; print('\\n'.join(sorted(sys.modules)))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return set(result.stdout.split())


@pytest.mark.parametrize("module", DEFERRED)
def test_importing_the_cli_does_not_import(module):
    assert module not in modules_after("import ai_taxman.cli")


@pytest.mark.parametrize("module", DEFERRED)
def test_importing_the_package_does_not_import(module):
    """`from ai_taxman import __version__` must not drag the whole API in."""
    assert module not in modules_after("import ai_taxman")


def test_asking_without_a_terminal_does_not_import_the_prompt_library():
    """`ask_yes_no` returns False with no tty; it must not pay for prompt_toolkit."""
    code = (
        "import sys;"
        "from ai_taxman.cli.doctor_cmd import ask_yes_no;"
        "answer = ask_yes_no('?');"
        "print(answer, 'questionary' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "False"]
