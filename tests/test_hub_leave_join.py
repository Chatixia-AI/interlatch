"""Leaving a hub and joining one: leaving puts aside and clears everything this computer kept for that hub (its token
first), so a different hub joined later never gets the first one's folders, groups or left projects; joining a
different hub while still in one is refused before anything goes out (the join sends this computer's key); joining
the same hub (however its address is spelled) is a token refresh that keeps everything, and after a leave it offers
to bring back what was put aside. `interlatch hub set-url` is for the same hub at a new address."""

import hashlib
import http.client
import io
import json
import os
import stat
import sys
import textwrap

import pytest

from interlatch.core.config import load_config, set_config_value, toml_table
from interlatch.interfaces.cli.main import main
from interlatch.projects import groups
from interlatch.projects.routing import FOLDERS_KV
from interlatch.sharing import client, people
from interlatch.sharing.client import leave_hub, redeem_invite
from interlatch.sharing.identity import new_token, read_token, token_ok, write_token
from interlatch.sharing.membership import JOINED, STASH_DIR, hub_origin, keep_token, left_token, same_hub, stash_path
from interlatch.sharing.projects import FOLDERS_FILE
from interlatch.sharing.protocol import TEAM_SOURCE, HubError
from interlatch.sharing.pull import TEAM_FILE
from interlatch.sharing.push import LAST_PUSH, push
from interlatch.sharing.receiver import IngestTrigger
from interlatch.storage.kv import kv_get
from interlatch.storage.sqlite import connect
from interlatch.core.machine import local_machine

from test_hub import SPOKE_CWD, _make_spoke, _serve, hubenv  # noqa: F401  (the fixture)
from test_people_web import _call

HUB_A = "https://hub-a.example.com"
A_PROJECT, A_LEFT, A_GROUP, A_KEPT = "/srv/a/project", "/srv/a/left", "/srv/a/group", "/srv/a/kept"
GROUPED = "/home/test/code/grouped"
KEPT_FOLDER = "/home/test/code/kept"


def _spoke(tmp_path, monkeypatch, url=HUB_A, name="one"):
    """A computer that joined the hub at `url` and kept, for it, everything a computer keeps for its hub."""
    (tmp_path / name).mkdir()
    cfg, _, _ = _make_spoke(tmp_path / name, url, "token-of-a")
    monkeypatch.setenv("INTERLATCH_HOME", str(cfg.home))
    keep_token(cfg, url, "token-of-a")
    set_config_value(cfg, "hub", "folders", toml_table({SPOKE_CWD: A_PROJECT}))
    set_config_value(cfg, "hub", "left", json.dumps([A_LEFT]))
    set_config_value(cfg, "hub", "share", '"knowledge"')
    set_config_value(cfg, "hub", "all_folders", "true")
    conn = connect(cfg.db_path)
    conn.execute("INSERT INTO sessions(id, source, agent, project_path) VALUES ('g1', 'transcript', 'claude', ?)",
                 (GROUPED,))
    gid = groups.save(conn, None, "Grouped", [GROUPED])
    groups.share(conn, gid, A_GROUP)
    conn.execute("INSERT INTO group_shares(path, hub_project, group_id, shared_at) VALUES (?, ?, ?, '2026-10-01')",
                 (KEPT_FOLDER, A_KEPT, gid))
    conn.execute("INSERT INTO knowledge(project_path, kind, title, source, fingerprint) VALUES (?, 'gotcha', "
                 "'A teammate lesson', ?, 'team:1')", (SPOKE_CWD, TEAM_SOURCE))
    conn.commit()
    conn.close()
    for f in (LAST_PUSH, TEAM_FILE, FOLDERS_FILE):
        (cfg.home / f).write_text(json.dumps({"hub": url}))
    return load_config(cfg.home)


def _state(cfg):
    """Everything a computer keeps for its hub, as it is now."""
    cfg = load_config(cfg.home)
    conn = connect(cfg.db_path)
    try:
        linked = [tuple(r) for r in conn.execute("SELECT name, hub_project FROM project_groups WHERE hub_project IS NOT NULL")]
        shares = [tuple(r) for r in conn.execute("SELECT path, hub_project FROM group_shares")]
        team = conn.execute("SELECT COUNT(*) FROM knowledge WHERE source = ?", (TEAM_SOURCE,)).fetchone()[0]
    finally:
        conn.close()
    return {"url": cfg.hub_url, "token": read_token(cfg), "folders": cfg.hub_folders, "left": cfg.hub_left,
            "share": cfg.hub_share, "all_folders": cfg.hub_all_folders, "groups": linked, "group_shares": shares,
            "team": team, "files": sorted(f for f in (LAST_PUSH, TEAM_FILE, FOLDERS_FILE, JOINED)
                                          if (cfg.home / f).exists())}


