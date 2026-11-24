"""Glossary: the vocabulary of the developer's work, distilled from sessions (terms, aliases, definitions, usage)."""

from __future__ import annotations

import json
import logging
import re
import sqlite3

from ..core.config import Config
from ..core.i18n import tr
from ..analysis.runners.base import Runner
from ..analysis.runners.factory import make_runner
from ..analysis.language import written_in
from .synthesize import GLOBAL, kb_sections
from ..core.util import dumps, loads, one_line, safe_text, truncate, utcnow_iso

log = logging.getLogger("interlatch.glossary")

CATEGORIES = ["system", "service", "component", "tool", "library", "platform", "concept", "acronym", "file",
              "command", "data", "domain", "organization", "other"]

MAX_ALIASES = 10  # kept per term

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
# a pass that reads only the knowledge items added since the last one (build_glossaries)
UPDATE_NOTE = """\
This glossary already exists: <existing_terms> lists its terms, and the knowledge items given are only the ones added \
since it was written. Return the terms these items mention: new ones, and existing ones, spelled exactly as listed, \
whose definition or use here they add to or change. Leave out every other existing term: it stays as it is."""
UPDATE_LIMIT = 200  # more new knowledge items than this since the last pass: rebuild from all of it
SEEN_KEY = "glossary_read:"  # kv, + project path: the newest knowledge item id the project's glossary has read
# with [analysis] language = "ja", besides analysis.language.JAPANESE
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


_GLOBAL_ITEMS = "status = 'active' AND (scope = 'global' OR kind IN ('preference', 'reference'))"
_PROJECT_ITEMS = "status = 'active' AND project_path = ?"


def _since(conn: sqlite3.Connection, project_path: str) -> int | None:
    """The newest knowledge item id the project's glossary last read, or None when it needs a pass over all of its
    knowledge: never built (or built before this was kept), none of its terms left, or more new items than
    UPDATE_LIMIT."""
    from ..storage.kv import kv_get

    seen = kv_get(conn, SEEN_KEY + project_path)
    if seen is None or not conn.execute("SELECT 1 FROM glossary_usage WHERE project_path = ? LIMIT 1",
                                        (project_path,)).fetchone():
        return None
    where, params = (_GLOBAL_ITEMS, []) if project_path == GLOBAL else (_PROJECT_ITEMS, [project_path])
    new = conn.execute(f"SELECT COUNT(*) FROM knowledge WHERE {where} AND id > ?", [*params, int(seen)]).fetchone()[0]
    return None if new > UPDATE_LIMIT else int(seen)


def _known_terms(conn: sqlite3.Connection, project_path: str) -> str:
    rows = conn.execute("SELECT g.term, g.category, g.aliases_json FROM glossary g JOIN glossary_usage u ON u.term_id = g.id "
                        "WHERE u.project_path = ? ORDER BY g.term COLLATE NOCASE", (project_path,)).fetchall()
    return "<existing_terms>\n" + "\n".join(json.dumps(
        {"term": r["term"], "category": r["category"], "aliases": (loads(r["aliases_json"], []) or [])[:3]},
        ensure_ascii=False) for r in rows) + "\n</existing_terms>"


def _material(conn: sqlite3.Connection, project_path: str, since: int | None = None) -> tuple[str, list[int]]:
    """The distilled knowledge a glossary pass reads (never raw transcripts). `since`: only the knowledge items newer
    than that id, with the glossary's existing terms instead of the knowledge base, session titles and tags."""
    parts: list[str] = []
    if since is not None:
        where, params = (_GLOBAL_ITEMS, []) if project_path == GLOBAL else (_PROJECT_ITEMS, [project_path])
        items = conn.execute(f"SELECT id, kind, title, body, project_name FROM knowledge WHERE {where} AND id > ? "
                             "ORDER BY id DESC", [*params, since]).fetchall()
        kb = None
        parts.append(_known_terms(conn, project_path))
    elif project_path == GLOBAL:
        items = conn.execute(
            f"SELECT id, kind, title, body, project_name FROM knowledge WHERE {_GLOBAL_ITEMS} ORDER BY id DESC LIMIT 300"
        ).fetchall()
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
            f"SELECT id, kind, title, body, project_name FROM knowledge WHERE {_PROJECT_ITEMS} ORDER BY id DESC LIMIT 250",
            (project_path,)).fetchall()
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


