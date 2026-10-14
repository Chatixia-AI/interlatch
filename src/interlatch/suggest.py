"""Suggestions: one approval queue of proposed fixes, from the friction catalog and from trusted knowledge.

refresh() rebuilds the queue after a sync: every recurring, still-happening friction cause proposes its fixes (an
instruction line, a config change, or a setup step for you to run), and knowledge worth an instruction line proposes
one. Nothing is written until a suggestion is applied. A dismissed suggestion never comes back, text you edited
before applying is kept, and a proposal whose cause stopped happening goes stale instead of nagging.

Applying writes only inside Interlatch's block of an instruction file (instructions.py), or adds the missing
Playwright MCP arguments to ~/.claude.json. Environment steps are shown, never run: you mark them done.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

from . import friction, instructions
from .config import Config
from .i18n import lang, tr, trn
from .util import dumps, loads, one_line, utcnow_iso

log = logging.getLogger("interlatch.suggest")

STATUSES = ("new", "applied", "dismissed", "stale", "done")
COLUMNS = ("key", "kind", "origin", "cause_id", "knowledge_id", "project_path", "agent", "target_path", "title", "text",
           "evidence_json", "warnings_json", "score")
_CONFIG_CHANGE = re.compile(r"^\s*mcpServers\.([\w.@/-]+?)\.args\s*\+=\s*(\[.*\])\s*$", re.S)


# ---------------------------------------------------------------- reading
def _row(r) -> dict:
    d = dict(r)
    d["evidence"] = loads(d.pop("evidence_json", None), {}) or {}
    d["warnings"] = loads(d.pop("warnings_json", None), {}) or {}
    return d


def get(conn: sqlite3.Connection, sid: int) -> dict | None:
    r = conn.execute("SELECT * FROM suggestions WHERE id = ?", (int(sid),)).fetchone()
    return _row(r) if r else None


def list_suggestions(conn: sqlite3.Connection, status: str | None = None, project: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM suggestions WHERE 1 = 1", []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if project:
        sql += " AND project_path = ?"
        params.append(project)
    return [_row(r) for r in conn.execute(sql + " ORDER BY score DESC, id", params)]


def display(s: dict) -> dict:
    """A suggestion as the dashboard shows it, in the viewer's language (i18n). The row keeps its English title and
    evidence line (a knowledge title, and every line's text, stay as they were written), so they are rebuilt here."""
    if lang.get() == "en":
        return s
    e = dict(s.get("evidence") or {})
    if s["origin"] == "friction":
        s = {**s, "title": tr(s["title"])}
        if e.get("cause"):
            e["cause"] = tr(e["cause"])
        if e.get("line") and "sessions" in e and "still_happening" in e:
            e["line"] = _friction_line(e)
        e["examples"] = [{**x, "note": friction.display_note(x.get("note"))} for x in e.get("examples") or []]
    elif s["origin"] == "knowledge" and e.get("line") and "sessions" in e:
        e["line"] = _knowledge_line(e)
    return {**s, "evidence": e}


def _friction_line(e: dict) -> str:
    """_evidence_line, in the viewer's language."""
    n, p = e.get("sessions") or 0, e.get("projects") or 0
    parts = [trn(n, "seen in {n} session across {p} projects", "seen in {n} sessions across {p} projects", p=p) if p > 1
             else trn(n, "seen in {n} session", "seen in {n} sessions")]
    parts.append(tr("still happening") if e.get("still_happening") else tr("not seen lately"))
    if e.get("last_seen"):
        parts.append(tr("last {date}", date=e["last_seen"]))
    if e.get("project_sessions") is not None:
        parts.append(tr("{n} in this project", n=e["project_sessions"]))
    return " · ".join(parts)


def _knowledge_line(e: dict) -> str:
    """A knowledge suggestion's evidence line (instructions._candidate), in the viewer's language."""
    n, p = e.get("sessions") or 0, e.get("projects") or 0
    where = e["line"].rsplit(" · ", 1)[-1]
    confirmed = (trn(n, "confirmed in {n} session across {p} projects", "confirmed in {n} sessions across {p} projects", p=p)
                 if p > 1 else trn(n, "confirmed in {n} session", "confirmed in {n} sessions"))
    return " · ".join([tr(e.get("stage") or "provisional"), confirmed, tr(where) if where == "user level" else where])


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for r in conn.execute("SELECT status, COUNT(*) FROM suggestions GROUP BY status"):
        out[r[0]] = r[1]
    return out


def count_unseen(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM suggestions WHERE status = 'new' AND seen_at IS NULL").fetchone()[0]


def mark_seen(conn: sqlite3.Connection) -> int:
    cur = conn.execute("UPDATE suggestions SET seen_at = ? WHERE status = 'new' AND seen_at IS NULL", (utcnow_iso(),))
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------- building proposals
def claude_json_path() -> Path:
    """Claude Code's user config (MCP servers): $CLAUDE_CONFIG_DIR/.claude.json when set, else ~/.claude.json."""
    custom = os.environ.get("CLAUDE_CONFIG_DIR")
    if custom and (Path(custom).expanduser() / ".claude.json").exists():
        return Path(custom).expanduser() / ".claude.json"
    return Path("~/.claude.json").expanduser()


def playwright_output_dir() -> str:
    return str(Path("~/.cache/playwright-mcp").expanduser())


def fix_text(fix: dict, cfg: Config) -> str:
    """A catalog fix's text in the language Interlatch writes in: instruction lines have a Japanese version (text_ja);
    commands and config changes are the same in every language."""
    text = fix.get("text_ja") if cfg.analysis.language == "ja" and fix.get("text_ja") else fix["text"]
    return text.replace("{playwright_output_dir}", playwright_output_dir())


def _evidence_line(cause: dict) -> str:
    n, p = cause["sessions"], len(cause["projects"])
    parts = [f"seen in {n} session{'s' if n != 1 else ''}" + (f" across {p} projects" if p > 1 else "")]
    parts.append("still happening" if cause["still_happening"] else "not seen lately")
    if cause["last_seen"]:
        parts.append(f"last {cause['last_seen']}")
    return " · ".join(parts)


def _friction_evidence(cause: dict, text: str) -> dict:
    return {
        "line": _evidence_line(cause), "sessions": cause["sessions"], "occurrences": cause["occurrences"],
        "immediate_repeats": cause["immediate_repeats"], "projects": len(cause["projects"]),
        "project_names": cause["projects"][:5], "agents": cause["agents"], "first_seen": cause["first_seen"],
        "last_seen": cause["last_seen"], "still_happening": cause["still_happening"], "rate": cause["rate"],
        "weekly": cause["weekly"], "examples": cause["examples"], "cause": cause["name"], "generated_text": text,
    }


def _file_label(target: Path) -> str:
    return "claude" if target.name == "CLAUDE.md" else "codex"


def _read(path: Path | None) -> str:
    try:
        return path.read_text() if path else ""
    except OSError:
        return ""


def _friction_proposals(conn: sqlite3.Connection, cfg: Config, cause: dict,
                        project_agents: dict[str, list[str]] | None = None,
                        choices: dict[str, tuple[str, str]] | None = None) -> list[dict]:
    out = []
    cid = cause["id"]
    choice = instructions.chosen_scope(choices or {}, [f"cause:{cid}"])  # where you moved it, if you did
    seen_agents = [a for a in cause["agents"] if a in ("claude", "codex", "copilot", "bob", "antigravity")]
    score = round(cause["sessions"] + 0.5 * len(cause["projects"]) + 0.25 * cause["immediate_repeats"], 3)
    base = {"origin": "friction", "cause_id": cid, "knowledge_id": None, "score": score}
    for fix in cause["fixes"]:
        kind = fix["kind"]
        text = fix_text(fix, cfg)
        if kind == "environment":
            out.append({**base, "key": f"friction:{cid}:all:user:environment", "kind": kind, "project_path": None,
                        "agent": "all", "target_path": None, "title": fix["title"], "text": text,
                        "warnings": {"sensitive": instructions.sensitive(text), "public_repo": False, "untracked": False,
                                     "overlap": False}})
            continue
        if kind == "config":
            if "claude" not in seen_agents or config_state(claude_json_path(), text) != "pending":
                continue  # Playwright MCP not configured here, or the arguments are already there
            out.append({**base, "key": f"friction:{cid}:claude:user:config", "kind": kind, "project_path": None,
                        "agent": "claude", "target_path": str(claude_json_path()), "title": fix["title"], "text": text,
                        "warnings": {"sensitive": [], "public_repo": False, "untracked": False, "overlap": False}})
            continue
        agents = seen_agents if "all" in fix["agents"] else [a for a in fix["agents"] if a in seen_agents]
        if not agents:
            continue
        spread = len(cause["projects"]) >= 3
        scope = choice or ("project" if fix["scope"] == "project" else ("user" if spread else "project"))
        targets: dict[tuple[Path, str | None], list[str]] = {}
        if scope == "user":
            for a in agents:
                t = instructions.target_for(a, "user", None, cfg)
                if t:
                    targets.setdefault((t, None), []).append(a)
        else:
            paths = cause.get("project_paths") or []
            chosen = [p["path"] for p in paths if p["sessions"] >= 2][:5] if "project" in (fix["scope"], choice) else []
            for path in chosen or [p["path"] for p in paths[:1]]:
                here = (project_agents or {}).get(path)
                for a in [a for a in agents if not here or a in here]:
                    t = instructions.target_for(a, "project", path, cfg)
                    if t:
                        targets.setdefault((t, path), []).append(a)
        for (target, project), who in targets.items():
            content = _read(target)
            if any(instructions.overlap(t, content) for t in (text, fix["text"], fix.get("text_ja")) if t):
                continue  # the file already says this, in either language
            out.append({**base, "key": f"friction:{cid}:{_file_label(target)}:{project or 'user'}", "kind": "instruction",
                        "project_path": project, "agent": who[0] if len(who) == 1 else "all",
                        "target_path": str(target), "title": fix["title"], "text": text,
                        "warnings": instructions.warnings_for(text, target, project, conn)})
    here = {p["path"]: p["sessions"] for p in cause.get("project_paths") or []}
    for p in out:
        p["evidence"] = _friction_evidence(cause, p["text"])
        if p["project_path"] and len(here) > 1:
            n = here.get(p["project_path"], 0)
            p["evidence"]["project_sessions"] = n
            p["evidence"]["line"] += f" · {n} in this project"
    return out


def proposals(conn: sqlite3.Connection, cfg: Config) -> tuple[list[dict], dict[str, dict], list[dict]]:
    """(every suggestion the data supports right now, the scanned causes by id, knowledge lines waiting for room in
    their file)."""
    causes = {c["id"]: c for c in friction._scan(conn)}
    project_agents = instructions._project_agents(conn)
    choices = instructions.scope_choices(conn)
    rows = [dict(r) for r in conn.execute("SELECT * FROM suggestions")]
    status = {r["key"]: r["status"] for r in rows}
    decided = [r for r in rows if r["origin"] == "knowledge" and r["status"] in ("dismissed", "applied")]
    out: list[dict] = []
    waiting: list[dict] = []
    for c in causes.values():
        if c["noise"] or c["category"] == "other" or not c["fixes"]:
            continue
        if not c["still_happening"] or not (c["sessions"] >= 3 or len(c["projects"]) >= 2):
            continue
        out.extend(_friction_proposals(conn, cfg, c, project_agents, choices))
    # lessons you already dismissed or applied in a file take none of its places, so the next ones come up
    for k in instructions.candidates(conn, cfg, lambda c: status.get(c["key"]) in ("dismissed", "applied", "done")
                                     or _decided_lesson(decided, c), waiting):
        out.append({**k, "warnings": k.get("warnings") or {}})
    seen: set[str] = set()
    return [p for p in out if not (p["key"] in seen or seen.add(p["key"]))], causes, waiting


def refresh(conn: sqlite3.Connection, cfg: Config) -> dict[str, int]:
    """Upsert every current proposal by key; mark proposals the data no longer supports stale."""
    found, causes, waiting = proposals(conn, cfg)
    now = utcnow_iso()
    existing = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM suggestions")}
    report = {"new": 0, "updated": 0, "stale": 0}
    for p in found:
        evidence, warnings = dumps(p["evidence"]), dumps(p["warnings"])
        old = existing.get(p["key"])
        if old is None and _decided_lesson(existing.values(), p):
            continue  # the same lesson, under another member's id, was already dismissed or applied here
        if old is None and _in_file(p):
            continue  # its line is already in the file's block (its marker is the same in every language)
        if old is None:
            conn.execute(
                f"INSERT INTO suggestions({', '.join(COLUMNS)}, status, created_at, updated_at) "
                f"VALUES ({', '.join('?' * len(COLUMNS))}, 'new', ?, ?)",
                (p["key"], p["kind"], p["origin"], p["cause_id"], p["knowledge_id"], p["project_path"], p["agent"],
                 p["target_path"], p["title"], p["text"], evidence, warnings, p["score"], now, now))
            report["new"] += 1
            continue
        if old["status"] == "dismissed":  # never resurrected; only its cluster is kept current for _decided_lesson
            ev = loads(old["evidence_json"], {}) or {}
            ids = sorted({*(ev.get("knowledge_ids") or []), *(p["evidence"].get("knowledge_ids") or [])})
            if ids and ev.get("knowledge_ids") != ids:
                conn.execute("UPDATE suggestions SET evidence_json = ? WHERE id = ?", (dumps({**ev, "knowledge_ids": ids}), old["id"]))
            continue
        status, seen_at = old["status"], old["seen_at"]
        if status == "stale":
            status, seen_at = "new", None
            report["new"] += 1
        generated = (loads(old["evidence_json"], {}) or {}).get("generated_text")
        edited = generated is not None and old["text"] != generated
        text = old["text"] if edited or old["status"] in ("applied", "done") else p["text"]
        if status == "applied" and old["origin"] == "friction":
            cause = causes.get(old["cause_id"] or "")
            if cause:
                p["evidence"]["sessions_since_applied"] = friction.sessions_since(cause, old["applied_at"])
                evidence = dumps(p["evidence"])
        # an applied line stays where it was written: undo must find it there, whatever the routing says now
        agent, target = (old["agent"], old["target_path"]) if status in ("applied", "done") else (p["agent"], p["target_path"])
        changed = (text, evidence, warnings, p["score"], status, p["title"], agent, target) != (
            old["text"], old["evidence_json"], old["warnings_json"], old["score"], old["status"], old["title"],
            old["agent"], old["target_path"])
        if changed:
            conn.execute(
                "UPDATE suggestions SET text = ?, title = ?, evidence_json = ?, warnings_json = ?, score = ?, status = ?, "
                "seen_at = ?, agent = ?, target_path = ?, updated_at = ? WHERE id = ?",
                (text, p["title"], evidence, warnings, p["score"], status, seen_at, agent, target, now, old["id"]))
            if old["status"] != "stale":
                report["updated"] += 1
    keys = {p["key"] for p in found}
    # a waiting card whose lesson or cause is now proposed for another file, or waits for room in its file, was only
    # a proposal: it is dropped rather than called stale, and comes back where and when there is room
    live = {i for p in found + waiting if p["origin"] == "knowledge" for i in _lesson_ids(p)}
    live_causes = {p["cause_id"] for p in found if p["origin"] == "friction" and p["kind"] == "instruction"}
    for key, old in existing.items():
        if key in keys:
            continue
        if old["status"] == "new" and old["kind"] == "instruction" and (
                old["cause_id"] in live_causes if old["origin"] == "friction" else bool(live & _lesson_ids(old))):
            conn.execute("DELETE FROM suggestions WHERE id = ?", (old["id"],))
        elif old["status"] == "new":
            conn.execute("UPDATE suggestions SET status = 'stale', updated_at = ? WHERE id = ?", (now, old["id"]))
            report["stale"] += 1
        elif old["status"] == "applied" and old["origin"] == "friction":
            cause = causes.get(old["cause_id"] or "")  # no longer proposed (fixed?): keep counting what follows
            if cause:
                ev = loads(old["evidence_json"], {}) or {}
                ev["sessions_since_applied"] = friction.sessions_since(cause, old["applied_at"])
                ev["still_happening"] = cause["still_happening"]
                conn.execute("UPDATE suggestions SET evidence_json = ? WHERE id = ?", (dumps(ev), old["id"]))
    conn.commit()
    return report


def move(conn: sqlite3.Connection, cfg: Config, sid: int, to: str) -> dict:
    """Send a waiting instruction line to the user-level file ("user") or back to the files of its projects
    ("project"), and remember that over the automatic choice. Every waiting card of the same lesson or cause is made
    again where it now goes, ahead of the 8-a-file limit; applied and dismissed ones stay as they are."""
    s = get(conn, sid)
    if s is None:
        return {"ok": False, "error": tr("no such suggestion")}
    if to not in ("user", "project"):
        return {"ok": False, "error": tr("move to user or project")}
    if s["kind"] != "instruction" or s["status"] != "new" or s["origin"] not in ("friction", "knowledge"):
        return {"ok": False, "error": tr("only an instruction line waiting for review can move")}
    if (s["project_path"] is None) == (to == "user"):
        return {"ok": False, "error": tr("it is already there")}
    ids = _lesson_ids(s)
    subjects = [f"cause:{s['cause_id']}"] if s["origin"] == "friction" else [f"knowledge:{i}" for i in sorted(ids)]

    def mine(r: dict) -> bool:
        if r["origin"] != s["origin"]:
            return False
        return r["cause_id"] == s["cause_id"] if r["origin"] == "friction" else bool(ids & _lesson_ids(r))
    now = utcnow_iso()
    conn.executemany("INSERT INTO suggestion_scopes(subject, scope, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(subject) DO UPDATE SET scope = excluded.scope, updated_at = excluded.updated_at",
                     [(x, to, now) for x in subjects])
    conn.commit()
    refresh(conn, cfg)  # makes its cards where it now goes and drops the waiting ones where it was
    waiting = "SELECT * FROM suggestions WHERE status = 'new' AND kind = 'instruction'"
    arrived = [r for r in map(dict, conn.execute(waiting)) if mine(r) and (r["project_path"] is None) == (to == "user")]
    if s["text"] != s["evidence"].get("generated_text", s["text"]):  # your wording comes along
        conn.executemany("UPDATE suggestions SET text = ? WHERE id = ?", [(s["text"], r["id"]) for r in arrived])
        conn.commit()
    return {"ok": True, "moved": len(arrived), "targets": sorted({r["target_path"] for r in arrived})}


def _in_file(p: dict) -> bool:
    """The target file's Interlatch block already holds this proposal's line, by its marker."""
    if p["kind"] != "instruction" or not p["target_path"]:
        return False
    try:
        return marker(p) in {k for k, _text in instructions.read_block(Path(p["target_path"]))}
    except instructions.MalformedBlock:
        return False


def _lesson_ids(s: dict) -> set[int]:
    """The knowledge items behind a proposal or a stored suggestion: the one it names and the rest of its lesson."""
    ev = s["evidence"] if "evidence" in s else (loads(s.get("evidence_json"), {}) or {})
    return {i for i in (s.get("knowledge_id"), *(ev.get("knowledge_ids") or [])) if i is not None}


def _decided_lesson(rows, p: dict) -> bool:
    """A knowledge proposal for a lesson already dismissed or applied for the same file: one of its knowledge items
    was in that suggestion's cluster, or its title is a near-duplicate of that suggestion's title."""
    if p["origin"] != "knowledge":
        return False
    ids = set(p["evidence"].get("knowledge_ids") or [])
    title = instructions.tokens(p["title"])
    for r in rows:
        if r["origin"] != "knowledge" or r["status"] not in ("dismissed", "applied") or r["target_path"] != p["target_path"]:
            continue
        theirs = {r["knowledge_id"], *((loads(r["evidence_json"], {}) or {}).get("knowledge_ids") or [])}
        if ids & theirs or instructions.jaccard(title, instructions.tokens(r["title"])) >= instructions.JACCARD_DUPLICATE:
            return True
    return False


def after_sync(conn: sqlite3.Connection, cfg: Config) -> dict[str, int] | None:
    """The background run's hook: refresh when enabled, and notify about new suggestions when asked to."""
    if not cfg.suggestions_enabled:
        return None
    try:
        report = refresh(conn, cfg)
    except Exception:  # never let suggestions break a sync
        log.exception("suggestions refresh failed")
        return None
    if cfg.suggestions_notify and report["new"] > 0:
        from .notify import post

        post("Interlatch", f"{report['new']} new suggestion{'s' if report['new'] != 1 else ''}. "
                          "Open the dashboard > Suggestions")
    return report


# ---------------------------------------------------------------- config changes (~/.claude.json)
# the only config change Interlatch makes: these flags, on a Playwright MCP server (an edited text cannot widen it)
CONFIG_FLAGS = {"--isolated": False, "--output-dir": True}  # flag -> takes a value


def parse_config_change(text: str) -> tuple[str, list[str]]:
    """'mcpServers.<name>.args += [...]' -> (server name, args to add). Raises ValueError otherwise."""
    m = _CONFIG_CHANGE.match(text or "")
    if not m:
        raise ValueError(tr("expected: mcpServers.<name>.args += [\"--flag\", ...]"))
    try:
        args = json.loads(m.group(2))
    except ValueError:
        raise ValueError(tr("the arguments must be a JSON list of strings")) from None
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError(tr("the arguments must be a JSON list of strings"))
    if "playwright" not in m.group(1).lower():
        raise ValueError(tr("Interlatch only changes the Playwright MCP server's arguments"))
    i = 0
    while i < len(args):
        if args[i] not in CONFIG_FLAGS:
            raise ValueError(tr("Interlatch does not set {arg!r}; only {flags}", arg=args[i], flags=", ".join(CONFIG_FLAGS)))
        if CONFIG_FLAGS[args[i]]:
            i += 1
            if i >= len(args) or not args[i].startswith("/"):
                raise ValueError(tr("{flag} needs an absolute path", flag=args[i - 1]))
        i += 1
    return m.group(1), args


def _groups(args: list[str]) -> list[list[str]]:
    """Split ['--isolated', '--output-dir', 'x'] into [['--isolated'], ['--output-dir', 'x']]."""
    out: list[list[str]] = []
    for a in args:
        if a.startswith("-") or not out:
            out.append([a])
        else:
            out[-1].append(a)
    return out


def missing_args(current: list[str], wanted: list[str]) -> list[str]:
    """The groups of `wanted` whose flag `current` lacks (a flag already set is left as it is)."""
    have = set(current)
    return [a for g in _groups(wanted) if g[0] not in have for a in g]


def _load_json(path: Path) -> tuple[str, dict]:
    old = path.read_text()
    data = json.loads(old) if old.strip() else {}
    if not isinstance(data, dict):
        raise ValueError(tr("{path} is not a JSON object", path=path))
    return old, data


def config_text(name: str, args: list[str]) -> str:
    return f"mcpServers.{name}.args += {json.dumps(args)}"


def config_state(path: Path, text: str) -> str:
    """'pending' (the change would do something), 'satisfied', 'no-server', 'no-file' or 'invalid'."""
    try:
        name, wanted = parse_config_change(text)
        _old, data = _load_json(path)
    except FileNotFoundError:
        return "no-file"
    except (OSError, ValueError):
        return "invalid"
    server = (data.get("mcpServers") or {}).get(name)
    if not isinstance(server, dict):
        return "no-server"
    return "pending" if missing_args(list(server.get("args") or []), wanted) else "satisfied"


def plan_config(path: Path, text: str, *, remove: bool = False) -> tuple[str, str]:
    """(old file text, new file text) for adding (or removing) the arguments; every other key is kept.

    Undo passes only the groups apply added (applied_text), so flags you had set yourself stay.
    """
    name, wanted = parse_config_change(text)
    old, data = _load_json(path)
    server = (data.get("mcpServers") or {}).get(name)
    if not isinstance(server, dict):
        raise ValueError(tr("no MCP server named {name!r} in {path}", name=name, path=path))
    args = list(server.get("args") or [])
    if remove:
        for g in _groups(wanted):
            for i in range(len(args) - len(g) + 1):
                if args[i:i + len(g)] == g:
                    del args[i:i + len(g)]
                    break
    else:
        args += missing_args(args, wanted)
    server["args"] = args
    new = json.dumps(data, indent=2, ensure_ascii=False)
    return old, new + "\n" if old.endswith("\n") else new  # keep the file's own last line as it was


# ---------------------------------------------------------------- preview / apply
def _diff(old: str, new: str, path: Path) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=str(path), tofile=str(path)))


