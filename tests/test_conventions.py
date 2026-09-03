"""Project conventions that are easy to break by habit."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SEARCHED = ("src", "tests", "docs", ".github")
EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".toml", ".txt"}

#: `uv` is the only supported installer. See CLAUDE.md.
PIP = re.compile(r"\bpip\s+install\b|\buv\s+pip\b|\bpython\s+-m\s+pip\b")


#: CLAUDE.md states the rule, so it necessarily quotes what it forbids.
#: This test file does too.
EXEMPT = {"CLAUDE.md", "tests/test_conventions.py"}


def project_files():
    files = [ROOT / "README.md", ROOT / "pyproject.toml"]
    for directory in SEARCHED:
        files += [
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file() and path.suffix in EXTENSIONS
        ]
    return sorted(path for path in set(files) if str(path.relative_to(ROOT)) not in EXEMPT)


@pytest.mark.parametrize("path", project_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_pip_instructions_anywhere(path):
    """This project installs with uv, never pip - including in error messages."""
    offenders = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if PIP.search(line)
    ]

    assert not offenders, "Use uv, not pip:\n" + "\n".join(offenders)


def test_the_rule_is_written_down_in_claude_md():
    """The guard is only half the rule; the other half has to be documented."""
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "uv only" in text.lower()


# --- provider isolation ---------------------------------------------------

CORE_AND_CLI = ("src/ai_taxman/core", "src/ai_taxman/cli")

#: Keys that belong to a provider's `model:` block. Core must never read one.
PROVIDER_KEYS = ("name", "temperature", "top_p", "web_search", "reasoning_effort")

#: `providers.base` is the shared contract and is fine; `providers.<name>` is not.
IMPORTS_A_PROVIDER = re.compile(r"^\s*(?:from|import)\s+(?:ai_taxman\.)?providers\.(?!base\b)\w+")


def core_and_cli_files():
    return sorted(path for directory in CORE_AND_CLI for path in (ROOT / directory).rglob("*.py"))


@pytest.mark.parametrize("path", core_and_cli_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_core_and_cli_never_import_a_specific_provider(path):
    """Providers are reached through the registry, never by name."""
    offenders = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if IMPORTS_A_PROVIDER.match(line)
    ]

    assert not offenders, "Use core.registry, not a direct import:\n" + "\n".join(offenders)


@pytest.mark.parametrize("path", core_and_cli_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_core_and_cli_never_read_keys_out_of_the_model_block(path):
    """The `model:` block is opaque to core; ask the provider instead."""
    keys = "|".join(PROVIDER_KEYS)
    pattern = re.compile(rf"""\.model(?:_config)?\s*(?:\.get\(|\[)\s*["']({keys})["']""")
    offenders = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]

    assert not offenders, (
        "Core read a provider-owned key. Use provider.describe_model() or pass the "
        "block through untouched:\n" + "\n".join(offenders)
    )


def test_no_provider_name_is_hardcoded_in_core():
    """A provider name in core means a branch that will rot when one is added."""
    from ai_taxman.core.registry import available_providers

    offenders = []
    for path in core_and_cli_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#") or "help=" in line:
                continue  # comments and CLI help may name one as an example
            for name in available_providers():
                if f'"{name}"' in line or f"'{name}'" in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")

    assert not offenders, "Provider named in core:\n" + "\n".join(offenders)
