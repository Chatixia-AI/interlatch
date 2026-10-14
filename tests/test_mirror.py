"""The mirror: a copy of the archive in a Postgres database you choose (`[mirror] to = "postgres"`), written after
every background run and never read back.

What goes, and as what, is checked without a database. The sync itself runs against a real one only when
CHRONICLE_TEST_PG names a PG* env file for a database tests may write to (each test uses a schema of its own and drops
it); CI's postgres job sets it."""

import json
import os
import secrets
import shutil
import stat
import uuid

import pytest

from interlatch import mirror
from interlatch.core.config import load_config, set_config_value
from interlatch.core.util import one_line

from conftest import CWD, SECRET, SID
from test_hub import _serve
from test_team import _call

PG_ENV = os.environ.get("CHRONICLE_TEST_PG")


def test_config_says_what_goes(tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    cfg = load_config(home)
    assert (cfg.mirror_to, cfg.mirror_include, cfg.mirror_schema) == ("", "knowledge", "chronicle") and not mirror.enabled(cfg)
    (home / "config.toml").write_text('[mirror]\nto = "Postgres"\ninclude = "all"\nschema = "x; drop table y"\n')
    cfg = load_config(home)
    # a typo never sends transcripts, and the schema is never a piece of SQL
    assert (cfg.mirror_to, cfg.mirror_include, cfg.mirror_schema) == ("postgres", "knowledge", "chronicle")


def test_what_each_include_takes(synced):
    conn = synced["conn"]
    knowledge = {n for n, _t, _pk in mirror._columns(conn, "sessions", "knowledge")}
    everything = {n for n, _t, _pk in mirror._columns(conn, "sessions", "everything")}
    assert {"summary", "llm_title", "est_cost_usd", "project_path"} <= knowledge
    assert not knowledge & {"first_prompt", "last_prompt"} and {"first_prompt", "last_prompt"} <= everything
    assert not (knowledge | everything) & mirror.INTERNAL["sessions"]  # Chronicle's bookkeeping never goes
    assert "description" not in {n for n, _t, _pk in mirror._columns(conn, "subagents", "knowledge")}
    assert "events" not in mirror.tables("knowledge") and {"events", "tool_calls"} <= set(mirror.tables("everything"))
    types = {n: t for n, t, _pk in mirror._columns(conn, "sessions", "knowledge")}
    assert types["started_at"] == "timestamptz" and types["tags_json"] == "jsonb" and types["n_prompts"] == "bigint"


def test_a_row_as_it_goes(synced):
    pytest.importorskip("psycopg")
    conn = synced["conn"]
    rec = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (SID,)).fetchone())
    assert SECRET in rec["first_prompt"]
    prompt_title = {**rec, "title": one_line(rec["first_prompt"], 90)}  # no title of its own: its prompt's opening
    for include in ("knowledge", "everything"):
        cols = mirror._columns(conn, "sessions", include)
        names = [n for n, _t, _pk in cols]
        row = dict(zip(names, mirror._row("sessions", cols, rec, include), strict=True))
        assert row["started_at"].isoformat() == "2026-09-20T10:00:00+00:00" and row["title"] == rec["title"]
        assert SECRET not in json.dumps(row, default=str)  # never as it was typed
        row = dict(zip(names, mirror._row("sessions", cols, prompt_title, include), strict=True))
        assert SECRET not in json.dumps(row, default=str)
        if include == "everything":
            assert "[REDACTED" in row["first_prompt"] and "[REDACTED" in row["title"]
        else:
            assert row["title"] is None  # a title that is only the prompt's opening is a prompt
    assert mirror._value("a\x00b", "text", False) == "ab"  # Postgres text can't hold NUL
    assert mirror._value("not json", "jsonb", False).obj == "not json"
    assert mirror._value("12", "bigint", False) == 12 and mirror._value("x", "bigint", False) is None


def test_the_background_run_writes_it(env, monkeypatch):
    from interlatch.worker import run_worker

    calls = []
    monkeypatch.setattr(mirror, "sync", lambda cfg, **kw: calls.append(cfg.mirror_include) or mirror.Result(sessions=2, removed=0))
    cfg = env["cfg"]
    assert run_worker(cfg, analyze=False, synthesize=False, export=False).mirrored is None and not calls  # off
    cfg.mirror_to = "postgres"
    report = run_worker(cfg, analyze=False, synthesize=False, export=False)
    assert calls == ["knowledge"] and report.mirrored == "2 sessions written, 0 removed" and "mirror: 2 sessions" in report.summary()
    run_worker(cfg, session_ids=["nope"], analyze=False, synthesize=False, export=False)
    assert len(calls) == 1  # one session analyzed from its page: the next background run writes it


