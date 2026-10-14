"""A member's computer and the hub's projects: leaving one (what it shared stays; nothing more goes and no lessons come
back), rejoining it, and removing a folder added by mistake (the hub takes back what it shared from there). And
Team › Hub settings, where an admin renames the hub or changes its address."""

import json
import urllib.error
import urllib.request

from interlatch import hub, people
from interlatch.cli import main
from interlatch.config import load_config, set_config_value, toml_table
from interlatch.server import App

from conftest import CWD
from test_hub import SPOKE_CWD, SPOKE_SID, _analyzed_on_spoke, _serve
from test_people_web import REMOTE, _as, _call, hubweb, team  # noqa: F401  (the fixtures)
from test_team import TEAMMATE, FakeStore, _team_rows, _teammate_session, teamenv  # noqa: F401


def _in_project(teamenv):  # noqa: F811
    """The spoke shares only the hub's projects; its folder is added to the hub's project (CWD), with a teammate's
    lesson there."""
    spoke = teamenv["spoke"]
    set_config_value(spoke, "hub", "all_folders", "false")
    set_config_value(spoke, "hub", "folders", toml_table({SPOKE_CWD: CWD}))  # exists only on the other computer
    teamenv["fake"].put_sessions(TEAMMATE, [_teammate_session("mate-1", [("gotcha", "Teammate lesson")], project=CWD)])
    _analyzed_on_spoke(spoke, title="Mine", at="2026-10-02T00:00:00Z", lessons=[("p1", "project", "fact")])
    spoke = load_config(spoke.home)
    report = hub.push(spoke)
    assert report.sent == 1 and report.team == 1 and [r["title"] for r in _team_rows(spoke)] == ["Teammate lesson"]
    assert teamenv["conn"].execute("SELECT project_path FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == CWD
    return spoke


def _shared_at(conn):
    return conn.execute("SELECT project_path, analyzed_at FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()


def test_leaving_keeps_what_was_shared_and_stops_the_rest(teamenv):  # noqa: F811
    fake, conn = teamenv["fake"], teamenv["conn"]
    spoke = _in_project(teamenv)
    me = hub.local_machine(spoke)["id"]

    spoke = hub.leave_project(spoke, CWD)
    assert spoke.hub_left == [CWD] and spoke.hub_folders == {SPOKE_CWD: CWD}  # the folder stays: so do its sessions
    _analyzed_on_spoke(spoke, title="Mine, again", at="2026-10-03T00:00:00Z", lessons=[("p2", "project", "fact")])
    report = hub.push(spoke)
    assert report.sent == 0 and report.skipped == 1 and report.team == 0 and not report.errors
    assert _team_rows(spoke) == []  # the project's lessons went with it
    assert tuple(_shared_at(conn)) == (CWD, "2026-10-02T00:00:00Z")  # what it shared stays, as it was, in the project
    assert fake.sessions[SPOKE_SID]["project"] == CWD
    assert hub.left_of(conn, me) == [CWD]
    ask = {"machine": me, "remotes": [], "projects": [CWD]}  # the hub itself leaves the project's lessons out too
    hub_cfg = load_config(teamenv["cfg"].home)
    assert [x["title"] for x in hub.team_lessons(hub_cfg, ask)["lessons"]] == ["Teammate lesson"]
    assert hub.team_lessons(hub_cfg, {**ask, "left": [CWD]})["lessons"] == []
    audit = people.audit_log(conn)[0]
    assert (audit["action"], audit["detail"]["path"], audit["detail"]["machine"]) == ("leave", CWD, me)

    spoke = hub.rejoin_project(spoke, CWD)
    report = hub.push(spoke)
    assert report.sent == 1 and report.team == 1 and [r["title"] for r in _team_rows(spoke)] == ["Teammate lesson"]
    assert tuple(_shared_at(conn)) == (CWD, "2026-10-03T00:00:00Z")
    assert hub.left_of(conn, me) == [] and people.audit_log(conn)[0]["action"] == "rejoin"


def test_a_hub_that_still_sends_a_left_projects_lessons_is_ignored(teamenv, monkeypatch):  # noqa: F811
    fake = teamenv["fake"]
    spoke = hub.leave_project(_in_project(teamenv), CWD)
    # an older hub knows nothing of [hub] left: it answers with every lesson of the places this computer worked in
    monkeypatch.setattr(fake, "lessons_for", lambda *a, left=None, **k: FakeStore.lessons_for(fake, *a, **k))
    assert hub.push(spoke).team == 0 and _team_rows(spoke) == []


def test_only_a_computer_that_shares_knowledge_leaves(teamenv):  # noqa: F811
    spoke = teamenv["spoke"]
    set_config_value(spoke, "hub", "share", '"everything"')
    try:
        hub.leave_project(load_config(spoke.home), CWD)
    except hub.FolderError as exc:
        assert "sends them all" in str(exc)
    else:
        raise AssertionError("a computer that sends transcripts left a project")
    assert load_config(spoke.home).hub_left == []


def test_removing_a_folder_takes_back_what_it_shared(teamenv):  # noqa: F811
    fake, conn = teamenv["fake"], teamenv["conn"]
    spoke = hub.leave_project(_in_project(teamenv), CWD)

    spoke, taken = hub.remove_folder(spoke, SPOKE_CWD)
    assert taken == 1 and spoke.hub_folders == {} and spoke.hub_left == []  # nothing of it left to leave
    assert _shared_at(conn) is None and SPOKE_SID not in fake.sessions
    assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE session_id = ?", (SPOKE_SID,)).fetchone()[0] == 0
    audit = people.audit_log(conn)[0]
    assert (audit["action"], audit["detail"]["sessions"], audit["detail"]["path"]) == ("withdraw", 1, CWD)
    assert hub.push(spoke).sent == 0  # its folder is in no project now: it stays here

    # unlike a purge, the hub doesn't keep it out: added to the right project, it is shared again
    spoke = hub._save_hub(spoke, folders={SPOKE_CWD: CWD})
    assert hub.push(spoke).sent == 1 and tuple(_shared_at(conn)) == (CWD, "2026-10-02T00:00:00Z")


def test_removing_a_folder_changes_nothing_when_the_hub_cant_be_reached(teamenv, monkeypatch):  # noqa: F811
    spoke = _in_project(teamenv)

    def down(self, *a, **k):
        raise hub.HubUnreachable("can't reach the hub")

    monkeypatch.setattr(hub.HubClient, "request", down)
    try:
        hub.remove_folder(spoke, SPOKE_CWD)
    except hub.HubUnreachable:
        pass
    else:
        raise AssertionError("removed without the hub")
    assert load_config(spoke.home).hub_folders == {SPOKE_CWD: CWD}
    assert _shared_at(teamenv["conn"]) is not None


def _withdraw(url, token, machine, folder=SPOKE_CWD):
    req = urllib.request.Request(f"{url}/api/hub/withdraw", data=json.dumps({"machine": machine, "folder": folder}).encode(),
                                 method="POST", headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_a_computer_takes_back_only_its_own(teamenv):  # noqa: F811
    conn, url = teamenv["conn"], teamenv["url"]
    spoke = _in_project(teamenv)
    me = hub.local_machine(spoke)["id"]
    other = "aaaaaaaa-0000-4000-8000-000000000001"
    p = people.add(conn, "Yuma", "yuma@example.com", "member", projects=None)
    for machine, token in ((me, "mine"), (other, "theirs")):
        conn.execute("INSERT INTO people_tokens(token_hash, person_id, kind, machine_id, created_at) VALUES "
                     "(?, ?, 'computer', ?, '2026-10-01')", (people._hash(token), p["id"], machine))
    conn.commit()
    set_config_value(teamenv["cfg"], "hub", "shared_token", "false")
    # Yuma's other computer has a folder at the same path, added to the same project: none of it is this one's; nor
    # is what this one filed under the project from elsewhere (another folder, or a repository by its git remote)
    conn.execute("INSERT INTO sessions(id, source, agent, machine_id, project_path, machine_path, analysis_status) "
                 "VALUES ('theirs-1', 'remote', 'claude', ?, ?, ?, 'done')", (other, CWD, SPOKE_CWD))
    conn.execute("INSERT INTO sessions(id, source, agent, machine_id, project_path, machine_path, analysis_status) "
                 "VALUES ('mine-elsewhere', 'remote', 'claude', ?, ?, '/home/test/code/demo-app-2', 'done')", (me, CWD))
    conn.execute("INSERT INTO kv(key, value) VALUES (?, ?)", (hub.FOLDERS_KV + other, json.dumps({SPOKE_CWD: CWD})))
    conn.commit()

    assert _withdraw(url, "theirs", me)[0] == 401  # another computer's token never speaks for this one
    assert _withdraw(url, hub.read_token(spoke), me)[0] == 401  # nor the shared token, once it is off
    assert _shared_at(conn) is not None
    assert _withdraw(url, "mine", me, "/home/test/code")[1]["sessions"] == 0  # not a folder it added: nothing
    code, r = _withdraw(url, "mine", me)
    assert code == 200 and r == {"sessions": 1, "project": CWD} and _shared_at(conn) is None
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id IN ('theirs-1', 'mine-elsewhere')").fetchone()[0] == 2


def test_the_command_line_leaves_and_rejoins(teamenv, monkeypatch, capsys):  # noqa: F811
    spoke = _in_project(teamenv)
    monkeypatch.setenv("INTERLATCH_HOME", str(spoke.home))
    assert main(["hub", "leave", "--project", "nope", "--no-push"]) == 1
    assert main(["hub", "leave", "--project", CWD, "--no-push"]) == 0
    assert "What it already shared stays on the hub" in " ".join(capsys.readouterr().out.split())
    assert load_config(spoke.home).hub_left == [CWD] and load_config(spoke.home).hub_url  # still in the hub
    assert main(["hub", "rejoin", "--project", CWD]) == 0
    assert load_config(spoke.home).hub_left == [] and "Rejoined" in capsys.readouterr().out
    assert main(["hub", "remove-folder", SPOKE_CWD]) == 0
    assert "took back the 1 session" in " ".join(capsys.readouterr().out.split())
    assert load_config(spoke.home).hub_folders == {} and _shared_at(teamenv["conn"]) is None


def test_the_dashboard_lists_leaves_and_rejoins_from_this_computer_only(teamenv, monkeypatch):  # noqa: F811
    spoke = _in_project(teamenv)
    hub.handshake(spoke)  # the hub counts what a folder sent as of the hello: the next one has the push before
    monkeypatch.setattr(App, "action_push", lambda self: False)  # the hub hears of it at the next push
    app, httpd, url = _serve(spoke)
    try:
        dv = _call(url, "/api/devices")[1]
        [row] = dv["projects"]
        assert (row["path"], row["left"], [f["folder"] for f in row["folders"]]) == (CWD, False, [SPOKE_CWD])
        assert row["folders"][0]["sessions"] == 1 and dv["hub_name"] == hub.local_machine(teamenv["cfg"])["name"]  # no [hub] name
        assert dv["last_push"]["sent"] == 1 and dv["last_push"]["kind"] == "knowledge"

        for path, body in (("/api/devices/projects/leave", {"project": CWD}), ("/api/devices/projects/remove", {"folder": SPOKE_CWD}),
                           ("/api/devices/projects/choices", {}), ("/api/devices/leave-hub", {})):
            assert _call(url, path, body, REMOTE)[0] == 403, path  # from a phone: never
        assert load_config(spoke.home).hub_left == [] and load_config(spoke.home).is_spoke

        code, r = _call(url, "/api/devices/projects/leave", {"project": CWD})
        assert code == 200 and r["ok"] and load_config(spoke.home).hub_left == [CWD]
        assert _call(url, "/api/devices")[1]["projects"][0]["left"] is True
        assert _call(url, "/api/devices/projects/rejoin", {"project": CWD})[0] == 200
        assert load_config(spoke.home).hub_left == []
        assert _call(url, "/api/devices/projects/leave", {"project": "relative"})[0] == 400

        choices = _call(url, "/api/devices/projects/choices", {})[1]
        assert CWD in [p["path"] for p in choices["projects"]] and SPOKE_CWD in [f["path"] for f in choices["folders"]]
        code, r = _call(url, "/api/devices/projects/add", {"project": "/no/such", "folder": str(teamenv["tmp"])})
        assert code == 400 and "no such project" in r["error"]
        code, r = _call(url, "/api/devices/projects/add", {"project": CWD, "folder": str(teamenv["tmp"] / "missing")})
        assert code == 400 and "is not a folder" in r["error"]
        code, r = _call(url, "/api/devices/projects/add", {"project": CWD, "folder": str(teamenv["tmp"])})
        assert code == 200 and load_config(spoke.home).hub_folders[str(teamenv["tmp"].resolve())] == CWD

        code, r = _call(url, "/api/devices/projects/remove", {"folder": SPOKE_CWD})
        assert code == 200 and r["taken"] == 1 and SPOKE_CWD not in load_config(spoke.home).hub_folders

        assert _call(url, "/api/devices/leave-hub", {})[1]["ok"]
        assert not load_config(spoke.home).is_spoke and hub.read_token(load_config(spoke.home)) is None
    finally:
        httpd.shutdown()


# ---- Team › Hub settings
def test_an_admin_renames_the_hub_and_sets_its_address(team):  # noqa: F811
    url, s, cfg = team["url"], team["sessions"], team["cfg"]
    code, hs = _call(url, "/api/team/settings", headers=_as(s["Ada"]))
    assert code == 200 and hs["name"] == "" and hs["managed"] == {"address": False, "name": False} and not hs["container"]
    for who in ("Bob", "Vic"):
        assert _call(url, "/api/team/settings", headers=_as(s[who]))[0] == 403
        assert _call(url, "/api/team/settings", {"name": "Mine"}, _as(s[who]))[0] == 403

    code, hs = _call(url, "/api/team/settings", {"name": "  Acme\n team\x07 "}, _as(s["Ada"]))
    assert code == 200 and hs["name"] == "Acme team" and load_config(cfg.home).hub_name == "Acme team"
    assert _call(url, "/api/me", headers=_as(s["Bob"]))[1]["hub"]["name"] == "Acme team"
    assert _call(url, "/api/team/settings", {"name": "x" * 81}, _as(s["Ada"]))[0] == 400

    for bad in ("chronicle.example.com", "ftp://x.example.com", "https://x.example.com/path", "https://user@x.example.com",
                "https://", "https://x.example.com/?a=1"):
        assert _call(url, "/api/team/settings", {"address": bad}, _as(s["Ada"]))[0] == 400, bad
    code, hs = _call(url, "/api/team/settings", {"address": "https://Hub.Example.com:8443/"}, _as(s["Ada"]))
    assert code == 200 and hs["address"] == "https://Hub.Example.com:8443"
    assert load_config(cfg.home).server_allowed_hosts[-1] == "hub.example.com"  # its host name is let in, as enable does
    audit = _call(url, "/api/people", headers=_as(s["Ada"]))[1]["audit"]
    assert [(a["action"], a["actor_name"]) for a in audit[:2]] == [("settings", "Ada"), ("settings", "Ada")]
    assert _call(url, "/api/team/settings", {}, _as(s["Ada"]))[0] == 400  # nothing to change


def test_a_containers_variables_set_its_name_and_address(team, monkeypatch):  # noqa: F811
    url, s, cfg = team["url"], team["sessions"], team["cfg"]
    monkeypatch.setenv("CHRONICLE_CONTAINER", "1")
    monkeypatch.setenv("CHRONICLE_HUB_NAME", "Acme")
    hs = _call(url, "/api/team/settings", headers=_as(s["Ada"]))[1]
    assert hs["container"] and hs["managed"] == {"address": True, "name": True}
    code, r = _call(url, "/api/team/settings", {"address": "https://other.example.com"}, _as(s["Ada"]))
    assert code == 400 and "INTERLATCH_HUB_URL" in r["error"]
    code, r = _call(url, "/api/team/settings", {"name": "Other"}, _as(s["Ada"]))
    assert code == 400 and "INTERLATCH_HUB_NAME" in r["error"]
    monkeypatch.delenv("CHRONICLE_HUB_NAME")
    assert _call(url, "/api/team/settings", {"name": ""}, _as(s["Ada"]))[0] == 400  # not the container's host name
    assert _call(url, "/api/team/settings", {"name": "Other"}, _as(s["Ada"]))[0] == 200
    assert load_config(cfg.home).hub_name == "Other" and load_config(cfg.home).hub_address == ""
