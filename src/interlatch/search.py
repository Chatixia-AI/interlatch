"""Full-text search over transcripts and knowledge (FTS5 trigram, with LIKE for very short terms)."""

from __future__ import annotations

import re
import sqlite3

from .db import fts_query, short_terms
from .ladder import STAGE_ORDER_SQL, confirmations
from .util import loads


def query_terms(query: str) -> list[str]:
    return [t.strip('"') for t in re.findall(r'"[^"]+"|\S+', query.strip()) if t.strip('"')]


def make_snippet(text: str, terms: list[str], width: int = 180) -> str:
    """Window of `text` around the first matching term, with matches wrapped in «»."""
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", text)
    lower = flat.lower()
    pos = min((i for i in (lower.find(t.lower()) for t in terms) if i >= 0), default=0)
    start = max(0, pos - width // 3)
    end = min(len(flat), start + width)
    snippet = ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")
    for t in sorted(terms, key=len, reverse=True):
        snippet = re.sub(re.escape(t), lambda m: f"«{m.group(0)}»", snippet, flags=re.I)
    return snippet


def _like_clause(column: str, terms: list[str]) -> tuple[str, list]:
    return " AND ".join(f"{column} LIKE ?" for _ in terms), [f"%{t}%" for t in terms]


def _matching_events(query: str, columns: str, ranked: bool) -> tuple[str, list, list]:
    """SELECT over the events matching `query` (FTS5 trigram, LIKE for short terms): sql, params, extra where clauses."""
    fts = fts_query(query)
    short = short_terms(query)
    where, params = [], []
    if fts:
        sql = (f"SELECT {columns}{', bm25(events_fts) AS rank' if ranked else ''} "
               "FROM events_fts JOIN events e ON e.id = events_fts.rowid WHERE events_fts MATCH ?")
        params.append(fts)
    else:
        sql = f"SELECT {columns}{', 0 AS rank' if ranked else ''} FROM events e WHERE e.searchable = 1"
    if short:
        clause, p = _like_clause("e.text", short)
        where.append(clause)
        params += p
    return sql, params, where


def search_events(conn: sqlite3.Connection, query: str, *, project: str | None = None, session_id: str | None = None,
                  kinds: list[str] | None = None, limit: int = 50) -> list[dict]:
    terms = query_terms(query)
    if not terms:
        return []
    sql, params, where = _matching_events(query, "e.id, e.session_id, e.agent_id, e.seq, e.ts, e.kind, e.tool_name, e.text", True)
    if session_id:
        where.append("e.session_id = ?")
        params.append(session_id)
    if kinds:
        where.append(f"e.kind IN ({','.join('?' * len(kinds))})")
        params += kinds
    if project:
        where.append("e.session_id IN (SELECT id FROM sessions WHERE project_path = ? OR project_name = ?)")
        params += [project, project]
    if where:
        sql += " AND " + " AND ".join(where)
    sql += " ORDER BY rank, e.ts DESC LIMIT ?"
    params.append(limit)
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []
    if not rows:
        return []
    meta = {r["id"]: dict(r) for r in conn.execute(
        f"SELECT id, title, project_name, project_path, started_at, outcome FROM sessions WHERE id IN "
        f"({','.join('?' * len({r['session_id'] for r in rows}))})", list({r["session_id"] for r in rows}))}
    for r in rows:
        r["snippet"] = make_snippet(r.pop("text") or "", terms)
        s = meta.get(r["session_id"], {})
        r.update({"title": s.get("title"), "project_name": s.get("project_name"), "started_at": s.get("started_at"),
                  "outcome": s.get("outcome")})
    return rows


def search_sessions(conn: sqlite3.Connection, query: str, *, project: str | None = None, limit: int = 10) -> list[dict]:
    """Sessions ranked by transcript matches plus title/summary/tag matches."""
    terms = query_terms(query)
    if not terms:
        return []
    hits = search_events(conn, query, project=project, limit=400)
    by_session: dict[str, dict] = {}
    for h in hits:
        s = by_session.setdefault(h["session_id"], {
            "session_id": h["session_id"], "title": h["title"], "project_name": h["project_name"],
            "started_at": h["started_at"], "outcome": h["outcome"], "hits": 0, "snippets": [], "score": 0.0,
        })
        s["hits"] += 1
        s["score"] += 1.0
        if len(s["snippets"]) < 3:
            s["snippets"].append({"kind": h["kind"], "seq": h["seq"], "agent_id": h["agent_id"], "text": h["snippet"]})
    clause, params = _like_clause("(COALESCE(title,'') || ' ' || COALESCE(summary,'') || ' ' || COALESCE(tags_json,'') || ' ' || COALESCE(first_prompt,''))", terms)
    sql = f"SELECT id, title, project_name, started_at, outcome, summary FROM sessions WHERE {clause}"
    if project:
        sql += " AND (project_path = ? OR project_name = ?)"
        params += [project, project]
    for r in conn.execute(sql + " LIMIT 200", params).fetchall():
        s = by_session.setdefault(r["id"], {
            "session_id": r["id"], "title": r["title"], "project_name": r["project_name"], "started_at": r["started_at"],
            "outcome": r["outcome"], "hits": 0, "snippets": [], "score": 0.0,
        })
        s["score"] += 5.0
        if r["summary"] and len(s["snippets"]) < 3:
            s["snippets"].insert(0, {"kind": "summary", "seq": None, "agent_id": "", "text": make_snippet(r["summary"], terms)})
    ranked = sorted(by_session.values(), key=lambda s: (s["score"], s["started_at"] or ""), reverse=True)
    return ranked[:limit]


def search_knowledge(conn: sqlite3.Connection, query: str | None = None, *, project: str | None = None,
                     kind: str | None = None, include_inactive: bool = False, limit: int = 30,
                     sessions: tuple[str, list] | None = None, source: str | None = None,
                     cases: bool = False, lessons: bool = False, offset: int = 0) -> list[dict]:
    """`sessions`: (SQL on the sessions table, its params) to keep the lessons of those sessions only. `source`: only
    items from there ('analysis', 'memory', 'team'), filtered before the limit. `cases`: only the ones with a case
    file (analyze.case_of). `lessons`: only the kinds a lesson is made of (learning.PRACTICE_SQL)."""
    where, params = [], []
    if query and query.strip():
        fts = fts_query(query)
        short = short_terms(query)
        if fts:
            where.append("k.id IN (SELECT rowid FROM knowledge_fts WHERE knowledge_fts MATCH ?)")
            params.append(fts)
        if short:
            clause, p = _like_clause("(k.title || ' ' || COALESCE(k.body, ''))", short)
            where.append(clause)
            params += p
        if not fts and not short:
            return []
    if project:
        where.append("(k.project_path = ? OR k.project_name = ?)")
        params += [project, project]
    if kind:
        where.append("k.kind = ?")
        params.append(kind)
    if source:
        where.append("k.source = ?")
        params.append(source)
    if cases:
        from .analyze import CASE_FILE_SQL

        where.append(CASE_FILE_SQL)
    if lessons:
        from .learning import PRACTICE_SQL

        where.append(PRACTICE_SQL)
    if sessions:
        where.append(f"k.session_id IN (SELECT id FROM sessions WHERE {sessions[0]})")
        params += sessions[1]
    if not include_inactive:
        where.append("k.status = 'active'")
    sql = "SELECT k.*, s.title AS session_title, s.started_at AS session_started FROM knowledge k LEFT JOIN sessions s ON s.id = k.session_id"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY k.pinned DESC, {STAGE_ORDER_SQL}, COALESCE(s.started_at, k.created_at) DESC, k.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, offset)])
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []
    for r in rows:
        r["tags"] = loads(r.pop("tags_json", None), []) or []
        r["confirmations"] = len(confirmations(r))
    return rows