def build_glossary(conn: sqlite3.Connection, cfg: Config, project_path: str, runner: Runner | None = None, *,
                   full: bool = False) -> int:
    """Bring one project's glossary contribution (or the cross-project one for GLOBAL) up to date with its knowledge:
    from the items added since the last pass, or from all of it (`full`, or see _since). Returns #terms the pass
    wrote, 0 when nothing was new."""
    since = None if full else _since(conn, project_path)
    material, ids = _material(conn, project_path, since)
    if not ids and since is not None:
        return 0
    return _store(conn, project_path, *_generate(cfg, project_path, material, ids, runner or make_runner(cfg), since))


def build_glossaries(cfg: Config, paths: list[str], *, concurrency: int = 3, progress=None,
                     full: bool = False) -> dict[str, int | str]:
    """Build several glossaries (see build_glossary): model calls run in parallel, database writes stay serialized
    (one connection). Returns #terms per path, or "up to date", "no knowledge" or "failed: …"."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from ..storage.sqlite import connect

    conn = connect(cfg.db_path)
    runner = make_runner(cfg)
    results: dict[str, int | str] = {}
    try:
        inputs = {}
        for path in paths:
            since = None if full else _since(conn, path)
            material, ids = _material(conn, path, since)
            if ids:
                inputs[path] = (material, ids, since)
            else:
                results[path] = "no knowledge" if since is None else "up to date"
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {pool.submit(_generate, cfg, p, m, i, runner, s): p for p, (m, i, s) in inputs.items()}
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


def _generate(cfg: Config, project_path: str, material: str, ids: list[int], runner: Runner, since: int | None = None):
    if not ids:
        raise ValueError(f"no knowledge to build a glossary from for {project_path}")
    is_global = project_path == GLOBAL
    header = "" if is_global else f"<project path=\"{project_path}\" />\n\n"
    system = GLOBAL_SYSTEM if is_global else PROJECT_SYSTEM
    res = runner.run(header + material + ("\n\nUpdate the glossary." if since is not None else "\n\nWrite the glossary."),
                     GLOSSARY_SCHEMA, system=written_in(cfg, system + ("\n\n" + UPDATE_NOTE if since is not None else ""),
                                                        note=TERMS_JA),
                     model=cfg.synthesis.model)
    return normalize_terms(res.data), ids, res, len(material), since is not None


def _prune_usage(conn: sqlite3.Connection, project_path: str) -> None:
    """Keep only the active knowledge items among each use's sources, and drop a use none of whose sources is active
    any more (dismissed or superseded since). A use that never cited a source stays. No commit."""
    for u in conn.execute("SELECT term_id, sources_json FROM glossary_usage WHERE project_path = ?",
                          (project_path,)).fetchall():
        sources = loads(u["sources_json"], []) or []
        if not sources:
            continue
        live = [r[0] for r in conn.execute(f"SELECT id FROM knowledge WHERE status = 'active' AND id IN "
                                           f"({','.join('?' * len(sources))})", sources)]
        if not live:
            conn.execute("DELETE FROM glossary_usage WHERE term_id = ? AND project_path = ?", (u["term_id"], project_path))
        elif len(live) < len(sources):
            conn.execute("UPDATE glossary_usage SET sources_json = ? WHERE term_id = ? AND project_path = ?",
                         (dumps(sorted(live)), u["term_id"], project_path))


def _store(conn: sqlite3.Connection, project_path: str, terms: list[dict], ids: list[int], res, material_chars: int,
           update: bool = False) -> int:
    """Store a pass's terms and how this project uses them. A full pass replaces the project's uses; an `update` (from
    the items added since the last pass) adds to them, keeps the sources a use already had, and drops the uses whose
    sources all went inactive. Either way the terms no project uses any more go, and the pass's newest item id is
    kept for the next update."""
    from ..storage.kv import kv_set

    valid = set(ids)
    _measure_aliases(conn)  # a merge below reads this evidence
    try:
        if not update:
            conn.execute("DELETE FROM glossary_usage WHERE project_path = ?", (project_path,))
        for t in terms:
            term_id = _upsert_term(conn, t, source=project_path)
            sources = [i for i in t["sources"] if i in valid]
            old = conn.execute("SELECT sources_json FROM glossary_usage WHERE term_id = ? AND project_path = ?",
                               (term_id, project_path)).fetchone()
            if old:
                sources = sorted({*(loads(old[0], []) or []), *sources})
            conn.execute(
                "INSERT INTO glossary_usage(term_id, project_path, context, sources_json, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(term_id, project_path) DO UPDATE SET context = excluded.context, "
                "sources_json = excluded.sources_json, updated_at = excluded.updated_at",
                (term_id, project_path, t["context"], dumps(sources), utcnow_iso()),
            )
        if update:
            _prune_usage(conn, project_path)
        conn.execute("DELETE FROM glossary WHERE id NOT IN (SELECT term_id FROM glossary_usage)")
        _recount_themes(conn)
        kv_set(conn, SEEN_KEY + project_path, str(max(ids)))
        conn.execute(
            "INSERT INTO analyses(kind, target, started_at, finished_at, model, status, input_chars, chunks, cost_usd, duration_ms) "
            "VALUES ('glossary', ?, ?, ?, ?, 'done', ?, 1, ?, ?)",
            (project_path, utcnow_iso(), utcnow_iso(), res.model, material_chars, res.cost_usd, res.duration_ms))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    refresh_stats(conn, [norm(t["term"]) for t in terms])
    log.info("glossary %s for %s: %d terms from %d items ($%.3f)", "update" if update else "build", project_path,
             len(terms), len(ids), res.cost_usd)
    return len(terms)


def _upsert_term(conn: sqlite3.Connection, t: dict, *, source: str) -> int:
    key = norm(t["term"])
    row = conn.execute("SELECT * FROM glossary WHERE norm = ?", (key,)).fetchone()
    if row is None:
        # the new term may be another term's alias, or share an alias with it that sessions used. A shared alias only
        # the model wrote joins nothing: one invented name would fuse two terms
        mine = {norm(a) for a in t["aliases"]}
        for cand in conn.execute("SELECT * FROM glossary").fetchall():
            seen = loads(cand["alias_seen_json"], {}) or {}
            names: dict[str, int] = {}
            for a in loads(cand["aliases_json"], []) or []:
                names[norm(a)] = max(names.get(norm(a), 0), seen.get(a) or 0)
            if key in names or any(names.get(n) for n in mine):
                row = cand
                break
    if row is None:
        cur = conn.execute(
            "INSERT INTO glossary(term, norm, aliases_json, category, definition, definition_source, related_json, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (t["term"], key, dumps(t["aliases"]), t["category"], t["definition"], source, dumps(t["related"]), utcnow_iso()))
        return cur.lastrowid
    names: dict[str, str] = {}  # one spelling per name ("ACA", "aca"), the first one met
    for a in [*(loads(row["aliases_json"], []) or []), *t["aliases"], t["term"]]:
        if norm(a) and norm(a) != row["norm"]:
            names.setdefault(norm(a), a)
    aliases = list(names.values())
    if len(aliases) > MAX_ALIASES:  # too many: the names no session used go first
        seen = loads(row["alias_seen_json"], {}) or {}
        aliases = sorted(aliases, key=lambda a: seen.get(a) == 0)
    related = list(dict.fromkeys([*(loads(row["related_json"], []) or []), *t["related"]]))[:12]
    # a cross-project definition wins; otherwise the latest pass refreshes it
    keep = row["definition_source"] == GLOBAL and source != GLOBAL
    conn.execute(
        "UPDATE glossary SET aliases_json = ?, related_json = ?, category = ?, definition = ?, definition_source = ?, "
        "updated_at = ? WHERE id = ?",
        (dumps(aliases[:MAX_ALIASES]), dumps(related), row["category"] if keep else t["category"],
         row["definition"] if keep else t["definition"], row["definition_source"] if keep else source, utcnow_iso(), row["id"]))
    return row["id"]


def alias_evidence(conn: sqlite3.Connection, aliases: list[str]) -> dict[str, int]:
    """How many sessions' transcripts hold each alias, in any case and with its punctuation as written or as spaces
    ("write-ahead log", "write ahead log"): 0 means the model wrote a name no session used. Aliases under 3
    characters can't be searched (trigram FTS) and are left out: unknown, not unseen."""
    seen: dict[str, int] = {}
    for a in aliases:
        forms = [f for f in dict.fromkeys([a, norm(a)]) if len(f) >= 3]
        if not forms:
            continue
        try:
            seen[a] = conn.execute(
                "SELECT COUNT(DISTINCT e.session_id) FROM events_fts JOIN events e ON e.id = events_fts.rowid "
                "WHERE events_fts MATCH ?", (" OR ".join('"' + f.replace('"', '""') + '"' for f in forms),)).fetchone()[0]
        except sqlite3.OperationalError:
            continue
    return seen


