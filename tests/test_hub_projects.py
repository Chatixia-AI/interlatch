"""Projects on a hub: set up ahead of time from a folder on the hub (`chronicle hub project add`), and people limited
to some of them (`--project`): what they see on the dashboard, what their computers may send, and what comes back."""

import gzip
import json
import re
from pathlib import Path

import pytest

from chronicle import hub, people
from chronicle.cli import main
from chronicle.config import load_config
from chronicle.db import connect

from conftest import CWD, SID
from test_hub import _serve
from test_people_web import MACHINE, _as, _call, _raw

PROJECTS = str(Path(CWD).parent)  # /Users/test/Projects: a folder above the synced session's own
OTHER = "/work/other-client"
OTHER_SID = "77777777-2222-4333-8444-555555555555"
# what someone limited to PROJECTS must never read: another project, a person's lessons, a transcript
CANARIES = ("OTHER-CANARY", "PREF-CANARY", "GLOBAL-CANARY", "Fix the failing login test", "Logout lives in session.py",
            "seconds vs ms")


def _project_setup(conn, cfg):
    """The synced session analyzed in PROJECTS (set up on the hub), and another client's project beside it."""
    got = hub.add_project(cfg, conn, PROJECTS)
    conn.execute("UPDATE sessions SET analysis_status = 'done', analyzed_at = '2026-09-21T00:00:00Z', "
                 "llm_title = 'Resona title', summary = 'Resona summary' WHERE id = ?", (SID,))
    for title, scope, kind in (("Resona lesson", "project", "gotcha"), ("PREF-CANARY likes tabs", "project", "preference"),
                               ("GLOBAL-CANARY habit", "global", "gotcha")):
        conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, confidence, "
                     "source, fingerprint, created_at, updated_at, status) VALUES (?, ?, 'Projects', ?, ?, 'b', ?, 'high', "
                     "'analysis', ?, '2026-09-21', '2026-09-21', 'active')", (SID, PROJECTS, kind, title, scope, title))
    me = hub.local_machine(cfg)["id"]
    conn.execute("INSERT INTO sessions(id, source, agent, project_path, project_name, title, llm_title, summary, "
                 "first_prompt, analysis_status, started_at, machine_id) VALUES (?, 'transcript', 'claude', ?, "
                 "'OTHER-CANARY-name', 'OTHER-CANARY title', 'OTHER-CANARY title', 'OTHER-CANARY summary', 'OTHER-CANARY prompt', "
                 "'done', '2026-09-22T00:00:00Z', ?)", (OTHER_SID, OTHER, me))
    conn.execute("INSERT INTO events(session_id, seq, ts, kind, agent_id, text) VALUES (?, 1, '2026-09-22T00:00:00Z', "
                 "'prompt', '', 'OTHER-CANARY event')", (OTHER_SID,))
    conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, confidence, "
                 "source, fingerprint, created_at, updated_at, status) VALUES (?, ?, 'other-client', 'gotcha', "
                 "'OTHER-CANARY lesson', 'b', 'project', 'high', 'analysis', 'oc', '2026-09-22', '2026-09-22', 'active')",
                 (OTHER_SID, OTHER))
    conn.execute("INSERT INTO project_kb(project_path, project_name, updated_at, n_items, kb_json, knowledge_max_id) "
                 "VALUES (?, 'other-client', '2026-09-22', 1, ?, 0)",
                 (OTHER, json.dumps({"summary": "OTHER-CANARY kb", "sections": []})))
    conn.commit()
    return got


