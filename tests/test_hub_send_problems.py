"""A computer whose pushes stop getting through hears about it: the failure is noted beside the last good push, every
place that shows the hub warns (once it is worth a warning), a desktop notification says so once, and a new invite
from the dashboard puts it right. Before, a hub that turned the token away left only a line in the log: the dashboard,
the menu bar and `interlatch hub status` kept saying all was well, for days."""

import json
from datetime import timedelta

import pytest

from interlatch.core.config import load_config, set_config_value
from interlatch.core.util import parse_ts
from interlatch.sharing import people
from interlatch.sharing.identity import read_token
from interlatch.sharing.protocol import HubError, HubUnauthorized, HubUnreachable
from interlatch.sharing.push import UNREACHABLE_GRACE, last_push, note_failure, push, send_problem

from test_hub import _make_spoke, _serve, hubenv  # noqa: F401  (the fixture)
from test_people_web import REMOTE, _call


@pytest.fixture()
def posted(monkeypatch):
    sent = []
    monkeypatch.setattr("interlatch.runtime.notify.post", lambda title, message: sent.append((title, message)) or True)
    return sent


def _turned_away(env):
    """The hub has people now and no shared token: the spoke's shared token no longer gets in, as on a hub where the
    computer's own token was revoked."""
    set_config_value(env["cfg"], "hub", "shared_token", "false")
    env["app"].refresh_config()
    assert not env["app"].cfg.hub_shared_token
    people.add(env["conn"], "Ada", None, "admin")
    env["conn"].commit()


def test_a_token_the_hub_turns_away_is_noted_and_warned_about_at_once(hubenv, posted):  # noqa: F811
    spoke = hubenv["spoke"]
    good = push(spoke)
    assert good.sent and "failure" not in last_push(spoke)
    _turned_away(hubenv)
    for _ in range(2):
        with pytest.raises(HubUnauthorized):
            push(spoke)
    last = last_push(spoke)
    failure = last["failure"]
    assert failure["reason"] == "token" and failure["attempts"] == 2 and failure["hub"] == hubenv["url"]
    assert parse_ts(failure["since"]) <= parse_ts(failure["at"])
    assert last["sent"] == good.sent and last["summary"].startswith(f"to {hubenv['url']}: ")  # the good push stays
    problem = send_problem(last)
    assert problem["reason"] == "token" and problem["last_ok"] == last["at"] and problem["attempts"] == 2
    assert posted == []  # [updates] notify is off by default


def test_a_push_that_gets_through_again_clears_it(hubenv):  # noqa: F811
    spoke = hubenv["spoke"]
    _turned_away(hubenv)
    with pytest.raises(HubUnauthorized):
        push(spoke)
    assert send_problem(last_push(spoke))
    hubenv["conn"].execute("DELETE FROM people")  # a hub with nobody on it takes the shared token again
    hubenv["conn"].commit()
    assert push(spoke).sent
    assert "failure" not in last_push(spoke) and send_problem(last_push(spoke)) is None


def test_an_unreachable_hub_waits_out_the_grace_before_warning(tmp_path, posted):
    spoke, _claude, _main = _make_spoke(tmp_path, "http://127.0.0.1:9", "tok", extra="[updates]\nnotify = true\n")
    with pytest.raises(HubUnreachable):
        push(spoke)
    last = last_push(spoke)
    assert last["failure"]["reason"] == "unreachable" and "at" not in last  # never got through
    assert send_problem(last) is None and posted == []  # offline for a bit, a hub restarting: no news
    since = parse_ts(last["failure"]["since"])
    later = send_problem(last, now=since + UNREACHABLE_GRACE + timedelta(minutes=1))
    assert later["reason"] == "unreachable" and later["last_ok"] is None

    # the next failure after the grace notifies, once
    last["failure"]["since"] = (since - UNREACHABLE_GRACE - timedelta(minutes=1)).isoformat()
    (spoke.home / "last-push.json").write_text(json.dumps(last))
    for _ in range(3):
        with pytest.raises(HubUnreachable):
            push(spoke)
    assert len(posted) == 1 and "Can't reach the hub" in posted[0][0]
    assert last_push(spoke)["failure"]["notified"] is True and last_push(spoke)["failure"]["attempts"] == 4