def marker(s: dict) -> str:
    """The id written into the file next to the line: short and free of paths (the full key holds the project path,
    which has no business in a committed CLAUDE.md). Unique within one file: one line per cause or lesson."""
    ident = s["cause_id"] if s["origin"] == "friction" else s["knowledge_id"]
    if ident is not None and str(ident).strip() and not re.search(r"[\s/]", str(ident)):
        return f"{s['origin']}:{ident}"
    return "s-" + hashlib.sha1(s["key"].encode()).hexdigest()[:12]


def _plan(s: dict, text: str, *, remove: bool = False) -> tuple[Path, str, str]:
    path = Path(s["target_path"])
    if s["project_path"] and not Path(s["project_path"]).is_dir():
        raise ValueError(tr("the project {path} no longer exists", path=s["project_path"]))
    if s["kind"] == "instruction":
        old = _read(path) if path.exists() else ""
        mark = marker(s)
        new = instructions.merge_file(path, old, [] if remove else [(mark, text)], {mark, s["key"]} if remove else set())
        return path, old, new
    if s["kind"] == "config":
        old, new = plan_config(path, text, remove=remove)
        return path, old, new
    raise ValueError(tr("{kind} suggestions are not written by Interlatch", kind=s["kind"]))


def preview(conn: sqlite3.Connection, cfg: Config, sid: int, text: str | None = None) -> dict:
    """The unified diff applying would make, written nowhere. Environment steps return their command instead."""
    s = get(conn, sid)
    if s is None:
        return {"ok": False, "error": tr("no such suggestion")}
    text = (text if text is not None else s["text"]).strip()
    # ~/.claude.json is never committed or shared, so a home path there is not a leak
    warnings = {**(s["warnings"] or {}), "sensitive": [] if s["kind"] == "config" else instructions.sensitive(text)}
    if s["kind"] == "environment":
        return {"ok": True, "diff": "", "path": None, "command": text, "warnings": warnings}
    try:
        path, old, new = _plan(s, text)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "diff": "", "path": s["target_path"], "warnings": warnings}
    return {"ok": True, "diff": _diff(old, new, path), "path": str(path), "warnings": warnings}


