"""Whether a lesson still matches the code it was learned from. When a session is analyzed, each lesson names the
files it depends on; those the session actually read or changed are recorded with a hash of what they held then
(knowledge.sources_json). Reading the lesson later compares the files as they are now: unchanged, it is still what the
session saw; changed or gone, the lesson may be out of date and is worth checking before it is relied on. Only the
project's own folder on this computer is read, and nothing is ever fixed for you."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

from ..core.util import dumps, loads

MAX_FILES = 5  # recorded per lesson
HASH_BYTES = 8 << 20  # of a file, only the first 8 MiB is hashed (with its size), so a huge file can't stall a read
_cache: dict[tuple[str, int, int], str] = {}  # (path, size, mtime) -> hash: a file read once per change


def _digest(path: Path) -> tuple[str, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _cache:
        h = hashlib.sha256()
        try:
            with path.open("rb") as fh:
                h.update(fh.read(HASH_BYTES))
        except OSError:
            return None
        h.update(str(st.st_size).encode())
        _cache[key] = h.hexdigest()
    return _cache[key], st.st_size


def _under(project: str, path: str) -> str | None:
    """`path` relative to the project's folder, or None when it lies outside it."""
    try:
        rel = os.path.relpath(path, project)
    except ValueError:  # another drive on Windows
        return None
    return None if rel == "." or rel.startswith("..") or os.path.isabs(rel) else rel.replace(os.sep, "/")


def record(conn: sqlite3.Connection, session_id: str, project: str | None, cited) -> list[dict]:
    """The files a lesson names (`cited`, as the analysis gave them), kept only when the session read or changed them
    and they lie in the project's folder on this computer, each with a hash of what it holds now."""
    if not project or not isinstance(cited, list) or not os.path.isdir(project):
        return []
    touched = {}
    for (path,) in conn.execute("SELECT path FROM session_files WHERE session_id = ?", (session_id,)):
        if (rel := _under(project, path)) is not None:
            touched[rel] = path
    out, seen = [], set()
    for raw in cited:
        name = str(raw or "").strip().replace("\\", "/")
        if not name:
            continue
        rel = _under(project, name) if os.path.isabs(name) else name.removeprefix("./")
        if rel not in touched:  # a bare name the session used under a longer path: only when that's unambiguous
            matches = [r for r in touched if r.endswith("/" + name)]
            rel = matches[0] if len(matches) == 1 else None
        if rel is None or rel in seen:
            continue
        if (got := _digest(Path(project) / rel)) is None:
            continue
        seen.add(rel)
        out.append({"path": rel, "sha256": got[0], "size": got[1]})
        if len(out) >= MAX_FILES:
            break
    return out


def check(project: str | None, sources_json) -> dict | None:
    """{"state": "fresh" | "stale", "changed": [paths]} for a lesson with recorded files, or None when it has none or
    its project's folder isn't on this computer (a lesson a teammate's hub sent, say)."""
    sources = loads(sources_json, []) if isinstance(sources_json, str) else sources_json or []
    if not sources or not project or not os.path.isdir(project):
        return None
    changed = []
    for s in sources:
        got = _digest(Path(project) / s.get("path", ""))
        if got is None or got[0] != s.get("sha256"):
            changed.append(s.get("path"))
    return {"state": "stale" if changed else "fresh", "changed": changed}


def stale_note(project: str | None, sources_json) -> str:
    """' (may be out of date: app.py changed since)' for a stale lesson, else ''."""
    got = check(project, sources_json)
    if not got or got["state"] != "stale":
        return ""
    files = ", ".join(got["changed"][:3]) + (f" and {len(got['changed']) - 3} more" if len(got["changed"]) > 3 else "")
    return f" (may be out of date: {files} changed since)"


def stale_ids(conn: sqlite3.Connection, ids) -> dict[int, str]:
    """Of these knowledge ids, those whose recorded files changed: id -> note."""
    ids = [int(i) for i in ids or [] if str(i).isdigit()]
    if not ids:
        return {}
    try:
        rows = conn.execute(f"SELECT id, project_path, sources_json FROM knowledge WHERE id IN ({','.join('?' * len(ids))}) "
                            "AND sources_json IS NOT NULL", ids).fetchall()
    except sqlite3.OperationalError:  # an archive from before sources_json, opened read-only so not yet upgraded
        return {}
    return {r[0]: note for r in rows if (note := stale_note(r[1], r[2]))}


def sources_value(sources: list[dict]) -> str | None:
    return dumps(sources) if sources else None
