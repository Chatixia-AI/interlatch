"""Where a project stands, on its page (progress.py): the last sessions as each one left things and the decisions they
left to a person, the work week by week with each pull request counted once, and the folder's git state and roadmap
file, read only for someone at this computer."""

import json
import os
import subprocess
import time

import pytest

from interlatch.core.util import parse_ts
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


def test_repo_state_dates_what_waits_uncommitted_and_the_commits_on_no_remote(repo, tmp_path):
    three_days_ago = time.time() - 3 * 86400
    os.utime(repo / "app.py", (three_days_ago, three_days_ago))  # the change not committed
    (repo / "with space.py").write_text("x\n")
    _git(repo, "add", "with space.py")  # a staged new file, named as git would quote it
    state = progress.repo_state(str(repo))
    assert state["changed"] == 2 and state["untracked"] == 1  # notes.md is only counted, never dated
    assert abs(parse_ts(state["changed_since"]).timestamp() - three_days_ago) < 2
    assert state["unpushed"] == 1 and state["unpushed_since"]  # "Search by number" is on no remote branch
    alone = tmp_path / "no-remote"
    alone.mkdir()
    _git(alone, "init", "-q", "-b", "main")
    (alone / "a.txt").write_text("a\n")
    _git(alone, "add", "a.txt")
    _git(alone, "commit", "-q", "-m", "Only here")
    assert progress.repo_state(str(alone))["unpushed"] == 0  # without remotes, nothing is waiting to be pushed


def test_asks_are_the_decisions_and_questions_the_last_analyzed_sessions_left_open():
    def s(sid, status, *threads):
        return {"id": sid, "started_at": f"2026-10-1{sid[-1]}T12:00:00Z", "analysis_status": status, "open_threads": list(threads)}

    rows = [s("s4", "done", "A restart of app-coc-quote is pending the developer's decision",
              "Commit the changes; nothing is committed or deployed.", "Decide whether to commit docs/manual",
              "Merge PRs #154 and #155", "本番に移行するかどうか未定"),
            s("s3", "pending", "Should we ship?"),  # not analyzed: what it left open isn't known yet
            s("s2", "done", "Decide whether to commit docs/manual", "Is the export needed?", "Re-shoot the screenshots"),
            s("s1", "done", "Confirm the deploy date with the customer"),
            s("s0", "done", "Approve the pull request")]  # a fourth analyzed session: too far back
    assert [(a["session_id"], a["text"][:16]) for a in progress.asks(rows)] == [
        ("s4", "A restart of app"), ("s4", "Decide whether t"), ("s4", "Merge PRs #154 a"), ("s4", "本番に移行するかどうか未定"),
        ("s2", "Is the export ne"), ("s1", "Confirm the depl")]


ROADMAP = """# Roadmap

What is coming, roughly in order.

## Now

### Answers that say what they don't know

- **Trust that expires.** An established item no session confirmed for 90 days shows as seen once.
  - an indented detail is not an item
- [Empty search results](docs/search.md) that say what was searched and what wasn't, so a miss is not mistaken

### Fixes

- __Desktop app__ without the command-line install

## Next

- [ ] Named upgrade steps
- [x] Something finished

```markdown
- **Not an item:** inside a code block
```

## Suggesting something

Open an issue.
"""


def test_a_roadmap_file_is_read_by_its_sections_and_items(tmp_path):
    project = tmp_path / "proj"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "todo.md").write_text("- [ ] Ship it\n- [x] Plan it\n")
    got = progress.roadmap(str(project))
    assert got["file"] == "docs/todo.md" and got["updated"]
    assert got["sections"] == [{"title": "", "items": ["Ship it"], "total": 1, "open": 1, "done": 1}]
    (project / "RoadMap.md").write_text(ROADMAP)  # any case, and a roadmap before a to-do list
    got = progress.roadmap(str(project))
    assert got["file"] == "RoadMap.md" and os.path.samefile(got["path"], project / "RoadMap.md")
    assert got["sections"] == [
        {"title": "Now", "items": ["Trust that expires", "Empty search results that say what was searched and what "
                                   "wasn't, so a miss is not mistaken", "Desktop app"], "total": 3, "open": 0, "done": 0},
        {"title": "Next", "items": ["Named upgrade steps"], "total": 1, "open": 1, "done": 1}]
    inner = project / "src"
    inner.mkdir()
    assert progress.roadmap(str(inner)) is None  # the folder alone has none
    assert progress.roadmap(str(inner), root=str(project))["file"] == "RoadMap.md"  # its repository's root has one
    assert progress.roadmap(str(tmp_path / "gone")) is None and progress.roadmap("claude.ai") is None


def test_the_page_lists_asks_always_and_reads_a_roadmap_only_on_this_computer(project, repo):
    conn = project["conn"]
    (repo / "ROADMAP.md").write_text(ROADMAP)
    _session(conn, "s-repo", "2026-10-14T12:00:00Z", path=str(repo), title="Here", analysis_status="done",
             open_threads_json=json.dumps(["Decide whether to ship the beta"]))
    conn.commit()
    ctx = App(project["cfg"]).context()
    away = api.projects.project(ctx, str(repo))
    assert away["roadmap"] is None and [a["text"] for a in away["asks"]] == ["Decide whether to ship the beta"]
    assert api.projects.project(ctx, str(repo), here=True)["roadmap"]["sections"][0]["title"] == "Now"

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


