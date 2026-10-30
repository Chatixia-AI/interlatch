"""Which of your coding projects a web chat (claude.ai, ChatGPT) is about: a suggestion on the chat, never a move.

A chat stays under claude.ai or chatgpt.com; its `link_*` columns say which project it most likely belongs to, how
sure that is, and who said so. Several steps suggest, each only where a surer one hasn't:

1. Rules, no model call (`link_pending`, on import, capture and every sync): the chat's claude.ai or ChatGPT project
   has a coding project's name, or the chat names one (its name, folder, repository) or terms only that project's
   glossary uses. A chat is checked again when it changes.
2. Screening (screen.py) picks one of `choices()` or none for the chats rules left open.
3. A chat's full analysis (analysis/session.py) picks too, and corrects what rules or screening said.
4. You: Confirm, or Change to another project or to none, on the session page. Nothing overwrites that.

Project pages show the chats you confirmed, and their knowledge (api/projects.py); the knowledge itself keeps the
chat's own project, so where it is shared (hubs, profiles) doesn't change.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePath

from ..core.util import utcnow_iso
from .systems import CHAT_SOURCES

BY_RULES, BY_SCREEN, BY_ANALYSIS, BY_YOU = "rules", "screen", "analysis", "you"
CONFIDENCE = ("high", "medium", "low")
NONE = "none"  # what screening and analysis answer for a chat about no project
MAX_CHOICES = 25  # projects a model picks from: the most recent
TEXT_CHARS = 40_000  # how much of a chat the rules read
# names too common to tell a project by in prose
_COMMON = {"app", "apps", "web", "site", "website", "api", "server", "client", "backend", "frontend", "test", "tests",
           "demo", "docs", "project", "projects", "main", "code", "tools", "tool", "data", "home", "work", "notes",
           "scripts", "src", "lib", "core", "game", "blog", "mobile", "admin", "dashboard", "service", "bot", "chat",
           "agent", "agents", "config", "setup", "temp", "tmp", "sandbox", "playground", "personal"}
_SITE_RE = re.compile(r"^(claude\.ai|chatgpt\.com)/(.+)$")


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())


def _word_re(term: str) -> re.Pattern:
    return re.compile(r"(?<![\w-])" + re.escape(term.lower()) + r"(?![\w-])")


@dataclass
class Project:
    path: str
    name: str
    aliases: set[str] = field(default_factory=set)  # its name, folder and repository names, lowercase
    terms: set[str] = field(default_factory=set)  # glossary terms no other project uses
    last: str = ""


class Linker:
    """The coding projects a chat can belong to, read once for many chats."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        marks = ",".join("?" * len(CHAT_SOURCES))
        self.projects: dict[str, Project] = {}
        for path, name, last in conn.execute(
                f"SELECT project_path, MAX(project_name), MAX(started_at) FROM sessions WHERE source NOT IN ({marks}) "
                f"AND source != 'history' AND project_path IS NOT NULL AND project_path != '' GROUP BY project_path",
                CHAT_SOURCES):
            p = Project(path, name or PurePath(path).name, last=last or "")
            p.aliases = {a.lower() for a in (p.name, PurePath(path).name) if a}
            self.projects[path] = p
        self._repos()
        self._terms()
        self._choices: dict[str, str] | None = None

    def _repos(self) -> None:
        """Repository names from the remotes computers reported ({cwd: [top level, github.com/org/repo]})."""
        for (raw,) in self.conn.execute("SELECT repos_json FROM machines WHERE repos_json IS NOT NULL"):
            try:
                repos = json.loads(raw) or {}
            except ValueError:
                continue
            for cwd, info in repos.items() if isinstance(repos, dict) else ():
                remote = info[1] if isinstance(info, (list, tuple)) and len(info) > 1 else None
                top = info[0] if isinstance(info, (list, tuple)) and info else cwd
                if isinstance(remote, str) and "/" in remote:
                    for path in (cwd, top):
                        if path in self.projects:
                            self.projects[path].aliases.add(remote.rsplit("/", 1)[-1].lower())

    def _terms(self) -> None:
        used: dict[str, set[str]] = defaultdict(set)
        for term, path in self.conn.execute(
                "SELECT g.term, u.project_path FROM glossary_usage u JOIN glossary g ON g.id = u.term_id "
                "WHERE u.project_path != '__global__'"):
            if term and len(term) >= 4:
                used[term.lower()].add(path)
        for term, paths in used.items():
            if len(paths) == 1 and (path := next(iter(paths))) in self.projects:
                self.projects[path].terms.add(term)

    def choices(self) -> dict[str, str]:
        """{label: project path} for a model to pick from: the most recent projects, a name used twice told apart by
        its parent folder."""
        if self._choices is None:
            recent = sorted(self.projects.values(), key=lambda p: p.last, reverse=True)[:MAX_CHOICES]
            names = defaultdict(int)
            for p in recent:
                names[p.name] += 1
            self._choices = {}
            for p in recent:
                label = p.name if names[p.name] == 1 else f"{p.name} ({PurePath(p.path).parent.name})"
                self._choices.setdefault(label, p.path)
        return self._choices

    def rule(self, chat_project: str | None, text: str) -> tuple[str, str, str] | None:
        """(project path, confidence, reason) when a rule settles it; reason is `site:<site>:<name>` or
        `mentions:<a>|<b>`, which the dashboard words."""
        m = _SITE_RE.match(chat_project or "")
        if m:
            want = _norm(m.group(2))
            hits = [p for p in self.projects.values() if want and any(_norm(a) == want for a in p.aliases)]
            if len(hits) == 1:
                return hits[0].path, "high", f"site:{m.group(1)}:{m.group(2)}"
        low = (text or "").lower()
        scored = []
        for p in self.projects.values():
            found, score = [], 0
            if len(p.path) > 8 and p.path.lower() in low:
                found.append(PurePath(p.path).name)
                score += 4
            for a in sorted(p.aliases, key=len, reverse=True):
                if len(a) >= 4 and _norm(a) not in _COMMON and not a.isdigit() and _word_re(a).search(low):
                    if not found:
                        found.append(a)
                        score += 3
                    break
            terms = [t for t in sorted(p.terms) if _word_re(t).search(low)][:3]
            found += terms
            score += len(terms)
            if score:
                scored.append((score, p, found))
        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])
        best, p, found = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0
        if best < 3 or best - runner_up < 2:
            return None
        return p.path, "high" if best >= 5 else "medium", "mentions:" + "|".join(dict.fromkeys(found))


