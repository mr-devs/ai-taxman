"""Finding the project root, and the audits inside it.

taxman is project-scoped top to bottom: an audit lives in exactly one place,
`<project root>/audits/<name>.yaml`, and there is no user-global fallback. The
root is the nearest ancestor holding a `taxman.yaml` marker, so every command
works from anywhere inside a project.
"""

import pytest

from ai_taxman.core.discovery import (
    MARKER_FILENAME,
    AuditRef,
    Layout,
    audits_dir,
    find_audit,
    find_project_root,
    list_audits,
    read_layout,
    require_project_root,
    write_marker,
)
from ai_taxman.core.errors import AuditNotFoundError, ConfigError, NotATaxmanProjectError


@pytest.fixture
def project(tmp_path):
    """A project root with a marker and an empty `audits/` directory."""
    root = tmp_path / "project"
    (root / "taxman" / "audits").mkdir(parents=True)
    write_marker(root)
    return root


def make(directory, name, suffix=".yaml"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}{suffix}"
    path.write_text(f"audit: {name}\n", encoding="utf-8")
    return path


# --- finding the root -----------------------------------------------------


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
    assert audits_dir(project) == project / "taxman" / "audits"


# --- the folders a project uses -------------------------------------------


def test_a_new_marker_records_the_default_folders(project):
    assert read_layout(project) == Layout(
        data="taxman/data",
        audits="taxman/audits",
        messages="taxman/messages",
        prompts="taxman/prompts",
        logs="taxman/logs",
    )


def test_the_marker_lists_folders_in_the_order_they_are_used(project):
    """Audits, then what they send, then what a run produces."""
    import yaml

    folders = yaml.safe_load((project / MARKER_FILENAME).read_text(encoding="utf-8"))["paths"]

    assert list(folders) == ["audits", "messages", "prompts", "data", "logs"]


def test_the_marker_records_the_folders_chosen(tmp_path):
    write_marker(tmp_path, Layout(audits="studies", data="results"))

    layout = read_layout(tmp_path)

    assert layout.audits == "studies"
    assert layout.data == "results"
    assert layout.messages == "taxman/messages"


def test_audits_are_found_in_the_folder_the_marker_names(tmp_path):
    write_marker(tmp_path, Layout(audits="studies"))
    expected = make(tmp_path / "studies", "election")

    assert find_audit("election", root=tmp_path) == expected


def test_a_folder_missing_from_the_marker_takes_its_default(tmp_path):
    (tmp_path / MARKER_FILENAME).write_text(
        "taxman_project: 2\npaths:\n  audits: studies\n", encoding="utf-8"
    )

    layout = read_layout(tmp_path)

    assert layout.audits == "studies"
    assert layout.data == "taxman/data"


