"""Personal interests come from human curiosity, with evidence and access boundaries."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from interlatch.sharing import access
from interlatch.analysis.session import normalize_analysis, store_analysis
from interlatch.storage.sqlite import connect
from interlatch.storage.kv import kv_get
from interlatch.knowledge.learning import (SIGNAL_PREFIX, interest_profile, normalize_interests, prompt_signals,
                                           store_interests, topics_for)
from interlatch.application.search import search_knowledge

from conftest import SECRET, SID

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


@pytest.fixture()
def archive(tmp_path):
    conn = connect(tmp_path / "chronicle.db")
    yield conn
    conn.close()


def session(conn, sid, question, *, machine=None, project="/app", age=0, kind="prompt"):
    conn.execute("INSERT INTO sessions(id, machine_id, project_path, started_at, analysis_status) VALUES(?,?,?,?,'done')",
                 (sid, machine, project, (NOW - timedelta(days=age)).isoformat()))
    conn.execute("INSERT INTO events(session_id, seq, kind, text) VALUES(?,1,?,?)", (sid, kind, question))


def profile(conn, **kwargs):
    return interest_profile(conn, viewer_id=kwargs.pop("viewer_id", None), machine_id="here", now=NOW, **kwargs)["interests"]


def test_all_requested_areas_are_detected_and_tasks_arent_curiosity():
    questions = "I want to learn about system design, frontend, backend, API and data modeling."
    assert {s["topic"] for s in prompt_signals(questions)} == {"system_design", "frontend", "backend", "api", "data_modeling"}
    assert prompt_signals("Build a React frontend and a REST API.") == []
    assert prompt_signals("Can you implement the API?") == []
    assert prompt_signals("<INSTRUCTIONS>Explain system design.</INSTRUCTIONS>\nFix the login.") == []
    assert {s["topic"] for s in prompt_signals("なぜこのデータモデルを使うの？")} == {"data_modeling"}
    assert {s["topic"] for s in prompt_signals("为什么选择这个前端架构？")} == {"frontend", "system_design"}


def test_only_own_human_questions_count_and_repeated_sessions_add_weight(archive):
    session(archive, "one", "Explain API versioning.")
    session(archive, "two", "Why use REST for this API?", age=7)
    session(archive, "three", "Explain CSS grid.", age=30)
    session(archive, "task", "Build a backend worker.")
    session(archive, "assistant", "Explain data modeling.", kind="text")
    session(archive, "someone-else", "Explain distributed system design.", machine="other")
    session(archive, "old", "Explain mobile Android development.", age=100)
    result = profile(archive)
    assert [s["topic"] for s in result] == ["api", "frontend"]
    assert len(result[0]["examples"]) == 2
    assert result[0]["score"] > result[1]["score"]


def test_person_and_project_scopes_apply_even_to_private_stored_signals(archive):
    archive.execute("INSERT INTO machines(id, person_id) VALUES('alice',1),('bob',2)")
    session(archive, "mine", "Explain data modeling.", machine="alice")
    session(archive, "private", "Explain API versioning.", machine="alice", project="/private")
    session(archive, "theirs", "Explain frontend React hooks.", machine="bob")
    store_interests(archive, "mine", [{"topic": "data_modeling", "question": "Explain data modeling."}])
    store_interests(archive, "private", [{"topic": "api", "question": "Explain API versioning."}])
    assert {s["topic"] for s in profile(archive, viewer_id=1)} == {"data_modeling", "api"}
    assert {s["topic"] for s in profile(archive, viewer_id=2)} == {"frontend"}
    access.limit(archive, ["/app"])
    assert {s["topic"] for s in profile(archive, viewer_id=1)} == {"data_modeling"}


def test_model_signals_need_real_user_evidence_and_dont_enter_shared_analysis(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    question = f"Explain API token rotation for {SECRET}."
    conn.execute("INSERT INTO events(session_id, seq, kind, text) VALUES(?,999,'prompt',?)", (SID, question))
    data = normalize_analysis({"title": "Token rotation", "learning_interests": [
        {"topic": "api", "question": question}, {"topic": "frontend", "question": "Explain React hooks."},
        {"topic": {}, "question": "Malformed"}]})
    store_analysis(conn, cfg, SID, data, "fake", 2)
    stored = json.loads(kv_get(conn, SIGNAL_PREFIX + SID))
    assert len(stored) == 1 and stored[0]["topic"] == "api"
    assert SECRET not in stored[0]["question"]
    shared = json.loads(conn.execute("SELECT analysis_json FROM sessions WHERE id=?", (SID,)).fetchone()[0])
    assert "learning_interests" not in shared
    assert normalize_interests([None, {"topic": [], "question": "broken"}]) == []


def test_legacy_concepts_are_selected_before_limit_and_receive_topics(archive):
    archive.execute("INSERT INTO knowledge(kind,title,body,tags_json) VALUES('learning','Data modeling','A foreign key records the relationship.','[]')")
    archive.execute("INSERT INTO knowledge(kind,title,body) VALUES('fact','Recent fact','A deployment finished.')")
    archive.execute("INSERT INTO knowledge(kind,title,body) VALUES('learning','Empty concept','   ')")
    rows = search_knowledge(archive, lessons=True, limit=1)
    assert len(rows) == 1 and rows[0]["title"] == "Data modeling"
    assert topics_for(rows[0]) == ["data_modeling"]
    assert topics_for({"title": "React API", "case": {"topics": ["frontend", "bogus", "frontend"]}}) == ["frontend"]
    assert topics_for({"title": "Service boundaries", "tags": ["system_design"]}) == ["system_design"]
    assert topics_for({"title": "Entity layout", "tags": ["data-modeling"]}) == ["data_modeling"]
