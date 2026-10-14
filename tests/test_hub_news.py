"""News on a hub's Team page (news.py): what other computers sent, as the toasts, the rail's count and Team overview's
"New since your last visit" show it. New sessions and new lessons count, a session sent again does not; a computer
that keeps sending adds to its row; who sees what follows the Team page; each person's "seen" is their own."""

from datetime import timedelta

from interlatch import hub, news, people
from interlatch.core.util import to_iso, utcnow
from interlatch.core.machine import local_machine

from conftest import CWD
from test_hub import SPOKE_CWD, SPOKE_SID, _analyzed_on_spoke, hubenv  # noqa: F401  (the fixture)
from test_people_web import MACHINE, _call, _as, team, hubweb  # noqa: F401  (the fixtures)
from test_team import teamenv  # noqa: F401  (the fixture)

OTHER_PROJECT = "/hub/other-project"


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT kind, machine_id, project_path, sessions, lessons FROM hub_news ORDER BY id")]


def test_shared_knowledge_is_news_once(teamenv):  # noqa: F811
    conn, spoke = teamenv["conn"], teamenv["spoke"]
    me = local_machine(spoke)["id"]
    _analyzed_on_spoke(spoke, title="Mine", at="2026-10-02T00:00:00Z",
                       lessons=[("p1", "project", "gotcha"), ("pref", "project", "preference"), ("g1", "global", "fix")])
    assert hub.push(spoke).sent == 1
    assert _rows(conn) == [{"kind": "shared", "machine_id": me, "project_path": SPOKE_CWD, "sessions": 1, "lessons": 1}]
    hub.push(spoke)  # nothing changed: nothing new
    _analyzed_on_spoke(spoke, title="Mine, later", at="2026-10-03T00:00:00Z",  # analyzed again: one lesson more
                       lessons=[("p1", "project", "gotcha"), ("p2", "project", "fact")])
    assert hub.push(spoke).sent == 1  # sent again, as it grew: not a new session, but a new lesson
    assert _rows(conn) == [{"kind": "shared", "machine_id": me, "project_path": SPOKE_CWD, "sessions": 1, "lessons": 2}]


def test_transcripts_and_the_hubs_own_analysis_of_them(hubenv):  # noqa: F811
    from interlatch.analyze import store_analysis
    from interlatch.ingest import sync

    cfg, conn, spoke = hubenv["cfg"], hubenv["conn"], hubenv["spoke"]
    me = local_machine(spoke)["id"]
    hub.push(spoke)
    sync(cfg, conn)
    assert _rows(conn) == [{"kind": "shared", "machine_id": me, "project_path": SPOKE_CWD, "sessions": 1, "lessons": 0}]
    data = {"title": "T", "knowledge": [{"kind": "gotcha", "title": "A gotcha", "scope": "project"},
                                        {"kind": "preference", "title": "Likes tabs", "scope": "project"}]}
    store_analysis(conn, cfg, SPOKE_SID, data, "test", 2)
    store_analysis(conn, cfg, SPOKE_SID, data, "test", 2)  # the same lessons again: nothing new
    assert _rows(conn)[0]["lessons"] == 1
    own = conn.execute("SELECT id FROM sessions WHERE machine_id != ? LIMIT 1", (me,)).fetchone()[0]
    store_analysis(conn, cfg, own, data, "test", 1)  # the hub's own session: not news
    assert len(_rows(conn)) == 1


def test_rows_fold_while_a_computer_keeps_sending(hubweb):  # noqa: F811
    conn = hubweb["conn"]
    news.record(conn, MACHINE, CWD, sessions=2)
    news.record(conn, MACHINE, CWD, lessons=3)
    news.record(conn, MACHINE, CWD)  # nothing new: no row
    news.record(conn, MACHINE, OTHER_PROJECT, sessions=1)
    assert [(r["project_path"], r["sessions"], r["lessons"]) for r in _rows(conn)] == [(CWD, 2, 3), (OTHER_PROJECT, 1, 0)]
    old = to_iso(utcnow() - timedelta(minutes=news.FOLD_MINUTES + 1))
    conn.execute("UPDATE hub_news SET at = ? WHERE project_path = ?", (old, CWD))
    news.record(conn, MACHINE, CWD, sessions=1)  # a while later: a row of its own
    assert [(r["sessions"], r["lessons"]) for r in _rows(conn) if r["project_path"] == CWD] == [(2, 3), (1, 0)]
    gone = to_iso(utcnow() - timedelta(days=news.KEEP_DAYS + 1))
    conn.execute("UPDATE hub_news SET at = ? WHERE project_path = ?", (gone, OTHER_PROJECT))
    news.record(conn, MACHINE, CWD, sessions=1)
    assert OTHER_PROJECT not in {r["project_path"] for r in _rows(conn)}  # kept KEEP_DAYS


