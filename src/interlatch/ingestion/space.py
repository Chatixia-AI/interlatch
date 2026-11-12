"""How much disk Interlatch uses, and keeping the transcripts of older sessions in another folder ([storage]).

A session moves once it is older than [storage] older_after_days and the agent's own transcript is gone, so the
archive's copy is the only one and nothing writes to it again. Its files keep their place under the other folder
(<older_dir>/claude/projects/...), and the database records where they are: everything that reads an archived
transcript goes by that path, so a moved session opens as before while the folder is reachable.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

from ..core.config import Config
from ..core.util import file_lock, to_iso, utcnow
from ..sources.claude.files import session_dir_for

log = logging.getLogger("interlatch.storage")

PARTS = ("database", "archive", "backups", "notes", "logs", "other")  # what usage() tells apart, in this order
RUN_BYTES = 1 << 30  # a background run moves at most this much, so a first run never holds the sync for long


class StorageError(Exception):
    pass


def _tree_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


def usage(cfg: Config, conn: sqlite3.Connection | None = None) -> dict:
    """Bytes per part of Interlatch's folder, the older-sessions folder, and whether the total passed the limit."""
    home = cfg.home
    sizes = dict.fromkeys(PARTS, 0)
    notes_inside = cfg.notes_dir.resolve().is_relative_to(home.resolve())
    if home.is_dir():
        for entry in os.scandir(home):
            try:
                size = _tree_bytes(Path(entry.path)) if entry.is_dir(follow_symlinks=False) else entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            name = entry.name
            part = ("database" if name.startswith(cfg.db_path.name) else name if name in ("archive", "backups", "logs")
                    else "notes" if notes_inside and Path(entry.path).resolve() == cfg.notes_dir.resolve() else "other")
            sizes[part] += size
    if cfg.archive_home:  # a profile reads Personal's database and archive: counted there, not here
        sizes["database"] = sizes["archive"] = 0
    if not notes_inside and cfg.notes_dir.is_dir():
        sizes["notes"] = _tree_bytes(cfg.notes_dir)
    total = sum(sizes.values())
    older = cfg.storage_older_dir
    limit = int(cfg.storage_warn_gb * (1 << 30))
    return {
        "home": str(home), "parts": [{"key": k, "bytes": sizes[k]} for k in PARTS], "total": total,
        "warn_gb": cfg.storage_warn_gb, "over": bool(limit and total > limit),
        "older": {"dir": str(older) if older else "", "days": cfg.storage_older_days,
                  "reachable": bool(older and older.is_dir()),
                  "bytes": _tree_bytes(older) if older and older.is_dir() else None,
                  "sessions": len(_moved(conn, older)) if conn is not None and older else 0},
    }


def check_older_dir(cfg: Config, folder: str) -> Path:
    """The folder older sessions go to, as given in the settings: an existing, writable folder outside Interlatch's."""
    path = Path(folder).expanduser()
    if not path.is_absolute():
        raise StorageError("give the folder's full path")
    if not path.is_dir():
        raise StorageError(f"{path} is not a folder; create it first")
    real, home = path.resolve(), (cfg.archive_home or cfg.home).resolve()
    if real == home or real.is_relative_to(home) or home.is_relative_to(real):
        raise StorageError("pick a folder outside Interlatch's own")
    if not os.access(real, os.W_OK):
        raise StorageError(f"{path} can't be written to")
    return path


def _under(path: str, root: Path) -> bool:
    return path.startswith(str(root).rstrip("/\\") + os.sep)


def _candidates(conn: sqlite3.Connection, cfg: Config) -> list[tuple[str, str, Path]]:
    """(id, agent, archived transcript) of each session old enough to move: its own transcript is gone, the archive's
    copy is this session's alone and still in Interlatch's folder. Oldest first."""
    cutoff = to_iso(utcnow() - timedelta(days=cfg.storage_older_days))
    rows = conn.execute(
        "SELECT id, agent, archive_path FROM sessions WHERE source_present = 0 AND archive_path IS NOT NULL "
        "AND COALESCE(ended_at, started_at) < ? AND archive_path IN "
        "(SELECT archive_path FROM sessions GROUP BY archive_path HAVING COUNT(*) = 1) "
        "ORDER BY COALESCE(ended_at, started_at)", (cutoff,)).fetchall()
    return [(r[0], r[1], Path(r[2])) for r in rows
            if _under(r[2], cfg.archive_dir) and r[2].endswith(".gz") and os.path.isfile(r[2])]


def _moved(conn: sqlite3.Connection, older: Path) -> list[tuple[str, str, Path]]:
    rows = conn.execute("SELECT id, agent, archive_path FROM sessions WHERE archive_path IS NOT NULL").fetchall()
    return [(r[0], r[1], Path(r[2])) for r in rows if _under(r[2], older)]


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _copy_checked(src: Path, dst: Path) -> None:
    """Copy src to dst and prove the copy: dst appears only once it holds exactly what src holds."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + f".tmp{os.getpid()}")
    try:
        shutil.copyfile(src, tmp)
        if _digest(tmp) != _digest(src):
            raise StorageError(f"the copy of {src} in {dst.parent} doesn't match it")
        shutil.copystat(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _session_files(agent: str, main: Path) -> list[Path]:
    """A session's files in the archive: its transcript and, for Claude Code, the folder beside it."""
    sdir = session_dir_for(main)
    extra = sorted(p for p in sdir.rglob("*") if p.is_file()) if agent == "claude" and sdir.is_dir() else []
    return [main, *extra]


