"""Lesson material survives analysis, additive upgrades and project access without invented material."""

import json
import sqlite3

from interlatch import access
from interlatch.analysis.session import case_of, normalize_analysis, store_analysis
from interlatch.cases import visual_of
from interlatch.storage.sqlite import connect
from interlatch.search import search_knowledge
from interlatch.server import App

from conftest import CWD, SECRET, SID


def material(**extra):
    return {"kind": "fix", "title": "The worker pushed stale configuration", "body": "Reload changed config before push.",
            "scene": "A folder moved after a background job finished.", "question": "When was the config read?",
            "answer": "The worker held an older snapshot.", "clues": ["The config changed during the job."],
            "principle": "Check snapshot freshness before replacing full state.",
            "checklist": ["Compare config-read and push times."], "ruled_out": [], **extra}


def test_case_uses_real_fields_and_handles_legacy_and_malformed_material():
    assert case_of({"kind": "fix", "body": "A fix without a scene"}) is None
    assert case_of(material(kind="command")) is None
    old = {"scene": "A copy hangs.", "question": "What does cp resolve to?", "answer": "An interactive alias.", "ruled_out": []}
    case = case_of({"kind": "gotcha", "case": old})
    assert case["scene"] == old["scene"] and "clues" not in case and "principle" not in case
    # no case file without a real scene, but the principle and checks the analysis found are kept
    case = case_of(material(scene={"bad": "type"}))
    assert "scene" not in case and "question" not in case and case["principle"] == material()["principle"]
    assert case_of({"kind": "learning", "title": "Why WAL", "principle": "Readers never block the writer."}) == {
        "principle": "Readers never block the writer."}
    assert case_of(material(kind="learning"))["checklist"] == material()["checklist"]
    assert "scene" not in case_of(material(kind="learning"))  # case files are for fixes, gotchas and decisions
    case = case_of(material(clues=[None, "observed", {"invented": "fact"}], checklist="not a list",
                            ruled_out=[None, {"lead": "Bad remote mapping", "why": "The mapping was correct."}]))
    assert case["clues"] == ["observed"] and "checklist" not in case
    assert case["ruled_out"] == [{"lead": "Bad remote mapping", "why": "The mapping was correct."}]


def test_every_case_field_is_redacted():
    case = case_of(material(scene=SECRET, question=SECRET, answer=SECRET, clues=[SECRET], principle=SECRET,
                            checklist=[SECRET], ruled_out=[{"lead": SECRET, "why": SECRET}]))
    assert SECRET not in json.dumps(case)
    assert "REDACTED" in json.dumps(case)


