"""Who's hiring (sharing/market.py, and the Party's market blocks): the numbers are read from Firestore with the web key
alone and nothing about the person, only once a Party is signed in, kept six hours, and the bundled copy stands in
whenever Firestore can't answer (no document yet, offline, slow) without ever failing the Party. Only known fields of
the right types reach the page, and each payload carries the numbers of the tracks it shows. Nothing here reaches
Firebase: leaderboard._call is test_leaderboard's FakeNet, and conftest keeps every other test off the market."""

from __future__ import annotations

import json
import threading
import time

import pytest

from interlatch.sharing import leaderboard as lb
from interlatch.sharing import market

from conftest import SID
from test_leaderboard import net  # noqa: F401  (the fixture)
from test_party import _lesson, _sign_in, catalog  # noqa: F401  (catalog: the fixture)
from test_people_web import _as, hubweb, team  # noqa: F401  (the fixtures)
from test_people_web import _call as _http

READ_LIVE = market.read_live  # the real read: conftest replaces it in every test with one that can't connect

MARKET = {
    "status": "test", "as_of": "2026-10-15", "window_days": 90, "catalog_version": 3,
    "sample": {"boards": 73, "global": 9164, "japan": 169},
    "classes": {
        "backend": {"global": 312, "japan": 4, "new_global": 90, "new_japan": 1, "share_global": 0.09,
                    "share_japan": 0.03, "trend_global": None, "trend_japan": None,
                    "top_modules": ["M-sql", "M-python", "M-systems"]},
        "sre": {"global": 0, "japan": 0, "new_global": 0, "new_japan": 0, "share_global": 0.0, "share_japan": 0.0,
                "trend_global": None, "trend_japan": None, "top_modules": []},
        "devtools": {"global": 120, "japan": 2, "new_global": 30, "new_japan": 0, "share_global": 0.04,
                     "share_japan": 0.01, "trend_global": 1.2, "trend_japan": None, "top_modules": ["M-python"]},
    },  # no ai-eng: a track the market doesn't have
    "unmatched": {"global": 0.08, "japan": 0.02},
}


def _fs(v):
    """A plain value as Firestore's REST API writes it."""
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, list):
        return {"arrayValue": {"values": [_fs(x) for x in v]} if v else {}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: _fs(x) for k, x in v.items()}} if v else {}}
    return {"stringValue": str(v)}


def _doc(data: dict) -> dict:
    return {"name": "projects/interlatch/databases/(default)/documents/market/current",
            "fields": {k: _fs(v) for k, v in data.items()}, "updateTime": "2026-10-15T06:00:00Z"}


@pytest.fixture()
def live(net, monkeypatch):  # noqa: F811
    """The real read, against FakeNet: Firestore holds MARKET until a test says otherwise."""
    monkeypatch.setattr(market, "read_live", READ_LIVE)
    net.on("GET", market.DOC_URL, _doc(MARKET))
    return net


def _bundle(data) -> None:
    market.BUNDLED_PATH.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def _reads(fake) -> list[dict]:
    return [c for c in fake.calls if c["url"].startswith(market.DOC_URL)]


def _settle() -> None:
    if market._reader is not None:
        market._reader.join(5)


def _clock(monkeypatch, start=1000.0):
    now = {"t": start}
    monkeypatch.setattr(market, "_now", lambda: now["t"])
    return now


# ------------------------------------------------------------------ reading
def test_the_market_is_read_with_the_web_key_alone(live):
    got = market.current()
    assert got["source"] == "live" and got["status"] == "test" and got["as_of"] == "2026-10-15"
    assert got["sample"] == {"boards": 73, "global": 9164, "japan": 169} and got["window_days"] == 90
    assert got["classes"]["backend"] == {**MARKET["classes"]["backend"], "top_modules": ["M-sql", "M-python", "M-systems"]}
    assert got["classes"]["sre"]["top_modules"] == [] and got["classes"]["devtools"]["trend_global"] == 1.2
    assert "unmatched" not in got and "catalog_version" not in got
    [call] = _reads(live)
    assert call["method"] == "GET" and call["url"] == f"{market.DOC_URL}?key=test-key"
    assert call["token"] is None and call["body"] is None and call["form"] is None  # no sign-in, nothing about anyone
    assert [c["url"] for c in live.calls] == [call["url"]]


def test_the_live_numbers_are_kept_six_hours_then_read_again_beside_the_party(live, monkeypatch):
    now = _clock(monkeypatch)
    assert market.current()["classes"]["backend"]["global"] == 312
    now["t"] += market.TTL - 1
    assert market.current()["source"] == "live"
    _settle()
    assert len(_reads(live)) == 1  # no read beside it either
    live.on("GET", market.DOC_URL, _doc({**MARKET, "as_of": "2026-10-16"}))
    now["t"] += 2
    assert market.current()["as_of"] == "2026-10-15"  # stale: answered at once, the new read runs beside it
    _settle()
    assert market.current()["as_of"] == "2026-10-16" and len(_reads(live)) == 2


