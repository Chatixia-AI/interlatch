"""The Knowledge page's Learn band picks real material and hides what it doesn't have."""

import json
from datetime import datetime, timezone

from interlatch.analysis.session import normalize_analysis, store_analysis
from interlatch.knowledge.learning import glance
from interlatch.interfaces.web.app import App

from conftest import SID

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def lesson(i, day, topics=(), case=None, **extra):
    return {"id": i, "title": f"Lesson {i}", "kind": "fix", "confidence": "high", "stage": "provisional",
            "created_at": f"2026-{day}", "learning_topics": list(topics), "case": case, **extra}


def test_latest_prefers_a_principle_then_a_case_file_and_skips_unsure_lessons():
    principle = lesson(1, "09-01", case={"principle": "Frame stream messages yourself."})
    case_file = lesson(2, "10-01", case={"scene": "s", "question": "Why?", "answer": "a"})
    newest = [lesson(3, "10-08"), lesson(4, "10-09", confidence="low", case={"principle": "unsure"}),
              lesson(5, "10-09", stage="wip", case={"question": "work in progress?"})]
    g = glance([principle, case_file, *newest], [], now=NOW)
    assert g["latest"]["id"] == 1  # older, but it has a principle to show
    assert [k["id"] for k in g["cases"]] == [2]
    assert glance([case_file, newest[0]], [], now=NOW)["latest"]["id"] == 2
    assert glance([newest[0]], [], now=NOW)["latest"] is None
    choices = lesson(6, "08-01", case={"question": "Which?", "ruled_out": [{"lead": "A wrong lead", "why": "w"}]})
    assert [k["id"] for k in glance([case_file, choices], [], now=NOW)["cases"]] == [6, 2]  # real choices ask first


def test_the_topic_is_the_one_asked_about_else_the_busiest_this_month_never_other():
    items = [lesson(1, "10-01", ["api"]), lesson(2, "10-02", ["api"]), lesson(3, "10-03", ["backend"]),
             *[lesson(10 + i, "06-01", ["frontend"]) for i in range(5)], lesson(20, "10-04")]
    g = glance(items, [], now=NOW)
    assert g["topic"]["id"] == "api" and g["topic"]["asked"] is False  # frontend has more, but none this month
    assert g["topic"]["count"] == 2 and [k["id"] for k in g["topic"]["lessons"]] == [2, 1]
    g = glance(items, [{"topic": "security", "score": 9}, {"topic": "backend", "score": 1}], now=NOW)
    assert g["topic"]["id"] == "backend" and g["topic"]["asked"] is True  # no security lessons to show
    assert glance([lesson(20, "10-04")], [], now=NOW)["topic"] is None


def test_endpoint_sends_shaped_lessons(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    item = {"kind": "fix", "title": "The worker pushed a stale snapshot", "body": "Reload config before the push.",
            "scene": "A folder moved after a job finished.", "question": "When was the config read?",
            "answer": "Before the job started.", "ruled_out": [], "principle": "Read state when you apply it.",
            "topics": ["backend"]}
    store_analysis(conn, cfg, SID, normalize_analysis({"title": "t", "knowledge": [item]}), "fake", 1)
    conn.commit()
    app = App(cfg)
    try:
        g = json.loads(json.dumps(app.learning_glance(None, {})))
        assert g["latest"]["case"]["principle"] == item["principle"] and "case_json" not in g["latest"]
        assert g["cases"][0]["case"]["question"] == item["question"]
        assert g["topic"]["id"] == "backend"
    finally:
        app.release()
