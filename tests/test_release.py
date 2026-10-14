"""packaging/release.py: the next version from the tags, release notes from changelog.d/ and folding them into
CHANGELOG.md."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("release", Path(__file__).parents[1] / "packaging" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

CHANGELOG = """# Changelog

## 0.6.1 (2026-10-02)

- Older.

## 0.6.0 (2026-10-01)

- Oldest.
"""
ENTRY = "- **New:** see [the docs](docs/devices.md#people) and [PyPI](https://pypi.org/) or [below](#x).\n"


def test_next_version_follows_the_highest_tag():
    tags = ["v0.6.1", "v0.10.0", "v0.9.3", "v1.0.0rc1", "nightly"]
    assert release.next_version(tags, "patch") == "0.10.1"
    assert release.next_version(tags, "minor") == "0.11.0"
    assert release.next_version(tags, "major") == "1.0.0"
    assert release.next_version([], "minor") == "0.1.0"
    with pytest.raises(ValueError):
        release.next_version(tags, "dry run")
    assert release.previous(tags, "0.10.0") == "v0.9.3"
    assert release.previous(tags, "0.10.1") == "v0.10.0"
    assert release.previous(tags, "0.6.1") is None


def test_only_markdown_files_directly_in_changelog_d_are_entries():
    assert release.is_fragment("changelog.d/menu-bar.md")
    assert not release.is_fragment("changelog.d/README.md")
    assert not release.is_fragment("changelog.d/old/x.md")
    assert not release.is_fragment("changelog.d/x.txt")
    assert not release.is_fragment("docs/changelog.d/x.md")


def test_notes_are_the_entries_with_absolute_links():
    out = release.notes([ENTRY, "\n", "- **Two:** more.\n- **Three:** lines.\n"], "0.7.0")
    assert out == ("- **New:** see [the docs](https://github.com/Chatixia-AI/interlatch/blob/v0.7.0/docs/devices.md#people)"
                   " and [PyPI](https://pypi.org/) or [below](#x).\n- **Two:** more.\n- **Three:** lines.")


def test_fold_adds_a_dated_section_newest_first():
    out = release.fold(CHANGELOG, "0.7.0", "2026-10-05", [ENTRY, "- **Two:** more.\n"])
    assert out.startswith("# Changelog\n\n## 0.7.0 (2026-10-05)\n\n- **New:** see [the docs](docs/devices.md#people)")
    assert "- **Two:** more.\n\n## 0.6.1 (2026-10-02)" in out  # links stay relative in CHANGELOG.md
    with pytest.raises(ValueError):
        release.fold(out, "0.7.0", "2026-10-06", [ENTRY])
    # 0.7.1's changelog pull request merged before 0.7.0's: 0.7.0 still goes below it
    later = release.fold(CHANGELOG, "0.7.1", "2026-10-06", ["- Later."])
    both = release.fold(later, "0.7.0", "2026-10-05", ["- Earlier."])
    assert both.index("## 0.7.1") < both.index("## 0.7.0") < both.index("## 0.6.1")
    assert release.fold("# Changelog\n", "0.1.0", "2026-10-01", ["- First."]) == (
        "# Changelog\n\n## 0.1.0 (2026-10-01)\n\n- First.\n")


def test_not_ready_without_new_entries_or_with_lines_under_unreleased():
    assert release.not_ready(CHANGELOG, [ENTRY], "v0.6.1") is None
    assert "nothing new in changelog.d/ since v0.6.1" in release.not_ready(CHANGELOG, [], "v0.6.1")
    assert "nothing new" in release.not_ready(CHANGELOG, ["\n"], None)
    old_way = CHANGELOG.replace("## 0.6.1", "## Unreleased\n\n- Written the old way.\n\n## 0.6.1")
    assert "move each into its own file in changelog.d/" in release.not_ready(old_way, [ENTRY], "v0.6.1")
    assert release.not_ready(CHANGELOG.replace("## 0.6.1", "## Unreleased\n\n## 0.6.1"), [ENTRY], "v0.6.1") is None


def test_a_change_to_what_ships_needs_a_new_changelog_d_file():
    code = ["src/interlatch/web/app.js", "tests/test_web.py"]
    assert release.missing_entry([*code, "changelog.d/dots.md"], ["changelog.d/dots.md"], CHANGELOG) is None
    why = release.missing_entry(code, [], CHANGELOG)
    assert "src/interlatch/web/app.js" in why and "tests/" not in why and "no-changelog" in why
    assert release.missing_entry(["docker/Dockerfile"], ["changelog.d/README.md"], CHANGELOG)  # the README isn't one
    assert release.missing_entry(["docker/Dockerfile", "changelog.d/old.md"], [], CHANGELOG)  # editing an old one
    assert release.missing_entry(["README.md", "docs/hub.md", ".github/workflows/ci.yml", "uv.lock"], [], CHANGELOG) is None
    many = [f"src/interlatch/m{i}.py" for i in range(5)]
    assert "and 2 more" in release.missing_entry(many, [], CHANGELOG)
    # a branch written the old way: its line under Unreleased would go out unnoticed, so it fails even with docs only
    old_way = CHANGELOG.replace("## 0.6.1", "## Unreleased\n\n- Old way.\n\n## 0.6.1")
    assert "move them into a new file in changelog.d/" in release.missing_entry(["docs/hub.md"], [], old_way)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    def run(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "T")
    (tmp_path / "changelog.d").mkdir()
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "CHANGELOG", tmp_path / "CHANGELOG.md")

    def commit(message, **files):
        for name, body in files.items():
            path = tmp_path / name.replace("__", "/")
            if body is None:
                path.unlink()
            else:
                path.write_text(body)
        run("add", "-A")
        run("commit", "-q", "-m", message)

    run.commit = commit
    return run


def main(*argv, capsys):
    code = release.main(list(argv))
    out, err = capsys.readouterr()
    return code, out.strip(), err


def test_an_entry_merged_after_the_tag_goes_in_the_next_release(repo, capsys):
    repo.commit("start", **{"CHANGELOG.md": CHANGELOG, "changelog.d__README.md": "How to.\n"})
    repo("tag", "v0.6.1")
    assert "nothing new in changelog.d/ since v0.6.1" in main("next", "minor", capsys=capsys)[2]

    repo.commit("b", **{"changelog.d__b-first.md": "- **B:** first.\n"})
    repo.commit("a", **{"changelog.d__a-second.md": "- **A:** second.\n"})
    assert main("next", "minor", capsys=capsys)[:2] == (0, "0.7.0")
    assert main("notes", "0.7.0", capsys=capsys)[1] == "- **B:** first.\n- **A:** second."  # in merge order
    repo("tag", "v0.7.0")

    # merged after the tag, before the changelog pull request: today's race, which now just waits for 0.7.1
    repo.commit("c", **{"changelog.d__c-late.md": "- **C:** late.\n"})
    code, gone, _ = main("fold", "0.7.0", "2026-10-05", capsys=capsys)
    assert code == 0 and gone.splitlines() == ["changelog.d/b-first.md", "changelog.d/a-second.md"]
    folded = (release.CHANGELOG).read_text()
    assert "## 0.7.0 (2026-10-05)\n\n- **B:** first.\n- **A:** second.\n\n## 0.6.1" in folded and "late" not in folded

    # the next release doesn't wait for that changelog pull request, and leaves out what 0.7.0 released
    repo("checkout", "-q", "--", "CHANGELOG.md")
    assert main("next", "patch", capsys=capsys)[:2] == (0, "0.7.1")
    assert main("notes", "0.7.1", capsys=capsys)[1] == "- **C:** late."

    # once the changelog pull request is merged, the same still holds
    repo.commit("Changelog: 0.7.0", **{"CHANGELOG.md": folded, "changelog.d__b-first.md": None,
                                       "changelog.d__a-second.md": None})
    assert main("notes", "0.7.1", capsys=capsys)[1] == "- **C:** late."
    repo("tag", "v0.7.1")
    assert main("notes", "0.7.1", capsys=capsys)[1] == "- **C:** late."  # read from the tag once it exists
    code, gone, _ = main("fold", "0.7.1", "2026-10-06", capsys=capsys)
    assert gone == "changelog.d/c-late.md"
    # a file someone already deleted on main is still folded in, but not deleted twice
    repo("checkout", "-q", "--", "CHANGELOG.md")
    repo.commit("drop", **{"changelog.d__c-late.md": None})
    code, gone, _ = main("fold", "0.7.1", "2026-10-06", capsys=capsys)
    assert code == 0 and gone == "" and "- **C:** late." in release.CHANGELOG.read_text()


def test_entry_check_reads_the_pull_requests_commits(repo, capsys):
    repo.commit("start", **{"CHANGELOG.md": CHANGELOG})
    repo("branch", "base")
    (release.ROOT / "src").mkdir()
    repo.commit("code", **{"src__x.py": "x = 1\n"})
    code, _, err = main("entry", "base", capsys=capsys)
    assert code == 1 and "adds no file to changelog.d/" in err
    repo.commit("entry", **{"changelog.d__x.md": "- **X:** one.\n"})
    assert main("entry", "base", capsys=capsys)[0] == 0

    # rewording an entry no release has taken counts; one a release took (v1.0.0) doesn't
    repo("tag", "v1.0.0")
    repo.commit("next", **{"changelog.d__y.md": "- **Y:** two.\n"})
    repo("branch", "-f", "base")
    repo.commit("code and y", **{"src__x.py": "x = 2\n", "changelog.d__y.md": "- **Y:** two, reworded.\n"})
    assert main("entry", "base", capsys=capsys)[0] == 0
    repo("reset", "-q", "--hard", "base")
    repo.commit("code and x", **{"src__x.py": "x = 3\n", "changelog.d__x.md": "- **X:** one, reworded.\n"})
    code, _, err = main("entry", "base", capsys=capsys)
    assert code == 1 and "adds no file to changelog.d/" in err
