"""Groups connected to a hub (projects.hubgroups, application.profiles): what each hub owns follows your groups, a
project taken out of a connected group waits instead of going back to Personal, and an edit the hubs can't follow
changes nothing."""

import pytest

from interlatch.application import profiles as ops
from interlatch.core.profiles import PERSONAL, WAITING, load_registry, route
from interlatch.projects import groups, owners
from interlatch.sharing.push import push
from interlatch.storage.sqlite import connect

from conftest import CWD, SID
from hubs_fixture import group, hub_with
from test_hub import SPOKE_CWD, SPOKE_SID, _analyzed_on_spoke
from test_team import teamenv  # noqa: F401  (the fixture)

WORK = "/Users/test/Projects"


def _owner(cfg, sid=SID):
    conn = connect(cfg.db_path)
    try:
        return owners.owner_of(conn, sid)
    finally:
        conn.close()


def _change(cfg, fn, **kw):
    return ops.change_groups(cfg, fn, **kw)


def test_what_a_hub_owns_follows_its_groups(synced):
    cfg = synced["cfg"]
    p, moved = hub_with(cfg, "Acme", [WORK])
    assert _owner(cfg) == p.id and moved.sessions == {PERSONAL: 1}
    reg = load_registry(cfg.home)
    assert reg.find("Acme").owns == [WORK] and route(reg, CWD).owner == p.id

    # a project picked out of the connected group waits: never back to Personal on its own
    _change(cfg, lambda c: groups.move(c, [CWD], None))
    reg = load_registry(cfg.home)
    assert reg.held == [CWD] and route(reg, CWD).owner == WAITING and _owner(cfg) == WAITING

    # picked back in: Acme's again
    gid = next(g["id"] for g in groups.groups(synced["conn"]) if g["name"] == "Acme work")
    _change(cfg, lambda c: groups.move(c, [CWD], gid))
    assert load_registry(cfg.home).held == [] and _owner(cfg) == p.id


def test_a_group_connected_to_no_hub_inside_a_connected_one_waits(synced):
    cfg = synced["cfg"]
    hub_with(cfg, "Acme", [WORK])
    group(cfg, "Side", [CWD])  # nested, connected to nothing
    ops.sync(cfg)
    reg = load_registry(cfg.home)
    assert reg.held == [CWD] and _owner(cfg) == WAITING
    # connected to Personal's hub's side instead: choose it for Personal
    ops.choose(cfg, CWD, "personal")
    assert _owner(cfg) == PERSONAL


def test_a_project_picked_into_a_connected_group_is_that_hubs_wherever_it_is(synced):
    cfg = synced["cfg"]
    p = ops.add_hub(cfg, "Acme")
    gid = group(cfg, "Acme work", ["/Users/test/elsewhere"])
    ops.connect(cfg, gid, "Acme")
    assert _owner(cfg) == PERSONAL
    _change(cfg, lambda c: groups.move(c, [CWD], gid))
    assert CWD in load_registry(cfg.home).find("Acme").owns and _owner(cfg) == p.id


def test_adding_a_folder_to_a_connected_group_moves_its_history(synced):
    cfg = synced["cfg"]
    p, _ = hub_with(cfg, "Acme", ["/Users/test/elsewhere"])
    gid = next(g["id"] for g in groups.groups(synced["conn"]) if g["name"] == "Acme work")
    assert _owner(cfg) == PERSONAL
    _change(cfg, lambda c: groups.save(c, gid, "Acme work", ["/Users/test/elsewhere", WORK]))
    assert _owner(cfg) == p.id


def _hub_down(monkeypatch):
    from interlatch.sharing import client
    from interlatch.sharing.protocol import HubUnreachable

    def down(*a, **kw):
        raise HubUnreachable("can't reach the hub")

    monkeypatch.setattr(client, "withdraw_sessions", down)


