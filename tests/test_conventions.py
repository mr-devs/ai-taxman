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


# --- project scope --------------------------------------------------------

#: taxman is project-scoped top to bottom. See CLAUDE.md.
GLOBAL_AUDITS = re.compile(r"~/\.taxman/audits|taxman_home\(\)\s*/\s*[\"']audits|global_dir")


@pytest.mark.parametrize("path", project_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_user_global_audit_directory_anywhere(path):
    """There is no `~/.taxman/audits`, and there never will be."""
    offenders = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if GLOBAL_AUDITS.search(line)
    ]

    assert not offenders, (
        "Audits are project-scoped; a global one is invisible to the repo that "
        "depends on it:\n" + "\n".join(offenders)
    )


def test_the_project_scope_rule_is_written_down_in_claude_md():
    """The guard is only half the rule; the other half has to be documented."""
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "project-scoped" in text.lower()


def test_the_marker_holds_a_version_and_folders_and_nothing_else():
    """Folders only. An audit setting in the marker is the config channel we deleted."""
    import yaml

    from ai_taxman.core.discovery import (
        MARKER_PATHS_KEY,
        MARKER_VERSION_KEY,
        Layout,
        render_marker,
    )

    assert set(yaml.safe_load(render_marker(Layout()))) == {MARKER_VERSION_KEY, MARKER_PATHS_KEY}


def test_core_ignores_every_other_key_in_the_marker(tmp_path):
    """A setting smuggled into the marker must have no effect at all."""
    from ai_taxman.core.config import load_audit
    from ai_taxman.core.discovery import MARKER_FILENAME, write_marker

    write_marker(tmp_path)
    audit = tmp_path / "audits" / "probe.yaml"
    audit.parent.mkdir()
    audit.write_text(
        "audit: probe\nprovider: openai\nmessages: messages/probe.txt\n"
        "output:\n  dir: data/{audit}/{run_id}\n  log_dir: logs/{audit}\n"
        "model:\n  name: gpt-5\n",
        encoding="utf-8",
    )
    plain = load_audit(audit).model_dump(mode="json")

    (tmp_path / MARKER_FILENAME).write_text(
        "taxman_project: 2\nprovider: anthropic\noutput:\n  dir: somewhere-else\n",
        encoding="utf-8",
    )

    assert load_audit(audit).model_dump(mode="json") == plain


def test_remembered_state_is_only_ever_skip_flags():
    """`~/.taxman/state.yaml` holds facts about the machine, never about an audit."""
    from dataclasses import fields

    from ai_taxman.core.state import State

    for field in fields(State):
        assert field.name.startswith("skip_"), f"{field.name} is not a doctor skip flag"
        assert field.type in ("bool", bool), f"{field.name} is not a yes/no answer"