def search_all(conn: sqlite3.Connection, query: str, *, project: str | None = None, sort: str = "hits",
               offset: int = 0, limit: int = 25, per_session: int = 5) -> dict:
    """Every session that mentions the query, with how often, and its first few mentions in transcript order.

    All matching events are counted (ids only); text is read just for the mentions shown."""
    terms = query_terms(query)
    if not terms:
        return {"total": 0, "mentions": 0, "sessions": []}
    sql, params, where = _matching_events(query, "e.id, e.session_id, e.agent_id, e.seq", False)
    if project:
        where.append("e.session_id IN (SELECT id FROM sessions WHERE project_path = ? OR project_name = ?)")
        params += [project, project]
    if where:
        sql += " AND " + " AND ".join(where)
    hits: dict[str, list[tuple]] = {}
    try:
        for r in conn.execute(sql + " LIMIT 200000", params):
            hits.setdefault(r["session_id"], []).append((r["agent_id"] != "", r["agent_id"], r["seq"], r["id"]))
    except sqlite3.OperationalError:
        return {"total": 0, "mentions": 0, "sessions": []}
    # sessions whose title, summary or tags match count too, even with no transcript mention
    clause, p = _like_clause("(COALESCE(title,'') || ' ' || COALESCE(summary,'') || ' ' || COALESCE(tags_json,''))", terms)
    meta_sql = f"SELECT id FROM sessions WHERE {clause}"
    if project:
        meta_sql += " AND (project_path = ? OR project_name = ?)"
        p += [project, project]
    named = {r[0] for r in conn.execute(meta_sql, p)}
    ids = list(hits.keys() | named)
    if not ids:
        return {"total": 0, "mentions": 0, "sessions": []}
    meta = {}
    for i in range(0, len(ids), 900):  # SQLite's parameter limit
        chunk = ids[i:i + 900]
        for r in conn.execute("SELECT id, title, project_name, project_path, started_at, outcome, agent, summary FROM sessions "
                              f"WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            meta[r["id"]] = dict(r)
    ids = [i for i in ids if i in meta]
    if sort == "newest":
        ids.sort(key=lambda i: meta[i]["started_at"] or "", reverse=True)
    elif sort == "oldest":
        ids.sort(key=lambda i: meta[i]["started_at"] or "")
    else:
        ids.sort(key=lambda i: (len(hits.get(i, ())), i in named, meta[i]["started_at"] or ""), reverse=True)
    page = ids[offset:offset + limit]
    shown = {i: sorted(hits.get(i, []))[:per_session] for i in page}
    event_ids = [h[3] for hs in shown.values() for h in hs]
    rows = {}
    if event_ids:
        rows = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT id, seq, agent_id, kind, tool_name, ts, text FROM events WHERE id IN ({','.join('?' * len(event_ids))})", event_ids)}
    out = []
    for i in page:
        m = meta[i]
        snippets = [{"seq": r["seq"], "agent_id": r["agent_id"], "kind": r["kind"], "tool_name": r["tool_name"], "ts": r["ts"],
                     "text": make_snippet(r["text"] or "", terms)} for r in (rows[h[3]] for h in shown[i] if h[3] in rows)]
        out.append({"session_id": i, "title": m["title"], "project_name": m["project_name"], "project_path": m["project_path"],
                    "started_at": m["started_at"], "outcome": m["outcome"], "agent": m["agent"], "hits": len(hits.get(i, ())),
                    "summary": make_snippet(m["summary"], terms) if i in named and m["summary"] else None, "snippets": snippets})
    return {"total": len(ids), "mentions": sum(len(v) for v in hits.values()), "sessions": out}
