"""Knowledge maturity: items climb from provisional to established to canonical as sessions confirm them."""

import json

import pytest

from interlatch import ladder
from interlatch.analyze import analyze_session
from interlatch.db import connect, init_schema
from interlatch.reviews import generate_review
from interlatch.search import search_knowledge
from interlatch.synthesize import normalize_kb, render_kb_markdown, synthesize_project

from conftest import CWD, SID


def _session(conn, sid, started):
    conn.execute("INSERT INTO sessions(id, project_path, project_name, started_at, ended_at, source, agent, analysis_status) "
                 "VALUES (?, ?, 'demo-app', ?, ?, 'claude', 'claude', 'done')", (sid, CWD, started, started))


def _item(conn, sid, title, *, confidence="high", source="analysis", pinned=0):
    cur = conn.execute(
        "INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, confidence, source, "
        "fingerprint, pinned, created_at, updated_at, stage) VALUES (?, ?, 'demo-app', 'gotcha', ?, 'b', 'project', ?, ?, ?, ?, "
        "'2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', ?)",
        (sid, CWD, title, confidence, source, f"fp-{sid}-{title}", pinned,
         ladder.initial_stage({"confidence": confidence}, source=source)))
    ladder.refresh(conn, [cur.lastrowid])
    return cur.lastrowid


def _row(conn, kid):
    return conn.execute("SELECT * FROM knowledge WHERE id = ?", (kid,)).fetchone()


def test_new_items_start_by_what_they_are(synced):
    conn = synced["conn"]
    _session(conn, "s1", "2026-09-01T10:00:00Z")
    assert _row(conn, _item(conn, "s1", "a"))["stage"] == "provisional"
    assert _row(conn, _item(conn, "s1", "b", confidence="low"))["stage"] == "wip"
    assert _row(conn, _item(conn, None, "c", source="memory"))["stage"] == "established"
    pinned = _row(conn, _item(conn, "s1", "d", pinned=1))
    assert (pinned["stage"], pinned["stage_reason"]) == ("canonical", "pinned by you")


def test_duplicates_pool_sessions_and_climb_the_ladder(synced):
    conn = synced["conn"]
    for sid, day in (("s1", "01"), ("s2", "05"), ("s3", "20")):
        _session(conn, sid, f"2026-09-{day}T10:00:00Z")
    a, b, c = (_item(conn, sid, "Stripe needs the raw body") for sid in ("s1", "s2", "s3"))

    retired = ladder.apply_synthesis(conn, [{"id": b, "by": a, "reason": "duplicate"}], {a, b, c}, retire=True)
    assert retired == [b]
    survivor, dup = _row(conn, a), _row(conn, b)
    assert survivor["stage"] == "established" and "2 sessions" in survivor["stage_reason"]
    assert (dup["status"], dup["superseded_by"], dup["superseded_reason"]) == ("superseded", a, "duplicate")

    ladder.apply_synthesis(conn, [{"id": c, "by": a, "reason": "duplicate"}], {a, c}, retire=True)
    survivor = _row(conn, a)
    assert survivor["stage"] == "canonical"  # three sessions spread over 19 days
    assert survivor["stage_reason"] == "confirmed in 3 sessions over 19 days (2026-09-01 → 2026-09-20)"
    assert ladder.stage_label(survivor) == "canonical ×3"


def test_three_sessions_in_one_week_stay_established(synced):
    conn = synced["conn"]
    for sid, day in (("s1", "01"), ("s2", "02"), ("s3", "03")):
        _session(conn, sid, f"2026-09-{day}T10:00:00Z")
    a, b, c = (_item(conn, sid, "x") for sid in ("s1", "s2", "s3"))
    ladder.apply_synthesis(conn, [{"id": b, "by": a, "reason": "duplicate"}, {"id": c, "by": a, "reason": "duplicate"}],
                           {a, b, c}, retire=True)
    assert _row(conn, a)["stage"] == "established"


def test_the_playbook_pools_evidence_without_retiring(synced):
    conn = synced["conn"]
    _session(conn, "s1", "2026-09-01T10:00:00Z")
    _session(conn, "s2", "2026-09-02T10:00:00Z")
    a, b = _item(conn, "s1", "x"), _item(conn, "s2", "x")
    assert ladder.apply_synthesis(conn, [{"id": b, "by": a, "reason": "duplicate"}], {a, b}, retire=False) == []
    assert [(_row(conn, i)["status"], _row(conn, i)["stage"]) for i in (a, b)] == [("active", "established")] * 2


def test_outdated_names_its_successor_and_pins_are_kept(synced):
    conn = synced["conn"]
    _session(conn, "s1", "2026-09-01T10:00:00Z")
    old, new, pinned = _item(conn, "s1", "old"), _item(conn, "s1", "new"), _item(conn, "s1", "p", pinned=1)
    retired = ladder.apply_synthesis(conn, [{"id": old, "by": new, "reason": "contradicted"},
                                            {"id": pinned, "by": None, "reason": "outdated"},
                                            {"id": 999, "by": new, "reason": "outdated"}], {old, new, pinned}, retire=True)
    assert retired == [old]
    assert (_row(conn, old)["superseded_by"], _row(conn, old)["superseded_reason"]) == (new, "contradicted")
    assert _row(conn, pinned)["status"] == "active"