def test_a_group_page_reads_its_projects_as_one(project):
    from interlatch.projects import groups

    conn = project["conn"]
    _session(conn, "s-away", "2026-10-15T12:00:00Z", path="/Users/me/Home/blog", title="Not in the group")
    conn.execute("UPDATE sessions SET analysis_status = 'done' WHERE id IN ('s-mid', 's-else')")
    conn.execute("UPDATE sessions SET open_threads_json = ? WHERE id = 's-else'", (json.dumps(["Decide on a name"]),))
    conn.commit()
    work = groups.save(conn, None, "Work", ["/Users/me/Work"])
    ctx = App(project["cfg"]).context()
    page = api.projects.group(ctx, work)
    assert (page["name"], page["folders"]) == ("Work", ["/Users/me/Work"])
    assert [m["label"] for m in page["members"]] == ["other", "quote-agent"]  # the latest session's first
    assert [m["sessions"] for m in page["members"]] == [1, 4] and page["stats"]["sessions"] == 5
    assert page["stats"]["active_s"] == 6900  # every project's, not the blog's
    assert [r["id"] for r in page["recent"][:2]] == ["s-else", "s-last"]
    assert [x["id"] for x in page["timeline"]["weeks"][0]["items"]] == ["s-else", "s-last"]
    assert page["timeline"]["weeks"][0]["items"][0]["project_path"] == "/Users/me/Work/other"  # named on the page
    # each project's own last sessions give its asks, newest first, each with the project it came from
    assert [(a["text"], a["project_path"]) for a in page["asks"]] == [
        ("Decide on a name", "/Users/me/Work/other"), ("Deploy b11 awaits approval", PROJECT)]
    assert len(page["sessions"]) == 5 and "s-away" not in {s["id"] for s in page["sessions"]}
    assert page["artifacts"]["total"] == 3  # the pull request and two commits, each once
    assert page["repos"] is None and page["roadmaps"] is None  # not asked from this computer
    weekly = page["weekly"]
    assert len(weekly["weeks"]) == 26 and weekly["weeks"] == sorted(weekly["weeks"])  # oldest first, Mondays
    assert [x["project_path"] for x in weekly["series"]] == ["/Users/me/Work/other", PROJECT]
    assert {x["date"] for x in page["daily"]} <= {"2026-09-16", "2026-10-07", "2026-10-14"}  # the blog's day isn't one
    assert page["diagram_sources"] == [] and all(m["diagram"] is None for m in page["members"])
    assert api.projects.group(ctx, work + 100) is None  # no such group
    empty = groups.save(conn, None, "Nothing yet", ["/Users/me/Elsewhere"])
    assert api.projects.group(ctx, empty) is None  # no sessions in it


def test_a_group_page_counts_this_weeks_work_and_draws_each_projects_own_sketch(project):
    from interlatch.core.util import to_iso, utcnow
    from interlatch.projects import groups

    conn = project["conn"]
    _session(conn, "s-now", to_iso(utcnow()), active_s=1200)
    sketch = {"nodes": [{"id": "api", "label": "API", "kind": "component", "note": "", "sources": [7]},
                        {"id": "db", "label": "DB", "kind": "store", "note": "", "sources": [7]}],
              "edges": [{"from": "api", "to": "db", "label": "writes", "sources": [7]}]}
    conn.execute("INSERT INTO knowledge(id, project_path, kind, title, status) VALUES (7, ?, 'fact', 'The API writes to the DB', 'active')",
                 (PROJECT,))
    conn.execute("INSERT INTO project_kb(project_path, project_name, updated_at, kb_json) VALUES (?, 'quote-agent', ?, ?)",
                 (PROJECT, to_iso(utcnow()), json.dumps({"overview": "x", "diagram": sketch})))
    conn.commit()
    gid = groups.save(conn, None, "Work", ["/Users/me/Work"])
    page = api.projects.group(App(project["cfg"]).context(), gid)
    mine = next(x for x in page["weekly"]["series"] if x["project_path"] == PROJECT)
    assert mine["values"][-1] >= 1200  # this week is the last (with s-last too when it falls in it)
    member = next(m for m in page["members"] if m["project_path"] == PROJECT)
    assert [n["id"] for n in member["diagram"]["nodes"]] == ["api", "db"] and member["diagram"]["layout"]["nodes"]
    assert [k["title"] for k in page["diagram_sources"]] == ["The API writes to the DB"]


def test_a_group_page_reads_each_folder_once_and_only_for_someone_at_this_computer(project, repo):
    from interlatch.projects import groups

    conn = project["conn"]
    (repo / "ROADMAP.md").write_text(ROADMAP)
    (repo / "src").mkdir()
    _session(conn, "s-repo", "2026-10-14T12:00:00Z", path=str(repo), title="Here")
    _session(conn, "s-src", "2026-10-13T12:00:00Z", path=str(repo / "src"), title="In a folder of it")
    conn.commit()
    gid = groups.save(conn, None, "Mine", [str(repo)])
    ctx = App(project["cfg"]).context()
    away = api.projects.group(ctx, gid)
    assert away["repos"] is None and away["roadmaps"] is None
    page = api.projects.group(ctx, gid, here=True)
    assert [r["project_path"] for r in page["repos"]] == [str(repo)]  # one repository, read once for both projects
    assert page["repos"][0]["branch"] == "main" and page["repos"][0]["changed"] == 1
    assert [(r["project_path"], r["file"]) for r in page["roadmaps"]] == [(str(repo), "ROADMAP.md")]
