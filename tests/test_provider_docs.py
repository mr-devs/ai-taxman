"""Guards for `docs/provider-apis/`.

The offline test keeps the listing honest: every page a provider's template links
a setting to is one its file lists, so the link-rot check below fetches it.

The network test is the one that catches link rot. It is marked `network`, not
`live`: it needs the network but costs nothing, and `uv run pytest` stays offline.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "provider-apis"

#: Markdown link targets, e.g. the URL inside `[Create a response](https://...)`.
LINK = re.compile(r"\((https://[^)\s]+)\)")

#: A docs host that answers 200 with a rendered page instead of markdown.
#: Two providers do this, which is the whole reason the live test checks the
#: body and not just the status code.
HTML_START = re.compile(r"^\s*(<!doctype|<html)", re.IGNORECASE)


#: The markdown twin of a docs page is its URL plus one of these. See the README.
MARKDOWN_SUFFIXES = (".md", ".md.txt")

#: A `Docs:` line in a generated template.
DOCS_LINE = re.compile(r"#\s+Docs:\s+(\S+)")


def template_links(provider):
    """Every page a provider's template links its settings to."""
    return DOCS_LINE.findall(provider.render_template())


def unlisted(links, listing):
    """The links whose markdown twin `listing` does not name."""
    listed = set(LINK.findall(listing))
    return [url for url in links if not any(url + suffix in listed for suffix in MARKDOWN_SUFFIXES)]


def test_a_link_missing_from_the_listing_is_caught():
    listing = "[Create](https://example.com/create.md)"

    assert unlisted(["https://example.com/create", "https://example.com/made-up"], listing) == [
        "https://example.com/made-up"
    ]


def registered_providers():
    from ai_taxman.core.registry import available_providers, get_provider

    return [get_provider(name) for name in available_providers()]


@pytest.mark.parametrize("provider", registered_providers(), ids=lambda p: p.name)
def test_every_template_link_is_a_page_the_provider_file_lists(provider):
    """A link in an audit file is only as good as the check its listed twin gets.

    The network test below fetches every listed page, so a link that is not listed
    is one nothing checks. List the page in docs/provider-apis/ first.
    """
    links = template_links(provider)
    listing = (DOCS / f"{provider.name}.md").read_text(encoding="utf-8")

    assert links, "the template links none of its settings to the provider's docs"
    assert unlisted(links, listing) == []


def documented_urls():
    """Every link target across the directory, deduplicated."""
    urls = set()
    for path in sorted(DOCS.glob("*.md")):
        source = str(path.relative_to(ROOT))
        urls |= {(source, url) for url in LINK.findall(path.read_text(encoding="utf-8"))}
    return sorted(urls)


@pytest.mark.network
@pytest.mark.enable_socket
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
