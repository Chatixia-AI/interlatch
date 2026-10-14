"""Where a project stands, for someone coming back to it: its last sessions as each one left things, its work week by
week, and its repository as it is now. All of it is read from the archive and the project's folder; no model writes
any of it, so it is never more than the sessions and git say."""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..core.paths import is_abs_path
from ..core.util import loads, parse_ts, to_iso

RECENT = 5  # sessions under "Where you left off"
ASK_SESSIONS = 3  # the last analyzed sessions whose open decisions and questions "Needs you" lists
WEEKS = 52  # weeks with sessions the timeline lists; older ones are only counted
WEEK_ITEMS = 100  # sessions listed for one week
GIT_TIMEOUT = 4  # seconds for each git call: the project page waits for them
ROADMAP_FILES = ("roadmap.md", "backlog.md", "todo.md", "todos.md")  # in this order; any case, in the folder or docs/
ROADMAP_BYTES = 256_000  # read of a roadmap file at most
SECTION_ITEMS = 30  # items kept for one section of a roadmap

# An open thread that waits on a person: a decision, an approval, an answer or a check the session couldn't make
# itself. Matched on how session analyses word them, in English and (with [analysis] language = "ja") Japanese.
ASK = re.compile(
    r"\?\s*$|？"
    r"|^(decide|choose|confirm|should|whether|merge|approve|review)\b"
    r"|\b(await(s|ing)?|waiting\s+(for|on)|pending|needs?|requires?|up\s+to)\b[^.;]{0,60}"
    r"\b(approv\w*|decision|decide|sign-?off|answer|confirm\w*|choice|go-ahead|merge|review)\b"
    r"|\b(developer|user)('s)?\b[^.;]{0,40}\b(decide|approve|choose|confirm|pick|answer|review|offered)"
    r"|\b(was|were)\s+offered\b|\bnot\s+yet\s+approved\b|\bno\s+(answer|confirmation)\b|\bundecided\b|\bunconfirmed\b"
    r"|承認|判断|決め|決定|かどうか",
    re.IGNORECASE)


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


def asks(rows: list[dict], sessions: int = ASK_SESSIONS) -> list[dict]:
    """What the last few analyzed sessions (`rows` from recent(), newest first) left open that waits on a person: a
    decision, an approval, an answer. Each with the session it came from; the same words in an older one once."""
    out, seen = [], set()
    for r in [r for r in rows if r.get("analysis_status") == "done"][:sessions]:
        for text in r.get("open_threads") or []:
            if ASK.search(text) and text not in seen:
                seen.add(text)
                out.append({"text": text, "session_id": r["id"], "started_at": r["started_at"], "who": r.get("who")})
    return out


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
    committed and since when, the commits that are only on this computer, the last commit and the repository's other
    worktrees. None when the folder isn't on this computer or isn't in a repository, or git fails or takes too long.

    changed_since: the oldest edit among the tracked files that differ from the last commit (new, untracked files are
    only counted: build output and scratch files sit there for good). unpushed: commits on no remote branch, with the
    oldest one's date; none counted in a repository without remotes.

    Only reads: no lock is taken, so an agent working in the folder isn't disturbed, and the repository's own config
    can't make git run anything (fsmonitor, hooks)."""
    if not path or not is_abs_path(path) or not os.path.isdir(path):
        return None

    def git(*args: str) -> str | None:
        return _git(path, *args)

    try:
        top = (git("rev-parse", "--show-toplevel") or "").strip()
        if not top:
            return None
        root = Path(top)
        if root.resolve() == Path.home().resolve() or root.parent == root:
            return None  # a home folder (or a whole disk) kept in git: its status would walk everything under it
        status = git("status", "--porcelain=v2", "--branch", "-z")  # -z: paths exactly as they are, never quoted
        if status is None:
            return None
        last = git("log", "-1", "--format=%h%x00%s%x00%cI")  # none yet in a repository without commits
        trees = git("worktree", "list", "--porcelain") or ""
        unpushed = git("log", "--format=%cI", "HEAD", "--not", "--remotes") if (git("remote") or "").strip() else None
    except (OSError, subprocess.SubprocessError):
        return None
    state = {"root": top, "branch": None, "upstream": None, "ahead": 0, "behind": 0, "changed": 0, "untracked": 0,
             "conflicts": 0, "changed_since": None, "unpushed": 0, "unpushed_since": None, "last": None,
             "worktrees": []}
    changed: list[str] = []
    entries = iter(status.split("\0"))
    for entry in entries:
        if entry.startswith("# branch.head "):
            head = entry[len("# branch.head "):]
            state["branch"] = None if head == "(detached)" else head
        elif entry.startswith("# branch.upstream "):
            state["upstream"] = entry[len("# branch.upstream "):]
        elif entry.startswith("# branch.ab "):
            ahead, behind = entry[len("# branch.ab "):].split()
            state["ahead"], state["behind"] = abs(int(ahead)), abs(int(behind))
        elif entry.startswith("1 "):
            state["changed"] += 1
            changed.append(entry.split(" ", 8)[-1])
        elif entry.startswith("2 "):  # a rename: its old path follows as an entry of its own
            state["changed"] += 1
            changed.append(entry.split(" ", 9)[-1])
            next(entries, None)
        elif entry.startswith("u "):
            state["conflicts"] += 1
            changed.append(entry.split(" ", 10)[-1])
        elif entry.startswith("? "):
            state["untracked"] += 1
    edits = []
    for rel in changed[:5000]:
        try:
            edits.append(os.stat(root / rel).st_mtime)
        except OSError:
            continue  # deleted: there's no file to date
    if edits:
        state["changed_since"] = to_iso(datetime.fromtimestamp(min(edits), tz=timezone.utc))
    dates = [d for d in map(parse_ts, (unpushed or "").split()) if d]
    if dates:
        state["unpushed"], state["unpushed_since"] = len(dates), to_iso(min(dates))
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


