"""The team's record in Postgres: what the computers that share with a hub learned, merged across them.

With `[hub] store = "postgres"` the hub keeps every session a computer shares (`[hub] share = "knowledge"`) and its
project lessons here as well as in its own SQLite, which stays the copy the dashboard reads. The hub is the only
writer: members' computers never connect to the database. They get their teammates' lessons for the projects they
work on from the hub API (hub.team_lessons).

A lesson belongs to a place: the git repository it was learned in (its normalized remote), wherever each member
cloned it, or the hub project of a folder without a remote. Lessons in the same place merge when their kind and title
match (after normalizing case and punctuation); each keeps the sessions and computers that stated it
(lesson_sources): how many confirm it, and whom to ask.

The connection is read from PG* lines (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD, PGSSLMODE) in
<interlatch home>/team-store.env, else from the PG* environment. It needs the Postgres driver, which the `team` extra
installs on the hub only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import pg
from .config import Config
from .pg import PG_KEYS, SETTINGS, SSLMODES, driver_available  # noqa: F401 (this module's names, as before)
from .util import fingerprint

log = logging.getLogger("interlatch.team_store")

ENV_FILE = "team-store.env"
MAX_LESSONS = 2000  # sent to one computer per pull
MAX_SOURCES = 50    # session ids per lesson sent with it (its confirmations)
PING_AFTER_S = 60   # check an idle connection before using it again

# Named upgrade steps: each runs once, in this order, recorded by name in <schema>.migrations. Append, never edit.
STEPS: list[tuple[str, str]] = [
    ("0001-team-tables", """
