"""The team store: a hub with `[hub] store = "postgres"` keeps what computers share in Postgres as well, and each
computer that shares knowledge gets its teammates' lessons back, read-only, where its MCP tools and notes find them.

The flow runs against an in-memory stand-in for the store. The SQL itself runs against a real database only when
CHRONICLE_TEST_PG names a PG* env file for one tests may write to (they use a schema of their own and drop it)."""

import json
import os
import secrets
import shutil
import uuid

import pytest

from chronicle import hub, team_store
from chronicle.config import load_config, set_config_value
from chronicle.db import connect
from chronicle.util import fingerprint

from test_hub import SPOKE_CWD, SPOKE_SID, _analyzed_on_spoke, _make_spoke, _serve

TEAMMATE = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


class FakeStore:
    """team_store.TeamStore's behavior, in memory."""

    def __init__(self):
        self.names: dict[str, str | None] = {}
        self.sessions: dict[str, dict] = {}
        self.lessons: dict[int, dict] = {}
        self.sources: list[tuple[int, str, str]] = []  # (lesson id, session id, computer id)
        self.next_id = 1
        self.fail = False

    def computer_seen(self, machine_id, name, platform, version):
        self.names[machine_id] = name

    def status(self):
        return {"where": "fake", "schema": "team", "server": "17", "steps": [n for n, _ in team_store.STEPS],
                "counts": {"computers": len(self.names), "sessions": len(self.sessions), "lessons": len(self.lessons),
                           "lesson_sources": len(self.sources), "audit": 0}, "last_activity": None}

    def shared(self, machine_id):
        return {sid: s["analyzed_at"] for sid, s in self.sessions.items() if s["computer"] == machine_id}

    def put_sessions(self, machine_id, sessions):
        if self.fail:
            raise team_store.TeamStoreError("team store (test): the database is down")
        for rec in sessions:
            self.sessions[rec["id"]] = {**rec, "computer": machine_id}
            before = {lid for lid, sid, _ in self.sources if sid == rec["id"]}
            self.sources = [x for x in self.sources if x[1] != rec["id"]]
            at = team_store.place(rec.get("remote"), rec.get("project"))
            for k in rec["lessons"] if at else []:
                key = (at, fingerprint(k["kind"], k["title"]))
                lid = next((i for i, x in self.lessons.items() if (x["place"], x["key"]) == key), None)
                if lid is None:
                    lid, self.next_id = self.next_id, self.next_id + 1
                self.lessons[lid] = {**k, "place": at, "project": rec["project"], "remote": rec.get("remote"),
                                     "key": key[1], "language": rec.get("language"),
                                     "updated_at": f"2026-10-05T00:00:{len(self.sources):02d}Z"}
                self.sources.append((lid, rec["id"], machine_id))
            for lid in before - {x[0] for x in self.sources}:
                del self.lessons[lid]
        return {"sessions": len(sessions)}

    def forget_sessions(self, session_ids, computer_id=None):
        if computer_id is not None:
            session_ids = [sid for sid in session_ids if self.sessions.get(sid, {}).get("computer") == computer_id]
        gone = [sid for sid in session_ids if self.sessions.pop(sid, None) is not None]
        before = {lid for lid, sid, _ in self.sources if sid in gone}
        self.sources = [x for x in self.sources if x[1] not in gone]
        for lid in before - {x[0] for x in self.sources}:
            del self.lessons[lid]
        return len(gone)

    def lessons_for(self, machine_id, remotes, projects, limit=2000, within=None, left=None):
        def at(s):
            return team_store.place(s.get("remote"), s["project"])

        def counts(s):  # within: only what sessions filed under those hub projects stated; left: nothing there
            return (within is None or s["project"] in within) and s["project"] not in (left or [])

        scope = sorted({at(s) for s in self.sessions.values() if at(s) and counts(s) and (
            s["computer"] == machine_id or s.get("remote") in remotes or s["project"] in projects)})
        out = []
        for lid, x in sorted(self.lessons.items()):
            src = [s for s in self.sources if s[0] == lid and counts(self.sessions[s[1]])]
            if not src:
                continue
            if x["place"] not in scope or any(s[2] == machine_id for s in src):
                continue
            out.append({"id": lid, "place": x["place"], "project": x["project"], "remote": x["remote"],
                        "project_name": None, "kind": x["kind"], "title": x["title"],
                        "body": x.get("body"), "tags": x.get("tags"), "language": x["language"], "created_at": None,
                        "updated_at": x["updated_at"], "sessions": len(src),
                        "computers": sorted({self.names.get(s[2]) or s[2][:8] for s in src}),
                        "session_ids": [s[1] for s in src]})
        places = {}
        for p in scope:
            there = {i: s for i, s in self.sessions.items() if at(s) == p}
            places[p] = {"remote": max((s.get("remote") or "" for s in there.values()), default="") or None,
                         "project": max(s["project"] for s in there.values()),
                         "mine": max((i for i, s in there.items() if s["computer"] == machine_id), default=None)}
        return {"lessons": out, "places": places}