def test_an_edit_the_hubs_cant_follow_changes_nothing(teamenv, monkeypatch):  # noqa: F811
    """Personal's hub has the session already and can't be reached to take it back: putting its folder in a connected
    group is refused, and the group stays as it was (the dashboard says why)."""
    spoke = teamenv["spoke"]
    _analyzed_on_spoke(spoke, title="Shared", at="2026-10-02T00:00:00Z", lessons=[])
    assert push(spoke).sent == 1
    p, _ = hub_with(spoke, "Acme", ["/nowhere/acme"])
    gid = next(g["id"] for g in groups.groups(connect(spoke.db_path)) if g["name"] == "Acme work")
    _hub_down(monkeypatch)
    with pytest.raises(ops.ProfileError, match="take back the 1 sessions"):
        _change(spoke, lambda c: groups.save(c, gid, "Acme work", ["/nowhere/acme", SPOKE_CWD]))
    conn = connect(spoke.db_path)
    assert next(g for g in groups.groups(conn) if g["id"] == gid)["folders"] == ["/nowhere/acme"]
    conn.close()
    assert _owner(spoke, SPOKE_SID) == PERSONAL and load_registry(spoke.home).find("Acme").owns == ["/nowhere/acme"]
    _change(spoke, lambda c: groups.save(c, gid, "Acme work", ["/nowhere/acme", SPOKE_CWD]), leave_on_hub=True)
    assert _owner(spoke, SPOKE_SID) == p.id