@pytest.fixture()
def limited(synced, monkeypatch):
    """The synced computer as a hub with PROJECTS set up, another client's project, and Bob, a member who sees only
    PROJECTS, signed in to the dashboard."""
    monkeypatch.setattr(hub.IngestTrigger, "request", lambda self: None)
    cfg, conn = synced["cfg"], synced["conn"]
    token = hub.new_token(cfg)
    got = _project_setup(conn, cfg)
    ada = people.add(conn, "Ada", "ada@example.com", "admin")
    bob = people.add(conn, "Bob", "bob@example.com", "member", by=ada, projects=[PROJECTS])
    session = people.open_browser(conn, people.invite(conn, bob["id"]), "test")[1]
    app, httpd, url = _serve(cfg)
    synced.update(app=app, url=url, token=token, bob=bob, ada=ada, bob_session=session, setup=got)
    yield synced
    httpd.shutdown()


def test_projects_of():
    assert people.projects_of(None) is None
    assert people.projects_of({"role": "admin", "projects_json": "[]"}) is None  # admins see everything
    assert people.projects_of({"role": "member", "projects_json": None}) is None  # added before projects existed
    assert people.projects_of({"role": "member", "projects_json": '["/a"]'}) == ["/a"]
    assert people.projects_of({"role": "readonly", "projects_json": "nonsense"}) == []  # never everything by mistake
    assert people.clean_projects(["/b/", "/a", "/a"]) == ["/a", "/b"]
    with pytest.raises(people.PeopleError):
        people.clean_projects(["relative"])
    with pytest.raises(people.PeopleError):
        people.clean_projects("/a")


def test_a_project_set_up_ahead_of_time(synced):
    """A folder on the hub becomes one project: its sessions and session-less notes are filed under it, new ones too,
    another computer can add a folder to it before anything was sent, and removing it files them back."""
    from chronicle.hub import resolver

    cfg, conn = synced["cfg"], synced["conn"]
    assert conn.execute("SELECT project_path FROM sessions WHERE id = ?", (SID,)).fetchone()[0] == CWD
    got = hub.add_project(cfg, conn, PROJECTS + "/")
    assert got == {"path": PROJECTS, "name": "Projects", "moved": 1, "sessions": 1, "existed": False}
    row = conn.execute("SELECT project_path, project_name, machine_path FROM sessions WHERE id = ?", (SID,)).fetchone()
    assert tuple(row) == (PROJECTS, "Projects", CWD)
    assert not conn.execute("SELECT COUNT(*) FROM knowledge WHERE project_path = ?", (CWD,)).fetchone()[0]
    assert resolver(cfg, conn).resolve(None, CWD + "/sub") == PROJECTS  # what ingest files new sessions under
    assert hub.add_project(cfg, conn, PROJECTS)["existed"]
    with pytest.raises(hub.HubError, match="overlaps"):
        hub.add_project(cfg, conn, CWD)
    with pytest.raises(hub.HubError, match="not a folder"):
        hub.add_project(cfg, conn, "/nowhere/at-all")
    for everything in ("/", str(Path.home()), str(Path.home().parent)):
        with pytest.raises(hub.HubError, match="holds every project"):
            hub.add_project(cfg, conn, everything)
    projects = {p["path"]: p for p in hub.hub_projects(conn)}
    assert projects[PROJECTS]["set_up"] and projects[PROJECTS]["sessions"] == 1

    conn.execute("DELETE FROM sessions")  # a project set up before any session is still one to add folders to
    conn.execute("DELETE FROM files_state")
    conn.commit()
    assert [p["path"] for p in hub.hub_projects(conn)] == [PROJECTS]
    from chronicle.ingest import sync

    sync(cfg, conn)  # recorded again: filed under the project from the start
    row = conn.execute("SELECT project_path, machine_path FROM sessions WHERE id = ?", (SID,)).fetchone()
    assert tuple(row) == (PROJECTS, CWD)

    out = hub.remove_project(cfg, conn, PROJECTS)
    assert out == {"path": PROJECTS, "moved": 1} and hub.declared_projects(conn) == []
    row = conn.execute("SELECT project_path, machine_path FROM sessions WHERE id = ?", (SID,)).fetchone()
    assert tuple(row) == (CWD, None)
    with pytest.raises(hub.HubError):
        hub.remove_project(cfg, conn, PROJECTS)