def test_settings_from_the_dashboard(env, monkeypatch):
    """Settings › Storage: tested before it is saved, the password goes in and never comes out, a schema another
    computer mirrors into is refused, and only a request from the computer itself may see or change it."""
    from interlatch.hub import local_machine

    cfg, home = env["cfg"], env["home"]
    me = local_machine(cfg)["id"]
    owner = {"value": None}
    tried = []
    monkeypatch.setattr(mirror, "probe", lambda params, schema: tried.append((params, schema)) or {
        "where": f"{params['dbname']} on {params['host']}", "server": "17.11", "schema": schema, "owner": owner["value"],
        "can_create": True})
    synced_with = []
    monkeypatch.setattr(mirror, "sync", lambda cfg, **kw: synced_with.append(cfg.mirror_schema) or mirror.Result(sessions=0, removed=0))
    _app, httpd, url = _serve(cfg)
    try:
        code, mi = _call(url, "/api/mirror")
        assert code == 200 and not mi["enabled"] and mi["settings"] is None and mi["include"] == "knowledge"
        form = {"host": "db.example.net", "port": "5432", "dbname": "archive", "user": "me", "password": " s3cret'x ",
                "sslmode": "require", "schema": "adrian_mac", "include": "everything"}
        tailnet = {"X-Forwarded-For": "203.0.113.5", "Tailscale-User-Login": "me@github"}  # let in, but not here
        code, _r = _call(url, "/api/mirror/save", {"enabled": True, **form}, tailnet)
        assert code == 403 and not (home / mirror.ENV_FILE).exists()
        assert _call(url, "/api/mirror", None, tailnet)[0] == 403 and _call(url, "/api/mirror/sync", {}, tailnet)[0] == 403
        assert "schema name" in _call(url, "/api/mirror/save", {"enabled": True, **form, "schema": "a b"})[1]["error"]
        assert "knowledge" in _call(url, "/api/mirror/save", {"enabled": True, **form, "include": "all"})[1]["error"]
        owner["value"] = {"id": str(uuid.uuid4()), "name": "Other Mac", "include": "knowledge"}
        r = _call(url, "/api/mirror/save", {"enabled": True, **form})[1]
        assert "another computer (Other Mac)" in r["error"] and not (home / mirror.ENV_FILE).exists()
        owner["value"] = {"id": me, "name": "This Mac", "include": "knowledge"}
        assert _call(url, "/api/mirror/test", form)[1]["mine"] is True
        code, r = _call(url, "/api/mirror/save", {"enabled": True, **form})
        assert code == 200 and r["ok"] and r["mirror"]["enabled"] and tried[-1][1] == "adrian_mac"
        env_file = home / mirror.ENV_FILE
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
        assert mirror.connection_params(load_config(home))["password"] == " s3cret'x "  # exactly as typed
        now = load_config(home)
        assert (now.mirror_to, now.mirror_include, now.mirror_schema) == ("postgres", "everything", "adrian_mac")
        code, mi = _call(url, "/api/mirror")
        assert mi["settings"] == {"host": "db.example.net", "port": "5432", "dbname": "archive", "user": "me",
                                  "sslmode": "require", "password_set": True}
        assert "s3cret" not in json.dumps(mi) and "s3cret" not in json.dumps(r)
        code, r = _call(url, "/api/mirror/test", {**form, "password": ""})  # empty: the saved one
        assert r["ok"] and tried[-1][0]["password"] == " s3cret'x "
        code, r = _call(url, "/api/mirror/save", {"enabled": False})
        assert r["ok"] and not r["mirror"]["enabled"] and env_file.exists() and load_config(home).mirror_to == ""
        assert _call(url, "/api/mirror/sync", {})[1] == {"started": False}  # off: nothing to write
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ against a real Postgres
@pytest.fixture()
def pg_mirror(synced):
    if not PG_ENV:
        pytest.skip("CHRONICLE_TEST_PG names no PG* env file for a test database")
    psycopg = pytest.importorskip("psycopg")
    cfg = synced["cfg"]
    shutil.copy(PG_ENV, cfg.home / mirror.ENV_FILE)
    os.chmod(cfg.home / mirror.ENV_FILE, 0o600)
    cfg.mirror_to, cfg.mirror_schema = "postgres", f"mirror_test_{secrets.token_hex(4)}"
    db = psycopg.connect(**mirror.connection_params(cfg), autocommit=True)

    def q(sql, *args):
        return db.execute(sql.replace("{s}", cfg.mirror_schema), args).fetchall()

    synced["q"] = q
    yield synced
    db.execute(f"DROP SCHEMA IF EXISTS {cfg.mirror_schema} CASCADE")
    db.close()


