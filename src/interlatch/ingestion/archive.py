"""The archive of raw transcripts: every file a source writes is copied in (JSONL gzip-compressed) and never removed
there, under a key per source root; signatures say when a session's files changed."""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import re
import shutil
import sqlite3
from pathlib import Path

from ..core.machine import MACHINE_RE
from ..core.util import utcnow_iso
from ..sources.claude.parser import PARSER_VERSION
from ..storage.kv import kv_get, kv_set
from .report import SyncReport
from ..core.config import Config

log = logging.getLogger("interlatch.ingest")

ROOT_RE = re.compile(r"^(claude|codex)(-\d+)?$")  # a source root received from another computer (sharing/receiver.py)


def archive_key(claude_dir: Path) -> str:
    if claude_dir.parent.parent.name == "machines" and MACHINE_RE.match(claude_dir.parent.name) and ROOT_RE.match(claude_dir.name):
        return f"machines/{claude_dir.parent.name}/{claude_dir.name}"  # received from another computer (sharing/)
    for name in ("claude", "codex"):
        if claude_dir.resolve() == Path(f"~/.{name}").expanduser().resolve():
            return name
    return re.sub(r"[^\w.-]+", "_", str(claude_dir).strip("/"))


class Archiver:
    def __init__(self, conn: sqlite3.Connection, report: SyncReport):
        self.conn = conn
        self.report = report
        self.state = {
            r[0]: (r[1], r[2], r[3])
            for r in conn.execute("SELECT path, size, mtime, archive_path FROM files_state").fetchall()
        }

    def archive(self, src: Path, dst: Path) -> Path:
        """Copy src into the archive if it changed; .jsonl files are gzip-compressed."""
        try:
            st = src.stat()
        except FileNotFoundError:
            return dst
        if src.suffix == ".jsonl":
            dst = dst.with_name(dst.name + ".gz")
        prev = self.state.get(str(src))
        if prev and prev[0] == st.st_size and prev[1] == st.st_mtime and dst.exists():
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + f".tmp{os.getpid()}")
        try:
            if src.suffix == ".jsonl":
                with open(src, "rb") as fin, gzip.open(tmp, "wb", compresslevel=6) as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
            else:
                shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            self.report.errors.append(f"archive {src}: {exc}")
            return dst
        self.conn.execute(
            "INSERT INTO files_state(path, size, mtime, archive_path, archived_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
            "archive_path=excluded.archive_path, archived_at=excluded.archived_at",
            (str(src), st.st_size, st.st_mtime, str(dst), utcnow_iso()),
        )
        self.conn.commit()  # never hold the write lock while compressing/parsing the next file
        self.state[str(src)] = (st.st_size, st.st_mtime, str(dst))
        self.report.files_archived += 1
        self.report.bytes_archived += st.st_size
        return dst

    def archive_tree(self, src_root: Path, dst_root: Path) -> None:
        for dirpath, dirnames, filenames in os.walk(src_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            rel = Path(dirpath).relative_to(src_root)
            for fn in filenames:
                if fn.startswith("."):
                    continue
                self.archive(Path(dirpath) / fn, dst_root / rel / fn)


def files_signature(main: Path, session_dir: Path) -> str:
    h = hashlib.sha1()
    parts = []
    for p in [main, *(sorted(session_dir.rglob("*")) if session_dir.is_dir() else [])]:
        if p.is_file() and (p.name.endswith((".jsonl", ".jsonl.gz")) or p.name.endswith(".json")):
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            parts.append(f"{p.name}:{st.st_size}:{int(st.st_mtime)}")
    h.update("|".join(parts).encode())
    h.update(f"v{PARSER_VERSION}".encode())
    return h.hexdigest()


def paths_signature(paths, version: int) -> str:
    h = hashlib.sha1()
    parts = []
    for p in paths:
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        parts.append(f"{p.name}:{st.st_size}:{int(st.st_mtime)}")
    h.update("|".join(parts).encode())
    h.update(f"v{version}".encode())
    return h.hexdigest()


def snapshot_sqlite(conn, src: Path, dst: Path) -> bool:
    """Archive a live SQLite database consistently (backup API), only when it or its WAL changed."""
    parts = []
    for f in (src, src.with_name(src.name + "-wal")):
        try:
            st = f.stat()
            parts.append(f"{st.st_size}:{int(st.st_mtime)}")
        except FileNotFoundError:
            continue
    sig, key = "|".join(parts), f"snapshot:{src}"
    if not parts or (kv_get(conn, key) == sig and dst.exists()):
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + f".tmp{os.getpid()}")
    tmp.unlink(missing_ok=True)
    try:
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=10)
        target = sqlite3.connect(tmp)
        with target:
            source.backup(target)
        target.close()
        source.close()
        os.replace(tmp, dst)
    except sqlite3.Error as exc:
        tmp.unlink(missing_ok=True)
        log.warning("snapshot of %s failed: %s", src, exc)
        return False
    kv_set(conn, key, sig)
    conn.commit()
    return True


def machine_roots(cfg: Config) -> list[tuple[str, str, Path]]:
    """(machine id, kind, folder) of every agent folder received from other computers."""
    out = []
    if not cfg.machines_dir.is_dir():
        return out
    for mdir in sorted(cfg.machines_dir.iterdir()):
        if not mdir.is_dir() or not MACHINE_RE.match(mdir.name):
            continue
        for root in sorted(mdir.iterdir()):
            m = ROOT_RE.match(root.name)
            if root.is_dir() and m:
                out.append((mdir.name, m.group(1), root))
    return out


def machine_of(cfg: Config, path: Path | str | None) -> str | None:
    """The id of the computer a received folder belongs to, or None for this computer's own folders."""
    if not path:
        return None
    try:
        rel = Path(path).resolve().relative_to(cfg.machines_dir.resolve())
    except (ValueError, OSError):
        return None
    first = rel.parts[0] if rel.parts else ""
    return first if MACHINE_RE.match(first) else None