def _text(conn: sqlite3.Connection, sid: str, title: str | None) -> str:
    parts, n = [title or ""], 0
    for (t,) in conn.execute("SELECT text FROM events WHERE session_id = ? AND agent_id = '' AND kind IN ('prompt', 'text') "
                             "ORDER BY seq", (sid,)):
        if not t:
            continue
        parts.append(t)
        n += len(t)
        if n > TEXT_CHARS:
            break
    return "\n".join(parts)[:TEXT_CHARS]


def link_pending(conn: sqlite3.Connection, ids: list[str] | None = None, *, linker: Linker | None = None,
                 limit: int = 5000) -> int:
    """Run the rules on chats not checked at their current version (or `ids`); returns how many got a suggestion."""
    marks = ",".join("?" * len(CHAT_SOURCES))
    sql = (f"SELECT id, files_sig, title, project_path FROM sessions WHERE source IN ({marks}) "
           f"AND (link_by IS NULL OR link_by = 'rules') AND (link_sig IS NULL OR link_sig != files_sig)")
    params: list = list(CHAT_SOURCES)
    if ids is not None:
        if not ids:
            return 0
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params += ids
    rows = conn.execute(sql + f" ORDER BY started_at DESC LIMIT {int(limit)}", params).fetchall()
    if not rows:
        return 0
    linker = linker or Linker(conn)
    found = 0
    for r in rows:
        hit = linker.rule(r["project_path"], _text(conn, r["id"], r["title"])) if linker.projects else None
        if hit:
            conn.execute("UPDATE sessions SET link_project = ?, link_state = 'suggested', link_confidence = ?, "
                         "link_by = 'rules', link_reason = ?, link_sig = ?, linked_at = ? WHERE id = ?",
                         (hit[0], hit[1], hit[2], r["files_sig"], utcnow_iso(), r["id"]))
            found += 1
        else:  # checked: rules have nothing to say, so screening or analysis may
            conn.execute("UPDATE sessions SET link_project = NULL, link_state = NULL, link_confidence = NULL, "
                         "link_by = NULL, link_reason = NULL, link_sig = ? WHERE id = ?", (r["files_sig"], r["id"]))
    conn.commit()
    return found


