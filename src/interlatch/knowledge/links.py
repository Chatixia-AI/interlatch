"""Lesson links: which known things each lesson is about, found with no model call.

The things are the ones Interlatch already knows from evidence: glossary terms (by name, or an alias sessions used)
and Systems map parts (by label, or a resource's own name). A lesson is linked to each one its text names, on word
boundaries, only for terms its project uses (or the cross-project pass wrote) and parts of its project's systems. A
name in the title makes the lesson *about* it; one only in the body, a *mention*. Where one name sits inside a longer
one ("Ariba" in "Ariba API"), only the longer counts.

Links are derived and rebuilt whenever their inputs change (link_lessons): a lesson whose text changed (re-analyzing a
session writes its lessons again, often under the same ids), a project whose glossary was refreshed, or new rules
(RULES). They hold the name, not a term or part id, because terms are deleted when no project uses them and parts are
rebuilt with the map. Readers match on `norm` (and
`target`, the term or part label it stood for when linked). Checked by hand on lessons the rules were not tuned on, 89%
of title links named what the lesson is about.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3

from ..core.util import loads
from .glossary import norm
from .synthesize import GLOBAL

log = logging.getLogger("interlatch.links")

MIN_CHARS = 3  # shorter names match too much ("ui", "db")
RULES = "1"  # change it when the rules change: every lesson is linked again


def _boundary(text: str, name: str) -> bool:
    """`name` occurs in `text` (both normalized) not inside a longer ASCII word: "ttl" not in "throttle". Names in
    other scripts (Japanese) have no word spaces, so they match anywhere."""
    i = text.find(name)
    while i >= 0:
        before = text[i - 1] if i else " "
        after = text[i + len(name)] if i + len(name) < len(text) else " "
        inside = (name[0].isascii() and name[0].isalnum() and before.isascii() and before.isalnum()) or \
                 (name[-1].isascii() and name[-1].isalnum() and after.isascii() and after.isalnum())
        if not inside:
            return True
        i = text.find(name, i + 1)
    return False


# Systems map parts that are things in their own right (a deployed app, a database, an outside service); the others
# are named after the project's own folders ("backend", "app", "dashboard"), words most lessons of the project use
NAMED_PARTS = ("deployed", "store", "external", "service")


class Names:
    """The names a project's lessons can be linked to: norm -> (name as written, what it is: the glossary term or
    part label, basis)."""

    def __init__(self, conn: sqlite3.Connection, parts: dict[str, list[dict]] | None = None):
        # (norm, name, term, projects using the term, an alias): an alias links only in a project that uses the term,
        # since a cross-project term's aliases are loose ("Studio" for one product finds another)
        self.terms: list[tuple[str, str, str, set[str], bool]] = []
        uses: dict[int, set[str]] = {}
        for u in conn.execute("SELECT term_id, project_path FROM glossary_usage"):
            uses.setdefault(u[0], set()).add(u[1])
        for r in conn.execute("SELECT id, term, aliases_json, alias_seen_json FROM glossary"):
            seen = loads(r["alias_seen_json"], {}) or {}
            aliases = [a for a in loads(r["aliases_json"], []) or [] if seen.get(a) != 0]
            for i, name in enumerate([r["term"], *aliases]):
                if len(norm(name)) >= MIN_CHARS:
                    self.terms.append((norm(name), name, r["term"], uses.get(r["id"], set()), i > 0))
        self.parts = parts or {}

    def of(self, project_path: str | None) -> dict[str, tuple[str, str, str]]:
        own = own_name(project_path)  # every lesson of the project is about it
        out: dict[str, tuple[str, str, str]] = {}
        for p in self.parts.get(project_path or "", []):
            # a Terraform resource without a literal name is named after its HCL label ("app", "main"): not a name
            literal = p.get("name") if p.get("name") != (p.get("resource") or "").rsplit(".", 1)[-1] else None
            for name in (literal, p["label"] if p["kind"] in NAMED_PARTS else None):
                if name and len(norm(name)) >= MIN_CHARS and norm(name) != own:
                    out.setdefault(norm(name), (name, p["label"], "part"))
        for key, name, term, projects, alias in self.terms:
            if key != own and (project_path in projects or (GLOBAL in projects and not alias)):
                out.setdefault(key, (name, term, "term"))
        return out


def own_name(project_path: str | None) -> str:
    return norm((project_path or "").rstrip("/").rsplit("/", 1)[-1])


def system_parts(conn: sqlite3.Connection, cfg) -> dict[str, list[dict]]:
    """Systems map parts by project path (a part belongs to every project of its system). Builds the map: 1 to 3 s."""
    from ..projects import systems

    data = systems.build(conn, cfg)
    out: dict[str, list[dict]] = {}
    for s in data["systems"]:
        for path in s.get("project_paths") or [s.get("path")]:
            if path:
                out.setdefault(path, []).extend(s["parts"])
    return out


def _without(text: str, own: str) -> str:
    if len(own) < MIN_CHARS:
        return text
    return " | ".join(part for part in text.split(own))


def _written(raw: str, name: str) -> bool:
    """A name with punctuation is found only as written: "/status" is not the word "status", "llm.py" not "llm py"."""
    return name.isalnum() or " " in name or name.lower() in raw.lower()


def find(title: str, body: str | None, names: dict[str, tuple[str, str, str]], project_path: str | None = None) -> list[dict]:
    """The names a lesson's text holds: [{norm, name, target, basis, place}], title matches first. `target` is the
    glossary term or part label the name stands for. The project's own name is taken out of the text first, so a name
    inside it ("Databricks" in "databricks-sandbox") isn't found there."""
    own = own_name(project_path)
    found: dict[str, dict] = {}
    for place, raw in (("title", title), ("body", body or "")):
        text = _without(norm(raw), own)
        here = {key: {"norm": key, "name": name, "target": target, "basis": basis, "place": place}
                for key, (name, target, basis) in names.items()
                if key not in found and key in text and _boundary(text, key) and _written(raw, name)}
        # a name inside a longer one found here counts only when it also stands on its own here
        for key in list(here):
            longer = [k for k in here if k != key and key in k]
            if longer and text.count(key) <= sum(text.count(k) for k in longer):
                del here[key]
        found.update(here)
    return sorted(found.values(), key=lambda f: (f["place"] != "title", f["norm"]))