def test_who_hears_of_what_and_what_they_saw(team):  # noqa: F811
    conn, url, s = team["conn"], team["url"], team["sessions"]
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'Bob''s Mac', 'spoke', ?)",
                 (MACHINE, team["bob"]["id"]))
    news.record(conn, MACHINE, None, kind="joined")
    news.record(conn, MACHINE, CWD, sessions=2, lessons=1)
    news.record(conn, MACHINE, OTHER_PROJECT, sessions=1)
    conn.commit()

    code, r = _call(url, "/api/team/news", headers=_as(s["Ada"]))  # an admin: joins too
    assert code == 200 and r["unseen"] == 3 and [x["kind"] for x in r["items"]] == ["shared", "shared", "joined"]
    assert r["items"][2]["person"] == "Bob" and r["items"][2]["computer"] == "Bob's Mac"
    shared = next(x for x in r["items"] if x["project_path"] == CWD)
    assert (shared["sessions"], shared["lessons"], shared["project"]) == (2, 1, CWD.rsplit("/", 1)[-1])
    code, r = _call(url, "/api/team/news", headers=_as(s["Bob"]))  # a member: no joins
    assert code == 200 and r["unseen"] == 2 and {x["kind"] for x in r["items"]} == {"shared"}

    people.set_projects(conn, team["bob"]["id"], [OTHER_PROJECT])  # limited to one project: of that one only
    code, r = _call(url, "/api/team/news", headers=_as(s["Bob"]))
    assert code == 200 and r["unseen"] == 1 and [x["project_path"] for x in r["items"]] == [OTHER_PROJECT]
    code, r = _call(url, "/api/team", headers=_as(s["Bob"]))
    assert code == 200 and [x["project_path"] for x in r["news"]["items"]] == [OTHER_PROJECT]

    code, r = _call(url, "/api/team/news", headers=_as(s["Vic"]))  # read-only people mark what they saw, too
    assert r["unseen"] == 2
    assert _call(url, "/api/team/news/seen", {"at": r["now"]}, _as(s["Vic"])) == (200, {"ok": True})
    assert _call(url, "/api/team/news", headers=_as(s["Vic"]))[1]["unseen"] == 0
    assert _call(url, "/api/team/news", headers=_as(s["Ada"]))[1]["unseen"] == 3  # each person's own
    assert _call(url, "/api/team/news/seen", {"at": "2020-01-01T00:00:00.000Z"}, _as(s["Vic"]))[0] == 200  # never back
    assert _call(url, "/api/team/news", headers=_as(s["Vic"]))[1]["unseen"] == 0

    news.record(conn, MACHINE, CWD, lessons=4)  # added to since: `after` brings that row only
    conn.commit()
    code, r2 = _call(url, "/api/team/news?after=" + r["now"], headers=_as(s["Vic"]))
    assert code == 200 and [(x["project_path"], x["lessons"]) for x in r2["items"]] == [(CWD, 5)] and r2["unseen"] == 1


def test_seen_is_kept_honest(hubweb):  # noqa: F811
    conn = hubweb["conn"]
    news.mark_seen(conn, None, "2999-01-01T00:00:00.000Z")  # never later than now
    assert news.seen_at(conn, None) <= to_iso(utcnow())
    news.mark_seen(conn, {"id": 7}, "not a time")  # what isn't a time: now
    assert abs((utcnow() - _parse(news.seen_at(conn, {"id": 7}))).total_seconds()) < 5
    first = news.seen_at(conn, {"id": 8})  # never looked: the last FIRST_DAYS are new
    assert abs((utcnow() - timedelta(days=news.FIRST_DAYS) - _parse(first)).total_seconds()) < 5


def test_a_computer_joining_is_news(team, monkeypatch):  # noqa: F811
    cfg, conn = team["cfg"], team["conn"]
    code = people.invite(conn, team["bob"]["id"])
    hub.join_with_code(cfg, conn, {"code": code, "machine": MACHINE, "name": "Bob's Mac"})
    assert _rows(conn) == [{"kind": "joined", "machine_id": MACHINE, "project_path": None, "sessions": 0, "lessons": 0}]


def _parse(ts):
    from interlatch.core.util import parse_ts

    return parse_ts(ts)


def test_an_archive_at_the_case_files_schema_gains_the_news_table(tmp_path):
    """Case files (#101) took schema 17 first: an archive already there must still get hub_news."""
    import sqlite3

    from interlatch.storage.sqlite import connect
    from interlatch.storage.schema import SCHEMA_VERSION

    db = tmp_path / "chronicle.db"
    connect(db).close()
    old = sqlite3.connect(db)
    old.execute("DROP TABLE hub_news")
    old.execute("PRAGMA user_version = 17")
    old.commit()
    old.close()
    conn = connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM hub_news").fetchone()[0] == 0
    conn.close()