def test_sync_against_postgres(pg_mirror):
    cfg, conn, q = pg_mirror["cfg"], pg_mirror["conn"], pg_mirror["q"]
    local = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    n_sessions, n_calls = local("SELECT count(*) FROM sessions"), local("SELECT count(*) FROM api_calls")
    assert n_sessions and n_calls

    res = mirror.sync(cfg)
    assert not res.get("error") and res["sessions"] == n_sessions and mirror.last(cfg)["sessions"] == n_sessions
    assert q("SELECT count(*) FROM {s}.sessions")[0][0] == n_sessions
    assert q("SELECT count(*) FROM {s}.api_calls")[0][0] == n_calls
    cols = {r[0] for r in q("SELECT column_name FROM information_schema.columns WHERE table_schema = '{s}' "
                            "AND table_name = 'sessions'")}
    assert "first_prompt" not in cols and "transcript_path" not in cols and "summary" in cols
    assert not q("SELECT 1 FROM information_schema.tables WHERE table_schema = '{s}' AND table_name = 'events'")
    started, = q("SELECT started_at FROM {s}.sessions WHERE id = %s", SID)[0]
    assert started.isoformat().startswith("2026-09-20T10:00:00")
    assert SECRET not in json.dumps(q("SELECT row_to_json(x) FROM {s}.sessions x"), default=str)

    assert mirror.sync(cfg)["sessions"] == 0  # nothing changed: nothing written

    conn.execute("UPDATE sessions SET summary = 'Fixed the login test' WHERE id = ?", (SID,))
    conn.execute("INSERT INTO knowledge(session_id, project_path, kind, title, body, fingerprint) "
                 "VALUES (?, ?, 'gotcha', 'Tokens expire', 'b', 'fp-mirror')", (SID, CWD))
    conn.commit()
    res = mirror.sync(cfg)
    assert res["sessions"] == 1 and res["rows"]["knowledge"] == 1
    assert q("SELECT summary FROM {s}.sessions WHERE id = %s", SID) == [("Fixed the login test",)]
    assert q("SELECT count(*) FROM {s}.api_calls")[0][0] == n_calls  # its own rows written again, once
    conn.execute("DELETE FROM knowledge WHERE fingerprint = 'fp-mirror'")
    conn.commit()
    assert mirror.sync(cfg)["removed"] == 1 and not q("SELECT 1 FROM {s}.knowledge WHERE title = 'Tokens expire'")

    cfg.mirror_include = "everything"
    assert not mirror.sync(cfg).get("error")
    events = q("SELECT text FROM {s}.events WHERE session_id = %s AND text IS NOT NULL", SID)
    assert events and not any(SECRET in t for (t,) in events) and any("[REDACTED" in t for (t,) in events)
    prompt, = q("SELECT first_prompt FROM {s}.sessions WHERE id = %s", SID)[0]
    assert prompt and SECRET not in prompt and "[REDACTED" in prompt

    cfg.mirror_include = "knowledge"  # back: the transcript and prompts leave the mirror
    assert not mirror.sync(cfg).get("error")
    assert not q("SELECT 1 FROM information_schema.tables WHERE table_schema = '{s}' AND table_name IN ('events', 'tool_calls')")
    assert not q("SELECT 1 FROM information_schema.columns WHERE table_schema = '{s}' AND column_name = 'first_prompt'")

    cfg.exclude_projects = [CWD]  # excluded later: what it sent goes
    assert mirror.sync(cfg)["removed"] >= 1
    assert not q("SELECT 1 FROM {s}.sessions WHERE id = %s", SID) and not q("SELECT 1 FROM {s}.api_calls WHERE session_id = %s", SID)
    cfg.exclude_projects = []

    st = mirror.status(cfg)
    assert st["owner"]["include"] == "knowledge" and st["counts"]["sessions"] == n_sessions - 1

    # another computer pointed at the same schema is turned away, and nothing of its own is written
    (cfg.home / "machine.json").write_text(json.dumps({"id": str(uuid.uuid4()), "name": "Other"}))
    res = mirror.sync(cfg)
    assert "another computer" in res["error"] and q("SELECT count(*) FROM {s}.sessions")[0][0] == n_sessions - 1


def test_a_database_it_cannot_reach(env):
    pytest.importorskip("psycopg")
    cfg = env["cfg"]
    cfg.mirror_to = "postgres"
    res = mirror.sync(cfg)
    assert "no mirror database configured" in res["error"]
    (cfg.home / mirror.ENV_FILE).write_text("PGHOST=127.0.0.1\nPGPORT=1\nPGDATABASE=x\nPGUSER=x\nPGPASSWORD=x\nPGSSLMODE=disable\n")
    os.chmod(cfg.home / mirror.ENV_FILE, 0o600)
    res = mirror.sync(cfg)
    assert res["error"].startswith("can't reach the mirror database (x on 127.0.0.1)") and mirror.last(cfg)["error"]
    set_config_value(cfg, "mirror", "to", '""')
    assert mirror.sync(load_config(cfg.home)) == {"note": "the mirror is off"}