def _measure_aliases(conn: sqlite3.Connection) -> None:
    """Alias evidence for the terms that have none yet: an archive from before it was recorded, or a term just added."""
    rows = conn.execute("SELECT id, aliases_json FROM glossary WHERE alias_seen_json IS NULL").fetchall()
    found = [(dumps(alias_evidence(conn, loads(r["aliases_json"], []) or [])), r["id"]) for r in rows]
    if found:  # read first, write at once: a big archive takes a while to search and others write meanwhile
        conn.executemany("UPDATE glossary SET alias_seen_json = ? WHERE id = ?", found)
        conn.commit()


def alias_split(e: dict) -> tuple[list[str], list[str]]:
    """A term's aliases as (the ones its sessions used or that are too short to look for, the ones only the model wrote)."""
    seen = e.get("alias_seen") or {}
    return [a for a in e["aliases"] if seen.get(a) != 0], [a for a in e["aliases"] if seen.get(a) == 0]


def also(e: dict) -> str:
    """A term's aliases in a sentence, the ones no session used told apart; "" when it has none."""
    used, unseen = alias_split(e)
    return "; ".join(p for p in ("also: " + ", ".join(used) if used else "",
                                 "added by the model, not seen in sessions: " + ", ".join(unseen) if unseen else "") if p)


