"""Where a project stands, on its page (progress.py): the last sessions as each one left things, the work week by week
with each pull request counted once, and the folder's git state, read only for someone at this computer."""

import json
import os
import subprocess

import pytest

from interlatch.interfaces.web import api
from interlatch.interfaces.web.app import App
from interlatch.projects import progress

PROJECT = "/Users/me/Work/quote-agent"


def _session(conn, sid, started, path=PROJECT, **cols):
    cols = {"id": sid, "project_path": path, "project_name": path.rsplit("/", 1)[-1], "started_at": started, **cols}
    conn.execute(f"INSERT INTO sessions({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(cols.values()))


@pytest.fixture()
def project(synced):
    conn = synced["conn"]
    # Wednesdays at noon UTC, so each lands in the same week (Monday to Sunday) in any time zone
    _session(conn, "s-old", "2026-09-16T12:00:00Z", title="Extraction pipeline", outcome="completed", active_s=3600)
    _session(conn, "s-mid", "2026-10-07T12:00:00Z", title="Review overlay", outcome="partial", active_s=1800,
             goal="Make the overlay truthful", open_threads_json=json.dumps(["Deploy b11 awaits approval", " "]))
    _session(conn, "s-new", "2026-10-07T13:00:00Z", title="Golden set", outcome=None, analysis_status="pending",
             active_s=600)
    _session(conn, "s-last", "2026-10-14T12:00:00Z", title="Search by number", outcome="completed", active_s=900,
             goal="Find quotes by any number", open_threads_json=json.dumps(["Restart app-coc-quote"]))
    _session(conn, "s-else", "2026-10-14T12:30:00Z", path="/Users/me/Work/other", title="Elsewhere")
    pr = "url:https://dev.azure.com/x/pr/2"
    for sid, key, kind, title in (("s-mid", pr, "pr", "Checker fix"), ("s-last", pr, "pr", "Checker fix"),
                                  ("s-mid", "commit:a1", "commit", "one"), ("s-mid", "commit:b2", "commit", "two"),
                                  ("s-last", "commit:a1", "commit", "one")):
        conn.execute("INSERT INTO artifacts(session_id, key, kind, action, title, url) VALUES (?, ?, ?, 'opened', ?, ?)",
                     (sid, key, kind, title, key[4:] if kind == "pr" else None))
    conn.commit()
    return synced


def test_recent_is_the_last_sessions_newest_first_with_what_each_left_open(project):
    rows = progress.recent(project["conn"], PROJECT, limit=3)
    assert [r["id"] for r in rows] == ["s-last", "s-new", "s-mid"]  # another project's newer session isn't one of them
    assert rows[0]["goal"] == "Find quotes by any number" and rows[0]["open_threads"] == ["Restart app-coc-quote"]
    assert rows[1]["open_threads"] == [] and rows[1]["analysis_status"] == "pending"
    assert rows[2]["open_threads"] == ["Deploy b11 awaits approval"]  # a blank thread is dropped
    assert "open_threads_json" not in rows[0]


def test_the_timeline_goes_week_by_week_and_counts_a_pull_request_once(project):
    tl = progress.timeline(project["conn"], PROJECT)
    assert [w["start"] for w in tl["weeks"]] == ["2026-10-12", "2026-10-05", "2026-09-14"] and tl["earlier"] == 0
    newest, middle, oldest = tl["weeks"]
    assert [x["id"] for x in middle["items"]] == ["s-new", "s-mid"] and middle["sessions"] == 2
    assert middle["active_s"] == 2400 and middle["outcomes"] == {"partial": 1, "not analyzed": 1}
    # the PR and commit a1 went into the week of the first session that made them, not again when s-last did
    assert middle["prs"] == [{"title": "Checker fix", "url": "https://dev.azure.com/x/pr/2"}] and middle["commits"] == 2
    assert newest["prs"] == [] and newest["commits"] == 0 and oldest["outcomes"] == {"completed": 1}
    capped = progress.timeline(project["conn"], PROJECT, weeks=2)
    assert len(capped["weeks"]) == 2 and capped["earlier"] == 1


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(cwd), *args],
                   check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    work = tmp_path / "quote-agent"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / "app.py").write_text("print(1)\n")
    _git(work, "add", "app.py")
    _git(work, "commit", "-q", "-m", "First")
    _git(tmp_path, "clone", "-q", "--bare", str(work), "origin.git")
    _git(work, "remote", "add", "origin", str(tmp_path / "origin.git"))
    _git(work, "fetch", "-q", "origin")
    _git(work, "branch", "-q", "-u", "origin/main")
    (work / "app.py").write_text("print(2)\n")
    _git(work, "commit", "-q", "-am", "Search by number")  # one ahead of origin/main
    (work / "app.py").write_text("print(3)\n")  # changed, not committed
    (work / "notes.md").write_text("new\n")  # untracked
    _git(work, "worktree", "add", "-q", "-b", "golden", str(tmp_path / "quote-agent-golden"))
    return work


