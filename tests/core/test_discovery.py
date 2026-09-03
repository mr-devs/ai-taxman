import pytest

from ai_taxman.core.discovery import AuditRef, find_audit, list_audits
from ai_taxman.core.errors import AuditNotFoundError


@pytest.fixture
def roots(tmp_path):
    """A local ./audits dir and a global ~/.taxman/audits dir."""
    local = tmp_path / "project" / "audits"
    glob = tmp_path / "home" / ".taxman" / "audits"
    local.mkdir(parents=True)
    glob.mkdir(parents=True)
    return local, glob


def make(directory, name, suffix=".yaml"):
    path = directory / f"{name}{suffix}"
    path.write_text(f"audit: {name}\n", encoding="utf-8")
    return path


def test_finds_an_audit_in_the_local_directory(roots):
    local, glob = roots
    expected = make(local, "election")

    assert find_audit("election", local_dir=local, global_dir=glob) == expected


def test_finds_an_audit_in_the_global_directory(roots):
    local, glob = roots
    expected = make(glob, "shared")

    assert find_audit("shared", local_dir=local, global_dir=glob) == expected


def test_local_wins_over_global(roots):
    local, glob = roots
    expected = make(local, "both")
    make(glob, "both")

    assert find_audit("both", local_dir=local, global_dir=glob) == expected


def test_accepts_the_yml_suffix(roots):
    local, glob = roots
    expected = make(local, "terse", suffix=".yml")

    assert find_audit("terse", local_dir=local, global_dir=glob) == expected


def test_yaml_wins_over_yml_in_the_same_directory(roots):
    local, glob = roots
    expected = make(local, "dupe", suffix=".yaml")
    make(local, "dupe", suffix=".yml")

    assert find_audit("dupe", local_dir=local, global_dir=glob) == expected


def test_accepts_a_path_to_a_yaml_file_directly(roots, tmp_path):
    local, glob = roots
    elsewhere = tmp_path / "somewhere" / "one-off.yaml"
    elsewhere.parent.mkdir()
    elsewhere.write_text("audit: one-off\n", encoding="utf-8")

    assert find_audit(str(elsewhere), local_dir=local, global_dir=glob) == elsewhere


def test_unknown_audit_lists_what_is_available(roots):
    local, glob = roots
    make(local, "election")
    make(glob, "refusals")

    with pytest.raises(AuditNotFoundError) as exc:
        find_audit("typo", local_dir=local, global_dir=glob)

    message = str(exc.value)
    assert "typo" in message
    assert "election" in message
    assert "refusals" in message


def test_unknown_audit_with_none_available_suggests_init(roots):
    local, glob = roots

    with pytest.raises(AuditNotFoundError, match="taxman init"):
        find_audit("typo", local_dir=local, global_dir=glob)


def test_lists_audits_from_both_directories_sorted_by_name(roots):
    local, glob = roots
    make(local, "beta")
    make(glob, "alpha")

    refs = list_audits(local_dir=local, global_dir=glob)

    assert [r.name for r in refs] == ["alpha", "beta"]
    assert all(isinstance(r, AuditRef) for r in refs)


def test_listing_records_where_each_audit_came_from(roots):
    local, glob = roots
    make(local, "here")
    make(glob, "there")

    sources = {r.name: r.source for r in list_audits(local_dir=local, global_dir=glob)}

    assert sources == {"here": "local", "there": "global"}


def test_listing_deduplicates_shadowed_audits_keeping_the_local_one(roots):
    local, glob = roots
    make(local, "both")
    make(glob, "both")

    refs = list_audits(local_dir=local, global_dir=glob)

    assert [(r.name, r.source) for r in refs] == [("both", "local")]


def test_listing_ignores_non_yaml_files(roots):
    local, glob = roots
    make(local, "real")
    (local / "notes.txt").write_text("ignore me", encoding="utf-8")

    assert [r.name for r in list_audits(local_dir=local, global_dir=glob)] == ["real"]


def test_listing_tolerates_missing_directories(tmp_path):
    refs = list_audits(local_dir=tmp_path / "nope", global_dir=tmp_path / "also-nope")

    assert refs == []