def test_without_the_document_the_bundled_copy_and_another_try_later(live, monkeypatch):
    now = _clock(monkeypatch)
    _bundle({**MARKET, "as_of": "2026-10-01"})

    def missing(body, form):
        raise lb.LeaderboardError("404 Document not found")

    live.on("GET", market.DOC_URL, missing)  # what Firestore answers until the first publish
    got = market.current()
    assert got["source"] == "bundled" and got["as_of"] == "2026-10-01" and got["classes"]["backend"]["global"] == 312
    now["t"] += market.RETRY - 1
    assert market.current()["source"] == "bundled"
    _settle()
    assert len(_reads(live)) == 1
    live.on("GET", market.DOC_URL, _doc(MARKET))
    now["t"] += 2
    market.current()
    _settle()
    assert market.current()["source"] == "live" and len(_reads(live)) == 2


def test_a_failed_read_keeps_the_last_live_numbers(live, monkeypatch):
    now = _clock(monkeypatch)
    _bundle({**MARKET, "as_of": "2026-09-01"})
    assert market.current()["source"] == "live"

    def offline(body, form):
        raise lb.LeaderboardError("no connection: [Errno 8] nodename nor servname provided")

    live.on("GET", market.DOC_URL, offline)
    now["t"] += market.TTL + 1
    market.current()
    _settle()
    got = market.current()
    assert got["source"] == "live" and got["as_of"] == "2026-10-15" and len(_reads(live)) == 2


def test_a_slow_firestore_holds_the_party_back_a_moment_at_most(live, monkeypatch):
    monkeypatch.setattr(market, "WAIT", 0.05)
    _bundle(MARKET)
    gate = threading.Event()

    def slow(body, form):
        gate.wait(5)
        return _doc({**MARKET, "as_of": "2026-10-16"})

    live.on("GET", market.DOC_URL, slow)
    start = time.monotonic()
    assert market.current()["source"] == "bundled" and time.monotonic() - start < 1
    assert market.current()["source"] == "bundled"  # the read still runs: nobody waits for it again
    gate.set()
    _settle()
    assert market.current()["source"] == "live" and len(_reads(live)) == 1


def test_no_connection_and_no_bundled_copy_is_no_market_and_nothing_raises(live, monkeypatch):
    def broken(body, form):
        raise RuntimeError("anything at all")

    live.on("GET", market.DOC_URL, broken)
    assert market.current() is None
    market.clear_cache()
    _bundle("{not json")
    assert market.current() is None
    market.clear_cache()
    monkeypatch.setattr(market, "bundled", lambda: 1 / 0)  # even a failure of its own, in the reader or here
    market._refresh()
    assert market._cache is None
    assert market.current() is None
    _settle()
    monkeypatch.setattr(market, "_cache", (0.0, 1.0, None))
    monkeypatch.setattr(market, "_now", lambda: 1 / 0)
    assert market.current() is None


def test_only_known_fields_of_the_right_types_reach_the_page():
    raw = {"status": "test<script>", "as_of": "15 Oct", "window_days": "90", "title": "Senior engineer",
           "sample": {"boards": -3, "global": 9164.0, "japan": True, "url": "https://boards.example/1"},
           "classes": {"backend": {"global": "312", "japan": -1, "new_global": float("nan"), "share_global": 1.7,
                                   "share_japan": -0.2, "trend_global": float("inf"), "trend_japan": "up",
                                   "top_modules": ["M-sql", "M-sql", {"x": 1}, "../etc", "M-python", 7] + ["M-a"] * 1 +
                                   [f"M-{i}" for i in range(9)], "description": "We build…"},
                       "bad id!": {"global": 1}, "ai-eng": "not a map"}}
    got = market.clean(raw)
    assert got == {"status": None, "as_of": None, "window_days": None, "sample": {"boards": 0, "global": 9164, "japan": 0},
                   "classes": {"backend": {"global": 0, "japan": 0, "new_global": 0, "new_japan": 0, "share_global": 1.0,
                                           "share_japan": 0.0, "trend_global": None, "trend_japan": None,
                                           "top_modules": ["M-sql", "M-python", "M-a", "M-0", "M-1", "M-2"]}}}
    assert market.clean({"classes": []}) is None and market.clean([]) is None and market.clean(None) is None


