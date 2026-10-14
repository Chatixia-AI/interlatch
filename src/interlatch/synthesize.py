"""Cross-session synthesis: consolidate knowledge items into per-project knowledge bases and a global playbook."""

from __future__ import annotations

import copy
import json
import logging
import sqlite3

from . import ladder
from .diagram import DIAGRAM_SCHEMA, normalize_diagram, to_mermaid
from .config import Config
from .ladder import STAGE_ORDER_SQL, SUPERSEDE_REASONS, TRUSTED
from .llm import Runner, make_runner, written_in
from .util import dumps, local_str, loads, one_line, truncate, utcnow_iso

log = logging.getLogger("interlatch.synthesize")

GLOBAL = "__global__"
MAX_ITEMS = 250

KB_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tldr", "overview", "sections", "diagram", "superseded"],
    "properties": {
        "tldr": {"type": "array", "items": {"type": "string"},
                 "description": "Exactly 3 bullets, at most 14 words each: what matters most"},
        "overview": {
            "type": "string",
            "description": "At most 3 plain sentences: what this is, how it is built, and its current state",
        },
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "items"],
                "properties": {
                    "title": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["title", "text", "sources"],
                            "properties": {
                                "title": {"type": "string", "description": "3-8 words stating the rule or fact"},
                                "text": {"type": "string",
                                         "description": "The detail as markdown, at most 35 words; keep concrete identifiers"},
                                "sources": {"type": "array", "items": {"type": "integer"},
                                            "description": "ids of the knowledge items this bullet is based on"},
                            },
                        },
                    },
                },
            },
        },
        "diagram": DIAGRAM_SCHEMA,
        "superseded": {
            "type": "array",
            "description": "items that should leave the knowledge base, each with the item that replaces it",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "by", "reason"],
                "properties": {
                    "id": {"type": "integer", "description": "the item that leaves"},
                    "by": {"type": ["integer", "null"],
                           "description": "the item that replaces it (for a duplicate, the one that stays); null if none"},
                    "reason": {"type": "string", "enum": list(SUPERSEDE_REASONS)},
                },
            },
        },
    },
}

PROJECT_SYSTEM = """\
You maintain the knowledge base of one software project, distilled from many Claude Code sessions.

You receive the project's recent sessions and its knowledge items (each with an id, kind, date, confidence, \
source and stage; "memory" items were written by Claude Code's own memory feature and are usually reliable). The stage \
says how well an item is established: wip, provisional (seen once), established (confirmed by several sessions), \
canonical (long confirmed, or pinned by the developer). You may also \
receive the previous version of the knowledge base.

People skim the knowledge base and agents read it before they work, so keep it short: fragments are fine, no \
filler or hedging, and respect the word limits. Write the current knowledge base:
- A three-bullet TL;DR of what matters most, and a short overview of what the project is, how it is built, and \
where it stands now.
- Sections of concise, self-contained bullets, each with a short title that states the rule or fact. Suggested sections (use only those that have content, add others if \
needed): "Architecture & key facts", "Run, test & deploy", "Gotchas & fixes", "Decisions & rationale", \
"Conventions & preferences", "Useful commands", "Open threads".
- An architecture sketch (diagram): the project's main parts as nodes (component = code it owns, interface = a way in \
such as a CLI, UI, API or MCP server, store = a database, file or config it keeps, external = a service or tool it \
depends on) and edges for how they connect (calls, reads, writes, serves, deploys to). At most 12 nodes, labelled in \
the project's own names. Cite on every node and edge the ids of the items that state it, and draw only connections the \
items state; leave nodes and edges empty when the items do not say how the project is built.
- Merge duplicates into one bullet, prefer newer, higher-stage and higher-confidence items when they conflict, and \
keep concrete identifiers (paths, commands, config keys, error messages). Cite the ids each bullet is based on.
- List in superseded every item that should leave: reason "duplicate" when another item states the same lesson \
(by = the item that stays; prefer keeping the higher-stage one), "outdated" or "contradicted" when a newer item \
replaces it (by = that item, or null). Report every true duplicate, including ones from different sessions: that is \
how a lesson that keeps recurring becomes established. Items that are merely related are not duplicates.
Do not invent anything that the items do not support."""

