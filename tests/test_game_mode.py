"""Game mode ([game] enabled): Innerlatch, the Party, the leaderboard and the arena are opt-in. Off, their routes refuse
everyone and nothing is sent to the leaderboard; on a hub, its admins decide for everyone signed in to it."""

from __future__ import annotations

from interlatch.core.config import DEFAULT_CONFIG_TOML, load_config, set_config_value
from interlatch.interfaces.web.routes import GAME_OFF
from interlatch.sharing import leaderboard as lb

from test_leaderboard import _joined, home, net  # noqa: F401  (the fixtures)
from test_people_web import _as, hubweb, team  # noqa: F401  (the fixtures)
from test_people_web import _call as _http

GAME_ROUTES = ("/api/party", "/api/party/map", "/api/leaderboard?by=hours&days=7")


def test_a_new_install_starts_with_game_mode_off(tmp_path):
    assert "[game]\n" in DEFAULT_CONFIG_TOML
    assert not load_config(tmp_path / "fresh").game_enabled


def test_a_config_from_before_keeps_the_game_on_for_someone_who_played(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "config.toml").write_text("[server]\nport = 11524\n")  # no [game]: written before game mode was optional
    assert not load_config(old).game_enabled
    (old / "leaderboard.json").write_text("{}")  # a Google sign-in for the Party or the leaderboard
    assert load_config(old).game_enabled
    set_config_value(load_config(old), "game", "enabled", "false")  # turned off later: off, sign-in or not
    assert not load_config(old).game_enabled


def test_off_innerlatchs_routes_refuse_and_say_why(hubweb):  # noqa: F811
    url = hubweb["url"]
    assert _http(url, "/api/me")[1]["game"] is False
    for path in GAME_ROUTES:
        assert _http(url, path) == (403, {"error": GAME_OFF}), path
    assert _http(url, "/api/party/signin", {}) == (403, {"error": GAME_OFF})
    assert _http(url, "/api/leaderboard/join", {}) == (403, {"error": GAME_OFF})


def test_switching_it_on_and_off_from_this_computer(hubweb):  # noqa: F811
    url, cfg = hubweb["url"], hubweb["cfg"]
    assert _http(url, "/api/game", {"on": True}) == (200, {"game": True})
    assert load_config(cfg.home).game_enabled and _http(url, "/api/me")[1]["game"] is True
    status, party = _http(url, "/api/party")
    assert status == 200 and party["signed_in"] is False
    assert _http(url, "/api/game", {"on": "yes"}) == (200, {"game": False})  # only true turns it on
    assert _http(url, "/api/party") == (403, {"error": GAME_OFF})


def test_on_a_hub_its_admins_decide_for_everyone(team):  # noqa: F811
    url, s = team["url"], team["sessions"]
    for name in ("Bob", "Vic"):
        assert _http(url, "/api/game", {"on": True}, _as(s[name]))[0] == 403, name
    assert _http(url, "/api/party", headers=_as(s["Bob"])) == (403, {"error": GAME_OFF})
    assert _http(url, "/api/game", {"on": True}, _as(s["Ada"])) == (200, {"game": True})
    me = _http(url, "/api/me", headers=_as(s["Bob"]))[1]
    assert me["game"] is True and not me["can_admin"]
    assert _http(url, "/api/party", headers=_as(s["Bob"]))[0] == 200


def test_off_nothing_is_sent_to_the_leaderboard(home, net):  # noqa: F811
    conn, cfg = home["conn"], home["cfg"]
    set_config_value(cfg, "game", "enabled", "false")
    _joined(cfg)
    cfg = load_config(cfg.home)
    lb.send(cfg, conn, force=True)
    lb.send_safely(cfg, conn)
    assert net.calls == []
    set_config_value(cfg, "game", "enabled", "true")
    cfg = load_config(cfg.home)
    net.on("POST", lb.FIRESTORE + ":commit", {})
    lb.send(cfg, conn, force=True)
    assert len(net.commits()) == 1


def test_the_cli_wont_join_or_send_while_off_but_still_leaves(home, net, capsys):  # noqa: F811
    from interlatch.interfaces.cli.main import main

    cfg = home["cfg"]
    set_config_value(cfg, "game", "enabled", "false")
    _joined(cfg)
    for action in ("join", "send"):
        assert main(["leaderboard", action]) == 1
        assert "Game mode is off" in capsys.readouterr().out
    assert net.calls == []
    net.on("DELETE", lb.FIRESTORE + "/players/uid1", {})
    net.on("DELETE", lb.FIRESTORE + "/arena/uid1", {})
    assert main(["leaderboard", "leave"]) == 0
    assert lb.read_state(cfg) is None