def apply(conn: sqlite3.Connection, cfg: Config, sid: int, text: str | None = None) -> dict:
    """Write the suggestion (backing the file up first) and mark it applied. Returns {ok, path, diff}."""
    s = get(conn, sid)
    if s is None:
        return {"ok": False, "error": tr("no such suggestion")}
    if s["kind"] == "environment":
        return {"ok": False, "error": tr("Interlatch does not run setup steps: run the command yourself, then mark it done")}
    text = one_line(text, 2000) if text is not None and s["kind"] == "instruction" else (text or s["text"]).strip()
    applied = text
    evidence = dict(s["evidence"])
    try:
        path, old, new = _plan(s, text)
        if s["kind"] == "instruction" and s["status"] != "applied":  # applying again keeps what the first apply found
            evidence["created_file"] = not Path(os.path.realpath(path)).exists()
        if s["kind"] == "config":  # remember only what was added, so undo leaves your own flags alone
            name, wanted = parse_config_change(text)
            server = ((json.loads(old) if old.strip() else {}).get("mcpServers") or {}).get(name) or {}
            applied = config_text(name, missing_args(list(server.get("args") or []), wanted))
        if new != old:
            instructions.write_atomic(path, new, cfg=cfg, mkdir=not s["project_path"])
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "path": s["target_path"], "diff": ""}
    now = utcnow_iso()
    conn.execute("UPDATE suggestions SET status = 'applied', text = ?, applied_text = ?, applied_at = ?, updated_at = ?, "
                 "seen_at = COALESCE(seen_at, ?), evidence_json = ? WHERE id = ?",
                 (text, applied, now, now, now, dumps(evidence), sid))
    conn.commit()
    return {"ok": True, "path": str(path), "diff": _diff(old, new, path), "applied_text": applied}


