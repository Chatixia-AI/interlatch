"""Export sessions as files to download: one session as a file, several as a .zip.

Markdown is the readable record: the session overview (summary, knowledge, files) followed by the conversation.
JSON carries the same record plus every event. Both are redacted like everything else Interlatch shows. The
original transcript is the agent's own file as archived, unredacted, for the sessions that have one of their own.
"""

from __future__ import annotations

import gzip
import io
import json
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from .agents import full_name
from .export_md import safe_name
from .i18n import tr
from .redact import redact
from .util import local_str, one_line
from .views import session_markdown, session_record

FORMATS = {"md": "Markdown", "json": "JSON", "raw": "Original transcript"}
MAX_SESSIONS = 1000
# sources whose archive is the session's own transcript (a claude.ai export is one file for every chat in it)
RAW_SOURCES = ("transcript", "codex-import", "codex-cloud")


class ExportError(ValueError):
    pass


def file_stem(s: dict) -> str:
    """"2026-09-28 Repository access 1a2b3c4d": sorts by date, readable, unique."""
    date = local_str(s.get("started_at"), "%Y-%m-%d") if s.get("started_at") else "undated"
    return f"{date} {safe_name(s.get('title') or 'untitled', 60)} {s['id'][:8]}"


def conversation_markdown(conn: sqlite3.Connection, sid: str, agent: str | None) -> str:
    """The main thread as Markdown: prompts and replies in full, tool calls as one line each, failed results kept."""
    rows = conn.execute(
        "SELECT ts, kind, tool_name, is_error, text FROM events WHERE session_id = ? AND agent_id = '' "
        "AND kind IN ('prompt', 'text', 'tool_use', 'tool_result', 'command', 'interrupt', 'compact', 'api_error') "
        "ORDER BY seq", (sid,)).fetchall()
    who = {"prompt": "You", "text": full_name(agent)}
    out: list[str] = []
    speaker = None
    for r in rows:
        kind, text = r["kind"], redact(r["text"] or "").strip()
        if kind in who:
            if not text:
                continue
            if speaker != kind:
                if out and out[-1]:
                    out.append("")
                out += [f"### {who[kind]} · {local_str(r['ts'], '%Y-%m-%d %H:%M')}", ""]
                speaker = kind
            out += [text, ""]
        elif kind == "tool_use":
            out.append(f"- `` {one_line(text, 240)} ``")  # double backticks: a command may hold one
            speaker = "tool"
        elif kind == "tool_result" and r["is_error"]:
            out.append(f"  - failed: {one_line(text, 300)}")
        elif kind == "command":
            out += ["", f"> `{one_line(text, 200)}`", ""]
            speaker = None
        elif kind == "interrupt":
            out += ["", "> _interrupted_", ""]
            speaker = None
        elif kind == "compact":
            out += ["", "---", "_Context compacted._", "---", ""]
            speaker = None
        elif kind == "api_error":
            out += ["", f"> API error: {one_line(text, 300)}", ""]
            speaker = None
    return "\n".join(out).rstrip() + "\n" if out else "_No conversation recorded (only the prompts were kept)._\n"


def session_document(conn: sqlite3.Connection, sid: str) -> str:
    s = session_record(conn, sid)
    overview = session_markdown(s, frontmatter=True, include_prompts=s.get("source") == "history")
    if s.get("source") == "history":
        return overview
    return overview.rstrip() + "\n\n## Conversation\n\n" + conversation_markdown(conn, sid, s.get("agent"))


