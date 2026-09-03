"""Guards for `docs/provider-apis/`.

The offline test keeps the index honest: every provider we target has a file,
and each file names the machine-readable index Claude should fetch when a page
is not already listed.

The live test is the one that catches link rot. It is marked `live` because it
needs the network, so `uv run pytest` stays offline.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "provider-apis"

#: Every provider file, and the index URL it must point at. See the README
#: there for why each provider's markdown suffix differs.
PROVIDER_INDEXES = {
    "openai.md": "https://developers.openai.com/api/llms.txt",
    "anthropic.md": "https://platform.claude.com/llms.txt",
    "gemini.md": "https://ai.google.dev/gemini-api/docs/llms.txt",
    "xai.md": "https://docs.x.ai/llms.txt",
    "perplexity.md": "https://docs.perplexity.ai/llms.txt",
}

#: Markdown link targets, e.g. the URL inside `[Create a response](https://...)`.
LINK = re.compile(r"\((https://[^)\s]+)\)")

#: A docs host that answers 200 with a rendered page instead of markdown.
#: Two providers do this, which is the whole reason the live test checks the
#: body and not just the status code.
HTML_START = re.compile(r"^\s*(<!doctype|<html)", re.IGNORECASE)


def test_every_targeted_provider_has_a_file():
    missing = [name for name in PROVIDER_INDEXES if not (DOCS / name).is_file()]

    assert not missing, f"missing docs/provider-apis/: {', '.join(missing)}"


def test_the_readme_explains_the_conventions():
    """Without it the per-provider files are five lists with no rules."""
    readme = (DOCS / "README.md").read_text(encoding="utf-8")

    assert "llms.txt" in readme
    assert ".md.txt" in readme, "Gemini's suffix is the one people get wrong"


@pytest.mark.parametrize("name, index", sorted(PROVIDER_INDEXES.items()))
def test_each_file_names_its_machine_readable_index(name, index):
    """The index is the escape hatch for a page this file does not list."""
    assert index in (DOCS / name).read_text(encoding="utf-8")


def test_claude_md_points_at_the_directory():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "docs/provider-apis/" in text


def documented_urls():
    """Every link target across the directory, deduplicated."""
    urls = set()
    for path in sorted(DOCS.glob("*.md")):
        source = str(path.relative_to(ROOT))
        urls |= {(source, url) for url in LINK.findall(path.read_text(encoding="utf-8"))}
    return sorted(urls)


@pytest.mark.live
@pytest.mark.parametrize("source, url", documented_urls(), ids=lambda value: value)
def test_documented_url_still_serves_markdown(source, url):
    """A 200 is not proof - two providers serve HTML from a `.md` path."""
    request = urllib.request.Request(url, headers={"User-Agent": "ai-taxman-docs-check"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # pragma: no cover - network
        pytest.fail(f"{source}: {url} returned HTTP {exc.code}")
    except urllib.error.URLError as exc:  # pragma: no cover - network
        pytest.fail(f"{source}: {url} could not be reached ({exc.reason})")

    assert not HTML_START.match(body), f"{source}: {url} serves HTML, not markdown"
