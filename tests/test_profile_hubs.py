"""One computer, two hubs (core.profiles): Personal sends to its hub, a named profile to its own, both from Personal's
one archive. Each hub gets only its owner's sessions, knows the computer by a different identity, and its teammates'
lessons reach only the agents in its owner's folders."""

import gzip
import json
import textwrap

import pytest

from interlatch.application import profiles as ops
from interlatch.core.config import load_config, set_config_value
from interlatch.core.machine import local_machine_id
from interlatch.sharing import client
from interlatch.sharing.identity import new_token, write_token
from interlatch.sharing.pull import apply_team_lessons
from interlatch.sharing.push import push
from interlatch.storage.sqlite import connect

from test_hub import SPOKE_CWD, SPOKE_SID, _analyzed_on_spoke, _serve
from hubs_fixture import hub_with
from test_team import teamenv  # noqa: F401  (the fixture)

MINE, MINE_SID = "/home/test/code/mine", "mine-0000-1111-2222-333333333333"


@pytest.fixture()
def twohubs(teamenv, tmp_path):  # noqa: F811
    """teamenv's spoke is Personal, joined to hub A; Acme owns SPOKE_CWD and joins hub B."""
    home = tmp_path / "hub-b"
    home.mkdir()
    (home / "config.toml").write_text(textwrap.dedent("""\
        [sources]
        claude_dirs = []
        [hub]
        name = "Hub B"
        """))
    hub_b = load_config(home)
    token = new_token(hub_b)
    _app, httpd, url = _serve(load_config(home))
    spoke = teamenv["spoke"]
    _analyzed_on_spoke(spoke, title="ACME_WORK billing", at="2026-10-02T00:00:00Z",
                       lessons=[("acme-gotcha", "project", "gotcha")])
    conn = connect(spoke.db_path)  # a second session, in a folder of Personal's
    row = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone())
    row.update(id=MINE_SID, project_path=MINE, project_name="mine", llm_title="PERSONAL_WORK notes", machine_path=None)
    conn.execute(f"INSERT INTO sessions({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
    conn.commit()
    conn.close()
    p, _ = hub_with(spoke, "Acme", [SPOKE_CWD])
    acme = load_config(p.home)
    set_config_value(acme, "hub", "url", json.dumps(url))
    set_config_value(acme, "hub", "all_folders", "true")  # hub B has no projects yet: share every folder Acme owns
    write_token(acme, token)
    acme = load_config(p.home)
    teamenv.update(acme=acme, profile=p, hub_b=hub_b, url_b=url)
    yield teamenv
    httpd.shutdown()


def _hub_has(cfg, sid=SPOKE_SID) -> bool:
    conn = connect(cfg.db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (sid,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_each_hub_gets_only_its_owners_sessions(twohubs, monkeypatch):
    spoke, acme, hub_a, hub_b = twohubs["spoke"], twohubs["acme"], twohubs["cfg"], twohubs["hub_b"]
    sent: dict[str, list[str]] = {}
    request = client.HubClient.request

    def spy(self, method, path, **kw):
        body = kw.get("body")
        if isinstance(body, (bytes, bytearray)):
            try:
                body = gzip.decompress(body)
            except OSError:
                pass
            text = bytes(body).decode("utf-8", "replace")
        else:
            text = json.dumps(body)
        sent.setdefault(self.url, []).append(text)
        return request(self, method, path, **kw)

    monkeypatch.setattr(client.HubClient, "request", spy)
    assert push(spoke).sent == 1  # Personal's hub: Personal's session, nothing of Acme's
    assert push(acme).sent == 1
    assert _hub_has(hub_b) and not _hub_has(hub_a)
    assert _hub_has(hub_a, MINE_SID) and not _hub_has(hub_b, MINE_SID)
    to_a = "\n".join(sent[twohubs["url"].rstrip("/")])
    assert "ACME_WORK" not in to_a and SPOKE_CWD not in to_a and "acme-gotcha" not in to_a
    to_b = "\n".join(sent[twohubs["url_b"]])
    assert "ACME_WORK" in to_b and "PERSONAL_WORK" not in to_b and MINE not in to_b
    # the two hubs know this computer by two identities
    ids = {r[0] for r in connect(hub_b.db_path).execute("SELECT id FROM machines WHERE role != 'this'")}
    assert local_machine_id(acme) in ids and local_machine_id(spoke) not in ids
    assert local_machine_id(spoke) != local_machine_id(acme)


def test_the_cli_and_the_worker_send_for_every_profile(twohubs, monkeypatch):
    from interlatch.application.workflows import run_worker
    from interlatch.interfaces.cli.main import main

    spoke, hub_a, hub_b = twohubs["spoke"], twohubs["cfg"], twohubs["hub_b"]
    assert [c.owner for c in ops.joined(spoke)] == [twohubs["profile"].id]
    monkeypatch.setenv("INTERLATCH_HOME", str(spoke.home))
    assert main(["push", "--quiet"]) == 0
    assert _hub_has(hub_b) and not _hub_has(hub_a)
    report = run_worker(load_config(spoke.home), analyze=False, synthesize=False)
    assert "Acme:" in (report.shared or "")


def test_teammates_lessons_from_two_hubs_live_side_by_side(twohubs, monkeypatch):
    """Each hub's lessons are kept under its owner; one hub's pull never removes the other's, and agents in a folder
    see only its owner's hub's teammates."""
    import os

    from interlatch.interfaces.mcp import Tools

    spoke, acme = twohubs["spoke"], twohubs["acme"]
    mine = {"lessons": [{"id": 1, "title": "PERSONAL_TEAM lesson", "kind": "gotcha", "place": "x"}],
            "places": {"x": {"mine": SPOKE_SID}}, "hub": "A"}
    theirs = {"lessons": [{"id": 1, "title": "ACME_TEAM lesson", "kind": "gotcha", "place": "x"}],
              "places": {"x": {"mine": SPOKE_SID}}, "hub": "B"}
    assert apply_team_lessons(spoke, mine, {}) == 1
    assert apply_team_lessons(acme, theirs, {}) == 1
    assert apply_team_lessons(spoke, {"lessons": [], "hub": "A"}, {}) == 0  # Personal's hub sent none this time
    conn = connect(spoke.db_path)
    titles = {r[0]: r[1] for r in conn.execute("SELECT title, fingerprint FROM knowledge WHERE source = 'team'")}
    conn.close()
    assert titles == {"ACME_TEAM lesson": f"team:{acme.owner}:1"}
    apply_team_lessons(spoke, mine, {})
    monkeypatch.setattr(os, "getcwd", lambda: SPOKE_CWD)
    text = Tools(spoke).search_knowledge("lesson")
    assert "ACME_TEAM" in text and "PERSONAL_TEAM" not in text  # Acme's folder: Acme's hub's teammates only


def test_leaving_one_hub_keeps_the_other_and_personals_groups(twohubs):
    from interlatch.sharing.membership import leave

    spoke, acme = twohubs["spoke"], twohubs["acme"]
    conn = connect(spoke.db_path)
    conn.execute("INSERT INTO project_groups(name, folders_json, hub_project, created_at) VALUES "
                 "('Mine', '[]', '/hub/mine', '2026-10-01')")
    conn.commit()
    conn.close()
    apply_team_lessons(spoke, {"lessons": [{"id": 1, "title": "PERSONAL_TEAM", "kind": "gotcha", "place": "x"}],
                               "places": {"x": {"mine": SPOKE_SID}}, "hub": "A"}, {})
    apply_team_lessons(acme, {"lessons": [{"id": 2, "title": "ACME_TEAM", "kind": "gotcha", "place": "x"}],
                              "places": {"x": {"mine": SPOKE_SID}}, "hub": "B"}, {})
    leave(acme)
    conn = connect(spoke.db_path)
    assert [r[0] for r in conn.execute("SELECT title FROM knowledge WHERE source = 'team'")] == ["PERSONAL_TEAM"]
    assert conn.execute("SELECT hub_project FROM project_groups WHERE name = 'Mine'").fetchone()[0] == "/hub/mine"
    conn.close()
    assert load_config(spoke.home).is_spoke and not load_config(acme.home).is_spoke


def test_a_profile_shares_none_of_personals_groups(twohubs):
    from interlatch.sharing.projects import group_routes

    acme = twohubs["acme"]
    conn = connect(acme.db_path)
    conn.execute("INSERT INTO project_groups(name, folders_json, hub_project, created_at) VALUES "
                 f"('Mine', '[\"{SPOKE_CWD}\"]', '/hub/mine', '2026-10-01')")
    conn.commit()
    conn.close()
    assert not group_routes(acme) and not group_routes(acme).projects()


def test_a_session_goes_by_its_recorded_owner_not_only_its_folder(twohubs):
    """A session keeps the owner it was given, even when its folder now says another profile: its hub gets it only
    if both agree (here Personal's, recorded before; Acme's hub must not get it)."""
    spoke, acme, hub_b = twohubs["spoke"], twohubs["acme"], twohubs["hub_b"]
    conn = connect(spoke.db_path)
    conn.execute("UPDATE session_owner SET owner = 'personal' WHERE session_id = ?", (SPOKE_SID,))
    conn.commit()
    conn.close()
    assert push(acme).sent == 0 and not _hub_has(hub_b)


def test_a_disconnected_group_is_taken_back_from_its_hub(twohubs, monkeypatch):
    """What a disconnected group held waits, and no hub keeps what waits: Acme's hub takes it back at once, or (out of
    reach then) the next time Acme sends to it."""
    from interlatch.sharing.protocol import HubUnreachable

    spoke, acme, hub_b = twohubs["spoke"], twohubs["acme"], twohubs["hub_b"]
    pid = twohubs["profile"].id
    assert push(acme).sent == 1 and _hub_has(hub_b)
    _was, moved = ops.disconnect(spoke, "Acme work")
    assert moved.to == {"waiting": 1} and moved.taken == {pid: 1} and not _hub_has(hub_b)

    ops.connect(spoke, "Acme work", "Acme")
    assert push(acme).sent == 1 and _hub_has(hub_b)  # connected again: shared again

    def down(*a, **kw):
        raise HubUnreachable("can't reach the hub")

    with monkeypatch.context() as m:
        m.setattr(client, "withdraw_sessions", down)
        _was, moved = ops.disconnect(spoke, "Acme work")
    assert moved.kept == {pid: "can't reach the hub"} and _hub_has(hub_b)
    assert push(acme).withdrawn == 1 and not _hub_has(hub_b)


def test_choosing_a_folder_for_another_hub_takes_it_back_at_once(twohubs):
    spoke, acme, hub_a, hub_b = twohubs["spoke"], twohubs["acme"], twohubs["cfg"], twohubs["hub_b"]
    assert push(acme).sent == 1 and _hub_has(hub_b)
    moved = ops.choose(spoke, SPOKE_CWD, "personal")
    assert moved.taken == {twohubs["profile"].id: 1} and moved.hubs == {twohubs["profile"].id: twohubs["url_b"]}
    assert not _hub_has(hub_b)
    assert push(acme).sent == 0 and push(spoke).sent >= 1 and _hub_has(hub_a) and not _hub_has(hub_b)


class _Jobs:  # runs a dashboard job at once, so its result can be checked
    def __init__(self):
        self.ran: list[str] = []

    def start(self, name, fn):
        self.ran.append(fn(lambda *_: None))
        return True

    def snapshot(self):
        return {}


def test_settings_hubs_shows_and_drives_each_hub(twohubs, monkeypatch):
    """Settings › Hubs: each hub's sending (joined, last share, problem), Share now and Leave for any of them, the
    status bar's list of hubs and a problem with another hub named, and signing in to another hub as its own link."""
    from interlatch.interfaces.web import routes
    from interlatch.interfaces.web.api import status
    from interlatch.interfaces.web.api.hubs import action_hubs, hubs
    from interlatch.interfaces.web.context import Context

    spoke, acme, hub_b = twohubs["spoke"], twohubs["acme"], twohubs["hub_b"]
    conn = connect(spoke.db_path)
    jobs = _Jobs()
    ctx = Context(lambda: spoke, lambda c: None, lambda: conn, jobs, None)
    rows = {r["label"]: r for r in hubs(ctx)["hubs"]}
    assert rows["Acme"]["joined"] and rows["Acme"]["last_push"] is None and rows["Personal"]["joined"]

    out, code = action_hubs(ctx, "push", {"hub": "Acme"})  # Share now, for Acme's hub only
    assert code == 200 and out["started"] and "to " + twohubs["url_b"] in jobs.ran[0] and _hub_has(hub_b)
    acme_row = next(r for r in hubs(ctx)["hubs"] if r["label"] == "Acme")
    assert acme_row["last_push"]["sent"] == 1 and acme_row["hub_name"] == "Hub B" and acme_row["push_problem"] is None

    small = status.status_small(ctx)
    assert small["hub_urls"] == [spoke.hub_url, twohubs["url_b"]] and small["hub_problem"] is None
    monkeypatch.setattr("interlatch.sharing.push.last_push", lambda cfg: {"owner": cfg.owner})  # Acme's sends fail
    monkeypatch.setattr("interlatch.sharing.push.send_problem",
                        lambda last: {"reason": "unreachable"} if last["owner"] != "personal" else None)
    assert status.status_small(ctx)["hub_problem"] == {"reason": "unreachable", "profile": "Acme"}
    monkeypatch.undo()

    signed = []
    monkeypatch.setattr("interlatch.sharing.client.dashboard_signin", lambda cfg, page=None: signed.append(cfg.owner) or "x")

    class H:
        def _json(self, data, status=200):
            return data, status

    assert routes._hub_signin(H(), ctx, None, {"hub": "Acme"})[1] == 200
    assert routes._hub_signin(H(), ctx, None, {})[1] == 200
    assert signed == [twohubs["profile"].id, "personal"]
    assert routes._hub_signin(H(), ctx, None, {"hub": "Nope"})[1] == 400

    out, code = action_hubs(ctx, "leave", {"hub": "Acme"})  # Leave, for Acme's hub only
    assert code == 200 and not load_config(acme.home).is_spoke and load_config(spoke.home).is_spoke
    assert not next(r for r in hubs(ctx)["hubs"] if r["label"] == "Acme")["joined"]
    out, code = action_hubs(ctx, "push", {"hub": "Acme"})
    assert code == 409 and "hasn't joined" in out["error"]
    conn.close()


class _Here:  # the dashboard's request handler, as the routes see it
    viewer = None

    def __init__(self, here=True):
        self.here = here

    def _json(self, data, status=200):
        return data, status

    def _from_here(self):
        return self.here

    def _can_admin(self):
        return True

    def _me(self):
        return {}


def test_every_hub_has_the_same_device_actions(twohubs):
    """A hub's page (#/hub) runs Devices' actions for that hub: its link, folders and sharing, never Personal's; and
    another hub's only from this computer. Transcripts stay Personal's alone."""
    from interlatch.interfaces.web import routes
    from interlatch.interfaces.web.context import Context

    spoke, acme = twohubs["spoke"], twohubs["acme"]
    conn = connect(spoke.db_path)
    jobs = _Jobs()
    ctx = Context(lambda: spoke, lambda c: None, lambda: conn, jobs, None)
    here = _Here()

    dv, code = routes._devices(here, ctx, None, {"hub": "Acme"})
    assert code == 200 and dv["role"] == "spoke" and dv["hub_url"] == twohubs["url_b"] and dv["hub_key"] == "Acme"
    mine, _ = routes._devices(here, ctx, None, {})
    assert mine["hub_url"] == spoke.hub_url and mine["hub_key"] is None
    assert routes._devices(_Here(here=False), ctx, None, {"hub": "Acme"})[1] == 403  # from another device: no
    assert routes._devices(here, ctx, None, {"hub": "Nope"})[1] == 400

    out, code = routes._share(here, ctx, None, {"hub": "Acme", "all_folders": False})
    assert code == 200 and out.get("ok") and not load_config(acme.home).hub_all_folders
    assert load_config(spoke.home).hub_all_folders  # Personal's as it was
    out, _ = routes._share(here, ctx, None, {"hub": "Acme", "share": "everything"})
    assert "summaries and lessons only" in out["error"] and load_config(acme.home).hub_share == "knowledge"

    routes._share(here, ctx, None, {"hub": "Acme", "all_folders": True})  # hub B has no projects: every folder again
    push_route, _ = routes.find(routes.POST, "/api/devices/push")
    out, code = push_route.answer(here, ctx, None, {"hub": "Acme"})
    assert code == 200 and out["started"] and "to " + twohubs["url_b"] in jobs.ran[-1] and _hub_has(twohubs["hub_b"])
    leave_route, _ = routes.find(routes.POST, "/api/devices/leave-hub")
    out, code = leave_route.answer(here, ctx, None, {"hub": "Acme"})
    assert code == 200 and not load_config(acme.home).is_spoke and load_config(spoke.home).is_spoke
    conn.close()
