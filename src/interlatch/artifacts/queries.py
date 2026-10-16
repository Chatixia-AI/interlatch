"""The artifacts sessions made, read back from the archive: where each stands now (on disk, changed, gone, another
computer), listings for the dashboard and MCP, previews and thumbnails, and opening or revealing a file."""

from __future__ import annotations

import hashlib
import json
import os

pass

STATUS = {"present": "on disk", "changed": "changed since", "gone": "gone", "link": "link", "commit": "commit",
          "chat": "in the chat", "elsewhere": "another machine"}
_HASHES: dict[tuple, str] = {}
PREVIEW_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
                 ".svg": "image/svg+xml"}
PREVIEW_MAX = 25_000_000
THUMB_EXT = {".pptx", ".odp", ".docx", ".odt", ".xlsx", ".ods", ".pdf"}  # Quick Look draws their first page


def can_thumbnail() -> bool:
    import shutil
    import sys

    return sys.platform == "darwin" and bool(shutil.which("qlmanage"))  # (path, mtime, size) -> sha256, so a gallery page does not re-read unchanged files


def status_of(row: dict, local_machine: str | None = None) -> str:
    """Where an artifact stands now: still on disk as written, changed since, gone; or a link, a commit, a chat's file."""
    if row["kind"] in ("pr", "published"):
        return "link"
    if row["kind"] == "commit":
        return "commit"
    if (row.get("meta") or {}).get("where") or row.get("agent") in ("claude-ai", "chatgpt"):
        return "chat"
    if local_machine and row.get("machine_id") and row["machine_id"] != local_machine:
        return "elsewhere"
    path = row.get("path")
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return "gone"
    if not row.get("sha256") or st.st_size > 20_000_000:
        return "present"
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _HASHES:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            return "present"
        _HASHES[key] = h.hexdigest()
    return "present" if _HASHES[key] == row["sha256"] else "changed"


def query(conn, *, kind: str | None = None, project: str | list[str] | None = None, agent: str | None = None,
          q: str | None = None, session_id: str | None = None,
          local_machine: str | None = None) -> tuple[list[dict], dict[str, int]]:
    """Artifacts newest first, with where each stands now, and the count of each kind (before the kind filter).
    Across sessions the same file, link or commit is one item (its latest version) that knows how many sessions made it.
    project: one project's path, or a group's paths (a list)."""
    where, params = [], []
    if session_id:
        where.append("a.session_id = ?")
        params.append(session_id)
    if project:
        paths = [project] if isinstance(project, str) else project
        where.append(f"s.project_path IN ({','.join('?' * len(paths))})")
        params += paths
    if agent:
        where.append("s.agent = ?")
        params.append(agent)
    if q:
        where.append("(LOWER(COALESCE(a.title, '')) LIKE ? OR LOWER(COALESCE(a.path, '')) LIKE ? OR LOWER(COALESCE(a.url, '')) LIKE ?)")
        params += [f"%{q.lower()}%"] * 3
    rows = conn.execute(
        "SELECT a.*, s.project_path, s.project_name, s.agent, s.title AS session_title, s.machine_id, s.archive_path FROM artifacts a "
        f"JOIN sessions s ON s.id = a.session_id {'WHERE ' + ' AND '.join(where) if where else ''} "
        "ORDER BY COALESCE(a.ts, s.started_at) DESC, a.id DESC", params).fetchall()
    items: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
        key = d["key"] if not session_id else f"{d['key']}#{d['id']}"
        first = items.get(key)
        if first is None:
            d["session_ids"] = [d["session_id"]]
            items[key] = d
        else:  # an older version from another session
            if d["session_id"] not in first["session_ids"]:
                first["session_ids"].append(d["session_id"])
            first["versions"] += d["versions"] or 1
    counts: dict[str, int] = {}
    for d in items.values():
        counts[d["kind"]] = counts.get(d["kind"], 0) + 1
    out = [d for d in items.values() if not kind or d["kind"] == kind]
    for d in out:
        d["sessions"] = len(d.pop("session_ids"))
        d["status"] = status_of(d, local_machine)
        d["preview"] = previewable(d)
        d["openable"] = openable(d)
        d.pop("machine_id", None)
        d.pop("archive_path", None)
    return out, counts


def previewable(a: dict) -> bool:
    """An image or SVG still on disk, or a deck, document, spreadsheet or PDF Quick Look can draw (on a Mac)."""
    ext = os.path.splitext(a.get("path") or "")[1].lower()
    return a["status"] in ("present", "changed") and (ext in PREVIEW_TYPES or (ext in THUMB_EXT and can_thumbnail()))


def quicklook_thumbnail(path: str, cache_dir, size: int = 800) -> bytes | None:
    """A PNG of a file's first page or slide, drawn by macOS Quick Look and kept until the file changes."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    try:
        st = os.stat(path)
    except OSError:
        return None
    out = Path(cache_dir) / (hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{size}".encode()).hexdigest() + ".png")
    if not out.exists():
        with tempfile.TemporaryDirectory() as tmp:
            try:
                subprocess.run(["qlmanage", "-t", "-s", str(size), "-o", tmp, path], capture_output=True, timeout=30,
                               stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                return None
            made = Path(tmp) / f"{os.path.basename(path)}.png"
            if not made.exists():
                return None
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(made, out)
    try:
        return out.read_bytes()
    except OSError:
        return None


def artifact_by_id(conn, artifact_id: int, local_machine: str | None = None) -> dict | None:
    """One recorded artifact with where it stands now; the only way a request reaches a file."""
    row = conn.execute("SELECT a.*, s.agent, s.machine_id, s.archive_path FROM artifacts a JOIN sessions s ON s.id = a.session_id "
                       "WHERE a.id = ?", (artifact_id,)).fetchone()
    if row is None:
        return None
    a = dict(row)
    a["meta"] = json.loads(a.pop("meta_json") or "{}")
    a["status"] = status_of(a, local_machine)
    return a


def preview_file(conn, artifact_id: int, local_machine: str | None = None, cache_dir=None) -> tuple[bytes, str] | None:
    """An artifact's picture by its id (only a file Interlatch recorded as an artifact, never a path a request names): an
    image or SVG as it is, or a Quick Look thumbnail of a deck, document, spreadsheet or PDF."""
    a = artifact_by_id(conn, artifact_id, local_machine)
    if a is None or a["status"] not in ("present", "changed"):
        return None
    ext = os.path.splitext(a.get("path") or "")[1].lower()
    if ext in THUMB_EXT and cache_dir and can_thumbnail():
        png = quicklook_thumbnail(a["path"], cache_dir)
        return (png, "image/png") if png else None
    ctype = PREVIEW_TYPES.get(ext)
    if not ctype:
        return None
    try:
        if os.path.getsize(a["path"]) > PREVIEW_MAX:
            return None
        with open(a["path"], "rb") as f:
            return f.read(), ctype
    except OSError:
        return None


def openable(a: dict) -> bool:
    """Whether "Open" can show the file: it is on disk, or it is gone but the archived transcript holds what was written."""
    if not a.get("path") or a["kind"] in ("pr", "published", "commit"):
        return False
    if a["status"] in ("present", "changed"):
        return True
    return (a["status"] == "gone" and bool(a.get("sha256")) and a.get("agent") in ("claude", "codex")
            and bool(a.get("archive_path")) and os.path.exists(a["archive_path"]))
