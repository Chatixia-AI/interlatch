"""A group's knowledge graph: the glossary terms its projects use, and the links between them that a model has checked.

The links come from evidence only: each glossary term's `related` list, resolved to other terms the group uses. Those
lists are noisy (a term picks up whatever its sessions also talked about), so nothing is drawn until someone asks:
`analyze` sends the candidate links to the analyzer, which keeps or drops each one and names the relation. It never
adds a link. The result is stored per group (kv `group_graph:<id>`) and read back as it was; a glossary that changed
since leaves new candidates unchecked until the next run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time

from ..analysis.language import written_in
from ..analysis.runners.base import Runner
from ..analysis.runners.factory import make_runner
from ..core.config import Config
from ..core.util import dumps, loads, one_line, truncate, utcnow_iso
from .glossary import map_data, norm

log = logging.getLogger("interlatch.group_graph")

HIDDEN = {"file", "command"}  # paths and commands link to everything they were used with
MAX_LINKS = 600  # the most discussed pairs first; the rest stay unchecked
LINKS_PER_CALL = 150

SYSTEM = """\
You check the links between terms in a developer's glossary, for one group of related software projects. Each \
candidate link joins two terms that the glossary listed as related, often only because the same sessions talked \
about both.

For each link decide whether the two terms really relate in this work: one calls, uses, stores data in, runs on, \
deploys to, configures, contains or is part of, replaces, or is an alternative to the other, or they are two names \
for closely tied things. Drop links where they were merely discussed together. When you keep a link, name the \
relation in 1 to 4 words read from the term `from` to the other (for example "calls", "stores data in", "runs on", \
"part of", "replaced by"), and set `from` to whichever term makes the label read right. Judge only the links given; \
never add one."""
SYSTEM_JA = "Write each label in Japanese (system and product names stay as they are)."

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["links"],
    "properties": {
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "keep", "from", "label"],
                "properties": {
                    "id": {"type": "integer", "description": "the candidate link's id"},
                    "keep": {"type": "boolean"},
                    "from": {"type": "integer", "description": "the term id the label reads from"},
                    "label": {"type": "string", "description": "the relation, 1-4 words; empty when dropped"},
                },
            },
        }
    },
}


def candidates(conn: sqlite3.Connection, paths: list[str]) -> tuple[dict[int, dict], list[tuple[int, int]]]:
    """The group's terms (files and commands left out) by id, and each pair of them one term lists as related,
    most discussed first, at most MAX_LINKS."""
    inside = set(paths)
    used: dict[int, set[str]] = {}
    for r in conn.execute("SELECT term_id, project_path FROM glossary_usage"):
        if r["project_path"] in inside:
            used.setdefault(r["term_id"], set()).add(r["project_path"])
    terms: dict[int, dict] = {}
    for r in conn.execute("SELECT id, term, category, definition, aliases_json, related_json, n_sessions FROM glossary"):
        if r["id"] in used and (r["category"] or "other") not in HIDDEN:
            terms[r["id"]] = {"id": r["id"], "term": r["term"], "category": r["category"] or "other",
                              "definition": r["definition"] or "", "aliases": loads(r["aliases_json"], []) or [],
                              "related": loads(r["related_json"], []) or [], "n_sessions": r["n_sessions"] or 0,
                              "projects": sorted(used[r["id"]])}
    names: dict[str, int] = {}
    for t in sorted(terms.values(), key=lambda t: -t["n_sessions"]):  # a shared spelling goes to the most discussed
        for name in [t["term"], *t["aliases"]]:
            names.setdefault(norm(str(name)), t["id"])
    pairs = set()
    for t in terms.values():
        for name in t["related"]:
            other = names.get(norm(str(name)))
            if other is not None and other != t["id"]:
                pairs.add((min(t["id"], other), max(t["id"], other)))
    ranked = sorted(pairs, key=lambda p: (-(terms[p[0]]["n_sessions"] + terms[p[1]]["n_sessions"]), p))
    return terms, ranked[:MAX_LINKS]


def _fingerprint(pairs) -> str:
    return hashlib.sha1(",".join(f"{a}-{b}" for a, b in sorted(pairs)).encode()).hexdigest()[:16]


def _material(terms: dict[int, dict], pairs: list[tuple[int, int]], labels: dict[str, str]) -> str:
    ids = sorted({i for p in pairs for i in p})
    lines = [json.dumps({"id": i, "term": terms[i]["term"], "category": terms[i]["category"],
                         "definition": truncate(terms[i]["definition"], 200),
                         "projects": [labels.get(p, p.rstrip("/").split("/")[-1]) for p in terms[i]["projects"]]},
                        ensure_ascii=False) for i in ids]
    links = [json.dumps({"id": n, "a": a, "b": b}) for n, (a, b) in enumerate(pairs)]
    return "<terms>\n" + "\n".join(lines) + "\n</terms>\n\n<links>\n" + "\n".join(links) + "\n</links>"


def _check(cfg: Config, terms, pairs, labels, runner: Runner):
    prompt = _material(terms, pairs, labels) + "\n\nCheck each link."
    return runner.run(prompt, SCHEMA, system=written_in(cfg, SYSTEM, note=SYSTEM_JA), model=cfg.synthesis.model)


def _kept(res, pairs: list[tuple[int, int]]) -> list[dict]:
    """The links the reply keeps, each once: unknown ids and a `from` outside its pair are not trusted."""
    out, seen = [], set()
    for x in (res.data or {}).get("links") or []:
        if not isinstance(x, dict) or not isinstance(x.get("id"), int) or not 0 <= x["id"] < len(pairs):
            continue
        if x["id"] in seen or x.get("keep") is not True:
            continue
        seen.add(x["id"])
        a, b = pairs[x["id"]]
        src = x.get("from") if x.get("from") in (a, b) else a
        out.append({"from": src, "to": b if src == a else a, "label": one_line(str(x.get("label") or ""), 40).strip()})
    return out


def analyze(cfg: Config, conn: sqlite3.Connection, group_id: int, paths: list[str], *, runner: Runner | None = None,
            concurrency: int = 3, progress=None) -> dict:
    """Check the group's candidate links with the analyzer and store what it keeps. Returns the stored record."""
    from concurrent.futures import ThreadPoolExecutor

    from ..projects.labels import project_labels
    from ..storage.kv import kv_set

    terms, pairs = candidates(conn, paths)
    labels = project_labels(conn)
    runner = runner or (make_runner(cfg) if pairs else None)  # no links to check: no model call
    chunks = [pairs[i:i + LINKS_PER_CALL] for i in range(0, len(pairs), LINKS_PER_CALL)]
    links, cost, model, chars, started, t0 = [], 0.0, None, 0, utcnow_iso(), time.monotonic()
    if progress:
        progress(f"checking {len(pairs)} links between {len(terms)} terms")
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for chunk, res in zip(chunks, pool.map(lambda ch: _check(cfg, terms, ch, labels, runner), chunks), strict=True):
            links += _kept(res, chunk)
            cost += res.cost_usd or 0
            model = model or res.model
            chars += len(_material(terms, chunk, labels))
    record = {"analyzed_at": utcnow_iso(), "model": model, "cost_usd": round(cost, 4), "checked": [list(p) for p in pairs],
              "fingerprint": _fingerprint(pairs), "links": links}
    try:
        kv_set(conn, f"group_graph:{group_id}", dumps(record))
        conn.execute(
            "INSERT INTO analyses(kind, target, started_at, finished_at, model, status, input_chars, chunks, cost_usd, duration_ms) "
            "VALUES ('group_graph', ?, ?, ?, ?, 'done', ?, ?, ?, ?)",
            (str(group_id), started, record["analyzed_at"], model, chars, len(chunks), cost,
             round((time.monotonic() - t0) * 1000)))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    log.info("group %s graph: kept %d of %d links ($%.3f)", group_id, len(links), len(pairs), cost)
    return record