def refresh_stats(conn: sqlite3.Connection, keys: list[str] | None = None) -> None:
    """Where each term shows up in the transcripts (FTS): sessions, mentions, first/last seen, top sessions, and the
    sessions that used each alias."""
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
            "UPDATE glossary SET n_sessions = ?, n_mentions = ?, first_seen = ?, last_seen = ?, top_sessions_json = ?, "
            "alias_seen_json = ? WHERE id = ?",
            (stats["n_sessions"], stats["n_mentions"], stats["first_seen"], stats["last_seen"], dumps(stats["top"]),
             dumps(alias_evidence(conn, loads(r["aliases_json"], []) or [])), r["id"]))
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
        r["alias_seen"] = loads(r.pop("alias_seen_json", None), {}) or {}
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


def search_names(conn: sqlite3.Connection) -> dict[str, dict]:
    """Every name a term goes by (its own and its aliases, normalized) -> the term, its definition and the names a
    search for it looks for: its own and the aliases sessions used (one only the model wrote finds nothing). A name
    two terms share goes to the one more sessions mention."""
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT term, definition, aliases_json, alias_seen_json FROM glossary "
                          "ORDER BY n_sessions DESC, id"):
        aliases = loads(r["aliases_json"], []) or []
        seen = loads(r["alias_seen_json"], {}) or {}
        entry = {"term": r["term"], "definition": r["definition"],
                 "names": [r["term"], *(a for a in aliases if seen.get(a) != 0)]}
        for name in [r["term"], *aliases]:
            if norm(name):
                out.setdefault(norm(name), entry)
    return out


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


REGROUP_SHARE = 0.5  # a category more than this new since it was grouped is regrouped from scratch
THEMES_PLACE_SYSTEM = """\
You file the new terms of one category of a developer's vocabulary (terms from their coding-agent sessions, each \
with a short definition) into the themes the category is already organized in, so its mindmap stays as it is.
Put each new term id into the existing theme that fits it best, under that theme's exact name. Make a new theme (1 \
to 4 words a developer recognizes at a glance, and one short sentence) only for a subject none of the existing \
themes covers that at least two new terms share. Leave out terms that fit nowhere."""


def _category_terms(conn: sqlite3.Connection, category: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, term, aliases_json, definition, theme FROM glossary WHERE category = ? ORDER BY id",
                        (category,)).fetchall()


def _themes_of(conn: sqlite3.Connection, category: str) -> dict[str, str]:
    """A category's themes, biggest first: name -> description."""
    return {r["name"]: r["description"] for r in conn.execute(
        "SELECT name, description FROM glossary_themes WHERE category = ? ORDER BY n_terms DESC, name", (category,))}


def _unplaced(rows, themes: dict[str, str]) -> list:
    """The terms in none of `themes`: new since the category was grouped, or moved in from another category."""
    return [r for r in rows if r["theme"] not in themes]


def _recount_themes(conn: sqlite3.Connection) -> None:
    """Theme sizes from the terms in them, and no theme left empty (its terms were dropped or moved). No commit."""
    conn.execute("UPDATE glossary_themes SET n_terms = (SELECT COUNT(*) FROM glossary g WHERE "
                 "g.category = glossary_themes.category AND g.theme = glossary_themes.name)")
    conn.execute("DELETE FROM glossary_themes WHERE n_terms = 0")


