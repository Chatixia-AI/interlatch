"""Glossary: the vocabulary of the developer's work, distilled from sessions (terms, aliases, definitions, usage)."""

from __future__ import annotations

import json
import logging
import re
import sqlite3

from .config import Config
from .i18n import tr
from .llm import Runner, make_runner, written_in
from .synthesize import GLOBAL, kb_sections
from .util import dumps, loads, one_line, safe_text, truncate, utcnow_iso

log = logging.getLogger("interlatch.glossary")

CATEGORIES = ["system", "service", "component", "tool", "library", "platform", "concept", "acronym", "file",
              "command", "data", "domain", "organization", "other"]

GLOSSARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["terms"],
    "properties": {
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["term", "category", "definition", "aliases", "context", "related", "sources"],
                "properties": {
                    "term": {"type": "string", "description": "Canonical spelling, as the developer writes it"},
                    "aliases": {"type": "array", "items": {"type": "string"},
                                "description": "Abbreviations, expansions, translations and other spellings"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "definition": {"type": "string", "description": "What it is and what it is for, 1-2 sentences"},
                    "context": {"type": "string", "description": "How it is used here, one sentence"},
                    "related": {"type": "array", "items": {"type": "string"}, "description": "Other terms from this list"},
                    "sources": {"type": "array", "items": {"type": "integer"},
                                "description": "ids of the knowledge items that mention the term"},
                },
            },
        }
    },
}

PROJECT_SYSTEM = """\
You write the glossary for one of a developer's software projects, from what their Claude Code sessions \
learned about it (knowledge items, a synthesized overview, session titles).

Pick the terms that someone new to the project (or the developer six months from now) would need explained: \
internal system, service and component names, acronyms and abbreviations, domain vocabulary (translate \
non-English business terms and keep the original as the term or an alias), important files, commands and data \
entities, and external platforms or libraries as they are used in this project. Leave out generic programming \
words (function, API, database) unless they carry a specific meaning here.

Ground every definition in the material given. Keep the canonical spelling as the term; put abbreviations, \
expansions, translations and alternative spellings in aliases. Cite the ids of the knowledge items that mention \
each term. Aim for 10-40 terms, fewer if the material is thin."""

GLOBAL_SYSTEM = """\
You write a developer's personal glossary of cross-project vocabulary, from the knowledge their Claude Code \
sessions produced across all projects: the tools, platforms, services, libraries, concepts and acronyms they \
work with, and any recurring vocabulary of their own.

Leave out generic programming words. Ground every definition in the material given; put abbreviations, \
expansions and alternative spellings in aliases; cite the ids of the knowledge items that mention each term. \
Aim for 15-50 terms."""
# with [analysis] language = "ja", besides llm.JAPANESE
TERMS_JA = ("Write definitions and context in Japanese. A term stays spelled as the developer writes it; translations go "
            "in aliases.")


def norm(term: str) -> str:
    return re.sub(r"[\s_\-./:]+", " ", safe_text(term).lower()).strip()


def normalize_terms(data: dict) -> list[dict]:
    out, seen = [], set()
    for t in data.get("terms") if isinstance(data.get("terms"), list) else []:
        if not isinstance(t, dict):
            continue
        term = one_line(safe_text(t.get("term")), 80)
        key = norm(term)
        if not key or key in seen or not str(t.get("definition") or "").strip():
            continue
        seen.add(key)
        category = str(t.get("category") or "").lower()
        aliases = [one_line(safe_text(a), 80) for a in t.get("aliases") or [] if isinstance(a, str) and norm(a) and norm(a) != key]
        sources = []
        for v in t.get("sources") if isinstance(t.get("sources"), list) else []:
            try:
                sources.append(int(v))
            except (TypeError, ValueError):
                continue
        out.append({
            "term": term,
            "aliases": aliases[:8],
            "category": category if category in CATEGORIES else "other",
            "definition": safe_text(t.get("definition")).strip(),
            "context": safe_text(t.get("context") or "").strip(),
            "related": [one_line(safe_text(r), 80) for r in t.get("related") or [] if isinstance(r, str)][:8],
            "sources": sources,
        })
    return out