GLOBAL_SYSTEM = """\
You maintain a developer's personal engineering playbook, distilled from knowledge extracted across all of their \
Claude Code sessions and projects.

You receive cross-project knowledge items (each with an id, kind, project, date, confidence and stage: wip, \
provisional, established or canonical) and possibly the previous playbook. The developer skims the playbook, and agents read it before they work, so keep it short: \
fragments are fine, no filler or hedging, and respect the word limits. Write the current playbook:
- A three-bullet TL;DR of the rules that matter most, and a short overview of how this developer works and what \
they work on (no names, emails or long project lists).
- Sections of concise, self-contained bullets, each with a short title that states the rule or fact. Suggested \
sections (use only those with content): "Working preferences for Claude", "Tooling & platform gotchas", \
"Reusable patterns & commands", "Learnings", "Recurring problems".
- Merge duplicates, prefer newer and higher-stage items on conflict, keep concrete identifiers, and cite source ids per bullet.
- List in superseded the items that state the same lesson as another (reason "duplicate", by = the item that stays), \
and those that are outdated or contradicted (by = the newer item, or null). Merely related items are not duplicates.
Do not invent anything that the items do not support."""

# with [analysis] language = "ja", besides llm.JAPANESE: section names the dashboard can recognize, and duplicates
# across languages
DUPLICATES_ACROSS_LANGUAGES = "Items written in different languages that state the same lesson are duplicates."
PROJECT_JA = ("Name the sections in Japanese; the suggested ones are 「アーキテクチャと主な事実」, 「実行・テスト・デプロイ」, "
              "「落とし穴と修正」, 「決定とその理由」, 「規約と好み」, 「便利なコマンド」 and 「未解決の事項」. Diagram node labels keep "
              "the project's own names. " + DUPLICATES_ACROSS_LANGUAGES)
GLOBAL_JA = ("Name the sections in Japanese; the suggested ones are 「Claude への作業の好み」, 「ツールとプラットフォームの落とし穴」, "
             "「再利用できるパターンとコマンド」, 「気づき」 and 「繰り返す問題」. " + DUPLICATES_ACROSS_LANGUAGES)

# The playbook differs from a project's knowledge base in what its overview describes, and draws no architecture
GLOBAL_SCHEMA = copy.deepcopy(KB_SCHEMA)
GLOBAL_SCHEMA["properties"]["overview"]["description"] = "At most 3 plain sentences: how this developer works and on what"
del GLOBAL_SCHEMA["properties"]["diagram"]
GLOBAL_SCHEMA["required"].remove("diagram")


def _item_line(k: dict) -> dict:
    return {
        "id": k["id"],
        "kind": k["kind"],
        "date": (k.get("created_at") or "")[:10],
        "confidence": k.get("confidence"),
        "source": k.get("source"),
        "stage": k.get("stage") or "provisional",
        "project": k.get("project_name"),
        "title": k["title"],
        "body": truncate(k.get("body") or "", 900),
    }


def projects_needing_synthesis(conn: sqlite3.Connection, cfg: Config, force: bool = False) -> list[str]:
    out = []
    rows = conn.execute(
        "SELECT project_path, MAX(id) AS max_id, COUNT(*) AS n FROM knowledge "
        "WHERE status = 'active' AND project_path IS NOT NULL GROUP BY project_path"
    ).fetchall()
    for r in rows:
        if cfg.is_excluded(r["project_path"]):
            continue
        if not force and conn.execute(
            "SELECT 1 FROM sessions WHERE project_path = ? AND source != 'history' AND analysis_status IN ('pending', 'stale') "
            "LIMIT 1", (r["project_path"],)).fetchone():
            continue  # more of this project's sessions are queued: synthesize once, after they are analyzed
        kb = conn.execute("SELECT knowledge_max_id FROM project_kb WHERE project_path = ?", (r["project_path"],)).fetchone()
        last = kb["knowledge_max_id"] if kb else 0
        new_items = conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE project_path = ? AND status = 'active' AND id > ?",
            (r["project_path"], last or 0),
        ).fetchone()[0]
        if force and new_items:
            out.append(r["project_path"])
        elif new_items >= cfg.synthesis.min_new_items or (kb is None and r["n"] >= 2):
            out.append(r["project_path"])
    return out


def global_needs_synthesis(conn: sqlite3.Connection, cfg: Config) -> bool:
    kb = conn.execute("SELECT knowledge_max_id FROM project_kb WHERE project_path = ?", (GLOBAL,)).fetchone()
    last = kb["knowledge_max_id"] if kb else 0
    n = conn.execute(
        "SELECT COUNT(*) FROM knowledge WHERE status = 'active' AND (scope = 'global' OR kind = 'preference') AND id > ?",
        (last or 0,),
    ).fetchone()[0]
    return n >= max(cfg.synthesis.min_new_items, 5)