def test_reanalysis_keeps_earned_evidence_and_restores_merged_items(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    analyze_session(conn, cfg, SID)
    mine = conn.execute("SELECT * FROM knowledge WHERE session_id = ? AND kind = 'fix'", (SID,)).fetchone()
    _session(conn, "s2", "2026-09-25T10:00:00Z")
    other = _item(conn, "s2", "Token TTL compared in seconds vs ms")
    ladder.apply_synthesis(conn, [{"id": other, "by": mine["id"], "reason": "duplicate"}], {mine["id"], other}, retire=True)
    assert _row(conn, mine["id"])["stage"] == "established"

    analyze_session(conn, cfg, SID)  # the session continued: its items are replaced
    again = conn.execute("SELECT * FROM knowledge WHERE session_id = ? AND kind = 'fix'", (SID,)).fetchone()
    assert again["id"] != mine["id"] and again["stage"] == "established"  # same lesson, evidence carried over
    assert _row(conn, other)["status"] == "active"  # its successor was replaced, so it is back on its own


def test_synthesis_end_to_end_promotes_tags_and_renders(synced, monkeypatch):
    conn, cfg = synced["conn"], synced["cfg"]
    analyze_session(conn, cfg, SID)
    fix = conn.execute("SELECT id FROM knowledge WHERE session_id = ? AND kind = 'fix'", (SID,)).fetchone()[0]
    _session(conn, "s2", "2026-09-25T10:00:00Z")
    dup = _item(conn, "s2", "TTL seconds vs milliseconds")
    conn.commit()
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "superseded-json")
    monkeypatch.setenv("FAKE_SUPERSEDED", json.dumps([{"id": dup, "by": fix, "reason": "duplicate"}, {"id": "junk"}]))
    monkeypatch.setenv("FAKE_SOURCES", json.dumps([fix]))
    data = synthesize_project(conn, cfg, CWD)
    assert data["superseded"] == [{"id": dup, "by": fix, "reason": "duplicate"}]
    bullet = data["sections"][0]["items"][0]
    assert (bullet["stage"], bullet["sessions"]) == ("established", 2)
    md = conn.execute("SELECT markdown FROM project_kb WHERE project_path = ?", (CWD,)).fetchone()[0]
    assert "established ×2" in md
    prompt = [json.loads(line) for line in (synced["tmp"] / "fake_claude.log").read_text().splitlines()][-1]["prompt_head"]
    assert '"stage": "provisional"' in prompt  # the model sees each item's stage

    hits = search_knowledge(conn, None, project=CWD)
    ranks = [ladder.STAGE_RANK[h["stage"]] for h in hits]
    assert ranks == sorted(ranks, reverse=True)  # trusted knowledge first
    assert next(h for h in hits if h["id"] == fix)["confirmations"] == 2


def test_legacy_superseded_ids_still_parse():
    data = normalize_kb({"overview": "o", "sections": [], "superseded_ids": [3, "4", "x"],
                         "superseded": [{"id": 5, "by": 3, "reason": "duplicate"}, {"id": 3, "by": None, "reason": "weird"}]})
    assert data["superseded"] == [{"id": 5, "by": 3, "reason": "duplicate"}, {"id": 3, "by": None, "reason": "outdated"},
                                  {"id": 4, "by": None, "reason": "outdated"}]
    assert data["superseded_ids"] == [5, 3, 4]
    md = render_kb_markdown("p", {"sections": [{"title": "T", "items": [
        {"text": "a", "sources": [1], "stage": "canonical", "sessions": 3},
        {"text": "b", "sources": [2], "stage": "provisional", "sessions": 1}]}]}, n_items=2, model=None)
    assert "- a <sub>[k1 · canonical ×3]</sub>" in md and "- b <sub>[k2]</sub>" in md


def test_overturned_trusted_knowledge_appears_in_the_review(synced, monkeypatch):
    conn, cfg = synced["conn"], synced["cfg"]
    analyze_session(conn, cfg, SID)
    _session(conn, "s2", "2026-09-21T10:00:00Z")
    old = _item(conn, "s2", "Deploy with make ship", pinned=1)
    conn.execute("UPDATE knowledge SET pinned = 0, stage = 'established' WHERE id = ?", (old,))
    new = _item(conn, "s2", "Deploy with the release workflow")
    merged = _item(conn, "s2", "merged duplicate")
    conn.execute("UPDATE knowledge SET stage = 'established' WHERE id = ?", (merged,))
    ladder.apply_synthesis(conn, [{"id": old, "by": new, "reason": "outdated"}, {"id": merged, "by": new, "reason": "duplicate"}],
                           {old, new, merged}, retire=True)
    conn.execute("UPDATE knowledge SET superseded_at = '2026-09-22T00:00:00Z' WHERE status = 'superseded'")
    conn.commit()
    data = generate_review(conn, cfg, "2026-W39")
    assert data["overturned"] == ["Deploy with make ship (demo-app): was established, outdated; now “Deploy with the release workflow”"]
    md = conn.execute("SELECT markdown FROM reviews WHERE period = '2026-W39'").fetchone()[0]
    assert "## Overturned" in md


@pytest.mark.parametrize("version", [6, 7])  # 7: databases from 0.5.x, which used 7 for the hub columns
def test_migration_derives_stages_for_existing_items(tmp_path, version):
    path = tmp_path / "old.db"
    conn = connect(path)
    conn.execute("INSERT INTO knowledge(kind, title, confidence, source, pinned, fingerprint) VALUES "
                 "('fix', 'a', 'low', 'analysis', 0, 'f1'), ('fix', 'b', 'high', 'memory', 0, 'f2'), ('fix', 'c', 'high', 'analysis', 1, 'f3')")
    conn.execute("UPDATE knowledge SET stage = 'provisional'")
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    init_schema(conn)
    assert [r[0] for r in conn.execute("SELECT stage FROM knowledge ORDER BY fingerprint")] == ["wip", "established", "canonical"]
    conn.close()