def test_a_marker_from_an_older_taxman_is_refused(tmp_path):
    (tmp_path / MARKER_FILENAME).write_text("taxman_project: 1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="taxman init"):
        require_project_root(tmp_path)


def test_an_unreadable_marker_cannot_name_its_folders(project):
    (project / MARKER_FILENAME).write_text("{{ not yaml\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=MARKER_FILENAME):
        read_layout(project)


def test_a_folder_the_marker_points_outside_the_project_is_refused(tmp_path):
    (tmp_path / MARKER_FILENAME).write_text(
        "taxman_project: 2\npaths:\n  data: ../elsewhere\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="elsewhere"):
        read_layout(tmp_path)


@pytest.mark.parametrize("value", ["/abs/data", "../data", "a/../../b", "", "  ", ".", "~/data"])
def test_a_folder_must_be_a_relative_path_inside_the_project(value):
    with pytest.raises(ConfigError):
        Layout(data=value)


def test_a_folder_is_written_in_its_plainest_form():
    assert Layout(data=" results//raw/ ").data == "results/raw"


def test_two_purposes_cannot_share_a_folder():
    with pytest.raises(ConfigError, match="data"):
        Layout(data="shared", logs="shared")


def test_one_folder_cannot_sit_inside_another():
    with pytest.raises(ConfigError, match="audits"):
        Layout(audits="work", data="work/data")


# --- finding one audit ----------------------------------------------------


def test_finds_an_audit_in_the_project(project):
    expected = make(audits_dir(project), "election")

    assert find_audit("election", root=project) == expected


def test_accepts_the_yml_suffix(project):
    expected = make(audits_dir(project), "terse", suffix=".yml")

    assert find_audit("terse", root=project) == expected


def test_yaml_wins_over_yml(project):
    expected = make(audits_dir(project), "dupe", suffix=".yaml")
    make(audits_dir(project), "dupe", suffix=".yml")

    assert find_audit("dupe", root=project) == expected


def test_accepts_a_path_to_a_yaml_file_inside_the_project(project):
    elsewhere = make(project / "scratch", "one-off")

    assert find_audit(str(elsewhere), root=project) == elsewhere


def test_refuses_a_path_to_a_yaml_file_outside_the_project(project, tmp_path):
    outside = make(tmp_path / "elsewhere", "stray")

    with pytest.raises(AuditNotFoundError, match="outside"):
        find_audit(str(outside), root=project)


def test_unknown_audit_lists_what_is_available(project):
    make(audits_dir(project), "election")
    make(audits_dir(project), "refusals")

    with pytest.raises(AuditNotFoundError) as exc:
        find_audit("typo", root=project)

    message = str(exc.value)
    assert "typo" in message
    assert "election" in message
    assert "refusals" in message


def test_unknown_audit_with_none_available_suggests_creating_one(project):
    with pytest.raises(AuditNotFoundError, match="taxman audits new"):
        find_audit("typo", root=project)


def test_the_not_found_message_names_the_directory_searched(project):
    with pytest.raises(AuditNotFoundError) as exc:
        find_audit("typo", root=project)

    assert str(audits_dir(project)) in str(exc.value)


def test_finding_an_audit_outside_a_project_says_so(tmp_path):
    stray = tmp_path / "not-a-project"
    stray.mkdir()

    with pytest.raises(NotATaxmanProjectError):
        find_audit("anything", start=stray)


# --- listing --------------------------------------------------------------


def test_lists_audits_sorted_by_name(project):
    make(audits_dir(project), "beta")
    make(audits_dir(project), "alpha")

    refs = list_audits(root=project)

    assert [r.name for r in refs] == ["alpha", "beta"]
    assert all(isinstance(r, AuditRef) for r in refs)


def test_listing_ignores_non_yaml_files(project):
    make(audits_dir(project), "real")
    (audits_dir(project) / "notes.txt").write_text("ignore me", encoding="utf-8")

    assert [r.name for r in list_audits(root=project)] == ["real"]


def test_listing_tolerates_a_missing_audits_directory(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    write_marker(root)

    assert list_audits(root=root) == []


def test_listing_outside_a_project_says_so(tmp_path):
    stray = tmp_path / "not-a-project"
    stray.mkdir()

    with pytest.raises(NotATaxmanProjectError):
        list_audits(start=stray)


@pytest.mark.parametrize(
    "folder", ["my audits #2", "null", "true", "010", "[logs]", "p: q", "yes/no", "a'b"]
)
def test_a_folder_reads_back_exactly_as_it_was_given(tmp_path, folder):
    """The marker must name the folder `init` created, not what YAML makes of it."""
    write_marker(tmp_path, Layout(audits=folder))

    assert read_layout(tmp_path).audits == folder


@pytest.mark.parametrize("folder", ["data/{audit}", "results}"])
def test_a_folder_with_braces_is_refused(folder):
    """Folders are written into `output.dir`, where braces mark placeholders."""
    with pytest.raises(ConfigError, match="brace"):
        Layout(data=folder)