def _git(cwd: str, *args: str) -> str | None:
    """git's output in a folder, None when it fails. Reads only: no optional lock is taken, so an agent working there
    isn't disturbed, and the repository's own config can't make git run anything (fsmonitor, hooks). Raises OSError
    without git, and subprocess.TimeoutExpired after GIT_TIMEOUT."""
    out = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", cwd, *args],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
                         env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    return out.stdout if out.returncode == 0 else None


def roadmap(path: str, root: str | None = None) -> dict | None:
    """The project's own roadmap, backlog or to-do list, when its folder or its repository's root (`root`) has one:
    ROADMAP.md, BACKLOG.md, TODO.md or TODOS.md, there or in docs/, in any case. Its sections (## headings) with their
    items, and how many of their checkboxes are open and ticked; `updated` is the file's last commit, else its last
    change. None without such a file. Read from this computer's disk, like repo_state."""
    found = _roadmap_file(path, root)
    if not found:
        return None
    try:
        with open(found, "rb") as fh:
            text = fh.read(ROADMAP_BYTES).decode("utf-8", "replace")
        changed = to_iso(datetime.fromtimestamp(os.stat(found).st_mtime, tz=timezone.utc))
    except OSError:
        return None
    try:
        committed = (_git(str(found.parent), "log", "-1", "--format=%cI", "--", found.name) or "").strip()
    except (OSError, subprocess.SubprocessError):
        committed = ""
    sections = _sections(text)
    if not sections:
        return None
    name = found.name
    for base in (path, root):
        try:
            name = found.relative_to(base).as_posix()
            break
        except (TypeError, ValueError):
            continue
    return {"path": str(found), "file": name, "updated": committed or changed, "sections": sections}


def _roadmap_file(path: str, root: str | None) -> Path | None:
    folders = [f for f in dict.fromkeys((path, root)) if f and is_abs_path(f)]  # the project's folder first
    for want in ROADMAP_FILES:
        for folder in folders:
            for where in (Path(folder), Path(folder) / "docs"):
                try:
                    names = {n.lower(): n for n in os.listdir(where)}
                except OSError:
                    continue
                if want in names and (where / names[want]).is_file():
                    return where / names[want]
    return None


def _sections(text: str) -> list[dict]:
    """A Markdown roadmap's ## sections. An item is a top-level bullet, named by its bold lead or its first words
    (ticked checkboxes are only counted); a section without bullets lists its ### headings instead. A file without
    ## headings is one section with no title."""
    sections: list[dict] = []
    cur, fenced = None, False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        head = re.match(r"(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if head:
            level, title = len(head.group(1)), _plain(head.group(2))
            if level == 2 or (level > 2 and cur is None):
                cur = {"title": title, "items": [], "subs": [], "open": 0, "done": 0}
                sections.append(cur)
            elif level > 2:
                cur["subs"].append(title)
            continue  # a # heading is the document's title
        bullet = re.match(r"[-*+]\s+(?:\[([ xX])\]\s+)?(\S.*)", line)  # top level only: an indented one is detail
        if not bullet:
            continue
        if cur is None:
            cur = {"title": "", "items": [], "subs": [], "open": 0, "done": 0}
            sections.append(cur)
        if bullet.group(1) is not None:
            ticked = bullet.group(1) in "xX"
            cur["done" if ticked else "open"] += 1
            if ticked:
                continue
        cur["items"].append(_lead(bullet.group(2)))
    out = []
    for s in sections:
        items = s["items"] or s["subs"]
        if items:
            out.append({"title": s["title"], "items": items[:SECTION_ITEMS], "total": len(items), "open": s["open"],
                        "done": s["done"]})
    return out


def _plain(text: str) -> str:
    return re.sub(r"\*\*|__|`", "", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)).strip()


def _lead(text: str) -> str:
    """A bullet as one short item: its bold lead (**Trust that expires.**), else its first words."""
    bold = re.match(r"(\*\*|__)(.+?)\1", text)
    item = _plain(bold.group(2) if bold else text).rstrip(" .:：。")
    return item if len(item) <= 100 else item[:97].rsplit(" ", 1)[0] + "…"