def unapply(conn: sqlite3.Connection, cfg: Config, sid: int) -> dict:
    """Take an applied suggestion back out of its file (or an environment step out of 'done'); it returns to 'new'."""
    s = get(conn, sid)
    if s is None:
        return {"ok": False, "error": tr("no such suggestion")}
    diff, path = "", None
    gone = bool(s["project_path"]) and not Path(s["project_path"]).is_dir()  # the line went with the project
    if s["kind"] != "environment" and s["status"] == "applied" and not gone:
        try:
            path, old, new = _plan(s, s["applied_text"] or s["text"], remove=True)
            if not new.strip() and s["evidence"].get("created_file") and s["kind"] == "instruction":
                instructions.write_atomic(path, new, cfg=cfg, mkdir=False)  # backs it up first
                Path(os.path.realpath(path)).unlink(missing_ok=True)  # Interlatch made this file: leave none behind
            elif new != old:
                instructions.write_atomic(path, new, cfg=cfg, mkdir=not s["project_path"])
            diff = _diff(old, new, path)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
    conn.execute("UPDATE suggestions SET status = 'new', applied_at = NULL, applied_text = NULL, updated_at = ? "
                 "WHERE id = ?", (utcnow_iso(), sid))
    conn.commit()
    return {"ok": True, "path": str(path) if path else None, "diff": diff}