CLEARED = {"url": "", "token": None, "folders": {}, "left": [], "share": "everything", "all_folders": False,
           "groups": [], "group_shares": [], "team": 0, "files": []}


@pytest.fixture()
def no_network(monkeypatch):
    """Every HTTP request this process makes, refused and noted."""
    made = []

    def refuse(self, method, url, *a, **kw):
        made.append((self.host, method, url))
        raise OSError("no network in this test")

    monkeypatch.setattr(http.client.HTTPConnection, "request", refuse)
    return made


def test_one_hub_however_its_address_is_spelled():
    assert hub_origin("Hub.Example.COM") == "https://hub.example.com"
    assert same_hub("https://hub.example.com", "HTTPS://HUB.example.com:443/")
    assert same_hub("http://hub.example.com:80/x", "http://Hub.Example.com")
    assert same_hub("https://hub.example.com.", "https://hub.example.com")
    assert same_hub("https://Bücher.example", "https://xn--bcher-kva.example")
    assert same_hub("http://[::1]:8080", "http://[::1]:8080/")
    assert not same_hub("https://hub.example.com", "http://hub.example.com")
    assert not same_hub("https://hub.example.com", "https://hub.example.com:8443")
    assert not same_hub("https://hub.example.com", "https://hub.example.org")
    assert not same_hub("https://hub.example.com", "")
    with pytest.raises(HubError):
        hub_origin("ftp://hub.example.com")


def test_leaving_clears_and_puts_aside_everything_kept_for_the_hub(tmp_path, monkeypatch):
    spoke = _spoke(tmp_path, monkeypatch)
    before = _state(spoke)
    assert before["team"] == 1 and before["groups"] == [("Grouped", A_GROUP)] and len(before["files"]) == 4

    spoke = leave_hub(spoke)
    assert _state(spoke) == CLEARED  # the teammates' lessons too: they came from that hub

    path = stash_path(spoke, HUB_A)
    assert path.parent == spoke.home / STASH_DIR and ":" not in path.name and path.suffix == ".json"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    kept = json.loads(path.read_text())
    assert kept["origin"] == HUB_A and "token-of-a" not in path.read_text()
    assert (kept["folders"], kept["left"], kept["share"], kept["all_folders"]) == (
        {SPOKE_CWD: A_PROJECT}, [A_LEFT], "knowledge", True)
    assert [g["hub_project"] for g in kept["groups"]] == [A_GROUP]
    assert [(s["path"], s["hub_project"]) for s in kept["group_shares"]] == [(KEPT_FOLDER, A_KEPT)]


def test_leave_removes_the_token_before_the_address(tmp_path, monkeypatch):
    """A token file without an address makes a hub of this computer, with that hub's token as its own: a leave that
    stops halfway must leave the address, not the token."""
    from interlatch.core import config

    spoke = _spoke(tmp_path, monkeypatch)
    real = config.set_config_value

    def no_url(cfg, section, key, value):
        if key == "url":
            raise OSError("disk full")
        real(cfg, section, key, value)

    monkeypatch.setattr(config, "set_config_value", no_url)
    with pytest.raises(OSError):
        leave_hub(spoke)
    monkeypatch.setattr(config, "set_config_value", real)
    after = load_config(spoke.home)
    assert read_token(after) is None and after.is_spoke and after.hub_folders == {}
    kept = json.loads(stash_path(spoke, HUB_A).read_text())
    leave_hub(after)  # run again: it finishes, and keeps what the first one put aside
    assert _state(after) == CLEARED
    assert json.loads(stash_path(spoke, HUB_A).read_text())["folders"] == kept["folders"] == {SPOKE_CWD: A_PROJECT}


def test_joining_another_hub_while_in_one_sends_nothing(tmp_path, monkeypatch, no_network, capsys):
    spoke = _spoke(tmp_path, monkeypatch)
    before = _state(spoke)
    assert main(["hub", "join", "https://hub-b.example.com", "--code", "AAAA-BBBB", "--no-push"]) == 1
    out = " ".join(capsys.readouterr().out.split())
    assert f"sends to the hub at {HUB_A}" in out and "interlatch hub leave" in out and "set-url" in out
    assert main(["hub", "join", "hub-b.example.com", "--token", "token-of-b", "--no-push"]) == 1
    with pytest.raises(HubError, match="leave it first"):
        redeem_invite(spoke, "https://hub-b.example.com", "AAAA-BBBB")
    assert no_network == [] and _state(spoke) == before  # not the key, not a hello: nothing went out, nothing changed


