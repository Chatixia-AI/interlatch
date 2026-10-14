"""Where a project stands, for someone coming back to it: its last sessions as each one left things, its work week by
week, and its repository as it is now. All of it is read from the archive and the project's folder; no model writes
any of it, so it is never more than the sessions and git say."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from datetime import timedelta
from pathlib import Path

from ..core.paths import is_abs_path
from ..core.util import loads, parse_ts

RECENT = 5  # sessions under "Where you left off"
WEEKS = 52  # weeks with sessions the timeline lists; older ones are only counted
WEEK_ITEMS = 100  # sessions listed for one week
GIT_TIMEOUT = 4  # seconds for each git call: the project page waits for them


def recent(conn: sqlite3.Connection, path: str, limit: int = RECENT) -> list[dict]:
    """The project's last sessions, newest first, as each one left things: what it set out to do, how it went and what
    it left open. Whether a later session dealt with what an earlier one left open isn't recorded."""
    rows = []
    for r in conn.execute(
            "SELECT id, agent, source, machine_id, title, started_at, active_s, analysis_status, outcome, outcome_note, "
            "goal, summary, open_threads_json FROM sessions WHERE project_path = ? ORDER BY started_at DESC LIMIT ?",
            (path, limit)):
        d = dict(r)
        d["open_threads"] = [str(x) for x in loads(d.pop("open_threads_json"), []) or [] if str(x).strip()]
        rows.append(d)
    return rows


def week_of(ts) -> str | None:
    """The Monday that starts the week of a timestamp, in this computer's time zone (2026-10-12)."""
    dt = parse_ts(ts)
    if not dt:
        return None
    day = dt.astimezone().date()
    return (day - timedelta(days=day.weekday())).isoformat()


def timeline(conn: sqlite3.Connection, path: str, weeks: int = WEEKS, session_prs: bool = True) -> dict:
    """The project's work week by week, newest first: each week's sessions, outcomes and active time, and the pull
    requests and commits its sessions made (each counted once, in the week of the first session that made it). Weeks
    without sessions are left out; `earlier` counts the weeks with sessions beyond the last `weeks`.

    session_prs: also the pull requests a session lists itself (prs_json) when no artifact records them, as for a
    session a computer shared with a hub as knowledge: the hub has its details but not its transcript."""
    by_week: dict[str, dict] = {}
    for r in conn.execute(
            "SELECT id, agent, source, machine_id, title, started_at, active_s, analysis_status, outcome FROM sessions "
            "WHERE project_path = ? ORDER BY started_at DESC", (path,)):
        start = week_of(r["started_at"])
        if not start:
            continue
        w = by_week.setdefault(start, {"start": start, "sessions": 0, "active_s": 0.0, "outcomes": {}, "items": [],
                                       "prs": [], "commits": 0})
        w["sessions"] += 1
        w["active_s"] += r["active_s"] or 0
        if r["source"] != "history":  # recovered prompts were never analyzed: no outcome to count
            outcome = r["outcome"] or "not analyzed"
            w["outcomes"][outcome] = w["outcomes"].get(outcome, 0) + 1
        if len(w["items"]) < WEEK_ITEMS:
            w["items"].append(dict(r))
    seen: set[str] = set()
    for r in conn.execute(
            "SELECT a.kind, a.key, a.title, a.url, s.started_at FROM artifacts a JOIN sessions s ON s.id = a.session_id "
            "WHERE s.project_path = ? AND a.kind IN ('pr', 'commit') ORDER BY s.started_at, a.ts, a.id", (path,)):
        w = by_week.get(week_of(r["started_at"]) or "")
        if r["key"] in seen or not w:
            continue
        seen.add(r["key"])
        if r["kind"] == "pr":
            w["prs"].append({"title": r["title"], "url": r["url"]})
        else:
            w["commits"] += 1
    if session_prs:
        for r in conn.execute("SELECT started_at, prs_json FROM sessions WHERE project_path = ? AND prs_json LIKE '[{%' "
                              "ORDER BY started_at", (path,)):
            w = by_week.get(week_of(r["started_at"]) or "")
            for pr in loads(r["prs_json"], []) or []:
                url = pr.get("url") if isinstance(pr, dict) else None  # on a hub, as another computer sent it
                if not w or not isinstance(url, str) or not url.startswith(("https://", "http://")) or f"url:{url}" in seen:
                    continue  # (url:<link> is an artifact's key for a link)
                seen.add(f"url:{url}")
                title = f"{pr['repo']}#{pr['number']}" if pr.get("repo") and pr.get("number") else url
                w["prs"].append({"title": title, "url": url})
    ordered = sorted(by_week.values(), key=lambda w: w["start"], reverse=True)
    return {"weeks": ordered[:weeks], "earlier": max(0, len(ordered) - weeks)}


def repo_state(path: str) -> dict | None:
    """The project folder's git repository as it is now: its branch and how far that is from its upstream, what isn't
    committed, the last commit and the repository's other worktrees. None when the folder isn't on this computer or
    isn't in a repository, or git fails or takes too long.

    Only reads: no lock is taken, so an agent working in the folder isn't disturbed, and the repository's own config
    can't make git run anything (fsmonitor, hooks)."""
    if not path or not is_abs_path(path) or not os.path.isdir(path):
        return None
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}

    def git(*args: str) -> str | None:
        out = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", path, *args],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
                             env=env)
        return out.stdout if out.returncode == 0 else None

    try:
        top = (git("rev-parse", "--show-toplevel") or "").strip()
        if not top:
            return None
        root = Path(top)
        if root.resolve() == Path.home().resolve() or root.parent == root:
            return None  # a home folder (or a whole disk) kept in git: its status would walk everything under it
        status = git("status", "--porcelain=v2", "--branch")
        if status is None:
            return None
        last = git("log", "-1", "--format=%h%x00%s%x00%cI")  # none yet in a repository without commits
        trees = git("worktree", "list", "--porcelain") or ""
    except (OSError, subprocess.SubprocessError):
        return None
    state = {"root": top, "branch": None, "upstream": None, "ahead": 0, "behind": 0, "changed": 0, "untracked": 0,
             "conflicts": 0, "last": None, "worktrees": []}
    for line in status.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):]
            state["branch"] = None if head == "(detached)" else head
        elif line.startswith("# branch.upstream "):
            state["upstream"] = line[len("# branch.upstream "):]
        elif line.startswith("# branch.ab "):
            ahead, behind = line[len("# branch.ab "):].split()
            state["ahead"], state["behind"] = abs(int(ahead)), abs(int(behind))
        elif line.startswith(("1 ", "2 ")):
            state["changed"] += 1
        elif line.startswith("u "):
            state["conflicts"] += 1
        elif line.startswith("? "):
            state["untracked"] += 1
    if last and last.count("\0") >= 2:
        sha, subject, at = last.strip("\n").split("\0", 2)
        state["last"] = {"sha": sha, "subject": subject, "at": at}
    def same(p: str) -> str:  # both paths come from git, spelled its way (C:/x on Windows)
        return os.path.normcase(os.path.normpath(p))

    for block in trees.strip().split("\n\n"):
        fields = dict(line.split(" ", 1) if " " in line else (line, "") for line in block.splitlines())
        where = fields.get("worktree")
        if not where or same(where) == same(top):
            continue
        branch = fields.get("branch", "")
        state["worktrees"].append({"path": where, "branch": branch.removeprefix("refs/heads/") or None})
    return state
