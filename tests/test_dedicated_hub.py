"""A dedicated hub (`[hub] dedicated`, which the Docker image sets): its dashboard learns what kind of hub it is, and its
admins make projects there by name from any browser. On any other hub, projects stay with the hub computer itself."""

import pytest

from interlatch.core.config import set_config_value

from test_people_web import REMOTE, _as, _call, hubweb, team  # noqa: F401  (the fixtures)


def _dedicate(team):  # noqa: F811
    set_config_value(team["cfg"], "hub", "dedicated", "true")
    set_config_value(team["cfg"], "hub", "address", '"https://hub.example.com"')


def _shared(team, who="Ada"):  # noqa: F811
    return _call(team["url"], "/api/projects/shared", headers=_as(team["sessions"][who]))[1]["shared"]


def test_me_says_what_kind_of_hub_this_is(team):  # noqa: F811
    url, s = team["url"], team["sessions"]
    hub = _call(url, "/api/me", headers=_as(s["Bob"]))[1]["hub"]
    assert hub["dedicated"] is False and hub["address"] is None
    _dedicate(team)
    hub = _call(url, "/api/me", headers=_as(s["Bob"]))[1]["hub"]
    assert hub["dedicated"] is True and hub["address"] == "https://hub.example.com" and hub["team"] is True
    assert hub["knowledge_only"] is False


def test_an_admin_makes_and_removes_a_project_by_name(team):  # noqa: F811
    url, s, home = team["url"], team["sessions"], team["cfg"].home
    _dedicate(team)
    code, r = _call(url, "/api/projects/shared/add", {"name": "見積もり チェック"}, _as(s["Ada"]))
    folder = str((home / "projects" / "見積もり チェック").resolve())
    assert code == 200 and r["result"]["name"] == "見積もり チェック" and r["result"]["path"] == folder
    assert r["dedicated"] is True and [x["path"] for x in _shared(team)] == [folder]
    audit = _call(url, "/api/people", headers=_as(s["Ada"]))[1]["audit"]
    assert (audit[0]["action"], audit[0]["actor_name"]) == ("project-add", "Ada")

    code, r = _call(url, "/api/projects/shared/add", {"name": "見積もり チェック"}, _as(s["Ada"]))
    assert code == 200 and r["result"]["existed"]  # the same name again: the same project
    code, r = _call(url, "/api/projects/shared/remove", {"path": folder}, _as(s["Ada"]))
    assert code == 200 and _shared(team) == []


def test_only_a_dedicated_hubs_admins_and_only_by_name(team):  # noqa: F811
    url, s, home = team["url"], team["sessions"], team["cfg"].home
    body = {"name": "Website"}
    assert _call(url, "/api/projects/shared/add", body, _as(s["Ada"]))[0] == 403  # any other hub: its own computer only
    _dedicate(team)
    assert _call(url, "/api/projects/shared/add", body, _as(s["Bob"]))[0] == 403  # a member
    assert _call(url, "/api/projects/shared/add", body, _as(s["Vic"]))[0] == 403  # read-only
    assert _call(url, "/api/projects/shared/add", body, REMOTE)[0] in (401, 403)  # nobody signed in
    code, r = _call(url, "/api/projects/shared/add", {"folder": str(team["tmp"])}, _as(s["Ada"]))
    assert code == 400 and "80 characters" in r["error"]  # from a browser, a name only: never a folder of the hub
    assert _shared(team) == [] and not (home / "projects").exists()


@pytest.mark.parametrize("name", ["", "   ", "../escape", "a/b", "a\\b", ".hidden", "..", "x" * 81, "a\x00b"])
def test_a_projects_name_stays_inside_the_projects_folder(team, name):  # noqa: F811
    url, s, home = team["url"], team["sessions"], team["cfg"].home
    _dedicate(team)
    code, r = _call(url, "/api/projects/shared/add", {"name": name}, _as(s["Ada"]))
    assert code == 400 and "80 characters" in r["error"]
    assert not (home / "escape").exists() and not (home / "projects").exists() and _shared(team) == []