def _teammate_session(sid, lessons, *, project=SPOKE_CWD, remote=None):
    return {"id": sid, "project": project, "project_name": "demo-app", "remote": remote, "agent": "claude",
            "analyzed_at": "2026-10-01T00:00:00Z", "language": "en", "details": {},
            "lessons": [{"kind": k, "title": t, "body": f"why: {t}", "tags": None} for k, t in lessons]}


@pytest.fixture()
def teamenv(synced, monkeypatch):
    """The `synced` computer as a hub with a team store, and a spoke that shares knowledge with it."""
    monkeypatch.setattr(hub.IngestTrigger, "request", lambda self: None)
    fake = FakeStore()
    monkeypatch.setattr(team_store, "get", lambda cfg: fake if cfg.hub_store == "postgres" and not cfg.is_spoke else None)
    cfg = synced["cfg"]
    set_config_value(cfg, "hub", "store", '"postgres"')  # the dashboard reads its config again on every request
    token = hub.new_token(cfg)
    app, httpd, url = _serve(load_config(cfg.home))
    spoke, _, _ = _make_spoke(synced["tmp"], url, token, extra='share = "knowledge"\nall_folders = true\n')
    fake.computer_seen(TEAMMATE, "Teammate PC", "Linux", "0.7.0")
    synced.update(fake=fake, spoke=spoke, url=url)
    yield synced
    httpd.shutdown()


