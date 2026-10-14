"""Obsidian-compatible Markdown vault: one note per session, per project, per knowledge kind, plus a home page."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from pathlib import Path

from .config import Config
from .db import kv_get, kv_set
from .redact import redact
from .synthesize import GLOBAL, render_kb_markdown
from .util import human_cost, human_count, human_duration, local_str, loads, one_line
from .views import KIND_ICON, OUTCOME_ICON, project_labels, session_markdown, session_record

log = logging.getLogger("interlatch.export")

_BAD = re.compile(r'[\\/:*?"<>|#^\[\]\n\r\t]+')


def safe_name(text: str, limit: int = 80) -> str:
    text = _BAD.sub("-", text).strip(" .-")
    return (text[:limit].rstrip(" .-") or "untitled")


def session_note_name(s: dict, label: str) -> str:
    stamp = local_str(s.get("started_at"), "%Y-%m-%d %H%M")
    return f"{stamp} {safe_name(label, 30)} - {safe_name(s.get('title') or 'session', 70)} ({s['id'][:8]})"


def _write(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(errors="replace") == content:
        return False
    path.write_text(content)
    return True


def export_markdown(conn: sqlite3.Connection, cfg: Config, *, full: bool = False) -> int:
    root = cfg.notes_dir
    root.mkdir(parents=True, exist_ok=True)
    labels = project_labels(conn)
    written = 0
    names: dict[str, str] = {}
    markers: list[tuple[str, str]] = []  # written in one short transaction at the end
    sessions = conn.execute(
        "SELECT id, started_at, title, project_path, ingested_at, analyzed_at, files_sig, source FROM sessions "
        "ORDER BY started_at"
    ).fetchall()
    for row in sessions:
        label = labels.get(row["project_path"] or "", "unknown")
        name = session_note_name(dict(row), label)
        names[row["id"]] = name
        sig = hashlib.sha1(f"{row['ingested_at']}|{row['analyzed_at']}|{row['files_sig']}|{row['title']}|{label}".encode()).hexdigest()
        key = f"export:{row['id']}"
        prev = kv_get(conn, key)
        month = local_str(row["started_at"], "%Y/%m") if row["started_at"] else "undated"
        path = root / "Sessions" / month / f"{name}.md"
        if prev and not full:
            prev_path, prev_sig = prev.split("|", 1)
            if prev_sig == sig and Path(prev_path) == path and path.exists():
                continue
            if Path(prev_path) != path:
                Path(prev_path).unlink(missing_ok=True)
        s = session_record(conn, row["id"])
        body = session_markdown(s, frontmatter=True, project_link=f"[[{safe_name(label)}]]")
        written += _write(path, body)
        markers.append((key, f"{path}|{sig}"))
    for key, value in markers:
        kv_set(conn, key, value)
    conn.commit()
    written += _export_projects(conn, root, labels, names)
    written += _export_knowledge(conn, root, names)
    for r in conn.execute("SELECT period, markdown FROM reviews"):
        written += _write(root / "Reviews" / f"{r['period']}.md", r["markdown"])
    written += _export_glossary(conn, root, labels, names)
    written += _export_home(conn, root, labels, names)
    log.info("markdown export: %d notes written to %s", written, root)
    return written


def _export_projects(conn, root: Path, labels: dict, names: dict) -> int:
    written = 0
    for path, label in labels.items():
        kb = conn.execute("SELECT * FROM project_kb WHERE project_path = ?", (path,)).fetchone()
        sessions = conn.execute(
            "SELECT id, started_at, title, outcome, n_prompts, active_s, est_cost_usd FROM sessions WHERE project_path = ? "
            "ORDER BY started_at DESC", (path,)).fetchall()
        lines = []
        if kb:
            data = loads(kb["kb_json"], {}) or {}
            ids = {k: names.get(sid) for k, sid in conn.execute(
                "SELECT id, session_id FROM knowledge WHERE project_path = ?", (path,)).fetchall()}
            lines.append(render_kb_markdown(
                label, data, n_items=kb["n_items"] or 0, model=kb["model"],
                link=lambda i, ids=ids: f"[[{ids[i]}|k{i}]]" if ids.get(i) else f"k{i}",
            ))
        else:
            lines += [f"# {label}", "", f"`{path}`", "", "_No synthesized knowledge base yet._", ""]
        lines += ["## Sessions", "", "| Date | Session | Outcome | Prompts | Active | Est. cost |", "|---|---|---|---:|---:|---:|"]
        for s in sessions:
            icon = OUTCOME_ICON.get(s["outcome"] or "", "")
            lines.append(
                f"| {local_str(s['started_at'], '%Y-%m-%d')} | [[{names.get(s['id'], s['id'])}\\|{safe_name(s['title'] or 'session', 60)}]] "
                f"| {icon} {s['outcome'] or '-'} | {s['n_prompts']} | {human_duration(s['active_s'])} | {human_cost(s['est_cost_usd'])} |"
            )
        memory = conn.execute(
            "SELECT title, body FROM knowledge WHERE project_path = ? AND source = 'memory' AND status = 'active'", (path,)
        ).fetchall()
        if memory:
            lines += ["", "## Claude memory notes", ""]
            for m in memory:
                lines.append(f"- **{redact(m['title'])}** — {redact(one_line(m['body'], 300))}")
        written += _write(root / "Projects" / f"{safe_name(label)}.md", "\n".join(lines).rstrip() + "\n")
    kb = conn.execute("SELECT * FROM project_kb WHERE project_path = ?", (GLOBAL,)).fetchone()
    if kb:
        written += _write(root / "Global Playbook.md", render_kb_markdown(
            "Global Playbook", loads(kb["kb_json"], {}) or {}, n_items=kb["n_items"] or 0, model=kb["model"]))
    return written


def _export_knowledge(conn, root: Path, names: dict) -> int:
    written = 0
    kinds = [r[0] for r in conn.execute("SELECT DISTINCT kind FROM knowledge WHERE status = 'active'")]
    for kind in kinds:
        items = conn.execute(
            "SELECT * FROM knowledge WHERE kind = ? AND status = 'active' ORDER BY project_name, created_at DESC", (kind,)
        ).fetchall()
        lines = [f"# {KIND_ICON.get(kind, '')} {kind.title()} ({len(items)})", ""]
        current = None
        for k in items:
            if k["project_name"] != current:
                current = k["project_name"]
                lines += [f"## {current or 'General'}", ""]
            src = f"[[{names[k['session_id']]}|session]]" if k["session_id"] in names else ("memory" if k["source"] == "memory" else "")
            tags = " ".join(f"#{t.replace(' ', '-')}" for t in loads(k["tags_json"], []) or [])
            lines += [f"### {redact(k['title'])}", f"<sub>{k['confidence'] or ''} · {k['scope']} · {src} {tags}</sub>", "",
                      redact(k["body"] or ""), ""]
        written += _write(root / "Knowledge" / f"{kind.title()}.md", "\n".join(lines).rstrip() + "\n")
    return written


def _export_glossary(conn, root: Path, labels: dict, names: dict) -> int:
    from .glossary import glossary_entries

    entries = glossary_entries(conn)
    if not entries:
        return 0
    lines = ["# Glossary", "", f"{len(entries)} terms from your coding-agent sessions, A–Z.", ""]
    letter = None
    for e in entries:
        first = e["term"][:1].upper() if e["term"][:1].isalpha() else "#"
        if first != letter:
            letter = first
            lines += [f"## {letter}", ""]
        lines.append(f"### {e['term']}")
        meta = [e["category"] or ""]
        if e["aliases"]:
            meta.append("also: " + ", ".join(e["aliases"]))
        if e["n_sessions"]:
            meta.append(f"in {e['n_sessions']} sessions, {local_str(e['first_seen'], '%Y-%m-%d')} → {local_str(e['last_seen'], '%Y-%m-%d')}")
        lines += [f"<sub>{' · '.join(m for m in meta if m)}</sub>", "", redact(e["definition"] or ""), ""]
        for u in e["usage"]:
            label = labels.get(u["project_path"])
            where = f"[[{safe_name(label)}]]" if label else u["project_name"]
            if u["context"]:
                lines.append(f"- **{where}**: {redact(u['context'])}")
        if e["related"]:
            lines.append("- Related: " + ", ".join(f"[[Glossary#{safe_name(r)}|{r}]]" for r in e["related"]))
        tops = [f"[[{names[s['id']]}|{safe_name(s['title'] or 'session', 50)}]]" for s in e["top_sessions"][:3] if s["id"] in names]
        if tops:
            lines.append("- Seen in: " + ", ".join(tops))
        lines.append("")
    return _write(root / "Glossary.md", "\n".join(lines).rstrip() + "\n")


def _export_home(conn, root: Path, labels: dict, names: dict) -> int:
    t = conn.execute(
        "SELECT COUNT(*) n, SUM(n_prompts) prompts, SUM(n_tool_calls) tools, SUM(active_s) active, "
        "SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) tokens, SUM(est_cost_usd) cost, "
        "MIN(started_at) first FROM sessions"
    ).fetchone()
    k = conn.execute("SELECT COUNT(*) FROM knowledge WHERE status = 'active'").fetchone()[0]
    lines = [
        "# Interlatch",
        "",
        f"{t['n']} sessions since {local_str(t['first'], '%Y-%m-%d')} · {t['prompts'] or 0} prompts · "
        f"{human_count(t['tools'])} tool calls · {human_duration(t['active'])} active · {human_count(t['tokens'])} tokens · "
        f"est. {human_cost(t['cost'])} API-equivalent · {k} knowledge items",
        "",
        "[[Global Playbook]] · [[Glossary]] · Reviews: " + (" · ".join(f"[[Reviews/{p}|{p}]]" for (p,) in conn.execute(
            "SELECT period FROM reviews ORDER BY period DESC LIMIT 8")) or "none yet") + "  \nKnowledge: " + " · ".join(
            f"[[Knowledge/{kind.title()}|{kind}]]" for (kind,) in conn.execute(
                "SELECT DISTINCT kind FROM knowledge WHERE status = 'active' ORDER BY kind")),
        "",
        "## Projects",
        "",
    ]
    for path, n, last in conn.execute(
        "SELECT project_path, COUNT(*), MAX(started_at) FROM sessions GROUP BY project_path ORDER BY MAX(started_at) DESC"
    ):
        label = labels.get(path or "", "unknown")
        lines.append(f"- [[{safe_name(label)}]] — {n} sessions, last {local_str(last, '%Y-%m-%d')}")
    lines += ["", "## Recent sessions", ""]
    for s in conn.execute("SELECT id, started_at, title, outcome FROM sessions ORDER BY started_at DESC LIMIT 30"):
        lines.append(f"- {local_str(s['started_at'], '%m-%d %H:%M')} {OUTCOME_ICON.get(s['outcome'] or '', '')} "
                     f"[[{names.get(s['id'], s['id'])}|{safe_name(s['title'] or 'session', 80)}]]")
    return _write(root / "Home.md", "\n".join(lines) + "\n")