CREATE TABLE {s}.computers (
    id uuid PRIMARY KEY,
    name text,
    platform text,
    version text,
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE {s}.sessions (
    id text PRIMARY KEY,
    computer_id uuid NOT NULL REFERENCES {s}.computers(id),
    project text,
    project_name text,
    remote text,
    agent text,
    started_at text,
    ended_at text,
    title text,
    summary text,
    goal text,
    outcome text,
    analyzed_at text,
    analysis_model text,
    language text,
    details jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    received_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sessions_computer ON {s}.sessions(computer_id);
CREATE INDEX sessions_project ON {s}.sessions(project);
CREATE INDEX sessions_remote ON {s}.sessions(remote);
CREATE TABLE {s}.lessons (
    id bigserial PRIMARY KEY,
    place text NOT NULL,
    project text,
    project_name text,
    remote text,
    key text NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    body text,
    tags jsonb,
    language text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (place, key)
);
CREATE TABLE {s}.lesson_sources (
    lesson_id bigint NOT NULL REFERENCES {s}.lessons(id) ON DELETE CASCADE,
    session_id text NOT NULL REFERENCES {s}.sessions(id) ON DELETE CASCADE,
    computer_id uuid NOT NULL REFERENCES {s}.computers(id),
    seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lesson_id, session_id)
);
CREATE INDEX lesson_sources_session ON {s}.lesson_sources(session_id);
CREATE INDEX lesson_sources_computer ON {s}.lesson_sources(computer_id);
CREATE TABLE {s}.audit (
    id bigserial PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now(),
    computer_id uuid,
    action text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{{}}'::jsonb
);
"""),
]

SESSION_COLS = ("project", "project_name", "remote", "agent", "started_at", "ended_at", "title", "summary", "goal",
                "outcome", "analyzed_at", "analysis_model", "language")


class TeamStoreError(pg.PgError):
    pass


def env_path(cfg: Config) -> Path:
    return cfg.home / ENV_FILE


def connection_params(cfg: Config) -> dict:
    """psycopg connect() arguments from team-store.env, else the PG* environment. Never logged: it holds a password."""
    params = pg.params_from(env_path(cfg))
    if not params:
        raise TeamStoreError(f"no team database configured: put PGHOST, PGDATABASE, PGUSER and PGPASSWORD in {env_path(cfg)}")
    return params


def read_settings(cfg: Config) -> dict | None:
    """The connection as the dashboard shows it: everything but the password, which is only said to be there."""
    try:
        return pg.shown(connection_params(cfg))
    except (TeamStoreError, OSError):
        return None


def check_settings(values: dict, saved: dict | None) -> dict:
    """psycopg connect() arguments from what the dashboard sent: a password left empty keeps the saved one."""
    return pg.check_settings(values, saved, TeamStoreError)


def write_settings(cfg: Config, params: dict) -> None:
    """Write team-store.env (readable by this user only), replacing it in one step."""
    pg.write_settings(env_path(cfg), params, "The hub's team store ([hub] store = \"postgres\"). Written by the dashboard; keep it private.")


def probe(params: dict, *, schema: str = "team") -> dict:
    """Connect once with these settings and say what is there, creating nothing: the server's version, and the team
    store's upgrade steps and counts when it exists already."""
    try:
        import psycopg
    except ImportError:
        raise TeamStoreError("the team store needs the Postgres driver on the hub: install Interlatch with the team "
                             "extra (uv tool install 'interlatch[team]'), then restart the dashboard") from None
    where = f"{params.get('dbname')} on {params.get('host')}"
    try:
        with psycopg.connect(**params, connect_timeout=15, autocommit=True, application_name="interlatch-hub") as c:
            server = c.execute("SHOW server_version").fetchone()[0]
            exists = c.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.migrations",)).fetchone()[0]
            steps = [r[0] for r in c.execute(f"SELECT name FROM {schema}.migrations ORDER BY name")] if exists else []
            counts = {t: c.execute(f"SELECT count(*) FROM {schema}.{t}").fetchone()[0]
                      for t in ("computers", "sessions", "lessons")} if steps else {}
            can_create = c.execute("SELECT has_database_privilege(current_database(), 'CREATE')").fetchone()[0]
    except psycopg.Error as exc:
        raise TeamStoreError(f"can't reach the team store ({where}): {_first_line(exc)}") from None
    return {"where": where, "server": server, "schema": schema, "steps": steps, "counts": counts,
            "can_create": bool(can_create or steps)}


class TeamStore:
    """One connection to the team database, shared by the hub's request threads (one at a time)."""

    def __init__(self, params: dict, *, schema: str = "team"):
        if not schema.isidentifier():
            raise TeamStoreError(f"bad schema name {schema!r}")
        self.params = params
        self.schema = schema
        self.lock = threading.Lock()
        self.conn = None
        self.used = 0.0
        self.migrated = False

    # -------------------------------------------------------------- connection
    def where(self) -> str:
        return f"{self.params.get('dbname')} on {self.params.get('host')}"

    def _connect(self):
        try:
            import psycopg
        except ImportError:
            raise TeamStoreError("the team store needs the Postgres driver on the hub: install Interlatch with the "
                                 "team extra (uv tool install 'interlatch[team]')") from None
        try:
            conn = psycopg.connect(**self.params, connect_timeout=15, autocommit=True, application_name="interlatch-hub")
        except psycopg.Error as exc:
            raise TeamStoreError(f"can't reach the team store ({self.where()}): {_first_line(exc)}") from None
        if not self.migrated:
            try:
                self._migrate(conn)
            except psycopg.Error as exc:
                conn.close()
                raise TeamStoreError(f"can't set up the team store ({self.where()}): {_first_line(exc)}") from None
            self.migrated = True
        return conn

    def _alive(self) -> bool:
        if self.conn is None or self.conn.closed or self.conn.broken:
            return False
        if time.monotonic() - self.used < PING_AFTER_S:
            return True
        try:
            self.conn.execute("SELECT 1")
            return True
        except Exception:  # the server dropped an idle connection: connect again
            return False

    @contextmanager
    def _session(self):
        """The connection, inside one transaction; database errors come out as TeamStoreError."""
        import psycopg

        with self.lock:
            if not self._alive():
                if self.conn is not None:
                    try:
                        self.conn.close()
                    except Exception:
                        pass
                self.conn = self._connect()
            try:
                with self.conn.transaction():
                    yield self.conn
            except psycopg.Error as exc:
                raise TeamStoreError(f"team store ({self.where()}): {_first_line(exc)}") from None
            finally:
                self.used = time.monotonic()

    def close(self) -> None:
        with self.lock:
            if self.conn is not None:
                self.conn.close()
                self.conn = None

    def _migrate(self, conn) -> None:
        s = self.schema
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"chronicle-team-store:{s}",))
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
            conn.execute(f"CREATE TABLE IF NOT EXISTS {s}.migrations "
                         "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            done = {r[0] for r in conn.execute(f"SELECT name FROM {s}.migrations")}
            for name, sql in STEPS:
                if name not in done:
                    log.info("team store: upgrade step %s", name)
                    conn.execute(sql.format(s=s))
                    conn.execute(f"INSERT INTO {s}.migrations(name) VALUES (%s)", (name,))

    # -------------------------------------------------------------- writes
    def computer_seen(self, machine_id: str, name: str | None, platform: str | None, version: str | None) -> None:
        s = self.schema
        with self._session() as c:
            c.execute(f"INSERT INTO {s}.computers(id, name, platform, version) VALUES (%s, %s, %s, %s) "
                      "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, platform = EXCLUDED.platform, "
                      "version = EXCLUDED.version, last_seen = now()", (machine_id, name, platform, version))

    def put_sessions(self, machine_id: str, sessions: list[dict]) -> dict:
        """Store sessions a computer shared, replacing what each one stated before; returns counts.

        Each session is a dict of SESSION_COLS plus `id`, `details` (a dict) and `lessons` ([{kind, title, body,
        tags}]). A lesson no session states any more is removed."""
        from psycopg.types.json import Jsonb

        s = self.schema
        n_lessons = 0
        with self._session() as c:
            c.execute(f"INSERT INTO {s}.computers(id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (machine_id,))
            for rec in sessions:
                sid = rec["id"]
                owner = c.execute(f"SELECT computer_id FROM {s}.sessions WHERE id = %s", (sid,)).fetchone()
                if owner and str(owner[0]) != machine_id:
                    log.warning("team store: session %s already came from another computer; not replaced", sid)
                    continue
                cols = ("id", "computer_id", *SESSION_COLS, "details")
                vals = (sid, machine_id, *(rec.get(k) for k in SESSION_COLS), Jsonb(rec.get("details") or {}))
                c.execute(f"INSERT INTO {s}.sessions({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                          "ON CONFLICT (id) DO UPDATE SET "
                          + ", ".join(f"{k} = EXCLUDED.{k}" for k in cols[2:]) + ", received_at = now()", vals)
                before = [r[0] for r in c.execute(f"DELETE FROM {s}.lesson_sources WHERE session_id = %s "
                                                  "RETURNING lesson_id", (sid,))]
                where = place(rec.get("remote"), rec.get("project"))
                for k in rec.get("lessons") if where else []:
                    key = fingerprint(k["kind"], k["title"])
                    lid = c.execute(
                        f"INSERT INTO {s}.lessons(place, project, project_name, remote, key, kind, title, body, tags, "
                        "language) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (place, key) DO UPDATE "
                        "SET project = EXCLUDED.project, project_name = EXCLUDED.project_name, title = EXCLUDED.title, "
                        "body = EXCLUDED.body, tags = EXCLUDED.tags, language = EXCLUDED.language, updated_at = now() "
                        "RETURNING id",
                        (where, rec.get("project"), rec.get("project_name"), rec.get("remote"), key, k["kind"],
                         k["title"], k.get("body"), Jsonb(k["tags"]) if k.get("tags") else None,
                         rec.get("language"))).fetchone()[0]
                    c.execute(f"INSERT INTO {s}.lesson_sources(lesson_id, session_id, computer_id) VALUES (%s, %s, %s) "
                              "ON CONFLICT DO NOTHING", (lid, sid, machine_id))
                    n_lessons += 1
                if before:
                    c.execute(f"DELETE FROM {s}.lessons l WHERE l.id = ANY(%s) AND NOT EXISTS "
                              f"(SELECT 1 FROM {s}.lesson_sources x WHERE x.lesson_id = l.id)", (before,))
            c.execute(f"INSERT INTO {s}.audit(computer_id, action, detail) VALUES (%s, 'share', %s)",
                      (machine_id, Jsonb({"sessions": len(sessions), "lessons": n_lessons})))
        return {"sessions": len(sessions), "lessons": n_lessons}

    def forget_sessions(self, session_ids: list[str], computer_id: str | None = None) -> int:
        """Remove sessions (a hub's `interlatch hub purge`) and the lessons only they stated; returns how many went.
        With `computer_id` (a computer taking back what it shared), only the sessions that computer sent."""
        from psycopg.types.json import Jsonb

        s = self.schema
        with self._session() as c:
            if computer_id is not None:
                session_ids = [r[0] for r in c.execute(f"SELECT id FROM {s}.sessions WHERE id = ANY(%s) AND "
                                                       "computer_id = %s", (session_ids, computer_id))]
            lessons = [r[0] for r in c.execute(f"SELECT DISTINCT lesson_id FROM {s}.lesson_sources "
                                               "WHERE session_id = ANY(%s)", (session_ids,))]
            gone = c.execute(f"DELETE FROM {s}.sessions WHERE id = ANY(%s)", (session_ids,)).rowcount
            if lessons:  # their lesson_sources went with them (ON DELETE CASCADE)
                c.execute(f"DELETE FROM {s}.lessons l WHERE l.id = ANY(%s) AND NOT EXISTS "
                          f"(SELECT 1 FROM {s}.lesson_sources x WHERE x.lesson_id = l.id)", (lessons,))
            c.execute(f"INSERT INTO {s}.audit(computer_id, action, detail) VALUES (NULL, 'purge', %s)",
                      (Jsonb({"sessions": gone}),))
        return gone

    # -------------------------------------------------------------- reads
    def shared(self, machine_id: str) -> dict[str, str | None]:
        """{session id: analyzed_at} for what this computer has shared: it resends a session only when it changed."""
        with self._session() as c:
            return {r[0]: r[1] for r in c.execute(f"SELECT id, analyzed_at FROM {self.schema}.sessions "
                                                  "WHERE computer_id = %s", (machine_id,))}

    def lessons_for(self, machine_id: str, remotes: list[str], projects: list[str], limit: int = MAX_LESSONS,
                    within: list[str] | None = None, left: list[str] | None = None) -> dict:
        """Teammates' lessons for the places this computer works in: the repositories (and folders without a remote)
        it shared sessions in, those whose git remote it has a clone of, and those in hub projects it added a folder
        to. Lessons it stated itself are left out (it has them). `within`: hub projects the asking person is limited
        to; then only lessons stated in sessions filed under them count, whatever the computer asked for. `left`: hub
        projects the computer left; nothing filed under them counts.

        Returns {"lessons": [...], "places": {place: {"remote", "project", "mine": a session id of this computer's
        there, or None}}}, which the computer uses to find each place's folder on its side."""
        from psycopg.types.json import Jsonb

        s = self.schema
        at = "coalesce(remote, project)"  # place(), in SQL
        with self._session() as c:
            limit_to = within is not None
            scope = [r[0] for r in c.execute(
                f"SELECT DISTINCT {at} FROM {s}.sessions WHERE coalesce({at}, '') <> '' AND "
                "(computer_id = %s OR remote = ANY(%s) OR project = ANY(%s)) AND (NOT %s OR project = ANY(%s)) "
                "AND NOT coalesce(project, '') = ANY(%s)",
                (machine_id, remotes, projects, limit_to, list(within or []), list(left or [])))]
            if not scope:
                return {"lessons": [], "places": {}}
            places = {r[0]: {"remote": r[1], "project": r[2], "mine": r[3]} for r in c.execute(
                f"SELECT {at}, max(remote), max(project), max(id) FILTER (WHERE computer_id = %s) "
                f"FROM {s}.sessions WHERE {at} = ANY(%s) GROUP BY 1", (machine_id, scope))}
            rows = c.execute(
                f"SELECT l.id, l.place, l.project, l.project_name, l.remote, l.kind, l.title, l.body, l.tags, "
                f"l.language, l.created_at, l.updated_at, count(*) AS n, "
                f"array_agg(DISTINCT coalesce(m.name, left(m.id::text, 8))) AS computers, "
                f"(array_agg(x.session_id ORDER BY x.seen_at DESC))[1:{MAX_SOURCES}] AS session_ids "
                f"FROM {s}.lessons l JOIN {s}.lesson_sources x ON x.lesson_id = l.id "
                f"JOIN {s}.computers m ON m.id = x.computer_id JOIN {s}.sessions ss ON ss.id = x.session_id "
                "WHERE l.place = ANY(%s) AND (NOT %s OR ss.project = ANY(%s)) AND NOT coalesce(ss.project, '') = ANY(%s) "
                "GROUP BY l.id HAVING bool_and(x.computer_id <> %s) ORDER BY count(*) DESC, l.updated_at DESC, l.id LIMIT %s",
                (scope, limit_to, list(within or []), list(left or []), machine_id, limit)).fetchall()
            lessons = [{"id": r[0], "place": r[1], "project": r[2], "project_name": r[3], "remote": r[4], "kind": r[5],
                        "title": r[6], "body": r[7], "tags": r[8], "language": r[9], "created_at": _iso(r[10]),
                        "updated_at": _iso(r[11]), "sessions": r[12], "computers": sorted(r[13] or []),
                        "session_ids": list(r[14] or [])} for r in rows]
            c.execute(f"INSERT INTO {s}.audit(computer_id, action, detail) VALUES (%s, 'pull', %s)",
                      (machine_id, Jsonb({"lessons": len(lessons), "places": len(scope)})))
        return {"lessons": lessons, "places": {p: places.get(p, {"remote": None, "project": p, "mine": None})
                                               for p in scope}}

    def status(self) -> dict:
        s = self.schema
        with self._session() as c:
            counts = {t: c.execute(f"SELECT count(*) FROM {s}.{t}").fetchone()[0]
                      for t in ("computers", "sessions", "lessons", "lesson_sources", "audit")}
            steps = [r[0] for r in c.execute(f"SELECT name FROM {s}.migrations ORDER BY name")]
            server = c.execute("SHOW server_version").fetchone()[0]
            last = c.execute(f"SELECT max(at) FROM {s}.audit").fetchone()[0]
        return {"where": self.where(), "schema": s, "server": server, "steps": steps, "counts": counts,
                "last_activity": _iso(last)}


def place(remote: str | None, project: str | None) -> str | None:
    """Where a lesson belongs: its repository (normalized git remote, e.g. github.com/org/app) wherever each member
    cloned it, else the hub project of a folder without one (an absolute path, so never mistaken for a remote)."""
    return remote or project or None


_iso, _first_line = pg.iso, pg.first_line


_stores: dict[str, tuple[tuple, TeamStore]] = {}
_stores_lock = threading.Lock()


def get(cfg: Config) -> TeamStore | None:
    """The hub's team store when `[hub] store` names one, else None. Raises TeamStoreError when it is set up wrong."""
    if cfg.hub_store != "postgres" or cfg.is_spoke:
        return None
    params = connection_params(cfg)
    key = tuple(sorted(params.items()))
    with _stores_lock:
        have = _stores.get(str(cfg.home))
        if have and have[0] == key:
            return have[1]
        if have:
            have[1].close()
        store = TeamStore(params)
        _stores[str(cfg.home)] = (key, store)
        return store


def payload(rec: dict, *, project: str | None, project_name: str | None, remote: str | None, title: str | None,
            details_cols: tuple[str, ...]) -> dict:
    """A shared session as put_sessions takes it, from what a computer sent (hub.receive_sessions checked it)."""
    lang = rec.get("language")
    out = {k: _text(rec.get(k)) for k in SESSION_COLS}
    out.update(id=rec["id"], project=project, project_name=project_name, remote=remote, title=title,
               language=lang if isinstance(lang, str) and len(lang) <= 8 else None,
               details={k: rec.get(k) for k in details_cols if rec.get(k) is not None})
    lessons = []
    for k in rec.get("knowledge") or []:
        if not isinstance(k, dict) or k.get("status") not in (None, "active"):
            continue  # a lesson its computer retired (merged into another, outdated) is not the team's
        tags = k.get("tags_json")
        try:
            tags = json.loads(tags) if isinstance(tags, str) else tags
        except ValueError:
            tags = None
        lessons.append({"kind": str(k["kind"])[:40], "title": str(k["title"])[:500], "body": _text(k.get("body")),
                        "tags": tags if isinstance(tags, list) else None})
    out["lessons"] = lessons
    return out


def _text(value) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value) if isinstance(value, (dict, list)) else str(value)