def edit_text(conn: sqlite3.Connection, sid: int, text: str) -> dict:
    """Keep an edited proposal text; refresh will not overwrite it."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": tr("empty text")}
    cur = conn.execute("UPDATE suggestions SET text = ?, updated_at = ? WHERE id = ? AND status IN ('new', 'stale')",
                       (text, utcnow_iso(), sid))
    conn.commit()
    return {"ok": cur.rowcount == 1}


def dismiss(conn: sqlite3.Connection, sid: int, reason: str | None = None) -> dict:
    s = get(conn, sid)
    if s is not None and s["status"] == "applied":
        return {"ok": False, "error": tr("this one is applied: undo it first, so its line leaves the file")}
    cur = conn.execute("UPDATE suggestions SET status = 'dismissed', dismissed_reason = ?, updated_at = ? WHERE id = ?",
                       ((reason or "").strip() or None, utcnow_iso(), sid))
    conn.commit()
    return {"ok": cur.rowcount == 1}


def mark_done(conn: sqlite3.Connection, sid: int) -> dict:
    """You ran an environment step yourself."""
    s = get(conn, sid)
    if s is None or s["kind"] != "environment":
        return {"ok": False, "error": tr("only environment steps are marked done")}
    now = utcnow_iso()
    conn.execute("UPDATE suggestions SET status = 'done', applied_at = ?, applied_text = text, updated_at = ? WHERE id = ?",
                 (now, now, sid))
    conn.commit()
    return {"ok": True}