def test_repo_state_reads_the_branch_what_isnt_committed_the_last_commit_and_other_worktrees(repo):
    state = progress.repo_state(str(repo))
    assert os.path.samefile(state["root"], repo)
    assert (state["branch"], state["upstream"], state["ahead"], state["behind"]) == ("main", "origin/main", 1, 0)
    assert (state["changed"], state["untracked"], state["conflicts"]) == (1, 1, 0)
    assert state["last"]["subject"] == "Search by number" and state["last"]["at"]
    assert [w["branch"] for w in state["worktrees"]] == ["golden"]  # not the folder's own
    assert os.path.samefile(state["worktrees"][0]["path"], repo.parent / "quote-agent-golden")
    inner = repo / "src"
    inner.mkdir()
    assert os.path.samefile(progress.repo_state(str(inner))["root"], repo)  # a project in a folder of the repository


def test_repo_state_is_none_for_a_folder_that_isnt_a_repository_or_isnt_here(tmp_path):
    assert progress.repo_state(str(tmp_path)) is None
    assert progress.repo_state(str(tmp_path / "gone")) is None
    assert progress.repo_state("claude.ai") is None and progress.repo_state("") is None


def test_the_project_page_reads_git_only_for_someone_at_this_computer(project, repo):
    conn = project["conn"]
    _session(conn, "s-repo", "2026-10-14T12:00:00Z", path=str(repo), title="Here")
    conn.commit()
    ctx = App(project["cfg"]).context()
    assert api.projects.project(ctx, str(repo))["repo"] is None  # through a hub or from another device
    page = api.projects.project(ctx, str(repo), here=True)
    assert page["repo"]["branch"] == "main" and page["recent"][0]["id"] == "s-repo"
    assert page["timeline"]["weeks"][0]["start"] == "2026-10-12"


def test_a_session_shared_as_knowledge_shows_the_pull_requests_it_lists_but_not_to_a_limited_person(project):
    conn = project["conn"]
    prs = [{"number": 7, "url": "https://github.com/x/y/pull/7", "repo": "x/y"},
           {"number": 2, "url": "https://dev.azure.com/x/pr/2"},  # an artifact already: listed once, by its title
           {"number": 9, "url": "javascript:alert(1)", "repo": "x/y"}, "not a pr"]  # as another computer may send it
    conn.execute("UPDATE sessions SET prs_json = ? WHERE id = 's-last'", (json.dumps(prs),))
    conn.commit()
    newest, middle, _ = progress.timeline(conn, PROJECT)["weeks"]
    assert newest["prs"] == [{"title": "x/y#7", "url": "https://github.com/x/y/pull/7"}]
    assert middle["prs"] == [{"title": "Checker fix", "url": "https://dev.azure.com/x/pr/2"}]
    assert progress.timeline(conn, PROJECT, session_prs=False)["weeks"][0]["prs"] == []
    ctx = App(project["cfg"]).context()
    assert api.projects.project(ctx, PROJECT)["timeline"]["weeks"][0]["prs"] == newest["prs"]
    assert api.projects.project(ctx, PROJECT, limited=True)["timeline"]["weeks"][0]["prs"] == []  # as on its page


def test_on_a_hub_with_people_each_session_says_whose_it_is(project):
    from interlatch.sharing import people

    conn, ctx = project["conn"], App(project["cfg"]).context()
    assert "who" not in api.projects.project(ctx, PROJECT)["recent"][0]  # no people: they're all yours
    yuma = people.add(conn, "Yuma", None, "member")
    machine = "0b9e6a52-6f0e-4a8e-9a3e-2f1d0c4b5a61"
    conn.execute("INSERT INTO machines(id, person_id) VALUES (?, ?)", (machine, yuma["id"]))
    conn.execute("UPDATE sessions SET machine_id = ? WHERE id = 's-last'", (machine,))
    conn.commit()
    page = api.projects.project(ctx, PROJECT)
    assert page["recent"][0]["who"] == "Yuma" and page["recent"][1]["who"] != "Yuma"  # the hub's own computer
    assert page["timeline"]["weeks"][0]["items"][0]["who"] == "Yuma"
