"""The Party's roadmap catalog: shape, counts, references, regexes, Japanese text and no personal fields."""

import json
import re
from pathlib import Path

import pytest

import interlatch.knowledge

PATH = Path(interlatch.knowledge.__file__).parent / "roadmap.json"
ATTRS = {"craft", "data", "ops", "guard", "arcana", "lore"}
CATEGORIES = ["ai", "security", "infra", "software", "data", "enterprise", "quality", "systems", "devprod",
              "delivery", "architecture", "support"]
PERSONAL = ("Adrian", "Your connection", "Gap to check", "Your possible bridge", "For you",
            "Your starting hypothesis")


@pytest.fixture(scope="module")
def raw():
    return PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cat(raw):
    return json.loads(raw)


def _ids(items):
    return [x["id"] for x in items]


def test_top_level_shape(cat):
    assert list(cat) == ["version", "title", "title_ja", "attrs", "categories", "modules", "tracks", "capstones",
                         "routes", "phases"]
    assert cat["version"] == 3
    assert cat["title"] and cat["title_ja"]


def test_counts(cat):
    assert len(cat["attrs"]) == 6
    assert len(cat["categories"]) == 12
    assert len(cat["modules"]) == 70
    assert sum(1 for m in cat["modules"] if m["part"] == 1) == 38
    assert sum(1 for m in cat["modules"] if m["part"] == 2) == 32
    assert len(cat["tracks"]) == 65
    assert sum(len(t["milestones"]) for t in cat["tracks"]) == 260
    assert len(cat["capstones"]) == 13
    assert len(cat["routes"]) == 6
    assert len(cat["phases"]) == 6


@pytest.mark.parametrize("kind", ["attrs", "categories", "modules", "tracks", "capstones", "routes", "phases"])
def test_ids_unique_per_kind(cat, kind):
    ids = _ids(cat[kind])
    assert len(ids) == len(set(ids))


def test_fixed_ids(cat):
    assert set(_ids(cat["attrs"])) == ATTRS
    assert _ids(cat["categories"]) == CATEGORIES
    assert _ids(cat["phases"]) == ["P0", "P1", "P2", "P3", "P4", "P5"]
    for m in cat["modules"]:
        assert m["id"].startswith("M-g-") == (m["part"] == 2), m["id"]


def test_every_module_has_an_attr_and_valid_priors(cat):
    ids = set(_ids(cat["modules"]))
    for m in cat["modules"]:
        assert m["attr"] in ATTRS, m["id"]
        assert m["id"] not in m["prior"], m["id"]
        assert set(m["prior"]) <= ids, m["id"]
        assert m["study"] and all(m["study"]), m["id"]
        assert m["practice"] and m["gate"], m["id"]


def test_every_attr_is_used(cat):
    assert {m["attr"] for m in cat["modules"]} == ATTRS


def test_tracks_reference_existing_modules(cat):
    ids = set(_ids(cat["modules"]))
    cats = set(CATEGORIES)
    for t in cat["tracks"]:
        assert t["category"] in cats, t["id"]
        assert t["modules"] and set(t["modules"]) <= ids, t["id"]
        assert len(t["modules"]) == len(set(t["modules"])), t["id"]
        assert t["critical"] and set(t["critical"]) <= set(t["modules"]), t["id"]
        assert set(t["targets"]) <= set(t["modules"]), t["id"]
        assert all(isinstance(v, int) and 1 <= v <= 5 for v in t["targets"].values()), t["id"]
        assert t["owned"] and t["interview"] and t["evidence"] and t["family"], t["id"]


def test_part_one_tracks_are_ai_with_targets(cat):
    part1 = {m["id"] for m in cat["modules"] if m["part"] == 1}
    ai = [t for t in cat["tracks"] if t["category"] == "ai"]
    assert len(ai) == 25
    for t in ai:
        assert t["targets"], t["id"]
        assert set(t["critical"]) <= set(t["targets"]), t["id"]
        assert set(t["modules"]) <= part1, t["id"]
    for t in cat["tracks"]:
        if t["category"] != "ai":
            assert t["targets"] == {}, t["id"]