def themes_due(conn: sqlite3.Connection, *, force: bool = False, min_terms: int = THEME_MIN_TERMS) -> list[str]:
    """Categories big enough for themes that have terms in no theme yet (all of them with `force`). A term dropped
    from a category needs no model: the glossary build recounts its theme."""
    due = []
    for (cat,) in conn.execute("SELECT category FROM glossary WHERE category IS NOT NULL GROUP BY category "
                               "HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC", (min_terms,)):
        if force or _unplaced(_category_terms(conn, cat), _themes_of(conn, cat)):
            due.append(cat)
    return due


def _theme_material(rows, tag: str = "terms") -> str:
    return f"<{tag}>\n" + "\n".join(json.dumps({
        "id": r["id"], "term": r["term"], "aliases": (loads(r["aliases_json"], []) or [])[:3],
        "definition": truncate(r["definition"] or "", 200)}, ensure_ascii=False) for r in rows) + f"\n</{tag}>"


def _place_material(rows, themes: dict[str, str]) -> str:
    """The category's themes, each with a few of its terms, and the terms in none of them."""
    examples: dict[str, list[str]] = {}
    for r in rows:
        if r["theme"] in themes:
            examples.setdefault(r["theme"], []).append(r["term"])
    return ("<themes>\n" + "\n".join(json.dumps({"name": n, "description": d, "examples": examples.get(n, [])[:6]},
                                                 ensure_ascii=False) for n, d in themes.items())
            + "\n</themes>\n\n" + _theme_material(_unplaced(rows, themes), "new_terms"))


def _generate_themes(cfg: Config, category: str, rows, runner: Runner, themes: dict[str, str] | None = None):
    """One call: group the category's terms into themes, or with `themes` (its current ones) file the terms in none
    of them into those."""
    if themes:
        prompt = f"<category>{category}</category>\n\n{_place_material(rows, themes)}\n\nFile the new terms into the themes."
        system = THEMES_PLACE_SYSTEM
    else:
        prompt = f"<category>{category}</category>\n\n{_theme_material(rows)}\n\nGroup the terms into themes."
        system = THEMES_SYSTEM
    return runner.run(prompt, THEMES_SCHEMA, system=written_in(cfg, system, note=THEMES_JA), model=cfg.synthesis.model)


def _placements(res, valid: set[int], known: dict[str, str]) -> tuple[dict[int, str], dict[str, str]]:
    """The model's grouping of the `valid` term ids, checked: unknown ids and nameless themes dropped, each term in one
    theme, and a name that is a `known` theme's in any case is that theme. Returns term id -> theme name, and the new
    themes -> description."""
    names = {n.casefold(): n for n in known}
    placed: dict[int, str] = {}
    new: dict[str, str] = {}
    for t in res.data.get("themes") or []:
        if not isinstance(t, dict):
            continue
        name = one_line(str(t.get("name") or ""), 60).strip()
        ids = [i for i in (t.get("term_ids") or []) if isinstance(i, int) and i in valid and i not in placed]
        if not name or not ids:
            continue
        name = names.setdefault(name.casefold(), name)
        if name not in known:
            new.setdefault(name, one_line(str(t.get("description") or ""), 240))
        for i in ids:
            placed[i] = name
    return placed, new


def _store_themes(conn: sqlite3.Connection, category: str, rows, res, other: tuple[str, str] = OTHER_THEME["en"],
                  themes: dict[str, str] | None = None) -> int:
    """Store the model's grouping of a category, leftovers in `other`. With `themes` (the category's, as the model was
    shown them): only the terms in none of them move, into those or the new ones it made. Returns #themes."""
    themes = themes or {}
    todo = _unplaced(rows, themes)
    placed, new = _placements(res, {r["id"] for r in todo}, themes)
    for r in todo:
        if r["id"] not in placed:
            placed[r["id"]] = other[0]
            if other[0] not in themes:
                new.setdefault(other[0], other[1])
    material = _place_material(rows, themes) if themes else _theme_material(rows)
    try:
        now = utcnow_iso()
        if not themes:
            conn.execute("DELETE FROM glossary_themes WHERE category = ?", (category,))
        conn.executemany("INSERT OR IGNORE INTO glossary_themes(category, name, description, n_terms, updated_at) "
                         "VALUES (?,?,?,0,?)", [(category, n, d, now) for n, d in new.items()])
        conn.executemany("UPDATE glossary SET theme = ? WHERE id = ?", [(v, k) for k, v in placed.items()])
        _recount_themes(conn)
        conn.execute(
            "INSERT INTO analyses(kind, target, started_at, finished_at, model, status, input_chars, chunks, cost_usd, duration_ms) "
            "VALUES ('themes', ?, ?, ?, ?, 'done', ?, 1, ?, ?)",
            (category, now, now, res.model, len(material), res.cost_usd, res.duration_ms))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    n = conn.execute("SELECT COUNT(*) FROM glossary_themes WHERE category = ?", (category,)).fetchone()[0]
    log.info("themes for %s: %d themes, %d of %d terms placed ($%.3f)", category, n, len(todo), len(rows), res.cost_usd)
    return n