def _redacted(value):
    """Every string in a record, redacted: prompts, titles and tool input can all hold a secret."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redacted(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redacted(v) for v in value]
    return value


def session_json(conn: sqlite3.Connection, sid: str) -> dict:
    events = [
        {**{k: r[k] for k in r.keys() if k != "meta_json"}, "meta": json.loads(r["meta_json"]) if r["meta_json"] else None}
        for r in conn.execute("SELECT agent_id, seq, ts, role, kind, tool_name, is_error, text, meta_json "
                              "FROM events WHERE session_id = ? ORDER BY agent_id, seq", (sid,))]
    return {"chronicle_export": 1, "session": _redacted(session_record(conn, sid)), "events": _redacted(events)}


def has_original(conn: sqlite3.Connection, sid: str) -> bool:
    row = conn.execute("SELECT source, archive_path FROM sessions WHERE id = ?", (sid,)).fetchone()
    return bool(row and row["source"] in RAW_SOURCES and row["archive_path"] and Path(row["archive_path"]).is_file())


def original_file(conn: sqlite3.Connection, sid: str) -> tuple[str, bytes] | None:
    """The archived transcript, decompressed: (file name, bytes), or None when the session has none of its own."""
    if not has_original(conn, sid):
        return None
    path = Path(conn.execute("SELECT archive_path FROM sessions WHERE id = ?", (sid,)).fetchone()[0])
    data = path.read_bytes()
    name = path.name
    if name.endswith(".gz"):
        data, name = gzip.decompress(data), name[:-3]
    return name, data


def _one(conn: sqlite3.Connection, sid: str, fmt: str) -> tuple[str, bytes] | None:
    """(file name, bytes) of one session in one format; None when it has no such file (an original transcript)."""
    s = conn.execute("SELECT id, title, started_at FROM sessions WHERE id = ?", (sid,)).fetchone()
    stem = file_stem(dict(s))
    if fmt == "md":
        return f"{stem}.md", session_document(conn, sid).encode()
    if fmt == "json":
        return f"{stem}.json", json.dumps(session_json(conn, sid), ensure_ascii=False, indent=1, default=str).encode()
    raw = original_file(conn, sid)
    if raw is None:
        return None
    return f"{stem}{Path(raw[0]).suffix or '.jsonl'}", raw[1]


def export_sessions(conn: sqlite3.Connection, ids: list[str], fmt: str) -> tuple[str, str, bytes]:
    """(download name, content type, bytes): the file itself for one session, a .zip with an index for several."""
    if fmt not in FORMATS:
        raise ExportError(tr("unknown format {fmt!r}", fmt=fmt))
    ids = list(dict.fromkeys(ids))[:MAX_SESSIONS]
    if not ids:
        raise ExportError(tr("no sessions to export"))
    if len(ids) == 1:
        one = _one(conn, ids[0], fmt)
        if one is None:
            raise ExportError(tr("This session has no original transcript of its own (claude.ai chats and prompt-history "
                                 "sessions); export it as Markdown or JSON instead."))
        ctype = {"md": "text/markdown; charset=utf-8", "json": "application/json"}.get(fmt, "application/octet-stream")
        return one[0], ctype, one[1]
    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    folder = f"Interlatch sessions {stamp}"
    buf = io.BytesIO()
    index = [f"# Interlatch export, {stamp}", "", f"{len(ids)} sessions · {FORMATS[fmt]}", "",
             "| Started | Session | Project | Agent | File |", "|---|---|---|---|---|"]
    missing = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for sid in ids:
            s = conn.execute("SELECT id, title, started_at, project_name, agent FROM sessions WHERE id = ?", (sid,)).fetchone()
            one = _one(conn, sid, fmt)
            if one is None:
                missing.append(s)
                continue
            z.writestr(f"{folder}/{one[0]}", one[1])
            title = (s["title"] or "untitled").replace("|", "/")
            index.append(f"| {local_str(s['started_at'], '%Y-%m-%d %H:%M')} | {title} | {s['project_name'] or ''} | "
                         f"{full_name(s['agent'])} | [{one[0]}](<{one[0]}>) |")
        if missing:
            index += ["", f"No original transcript of their own ({len(missing)}): claude.ai chats share one export file "
                      "and prompt-history sessions have none. Export them as Markdown or JSON.", ""]
            index += [f"- {s['title'] or 'untitled'} `{s['id'][:8]}`" for s in missing]
        if len(missing) == len(ids):
            raise ExportError(tr("None of these sessions has an original transcript of its own; export them as Markdown "
                                 "or JSON."))
        z.writestr(f"{folder}/index.md", "\n".join(index) + "\n")
    return f"{folder}.zip", "application/zip", buf.getvalue()
