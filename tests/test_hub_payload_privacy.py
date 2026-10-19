"""What a computer that shares knowledge only sends a hub, byte for byte: what its sessions did for the project, redacted,
and never the model's raw analysis, the person's mood, friction notes, preferences or global lessons. A hub keeps no
more of what an older computer still sends, and a hub upgraded from before clears what it kept (schema 20)."""

import gzip
import json
import os
import secrets
import shutil
import urllib.request
import uuid

import pytest

from interlatch.analysis.session import normalize_analysis, store_analysis
from interlatch.core.config import load_config
from interlatch.core.machine import local_machine
from interlatch.ingestion.service import sync
from interlatch.sharing import client
from interlatch.sharing.identity import read_token
from interlatch.sharing.protocol import (ANALYSIS_COLS, KNOWLEDGE_COLS, PERSONAL_ANALYSIS_COLS, SHARED_ANALYSIS_COLS,
                                         SHARED_COLS)
from interlatch.sharing.push import push
from interlatch.storage import team as team_store
from interlatch.storage.sqlite import connect

from test_hub import SPOKE_CWD, SPOKE_SID
from test_team import teamenv  # noqa: F401  (the fixture)

# secret-shaped canaries core.redact knows, and words that must never leave the spoke
ANTHROPIC = "sk-ant-api03-" + "Q" * 40
GITHUB = "ghp_" + "Z" * 36
AWS = "AKIA" + "QWERTYUIOPASDFGH"
PERSONAL = ("PREFERENCE_CANARY", "GLOBAL_CANARY", "LEARNING_CANARY", "FRICTION_CANARY", "frustrated", ANTHROPIC,
            GITHUB, AWS)


