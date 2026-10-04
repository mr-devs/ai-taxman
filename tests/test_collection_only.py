"""taxman collects; it never cleans.

`taxman collect` writes the provider's response verbatim and nothing else. No
provider pulls `text` or `usage` out of a response into convenience fields on
the record, and these tests keep it that way: parsing a response is a separate
tool's job, working from `raw` on disk.

Serializing the SDK's object to JSON (`model_dump(mode="json")`) is not
extraction - it is what makes the response writable at all.
"""

import inspect
from pathlib import Path

import pytest

from ai_taxman.core.records import ResponseRecord
from ai_taxman.providers import base

ROOT = Path(__file__).resolve().parent.parent
PROVIDER_DIRS = sorted((ROOT / "src" / "ai_taxman" / "providers").iterdir())


def test_the_provider_contract_has_no_extract_method():
    assert not hasattr(base.Provider, "extract")


def test_there_is_no_extracted_type_to_return():
    assert not hasattr(base, "Extracted")


@pytest.mark.parametrize("field", ["text", "usage"])
def test_the_record_carries_no_extracted_convenience_field(field):
    """`raw` is the whole response. Anything derived from it lives downstream."""
    assert field not in ResponseRecord.model_fields


def test_the_record_still_carries_the_raw_response():
    assert "raw" in ResponseRecord.model_fields


@pytest.mark.parametrize(
    "path",
    [path for path in PROVIDER_DIRS if path.is_dir() and path.name != "__pycache__"],
    ids=lambda p: p.name,
)
def test_no_provider_defines_an_extract_function(path):
    """A provider that parses its own responses reintroduces the seam by hand."""
    offenders = [
        f"{source.relative_to(ROOT)}:{number}: {line.strip()}"
        for source in sorted(path.rglob("*.py"))
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1)
        if line.lstrip().startswith(("def extract", "async def extract"))
    ]

    assert not offenders, "Collection does not parse responses:\n" + "\n".join(offenders)


def test_the_runner_never_asks_a_provider_to_parse_a_response():
    from ai_taxman.core import runner

    assert "extract" not in inspect.getsource(runner)