def _hash(r) -> str:
    return hashlib.sha1(f"{RULES}\0{r['project_path']}\0{r['title']}\0{r['body'] or ''}".encode()).hexdigest()[:16]


def link_lessons(conn: sqlite3.Connection, cfg, *, projects: list[str] | tuple = (), full: bool = False) -> int:
    """Link the active lessons not linked from their current text yet (new, re-analyzed or edited ones), every lesson
    of `projects` (their glossary changed), or every lesson (`full`); drop the links of lessons gone or no longer
    active. Builds the Systems map only when there is something to link. Returns how many lessons were linked."""
    rows = [r for r in conn.execute("SELECT id, project_path, title, body, links_hash FROM knowledge "
                                    "WHERE status = 'active' AND project_path IS NOT NULL")
            if full or r["project_path"] in projects or r["links_hash"] != _hash(r)]
    try:
        conn.execute("DELETE FROM knowledge_links WHERE knowledge_id NOT IN (SELECT id FROM knowledge WHERE status = 'active')")
        if rows:
            names = Names(conn, system_parts(conn, cfg))
            by_project: dict[str, dict] = {}
            ids = [r["id"] for r in rows]
            for i in range(0, len(ids), 500):  # SQLite caps bound parameters per statement
                chunk = ids[i:i + 500]
                conn.execute(f"DELETE FROM knowledge_links WHERE knowledge_id IN ({','.join('?' * len(chunk))})", chunk)
            for r in rows:
                of = by_project.get(r["project_path"])
                if of is None:
                    of = by_project[r["project_path"]] = names.of(r["project_path"])
                conn.executemany(
                    "INSERT OR IGNORE INTO knowledge_links(knowledge_id, norm, name, target, basis, place) VALUES (?,?,?,?,?,?)",
                    [(r["id"], f["norm"], f["name"], f["target"], f["basis"], f["place"])
                     for f in find(r["title"], r["body"], of, r["project_path"])])
            conn.executemany("UPDATE knowledge SET links_hash = ? WHERE id = ?", [(_hash(r), r["id"]) for r in rows])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if rows:
        log.info("linked %d lessons", len(rows))
    return len(rows)


def lessons_about(conn: sqlite3.Connection, names: list[str], *, place: str | None = "title") -> list[int]:
    """Ids of the active lessons linked to any of `names` (any spelling, or any name of the same glossary term), in the
    title only by default."""
    from .glossary import search_names

    keys = sorted({norm(n) for n in names if norm(n)})
    if not keys:
        return []
    known = search_names(conn)
    targets = sorted({known[k]["term"] for k in keys if k in known} | set(names))
    sql = (f"SELECT DISTINCT l.knowledge_id FROM knowledge_links l JOIN knowledge k ON k.id = l.knowledge_id "
           f"WHERE k.status = 'active' AND (l.norm IN ({','.join('?' * len(keys))}) "
           f"OR l.target IN ({','.join('?' * len(targets))}))")
    params = [*keys, *targets]
    if place:
        sql += " AND l.place = ?"
        params.append(place)
    return [r[0] for r in conn.execute(sql, params)]