# ------------------------------------------------------------------ the Party's market blocks
def test_the_market_is_read_only_for_a_signed_in_party(hubweb, catalog, live):  # noqa: F811
    url, cfg, conn = hubweb["url"], hubweb["cfg"], hubweb["conn"]
    _lesson(conn, SID, "pytest secrets")  # claude leans to backend and ai-eng
    status, data = _http(url, "/api/party")
    assert status == 200 and "market" not in data
    status, tk = _http(url, "/api/party/tracks")
    assert status == 200 and "market" not in tk and set(tk["categories"][0]["tracks"][0]) == {"id", "name"}
    assert _http(url, "/api/party/track?id=backend")[0] == 403
    assert live.calls == []  # not even the web key's config: nothing reaches Firebase before a sign-in
    _sign_in(cfg, board=False)
    status, data = _http(url, "/api/party")
    m = data["market"]
    assert status == 200 and {k: m[k] for k in ("status", "as_of", "source", "window_days", "sample")} == {
        "status": "test", "as_of": "2026-10-15", "source": "live", "window_days": 90,
        "sample": {"boards": 73, "global": 9164, "japan": 169}}
    assert list(m["classes"]) == ["backend"]  # the tracks it shows (ai-eng too, which the market hasn't): not devtools
    assert m["classes"]["backend"]["global"] == 312 and m["classes"]["backend"]["share_global"] == 0.09
    assert m["classes"]["backend"]["top_modules"] == [{"module": "M-sql", "name": "Sql", "attr": "data"},
                                                      {"module": "M-python", "name": "Python", "attr": "craft"}]
    status, data = _http(url, "/api/party/goal", {"track": "sre"})  # the goal and its quests: sre's numbers too
    assert status == 200 and list(data["market"]["classes"]) == ["backend", "sre"]
    assert data["market"]["classes"]["sre"]["global"] == 0
    status, tr_ = _http(url, "/api/party/track?id=backend", headers={"X-Interlatch-Lang": "ja"})
    assert status == 200 and list(tr_["market"]["classes"]) == ["backend"] and tr_["market"]["source"] == "live"
    assert [x["name"] for x in tr_["market"]["classes"]["backend"]["top_modules"]] == ["M-sql（日本語）", "M-python（日本語）"]
    assert _http(url, "/api/party/track?id=ai-eng")[1]["market"]["classes"] == {}
    status, tk = _http(url, "/api/party/tracks")
    by = {tr["id"]: tr for c in tk["categories"] for tr in c["tracks"]}
    assert status == 200 and tk["market"]["as_of"] == "2026-10-15" and "classes" not in tk["market"]
    assert by["backend"] == {"id": "backend", "name": "backend track", "global": 312, "japan": 4, "share_global": 0.09}
    assert by["ai-eng"] == {"id": "ai-eng", "name": "ai-eng track", "global": None, "japan": None, "share_global": None}
    assert len(_reads(live)) == 1 and all(c["token"] is None for c in live.calls)  # kept: one read for all of them


def test_without_any_numbers_the_party_opens_as_before(hubweb, catalog):  # noqa: F811
    url, cfg, conn = hubweb["url"], hubweb["cfg"], hubweb["conn"]
    _lesson(conn, SID, "pytest secrets")
    _sign_in(cfg, board=False)  # conftest: no connection, no bundled copy
    status, data = _http(url, "/api/party")
    assert status == 200 and data["market"] is None and data["agents"][0]["skills"]
    status, tr_ = _http(url, "/api/party/track?id=backend")
    assert status == 200 and tr_["market"] is None and tr_["modules"]
    status, tk = _http(url, "/api/party/tracks")
    assert status == 200 and tk["market"] is None and tk["categories"][0]["tracks"][0] == {"id": "backend", "name": "backend track"}


def test_a_hub_person_gets_the_numbers_without_their_sign_in_going_anywhere(team, catalog, live):  # noqa: F811
    url, s = team["url"], team["sessions"]
    status, data = _http(url, "/api/party/goal", {"track": "backend"}, _as(s["Bob"]))
    assert status == 200 and list(data["market"]["classes"]) == ["backend"]
    assert [c["url"] for c in live.calls] == [f"{market.DOC_URL}?key=test-key"]
    assert s["Bob"] not in json.dumps(live.calls) and all(c["token"] is None for c in live.calls)


def test_the_bundled_copy_is_a_market_the_page_can_draw():
    """knowledge/market.json, once careers/ wrote it: cleaned it is the same document, every class a roadmap track."""
    from interlatch.knowledge import party

    path = market.Path(market.__file__).resolve().parents[1] / "knowledge" / "market.json"
    if not path.exists():
        pytest.skip("market.json isn't there yet")
    raw = json.loads(path.read_text(encoding="utf-8"))
    got = market.clean(raw)
    assert got is not None and got["as_of"] and got["status"] and got["sample"]["boards"] > 0
    assert set(got["classes"]) == set(raw["classes"]) <= set(party.load_catalog().track)
    for cid, c in raw["classes"].items():
        assert got["classes"][cid]["top_modules"] == c.get("top_modules", [])[:market.TOP_MODULES], cid
        assert all(got["classes"][cid][k] == c.get(k) for k in market.COUNTS), cid
