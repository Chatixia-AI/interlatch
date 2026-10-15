"""The market pipeline (careers/pipeline.py): each provider's postings from a small fixture (no network), which ones are
technical and in Japan (Japanese titles and places too), duplicates and a board's English/Japanese copies, the
boilerplate it strips, the id diff against the history (new, still open, closed, a board that failed), mapping
answers and their limits, the board-weighted aggregate, the Firestore values and writes, that nothing but the record's
own fields is ever stored, and that the rules still hold the leaderboard's block."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("careers_pipeline", ROOT / "careers" / "pipeline.py")
pl = importlib.util.module_from_spec(_spec)
sys.modules["careers_pipeline"] = pl
_spec.loader.exec_module(pl)

TITLE = "Secret Title Engineer"
GH = pl.Board("greenhouse", "acme", "Acme")
ASHBY = pl.Board("ashby", "beta", "Beta")
LEVER = pl.Board("lever", "gamma", "Gamma")
JP_HQ = pl.Board("greenhouse", "paypay", "PayPay", region="japan")
TWINS = pl.Board("lever", "woven", "Woven", region="japan", copies="en-ja")


def gh_job(jid, title, place="San Francisco, CA", content="&lt;p&gt;Build things.&lt;/p&gt;", offices=()):
    return {"id": jid, "title": title, "location": {"name": place}, "offices": list(offices),
            "departments": [{"name": "Engineering"}], "first_published": "2026-09-01T10:00:00-04:00",
            "updated_at": "2026-10-14T10:00:00-04:00", "content": content,
            "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{jid}"}


def lever_job(jid, title, location="Tokyo", team="AD/ADAS", country=None):
    return {"id": jid, "text": title, "categories": {"location": location, "allLocations": [location], "team": team,
                                                     "department": "Product"},
            "country": country, "createdAt": 1786469891368, "openingPlain": "About us", "descriptionBodyPlain": "Role",
            "lists": [{"text": "Requirements", "content": "<li>Python</li>"}], "additionalPlain": "",
            "hostedUrl": f"https://jobs.lever.co/gamma/{jid}"}


def posting(board, pid, title="Backend Engineer", region="global", technical=True, published="2026-09-01"):
    p = pl.Posting(board, str(pid), title, ["somewhere"], "Eng", published, "text", region, technical)
    return p


# ------------------------------------------------------------------ providers
def test_greenhouse_unescapes_and_strips_its_html():
    content = "&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Go&lt;/li&gt;&lt;/ul&gt;"
    [p] = pl.normalize(GH, {"jobs": [gh_job(1, "Backend Engineer", content=content)]})
    assert p.title == "Backend Engineer" and p.published == "2026-09-01" and p.department == "Engineering"
    assert "<" not in p.text and "Build & ship" in p.text and "Go" in p.text
    assert p.technical and p.region == "global"


def test_ashby_reads_secondary_locations_and_skips_unlisted():
    data = {"jobs": [
        {"id": "a1", "title": "Forward Deployed Engineer", "location": "San Francisco", "isListed": True,
         "secondaryLocations": [{"location": "Tokyo", "address": {"postalAddress": {"addressCountry": "Japan"}}}],
         "publishedAt": "2026-10-01T00:00:00.000+00:00", "descriptionPlain": "Work with customers",
         "department": "GTM", "team": "FDE"},
        {"id": "a2", "title": "Hidden Engineer", "location": "Remote", "isListed": False}]}
    [p] = pl.normalize(ASHBY, data)
    assert p.posting_id == "a1" and p.region == "japan" and p.department == "GTM / FDE"


def test_lever_country_and_lists():
    [p] = pl.normalize(LEVER, [lever_job("l1", "Platform Engineer", location="Remote", country="JP")])
    assert p.region == "japan" and "Requirements: Python" in p.text and p.published == "2026-08-11"


# ------------------------------------------------------------------ region and the technical filter
@pytest.mark.parametrize("board,places,want", [
    (GH, ["Tokyo, Japan"], "japan"),
    (GH, ["東京都中央区"], "japan"),
    (GH, ["福岡県福岡市"], "japan"),
    (GH, ["Susono, Shizuoka"], "japan"),
    (GH, ["Hybrid"], "global"),  # a global company's "Hybrid" says nothing
    (JP_HQ, ["Hybrid", "Hybrid"], "japan"),  # PayPay names no city
    (JP_HQ, ["Remote | リモート / Remote"], "japan"),
    (JP_HQ, ["Singapore"], "global"),
    (GH, ["Bangkok, Thailand"], "global"),
])
def test_region(board, places, want):
    assert pl.region_of(board, places) == want


@pytest.mark.parametrize("title,want", [
    ("Backend Engineer", True), ("Sales Engineer", True), ("Technical Account Manager", True),
    ("Solutions Consultant", True), ("Threat Hunter", True), ("Engineering Manager, Platform", True),
    ("バックエンドエンジニア", True), ("AD/ADAS ソフトアーキ設計、組み込みソフト設計", True), ("テスト戦略技術スペシャリスト", True),
    ("Account Executive, Enterprise", False), ("Recruiting Engineer", False), ("Legal Counsel", False),
    ("Product Designer", False), ("プロジェクトマネージャー・事業開発", False), ("エンタープライズ営業担当", False),
])
def test_technical_filter(title, want):
    assert pl.is_technical(title) is want


# ------------------------------------------------------------------ duplicates, copies, boilerplate
def test_identical_postings_collapse():
    jobs = [gh_job(1, "Backend Engineer"), gh_job(2, "Backend Engineer"), gh_job(3, "Backend Engineer", "Austin, TX")]
    assert [p.posting_id for p in pl.normalize(GH, {"jobs": jobs})] == ["1", "3"]


def test_japanese_copies_drop_only_on_a_board_that_posts_both():
    jobs = [lever_job("e1", "Motion Planning Engineer, AD/ADAS"), lever_job("j1", "モーションプランニングエンジニア・AD/ADAS"),
            lever_job("j2", "データエンジニア（障がい者雇用）", team="Data")]  # Japanese only: its department has no English
    kept = {p.posting_id for p in pl.normalize(TWINS, jobs)}
    assert kept == {"e1", "j2"}
    plain = pl.Board("lever", "other", "Other", region="japan")
    assert {p.posting_id for p in pl.normalize(plain, jobs)} == {"e1", "j1", "j2"}


def test_boilerplate_lines_are_stripped():
    pitch = "Acme builds the world's most reliable rockets and we are proud of it."
    jobs = [gh_job(i, f"Engineer {i}", content=f"&lt;p&gt;{pitch}&lt;/p&gt;&lt;p&gt;Role number {i} details "
                                              f"that are specific to this job only.&lt;/p&gt;") for i in range(6)]
    ps = pl.normalize(GH, {"jobs": jobs})
    assert all(pitch not in p.text and "Role number" in p.text for p in ps)


# ------------------------------------------------------------------ fetching
def test_fetch_one_request_per_board_and_failures_close_nothing():
    urls = []

    def get(url):
        urls.append(url)
        if "beta" in url:
            raise pl.URLError("down")
        return json.dumps({"jobs": [gh_job(1, "Backend Engineer")]}).encode()

    postings, outcome = pl.fetch([GH, ASHBY], pause=0, log=lambda s: None, get=get)
    assert len(urls) == 2 and outcome == {"greenhouse:acme": "ok", "ashby:beta": "failed: URLError"}
    assert len(postings) == 1


# ------------------------------------------------------------------ the id diff
def test_diff_new_open_closed_and_failed_boards():
    keep, gone, new = posting(GH, 1), posting(GH, 2), posting(GH, 3)
    other = posting(ASHBY, 9)
    retired = {"id": "r" * 24, "company": "Retired Co", "region": "global", "first_seen": "2026-01-01",
               "last_seen": "2026-09-01", "open": True}
    history = {
        keep.id: {"id": keep.id, "company": "Acme", "region": "global", "first_seen": "2026-08-01",
                  "last_seen": "2026-09-15", "open": True, "cls": "backend", "fit": "good", "modules": ["M-swe"],
                  "catalog_version": 3},
        gone.id: {"id": gone.id, "company": "Acme", "region": "global", "first_seen": "2026-08-01",
                  "last_seen": "2026-09-15", "open": True},
        other.id: {"id": other.id, "company": "Beta", "region": "global", "first_seen": "2026-08-01",
                   "last_seen": "2026-09-15", "open": True},
        retired["id"]: retired,
    }
    outcome = {"greenhouse:acme": "ok", "ashby:beta": "HTTP 500"}
    records, counts, deleted = pl.diff([keep, new], history, outcome, [GH, ASHBY], "2026-10-15")
    assert counts == {"open": 1, "new": 1, "closed": 2, "deleted": 0}
    assert records[keep.id]["first_seen"] == "2026-08-01" and records[keep.id]["cls"] == "backend"
    assert records[keep.id]["last_seen"] == "2026-10-15"
    assert records[gone.id]["open"] is False and records[gone.id]["last_seen"] == "2026-09-15"
    assert records[other.id]["open"] is True  # its board failed: nothing of Beta's closes
    assert records[retired["id"]]["open"] is False  # a company no longer listed closes
    assert records[new.id]["first_seen"] == "2026-09-01"  # the board's published date, before today
    assert not pl.needs_mapping(records[keep.id], 3) and pl.needs_mapping(records[new.id], 3)
    assert pl.needs_mapping(records[keep.id], 4)  # a new catalog maps again
    assert deleted == []


def test_diff_deletes_postings_closed_long_ago_and_reopens():
    old = {"id": "o" * 24, "company": "Acme", "region": "global", "first_seen": "2025-01-01",
           "last_seen": "2026-01-01", "open": False}
    back = posting(GH, 5)
    history = {old["id"]: old, back.id: {"id": back.id, "company": "Acme", "region": "global",
                                         "first_seen": "2026-05-01", "last_seen": "2026-06-01", "open": False}}
    records, counts, deleted = pl.diff([back], history, {"greenhouse:acme": "ok"}, [GH], "2026-10-15")
    assert deleted == [old["id"]] and old["id"] not in records
    assert counts["reopened"] == 1 and records[back.id]["open"] and records[back.id]["first_seen"] == "2026-05-01"


# ------------------------------------------------------------------ nothing but derived data is stored
def test_no_title_link_or_description_reaches_a_record(tmp_path):
    job = gh_job(77, TITLE, content=f"&lt;p&gt;{TITLE} secret description&lt;/p&gt;")
    [p] = pl.normalize(GH, {"jobs": [job]})
    rec = pl.record(p, None, "2026-10-15")
    assert tuple(rec) == pl.RECORD_FIELDS
    # a model answer with extra keys adds none of them
    m = pl.mapping_of({"sid": "p1", "best": "backend", "fit": "good", "title": TITLE, "url": job["absolute_url"],
                       "modules": ["M-swe"], "vertical": None, "seniority": "senior"}, {"backend"}, {"M-swe"})
    rec.update(m)
    records = {rec["id"]: rec | {"title": TITLE, "description": "x", "url": job["absolute_url"]}}
    pl.write_jsonl(tmp_path / "postings.jsonl", records)
    stored = (tmp_path / "postings.jsonl").read_text()
    assert TITLE.lower() not in stored.lower() and "greenhouse.io" not in stored and "description" not in stored
    assert set(json.loads(stored)) == set(pl.RECORD_FIELDS)
    writes = pl.market_writes({"as_of": "2026-10-15"}, records, [rec["id"]], [])
    assert TITLE.lower() not in json.dumps(writes).lower()
    assert set(writes[2]["update"]["fields"]) == set(pl.RECORD_FIELDS)
    assert rec["id"] == hashlib.sha256(b"greenhouse:acme:77").hexdigest()[:24]


# ------------------------------------------------------------------ mapping
CATALOG = {"version": 3,
           "tracks": [{"id": "backend", "name": "Backend engineer", "owned": "Own APIs. More."},
                      {"id": "frontend", "name": "Frontend engineer", "owned": "Own browser interfaces."},
                      {"id": "sre", "name": "Site reliability engineer", "owned": "Own reliability."}],
           "modules": [{"id": "M-swe", "name": "Software engineering"}, {"id": "M-sql", "name": "SQL"}]}


class FakeMapper:
    name = "fake"

    def __init__(self, answers, cost=0.01):
        self.answers, self.cost, self.prompts = list(answers), cost, []

    def __call__(self, system, prompt):
        self.prompts.append(prompt)
        sids = re.findall(r'"sid": "(p\d+)"', prompt)
        answer = self.answers.pop(0) if self.answers else "all"
        if answer == "all":
            items = [{"sid": s, "best": "backend", "second": "sre", "fit": "good", "modules": ["M-swe", "M-x"],
                      "vertical": "Financial Services", "seniority": "senior"} for s in sids]
        elif answer == "skip-last":
            items = [{"sid": s, "best": "backend", "fit": "partial", "modules": []} for s in sids[:-1]]
        else:
            raise pl.MapError(answer)
        return pl.CallResult("```json\n" + json.dumps(items) + "\n```", self.cost, "claude-haiku-5-5")


def test_mapping_validates_and_retries_only_what_is_missing():
    ps = [posting(GH, i) for i in range(5)]
    mapper = FakeMapper(["skip-last", "all"])
    got, stats = pl.map_postings(ps, CATALOG, mapper, batch=5, log=lambda s: None)
    assert len(got) == 5 and stats.calls == 2 and stats.failed == 0
    assert mapper.prompts[1].count('"sid"') == 1  # the retry asks for the one left out
    m = got[ps[0].id]
    assert m["modules"] == [] and m["fit"] == "partial" and m["mapped_by"] == "fake:claude-haiku-5-5"
    full = got[ps[4].id]
    assert full["modules"] == ["M-swe"] and full["industry"] == "financial services" and full["cls2"] == "sre"
    assert full["catalog_version"] == 3


def test_mapping_failures_stay_unmapped_and_budget_stops():
    ps = [posting(GH, i) for i in range(6)]
    got, stats = pl.map_postings(ps, CATALOG, FakeMapper(["boom", "boom"]), batch=6, log=lambda s: None)
    assert got == {} and stats.failed == 6 and stats.calls == 2
    got, stats = pl.map_postings(ps, CATALOG, FakeMapper([]), batch=2, max_calls=2, log=lambda s: None)
    assert len(got) == 4 and stats.calls == 2 and stats.stopped == "2 calls"
    # the first calls project past the budget: stop early
    got, stats = pl.map_postings(ps, CATALOG, FakeMapper([], cost=1.0), batch=1, max_cost=3.0, log=lambda s: None)
    assert stats.calls == 2 and stats.stopped.startswith("projected")


def test_api_mapper_reads_the_stream():
    events = [{"type": "message_start", "message": {"usage": {"input_tokens": 1000, "cache_read_input_tokens": 0}}},
              {"type": "content_block_delta", "delta": {"type": "text_delta", "text": '[{"sid": "p1", '}},
              {"type": "content_block_delta", "delta": {"type": "text_delta", "text": '"best": "sre"}]'}},
              {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 200}}]
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
    seen = {}

    def opener(req, timeout):
        seen["headers"] = dict(req.header_items())
        return io.BytesIO(body)

    res = pl.ApiMapper(key="k", opener=opener)("system", "prompt")
    assert res.text == '[{"sid": "p1", "best": "sre"}]' and res.cost == pytest.approx((1000 * 0.1 + 200 * 0.5) / 1e6)
    assert seen["headers"]["X-api-key"] == "k"


# ------------------------------------------------------------------ the aggregate
def _rec(i, company, cls, fit="good", region="global", first_seen="2026-10-01", is_open=True, modules=("M-swe",)):
    return {"id": f"{i:024d}", "company": company, "region": region, "first_seen": first_seen,
            "last_seen": "2026-10-15", "open": is_open, "cls": cls, "cls2": None, "fit": fit, "modules": list(modules),
            "industry": None, "seniority": "mid", "mapped_by": "fake", "catalog_version": 3}


def test_aggregate_weights_boards_by_the_square_root_of_their_size():
    recs = [_rec(i, "Big", "backend") for i in range(4)] + [_rec(10, "Small", "frontend"),
                                                            _rec(11, "Jp", "sre", region="japan"),
                                                            _rec(12, "Jp", "backend", fit="none", region="japan"),
                                                            _rec(13, "Big", "frontend", is_open=False)]
    m = pl.aggregate({r["id"]: r for r in recs}, CATALOG, "2026-10-15")
    c = m["classes"]
    # Big's 4 postings weigh 1/2 each (2 in all), Small's one weighs 1: backend 2/3, frontend 1/3
    assert c["backend"]["global"] == 4 and c["backend"]["share_global"] == pytest.approx(2 / 3, abs=1e-4)
    assert c["frontend"]["global"] == 1 and c["frontend"]["share_global"] == pytest.approx(1 / 3, abs=1e-4)
    assert c["backend"]["japan"] == 0  # fit none doesn't count...
    assert m["unmatched"] == {"global": 0.0, "japan": pytest.approx(0.5)}  # ...it is unmatched
    assert c["sre"]["share_japan"] == pytest.approx(0.5)
    assert c["frontend"]["new_global"] == 2  # the closed one was new in the window too
    assert m["sample"] == {"boards": 3, "global": 5, "japan": 2}
    assert c["backend"]["trend_global"] is None and c["backend"]["top_modules"] == ["M-swe"]
    assert set(c) == {"backend", "frontend", "sre"}  # every class, zeros included


def test_aggregate_trend_once_the_history_covers_two_windows():
    recs = [_rec(i, "A", "backend", first_seen="2026-09-01") for i in range(3)]
    recs += [_rec(9, "A", "backend", first_seen="2026-06-01", is_open=False)]
    m = pl.aggregate({r["id"]: r for r in recs}, CATALOG, "2026-10-15", history_since="2026-04-01")
    assert m["classes"]["backend"]["trend_global"] == 3.0 and m["classes"]["frontend"]["trend_global"] is None
    m = pl.aggregate({r["id"]: r for r in recs}, CATALOG, "2026-10-15", history_since="2026-08-01")
    assert m["classes"]["backend"]["trend_global"] is None


def test_bundled_market_matches_the_catalog():
    market = json.loads((ROOT / "src" / "interlatch" / "knowledge" / "market.json").read_text(encoding="utf-8"))
    catalog = json.loads(pl.CATALOG_PATH.read_text(encoding="utf-8"))
    assert market["catalog_version"] == catalog["version"] and market["status"] in ("test", "live")
    assert set(market["classes"]) == {t["id"] for t in catalog["tracks"]}
    assert set(market["sample"]) == {"boards", "global", "japan"} and set(market["unmatched"]) == set(pl.REGIONS)


# ------------------------------------------------------------------ Firestore
def test_firestore_values_round_trip():
    doc = {"s": "x", "i": 3, "f": 0.25, "b": False, "n": None, "l": ["a", 1], "e": [], "m": {"k": {"z": 1}}}
    enc = pl.encode(doc)
    assert enc["mapValue"]["fields"]["i"] == {"integerValue": "3"}
    assert enc["mapValue"]["fields"]["n"] == {"nullValue": None}
    assert enc["mapValue"]["fields"]["e"] == {"arrayValue": {}}
    assert pl.decode(enc) == doc


def test_writes_go_in_commits_of_at_most_400():
    records = {f"{i:024d}": _rec(i, "A", "backend") for i in range(850)}
    market = pl.aggregate(records, CATALOG, "2026-10-15")
    writes = pl.market_writes(market, records, list(records), ["d" * 24])
    names = [w.get("delete") or w["update"]["name"] for w in writes]
    assert names[0].endswith("/market/current") and names[1].endswith("/market/2026-10")
    assert len(writes) == 853 and names[-1].endswith("/careers_postings/" + "d" * 24)
    sent = []
    n = pl.commit(writes, "token", post=lambda url, body, tok: sent.append(len(body["writes"])) or {})
    assert n == 3 and sent == [400, 400, 53]
    assert pl.describe_writes(writes).startswith("853 writes in 3 commit(s)")


def test_history_from_firestore_pages():
    rec = _rec(1, "A", "backend")
    pages = [{"documents": [{"fields": pl.encode(rec)["mapValue"]["fields"]}], "nextPageToken": "t2"},
             {"documents": [{"fields": pl.encode(_rec(2, "A", "sre"))["mapValue"]["fields"]}]}]
    urls = []
    got = pl.firestore_history("tok", get=lambda url, tok: urls.append(url) or pages[len(urls) - 1])
    assert set(got) == {rec["id"], _rec(2, "A", "sre")["id"]} and got[rec["id"]] == rec
    assert "pageToken=t2" in urls[1]


def test_dry_run_sends_nothing_and_writes_local_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pl, "fetch", lambda boards: ([posting(GH, 1)], {"greenhouse:acme": "ok"}))
    monkeypatch.setattr(pl, "http", lambda *a, **k: pytest.fail("no network in a dry run"))
    boards = tmp_path / "boards.json"
    boards.write_text(json.dumps({"boards": [{"provider": "greenhouse", "token": "acme", "company": "Acme"}]}))
    catalog = tmp_path / "roadmap.json"
    catalog.write_text(json.dumps(CATALOG))
    out = tmp_path / "out"
    assert pl.main(["--boards", str(boards), "--catalog", str(catalog), "--out", str(out), "--mapper", "none",
                    "--firestore", "--dry-run", "--as-of", "2026-10-15", "--allow-partial"]) == 0
    printed = capsys.readouterr().out
    assert "Dry run, Firestore: 3 writes in 1 commit(s)" in printed
    assert json.loads((out / "market.json").read_text())["sample"]["global"] == 0  # nothing mapped yet
    assert len((out / "postings.jsonl").read_text().splitlines()) == 1


# ------------------------------------------------------------------ the first publish's bug: an empty Firestore history
def _files(tmp_path):
    boards = tmp_path / "boards.json"
    boards.write_text(json.dumps({"boards": [{"provider": "greenhouse", "token": "acme", "company": "Acme"}]}))
    catalog = tmp_path / "roadmap.json"
    catalog.write_text(json.dumps(CATALOG))
    return ["--boards", str(boards), "--catalog", str(catalog), "--as-of", "2026-10-15"]


def _mapped(p, **kw):
    return {"id": p.id, "company": "Acme", "region": "global", "first_seen": "2026-09-01", "last_seen": "2026-10-14",
            "open": True, "cls": "backend", "cls2": None, "fit": "good", "modules": ["M-swe"], "industry": None,
            "seniority": "mid", "mapped_by": "fake", "catalog_version": 3, **kw}


def test_merge_prefers_the_record_mapped_for_this_catalog():
    a, b, c = posting(GH, 1), posting(GH, 2), posting(GH, 3)
    remote = {a.id: _mapped(a, fit=None, cls=None, catalog_version=None, last_seen="2026-10-15"),
              b.id: _mapped(b, catalog_version=2)}
    local = {a.id: _mapped(a), b.id: _mapped(b, cls="sre"), c.id: _mapped(c)}
    merged, from_local = pl.merge_history(remote, local, 3)
    assert from_local == 3 and merged[a.id]["fit"] == "good" and merged[b.id]["cls"] == "sre" and c.id in merged
    merged, from_local = pl.merge_history({a.id: _mapped(a, last_seen="2026-10-15")}, {a.id: _mapped(a)}, 3)
    assert from_local == 0 and merged[a.id]["last_seen"] == "2026-10-15"


def test_publishing_with_an_empty_firestore_uses_the_local_history(tmp_path, monkeypatch, capsys):
    ps = [posting(GH, i) for i in range(3)]
    out = tmp_path / "out"
    out.mkdir()
    pl.write_jsonl(out / "postings.jsonl", {p.id: _mapped(p) for p in ps})
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"refresh_token": "r"}))
    sent = []
    monkeypatch.setattr(pl, "fetch", lambda boards: (ps, {"greenhouse:acme": "ok"}))
    monkeypatch.setattr(pl, "api_key", lambda: "key")
    monkeypatch.setattr(pl, "id_token", lambda path, key: "tok")
    monkeypatch.setattr(pl, "firestore_history", lambda tok: {})
    monkeypatch.setattr(pl, "firestore_market", lambda: None)
    monkeypatch.setattr(pl, "commit", lambda writes, tok: sent.append(len(writes)) or 1)
    monkeypatch.setattr(pl, "ClaudeMapper", lambda **kw: pytest.fail("nothing needs mapping"))
    monkeypatch.setattr(pl, "BUNDLED_PATH", tmp_path / "bundled.json")
    assert pl.main(_files(tmp_path) + ["--out", str(out), "--firestore", "--auth", str(auth), "--bundle"]) == 0
    assert "0 records in Firestore, 3 in" in capsys.readouterr().out
    assert sent == [5] and json.loads((tmp_path / "bundled.json").read_text())["classes"]["backend"]["global"] == 3


def test_a_market_left_mostly_unmapped_is_neither_bundled_nor_published(tmp_path, monkeypatch, capsys):
    ps = [posting(GH, i) for i in range(20)]
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "fetch", lambda boards: (ps, {"greenhouse:acme": "ok"}))
    monkeypatch.setattr(pl, "ClaudeMapper", lambda **kw: FakeMapper([]))
    monkeypatch.setattr(pl, "BUNDLED_PATH", tmp_path / "bundled.json")
    monkeypatch.setattr(pl, "commit", lambda *a, **k: pytest.fail("nothing is published"))
    args = _files(tmp_path) + ["--out", str(out), "--bundle", "--batch", "5", "--max-calls", "1"]
    assert pl.main(args) == 2
    printed = capsys.readouterr().out
    assert "75% of the open postings are unmapped" in printed and "nothing bundled or published" in printed
    assert not (tmp_path / "bundled.json").exists() and not (out / "market.json").exists()
    assert (out / "market.partial.json").exists()
    kept = pl.read_jsonl(out / "postings.jsonl")  # what it paid for is kept for the next run
    assert sum(r["fit"] == "good" for r in kept.values()) == 5
    assert pl.main(args + ["--allow-partial"]) == 0 and (tmp_path / "bundled.json").exists()


def test_the_history_file_never_loses_mappings(tmp_path):
    a, b = posting(GH, 1), posting(GH, 2)
    path = tmp_path / "postings.jsonl"
    pl.save_history(path, {a.id: _mapped(a), b.id: _mapped(b)})
    worse = {a.id: _mapped(a, fit=None, cls=None, catalog_version=None), b.id: _mapped(b)}
    with pytest.raises(pl.HistoryLossError):
        pl.save_history(path, worse)
    assert pl.read_jsonl(path)[a.id]["fit"] == "good" and (tmp_path / "postings.refused.jsonl").exists()
    with pytest.raises(pl.HistoryLossError):
        pl.save_history(path, {b.id: _mapped(b)})  # vanished...
    pl.save_history(path, {b.id: _mapped(b)}, gone=[a.id])  # ...unless deleted on purpose
    assert set(pl.read_jsonl(path)) == {b.id} and set(pl.read_jsonl(tmp_path / "postings.prev.jsonl")) == {a.id, b.id}


def _token(uid: str) -> str:
    claims = pl.base64.urlsafe_b64encode(json.dumps({"user_id": uid}).encode()).decode().rstrip("=")
    return f"h.{claims}.s"


@pytest.mark.parametrize("uid,ok", [(pl.OPERATOR_UID, True), ("someone-else", False)])
def test_publishing_needs_the_operators_sign_in_and_never_shows_it(tmp_path, monkeypatch, capsys, uid, ok):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"refresh_token": "REFRESH-SECRET"}))
    monkeypatch.setattr(pl, "http", lambda method, url, **kw: json.dumps({"id_token": _token(uid)}).encode())
    if ok:
        assert pl.id_token(auth, "key") == _token(uid)
    else:
        with pytest.raises(SystemExit) as e:
            pl.id_token(auth, "key")
        assert "operator" in str(e.value)
    out = capsys.readouterr()
    assert "REFRESH-SECRET" not in out.out + out.err


def test_firestore_without_auth_needs_a_dry_run(capsys):
    with pytest.raises(SystemExit):
        pl.main(["--firestore", "--mapper", "none"])
    assert "--auth" in capsys.readouterr().err


# ------------------------------------------------------------------ the rules
RULES = ROOT / "leaderboard" / "firestore.rules"
# the players block as the leaderboard shipped it: the market's rules must leave it byte for byte
PLAYERS_SHA = "21613fed20217fa7c1d2e918f757965813c2017a5317abf6f44b90f798f2d3a5"


def _block(text: str, head: str) -> str:
    """The match block that `head` (ending in its opening brace) opens, through its closing brace."""
    start = text.index(head)
    depth = 1
    for i in range(start + len(head), len(text)):
        depth += text[i] == "{"
        depth -= text[i] == "}"
        if depth == 0:
            return text[start:i + 1]
    raise AssertionError(f"{head} isn't closed")


def test_rules_keep_the_leaderboard_and_add_the_market():
    text = RULES.read_text(encoding="utf-8")
    players = _block(text, "match /players/{userId} {")
    assert hashlib.sha256(players.encode()).hexdigest() == PLAYERS_SHA
    assert text.rstrip().endswith("match /{document=**} {\n      allow read, write: if false;\n    }\n  }\n}")
    market = _block(text, "match /market/{doc} {")
    postings = _block(text, "match /careers_postings/{id} {")
    assert "allow get, list: if true;" in market and "isMarketOperator()" in market
    assert "allow read" in postings and "isMarketOperator()" in postings and "if true" not in postings
    assert f"'{pl.OPERATOR_UID}'" in text
    assert text.count("{") == text.count("}")