def test_every_category_has_tracks(cat):
    used = {t["category"] for t in cat["tracks"]}
    assert used == set(CATEGORIES)


def test_milestone_ids(cat):
    for t in cat["tracks"]:
        assert [m["id"] for m in t["milestones"]] == [f"{t['id']}-G{n}" for n in range(1, 5)]
        for m in t["milestones"]:
            assert m["text"], m["id"]


def test_capstones_reference_existing_tracks(cat):
    tracks = set(_ids(cat["tracks"]))
    caps = set(_ids(cat["capstones"]))
    for c in cat["capstones"]:
        assert c["tracks"] and set(c["tracks"]) <= tracks, c["id"]
        assert c["question"], c["id"]
    for t in cat["tracks"]:
        assert t["capstones"] and set(t["capstones"]) <= caps, t["id"]
        for cid in t["capstones"]:
            assert t["id"] in next(c for c in cat["capstones"] if c["id"] == cid)["tracks"], (t["id"], cid)


def _patterns(cat):
    for m in cat["modules"]:
        yield m["id"], m["keywords"]
    for t in cat["tracks"]:
        for m in t["milestones"]:
            yield m["id"], m["keywords"]


def test_keywords_compile_and_never_match_everything(cat):
    pats = list(_patterns(cat))
    assert len(pats) == 70 + 260
    for where, p in pats:
        assert isinstance(p, str) and p, where
        rx = re.compile(p, re.I)
        assert not rx.search(""), where
        assert not rx.search(" "), where
        assert "||" not in p and not p.startswith("|") and not p.endswith("|"), where
        assert not re.search(r"\(\?:\||\|\)", p), where  # no empty alternative inside a group


def test_keywords_find_what_they_name(cat):
    by_id = dict(_patterns(cat))

    def hits(mid, text):
        return re.search(by_id[mid], text, re.I) is not None

    assert hits("M-sql", "SQLite schema migration skipped a column")
    assert hits("M-g-appsec", "The CSP blocks inline scripts; XSS in innerHTML")
    assert hits("M-g-cicd", "Release workflow fails when PyPI trusted publishing breaks")
    assert hits("M-devtools", "The MCP server and Claude Code hooks")
    assert hits("M-retrieval", "FTS5 trigram search for Japanese")
    assert hits("M-frontend", "Dark mode CSS for the theme toggle")
    assert hits("M-g-cloudsec", "Firestore rules let anyone write")
    assert hits("M-g-containers", "docker compose bind mount")
    assert hits("M-multimodal", "見積書のOCR")
    assert not hits("M-eval", "pywebview needs 'unsafe-eval' in the CSP")
    assert not hits("M-g-storage", "ESP32-S3 node")
    assert hits("devtools-G4", "Ship releases and migrations with backward compatibility")


def test_japanese_fields_present(cat):
    for kind in ("attrs", "categories", "modules", "tracks", "capstones", "routes", "phases"):
        for x in cat[kind]:
            assert isinstance(x.get("name_ja"), str) and x["name_ja"].strip(), (kind, x["id"])
    for t in cat["tracks"]:
        for m in t["milestones"]:
            assert isinstance(m.get("text_ja"), str) and m["text_ja"].strip(), m["id"]
    for a in cat["attrs"]:
        assert a["name"] and a["about"] and a["about_ja"], a["id"]


def test_no_personal_text(raw):
    for bad in PERSONAL:
        assert bad not in raw, bad
    assert "Planning range" not in raw and "Work reality" not in raw
    assert "](http" not in raw  # markdown links were stripped
    assert not re.search(r"\[S\d+\]", raw)  # source tags were stripped
    assert "**" not in raw


def test_file_format(raw):
    assert raw.endswith("\n")
    assert raw == json.dumps(json.loads(raw), ensure_ascii=False, indent=2) + "\n"
