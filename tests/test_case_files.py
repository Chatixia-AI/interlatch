"""Case files: a fix, gotcha or decision keeps how it first showed (the scene), the question it raised, the answer and
the leads the session ruled out, so the dashboard can ask before it tells and the agent knows what not to chase."""

import json
import sqlite3

from interlatch.analyze import PROMPT_VERSION, case_of, normalize_analysis, normalize_knowledge, store_analysis
from interlatch.storage.sqlite import connect
from interlatch.storage.schema import SCHEMA_VERSION

from conftest import SECRET, SID

CASE = {"kind": "gotcha", "title": "Web files are read once at startup", "body": "Restart the server.",
        "scene": "Edited app.css and reloaded; nothing changed.", "question": "Why didn't the edit show up?",
        "answer": "The server read the web files once, at startup.",
        "ruled_out": [{"lead": "A more specific selector won.", "why": "The rule worked once the new file was served."}]}


def test_case_fields_only_for_fixes_gotchas_and_decisions():
    items = normalize_knowledge([
        CASE,
        {**CASE, "kind": "fact", "title": "A fact"},
        {**CASE, "kind": "decision", "title": "Leads as strings", "ruled_out": ["Option A", "", {"why": "no lead"}, *"BCDEF"]},
    ])
    assert items[0]["scene"] == CASE["scene"] and items[0]["ruled_out"] == CASE["ruled_out"]
    assert (items[1]["scene"], items[1]["question"], items[1]["answer"], items[1]["ruled_out"]) == ("", "", "", [])
    assert items[2]["ruled_out"][0] == {"lead": "Option A", "why": ""} and len(items[2]["ruled_out"]) == 4
    # an older reply without the fields still normalizes
    old = normalize_analysis({"knowledge": [{"kind": "fix", "title": "Old shape", "body": "b"}]})["knowledge"][0]
    assert old["scene"] == "" and old["ruled_out"] == [] and case_of(old) is None


def test_case_of_needs_scene_question_and_answer_and_is_redacted():
    assert case_of({**CASE, "question": ""}) is None
    assert case_of({**CASE, "kind": "command"}) is None
    leaky = {**CASE, "scene": f"curl failed with token={SECRET}",
             "ruled_out": [{"lead": f"export API_KEY={SECRET}", "why": f"Authorization: Bearer {SECRET}"}]}
    assert SECRET not in json.dumps(case_of(leaky))


def test_store_analysis_keeps_the_case_and_fills_a_pinned_lesson(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    data = normalize_analysis({"title": "t", "knowledge": [CASE, {"kind": "fact", "title": "A fact", "body": "b"}]})
    store_analysis(conn, cfg, SID, data, "m", 2)
    rows = {r["title"]: r for r in conn.execute("SELECT * FROM knowledge WHERE session_id = ?", (SID,))}
    assert json.loads(rows[CASE["title"]]["case_json"]) == {k: CASE[k] for k in ("scene", "question", "answer", "ruled_out")}
    assert rows["A fact"]["case_json"] is None
    assert rows[CASE["title"]]["source_ref"] == f"prompt-v{PROMPT_VERSION}"

    # a lesson pinned before case files existed: re-analysis keeps it as it is, and gives it its case
    conn.execute("UPDATE knowledge SET pinned = 1, case_json = NULL, body = 'pinned body' WHERE title = ?", (CASE["title"],))
    store_analysis(conn, cfg, SID, data, "m", 2)
    row = conn.execute("SELECT body, pinned, case_json FROM knowledge WHERE title = ?", (CASE["title"],)).fetchone()
    assert row["body"] == "pinned body" and row["pinned"] == 1 and json.loads(row["case_json"])["scene"] == CASE["scene"]
    assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE session_id = ?", (SID,)).fetchone()[0] == 2


def test_an_older_archive_gains_the_column(tmp_path):
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute("CREATE TABLE knowledge (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL)")
    old.execute("INSERT INTO knowledge(kind, title) VALUES ('fix', 'kept')")
    old.execute("PRAGMA user_version = 16")
    old.commit()
    old.close()
    conn = connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert "case_json" in {r[1] for r in conn.execute("PRAGMA table_info(knowledge)")}
    assert conn.execute("SELECT title, case_json FROM knowledge").fetchone()[:] == ("kept", None)
    conn.close()


def test_dashboard_and_mcp_show_the_case(synced):
    from interlatch.mcp_server import Tools
    from interlatch.server import App

    conn, cfg = synced["conn"], synced["cfg"]
    store_analysis(conn, cfg, SID, normalize_analysis({"title": "t", "knowledge": [
        CASE, {"kind": "fix", "title": "No case yet", "body": "b"}]}), "m", 2)
    conn.commit()
    app = App(cfg)
    data = app.knowledge({})
    by_title = {k["title"]: k for k in data["items"]}
    assert by_title[CASE["title"]]["case"]["question"] == CASE["question"] and "case_json" not in by_title[CASE["title"]]
    assert by_title["No case yet"]["case"] is None
    assert data["cases"] == 1
    assert [k["title"] for k in app.knowledge({"cases": "1"})["items"]] == [CASE["title"]]
    assert app.knowledge_hub()["knowledge"]["cases"] == 1
    session = app.session(SID)
    assert next(k for k in session["knowledge"] if k["title"] == CASE["title"])["case"]["scene"] == CASE["scene"]

    out = Tools(cfg).search_knowledge("startup")
    assert f"**Seen as:** {CASE['scene']}" in out
    assert "- A more specific selector won. (The rule worked once the new file was served.)" in out
    assert "Ruled out" in Tools(cfg).get_session(SID)