def test_leave_a_then_join_b_sends_b_nothing_of_a(tmp_path, monkeypatch):
    """Left hub A, joined hub B: B's hello and what it files get none of A's folders, groups or left projects."""
    monkeypatch.setattr(IngestTrigger, "request", lambda self: None)
    hub_b = tmp_path / "hub-b"
    hub_b.mkdir()
    (hub_b / "config.toml").write_text(textwrap.dedent(f"""\
        [sources]
        claude_dirs = ["{tmp_path / 'none'}"]
        codex_dirs = []
        """))
    b_cfg = load_config(hub_b)
    b_cfg.ensure_dirs()
    b_token = new_token(b_cfg)
    _, httpd, b_url = _serve(b_cfg)
    sent = []
    real = client.HubClient.request

    def noted(self, method, path, **kw):
        sent.append((self.url, path, kw.get("body") if isinstance(kw.get("body"), (dict, list)) else None))
        return real(self, method, path, **kw)

    monkeypatch.setattr(client.HubClient, "request", noted)
    try:
        spoke = _spoke(tmp_path, monkeypatch)
        set_config_value(spoke, "hub", "share", '"everything"')
        assert main(["hub", "leave"]) == 0
        assert main(["hub", "join", b_url, "--token", b_token, "--no-push"]) == 0
        spoke = load_config(spoke.home)
        assert spoke.hub_url == b_url and not spoke.hub_folders and not spoke.hub_left
        report = push(spoke)
        assert not report.errors and report.sent
    finally:
        httpd.shutdown()
    hellos = [body for url, path, body in sent if path == "/api/hub/hello"]
    assert hellos and all(u == b_url for u, _, _ in sent)
    for hello in hellos:
        assert hello["folders"] == {} and hello["left"] == [] and hello["share"] == "everything"
    text = json.dumps(sent)
    assert not any(p in text for p in (A_PROJECT, A_LEFT, A_GROUP, A_KEPT))
    conn = connect(b_cfg.db_path)
    try:
        me = local_machine(spoke)["id"]
        assert json.loads(kv_get(conn, FOLDERS_KV + me) or "{}") == {}
    finally:
        conn.close()
    assert json.loads(stash_path(spoke, HUB_A).read_text())["folders"] == {SPOKE_CWD: A_PROJECT}  # still kept, for A


def test_joining_a_hub_after_an_old_leave_clears_what_it_left_behind(tmp_path, monkeypatch, capsys):
    """A leave from before this cleared only the address and the token: the next join clears the rest first."""
    spoke = _spoke(tmp_path, monkeypatch)
    set_config_value(spoke, "hub", "url", '""')
    from interlatch.sharing.identity import token_path

    token_path(spoke).unlink()
    assert main(["hub", "join", "https://hub-b.example.com", "--token", "token-of-b", "--no-push"]) == 0
    assert "were cleared" in " ".join(capsys.readouterr().out.split())
    state = _state(spoke)
    assert (state["folders"], state["left"], state["groups"], state["group_shares"], state["team"]) == ({}, [], [], [], 0)
    assert json.loads((spoke.home / STASH_DIR / "earlier.json").read_text())["folders"] == {SPOKE_CWD: A_PROJECT}


def test_the_dashboard_leaves_as_the_command_line_does(tmp_path, monkeypatch):
    by_cli = _spoke(tmp_path, monkeypatch, name="cli")
    assert main(["hub", "leave"]) == 0
    by_web = _spoke(tmp_path, monkeypatch, name="web")
    app, httpd, url = _serve(by_web)
    try:
        status, r = _call(url, "/api/devices/leave-hub", {})
        assert status == 200 and r["ok"] and r["hub"] == HUB_A
    finally:
        httpd.shutdown()
    assert _state(by_cli) == _state(by_web) == CLEARED

    def kept(cfg):
        data = json.loads(stash_path(cfg, HUB_A).read_text())
        data.pop("left_at")
        return data

    assert kept(by_cli) == kept(by_web)


def test_joining_the_same_hub_again_offers_what_was_put_aside(tmp_path, monkeypatch, capsys):
    spoke = _spoke(tmp_path, monkeypatch)
    before = _state(spoke)
    leave_hub(spoke)

    # without a terminal and without --restore: not brought back, and it says how
    assert main(["hub", "join", "HUB-A.example.com:443", "--token", "token-again", "--no-push"]) == 0
    assert "--restore" in capsys.readouterr().out
    assert _state(spoke)["folders"] == {} and stash_path(spoke, HUB_A).exists()
    leave_hub(load_config(spoke.home))  # the next leave puts aside what this computer has for the hub then
    assert json.loads(stash_path(spoke, HUB_A).read_text())["folders"] == {}

    spoke = _spoke(tmp_path, monkeypatch, name="two")
    leave_hub(spoke)
    assert main(["hub", "join", HUB_A, "--token", "token-again", "--no-push", "--restore"]) == 0
    after = _state(spoke)
    assert after == {**before, "token": "token-again", "team": 0, "files": [JOINED]}  # lessons come with the next push
    assert not stash_path(spoke, HUB_A).exists()  # used up