def _relocate(conn: sqlite3.Connection, sid: str, agent: str, main: Path, src_root: Path, dst_root: Path) -> int:
    """Move one session's files from under src_root to the same place under dst_root. The copies are checked, then
    the database is pointed at them, and only then are the originals removed: stopped at any point, the session still
    opens. Returns the bytes moved."""
    files = _session_files(agent, main)
    pairs = [(f, dst_root / f.relative_to(src_root)) for f in files]
    size = 0
    for src, dst in pairs:
        _copy_checked(src, dst)
        size += src.stat().st_size
    try:
        conn.execute("UPDATE sessions SET archive_path = ? WHERE id = ?", (str(pairs[0][1]), sid))
        for src, dst in pairs:
            conn.execute("UPDATE files_state SET archive_path = ? WHERE archive_path = ?", (str(dst), str(src)))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    for src, _dst in pairs:
        src.unlink(missing_ok=True)
    sdir = session_dir_for(main)
    if agent == "claude" and sdir.is_dir():
        shutil.rmtree(sdir, ignore_errors=True)  # only empty folders are left in it
    return size


def _run(cfg: Config, conn: sqlite3.Connection, sessions, src_root: Path, dst_root: Path, *, limit: int | None,
         dry_run: bool, progress) -> dict:
    out = {"sessions": 0, "bytes": 0, "left": 0, "errors": []}
    for i, (sid, agent, main) in enumerate(sessions):
        if limit is not None and out["bytes"] >= limit:
            out["left"] = len(sessions) - i
            break
        try:
            if dry_run:
                size = sum(f.stat().st_size for f in _session_files(agent, main))
            else:
                size = _relocate(conn, sid, agent, main, src_root, dst_root)
        except (OSError, StorageError, sqlite3.Error) as exc:
            log.warning("moving session %s failed: %s", sid, exc)
            out["errors"].append(f"{sid}: {exc}")
            if isinstance(exc, OSError) and not dst_root.is_dir():
                break  # the folder went away (a disk unplugged): nothing else will work either
            continue
        out["sessions"] += 1
        out["bytes"] += size
        if progress and out["sessions"] % 20 == 0:
            progress(f"{out['sessions']} of {len(sessions)} sessions")
    return out


def _locked(cfg: Config, work, *, wait: float) -> dict:
    with file_lock(cfg.locks_dir / "ingest.lock", timeout=wait) as got:  # never while a sync archives or parses
        if not got:
            return {"sessions": 0, "bytes": 0, "left": 0, "errors": [], "note": "a sync is running; try again in a minute"}
        from ..storage.sqlite import connect

        conn = connect(cfg.db_path)
        try:
            return work(conn)
        finally:
            conn.rollback()
            conn.close()


def move_older(cfg: Config, *, limit: int | None = None, dry_run: bool = False, progress=None, wait: float = 5) -> dict:
    """Move the transcripts of sessions older than [storage] older_after_days to [storage] older_dir.

    Returns {"sessions", "bytes", "left", "errors"} and a "note" when nothing could be done. `limit` stops the run once
    that many bytes moved ("left" says how many sessions wait for the next run)."""
    older = cfg.storage_older_dir
    if not older or cfg.archive_home:
        return {"sessions": 0, "bytes": 0, "left": 0, "errors": [], "note": "no folder for older sessions is set"}
    try:
        check_older_dir(cfg, str(older))
    except StorageError as exc:
        return {"sessions": 0, "bytes": 0, "left": 0, "errors": [], "note": str(exc)}
    return _locked(cfg, lambda conn: _run(cfg, conn, _candidates(conn, cfg), cfg.archive_dir, older, limit=limit,
                                          dry_run=dry_run, progress=progress), wait=wait)


def bring_back(cfg: Config, *, progress=None, wait: float = 5) -> dict:
    """Move every session kept in [storage] older_dir back into Interlatch's own archive."""
    older = cfg.storage_older_dir
    if not older or cfg.archive_home:
        return {"sessions": 0, "bytes": 0, "left": 0, "errors": [], "note": "no folder for older sessions is set"}
    if not older.is_dir():
        return {"sessions": 0, "bytes": 0, "left": 0, "errors": [], "note": f"{older} can't be reached"}

    def work(conn):
        sessions = [s for s in _moved(conn, older) if s[2].is_file()]
        return _run(cfg, conn, sessions, older, cfg.archive_dir, limit=None, dry_run=False, progress=progress)

    return _locked(cfg, work, wait=wait)


def human_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def summary(res: dict) -> str:
    """One line for the log and the command line."""
    if res.get("note") and not res["sessions"]:
        return res["note"]
    parts = [f"{res['sessions']} moved ({human_bytes(res['bytes'])})"]
    if res["left"]:
        parts.append(f"{res['left']} more next run")
    if res["errors"]:
        parts.append(f"{len(res['errors'])} failed")
    return ", ".join(parts)