def test_an_edit_takes_the_sessions_back_from_the_hub(teamenv):  # noqa: F811
    spoke, hub = teamenv["spoke"], teamenv["conn"]
    _analyzed_on_spoke(spoke, title="Shared", at="2026-10-02T00:00:00Z", lessons=[])
    assert push(spoke).sent == 1
    p, _ = hub_with(spoke, "Acme", ["/nowhere/acme"])
    gid = next(g["id"] for g in groups.groups(connect(spoke.db_path)) if g["name"] == "Acme work")
    _, moved = _change(spoke, lambda c: groups.save(c, gid, "Acme work", ["/nowhere/acme", SPOKE_CWD]))
    assert moved.taken == {PERSONAL: 1} and _owner(spoke, SPOKE_SID) == p.id
    assert not hub.execute("SELECT 1 FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()


def test_the_dashboard_refuses_what_the_hubs_cant_follow(teamenv, monkeypatch):  # noqa: F811
    from interlatch.core.config import load_config
    from interlatch.interfaces.web.api.projects import action_project_groups
    from interlatch.interfaces.web.context import Context

    spoke = teamenv["spoke"]
    _analyzed_on_spoke(spoke, title="Shared", at="2026-10-02T00:00:00Z", lessons=[])
    assert push(spoke).sent == 1
    hub_with(spoke, "Acme", ["/nowhere/acme"])
    conn = connect(spoke.db_path)
    gid = next(g["id"] for g in groups.groups(conn) if g["name"] == "Acme work")

    cfg = load_config(spoke.home)
    ctx = Context(lambda: cfg, lambda c: None, lambda: conn, None, None)
    body = {"id": gid, "name": "Acme work", "folders": ["/nowhere/acme", SPOKE_CWD]}
    with monkeypatch.context() as m:
        _hub_down(m)
        out, code = action_project_groups(ctx, "save", body)
        assert code == 409 and "take back the 1 sessions" in out["error"] and out["on_hub"] is True
        out, code = action_project_groups(ctx, "save", {**body, "leave_on_hub": True})  # the dashboard's "anyway"
        assert code == 200
    assert _owner(spoke, SPOKE_SID) != PERSONAL
    out, code = action_project_groups(ctx, "delete", {"id": gid})
    assert code == 400 and "connected to a hub" in out["error"]
    conn.close()


def test_one_folder_cant_belong_to_two_hubs(synced):
    cfg = synced["cfg"]
    hub_with(cfg, "Acme", [WORK])
    with pytest.raises(ops.ProfileError, match="both Acme and Globex"):
        hub_with(cfg, "Globex", [WORK])
    ops.connect(cfg, group(cfg, "Inner", [CWD]), "Globex")  # inside Acme's folder: the narrower group wins
    assert _owner(cfg) == load_registry(cfg.home).find("Globex").id


def test_a_registry_from_0_28_1_keeps_its_folders(synced, monkeypatch, capsys):
    """0.28.1 wrote `owns` by hand and no groups: those folders stay the hub's through every sync and group change,
    and its commands say where they went (--profile still works)."""
    from interlatch.interfaces.cli.main import main

    cfg = synced["cfg"]
    p = ops.add_hub(cfg, "Acme")
    text = (cfg.home / "profiles.toml").read_text()
    old = "\n".join(line for line in text.splitlines() if not line.startswith(("groups =", "manual =", "held =")))
    (cfg.home / "profiles.toml").write_text(old.replace("owns = []", f'owns = ["{WORK}"]'))  # as 0.28.1 left it
    ops.sync(cfg)
    reg = load_registry(cfg.home)
    assert reg.find("Acme").manual == [WORK] and reg.find("Acme").owns == [WORK]
    assert route(reg, CWD).owner == p.id  # a new session there is Acme's, as under 0.28.1
    hub_with(cfg, "Acme", ["/Users/test/elsewhere"])  # a group connected later adds to it
    assert sorted(load_registry(cfg.home).find("Acme").owns) == sorted([WORK, "/Users/test/elsewhere"])
    monkeypatch.setenv("INTERLATCH_HOME", str(cfg.home))
    assert main(["--profile", "Acme", "config", "path"]) == 0 and str(p.home) in capsys.readouterr().out
    assert main(["profile", "list"]) == 2 and "interlatch hubs" in capsys.readouterr().err


def _ctx(cfg, conn):
    from interlatch.interfaces.web.context import Context

    return Context(lambda: cfg, lambda c: None, lambda: conn, None, None)


def test_the_dashboard_lists_hubs_and_connects_groups(synced):
    """Settings › Devices › Hubs and a group's page: list, add, connect (and back to Personal), choose a waiting
    folder; what a connected group's folder holds waiting shows once, under that folder."""
    from interlatch.interfaces.web.api.hubs import action_hubs, hubs
    from interlatch.interfaces.web.api.projects import project_groups

    cfg, conn = synced["cfg"], synced["conn"]
    ctx = _ctx(cfg, conn)
    out, code = action_hubs(ctx, "add", {"name": "Acme"})
    assert code == 200 and out["hub"] == "Acme"
    work, side = group(cfg, "Acme work", [WORK]), group(cfg, "Side", [CWD])
    out, code = action_hubs(ctx, "connect", {"group": work, "hub": "Acme"})
    assert code == 200
    listed = hubs(ctx)
    assert [(x["label"], x["groups"]) for x in listed["hubs"]] == [("Personal", []), ("Acme", ["Acme work"])]
    assert [g for g in listed["groups"] if g["hub"]] == [{"id": work, "name": "Acme work", "hub": "Acme"}]
    assert listed["waiting"] == [{"folder": CWD, "sessions": 1, "projects": 1, "held": True, "let_go": False, "why": None}]
    assert {g["name"]: g["goes_to"] for g in project_groups(ctx)["groups"]} == {"Acme work": "Acme", "Side": None}

    out, code = action_hubs(ctx, "choose", {"folder": CWD, "hub": "Acme"})
    assert code == 200 and out["moved"] == 1 and _owner(cfg) == load_registry(cfg.home).find("Acme").id
    assert hubs(ctx)["waiting"] == []
    out, code = action_hubs(ctx, "connect", {"group": side, "hub": "personal"})  # not connected: nothing to do
    assert code == 200 and out["moved"] == 0
    out, code = action_hubs(ctx, "connect", {"group": work, "hub": "personal"})  # disconnect
    assert code == 200 and not load_registry(cfg.home).find("Acme").groups
    out, code = action_hubs(ctx, "connect", {"group": work, "hub": "Nope"})
    assert code == 409 and "No hub named" in out["error"]
    out, code = action_hubs(ctx, "remove", {"hub": "Acme"})
    assert code == 200 and load_registry(cfg.home).find("Acme") is None
    assert [(w["folder"], w["let_go"]) for w in hubs(ctx)["waiting"]] == [(CWD, True)]  # what the hub had waits


def test_the_dashboard_adds_a_hub_but_never_joins_one_this_computer_already_sends_to(teamenv):  # noqa: F811
    """Added, not joined, and why: Personal sends to that hub already, refused before anything goes out."""
    from interlatch.interfaces.web.api.hubs import action_hubs

    spoke = teamenv["spoke"]
    conn = connect(spoke.db_path)
    try:
        out, code = action_hubs(_ctx(spoke, conn), "add", {"name": "Second", "address": teamenv["url"], "code": "X"})
    finally:
        conn.close()
    assert code == 200 and out["hub"] == "Second" and "already sends to the hub" in out["join_error"]
    assert load_registry(spoke.home).find("Second") is not None