def test_a_join_asks_in_a_terminal_and_no_restore_starts_afresh(tmp_path, monkeypatch):
    spoke = _spoke(tmp_path, monkeypatch)
    leave_hub(spoke)
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert main(["hub", "join", HUB_A, "--token", "t2", "--no-push"]) == 0
    assert load_config(spoke.home).hub_folders == {SPOKE_CWD: A_PROJECT}

    other = _spoke(tmp_path, monkeypatch, name="two")
    leave_hub(other)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("--no-restore doesn't ask"))
    assert main(["hub", "join", HUB_A, "--token", "t2", "--no-push", "--no-restore"]) == 0
    assert load_config(other.home).hub_folders == {} and stash_path(other, HUB_A).exists()


def test_another_hub_never_gets_what_was_put_aside(tmp_path, monkeypatch, capsys):
    spoke = _spoke(tmp_path, monkeypatch)
    leave_hub(spoke)
    assert main(["hub", "join", "https://hub-b.example.com", "--token", "t", "--no-push", "--restore"]) == 0
    assert "Nothing was kept from this hub" in capsys.readouterr().out
    state = _state(spoke)
    assert (state["folders"], state["left"], state["groups"], state["group_shares"]) == ({}, [], [], [])
    assert stash_path(spoke, HUB_A).exists()


def test_joining_the_same_hub_again_keeps_everything(hubenv, monkeypatch):  # noqa: F811
    """`Join again` (PR #223): a new invite for the hub this computer sends to, its address spelled another way, is a
    new token and nothing else."""
    spoke = hubenv["spoke"]
    monkeypatch.setenv("INTERLATCH_HOME", str(spoke.home))
    set_config_value(spoke, "hub", "folders", toml_table({SPOKE_CWD: "/srv/demo"}))
    old_token = read_token(spoke)
    bob = people.add(hubenv["conn"], "Bob", None, "member", projects=None)
    code = people.invite(hubenv["conn"], bob["id"])
    hubenv["conn"].commit()
    assert main(["hub", "join", hubenv["url"].replace("http://", "HTTP://") + "/", "--code", code, "--no-push"]) == 0
    after = load_config(spoke.home)
    assert read_token(after) != old_token and after.hub_url == hubenv["url"]  # its spelling stays
    assert after.hub_folders == {SPOKE_CWD: "/srv/demo"}
    noted = json.loads((spoke.home / JOINED).read_text())  # the new token is noted as the hub's, not this computer's
    assert noted["token_sha256"] == hashlib.sha256(read_token(after).encode()).hexdigest()


def test_a_leftover_token_is_never_a_hubs_own(tmp_path, monkeypatch, capsys):
    """[hub] url cleared by hand: the token of the hub it sent to is still on file. The hub API doesn't take it, and
    `interlatch hub enable` makes a new one instead of printing it."""
    spoke = _spoke(tmp_path, monkeypatch)
    set_config_value(spoke, "hub", "url", '""')
    spoke = load_config(spoke.home)
    assert not spoke.is_spoke and read_token(spoke) == "token-of-a" and left_token(spoke)
    assert not token_ok(spoke, "Bearer token-of-a")
    assert main(["hub", "enable"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "token-of-a" not in out and "making a new one" in out
    token = read_token(load_config(spoke.home))
    assert token and token != "token-of-a" and not left_token(spoke) and token_ok(spoke, f"Bearer {token}")

    hub = tmp_path / "hub"  # a hub's own token is no leftover
    hub.mkdir()
    cfg = load_config(hub)
    cfg.ensure_dirs()
    write_token(cfg, "the-hubs-own")
    assert not left_token(cfg) and token_ok(cfg, "Bearer the-hubs-own")


def test_set_url_moves_the_same_hub_and_keeps_everything(tmp_path, monkeypatch, no_network, capsys):
    spoke = _spoke(tmp_path, monkeypatch)
    before = _state(spoke)
    assert main(["hub", "set-url", "https://new-a.example.com"]) == 1  # no terminal, no --yes: nothing changes
    assert _state(spoke) == before
    assert main(["hub", "set-url", "https://HUB-A.example.com/"]) == 0  # the same address already
    assert main(["hub", "set-url", "new-a.example.com", "--yes"]) == 0
    assert _state(spoke) == {**before, "url": "https://new-a.example.com"} and no_network == []
    assert main(["hub", "set-url", "ftp://x", "--yes"]) == 2
    leave_hub(load_config(spoke.home))
    assert main(["hub", "set-url", "https://x.example.com", "--yes"]) == 1  # not in a hub