def build_themes(cfg: Config, categories: list[str] | None = None, *, force: bool = False, concurrency: int = 3,
                 progress=None) -> dict[str, int | str]:
    """Theme the big glossary categories, one model call per category that has terms in no theme: those terms are
    filed into its themes, or the whole category is grouped afresh when it has none yet, more than REGROUP_SHARE of it
    is new, or with `force`."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from ..storage.sqlite import connect

    conn = connect(cfg.db_path)
    runner = make_runner(cfg)
    results: dict[str, int | str] = {}
    try:
        due = categories if categories is not None else themes_due(conn, force=force)
        inputs = {}
        for c in due:
            rows = _category_terms(conn, c)
            themes = {} if force else _themes_of(conn, c)
            todo = _unplaced(rows, themes)
            if not todo:
                continue
            if len(todo) > REGROUP_SHARE * len(rows):
                themes = {}
            inputs[c] = (rows, themes)
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {pool.submit(_generate_themes, cfg, c, rows, runner, themes): c for c, (rows, themes) in inputs.items()}
            for fut in as_completed(futures):
                cat = futures[fut]
                rows, themes = inputs[cat]
                try:
                    results[cat] = _store_themes(conn, cat, rows, fut.result(),
                                                 OTHER_THEME.get(cfg.analysis.language, OTHER_THEME["en"]), themes)
                except Exception as exc:
                    conn.rollback()
                    results[cat] = f"failed: {exc}"
                if progress:
                    progress(f"themes {len(results)}/{len(inputs)}: {cat} -> {results[cat]}")
    finally:
        conn.rollback()
        conn.close()
    return results

def map_data(conn: sqlite3.Connection, paths: list[str] | None = None) -> dict:
    """Everything the dashboard's Map draws: projects, glossary terms with the projects that use them, and the
    knowledge items each term was distilled from. The browser builds the tree from this. paths: only these projects
    (a group's), each term with just its uses there and only the terms they use."""
    from ..projects.labels import project_labels

    inside = set(paths) if paths is not None else None
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
        if inside is None or r["project_path"] in inside
    ]
    if inside is None and conn.execute("SELECT 1 FROM glossary_usage WHERE project_path = ? LIMIT 1", (GLOBAL,)).fetchone():
        projects.insert(0, {"path": GLOBAL, "label": tr("Everywhere"), "sessions": None, "active_s": None, "last": None,
                            "knowledge": kinds.get(GLOBAL, {})})
    usage: dict[int, list] = {}
    for u in conn.execute("SELECT term_id, project_path, context, sources_json FROM glossary_usage"):
        if inside is not None and u["project_path"] not in inside:
            continue
        usage.setdefault(u["term_id"], []).append(
            {"path": u["project_path"], "context": u["context"], "sources": loads(u["sources_json"], []) or []})
    sessions = {r["id"]: {"id": r["id"], "title": r["title"], "agent": r["agent"] or "claude", "started_at": r["started_at"],
                          "project_path": r["project_path"]}
                for r in conn.execute("SELECT id, title, agent, started_at, project_path FROM sessions")}
    terms, wanted = [], set()
    for r in conn.execute("SELECT id, term, category, definition, aliases_json, related_json, n_sessions, n_mentions, "
                          "first_seen, last_seen, theme, top_sessions_json, alias_seen_json FROM glossary"):
        uses = usage.get(r["id"], [])
        if inside is not None and not uses:
            continue
        sources = sorted({s for u in uses for s in u["sources"]})
        wanted.update(sources)
        terms.append({"id": r["id"], "term": r["term"], "category": r["category"] or "other",
                      "definition": r["definition"], "aliases": loads(r["aliases_json"], []) or [],
                      "alias_seen": loads(r["alias_seen_json"], {}) or {},
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