def test_project_and_access_commands(synced, monkeypatch, capsys):
    cfg, conn = synced["cfg"], synced["conn"]
    hub.new_token(cfg)
    assert main(["hub", "project", "add", PROJECTS]) == 0
    out = capsys.readouterr().out
    assert "Set up Projects" in out and "with 1 session so far" in out
    assert main(["hub", "project", "list"]) == 0
    assert "set up here" in capsys.readouterr().out
    assert main(["hub", "invite", "Bob", "--email", "bob@example.com"]) == 2  # nothing until granted
    assert "Which projects should Bob see?" in capsys.readouterr().out
    assert main(["hub", "invite", "Bob", "--email", "bob@example.com", "--project", "nope"]) == 1
    assert "no project called nope" in capsys.readouterr().out
    assert main(["hub", "invite", "Bob", "--email", "bob@example.com", "--project", "Projects"]) == 0
    assert "a member, seeing Projects" in capsys.readouterr().out
    bob = people.by_email(conn, "bob@example.com")
    assert people.projects_of(bob) == [PROJECTS]
    assert main(["hub", "access", "bob@example.com", "--all-projects"]) == 0
    assert "Bob now sees every project" in capsys.readouterr().out
    assert people.projects_of(people.by_email(conn, "bob@example.com")) is None
    assert main(["hub", "access", "bob@example.com", "--project", PROJECTS]) == 0
    assert "shared token is on" in capsys.readouterr().out
    assert main(["hub", "people"]) == 0
    assert "sees Projects" in capsys.readouterr().out
    assert main(["hub", "invite", "Ann", "--email", "ann@example.com", "--role", "admin"]) == 0  # admins see everything
    assert main(["hub", "project", "remove", PROJECTS]) == 0
    assert "went back to their own folders" in capsys.readouterr().out
    assert [a["action"] for a in people.audit_log(conn)][:3] == ["invite", "add", "projects"]


def _get_routes() -> list[str]:
    """Every GET route the dashboard answers, read from server.py, with ids filled in."""
    src = (Path(hub.__file__).parent / "server.py").read_text()
    body = src[src.index("        def _get(self):"):src.index("        def _import_upload(self):")]
    routes = set(re.findall(r'p == "(/api/[^"]+)"', body))
    for pat in re.findall(r're\.fullmatch\(r"(/api/[^"]+)", p\)', body):
        routes.add(pat.replace(r"([\w-]+)", "{sid}").replace(r"(\d+)", "1"))
    return sorted(routes)