def test_the_notification_comes_once_per_run_of_failures(hubenv, posted):  # noqa: F811
    spoke = hubenv["spoke"]
    (spoke.home / "config.toml").write_text((spoke.home / "config.toml").read_text() + "[updates]\nnotify = true\n")
    spoke = load_config(spoke.home)
    _turned_away(hubenv)
    for _ in range(3):
        with pytest.raises(HubUnauthorized):
            push(spoke)
    assert len(posted) == 1
    title, message = posted[0]
    assert "no longer accepts this computer" in title and f"interlatch hub join {hubenv['url']} --code" in message

    hubenv["conn"].execute("DELETE FROM people")
    hubenv["conn"].commit()
    push(spoke)  # through again: a later run of failures is news again
    people.add(hubenv["conn"], "Ada", None, "admin")
    hubenv["conn"].commit()
    with pytest.raises(HubUnauthorized):
        push(spoke)
    assert len(posted) == 2


def test_a_computer_that_never_joined_notes_nothing(tmp_path):
    spoke, _claude, _main = _make_spoke(tmp_path, "http://127.0.0.1:9", "tok")
    spoke.home.joinpath("hub-token").unlink()
    with pytest.raises(HubError):
        push(spoke)
    assert last_push(spoke) is None


def test_the_menu_bar_says_why(tmp_path):
    from interlatch.runtime.platforms.menubar import ATTENTION, summarize

    spoke, _claude, _main = _make_spoke(tmp_path, "https://hub.example", "tok")
    note_failure(spoke, HubUnauthorized("the hub did not accept this computer's token"))
    st = {"jobs": {}, "pending": {"queued": 0}, "hub_url": "https://hub.example", "last_sync": None}
    snap = summarize(st, push=last_push(spoke))
    assert snap.state == ATTENTION and snap.title == "The hub no longer accepts this computer"
    assert "Nothing sent since" in snap.sub and "new invite" in snap.sub

    note_failure(spoke, HubUnreachable("can't reach the hub"))  # fresh trouble of another kind: not yet a warning
    last = last_push(spoke)
    assert last["failure"]["reason"] == "unreachable" and last["failure"]["attempts"] == 2
    assert summarize(st, push={**last, "failure": {**last["failure"], "since": last["failure"]["at"]}}).state != ATTENTION


def test_the_dashboard_warns_and_joins_again_with_a_new_invite(hubenv, monkeypatch):  # noqa: F811
    from interlatch.interfaces.web import api

    spoke = hubenv["spoke"]
    push(spoke)
    _turned_away(hubenv)
    with pytest.raises(HubUnauthorized):
        push(spoke)
    pushes = []
    monkeypatch.setattr(api.devices, "action_push", lambda ctx: pushes.append(1) or True)
    app, httpd, url = _serve(spoke)
    try:
        status = _call(url, "/api/jobs")[1]
        assert status["hub_problem"]["reason"] == "token" and status["hub_problem"]["hub"] == hubenv["url"]
        dv = _call(url, "/api/devices")[1]
        assert dv["push_problem"]["reason"] == "token" and dv["last_push"]["failure"]["attempts"] == 1
        assert _call(url, "/api/overview")[1]["status"]["hub_problem"]["reason"] == "token"

        bob = people.add(hubenv["conn"], "Bob", None, "member", projects=None)
        code = people.invite(hubenv["conn"], bob["id"])
        hubenv["conn"].commit()
        assert _call(url, "/api/devices/rejoin-hub", {"code": code}, REMOTE)[0] == 403  # from a phone: never
        assert _call(url, "/api/devices/rejoin-hub", {"code": ""})[0] == 400
        status_code, r = _call(url, "/api/devices/rejoin-hub", {"code": "not-a-code"})
        assert status_code == 400 and r["error"]
        before = read_token(spoke)
        status_code, r = _call(url, "/api/devices/rejoin-hub", {"code": code})
        assert status_code == 200 and r["ok"] and r["person"] == {"name": "Bob", "role": "member"}
        assert read_token(load_config(spoke.home)) != before and pushes == [1]
    finally:
        httpd.shutdown()
    report = push(load_config(spoke.home))  # what the job would do: it gets through, and the warning goes
    assert not report.errors and send_problem(last_push(spoke)) is None


def test_a_good_push_without_a_failure_keeps_its_shape(hubenv):  # noqa: F811
    spoke = hubenv["spoke"]
    push(spoke)
    assert set(last_push(spoke)) == {"at", "summary", "errors", "kind", "sent", "unchanged", "kept", "team"}