def _material(conn: sqlite3.Connection, project_path: str) -> tuple[str, list[int]]:
    """The distilled knowledge a glossary pass reads (never raw transcripts)."""
    parts: list[str] = []
    if project_path == GLOBAL:
        items = conn.execute(
            "SELECT id, kind, title, body, project_name FROM knowledge WHERE status = 'active' "
            "AND (scope = 'global' OR kind IN ('preference', 'reference')) ORDER BY id DESC LIMIT 300").fetchall()
        kb = conn.execute("SELECT * FROM project_kb WHERE project_path = ?", (GLOBAL,)).fetchone()
        tags = conn.execute("SELECT tags_json FROM sessions WHERE tags_json IS NOT NULL").fetchall()
        counts: dict[str, int] = {}
        for (t,) in tags:
            for tag in loads(t, []) or []:
                counts[tag] = counts.get(tag, 0) + 1
        top = sorted(counts.items(), key=lambda x: -x[1])[:80]
        if top:
            parts.append("<frequent_session_tags>\n" + ", ".join(f"{k} ({v})" for k, v in top) + "\n</frequent_session_tags>")
    else:
        items = conn.execute(
            "SELECT id, kind, title, body, project_name FROM knowledge WHERE status = 'active' AND project_path = ? "
            "ORDER BY id DESC LIMIT 250", (project_path,)).fetchall()
        kb = conn.execute("SELECT * FROM project_kb WHERE project_path = ?", (project_path,)).fetchone()
        sessions = conn.execute(
            "SELECT title, tags_json FROM sessions WHERE project_path = ? AND source != 'history' ORDER BY started_at DESC LIMIT 60",
            (project_path,)).fetchall()
        if sessions:
            parts.append("<session_titles>\n" + "\n".join(
                f"- {s['title']} [{', '.join(loads(s['tags_json'], []) or [])}]" for s in sessions) + "\n</session_titles>")
    if kb:
        kb = dict(kb)
        lines = [kb.get("overview") or ""]
        for sec in kb_sections(kb):
            lines.append(f"## {sec.get('title')}")
            lines += [f"- {i.get('text')}" for i in sec.get("items") or []]
        parts.insert(0, "<knowledge_base>\n" + truncate("\n".join(lines), 15000) + "\n</knowledge_base>")
    parts.append("<knowledge_items>\n" + "\n".join(json.dumps(
        {"id": k["id"], "kind": k["kind"], "project": k["project_name"], "title": k["title"],
         "body": truncate(k["body"] or "", 500)}, ensure_ascii=False) for k in items) + "\n</knowledge_items>")
    return "\n\n".join(parts), [k["id"] for k in items]


def build_glossary(conn: sqlite3.Connection, cfg: Config, project_path: str, runner: Runner | None = None) -> int:
    """(Re)build one project's glossary contribution (or the cross-project one for GLOBAL). Returns #terms."""
    material, ids = _material(conn, project_path)
    return _store(conn, project_path, *_generate(cfg, project_path, material, ids, runner or make_runner(cfg)))


