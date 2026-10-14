"""A group shared as one project on the hub (groups.share, hub.group_routes): every project in it goes there, whichever
joins it later; one kept out of it, or that left it, stays here, and what it sent stays filed on the hub."""

import json

import pytest

from interlatch.projects import groups
from interlatch.projects import routing
from interlatch.sharing.projects import FOLDERS_FILE, group_routes, hub_project_of, sent_folders
from interlatch.sharing.protocol import TEAM_SOURCE
from interlatch.sharing.pull import pull_team_lessons
from interlatch.sharing.push import _shared_records
from interlatch.core.config import load_config
from interlatch.storage.sqlite import connect
from interlatch.interfaces.web.app import App
from interlatch.interfaces.web import api

AKTIO = "/home/yuma/Work/Aktio"
PROJECT = "/srv/Aktio"
PATHS = {"root": AKTIO, "dash": f"{AKTIO}/operator_dashboard", "monitor": f"{AKTIO}/process_monitor",
         "private": f"{AKTIO}/private-notes", "vm": "aktio-vm:/root/Aktio/listener", "resona": "/home/yuma/resona/app",
         "misc": "/home/yuma/misc", "lib": f"{AKTIO}/lib"}


@pytest.fixture()
def member(tmp_path, monkeypatch):
    """A computer that shares knowledge with a hub (only its projects there), an Aktio group, and its sessions."""
    home = tmp_path / "member"
    home.mkdir()
    (home / "config.toml").write_text('[hub]\nurl = "http://hub"\nshare = "knowledge"\n'
                                      '[hub.folders]\n"/home/yuma/resona" = "/srv/Resona"\n')
    monkeypatch.setenv("INTERLATCH_HOME", str(home))
    cfg = load_config(home)
    conn = connect(cfg.db_path)
    for sid, path in PATHS.items():
        conn.execute("INSERT INTO sessions(id, source, agent, project_path, analysis_status, analyzed_at) VALUES "
                     "(?, 'transcript', 'claude', ?, 'done', '2026-10-01')", (sid, path))
    conn.commit()
    gid = groups.save(conn, None, "Aktio", [AKTIO, "aktio-vm:/root/Aktio"])
    groups.move(conn, [PATHS["private"]], None)  # kept out of the group by hand, inside its folder
    (home / FOLDERS_FILE).write_text(json.dumps({"hub": "Team hub", "projects": [
        {"path": PROJECT, "name": "Aktio", "sessions": 12}, {"path": "/srv/Resona", "name": "Resona", "sessions": "many"}]}))
    monkeypatch.setattr(routing, "git_info", lambda p: (f"{AKTIO}/lib", "github.com/acme/lib") if p.endswith("lib") else None)
    yield {"cfg": cfg, "conn": conn, "gid": gid}
    conn.close()


def _sent(cfg, **kw):
    recs, _, kept = _shared_records(cfg, {}, remotes=kw.pop("remotes", {}), projects_only=True,
                                        routes=group_routes(cfg), **kw)
    return sorted(r["id"] for r in recs), kept


def test_a_shared_group_sends_every_project_in_it(member):
    cfg, conn, gid = member["cfg"], member["conn"], member["gid"]
    assert _sent(cfg) == (["resona"], 7)  # not shared yet: only the folder added by hand
    groups.share(conn, gid, PROJECT)
    routes = group_routes(cfg)
    assert set(routes.active) == {PATHS[k] for k in ("root", "dash", "monitor", "lib")}  # not the VM's: not a folder here
    assert sent_folders(cfg, routes) == {"/home/yuma/resona": "/srv/Resona", AKTIO: PROJECT}  # one folder holds all
    # the private notes stay: kept out of the group by hand, though inside its folder; so do the VM's and misc
    assert _sent(cfg) == (["dash", "lib", "monitor", "resona", "root"], 3)
    # a repository whose git remote the hub files elsewhere follows its remote
    assert _sent(cfg, remotes={"github.com/acme/lib": "/srv/Lib"}) == (["dash", "lib", "monitor", "resona", "root"], 3)
    assert _shared_records(cfg, {}, remotes={"github.com/acme/lib": "/srv/Lib"}, projects_only=True, scope=["/srv/Lib"],
                               routes=routes)[0][0]["id"] == "lib"
    assert _sent(cfg, scope=["/srv/Resona"]) == (["resona"], 7)  # a person limited to other projects: none of it
    left = load_config(cfg.home)
    left.hub_left = [PROJECT]
    assert _sent(left) == (["resona"], 7)  # left that project: nothing more goes


