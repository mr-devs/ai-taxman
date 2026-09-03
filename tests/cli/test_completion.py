"""Tab completion has to be fast and must never import a provider SDK."""

import sys

from ai_taxman.cli.completion import complete_audit, complete_model, complete_provider


def test_completes_provider_names():
    assert "openai" in complete_provider("")


def test_filters_providers_by_prefix():
    assert complete_provider("open") == ["openai"]
    assert complete_provider("zzz") == []


def test_completes_audit_names(invoke, tmp_path):
    (tmp_path / "audits").mkdir()
    for name in ("election", "refusals"):
        (tmp_path / "audits" / f"{name}.yaml").write_text(f"audit: {name}\n", encoding="utf-8")

    assert complete_audit("") == ["election", "refusals"]


def test_filters_audits_by_prefix(invoke, tmp_path):
    (tmp_path / "audits").mkdir()
    (tmp_path / "audits" / "election.yaml").write_text("audit: election\n", encoding="utf-8")

    assert complete_audit("ele") == ["election"]
    assert complete_audit("zzz") == []


def test_completing_audits_never_raises_without_an_audits_directory(invoke):
    assert complete_audit("") == []


def test_completes_models_for_the_provider_on_the_command_line():
    class Ctx:
        params = {"provider": "openai"}

    assert any(name.startswith("gpt-5") for name in complete_model(Ctx(), ""))


def test_completing_models_for_an_unknown_provider_is_empty():
    class Ctx:
        params = {"provider": "openia"}

    assert complete_model(Ctx(), "") == []


def test_completing_providers_does_not_import_their_sdks():
    sys.modules.pop("ai_taxman.providers.openai", None)

    complete_provider("")

    assert "ai_taxman.providers.openai" not in sys.modules