def build_glossaries(cfg: Config, paths: list[str], *, concurrency: int = 3, progress=None) -> dict[str, int | str]:
    """Build several glossaries: Claude calls run in parallel, database writes stay serialized (one connection)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .db import connect

    conn = connect(cfg.db_path)
    runner = make_runner(cfg)
    results: dict[str, int | str] = {}
    try:
        inputs = {}
        for path in paths:
            material, ids = _material(conn, path)
            if ids:
                inputs[path] = (material, ids)
            else:
                results[path] = "no knowledge"
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {pool.submit(_generate, cfg, p, m, i, runner): p for p, (m, i) in inputs.items()}
            for fut in as_completed(futures):
                path = futures[fut]
                try:
                    results[path] = _store(conn, path, *fut.result())
                except Exception as exc:
                    conn.rollback()
                    results[path] = f"failed: {exc}"
                if progress:
                    progress(f"glossary {len(results)}/{len(paths)}: {path.rsplit('/', 1)[-1]} -> {results[path]}")
    finally:
        conn.rollback()
        conn.close()
    return results


def _generate(cfg: Config, project_path: str, material: str, ids: list[int], runner: Runner):
    if not ids:
        raise ValueError(f"no knowledge to build a glossary from for {project_path}")
    is_global = project_path == GLOBAL
    header = "" if is_global else f"<project path=\"{project_path}\" />\n\n"
    res = runner.run(header + material + "\n\nWrite the glossary.", GLOSSARY_SCHEMA,
                     system=written_in(cfg, GLOBAL_SYSTEM if is_global else PROJECT_SYSTEM, note=TERMS_JA),
                     model=cfg.synthesis.model)
    return normalize_terms(res.data), ids, res, len(material)


def _store(conn: sqlite3.Connection, project_path: str, terms: list[dict], ids: list[int], res, material_chars: int) -> int:
    valid = set(ids)
    try:
        conn.execute("DELETE FROM glossary_usage WHERE project_path = ?", (project_path,))
        for t in terms:
            term_id = _upsert_term(conn, t, source=project_path)
            conn.execute(
                "INSERT INTO glossary_usage(term_id, project_path, context, sources_json, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(term_id, project_path) DO UPDATE SET context = excluded.context, "
                "sources_json = excluded.sources_json, updated_at = excluded.updated_at",
                (term_id, project_path, t["context"], dumps([i for i in t["sources"] if i in valid]), utcnow_iso()),
            )
        conn.execute("DELETE FROM glossary WHERE id NOT IN (SELECT term_id FROM glossary_usage)")
        conn.execute(
            "INSERT INTO analyses(kind, target, started_at, finished_at, model, status, input_chars, chunks, cost_usd, duration_ms) "
            "VALUES ('glossary', ?, ?, ?, ?, 'done', ?, 1, ?, ?)",
            (project_path, utcnow_iso(), utcnow_iso(), res.model, material_chars, res.cost_usd, res.duration_ms))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    refresh_stats(conn, [norm(t["term"]) for t in terms])
    log.info("glossary for %s: %d terms ($%.3f)", project_path, len(terms), res.cost_usd)
    return len(terms)


def _upsert_term(conn: sqlite3.Connection, t: dict, *, source: str) -> int:
    key = norm(t["term"])
    row = conn.execute("SELECT * FROM glossary WHERE norm = ?", (key,)).fetchone()
    if row is None:  # the new term may be another term's alias (or vice versa)
        for cand in conn.execute("SELECT * FROM glossary").fetchall():
            names = {norm(a) for a in loads(cand["aliases_json"], []) or []}
            if key in names or names & {norm(a) for a in t["aliases"]}:
                row = cand
                break
    if row is None:
        cur = conn.execute(
            "INSERT INTO glossary(term, norm, aliases_json, category, definition, definition_source, related_json, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (t["term"], key, dumps(t["aliases"]), t["category"], t["definition"], source, dumps(t["related"]), utcnow_iso()))
        return cur.lastrowid
    aliases = list(dict.fromkeys([*(loads(row["aliases_json"], []) or []), *t["aliases"],
                                  *([t["term"]] if norm(t["term"]) != row["norm"] else [])]))
    related = list(dict.fromkeys([*(loads(row["related_json"], []) or []), *t["related"]]))[:12]
    # a cross-project definition wins; otherwise the latest pass refreshes it
    keep = row["definition_source"] == GLOBAL and source != GLOBAL
    conn.execute(
        "UPDATE glossary SET aliases_json = ?, related_json = ?, category = ?, definition = ?, definition_source = ?, "
        "updated_at = ? WHERE id = ?",
        (dumps(aliases[:10]), dumps(related), row["category"] if keep else t["category"],
         row["definition"] if keep else t["definition"], row["definition_source"] if keep else source, utcnow_iso(), row["id"]))
    return row["id"]


def refresh_stats(conn: sqlite3.Connection, keys: list[str] | None = None) -> None:
    """Where each term shows up in the transcripts (FTS): sessions, mentions, first/last seen, top sessions."""
    rows = conn.execute("SELECT id, term, norm, aliases_json FROM glossary" + (
        f" WHERE norm IN ({','.join('?' * len(keys))})" if keys else ""), keys or []).fetchall()
    for r in rows:
        names = [n for n in dict.fromkeys([r["term"], *(loads(r["aliases_json"], []) or [])]) if len(n) >= 3]
        stats = {"n_sessions": 0, "n_mentions": 0, "first_seen": None, "last_seen": None, "top": []}
        if names:
            expr = " OR ".join('"' + n.replace('"', '""') + '"' for n in names)
            try:
                hits = conn.execute(
                    "SELECT e.session_id, COUNT(*) n, MIN(e.ts) first, MAX(e.ts) last FROM events_fts "
                    "JOIN events e ON e.id = events_fts.rowid WHERE events_fts MATCH ? GROUP BY e.session_id ORDER BY n DESC",
                    (expr,)).fetchall()
            except sqlite3.OperationalError:
                hits = []
            if hits:
                stats = {
                    "n_sessions": len(hits), "n_mentions": sum(h["n"] for h in hits),
                    "first_seen": min(h["first"] for h in hits if h["first"]) if any(h["first"] for h in hits) else None,
                    "last_seen": max(h["last"] for h in hits if h["last"]) if any(h["last"] for h in hits) else None,
                    "top": [h["session_id"] for h in hits[:5]],
                }
        conn.execute(
            "UPDATE glossary SET n_sessions = ?, n_mentions = ?, first_seen = ?, last_seen = ?, top_sessions_json = ? WHERE id = ?",
            (stats["n_sessions"], stats["n_mentions"], stats["first_seen"], stats["last_seen"], dumps(stats["top"]), r["id"]))
    conn.commit()


def glossary_entries(conn: sqlite3.Connection, *, query: str | None = None, project: str | None = None,
                     category: str | None = None) -> list[dict]:
    where, params = [], []
    if project:
        where.append("g.id IN (SELECT term_id FROM glossary_usage WHERE project_path = ?)")
        params.append(project)
    if category:
        where.append("g.category = ?")
        params.append(category)
    if query and query.strip():
        like = f"%{query.strip()}%"
        where.append("(g.term LIKE ? OR g.aliases_json LIKE ? OR g.definition LIKE ?)")
        params += [like, like, like]
    sql = "SELECT g.* FROM glossary g" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY g.term COLLATE NOCASE"
    rows = [dict(r) for r in conn.execute(sql, params)]
    if not rows:
        return []
    names = {r[0]: r[1] for r in conn.execute("SELECT DISTINCT project_path, project_name FROM sessions")}
    usage: dict[int, list] = {}
    for u in conn.execute("SELECT * FROM glossary_usage").fetchall():
        usage.setdefault(u["term_id"], []).append({
            "project_path": u["project_path"],
            "project_name": tr("all projects") if u["project_path"] == GLOBAL else names.get(u["project_path"], u["project_path"]),
            "context": u["context"], "sources": loads(u["sources_json"], []) or []})
    titles = {r[0]: r[1] for r in conn.execute("SELECT id, title FROM sessions")}
    for r in rows:
        r["aliases"] = loads(r.pop("aliases_json", None), []) or []
        r["related"] = loads(r.pop("related_json", None), []) or []
        r["usage"] = usage.get(r["id"], [])
        r["top_sessions"] = [{"id": s, "title": titles.get(s)} for s in loads(r.pop("top_sessions_json", None), []) or []]
    return rows


def lookup(conn: sqlite3.Connection, term: str) -> dict | None:
    key = norm(term)
    for r in glossary_entries(conn):
        if r["norm"] == key or key in {norm(a) for a in r["aliases"]}:
            return r
    matches = glossary_entries(conn, query=term)
    return matches[0] if matches else None


# ------------------------------------------------------------------ themes: sub-groups inside big categories
THEME_MIN_TERMS = 25  # smaller categories are browsable as they are
THEMES_SYSTEM = """\
You organize one category of a developer's vocabulary (terms from their coding-agent sessions, each with a short \
definition) into themes, so that the category can be browsed as a mindmap instead of one long list.
Make 4 to 10 themes. Name each by subject or system in 1 to 4 words a developer recognizes at a glance (for \
example "Auth & identity", "Quote pipeline", "Test tooling"), using the language most of the terms use, and \
describe it in one short sentence. Avoid vague buckets such as "Misc" or "General"; use "Other" only for the few \
terms that fit nowhere. Put every term id in exactly one theme."""
THEMES_JA = ("Name and describe the themes in Japanese (system and product names stay as they are), and call the theme "
             "for terms that fit nowhere 「その他」 instead of \"Other\".")
OTHER_THEME = {"en": ("Other", "Terms that fit none of the other themes."),  # leftovers, in the language Interlatch writes in
               "ja": ("その他", "ほかのどのテーマにも入らない用語。")}

THEMES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["themes"],
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "description", "term_ids"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "term_ids": {"type": "array", "items": {"type": "integer"}},
                },
            },
        }
    },
}


def _category_terms(conn: sqlite3.Connection, category: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, term, aliases_json, definition FROM glossary WHERE category = ? ORDER BY id",
                        (category,)).fetchall()


def _fingerprint(rows) -> str:
    import hashlib

    return hashlib.sha1(",".join(str(r["id"]) for r in rows).encode()).hexdigest()[:16]


def themes_due(conn: sqlite3.Connection, *, force: bool = False, min_terms: int = THEME_MIN_TERMS) -> list[str]:
    """Categories big enough for themes whose term set changed since they were last grouped."""
    from .db import kv_get

    due = []
    for (cat,) in conn.execute("SELECT category FROM glossary WHERE category IS NOT NULL GROUP BY category "
                               "HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC", (min_terms,)):
        if force or kv_get(conn, f"themes:{cat}") != _fingerprint(_category_terms(conn, cat)):
            due.append(cat)
    return due


def _theme_material(rows) -> str:
    return "<terms>\n" + "\n".join(json.dumps({
        "id": r["id"], "term": r["term"], "aliases": (loads(r["aliases_json"], []) or [])[:3],
        "definition": truncate(r["definition"] or "", 200)}, ensure_ascii=False) for r in rows) + "\n</terms>"


def _generate_themes(cfg: Config, category: str, rows, runner: Runner):
    prompt = f"<category>{category}</category>\n\n{_theme_material(rows)}\n\nGroup the terms into themes."
    return runner.run(prompt, THEMES_SCHEMA, system=written_in(cfg, THEMES_SYSTEM, note=THEMES_JA), model=cfg.synthesis.model)


def _store_themes(conn: sqlite3.Connection, category: str, rows, res, other: tuple[str, str] = OTHER_THEME["en"]) -> int:
    """Validate Claude's grouping (unknown ids dropped, every term placed once, leftovers in `other`) and store it."""
    from .db import kv_set

    valid = {r["id"] for r in rows}
    placed: dict[int, str] = {}
    themes: dict[str, str] = {}
    for t in res.data.get("themes") or []:
        if not isinstance(t, dict):
            continue
        name = one_line(str(t.get("name") or ""), 60).strip()
        if not name:
            continue
        ids = [i for i in (t.get("term_ids") or []) if isinstance(i, int) and i in valid and i not in placed]
        if not ids:
            continue
        themes.setdefault(name, one_line(str(t.get("description") or ""), 240))
        for i in ids:
            placed[i] = name
    for i in valid - placed.keys():
        placed[i] = other[0]
        themes.setdefault(other[0], other[1])
    try:
        conn.execute("DELETE FROM glossary_themes WHERE category = ?", (category,))
        now = utcnow_iso()
        for name, desc in themes.items():
            n = sum(1 for v in placed.values() if v == name)
            conn.execute("INSERT INTO glossary_themes(category, name, description, n_terms, updated_at) VALUES (?,?,?,?,?)",
                         (category, name, desc, n, now))
        conn.executemany("UPDATE glossary SET theme = ? WHERE id = ?", [(v, k) for k, v in placed.items()])
        kv_set(conn, f"themes:{category}", _fingerprint(rows))
        conn.execute(
            "INSERT INTO analyses(kind, target, started_at, finished_at, model, status, input_chars, chunks, cost_usd, duration_ms) "
            "VALUES ('themes', ?, ?, ?, ?, 'done', ?, 1, ?, ?)",
            (category, now, now, res.model, len(_theme_material(rows)), res.cost_usd, res.duration_ms))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    log.info("themes for %s: %d themes over %d terms ($%.3f)", category, len(themes), len(valid), res.cost_usd)
    return len(themes)