def _leaky_analysis(spoke):
    """The spoke's session analyzed, with everything a model might write that is not the project's, and the raw
    rows an older Interlatch stored (an unredacted title, raw analysis with learning interests)."""
    conn = connect(spoke.db_path)
    sync(spoke, conn)
    n = conn.execute("SELECT n_prompts FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0]
    data = normalize_analysis({
        "title": f"Fix login {ANTHROPIC}", "summary": "Fixed the login flow", "goal": "Working login", "outcome": "success",
        "outcome_note": "Done", "work_types": ["bugfix"], "tags": ["auth"],
        "highlights": [f"Rotated {AWS}"], "open_threads": ["Add a test"], "sentiment": "frustrated",
        "friction": [{"kind": "tooling", "note": f"FRICTION_CANARY the tests were slow with {GITHUB}"}],
        "learning_interests": [{"topic": "security", "question": "LEARNING_CANARY how does OAuth work?"}],
        "knowledge": [
            {"kind": "gotcha", "title": "PROJECT_LESSON login needs the raw body", "body": f"token={GITHUB}",
             "scope": "project", "confidence": "high", "scene": "s", "question": "q", "answer": f"use {GITHUB}"},
            {"kind": "preference", "title": "PREFERENCE_CANARY prefers tabs", "body": "b", "scope": "project"},
            {"kind": "gotcha", "title": "GLOBAL_CANARY zsh globbing", "body": "b", "scope": "global"},
        ]})
    store_analysis(conn, spoke, SPOKE_SID, data, "test-model", n)
    here = conn.execute("SELECT llm_title, title, friction_json FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert here["llm_title"] == here["title"] == "Fix login [REDACTED:anthropic-key]"  # stored redacted here too
    assert GITHUB not in here["friction_json"] and "FRICTION_CANARY" in here["friction_json"]
    raw = {**data, "learning_interests": [{"topic": "security", "question": "LEARNING_CANARY how does OAuth work?"}]}
    conn.execute("UPDATE sessions SET llm_title = ?, highlights_json = ?, analysis_json = ? WHERE id = ?",
                 (data["title"], json.dumps(data["highlights"]), json.dumps(raw), SPOKE_SID))
    conn.execute("UPDATE knowledge SET body = ? WHERE session_id = ? AND title LIKE 'PROJECT_LESSON%'",
                 (f"token={GITHUB}", SPOKE_SID))
    conn.commit()
    conn.close()


def test_the_whole_sessions_upload_holds_only_the_project(teamenv, monkeypatch):  # noqa: F811
    hub, spoke = teamenv["conn"], teamenv["spoke"]
    _leaky_analysis(spoke)
    bodies = []
    request = client.HubClient.request

    def spy(self, method, path, **kw):
        if path == "/api/hub/sessions":
            bodies.append(gzip.decompress(kw["body"]))
        return request(self, method, path, **kw)

    monkeypatch.setattr(client.HubClient, "request", spy)
    report = push(spoke)
    assert report.kind == "knowledge" and report.sent == 1 and not report.errors

    wire = b"".join(bodies).decode()
    for canary in PERSONAL:
        assert canary not in wire, f"{canary} left the spoke"
    assert "PROJECT_LESSON" in wire and "[REDACTED" in wire  # the project's lesson went, its secret didn't
    records = [rec for body in bodies for rec in json.loads(body)["sessions"]]
    assert len(records) == 1
    rec = records[0]
    allowed = {"id", "project_path", "remote", "language", "knowledge", *SHARED_COLS, *SHARED_ANALYSIS_COLS}
    assert set(rec) <= allowed and not set(rec) & set(PERSONAL_ANALYSIS_COLS)
    assert [k["title"] for k in rec["knowledge"]] == ["PROJECT_LESSON login needs the raw body"]
    assert set(rec["knowledge"][0]) == set(KNOWLEDGE_COLS)

    row = hub.execute("SELECT * FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert row["source"] == "remote" and row["summary"] == "Fixed the login flow"
    assert row["title"].startswith("Fix login [REDACTED")
    stored = json.dumps([dict(row)] + [dict(r) for r in hub.execute("SELECT * FROM knowledge WHERE session_id = ?",
                                                                     (SPOKE_SID,))])
    for canary in PERSONAL:
        assert canary not in stored
    team = json.dumps(teamenv["fake"].sessions, default=str)
    assert "PROJECT_LESSON" in team and not any(canary in team for canary in PERSONAL)


def test_the_hub_keeps_none_of_what_an_older_computer_sends(teamenv):  # noqa: F811
    """A computer from before this fix sends its raw analysis, mood and friction notes, an unredacted title and lessons
    about the person: the hub keeps the project's part, redacted, and clears what an earlier upload left."""
    hub, spoke = teamenv["conn"], teamenv["spoke"]
    machine = local_machine(spoke)["id"]
    hub.execute("INSERT INTO sessions(id, source, agent, machine_id, project_path, analysis_status, analysis_json, "
                "sentiment, friction_json) VALUES ('old-1', 'remote', 'claude', ?, ?, 'done', "
                "'{\"x\": \"PREFERENCE_CANARY\"}', 'frustrated', '[{\"note\": \"FRICTION_CANARY\"}]')", (machine, SPOKE_CWD))
    hub.commit()
    lesson = {"kind": "gotcha", "title": f"PROJECT_LESSON {GITHUB}", "body": f"use {AWS}", "scope": "project",
              "status": "active", "pinned": 0, "case_json": json.dumps({"scene": f"key {ANTHROPIC}"}), "fingerprint": "fp-1"}
    rec = {"id": "old-1", "agent": "claude", "project_path": SPOKE_CWD, "analyzed_at": "2026-10-01T00:00:00Z",
           "llm_title": f"Deploy {ANTHROPIC}", "summary": f"Set {AWS}", "highlights_json": json.dumps([f"pushed {GITHUB}"]),
           "analysis_json": json.dumps({"knowledge": [{"title": "PREFERENCE_CANARY"}, {"title": "GLOBAL_CANARY"}],
                                        "learning_interests": ["LEARNING_CANARY"]}),
           "sentiment": "frustrated", "friction_json": json.dumps([{"kind": "other", "note": "FRICTION_CANARY"}]),
           "analysis_reason": "FRICTION_CANARY", "language": "en",
           "knowledge": [lesson, {**lesson, "kind": "preference", "title": "PREFERENCE_CANARY", "fingerprint": "fp-2"},
                         {**lesson, "scope": "global", "title": "GLOBAL_CANARY", "fingerprint": "fp-3"}]}
    req = urllib.request.Request(f"{teamenv['url']}/api/hub/sessions?machine={machine}", method="POST",
                                 data=gzip.compress(json.dumps({"sessions": [rec]}).encode()),
                                 headers={"Authorization": f"Bearer {read_token(spoke)}"})
    assert json.loads(urllib.request.urlopen(req, timeout=10).read())["stored"] == 1

    row = dict(hub.execute("SELECT * FROM sessions WHERE id = 'old-1'").fetchone())
    assert all(row[c] is None for c in PERSONAL_ANALYSIS_COLS)
    assert row["title"].startswith("Deploy [REDACTED") and row["llm_title"] == row["title"]
    lessons = [dict(r) for r in hub.execute("SELECT * FROM knowledge WHERE session_id = 'old-1'")]
    assert [k["fingerprint"] for k in lessons] == ["fp-1"]
    kept = json.dumps([row, *lessons]) + json.dumps(teamenv["fake"].sessions["old-1"], default=str)
    assert "PROJECT_LESSON" in kept and "[REDACTED" in kept
    for canary in PERSONAL:
        assert canary not in kept, canary


def test_schema_20_clears_what_a_hub_kept(tmp_path):
    db = tmp_path / "interlatch.db"
    conn = connect(db)
    conn.execute("INSERT INTO sessions(id, source, agent, analysis_status, llm_title, title, summary, highlights_json, "
                 "sentiment, friction_json, analysis_json, analysis_reason) VALUES ('r1', 'remote', 'claude', 'done', ?, ?, "
                 "?, ?, 'frustrated', ?, ?, 'x')",
                 (f"Deploy {ANTHROPIC}", f"Deploy {ANTHROPIC}", f"Set {AWS}", json.dumps([f"pushed {GITHUB}"]),
                  json.dumps([{"note": "FRICTION_CANARY"}]), json.dumps({"x": "PREFERENCE_CANARY"})))
    conn.execute("INSERT INTO sessions(id, source, agent, analysis_status, llm_title, title, sentiment, friction_json, "
                 "analysis_json) VALUES ('l1', 'transcript', 'claude', 'done', ?, ?, 'positive', ?, '{\"own\": 1}')",
                 (f"Local {ANTHROPIC}", f"Local {ANTHROPIC}", json.dumps([{"kind": "other", "note": f"leaked {GITHUB}"}])))
    conn.execute("INSERT INTO knowledge(session_id, kind, title, body, scope, source, fingerprint, created_at, updated_at, "
                 "case_json) VALUES ('r1', 'gotcha', ?, 'b', 'project', 'analysis', 'fp', 'now', 'now', ?)",
                 (f"Lesson {GITHUB}", json.dumps({"scene": f"key {AWS}"})))
    conn.execute("PRAGMA user_version = 19")  # as a hub before this fix left it
    conn.commit()
    conn.close()

    conn = connect(db)  # opening it upgrades it
    try:
        remote = dict(conn.execute("SELECT * FROM sessions WHERE id = 'r1'").fetchone())
        assert all(remote[c] is None for c in PERSONAL_ANALYSIS_COLS)
        local = dict(conn.execute("SELECT * FROM sessions WHERE id = 'l1'").fetchone())
        assert local["sentiment"] == "positive" and local["analysis_json"] == '{"own": 1}'  # this computer's own
        assert json.loads(local["friction_json"])[0]["note"] == "leaked [REDACTED:github-token]"
        lesson = dict(conn.execute("SELECT * FROM knowledge WHERE session_id = 'r1'").fetchone())
        everything = json.dumps([remote, local, lesson])
        for canary in PERSONAL:
            assert canary not in everything, canary
        assert remote["title"] == "Deploy [REDACTED:anthropic-key]"
        assert [r[0] for r in conn.execute("SELECT rowid FROM knowledge_fts WHERE knowledge_fts MATCH 'Lesson'")]
        from interlatch.storage.migrations import scrub_analyses

        assert scrub_analyses(conn) == {"sessions_cleared": 0, "sessions_redacted": 0, "lessons_redacted": 0}
    finally:
        conn.close()


def test_someone_limited_to_projects_sees_no_mood_or_friction(tmp_path):
    """A hub's own sessions keep their mood and friction notes, for its owner only: a member limited to the project
    sees what a computer would share of them."""
    from interlatch.sharing.access import limit

    conn = connect(tmp_path / "interlatch.db")
    try:
        conn.execute("INSERT INTO sessions(id, source, agent, project_path, analysis_status, llm_title, summary, sentiment, "
                     "friction_json, analysis_json, analysis_reason) VALUES ('h1', 'transcript', 'claude', '/hub/app', "
                     "'done', 'Title', 'Summary', 'frustrated', '[{\"note\": \"FRICTION_CANARY\"}]', '{}', 'why')")
        limit(conn, ["/hub/app"])
        row = dict(conn.execute("SELECT * FROM sessions").fetchone())
        assert row["summary"] == "Summary" and row["title"] == "Title"
        assert all(row[c] is None for c in PERSONAL_ANALYSIS_COLS)
    finally:
        conn.close()


def test_every_analysis_column_is_shared_or_personal():
    assert set(SHARED_ANALYSIS_COLS) | set(PERSONAL_ANALYSIS_COLS) == set(ANALYSIS_COLS)
    assert {"analysis_json", "sentiment", "friction_json"} <= set(PERSONAL_ANALYSIS_COLS)


PG_ENV = os.environ.get("CHRONICLE_TEST_PG")


@pytest.mark.skipif(not PG_ENV, reason="CHRONICLE_TEST_PG names no PG* env file for a test database")
def test_team_store_step_redacts_what_it_kept(tmp_path, monkeypatch):
    pytest.importorskip("psycopg")
    home = tmp_path / "home"
    home.mkdir()
    shutil.copy(PG_ENV, home / team_store.ENV_FILE)
    os.chmod(home / team_store.ENV_FILE, 0o600)
    params, schema = team_store.connection_params(load_config(home)), f"team_test_{secrets.token_hex(4)}"
    alice = str(uuid.uuid4())
    steps = team_store.STEPS
    monkeypatch.setattr(team_store, "STEPS", steps[:1])  # a store from before step 0002
    old = team_store.TeamStore(params, schema=schema)
    try:
        old.put_sessions(alice, [{"id": "a1", "project": "/hub/app", "project_name": "app", "remote": None,
                                  "agent": "claude", "title": f"Deploy {ANTHROPIC}", "summary": "s", "details": {},
                                  "lessons": [{"kind": "fact", "title": f"Key {AWS}", "body": "b", "tags": None}]}])
        old.close()
        monkeypatch.setattr(team_store, "STEPS", steps)
        new = team_store.TeamStore(params, schema=schema)
        with new._session() as c:
            title = c.execute(f"SELECT title FROM {schema}.sessions WHERE id = 'a1'").fetchone()[0]
            lesson = c.execute(f"SELECT title FROM {schema}.lessons").fetchone()[0]
        assert title == "Deploy [REDACTED:anthropic-key]" and lesson == "Key [REDACTED:aws-key-id]"
        assert new.status()["steps"] == [name for name, _ in steps]
        new.close()
    finally:
        with old._session() as c:
            c.execute(f"DROP SCHEMA {schema} CASCADE")
        old.close()
