"""What coding agents remember on their own (Claude Code's memory files, Codex memories), imported as knowledge."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import Config
from ..core.paths import project_name_for
from ..core.util import dumps, one_line, safe_text, to_iso, utcnow_iso
from ..knowledge.ladder import MEMORY_STAGE_REASON
from ..sources.claude.files import decode_project_dir, peek_cwd

_APPLIES_RE = re.compile(r"applies_to:.*?cwd=(\S+?)(?:;|\s|$)")


def import_codex_memories(conn, cfg: Config, mem: Path) -> int:
    """Codex's memory notes (MEMORY.md, memory_summary.md, rollout_summaries/*.md) as knowledge items."""
    changed = 0
    prefix = str(mem) + "/"
    for r in conn.execute("SELECT id, source_ref FROM knowledge WHERE source = 'memory' AND agent = 'codex' "
                          "AND status = 'active' AND source_ref LIKE ?", (prefix + "%",)).fetchall():
        if not Path(r["source_ref"]).exists():
            conn.execute("UPDATE knowledge SET status = 'superseded', updated_at = ? WHERE id = ?", (utcnow_iso(), r["id"]))
            changed += 1
    files = [p for p in mem.rglob("*.md") if ".git" not in p.parts and p.name != "raw_memories.md"]
    for f in sorted(files):
        try:
            text = f.read_text(errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        first = next((l.lstrip("# ").strip() for l in text.splitlines() if l.strip()), f.stem)
        title = one_line(safe_text(first), 120)
        body = safe_text(text if len(text) <= 8000 else text[:8000] + "\n…")
        m = _APPLIES_RE.search(text)
        project_path = m.group(1).rstrip("/") if m else None
        if cfg.is_excluded(project_path):
            continue
        fp = f"memory:{f}"
        now = utcnow_iso()
        tags = dumps(["memory", "codex", "rollout-summary" if f.parent.name == "rollout_summaries" else "summary"])
        existing = conn.execute("SELECT id, body, title, status FROM knowledge WHERE fingerprint = ?", (fp,)).fetchone()
        if existing:
            if existing["body"] != body or existing["title"] != title or existing["status"] == "superseded":
                conn.execute("UPDATE knowledge SET title=?, body=?, tags_json=?, updated_at=?, project_path=?, project_name=?, "
                             "status = CASE WHEN status = 'dismissed' THEN status ELSE 'active' END WHERE id=?",
                             (title, body, tags, now, project_path, project_name_for(project_path) if project_path else None, existing["id"]))
                changed += 1
            continue
        conn.execute(
            "INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, tags_json, scope, confidence, "
            "evidence, source, agent, source_ref, fingerprint, created_at, updated_at, stage, stage_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (None, project_path, project_name_for(project_path) if project_path else None, "fact", title, body, tags,
             "project" if project_path else "global", "high", "Codex memory", "memory", "codex", str(f), fp,
             to_iso(datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)), now, "established", MEMORY_STAGE_REASON))
        changed += 1
    return changed


_MEMORY_KIND = {"user": "preference", "feedback": "preference", "project": "fact", "reference": "reference"}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head, body = text[3:end], text[end + 4 :]
    meta: dict = {}
    parent = None
    for line in head.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([\w.-]+):\s*(.*)$", line)
        if not m:
            continue
        indent, key, value = m.groups()
        value = value.strip().strip('"').strip("'")
        if indent and parent is not None and isinstance(meta.get(parent), dict):
            meta[parent][key] = value
        elif value == "":
            meta[key] = {}
            parent = key
        else:
            meta[key] = value
            parent = None
    return meta, body.lstrip("\n")


def import_memory_dir(conn: sqlite3.Connection, cfg: Config, proj: Path, mem: Path, project_path: str | None = None) -> int:
    row = conn.execute(
        "SELECT project_path FROM sessions WHERE project_dir = ? AND project_path IS NOT NULL "
        "GROUP BY project_path ORDER BY COUNT(*) DESC LIMIT 1",
        (proj.name,),
    ).fetchone()
    project_path = row[0] if row else (project_path or peek_cwd(proj) or decode_project_dir(proj.name))
    if cfg.is_excluded(project_path):
        return 0
    changed = 0
    # Claude curates its memory: a deleted note is retired here too (the archive keeps the file)
    for r in conn.execute(
        "SELECT id, source_ref FROM knowledge WHERE source = 'memory' AND status = 'active' AND source_ref LIKE ?",
        (str(mem) + "/%",),
    ).fetchall():
        if not Path(r["source_ref"]).exists():
            conn.execute("UPDATE knowledge SET status = 'superseded', updated_at = ? WHERE id = ?", (utcnow_iso(), r["id"]))
            changed += 1
    for f in sorted(mem.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        meta, body = parse_frontmatter(text)
        mtype = meta["metadata"].get("type") if isinstance(meta.get("metadata"), dict) else meta.get("type")
        mtype = mtype if isinstance(mtype, str) else None
        kind = _MEMORY_KIND.get((mtype or "").strip(), "fact")
        title = next((v for v in (meta.get("description"), meta.get("name")) if isinstance(v, str) and v), f.stem)
        name = meta.get("name") if isinstance(meta.get("name"), str) else f.stem
        fp = f"memory:{f}"
        existing = conn.execute("SELECT id, body, title, status FROM knowledge WHERE fingerprint = ?", (fp,)).fetchone()
        now = utcnow_iso()
        tags = dumps(["memory", mtype] if mtype else ["memory"])
        body, title = safe_text(body), safe_text(title)
        if existing:
            if existing["body"] != body.strip() or existing["title"] != title or existing["status"] == "superseded":
                conn.execute(
                    "UPDATE knowledge SET title=?, body=?, kind=?, tags_json=?, updated_at=?, project_path=?, project_name=?, "
                    "status = CASE WHEN status = 'dismissed' THEN status ELSE 'active' END WHERE id=?",
                    (title, body.strip(), kind, tags, now, project_path, project_name_for(project_path), existing["id"]),
                )
                changed += 1
            continue
        conn.execute(
            "INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, tags_json, scope, confidence, "
            "evidence, source, source_ref, fingerprint, created_at, updated_at, stage, stage_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (None, project_path, project_name_for(project_path), kind, title, body.strip(), tags,
             "global" if mtype in ("user", "feedback") else "project", "high", name, "memory", str(f), fp,
             to_iso(datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)), now, "established", MEMORY_STAGE_REASON),
        )
        changed += 1
    return changed