def test_someone_limited_to_projects_sees_nothing_else(limited):
    """Every GET route, called by a person limited to PROJECTS, with query strings that point at the other project:
    nothing of it, of the transcripts, or of anyone's personal lessons comes back."""
    url, s = limited["url"], _as(limited["bob_session"])
    routes = _get_routes()
    assert {"/api/sessions", "/api/search", "/api/sessions/{sid}/events", "/api/devices", "/api/export"} <= set(routes)
    admin_sees = _raw(url, f"/api/sessions/{OTHER_SID}")[2].decode()
    assert "OTHER-CANARY" in admin_sees  # the canaries are there to be found

    answered = {}
    for route in routes:
        for sid, where in ((OTHER_SID, OTHER), (SID, PROJECTS)):
            path = route.format(sid=sid)
            qs = (f"?q=Fix&project={where}&path={where}&root={where}&ids={sid}&days=all&term=OTHER-CANARY&limit=50"
                  f"&format=md&check=1")
            status, _, data = _raw(url, path + qs, headers=s)
            text = data.decode(errors="replace")
            for c in CANARIES:
                assert c not in text, f"{path} showed {c!r}"
            answered[path] = status
    ok = {p for p, st in answered.items() if st == 200}
    assert "/api/sessions" in ok and "/api/knowledge" in ok and "/api/overview" in ok and f"/api/sessions/{SID}" in ok
    assert answered[f"/api/sessions/{OTHER_SID}"] == 404
    for denied in ("/api/search", f"/api/sessions/{SID}/events", f"/api/sessions/{SID}/matches", "/api/devices",
                   "/api/export", "/api/status", "/api/reviews", "/api/glossary", "/api/map", "/api/artifacts",
                   "/api/file", "/api/files", "/api/suggestions", "/api/friction", "/api/team-store", "/api/people"):
        assert answered[denied] == 403, denied

    code, r = _call(url, "/api/sessions", headers=s)
    assert [x["id"] for x in r["items"]] == [SID] and r["items"][0]["title"] == "Resona title"
    code, r = _call(url, f"/api/sessions/{SID}", headers=s)
    assert r["limited"] and r["summary"] == "Resona summary" and r["prompts"] == [] and "first_prompt" not in r
    assert [k["title"] for k in r["knowledge"]] == ["Resona lesson"]
    code, r = _call(url, "/api/projects", headers=s)
    assert [p["project_path"] for p in r] == [PROJECTS]
    code, r = _call(url, "/api/knowledge", headers=s)
    assert [k["title"] for k in r["items"]] == ["Resona lesson"]
    code, r = _call(url, "/api/sessions?q=summary", headers=s)  # titles and summaries, not transcripts
    assert r["total"] == 1
    code, r = _call(url, "/api/sessions?q=login", headers=s)
    assert r["total"] == 0
    code, me = _call(url, "/api/me", headers=s)
    assert me["viewer"]["projects"] == [PROJECTS]
    assert _call(url, "/api/people/add", {"name": "X", "projects": "all"}, s)[0] == 403  # members change nothing

    # the same person given every project sees the rest again
    people.set_projects(limited["conn"], limited["bob"]["id"], None)
    status, _, data = _raw(url, f"/api/sessions/{OTHER_SID}", headers=s)
    assert status == 200 and "OTHER-CANARY" in data.decode()


def _bob_computer(env):
    code = people.invite(env["conn"], env["bob"]["id"])
    status, r = _call(env["url"], "/api/hub/join", {"code": code, "machine": MACHINE, "name": "Bob laptop"})
    assert status == 200 and r["share"] == "knowledge" and [p["path"] for p in r["projects"]] == [PROJECTS]
    return r["token"]


def _hello(env, token, **extra):
    body = {"protocol": hub.PROTOCOL, "machine": MACHINE, "name": "Bob laptop", "share": "knowledge", **extra}
    return _call(env["url"], "/api/hub/hello", body, {"Authorization": f"Bearer {token}"})