def synthesize_project(conn: sqlite3.Connection, cfg: Config, project_path: str, runner: Runner | None = None) -> dict:
    runner = runner or make_runner(cfg)
    is_global = project_path == GLOBAL
    if is_global:
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM knowledge k WHERE status = 'active' AND (scope = 'global' OR kind = 'preference') "
            f"ORDER BY pinned DESC, {STAGE_ORDER_SQL}, id DESC LIMIT ?", (MAX_ITEMS,))]
        name = "Global playbook"
        sessions_txt = ""
    else:
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM knowledge k WHERE status = 'active' AND project_path = ? "
            f"ORDER BY pinned DESC, {STAGE_ORDER_SQL}, CASE confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
            "id DESC LIMIT ?",
            (project_path, MAX_ITEMS))]
        name_row = conn.execute("SELECT project_name FROM sessions WHERE project_path = ? LIMIT 1", (project_path,)).fetchone()
        name = name_row[0] if name_row else project_path.rstrip("/").split("/")[-1]
        sessions = conn.execute(
            "SELECT started_at, title, outcome, summary FROM sessions WHERE project_path = ? AND source != 'history' "
            "ORDER BY started_at DESC LIMIT 40", (project_path,)).fetchall()
        sessions_txt = "\n".join(
            f"- {local_str(s['started_at'], '%Y-%m-%d')} [{s['outcome'] or 'not analyzed'}] {s['title']}: "
            f"{one_line(s['summary'] or '', 220)}" for s in sessions
        )
    if not items:
        raise ValueError(f"no active knowledge for {project_path}")
    # threshold bookkeeping must reflect what this synthesis saw, not items added during the (slow) call
    max_id = conn.execute(
        "SELECT MAX(id) FROM knowledge WHERE " + ("(scope = 'global' OR kind = 'preference')" if is_global else "project_path = ?"),
        () if is_global else (project_path,),
    ).fetchone()[0]
    prev = conn.execute("SELECT markdown FROM project_kb WHERE project_path = ?", (project_path,)).fetchone()
    parts = [f"<project name=\"{name}\" path=\"{project_path}\" />"] if not is_global else []
    if sessions_txt:
        parts.append(f"<recent_sessions>\n{sessions_txt}\n</recent_sessions>")
    parts.append("<knowledge_items>\n" + "\n".join(json.dumps(_item_line(k), ensure_ascii=False) for k in items)
                 + "\n</knowledge_items>")
    if prev and prev["markdown"]:
        parts.append(f"<previous_version>\n{truncate(prev['markdown'], 20000)}\n</previous_version>")
    parts.append("Write the updated knowledge base." if not is_global else "Write the updated playbook.")
    system = written_in(cfg, GLOBAL_SYSTEM, note=GLOBAL_JA) if is_global else written_in(cfg, PROJECT_SYSTEM, note=PROJECT_JA)
    res = runner.run("\n\n".join(parts), GLOBAL_SCHEMA if is_global else KB_SCHEMA, system=system, model=cfg.synthesis.model)
    valid_ids = {k["id"] for k in items}
    data = normalize_kb(res.data, valid_ids)
    if is_global:
        data.pop("diagram", None)  # the playbook spans projects: it has no architecture to draw
    try:
        # only a project's own synthesis retires items: the global playbook merges across projects, and a
        # cross-project duplicate must stay in each project's knowledge base. Both pool the evidence of duplicates.
        superseded = ladder.apply_synthesis(conn, data["superseded"], valid_ids, retire=not is_global)
        for section in data["sections"]:
            for it in section["items"]:
                trust = ladder.bullet_trust(conn, [i for i in it.get("sources") or [] if i in valid_ids])
                if trust:
                    it["stage"], it["sessions"] = trust["stage"], trust["sessions"]
        markdown = render_kb_markdown(name, data, n_items=len(items), model=res.model)
        conn.execute(
            "INSERT INTO project_kb(project_path, project_name, updated_at, model, knowledge_max_id, n_items, overview, markdown, kb_json) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(project_path) DO UPDATE SET project_name=excluded.project_name, "
            "updated_at=excluded.updated_at, model=excluded.model, knowledge_max_id=excluded.knowledge_max_id, "
            "n_items=excluded.n_items, overview=excluded.overview, markdown=excluded.markdown, kb_json=excluded.kb_json",
            (project_path, name, utcnow_iso(), res.model, max_id, len(items), data["overview"], markdown, dumps(data)),
        )
        conn.execute(
            "INSERT INTO analyses(kind, target, started_at, finished_at, model, status, input_chars, chunks, cost_usd, duration_ms) "
            "VALUES ('synthesis', ?, ?, ?, ?, 'done', ?, 1, ?, ?)",
            (project_path, utcnow_iso(), utcnow_iso(), res.model, sum(len(k.get("body") or "") for k in items), res.cost_usd,
             res.duration_ms),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    log.info("synthesized KB for %s (%d items, %d superseded, $%.3f)", name, len(items), len(superseded), res.cost_usd)
    return data


def normalize_kb(data: dict, valid_ids: set[int] | None = None) -> dict:
    """Coerce a model reply into {overview, sections[{title, items[{text, sources[int]}]}], superseded_ids[int]},
    plus a diagram when one survives pruning to what the knowledge items (`valid_ids`, when given) support."""
    def ids(values) -> list[int]:
        out = []
        for v in values if isinstance(values, list) else []:
            try:
                out.append(int(v))
            except (TypeError, ValueError):
                continue
        return out

    sections = []
    raw_sections = data.get("sections") if isinstance(data.get("sections"), list) else []
    for sec in raw_sections:
        if isinstance(sec, str):
            sec = {"title": "Notes", "items": [sec]}
        if not isinstance(sec, dict):
            continue
        items = []
        raw_items = sec.get("items") if isinstance(sec.get("items"), list) else []
        for it in raw_items:
            if isinstance(it, str) and it.strip():
                items.append({"text": it.strip(), "sources": []})
            elif isinstance(it, dict) and str(it.get("text") or "").strip():
                item = {"text": str(it["text"]).strip(), "sources": ids(it.get("sources"))}
                if str(it.get("title") or "").strip():
                    item["title"] = str(it["title"]).strip()
                items.append(item)
        if items:
            sections.append({"title": str(sec.get("title") or "Notes"), "items": items})
    superseded, seen = [], set()
    for entry in data.get("superseded") if isinstance(data.get("superseded"), list) else []:
        if isinstance(entry, dict):
            got = ids([entry.get("id")])
            by = ids([entry.get("by")]) if entry.get("by") is not None else []
            reason = entry.get("reason") if entry.get("reason") in SUPERSEDE_REASONS else "outdated"
        else:
            got, by, reason = ids([entry]), [], "outdated"
        if got and got[0] not in seen:
            seen.add(got[0])
            superseded.append({"id": got[0], "by": by[0] if by else None, "reason": reason})
    for legacy in ids(data.get("superseded_ids")):  # the older reply shape: a bare list of ids
        if legacy not in seen:
            seen.add(legacy)
            superseded.append({"id": legacy, "by": None, "reason": "outdated"})
    out = {"overview": str(data.get("overview") or "").strip(), "sections": sections,
           "superseded": superseded, "superseded_ids": [e["id"] for e in superseded]}
    tldr = data.get("tldr")
    tldr = [tldr] if isinstance(tldr, str) else tldr if isinstance(tldr, list) else []
    if any(str(x).strip() for x in tldr):
        out["tldr"] = [str(x).strip() for x in tldr if str(x).strip()][:3]
    diagram = normalize_diagram(data.get("diagram"), valid_ids)
    if diagram:
        out["diagram"] = diagram
    return out


def render_kb_markdown(name: str, data: dict, *, n_items: int, model: str | None, link=None) -> str:
    """Render a KB as Markdown. `link(id) -> str` can turn source ids into links (defaults to [k12])."""
    link = link or (lambda i: f"k{i}")
    lines = [f"# {name}", "", f"_Synthesized from {n_items} knowledge items · {local_str(utcnow_iso())} · {model or ''}_", ""]
    if data.get("tldr"):
        lines += ["**TL;DR**", ""] + [f"- {x}" for x in data["tldr"]] + [""]
    if data.get("overview"):
        lines += ["## Overview", "", data["overview"].strip(), ""]
    if data.get("diagram"):
        lines += ["## Architecture", "", "```mermaid", to_mermaid(data["diagram"]), "```", ""]
    for section in data.get("sections") or []:
        items = section.get("items") or []
        if not items:
            continue
        lines += [f"## {section.get('title') or 'Notes'}", ""]
        for it in items:
            src = ", ".join(link(i) for i in it.get("sources") or [] if isinstance(i, int))
            title = f"**{str(it['title']).strip()}**: " if it.get("title") else ""
            trust = it.get("stage") if it.get("stage") in TRUSTED else ""
            if trust and (it.get("sessions") or 0) > 1:
                trust += f" ×{it['sessions']}"
            note = " · ".join(x for x in (src, trust) if x)
            lines.append(f"- {title}{str(it.get('text') or '').strip()}" + (f" <sub>[{note}]</sub>" if note else ""))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def kb_for_path(conn: sqlite3.Connection, cwd: str) -> dict | None:
    """Knowledge base for a working directory (exact project or the nearest enclosing one)."""
    rows = conn.execute("SELECT * FROM project_kb WHERE project_path != ?", (GLOBAL,)).fetchall()
    best = None
    for r in rows:
        p = r["project_path"].rstrip("/")
        if cwd == p or cwd.startswith(p + "/"):
            if best is None or len(p) > len(best["project_path"]):
                best = r
    return dict(best) if best else None


def kb_sections(row: dict) -> list[dict]:
    return (loads(row.get("kb_json"), {}) or {}).get("sections") or []