def test_analysis_preserves_cases_and_enriches_a_pinned_legacy_lesson(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    data = normalize_analysis({"title": "Folder sharing", "knowledge": [material()]})
    store_analysis(conn, cfg, SID, data, "fake-model", 2)
    row = conn.execute("SELECT id, case_json FROM knowledge WHERE session_id=?", (SID,)).fetchone()
    assert json.loads(row["case_json"])["checklist"] == ["Compare config-read and push times."]
    conn.execute("UPDATE knowledge SET pinned=1, case_json=NULL, body='Kept by the user' WHERE id=?", (row["id"],))
    store_analysis(conn, cfg, SID, data, "fake-model", 2)
    row = conn.execute("SELECT body, pinned, case_json FROM knowledge WHERE id=?", (row["id"],)).fetchone()
    assert row["body"] == "Kept by the user" and row["pinned"] == 1
    assert json.loads(row["case_json"])["scene"] == material()["scene"]


def test_schema_upgrade_keeps_existing_lessons(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE knowledge(id INTEGER PRIMARY KEY, kind TEXT, title TEXT); "
                       "INSERT INTO knowledge VALUES(1, 'fix', 'Existing lesson'); PRAGMA user_version=16;")
    conn.close()
    conn = connect(path)
    try:
        row = conn.execute("SELECT title, case_json FROM knowledge WHERE id=1").fetchone()
        assert tuple(row) == ("Existing lesson", None)
    finally:
        conn.close()


def test_case_filter_is_before_limit_and_pagination_preserves_old_cases(synced):
    conn = synced["conn"]
    for i in range(5):
        conn.execute("INSERT INTO knowledge(kind, title, project_path, case_json, created_at) VALUES(?,?,?,?,?)",
                     ("fix", f"Case {i}", CWD, json.dumps(case_of(material())), "2026-09-20"))
    for i in range(10):
        conn.execute("INSERT INTO knowledge(kind, title, project_path, created_at) VALUES(?,?,?,?)",
                     ("fact", f"New fact {i}", CWD, "2026-10-09"))
    # lesson material without a question (a learning's principle) is no case file
    conn.execute("INSERT INTO knowledge(kind, title, project_path, case_json, created_at) VALUES(?,?,?,?,?)",
                 ("learning", "Principle only", CWD, json.dumps({"principle": "Readers never block the writer."}), "2026-10-09"))
    first = search_knowledge(conn, cases=True, limit=3)
    rest = search_knowledge(conn, cases=True, limit=3, offset=3)
    assert len(first) == 3 and len(rest) == 2
    assert len({r["id"] for r in [*first, *rest]}) == 5
    assert all(r["title"].startswith("Case") for r in [*first, *rest])


def test_library_pages_include_existing_lessons_of_every_teaching_kind(synced):
    conn = synced["conn"]
    kinds = ("fix", "gotcha", "decision", "learning", "pattern")
    conn.executemany("INSERT INTO knowledge(kind, title, body, project_path, created_at) VALUES(?,?,?,?,?)",
                     [(kinds[i % len(kinds)], f"Lesson {i}", "A recorded explanation.", CWD, "2026-10-09")
                      for i in range(1006)])
    conn.execute("INSERT INTO knowledge(kind, title, body, project_path) VALUES('fact','Reference','A fact',?)", (CWD,))
    conn.execute("INSERT INTO knowledge(kind, title, body, project_path) VALUES('fix','Empty','  ',?)", (CWD,))
    first = search_knowledge(conn, lessons=True, limit=1000)
    rest = search_knowledge(conn, lessons=True, limit=1000, offset=1000)
    assert len(first) == 1000 and len(rest) == 6
    assert len({r["id"] for r in [*first, *rest]}) == 1006
    assert {r["kind"] for r in [*first, *rest]} == set(kinds)
    assert all(r["title"].startswith("Lesson") and r["case_json"] is None for r in [*first, *rest])


def test_member_only_gets_cases_from_allowed_projects(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    data = normalize_analysis({"title": "Folder sharing", "knowledge": [material()]})
    store_analysis(conn, cfg, SID, data, "fake-model", 2)
    conn.execute("INSERT INTO knowledge(kind, title, project_path, case_json) VALUES('fix', 'Private', '/private', ?)",
                 (json.dumps(case_of(material())),))
    conn.commit()
    app = App(cfg)
    try:
        access.limit(app.conn, [CWD])
        data = app.knowledge({"cases": "1"})
        assert data["cases"] == 1 and len(data["items"]) == 1
        assert data["items"][0]["case"]["scene"] == material()["scene"]
        assert "case_json" not in data["items"][0]
    finally:
        app.release()


def sketch(**extra):
    return {"type": "flow", "title": "Config freshness", "nodes": [
        {"id": "read", "label": "Read config", "detail": "A snapshot is read."},
        {"id": "push", "label": "Push state", "detail": "Reload changed config before push."}],
        "edges": [{"from": "read", "to": "push", "label": "Snapshot"}], **extra}


def test_visuals_keep_valid_links_bound_size_and_redact_every_label():
    raw = sketch()
    raw["nodes"] += [{"id": "read", "label": "Duplicate", "detail": ""}, {"id": {}, "label": "Malformed"}]
    raw["edges"] += [{"from": "missing", "to": "push", "label": "Invalid"},
                     {"from": "read", "to": "push", "label": "Duplicate"}, {"from": [], "to": "push"}]
    assert visual_of(raw) == sketch()
    assert visual_of({"type": [], "nodes": []}) is None
    assert visual_of(sketch(edges=[])) is None
    assert visual_of(sketch(type="comparison", edges=[]))["nodes"] == sketch()["nodes"]
    raw = sketch(title=SECRET)
    raw["nodes"][0].update(label=SECRET, detail=SECRET)
    raw["edges"][0]["label"] = SECRET
    assert SECRET not in json.dumps(visual_of(raw))
    raw = sketch(nodes=[{"id": f"part-{i}", "label": str(i), "detail": "x"*1000} for i in range(10)],
                 edges=[{"from": "part-0", "to": f"part-{i}", "label": "Connects"} for i in range(1, 10)])
    visual = visual_of(raw)
    assert len(visual["nodes"]) == 4 and len(visual["edges"]) == 3
    assert all(len(n["detail"]) == 600 for n in visual["nodes"])


def test_reanalysis_adds_a_visual_to_unchanged_pinned_material(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    data = normalize_analysis({"knowledge": [material()]})
    store_analysis(conn, cfg, SID, data, "fake", 1)
    conn.execute("UPDATE knowledge SET pinned=1 WHERE session_id=?", (SID,))
    data = normalize_analysis({"knowledge": [material(visual=sketch())]})
    store_analysis(conn, cfg, SID, data, "fake", 1)
    row = conn.execute("SELECT case_json, pinned FROM knowledge WHERE session_id=?", (SID,)).fetchone()
    assert json.loads(row["case_json"])["visual"] == sketch() and row["pinned"] == 1
    conn.execute("UPDATE knowledge SET body='Edited by the user', case_json=? WHERE session_id=?",
                 (json.dumps(case_of(material())), SID))
    store_analysis(conn, cfg, SID, data, "fake", 1)
    row = conn.execute("SELECT body, case_json FROM knowledge WHERE session_id=?", (SID,)).fetchone()
    assert row["body"] == "Edited by the user" and "visual" not in json.loads(row["case_json"])
