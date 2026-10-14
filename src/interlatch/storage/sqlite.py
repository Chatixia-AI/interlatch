"""Connections to the SQLite archive (a writable one is brought up to the current schema first), and the two query
helpers every feature uses."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .migrations import init_schema


def connect(db_path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if readonly and db_path.exists():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    else:
        conn = sqlite3.connect(db_path, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        init_schema(conn)
    return conn


def rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None