def _team_rows(spoke):
    conn = connect(spoke.db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM knowledge WHERE source = 'team' ORDER BY id")]
    finally:
        conn.close()


def test_teammates_lessons_come_back_read_only(teamenv):
    from chronicle.hooks import build_session_context
    from chronicle.mcp_server import Tools

    fake, spoke, conn = teamenv["fake"], teamenv["spoke"], teamenv["conn"]
    assert spoke.shares_knowledge
    fake.put_sessions(TEAMMATE, [_teammate_session("mate-1", [("gotcha", "Teammate lesson"),
                                                               ("gotcha", "lesson p-gotcha")])])
    _analyzed_on_spoke(spoke, title="Mine", at="2026-10-02T00:00:00Z", lessons=[("p-gotcha", "project", "gotcha"),
                                                                                ("p-pref", "project", "preference")])
    report = hub.push(spoke)
    assert not report.errors and report.sent == 1 and report.team == 1
    assert "1 team lesson here" in report.summary()

    # the team's record and the hub's own copy both have the shared session; only project lessons, merged by title
    assert fake.sessions[SPOKE_SID]["project"] == SPOKE_CWD and fake.sessions[SPOKE_SID]["language"] == "en"
    assert sorted(x["title"] for x in fake.lessons.values()) == ["Teammate lesson", "lesson p-gotcha"]
    merged = next(lid for lid, x in fake.lessons.items() if x["title"] == "lesson p-gotcha")
    assert {s[2] for s in fake.sources if s[0] == merged} == {TEAMMATE, hub.local_machine(spoke)["id"]}
    assert conn.execute("SELECT source FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == "remote"

    # here: the teammate's lesson, filed where this computer's own session in that project is; not the merged one,
    # which this computer stated itself and already has
    [row] = _team_rows(spoke)
    assert (row["title"], row["project_path"], row["session_id"], row["scope"]) == ("Teammate lesson", SPOKE_CWD, None, "project")
    assert row["fingerprint"].startswith("team:") and json.loads(row["confirmed_json"]) == ["mate-1"]
    assert hub.team_from(row) == ["Teammate PC"] and row["stage"] == "provisional"

    notes = build_session_context(spoke, SPOKE_CWD)
    assert "From teammates' sessions in this project" in notes and "Teammate lesson (from Teammate PC)" in notes
    tools = Tools(spoke)
    assert "teammates' sessions on Teammate PC" in tools.search_knowledge("Teammate")
    assert "Teammate lesson** — why: Teammate lesson (from teammates)" in tools.project_knowledge(SPOKE_CWD)

    # its own dashboard shows it too, marked with the computers whose sessions stated it, and can list only those
    from test_people_web import _call

    app, httpd, url = _serve(load_config(spoke.home))
    try:
        _, everything = _call(url, "/api/knowledge")
        _, only = _call(url, "/api/knowledge?source=team&limit=1")  # filtered before the limit, not after
        _, page = _call(url, f"/api/project?path={SPOKE_CWD}")
    finally:
        httpd.shutdown()
    assert everything["sources"]["team"] == 1
    assert [(k["title"], k["team_from"]) for k in only["items"]] == [("Teammate lesson", ["Teammate PC"])]
    assert all("team_from" not in k for k in everything["items"] if k["source"] != "team")
    assert next(k for k in page["knowledge"] if k["source"] == "team")["team_from"] == ["Teammate PC"]

    # the same answer again: nothing changes here
    assert hub.push(spoke).team == 1 and _team_rows(spoke)[0]["id"] == row["id"]
    connl = connect(spoke.db_path)
    connl.execute("DELETE FROM knowledge WHERE source = 'team'")  # lost here (a restored backup): fetched again
    connl.commit()
    connl.close()
    assert hub.push(spoke).team == 1 and [r["title"] for r in _team_rows(spoke)] == ["Teammate lesson"]
    row = _team_rows(spoke)[0]

    # a second teammate session confirms it: it climbs, keeps its id, and a dismissal here is kept
    connl = connect(spoke.db_path)
    connl.execute("UPDATE knowledge SET status = 'dismissed' WHERE id = ?", (row["id"],))
    connl.commit()
    connl.close()
    fake.put_sessions(TEAMMATE, [_teammate_session("mate-2", [("gotcha", "Teammate lesson")])])
    hub.push(spoke)
    [again] = _team_rows(spoke)
    assert again["id"] == row["id"] and again["stage"] == "established" and again["status"] == "dismissed"
    assert sorted(json.loads(again["confirmed_json"])) == ["mate-1", "mate-2"]

    # the teammate's sessions no longer state it: it is removed here too
    fake.put_sessions(TEAMMATE, [_teammate_session("mate-1", []), _teammate_session("mate-2", [])])
    assert hub.push(spoke).team == 0 and _team_rows(spoke) == []
    assert hub.last_team(spoke)["lessons"] == 0


def test_store_failure_keeps_nothing_and_is_resent(teamenv):
    fake, spoke, conn = teamenv["fake"], teamenv["spoke"], teamenv["conn"]
    _analyzed_on_spoke(spoke, title="Mine", at="2026-10-02T00:00:00Z", lessons=[("p1", "project", "fact")])
    fake.fail = True
    report = hub.push(spoke)
    assert report.sent == 0 and report.errors and "the database is down" in report.errors[0]
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == 0
    fake.fail = False
    assert hub.push(spoke).sent == 1  # the store is the record: what it lacks goes again
    assert SPOKE_SID in fake.sessions


def test_a_broken_store_only_stops_computers_that_share_knowledge(teamenv, monkeypatch):
    def broken(cfg):
        if cfg.hub_store == "postgres" and not cfg.is_spoke:
            raise team_store.TeamStoreError("no team database configured: put PGHOST … in team-store.env")

    monkeypatch.setattr(team_store, "get", broken)
    spoke = teamenv["spoke"]
    _analyzed_on_spoke(spoke, title="Mine", at="2026-10-02T00:00:00Z", lessons=[("p1", "project", "fact")])
    with pytest.raises(hub.HubError, match="no team database configured"):
        hub.push(spoke)
    spoke.hub_share = "everything"  # one that sends its transcripts never waits on the team store
    report = hub.push(spoke)
    assert report.kind == "files" and report.sent and not report.errors and report.team is None


def test_lessons_endpoint_needs_the_token_and_a_store(teamenv):
    import urllib.error
    import urllib.request

    body = json.dumps({"machine": hub.local_machine(teamenv["spoke"])["id"]}).encode()
    req = urllib.request.Request(f"{teamenv['url']}/api/hub/lessons", data=body, method="POST",
                                 headers={"Authorization": "Bearer wrong", "Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=10)
    assert err.value.code == 401
    cfg = load_config(teamenv["cfg"].home)
    cfg.hub_store = ""
    assert hub.team_lessons(cfg, {"machine": hub.local_machine(teamenv["spoke"])["id"]}) == {"team": False}


def test_team_lessons_find_their_folder_here(env):
    """No session of this computer there: the busiest clone of the repository, else a folder added to the project."""
    cfg = env["cfg"]
    cfg.hub_folders = {"/work/notes": "/hub/notes"}
    repos = {"/code/app": ["/code/app", "github.com/org/app"], "/code/app-copy": ["/code/app-copy", "github.com/org/app"]}
    conn = connect(cfg.db_path)
    conn.execute("INSERT INTO sessions(id, project_path, project_name, source) VALUES ('s1', '/code/app-copy', 'app', 'transcript')")
    conn.commit()
    conn.close()
    lesson = {"kind": "fact", "body": "b", "updated_at": "2026-10-05T00:00:00Z", "sessions": 1, "computers": ["B"],
              "session_ids": ["x"]}
    app, other = "github.com/org/app", "github.com/org/other"
    data = {"hub": "Hub", "places": {app: {"remote": app, "project": "/hub/app", "mine": None},
                                     "/hub/notes": {"remote": None, "project": "/hub/notes", "mine": None},
                                     other: {"remote": other, "project": "/hub/elsewhere", "mine": None}},
            "lessons": [{**lesson, "id": 1, "place": app, "project": "/hub/app", "title": "By remote"},
                        {**lesson, "id": 2, "place": "/hub/notes", "project": "/hub/notes", "title": "By folder"},
                        {**lesson, "id": 3, "place": other, "project": "/hub/elsewhere", "title": "No folder here"},
                        {"id": "bad", "place": app, "kind": "fact", "title": "ignored"}]}
    assert hub.apply_team_lessons(cfg, data, repos) == 2
    got = {r["title"]: (r["project_path"], r["project_name"]) for r in _team_rows(cfg)}
    assert got == {"By remote": ("/code/app-copy", "app"), "By folder": ("/work/notes", "notes")}


PG_ENV = os.environ.get("CHRONICLE_TEST_PG")


@pytest.mark.skipif(not PG_ENV, reason="CHRONICLE_TEST_PG names no PG* env file for a test database")
def test_postgres_store(tmp_path):
    pytest.importorskip("psycopg")
    home = tmp_path / "home"
    home.mkdir()
    shutil.copy(PG_ENV, home / team_store.ENV_FILE)
    os.chmod(home / team_store.ENV_FILE, 0o600)
    store = team_store.TeamStore(team_store.connection_params(load_config(home)), schema=f"team_test_{secrets.token_hex(4)}")
    alice, bob, carol, dave = (str(uuid.uuid4()) for _ in range(4))

    def sess(sid, project, remote, lessons):
        return {"id": sid, "project": project, "project_name": "app", "remote": remote, "agent": "claude", "title": sid,
                "summary": "s", "analyzed_at": "2026-10-01T00:00:00Z", "language": "ja", "details": {"n_prompts": 3},
                "lessons": [{"kind": k, "title": t, "body": "本文", "tags": ["x"]} for k, t in lessons]}

    try:
        store.computer_seen(alice, "Alice PC", "macOS", "0.7.0")
        store.computer_seen(bob, "Bob PC", "Linux", "0.7.0")
        store.put_sessions(alice, [sess("a1", "/hub/app", "github.com/org/app",
                                        [("gotcha", "Stripe needs the raw body"), ("fix", "冪等性キーは Postgres に保存する")])])
        store.put_sessions(bob, [sess("b1", "/hub/app", "github.com/org/app",
                                      [("gotcha", "stripe needs the RAW body!"), ("fact", "Bob's fact")]),
                                 sess("b2", "/hub/other", None, [("fact", "Elsewhere")])])
        assert store.shared(alice) == {"a1": "2026-10-01T00:00:00Z"}

        def titles(machine, remotes=(), projects=()):
            return {x["title"]: x for x in store.lessons_for(machine, list(remotes), list(projects))["lessons"]}

        assert set(titles(alice)) == {"Bob's fact"}  # the merged lesson is Alice's own too; Elsewhere is not her project
        assert set(titles(bob)) == {"冪等性キーは Postgres に保存する"}
        assert titles(carol) == {}  # no sessions, no clones, no folders: no projects
        seen = store.lessons_for(carol, ["github.com/org/app"], [])
        assert seen["places"] == {"github.com/org/app": {"remote": "github.com/org/app", "project": "/hub/app", "mine": None}}
        merged = {x["title"]: x for x in seen["lessons"]}["stripe needs the RAW body!"]
        assert merged["sessions"] == 2 and merged["computers"] == ["Alice PC", "Bob PC"] and merged["tags"] == ["x"]
        assert merged["session_ids"] == ["b1", "a1"] and merged["language"] == "ja" and merged["body"] == "本文"
        assert set(titles(carol, projects=["/hub/other"])) == {"Elsewhere"}
        assert store.lessons_for(alice, [], [])["places"]["github.com/org/app"]["mine"] == "a1"

        # Dave's clone of the same repository is another project on the hub: his lesson still merges into the same one
        store.computer_seen(dave, "Dave PC", "Windows", "0.7.0")
        store.put_sessions(dave, [sess("d1", "/hub/dave-app", "github.com/org/app", [("gotcha", "Stripe needs the raw body.")])])
        merged = titles(carol, ["github.com/org/app"])["Stripe needs the raw body."]
        assert merged["sessions"] == 3 and merged["computers"] == ["Alice PC", "Bob PC", "Dave PC"]

        # someone limited to hub projects: only what sessions filed under them stated, whatever the computer asks for
        within = store.lessons_for(carol, ["github.com/org/app"], ["/hub/other"], within=["/hub/other"])["lessons"]
        assert [x["title"] for x in within] == ["Elsewhere"]
        within = {x["title"]: x for x in store.lessons_for(carol, ["github.com/org/app"], [], within=["/hub/app"])["lessons"]}
        assert set(within) == {"Stripe needs the raw body.", "冪等性キーは Postgres に保存する", "Bob's fact"}
        assert within["Stripe needs the raw body."]["computers"] == ["Alice PC", "Bob PC"]  # not Dave's other project
        assert store.lessons_for(carol, ["github.com/org/app"], [], within=[])["lessons"] == []

        # a computer that left a hub project: nothing filed under it counts, whatever else it asks for. Dave's clone of
        # the repository is another project, so what his session there stated still comes, from him alone
        gone = {x["title"]: x for x in store.lessons_for(alice, ["github.com/org/app"], ["/hub/other"], left=["/hub/app"])["lessons"]}
        assert set(gone) == {"Elsewhere", "Stripe needs the raw body."}
        assert gone["Stripe needs the raw body."]["computers"] == ["Dave PC"]
        assert store.lessons_for(bob, [], ["/hub/other"], left=["/hub/app", "/hub/other"])["lessons"] == []

        store.put_sessions(alice, [sess("a1", "/hub/app", "github.com/org/app", [])])  # analyzed again: no lessons
        left = titles(carol, ["github.com/org/app"])
        assert "冪等性キーは Postgres に保存する" not in left and left["Stripe needs the raw body."]["sessions"] == 2
        st = store.status()
        assert st["steps"] == [name for name, _ in team_store.STEPS]
        assert st["counts"]["computers"] == 3 and st["counts"]["sessions"] == 4 and st["counts"]["audit"] >= 9

        # a hub purges sessions: they go, and the lessons only they stated go with them
        assert store.forget_sessions(["b2", "nope"]) == 1
        assert titles(carol, projects=["/hub/other"]) == {}
        assert store.forget_sessions(["b1"]) == 1
        left = titles(carol, ["github.com/org/app"])
        assert "Bob's fact" not in left and left["Stripe needs the raw body."]["computers"] == ["Dave PC"]
        assert store.shared(bob) == {} and store.status()["counts"]["sessions"] == 2
    finally:
        with store._session() as c:
            c.execute(f"DROP SCHEMA {store.schema} CASCADE")
        store.close()


def _call(url, path, body=None, headers=None):
    import urllib.request

    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json", "X-Interlatch": "1", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_team_store_settings_from_the_dashboard(teamenv, monkeypatch):
    """Devices › Team store: tested before it is saved, the password goes in and never comes out, and only a request
    from the hub itself may change it."""
    import stat

    url, home = teamenv["url"], teamenv["cfg"].home
    tried = []
    monkeypatch.setattr(team_store, "probe", lambda params, **kw: tried.append(params) or {
        "where": f"{params['dbname']} on {params['host']}", "server": "17.11", "schema": "team", "steps": [],
        "counts": {}, "can_create": True})
    code, dv = _call(url, "/api/devices")
    assert code == 200 and dv["here"] is True and dv["role"] == "hub" and dv["store"]["settings"] is None

    form = {"host": "db.example.net", "port": "5432", "dbname": "team", "user": "chron", "password": "  p'w \"x\" ",
            "sslmode": "require"}
    code, r = _call(url, "/api/team-store/save", {"enabled": True, **form}, {"X-Forwarded-For": "100.64.0.9"})
    assert code == 403 and not (home / team_store.ENV_FILE).exists()  # through Tailscale: refused
    code, r = _call(url, "/api/team-store/save", {"enabled": True, **form, "port": "99999"})
    assert "port" in r["error"] and not tried
    code, r = _call(url, "/api/team-store/save", {"enabled": True, **form})
    assert r["ok"] and r["store"]["enabled"] and tried[-1]["password"] == "  p'w \"x\" "
    env = home / team_store.ENV_FILE
    assert stat.S_IMODE(env.stat().st_mode) == 0o600 and load_config(home).hub_store == "postgres"
    assert team_store.connection_params(load_config(home))["password"] == "  p'w \"x\" "  # exactly as typed
    code, dv = _call(url, "/api/devices")
    assert dv["store"]["settings"] == {"host": "db.example.net", "port": "5432", "dbname": "team", "user": "chron",
                                       "sslmode": "require", "password_set": True}
    assert "p'w" not in json.dumps(dv) and "p'w" not in json.dumps(r)

    code, r = _call(url, "/api/team-store/test", {**form, "password": "", "host": "other.example.net"})
    assert r["ok"] and tried[-1]["password"] == "  p'w \"x\" " and tried[-1]["host"] == "other.example.net"
    code, r = _call(url, "/api/team-store/save", {"enabled": False})
    assert r["ok"] and not r["store"]["enabled"] and env.exists() and load_config(home).hub_store == ""


def test_what_a_member_sends_from_the_dashboard(teamenv):
    from chronicle.server import App

    spoke = teamenv["spoke"]
    app = App(spoke)
    assert app.devices()["share"] == "knowledge" and app.devices()["store"] is None
    assert app.action_share_mode("everything") == {"ok": True, "share": "everything"}
    assert load_config(spoke.home).hub_share == "everything"
    assert "unknown share mode" in app.action_share_mode("all")["error"]
    assert App(teamenv["cfg"]).action_share_mode("knowledge")["error"] == "this computer has not joined a hub"


def test_settings_file_round_trip(tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    cfg = load_config(home)
    for pw in ("plain", " lead", "trail ", "'quoted'", '"dq"', "a=b#c"):
        team_store.write_settings(cfg, {"host": "h", "port": "5432", "dbname": "d", "user": "u", "password": pw, "sslmode": "require"})
        assert team_store.connection_params(cfg)["password"] == pw
    with pytest.raises(team_store.TeamStoreError, match="line break"):
        team_store.check_settings({"host": "h", "dbname": "d", "user": "u", "password": "a\nPGHOST=evil"}, None)
    with pytest.raises(team_store.TeamStoreError, match="password is missing"):
        team_store.check_settings({"host": "h", "dbname": "d", "user": "u"}, None)
    assert team_store.check_settings({"host": "h", "dbname": "d", "user": "u"}, {"password": "kept"})["password"] == "kept"