def build_themes(cfg: Config, categories: list[str] | None = None, *, force: bool = False, concurrency: int = 3,
                 progress=None) -> dict[str, int | str]:
    """Group big glossary categories into themes, one Claude call per category (only those that changed)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .db import connect

    conn = connect(cfg.db_path)
    runner = make_runner(cfg)
    results: dict[str, int | str] = {}
    try:
        due = categories if categories is not None else themes_due(conn, force=force)
        inputs = {c: _category_terms(conn, c) for c in due}
        inputs = {c: rows for c, rows in inputs.items() if rows}
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {pool.submit(_generate_themes, cfg, c, rows, runner): c for c, rows in inputs.items()}
            for fut in as_completed(futures):
                cat = futures[fut]
                try:
                    results[cat] = _store_themes(conn, cat, inputs[cat], fut.result(),
                                                 OTHER_THEME.get(cfg.analysis.language, OTHER_THEME["en"]))
                except Exception as exc:
                    conn.rollback()
                    results[cat] = f"failed: {exc}"
                if progress:
                    progress(f"themes {len(results)}/{len(inputs)}: {cat} -> {results[cat]}")
    finally:
        conn.rollback()
        conn.close()
    return results

def map_data(conn: sqlite3.Connection) -> dict:
    """Everything the dashboard's Map draws: projects, glossary terms with the projects that use them, and the
    knowledge items each term was distilled from. The browser builds the tree from this."""
    from .views import project_labels

    labels = project_labels(conn)
    kinds: dict[str, dict] = {}
    for r in conn.execute("SELECT project_path, kind, COUNT(*) n FROM knowledge WHERE status = 'active' GROUP BY 1, 2"):
        kinds.setdefault(r["project_path"] or "", {})[r["kind"]] = r["n"]
    projects = [
        {"path": r["project_path"], "label": labels.get(r["project_path"], r["project_name"]), "sessions": r["n"],
         "active_s": r["active_s"] or 0, "last": r["last"], "knowledge": kinds.get(r["project_path"], {})}
        for r in conn.execute(
            "SELECT project_path, project_name, COUNT(*) n, SUM(active_s) active_s, MAX(started_at) last FROM sessions "
            "WHERE project_path IN (SELECT project_path FROM glossary_usage) GROUP BY project_path ORDER BY n DESC")
    ]
    if conn.execute("SELECT 1 FROM glossary_usage WHERE project_path = ? LIMIT 1", (GLOBAL,)).fetchone():
        projects.insert(0, {"path": GLOBAL, "label": tr("Everywhere"), "sessions": None, "active_s": None, "last": None,
                            "knowledge": kinds.get(GLOBAL, {})})
    usage: dict[int, list] = {}
    for u in conn.execute("SELECT term_id, project_path, context, sources_json FROM glossary_usage"):
        usage.setdefault(u["term_id"], []).append(
            {"path": u["project_path"], "context": u["context"], "sources": loads(u["sources_json"], []) or []})
    sessions = {r["id"]: {"id": r["id"], "title": r["title"], "agent": r["agent"] or "claude", "started_at": r["started_at"],
                          "project_path": r["project_path"]}
                for r in conn.execute("SELECT id, title, agent, started_at, project_path FROM sessions")}
    terms, wanted = [], set()
    for r in conn.execute("SELECT id, term, category, definition, aliases_json, related_json, n_sessions, n_mentions, "
                          "first_seen, last_seen, theme, top_sessions_json FROM glossary"):
        uses = usage.get(r["id"], [])
        sources = sorted({s for u in uses for s in u["sources"]})
        wanted.update(sources)
        terms.append({"id": r["id"], "term": r["term"], "category": r["category"] or "other",
                      "definition": r["definition"], "aliases": loads(r["aliases_json"], []) or [],
                      "related": loads(r["related_json"], []) or [], "n_sessions": r["n_sessions"] or 0,
                      "n_mentions": r["n_mentions"] or 0, "first_seen": r["first_seen"], "last_seen": r["last_seen"],
                      "theme": r["theme"], "projects": [{"path": u["path"], "context": u["context"]} for u in uses],
                      "knowledge": sources,
                      "sessions": [sessions[i] for i in loads(r["top_sessions_json"], []) or [] if i in sessions]})
    knowledge = {}
    ids = sorted(wanted)
    for i in range(0, len(ids), 500):  # SQLite caps bound parameters per statement
        chunk = ids[i:i + 500]
        for k in conn.execute(f"SELECT id, kind, title, body, session_id, project_path, agent FROM knowledge "
                              f"WHERE status = 'active' AND id IN ({','.join('?' * len(chunk))})", chunk):
            knowledge[k["id"]] = {**dict(k), "body": truncate(k["body"] or "", 600), "agent": k["agent"] or "claude"}
    for t in terms:  # drop sources that were dismissed or superseded since; agents = whose sessions taught it
        t["knowledge"] = [i for i in t["knowledge"] if i in knowledge]
        t["agents"] = sorted({knowledge[i]["agent"] for i in t["knowledge"]} | {s["agent"] for s in t["sessions"]})
    themes: dict[str, list] = {}
    for r in conn.execute("SELECT category, name, description, n_terms FROM glossary_themes ORDER BY n_terms DESC"):
        themes.setdefault(r["category"], []).append({"name": r["name"], "description": r["description"], "n_terms": r["n_terms"]})
    return {"projects": projects, "terms": terms, "knowledge": knowledge, "themes": themes,
            "themes_due": themes_due(conn)}
