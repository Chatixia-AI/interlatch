"""A copy of your archive in a Postgres database you choose (`[mirror] to = "postgres"`): on this computer, in
Docker, or in the cloud.

Interlatch keeps working from its own SQLite, the only copy it reads. The mirror is for querying with SQL or a BI
tool, and for a copy kept somewhere else. It is one-way: the background run writes it after each pass (worker.py),
`interlatch mirror sync` on demand, and nothing is ever read back from it.

What it holds (`[mirror] include`):
- "knowledge" (default): every session's details, summary and analysis, your lessons, knowledge bases, weekly
  reviews, glossary, artifacts, token usage and the files sessions touched. No prompts, no transcript.
- "everything": the prompts and transcripts (events, tool calls) too, with secrets redacted as in a digest.
Sessions in projects you excluded (`[sources] exclude_projects`) never go, nor does Interlatch's own bookkeeping.

How it stays current: each row of a table with a key of its own (sessions, knowledge, ...) carries a hash of what it
was written from (`_hash`). A sync hashes the rows here, reads the mirror's hashes, and writes only the rows that
differ, removing those gone here. A session's own rows in other tables (its usage, files, artifacts, transcript) go
with it: a session whose row or any of them changed is written again whole. One computer owns a schema (`_mirror`
names it), so two computers never overwrite each other's copy.

The connection is read from PG* lines (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD, PGSSLMODE) in
<interlatch home>/mirror.env. The Postgres driver comes with the postgres extra.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from . import pg
from .config import Config
from .db import connect, kv_get, kv_set
from .util import file_lock, one_line, utcnow_iso

log = logging.getLogger("interlatch.mirror")

ENV_FILE = "mirror.env"
INCLUDES = ("knowledge", "everything")
VERSION = 1  # what a row becomes; raising it writes every row again
LAST_KV = "mirror:last"  # the last sync's outcome, for the dashboard and `interlatch mirror status`
LOCK = "mirror.lock"
BATCH = 100  # sessions written per transaction
KEYS_BATCH = 500  # rows of the other tables read and written at a time
INSTALL = "uv tool install 'interlatch[postgres]'"

# Tables whose rows have a key of their own: (table, key)
TABLES = (("machines", "id"), ("sessions", "id"), ("knowledge", "id"), ("project_kb", "project_path"),
          ("reviews", "period"), ("glossary", "id"), ("analyses", "id"))
# A session's own rows, written with it
CHILDREN = ("api_calls", "session_files", "subagents", "artifacts")
TRANSCRIPT = ("events", "tool_calls")  # with include = "everything" only
# Interlatch's bookkeeping: never copied
INTERNAL = {"sessions": {"claude_dir", "transcript_path", "archive_path", "source_present", "files_sig", "parser_version",
                         "analysis_attempts", "analysis_not_before", "screen_sig"},
            "machines": {"key_hash", "person_id"},
            "analyses": {"result_json"}}
# What quotes your prompts: with include = "everything" only
PROMPTS = {"sessions": {"first_prompt", "last_prompt"}, "subagents": {"description"}}
# Free text that can hold a secret: redacted on the way
REDACT = {"sessions": {"title", "first_prompt", "last_prompt"}, "subagents": {"description"},
          "events": {"text", "meta_json"}, "tool_calls": {"summary", "command"}}
TIME_COLS = {"ts", "first_seen", "last_seen", "last_push"}  # and every column named *_at


class MirrorError(pg.PgError):
    pass


def env_path(cfg: Config) -> Path:
    return cfg.home / ENV_FILE


def connection_params(cfg: Config) -> dict:
    """psycopg connect() arguments from mirror.env. Never logged: it holds a password."""
    params = pg.params_from(env_path(cfg), environ=False)
    if not params:
        raise MirrorError(f"no mirror database configured: put PGHOST, PGDATABASE, PGUSER and PGPASSWORD in {env_path(cfg)}")
    return params


def read_settings(cfg: Config) -> dict | None:
    try:
        return pg.shown(connection_params(cfg))
    except (MirrorError, OSError):
        return None


def check_settings(values: dict, saved: dict | None) -> dict:
    return pg.check_settings(values, saved, MirrorError)


def write_settings(cfg: Config, params: dict) -> None:
    pg.write_settings(env_path(cfg), params, "Interlatch's mirror ([mirror] to = \"postgres\"). Written by the dashboard; keep it private.")


def enabled(cfg: Config) -> bool:
    return cfg.mirror_to == "postgres"


# ------------------------------------------------------------------ what goes, and as what
def tables(include: str) -> list[str]:
    return [t for t, _ in TABLES] + list(CHILDREN) + (list(TRANSCRIPT) if include == "everything" else [])


def _columns(local: sqlite3.Connection, table: str, include: str) -> list[tuple[str, str, bool]]:
    """(name, Postgres type, part of the key) of each column the mirror keeps of a local table."""
    skip = INTERNAL.get(table, set()) | (PROMPTS.get(table, set()) if include != "everything" else set())
    out = []
    for _cid, name, ctype, _notnull, _default, pk in local.execute(f"PRAGMA table_info({table})"):
        if name in skip:
            continue
        ctype = (ctype or "").upper()
        if name.endswith("_json"):
            typ = "jsonb"
        elif name.endswith("_at") or name in TIME_COLS:
            typ = "timestamptz"
        elif "INT" in ctype:
            typ = "bigint"
        elif "REAL" in ctype:
            typ = "double precision"
        else:
            typ = "text"
        out.append((name, typ, bool(pk)))
    return out


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _clean(text: str) -> str:
    return text.replace("\x00", "")  # Postgres text can't hold NUL


def _value(value, typ: str, redact: bool):
    from psycopg.types.json import Jsonb

    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        if redact:
            from .redact import redact as scrub

            value = scrub(value)
        value = _clean(value)
    if typ == "jsonb":
        if not isinstance(value, str):
            return Jsonb(value)
        try:
            return Jsonb(json.loads(value.replace("\\u0000", "")))
        except ValueError:
            return Jsonb(value)  # not JSON after all: kept as a JSON string
    if typ == "timestamptz":
        try:
            at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    if typ == "text" and not isinstance(value, str):
        return str(value)
    if typ in ("bigint", "double precision") and isinstance(value, str):  # SQLite keeps what it was given
        try:
            return int(value) if typ == "bigint" else float(value)
        except ValueError:
            return None
    return value


def _row(table: str, cols: list[tuple[str, str, bool]], rec: dict, include: str) -> tuple:
    redact = REDACT.get(table, set()) if include == "everything" else set()
    if table == "sessions" and include != "everything" and rec.get("title") and \
            rec["title"] == one_line(rec.get("first_prompt") or "(no prompt)", 90):
        rec = {**rec, "title": None}  # a title that is only the first prompt's opening: that's a prompt
    return tuple(_value(rec.get(name), typ, name in redact) for name, typ, _pk in cols)


def _hash(*parts) -> str:
    return hashlib.blake2b(repr(parts).encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()


# ------------------------------------------------------------------ connecting
def _connect(params: dict):
    try:
        import psycopg
    except ImportError:
        raise MirrorError(f"the mirror needs the Postgres driver: install Interlatch with the postgres extra ({INSTALL})") from None
    try:
        return psycopg.connect(**params, connect_timeout=15, autocommit=True, application_name="interlatch-mirror")
    except psycopg.Error as exc:
        raise MirrorError(f"can't reach the mirror database ({pg.where(params)}): {pg.first_line(exc)}") from None


def probe(params: dict, schema: str) -> dict:
    """Connect once with these settings and say what is there, creating nothing: the server's version, whether this
    schema holds a mirror already and whose, and whether the user may create one."""
    import psycopg

    with _connect(params) as c:
        try:
            server = c.execute("SHOW server_version").fetchone()[0]
            owner = None
            if c.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{_q(schema)}._mirror",)).fetchone()[0]:
                row = c.execute(f"SELECT computer_id, computer_name, include FROM {_q(schema)}._mirror").fetchone()
                owner = {"id": row[0], "name": row[1], "include": row[2]} if row else None
            schema_there = c.execute("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)", (schema,)).fetchone()[0]
            can_create = c.execute(
                "SELECT CASE WHEN %s THEN has_schema_privilege(%s, 'CREATE') "
                "ELSE has_database_privilege(current_database(), 'CREATE') END", (schema_there, schema)).fetchone()[0]
        except psycopg.Error as exc:
            raise MirrorError(f"mirror database ({pg.where(params)}): {pg.first_line(exc)}") from None
    return {"where": pg.where(params), "server": server, "schema": schema, "owner": owner, "can_create": bool(can_create)}


def _prepare(c, local: sqlite3.Connection, schema: str, include: str, me: dict) -> dict[str, list]:
    """Create or bring up to date the mirror's schema and tables, in one transaction; returns each table's columns.
    Refuses a schema another computer mirrors into."""
    s = _q(schema)
    want = {t: _columns(local, t, include) for t in tables(include)}
    with c.transaction():
        c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"chronicle-mirror:{schema}",))
        c.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
        c.execute(f"CREATE TABLE IF NOT EXISTS {s}._mirror (computer_id text PRIMARY KEY, computer_name text, "
                  "include text, chronicle_version text, version integer, synced_at timestamptz)")
        owner = c.execute(f"SELECT computer_id, computer_name FROM {s}._mirror").fetchone()
        if owner and owner[0] != me["id"]:
            raise MirrorError(f"schema {schema} holds the mirror of another computer ({owner[1] or owner[0]}); "
                              "set [mirror] schema to a schema of this computer's own")
        if not owner:
            c.execute(f"INSERT INTO {s}._mirror(computer_id, computer_name) VALUES (%s, %s)", (me["id"], me.get("name")))
        have: dict[str, dict[str, str]] = {}
        for t, col, typ in c.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                                     "WHERE table_schema = %s", (schema,)):
            have.setdefault(t, {})[col] = typ
        for t in TRANSCRIPT:  # include = "knowledge" again: the transcript goes
            if t not in want and t in have:
                c.execute(f"DROP TABLE {s}.{_q(t)}")
        for t, cols in want.items():
            keyed = t in dict(TABLES)
            if t not in have:
                defs = [f"{_q(n)} {typ}" for n, typ, _pk in cols]
                if keyed:
                    defs += ["_hash text NOT NULL", "_synced_at timestamptz NOT NULL DEFAULT now()"]
                pk = [_q(n) for n, _typ, is_pk in cols if is_pk]
                if pk:
                    defs.append(f"PRIMARY KEY ({', '.join(pk)})")
                c.execute(f"CREATE TABLE {s}.{_q(t)} ({', '.join(defs)})")
                if not keyed:
                    c.execute(f"CREATE INDEX ON {s}.{_q(t)} (session_id)")
                continue
            names = {n for n, _typ, _pk in cols} | ({"_hash", "_synced_at"} if keyed else set())
            for n, typ, _pk in cols:
                if n not in have[t]:
                    c.execute(f"ALTER TABLE {s}.{_q(t)} ADD COLUMN {_q(n)} {typ}")
            for n in have[t]:
                if n not in names:  # a prompt column after include went back to "knowledge", or one Interlatch dropped
                    c.execute(f"ALTER TABLE {s}.{_q(t)} DROP COLUMN {_q(n)}")
    return want


# ------------------------------------------------------------------ syncing
class Result(dict):
    def summary(self) -> str:
        if self.get("error"):
            return f"not written ({self['error']})"
        if self.get("note"):
            return self["note"]
        rows = self.get("rows") or {}
        parts = [f"{self.get('sessions', 0)} sessions written", f"{self.get('removed', 0)} removed"]
        others = sum(n for t, n in rows.items() if t != "sessions")
        if others:
            parts.append(f"{others} other rows")
        return ", ".join(parts)


def sync(cfg: Config, *, full: bool = False, progress=None) -> Result:
    """Bring the mirror up to date with this computer's database. Never raises for a database it can't reach: the
    outcome (and any error) is returned and kept for the dashboard; the next run catches up. `full` writes every row
    again, whatever the mirror holds."""
    if not enabled(cfg):
        return Result(note="the mirror is off")
    with file_lock(cfg.locks_dir / LOCK, blocking=False) as got:
        if not got:
            return Result(note="another mirror sync is running")
        started = time.monotonic()
        try:
            res = _sync(cfg, progress, full)
        except MirrorError as exc:
            res = Result(error=str(exc))
        except Exception as exc:  # a database error mid-way: what was committed stays, the rest is redone next time
            log.exception("mirror sync failed")
            res = Result(error=pg.first_line(exc))
        res["seconds"] = round(time.monotonic() - started, 1)
        res["at"] = utcnow_iso()
        res["include"] = cfg.mirror_include
        try:
            conn = connect(cfg.db_path)
            try:
                kv_set(conn, LAST_KV, json.dumps(res))
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            log.warning("mirror: could not record the outcome", exc_info=True)
        log.info("mirror: %s", res.summary())
        return res


def _sync(cfg: Config, progress, full: bool = False) -> Result:
    from . import __version__
    from .hub import local_machine

    params = connection_params(cfg)
    schema, include = cfg.mirror_schema, cfg.mirror_include
    say = progress or (lambda _m: None)
    me = local_machine(cfg)
    local = connect(cfg.db_path, readonly=True)
    try:
        with _connect(params) as c:
            say(f"connected to {pg.where(params)}")
            cols = _prepare(c, local, schema, include, me)
            res = Result(sessions=0, removed=0, rows={})
            plan = {t: _plan(c, local, cfg, schema, t, key, include, full) for t, key in TABLES}
            _remove(c, schema, plan, include)
            res["removed"] = sum(len(p["gone"]) for p in plan.values())
            for t, key in TABLES:
                if t != "sessions" and plan[t]["write"]:
                    say(f"{t}: writing {len(plan[t]['write'])}")
                    _write_rows(c, local, schema, t, key, cols[t], plan[t], include)
                    res["rows"][t] = len(plan[t]["write"])
            ids = plan["sessions"]["write"]
            for i in range(0, len(ids), BATCH):
                say(f"sessions: {min(i + BATCH, len(ids))} of {len(ids)}")
                _write_sessions(c, local, schema, ids[i:i + BATCH], cols, plan["sessions"], include)
            res["sessions"] = res["rows"]["sessions"] = len(ids)
            with c.transaction():
                c.execute(f"UPDATE {_q(schema)}._mirror SET computer_name = %s, include = %s, chronicle_version = %s, "
                          "version = %s, synced_at = now()", (me.get("name"), include, __version__, VERSION))
            res["where"] = pg.where(params)
            return res
    finally:
        local.close()


def _plan(c, local: sqlite3.Connection, cfg: Config, schema: str, table: str, key: str, include: str,
          full: bool = False) -> dict:
    """Which rows of a keyed table to write (`write`, with each one's hash in `hashes`) and which to remove (`gone`)."""
    hashes: dict = {}
    if table == "sessions":
        sigs: dict[str, list] = {}
        for child in [*CHILDREN, *(TRANSCRIPT if include == "everything" else ())]:
            for sid, n, top in local.execute(f"SELECT session_id, count(*), max(rowid) FROM {child} GROUP BY session_id"):
                sigs.setdefault(sid, []).append((child, n, top))
        for rec in local.execute("SELECT * FROM sessions"):
            if cfg.is_excluded(rec["project_path"]):
                continue
            hashes[rec["id"]] = _hash(VERSION, include, tuple(rec), sigs.get(rec["id"]))
    else:
        mirrored = [n for n, _typ, _pk in _columns(local, table, include)]
        has_project = table in ("knowledge", "project_kb")
        sel = ", ".join(dict.fromkeys([key, *(["project_path"] if has_project else []), *mirrored]))
        for rec in local.execute(f"SELECT {sel} FROM {table}"):
            if has_project and cfg.is_excluded(rec["project_path"]):
                continue
            hashes[rec[key]] = _hash(VERSION, include, tuple(rec))
    there = dict(c.execute(f"SELECT {_q(key)}, _hash FROM {_q(schema)}.{_q(table)}").fetchall())
    write = [k for k, h in hashes.items() if full or there.get(k) != h]
    gone = [k for k in there if k not in hashes]
    return {"key": key, "hashes": hashes, "write": write, "gone": gone, "replace": [k for k in write if k in there]}


def _remove(c, schema: str, plan: dict, include: str) -> None:
    """Remove, in one transaction, every row that goes or is about to be written again, and a session's own rows with
    it: no id a new row takes can still be held by an old one."""
    s = _q(schema)
    with c.transaction():
        for t, p in plan.items():
            drop = p["gone"] + p["replace"]
            if not drop:
                continue
            if t == "sessions":
                for child in [*CHILDREN, *(TRANSCRIPT if include == "everything" else ())]:
                    c.execute(f"DELETE FROM {s}.{_q(child)} WHERE session_id = ANY(%s)", (drop,))
            c.execute(f"DELETE FROM {s}.{_q(t)} WHERE {_q(p['key'])} = ANY(%s)", (drop,))


def _copy(c, schema: str, table: str, cols: list, rows, extra: tuple[str, ...] = ()) -> None:
    names = ", ".join(_q(n) for n, _typ, _pk in cols) + "".join(f", {_q(n)}" for n in extra)
    with c.cursor().copy(f"COPY {_q(schema)}.{_q(table)} ({names}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)


def _write_rows(c, local: sqlite3.Connection, schema: str, table: str, key: str, cols: list, p: dict, include: str) -> None:
    keys = p["write"]
    for i in range(0, len(keys), KEYS_BATCH):
        chunk = keys[i:i + KEYS_BATCH]
        recs = local.execute(f"SELECT * FROM {table} WHERE {key} IN ({', '.join('?' * len(chunk))})", chunk).fetchall()
        with c.transaction():
            _copy(c, schema, table, cols, ((*_row(table, cols, dict(r), include), p["hashes"][r[key]]) for r in recs),
                  extra=("_hash",))


def _write_sessions(c, local: sqlite3.Connection, schema: str, ids: list[str], cols: dict, p: dict, include: str) -> None:
    """Sessions and all their own rows, in one transaction."""
    marks = ", ".join("?" * len(ids))
    recs = local.execute(f"SELECT * FROM sessions WHERE id IN ({marks})", ids).fetchall()
    with c.transaction():
        _copy(c, schema, "sessions", cols["sessions"],
              ((*_row("sessions", cols["sessions"], dict(r), include), p["hashes"][r["id"]]) for r in recs), extra=("_hash",))
        for child in [*CHILDREN, *(TRANSCRIPT if include == "everything" else ())]:
            rows = local.execute(f"SELECT * FROM {child} WHERE session_id IN ({marks})", ids)
            _copy(c, schema, child, cols[child], (_row(child, cols[child], dict(r), include) for r in rows))


# ------------------------------------------------------------------ what's there
def last(cfg: Config) -> dict | None:
    """The last sync's outcome as this computer recorded it, without connecting."""
    try:
        conn = connect(cfg.db_path, readonly=True)
        try:
            raw = kv_get(conn, LAST_KV)
        finally:
            conn.close()
        return json.loads(raw) if raw else None
    except (sqlite3.Error, ValueError):
        return None


def status(cfg: Config) -> dict:
    """What the mirror holds: rows per table, its server, and whose it is. Raises MirrorError."""
    import psycopg

    params = connection_params(cfg)
    s = _q(cfg.mirror_schema)
    with _connect(params) as c:
        try:
            server = c.execute("SHOW server_version").fetchone()[0]
            if not c.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{s}._mirror",)).fetchone()[0]:
                return {"where": pg.where(params), "server": server, "schema": cfg.mirror_schema, "counts": {}, "owner": None}
            row = c.execute(f"SELECT computer_id, computer_name, include, chronicle_version, synced_at FROM {s}._mirror").fetchone()
            present = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                                               (cfg.mirror_schema,))}
            counts = {t: c.execute(f"SELECT count(*) FROM {s}.{_q(t)}").fetchone()[0] for t in tables("everything") if t in present}
        except psycopg.Error as exc:
            raise MirrorError(f"mirror database ({pg.where(params)}): {pg.first_line(exc)}") from None
    owner = {"id": row[0], "name": row[1], "include": row[2], "version": row[3], "synced_at": pg.iso(row[4])} if row else None
    return {"where": pg.where(params), "server": server, "schema": cfg.mirror_schema, "counts": counts, "owner": owner}