def test_a_limited_computer_hears_of_and_sends_only_its_projects(limited):
    url, conn = limited["url"], limited["conn"]
    token = _bob_computer(limited)
    auth = {"Authorization": f"Bearer {token}"}

    status, r = _hello(limited, token, share="everything")
    assert status == 400 and "share knowledge only" in r["error"]
    status, r = _hello(limited, token, folders={"/home/bob/resona": PROJECTS, "/home/bob/other": OTHER})
    assert status == 200 and r["scope"] == [PROJECTS]
    assert [p["path"] for p in r["projects"]] == [PROJECTS] and set(r["folders"]) == {"/home/bob/resona"}
    assert OTHER not in json.dumps(r)
    assert hub.folders_of(conn, MACHINE) == {"/home/bob/resona": PROJECTS}

    req_status, _, data = _raw(url, f"/api/hub/file?machine={MACHINE}&root=claude&path=projects/x/a.jsonl&size=2&mtime=1",
                               {"x": 1}, auth)
    assert req_status == 403 and b"share knowledge only" in data

    def shared(sid, path):
        return {"id": sid, "project_path": path, "agent": "claude", "analysis_status": "done", "llm_title": sid,
                "analyzed_at": "2026-10-01T00:00:00Z", "knowledge": [
                    {"kind": "gotcha", "title": f"lesson {sid}", "body": "b", "scope": "project", "status": "active"}]}

    blob = gzip.compress(json.dumps({"version": 1, "sessions": [shared("bob-in", "/home/bob/resona/app"),
                                                                 shared("bob-out", "/home/bob/other/app")]}).encode())
    import urllib.request

    req = urllib.request.Request(f"{url}/api/hub/sessions?machine={MACHINE}", data=blob, method="POST",
                                 headers={**auth, "Content-Type": "application/gzip", "X-Interlatch": "1"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        r = json.loads(resp.read())
    assert r["stored"] == 1 and r["refused"] == 1
    rows = dict(conn.execute("SELECT id, project_path FROM sessions WHERE id IN ('bob-in', 'bob-out')").fetchall())
    assert rows == {"bob-in": PROJECTS}


def test_a_limited_computer_shares_only_its_projects(tmp_path, monkeypatch):
    """The computer's side: with a scope from the hub, only sessions it files under those projects leave it."""
    from chronicle.config import load_config as lc

    home = tmp_path / "member"
    home.mkdir()
    (home / "config.toml").write_text('[hub]\nurl = "http://hub"\nshare = "knowledge"\n'
                                      '[hub.folders]\n"/home/bob/resona" = "/srv/Resona"\n"/home/bob/other" = "/srv/Other"\n')
    monkeypatch.setenv("INTERLATCH_HOME", str(home))
    cfg = lc(home)
    conn = connect(cfg.db_path)
    for sid, path in (("in", "/home/bob/resona/app"), ("out", "/home/bob/other/app"), ("none", "/home/bob/misc"),
                      ("repo", "/home/bob/misc/lib")):
        conn.execute("INSERT INTO sessions(id, source, agent, project_path, analysis_status, analyzed_at) VALUES "
                     "(?, 'transcript', 'claude', ?, 'done', '2026-10-01')", (sid, path))
    conn.commit()
    conn.close()
    monkeypatch.setattr(hub, "git_info", lambda p: ("/home/bob/misc/lib", "github.com/acme/lib") if p.endswith("lib") else None)
    recs, _, skipped = hub._shared_records(cfg, {}, scope=["/srv/Resona"], remotes={"github.com/acme/lib": "/srv/Resona"})
    assert sorted(r["id"] for r in recs) == ["in", "repo"] and skipped == 2
    recs, _, _ = hub._shared_records(cfg, {})  # not limited: everything analyzed, as before
    assert len(recs) == 4
    assert all("title" not in r and "machine_path" not in r for r in recs)


def test_the_hubs_own_sessions_reach_the_team_store(limited, monkeypatch):
    """With a team store, the hub owner's analyzed sessions in a project set up there go to the store as well, so a
    member's computer gets those lessons back; another project's never do."""
    from chronicle import team_store
    from chronicle.config import set_config_value

    from test_team import FakeStore

    fake = FakeStore()
    cfg = limited["cfg"]
    monkeypatch.setattr(team_store, "get", lambda c: fake if c.hub_store == "postgres" and not c.is_spoke else None)
    set_config_value(cfg, "hub", "store", '"postgres"')
    token = _bob_computer(limited)
    _hello(limited, token)
    status, r = _call(limited["url"], "/api/hub/lessons", {"machine": MACHINE, "projects": [PROJECTS, OTHER],
                                                           "remotes": []}, {"Authorization": f"Bearer {token}"})
    assert status == 200 and r["team"]
    assert [x["title"] for x in r["lessons"]] == ["Resona lesson"]  # project lessons only, of PROJECTS only
    me = hub.local_machine(load_config(cfg.home))["id"]
    assert set(fake.shared(me)) == {SID}
    assert "OTHER-CANARY" not in json.dumps(fake.sessions) and "PREF-CANARY" not in json.dumps(fake.lessons)


def test_a_limited_connection_hides_columns_and_tables(synced):
    """access.limit on its own: other projects' rows, prompts, paths and transcripts are gone from the connection."""
    from chronicle import access

    cfg, conn = synced["cfg"], synced["conn"]
    _project_setup(conn, cfg)
    c = connect(cfg.db_path)
    try:
        access.limit(c, [PROJECTS])
        rows = [dict(r) for r in c.execute("SELECT * FROM sessions")]
        assert [r["id"] for r in rows] == [SID]
        assert rows[0]["title"] == "Resona title" and rows[0]["summary"] == "Resona summary"
        for col in ("first_prompt", "last_prompt", "machine_path", "claude_dir", "transcript_path", "archive_path",
                    "commands_json", "branches_json", "hooks_json", "statusline_json", "analysis_json", "ai_title"):
            assert rows[0][col] is None, col
        assert [r[0] for r in c.execute("SELECT title FROM knowledge")] == ["Resona lesson"]
        for t in access.EMPTY:
            assert c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
        assert c.execute("SELECT COUNT(*) FROM project_kb WHERE project_path = ?", (OTHER,)).fetchone()[0] == 0
    finally:
        c.close()
    seen = conn.execute("SELECT COUNT(*) FROM sessions WHERE id IN (?, ?)", (SID, OTHER_SID)).fetchone()[0]
    assert seen == 2  # other connections see everything


def test_notes_inside_a_project_set_up_here(synced):
    """A session started in a folder inside a project set up on this hub gets that project's notes."""
    from chronicle.hooks import build_session_context

    cfg, conn = synced["cfg"], synced["conn"]
    _project_setup(conn, cfg)
    text = build_session_context(cfg, CWD) or ""
    assert "Resona lesson" in text and "Recent sessions here: 09-20 Fix login token expiry" in text


def test_shared_projects_on_the_dashboard(limited):
    """Settings › Devices › Shared projects: admins see which projects leave the hub and who sees them; only someone
    at the hub itself sets one up or stops sharing it, and both go into the audit log."""
    url, conn = limited["url"], limited["conn"]
    ada = _as(people.open_browser(conn, people.invite(conn, limited["ada"]["id"]), "test")[1])
    bob = _as(limited["bob_session"])

    code, r = _call(url, "/api/projects/shared")  # at the hub itself
    assert code == 200 and r["here"] and r["hub"]
    [x] = r["shared"]
    assert x["path"] == PROJECTS and x["name"] == "Projects" and x["sessions"] == 1
    assert [p["name"] for p in x["people"]] == ["Bob"] and x["everyone"] == 1  # Ada, an admin, sees every project
    assert OTHER in [c["path"] for c in r["candidates"]] and PROJECTS not in [c["path"] for c in r["candidates"]]
    code, r = _call(url, "/api/projects/shared", headers=ada)  # an admin elsewhere sees it, can't change it
    assert code == 200 and r["here"] is False
    assert _call(url, "/api/projects/shared", headers=bob)[0] == 403  # limited people: not theirs to see
    assert _call(url, "/api/projects/shared/add", {"folder": OTHER}, ada)[0] == 403
    assert _call(url, "/api/projects/shared/remove", {"path": PROJECTS}, ada)[0] == 403

    code, r = _call(url, "/api/projects/shared/add", {"folder": "relative/x"})
    assert code == 400
    code, r = _call(url, "/api/projects/shared/add", {"folder": OTHER})
    assert code == 200 and r["result"]["path"] == OTHER and r["result"]["sessions"] == 1
    assert sorted(x["path"] for x in r["shared"]) == sorted([PROJECTS, OTHER])
    code, rows = _call(url, "/api/projects")
    flags = {p["project_path"]: p["shared"] for p in rows}
    assert flags[PROJECTS] and flags[OTHER] and not flags["/Users/test"]  # the history-only session's folder
    assert _call(url, f"/api/project?path={OTHER}")[1]["shared"] is True

    code, r = _call(url, "/api/projects/shared/remove", {"path": OTHER})
    assert code == 200 and [x["path"] for x in r["shared"]] == [PROJECTS]
    assert [a["action"] for a in people.audit_log(conn)][:2] == ["project-remove", "project-add"]