def suggest(conn: sqlite3.Connection, sid: str, label: str | None, choices: dict[str, str], by: str,
            confidence: str = "medium") -> None:
    """A model's pick (`label` from `choices`, or none): screening fills only chats nothing has suggested for yet;
    analysis replaces any suggestion. Yours stays."""
    label = (label or "").strip()
    path = choices.get(label)
    if not path and label.lower() != NONE:
        return  # not one of the choices: say nothing rather than guess
    confidence = confidence if confidence in CONFIDENCE else "medium"
    where = "COALESCE(link_by, '') = ''" if by == BY_SCREEN else "COALESCE(link_by, '') != 'you'"  # yours stays
    conn.execute(f"UPDATE sessions SET link_project = ?, link_state = 'suggested', link_confidence = ?, link_by = ?, "
                 f"link_reason = NULL, linked_at = ? WHERE id = ? AND {where}",
                 (path, confidence, by, utcnow_iso(), sid))


def decide(conn: sqlite3.Connection, sid: str, path: str | None) -> dict:
    """Your answer: this chat belongs to `path` (confirmed), or to no project (path None or '')."""
    row = conn.execute("SELECT source FROM sessions WHERE id = ?", (sid,)).fetchone()
    if row is None or row["source"] not in CHAT_SOURCES:
        raise ValueError("only a claude.ai or ChatGPT chat links to a project")
    if path:
        marks = ",".join("?" * len(CHAT_SOURCES))
        known = conn.execute(f"SELECT 1 FROM sessions WHERE project_path = ? AND source NOT IN ({marks}) LIMIT 1",
                             (path, *CHAT_SOURCES)).fetchone()
        if not known:
            raise ValueError("not one of your projects")
    conn.execute("UPDATE sessions SET link_project = ?, link_state = ?, link_confidence = NULL, link_by = 'you', "
                 "link_reason = NULL, linked_at = ? WHERE id = ?",
                 (path or None, "confirmed" if path else NONE, utcnow_iso(), sid))
    conn.commit()
    return link_of(conn, sid)


def link_of(conn: sqlite3.Connection, sid: str) -> dict:
    """The chat's link as the session page shows it, with the projects it may be changed to."""
    r = conn.execute("SELECT link_project, link_state, link_confidence, link_by, link_reason, linked_at FROM sessions "
                     "WHERE id = ?", (sid,)).fetchone()
    linker = Linker(conn)
    name = linker.projects[r["link_project"]].name if r and r["link_project"] in linker.projects else None
    choices = [{"label": label, "path": path} for label, path in sorted(linker.choices().items(), key=lambda x: x[0].lower())]
    return {"project": r["link_project"] if r else None, "name": name, "state": r["link_state"] if r else None,
            "confidence": r["link_confidence"] if r else None, "by": r["link_by"] if r else None,
            "reason": r["link_reason"] if r else None, "at": r["linked_at"] if r else None, "choices": choices}


CONFIRMED_SQL = "link_state = 'confirmed' AND link_project = ?"  # a project page's chats: the ones you confirmed