def test_a_project_that_leaves_the_group_stays_filed_and_sends_no_more(member):
    cfg, conn, gid = member["cfg"], member["conn"], member["gid"]
    groups.share(conn, gid, PROJECT)
    group_routes(cfg).remember(cfg)  # the hub took a hello with them
    groups.move(conn, [PATHS["dash"]], None)
    routes = group_routes(cfg)
    assert routes.kept == {PATHS["dash"]: PROJECT}  # still a folder of the project on the hub: what it sent stays there
    assert sent_folders(cfg, routes)[AKTIO] == PROJECT and PATHS["dash"] not in sent_folders(cfg, routes)
    assert _sent(cfg) == (["lib", "monitor", "resona", "root"], 4)  # but nothing more goes from it
    groups.share(conn, gid, None)  # the group stops sharing: everything it shared stays filed, nothing more goes
    routes = group_routes(cfg)
    assert not routes.active and set(routes.kept) == {PATHS[k] for k in ("root", "dash", "monitor", "lib")}
    assert sent_folders(cfg, routes) == {"/home/yuma/resona": "/srv/Resona", AKTIO: PROJECT}
    assert _sent(cfg) == (["resona"], 7)
    groups.share(conn, gid, PROJECT)  # shared again: it all goes again
    assert _sent(cfg) == (["lib", "monitor", "resona", "root"], 4)


def test_all_folders_still_keeps_a_project_out_of_its_group_here(member):
    cfg, conn, gid = member["cfg"], member["conn"], member["gid"]
    groups.share(conn, gid, PROJECT)
    recs, _, kept = _shared_records(cfg, {}, routes=group_routes(cfg))  # all_folders = true
    assert sorted(r["id"] for r in recs) == ["dash", "lib", "misc", "monitor", "resona", "root", "vm"] and kept == 1


def test_a_folder_added_by_hand_inside_the_group_wins(member):
    cfg, conn, gid = member["cfg"], member["conn"], member["gid"]
    groups.share(conn, gid, PROJECT)
    cfg.hub_folders[PATHS["private"]] = "/srv/Private"
    assert _sent(cfg) == (["dash", "lib", "monitor", "private", "resona", "root"], 2)
    assert hub_project_of(cfg, [PATHS["private"], PATHS["dash"], PATHS["misc"]]) == {
        PATHS["private"]: {"path": "/srv/Private", "name": "Private"}, PATHS["dash"]: {"path": PROJECT, "name": "Aktio"}}


def test_teammates_lessons_come_back_to_the_groups_top_project(member, monkeypatch):
    cfg, conn, gid = member["cfg"], member["conn"], member["gid"]
    groups.share(conn, gid, PROJECT)
    asked = {}

    class Client:
        def request(self, method, path, body=None, **kw):
            asked.update(body)
            return {"team": True, "version": "v1", "lessons": [
                {"id": 7, "project": PROJECT, "kind": "gotcha", "title": "Aktio lesson", "body": "b", "who": ["Ben"]}]}

    assert pull_team_lessons(cfg, Client(), {}) == 1
    assert PROJECT in asked["projects"]
    row = conn.execute("SELECT project_path, title FROM knowledge WHERE source = ?", (TEAM_SOURCE,)).fetchone()
    assert tuple(row) == (AKTIO, "Aktio lesson")


def test_sharing_a_group_from_the_dashboard(member):
    cfg, gid = member["cfg"], member["gid"]
    app = App(cfg)
    got = api.projects.project_groups(app.context())
    assert got["hub"] == {"name": "Team hub", "url": "http://hub", "share": "knowledge", "projects": [  # the team's sessions there, as of the last push
        {"path": PROJECT, "name": "Aktio", "sessions": 12}, {"path": "/srv/Resona", "name": "Resona", "sessions": None}]}
    assert api.projects.action_project_groups(app.context(), "share", {"id": gid, "hub_project": "/srv/Nope"})[1] == 400  # not one of the hub's
    assert api.projects.action_project_groups(app.context(), "share", {"id": gid, "hub_project": PROJECT}) == ({"ok": True}, 200)
    assert api.projects.project_groups(app.context())["groups"][0]["hub_project"] == PROJECT
    assert next(p for p in api.devices.devices(app.context())["projects"] if p["path"] == PROJECT)["groups"] == [{"id": gid, "name": "Aktio"}]
    rows = {p["project_path"]: p["hub"] for p in api.projects.projects(app.context())}
    assert rows[PATHS["dash"]] == {"path": PROJECT, "name": "Aktio"} and rows[PATHS["private"]] is None
    assert api.projects.action_project_groups(app.context(), "share", {"id": gid, "hub_project": None}) == ({"ok": True}, 200)
    assert api.projects.project_groups(app.context())["groups"][0]["hub_project"] is None


def test_only_this_computer_itself_shares_a_group(member):
    from test_hub import _serve
    from test_people_web import TAILNET as REMOTE, _call

    gid = member["gid"]
    _, httpd, url = _serve(member["cfg"])
    try:
        code, got = _call(url, "/api/project-groups", headers=REMOTE)
        assert code == 200 and got["can_share"] is False and _call(url, "/api/project-groups")[1]["can_share"] is True
        code, got = _call(url, "/api/project-groups/share", {"id": gid, "hub_project": PROJECT}, headers=REMOTE)
        assert code == 403 and "computer itself" in got["error"]  # what leaves this computer: only from here
        assert _call(url, "/api/project-groups/save", {"id": gid, "name": "Aktio (work)", "folders": [AKTIO]},
                     headers=REMOTE)[0] == 200  # the group itself, from anywhere an admin may
        assert _call(url, "/api/project-groups/share", {"id": gid, "hub_project": PROJECT}) == (200, {"ok": True})
    finally:
        httpd.shutdown()
