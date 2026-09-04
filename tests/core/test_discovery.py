import pytest

from ai_taxman.core.discovery import (
    MARKER_FILENAME,
    AuditRef,
    audits_dir,
    find_audit,
    find_project_root,
    list_audits,
    require_project_root,
    write_marker,
)
from ai_taxman.core.errors import AuditNotFoundError, ConfigError, NotATaxmanProjectError


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


# --- the project root -----------------------------------------------------


@pytest.fixture
def project(tmp_path):
    """A project root, marked."""
    root = tmp_path / "project"
    root.mkdir()
    write_marker(root)
    return root


def test_the_marker_marks_the_root(project):
    assert find_project_root(project) == project


def test_the_root_is_found_from_a_subdirectory(project):
    deep = project / "messages" / "nested"
    deep.mkdir(parents=True)

    assert find_project_root(deep) == project


def test_the_nearest_marker_wins(project):
    inner = project / "sub"
    inner.mkdir()
    write_marker(inner)

    assert find_project_root(inner) == inner


def test_no_marker_anywhere_is_not_a_project(tmp_path):
    stray = tmp_path / "not-a-project"
    stray.mkdir()

    assert find_project_root(stray) is None


def test_an_audits_directory_alone_is_not_a_project(tmp_path):
    """`audits/` is a common directory name; it must never imply a taxman root."""
    stray = tmp_path / "some-repo"
    (stray / "audits").mkdir(parents=True)
    make(stray / "audits", "unrelated")

    assert find_project_root(stray) is None


def test_requiring_a_root_without_one_names_the_directory_searched(tmp_path):
    stray = tmp_path / "not-a-project"
    stray.mkdir()

    with pytest.raises(NotATaxmanProjectError) as exc:
        require_project_root(stray)

    message = str(exc.value)
    assert "not a taxman project" in message.lower()
    assert MARKER_FILENAME in message
    assert "taxman init" in message


def test_a_marker_from_a_newer_taxman_is_refused(project):
    (project / MARKER_FILENAME).write_text("taxman_project: 99\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="newer version of taxman"):
        require_project_root(project)


def test_an_unreadable_marker_still_marks_the_root(project):
    """A marker is a marker. Its contents are not worth failing a run over."""
    (project / MARKER_FILENAME).write_text("{{ not yaml\n", encoding="utf-8")

    assert require_project_root(project) == project


def test_writing_a_marker_is_idempotent(tmp_path):
    root = tmp_path / "fresh"
    root.mkdir()

    first = write_marker(root)
    first.write_text("# edited by hand\ntaxman_project: 1\n", encoding="utf-8")
    second = write_marker(root)

    assert first == second == root / MARKER_FILENAME
    assert "edited by hand" in second.read_text(encoding="utf-8")


def test_the_audits_directory_hangs_off_the_root(project):
    assert audits_dir(project) == project / "audits"