def graph(conn: sqlite3.Connection, group_id: int, paths: list[str]) -> dict:
    """What the group's Mindmap and Connections draw, once analyzed: the Map's data for the group's projects
    (glossary.map_data) and the checked links between terms it still has. Before that, only what an analysis would
    check. `unchecked`: candidate links the glossary has gained since."""
    from ..storage.kv import kv_get

    terms, pairs = candidates(conn, paths)
    record = loads(kv_get(conn, f"group_graph:{group_id}"), None)
    if not isinstance(record, dict):
        return {"analysis": None, "terms": len(terms), "candidates": len(pairs), "calls": -(-len(pairs) // LINKS_PER_CALL)}
    data = map_data(conn, paths)
    have = {t["id"] for t in data["terms"]}
    checked = {tuple(p) for p in record.get("checked") or []}
    data["links"] = [x for x in record.get("links") or [] if x.get("from") in have and x.get("to") in have]
    data["analysis"] = {"analyzed_at": record.get("analyzed_at"), "model": record.get("model"),
                        "cost_usd": record.get("cost_usd"), "checked": len(checked),
                        "unchecked": sum(1 for p in pairs if p not in checked), "candidates": len(pairs),
                        "calls": -(-len(pairs) // LINKS_PER_CALL)}
    return data
