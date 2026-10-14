"""A hub that takes knowledge only (`[hub] accept = "knowledge"`): no computer's transcripts are kept, whoever's it
is, while computers that share knowledge go on as before. Set from the config, or by an admin under Team › Computers."""

import gzip
import hashlib
import urllib.error
import urllib.request

import pytest

from interlatch import hub, people
from interlatch.core.config import load_config, set_config_value

from test_hub import hubenv  # noqa: F401  (fixture)
from test_people_web import MACHINE, _as, _call, _raw, hubweb, team  # noqa: F401  (fixtures)


def _knowledge_only(env):
    set_config_value(env["cfg"], "hub", "accept", '"knowledge"')  # the dashboard picks up config.toml's change


def _hello(env, share, token=None):
    body = {"protocol": hub.PROTOCOL, "machine": MACHINE, "name": "laptop", "share": share}
    return _call(env["url"], "/api/hub/hello", body, {"Authorization": f"Bearer {token or env['token']}"})


def _upload(env, path, token=None):
    data = b'{"a": 1}\n'
    params = (f"machine={MACHINE}&root=claude&path=projects/-x/a.jsonl&size={len(data)}&mtime=1"
              f"&sha256={hashlib.sha256(data).hexdigest()}")
    req = urllib.request.Request(f"{env['url']}{path}?{params}", data=gzip.compress(data), method="POST",
                                 headers={"Authorization": f"Bearer {token or env['token']}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_accept_reads_and_fails_closed(tmp_path):
    assert load_config(tmp_path).hub_accept == "everything"
    for written, taken in (('"knowledge"', "knowledge"), ('"Everything"', "everything"), ('"knowlege"', "knowledge"),
                           ('"none"', "knowledge")):
        (tmp_path / "config.toml").write_text(f"[hub]\naccept = {written}\n")
        assert load_config(tmp_path).hub_accept == taken, written  # a typo never lets transcripts in


def test_a_knowledge_only_hub_keeps_no_transcripts(hubenv):  # noqa: F811
    cfg, spoke = hubenv["cfg"], hubenv["spoke"]
    _knowledge_only(hubenv)

    with pytest.raises(hub.HubError, match="takes knowledge only"):  # a computer that sends transcripts
        hub.push(spoke)
    status, data = _upload(hubenv, "/api/hub/file")  # and one that skips the hello
    assert status == 403 and b"takes knowledge only" in data
    status, data = _upload(hubenv, "/api/hub/analyses")
    assert status == 403 and b"takes knowledge only" in data
    assert not cfg.machines_dir.exists() or not any(p.is_file() for p in cfg.machines_dir.rglob("*"))

    status, r = _hello(hubenv, "knowledge")  # sharing knowledge goes on as before
    assert status == 200 and "error" not in r
    status, r = _hello(hubenv, "everything")
    assert status == 400 and "interlatch config set hub.share knowledge" in r["error"]


def test_a_hub_that_takes_everything_still_does(hubenv):  # noqa: F811
    status, _ = _upload(hubenv, "/api/hub/file")
    assert status == 200
    assert _hello(hubenv, "everything")[0] == 200


def test_joining_a_knowledge_only_hub_shares_knowledge(team):  # noqa: F811
    conn, bob = team["conn"], team["bob"]  # a member who sees every project
    code = people.invite(conn, bob["id"])
    status, r = _call(team["url"], "/api/hub/join", {"code": code, "machine": MACHINE, "name": "Bob laptop", "key": "k" * 43})
    assert status == 200 and r["share"] is None and r["projects"] is None

    _knowledge_only(team)
    code = people.invite(conn, bob["id"])
    status, r = _call(team["url"], "/api/hub/join", {"code": code, "machine": MACHINE, "name": "Bob laptop", "key": "k" * 43})
    assert status == 200 and r["share"] == "knowledge" and r["projects"] is None
    assert _upload(team, "/api/hub/file", r["token"])[0] == 403  # his own token is turned away too


def test_only_an_admin_changes_what_the_hub_takes(team):  # noqa: F811
    url, cfg = team["url"], team["cfg"]
    s = team["sessions"]
    assert _call(url, "/api/team/accept", {"accept": "knowledge"}, _as(s["Bob"]))[0] == 403
    assert _call(url, "/api/team/accept", {"accept": "knowledge"}, _as(s["Vic"]))[0] == 403
    assert load_config(cfg.home).hub_accept == "everything"
    assert _call(url, "/api/team/accept", {"accept": "nothing"}, _as(s["Ada"]))[0] == 400

    code, r = _call(url, "/api/team/accept", {"accept": "knowledge"}, _as(s["Ada"]))
    assert code == 200 and r["accept"] == "knowledge" and load_config(cfg.home).hub_accept == "knowledge"
    code, dv = _call(url, "/api/devices", headers=_as(s["Ada"]))
    assert dv["accept"] == "knowledge"
    code, info = _call(url, "/api/people", headers=_as(s["Ada"]))
    assert (info["audit"][0]["action"], info["audit"][0]["actor_name"]) == ("accept", "Ada")
    assert _upload(team, "/api/hub/file")[0] == 403  # the shared token, too

    code, r = _call(url, "/api/team/accept", {"accept": "everything"})  # at the hub itself
    assert code == 200 and r["accept"] == "everything"
    assert _upload(team, "/api/hub/file")[0] == 200
