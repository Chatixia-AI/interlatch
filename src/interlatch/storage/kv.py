"""Small settings and state kept in the archive's kv table, by key."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager

_writing = threading.RLock()


def kv_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def kv_set(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute(
        "INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


@contextmanager
def kv_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """One read-modify-write of kv keys that two quick requests can't interleave (each request thread has its own
    connection): BEGIN IMMEDIATE takes SQLite's write lock before anything is read, so another writer waits for the
    commit (busy_timeout) and then reads what this one wrote. Committed at the end; rolled back on an error. Already in
    a transaction, it goes on in that one (and commits it)."""
    with _writing:
        own = not conn.in_transaction
        if own:
            conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            if own:
                conn.rollback()
            raise
        conn.commit()
