"""A lesson must not confirm itself: a session that only followed what Interlatch told it (the SessionStart context, a
line Interlatch wrote to an instruction file) repeats the lesson, and the ladder doesn't count it."""

import json

from interlatch.analysis.digest import session_guidance
from interlatch.analysis.session import analyze_session
from interlatch.core.sessions import GUIDANCE_OPENING
from interlatch.ingestion.service import sync
from interlatch.knowledge import ladder
from interlatch.knowledge.synthesize import synthesize_project
from interlatch.runtime.hooks import build_session_context

from conftest import CWD, SID, _line, fake_log


def _lesson(conn, sid, title, *, basis="observed", started="2026-09-01T10:00:00Z"):
    if not conn.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone():
        conn.execute("INSERT INTO sessions(id, project_path, project_name, started_at, source, agent, analysis_status) "
                     "VALUES (?, ?, 'demo-app', ?, 'claude', 'claude', 'done')", (sid, CWD, started))
    cur = conn.execute(
        "INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, confidence, evidence, "
        "basis, source, fingerprint, created_at, updated_at, stage) VALUES (?, ?, 'demo-app', 'gotcha', ?, 'b', 'project', "
        "'high', 'e', ?, 'analysis', ?, ?, ?, 'provisional')",
        (sid, CWD, title, basis, f"fp-{sid}-{title}", started, started))
    ladder.refresh(conn, [cur.lastrowid])
    return cur.lastrowid


def _row(conn, kid):
    return conn.execute("SELECT * FROM knowledge WHERE id = ?", (kid,)).fetchone()


def _told(env, context: str) -> None:
    """The session's transcript gains the context a SessionStart hook added, beside another hook's, and is re-synced."""
    with env["main"].open("a") as fh:
        fh.write(_line(type="attachment", uuid="h1", timestamp="2026-09-20T10:00:00.500Z", attachment={
            "type": "hook_additional_context", "hookName": "SessionStart", "toolUseID": "SessionStart",
            "hookEvent": "SessionStart", "content": [context, "Databricks context:\n- CLI v1.11.0."]}) + "\n")
    sync(env["cfg"], env["conn"])


def test_the_hook_context_is_recognised_in_every_form(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    _lesson(conn, "s1", "Stripe needs the raw body")
    notes = build_session_context(cfg, CWD)
    assert notes.startswith("Interlatch notes from") and GUIDANCE_OPENING.match(notes)
    synthesize_project(conn, cfg, CWD)
    conn.commit()
    kb = build_session_context(cfg, CWD)
    assert kb.startswith("Interlatch knowledge base for") and GUIDANCE_OPENING.match(kb)
    assert GUIDANCE_OPENING.match("Chronicle knowledge base for demo (from past coding-agent sessions, ...)")
    assert not GUIDANCE_OPENING.match("Databricks context:\n- CLI v1.11.0.")


def test_the_analysis_is_shown_what_the_session_was_told(synced, monkeypatch):
    conn, cfg = synced["conn"], synced["cfg"]
    _told(synced, "Interlatch notes from past coding-agent sessions in this project:\n"
                  "- [gotcha · established ×2] Token TTL is in ms\nRecent sessions here: 09-19 Login (completed)")
    kinds = [r[0] for r in conn.execute("SELECT kind FROM events WHERE session_id = ? AND kind = 'guidance'", (SID,))]
    assert kinds == ["guidance"]  # only Interlatch's part of the hook's context, and never in the transcript digest
    conn.execute("INSERT INTO suggestions(key, kind, origin, agent, target_path, title, text, status, applied_at) VALUES "
                 "('a', 'instruction', 'friction', 'claude', '~/.claude/CLAUDE.md', 't', 'Re-read before editing.', 'applied', '2026-09-01T00:00:00Z'),"
                 "('b', 'instruction', 'friction', 'claude', '~/.claude/CLAUDE.md', 't', 'Applied after it began.', 'applied', '2026-09-21T00:00:00Z'),"
                 "('c', 'instruction', 'friction', 'codex', '~/.codex/AGENTS.md', 't', 'For Codex only.', 'applied', '2026-09-01T00:00:00Z'),"
                 "('d', 'instruction', 'friction', 'claude', '~/.claude/CLAUDE.md', 't', 'Proposed, never applied.', 'new', NULL)")
    assert session_guidance(conn, SID).splitlines() == ["- [gotcha · established ×2] Token TTL is in ms",
                                                         "- Re-read before editing."]

    monkeypatch.setenv("FAKE_BASIS", "guidance")
    analyze_session(conn, cfg, SID)
    prompt = fake_log(synced)[-1]["prompt_head"]
    assert "<interlatch_guidance>\n- [gotcha · established ×2] Token TTL is in ms" in prompt
    assert "Recent sessions here" not in prompt and "Databricks" not in prompt
    first, second = conn.execute("SELECT * FROM knowledge WHERE session_id = ? ORDER BY id", (SID,)).fetchall()
    assert first["basis"] == "guidance" and (first["stage"], first["stage_reason"]) == ("wip", ladder.GUIDANCE_STAGE_REASON)
    assert ladder.confirmations(first) == [] and second["basis"] == "observed"


def test_a_session_told_nothing_cannot_have_followed_guidance(synced, monkeypatch):
    conn, cfg = synced["conn"], synced["cfg"]
    monkeypatch.setenv("FAKE_BASIS", "guidance")
    analyze_session(conn, cfg, SID)
    assert "<interlatch_guidance>" not in fake_log(synced)[-1]["prompt_head"]
    assert {r[0] for r in conn.execute("SELECT basis FROM knowledge WHERE session_id = ?", (SID,))} == {"observed"}


def test_a_followed_lesson_never_confirms_the_lesson_it_followed(synced):
    conn = synced["conn"]
    original = _lesson(conn, "s1", "Stripe needs the raw body")
    echo = _lesson(conn, "s2", "Stripe needs the raw body", basis="guidance", started="2026-09-05T10:00:00Z")
    ladder.apply_synthesis(conn, [{"id": echo, "by": original, "reason": "duplicate"}], {original, echo}, retire=True)
    assert (_row(conn, original)["stage"], ladder.confirmations(_row(conn, original))) == ("provisional", ["s1"])

    again = _lesson(conn, "s3", "Stripe needs the raw body", started="2026-09-20T10:00:00Z")  # hit the problem anyway
    ladder.apply_synthesis(conn, [{"id": again, "by": original, "reason": "duplicate"}], {original, again}, retire=True)
    assert (_row(conn, original)["stage"], ladder.confirmations(_row(conn, original))) == ("established", ["s1", "s3"])


def test_a_followed_lesson_kept_as_the_survivor_counts_only_the_others(synced):
    conn = synced["conn"]
    echo = _lesson(conn, "s2", "x", basis="guidance")
    found = _lesson(conn, "s3", "x", started="2026-09-03T10:00:00Z")
    ladder.apply_synthesis(conn, [{"id": found, "by": echo, "reason": "duplicate"}], {echo, found}, retire=False)
    assert ladder.confirmations(_row(conn, echo)) == ["s3"] and _row(conn, echo)["stage"] == "provisional"
    assert ladder.confirmations(_row(conn, found)) == ["s3"]
    assert json.loads(_row(conn, echo)["confirmed_json"]) == ["s3"]
