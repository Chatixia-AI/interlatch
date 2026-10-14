import json

import pytest

from interlatch.analysis.session import analyze_session, normalize_analysis
from interlatch.storage.kv import kv_get
from interlatch.analysis.digest import build_digest
from interlatch.interfaces.exports.vault import export_markdown
from interlatch.analysis.runners.base import UsageLimitError
from interlatch.analysis.runners.replies import parse_json_object
from interlatch.core.redact import redact
from interlatch.knowledge.synthesize import GLOBAL, projects_needing_synthesis, synthesize_project
from interlatch.application.workflows import run_worker
from interlatch.analysis.queue import PAUSE_KEY, pending_sessions

from conftest import CWD, SECRET, SID, fake_log


def test_digest_is_redacted_and_structured(synced):
    header, d = build_digest(synced["conn"], SID, 100_000)
    assert d.level == 3 and len(d.chunks) == 1
    assert SECRET not in d.text and "[REDACTED" in d.text
    assert "### [1] USER" in d.text and "### [2] USER" in d.text and "sent while the agent was working" in d.text
    assert "→ Bash: pytest tests/test_login.py -x" in d.text
    assert "✗ File does not exist." in d.text
    assert "subagent report: Logout lives in session.py" in d.text
    assert "auth.py (+2/-1)" in header


def test_digest_chunks_long_sessions(synced):
    _, d = build_digest(synced["conn"], SID, 300)
    assert len(d.chunks) > 1
    assert all(c.strip() for c in d.chunks)


def test_analyze_session_stores_overview_and_knowledge(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    analyze_session(conn, cfg, SID)
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (SID,)).fetchone()
    assert s["analysis_status"] == "done" and s["outcome"] == "completed"
    assert s["title"] == "Fixed login token expiry bug"
    assert s["analysis_model"] == "claude-sonnet-5"  # the model that did the work, not the first listed
    assert json.loads(s["work_types_json"]) == ["bugfix"]
    assert json.loads(s["tags_json"]) == ["auth", "pytest"]
    assert json.loads(s["friction_json"])[0] == {"kind": "other", "note": "read a missing file"}
    ks = conn.execute("SELECT kind, title, confidence, scope FROM knowledge WHERE session_id=? ORDER BY id", (SID,)).fetchall()
    assert [tuple(k) for k in ks] == [
        ("fix", "Token TTL compared in seconds vs ms", "high", "project"),
        ("learning", "Unknown kinds become learnings", "medium", "global"),
    ]
    call = fake_log(synced)[-1]
    assert SECRET not in call["prompt_head"]
    for flag in ("--no-session-persistence", "--safe-mode", "--tools", "--strict-mcp-config"):
        assert flag in call["args"]
    assert "--json-schema" not in call["args"]
    # re-analysis replaces the session's extracted knowledge instead of duplicating it
    analyze_session(conn, cfg, SID)
    assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE session_id=?", (SID,)).fetchone()[0] == 2


def test_map_reduce_for_long_sessions(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    cfg.analysis.chunk_chars = 400
    analyze_session(conn, cfg, SID)
    calls = fake_log(synced)
    parts = [c for c in calls if "<transcript_part" in c["prompt_head"]]
    assert len(parts) >= 2
    assert "<part_notes>" in calls[-1]["prompt_head"]
    kinds = [r[0] for r in conn.execute("SELECT kind FROM analyses WHERE target=?", (SID,))]
    assert kinds.count("chunk") == len(parts) and kinds[-1] == "session"
    # the merge names the items it keeps; the item itself comes from the part, never retyped
    assert '"id": "p1.1"' in calls[-1]["prompt_head"]
    titles = lambda: [r[0] for r in conn.execute("SELECT title FROM knowledge WHERE session_id=? ORDER BY id", (SID,))]
    assert titles() == [f"Part {len(parts)} gotcha"]


@pytest.mark.parametrize("ids", ["[]", '["p99.1", 7]', "null"])
def test_a_merge_that_keeps_nothing_keeps_every_part_item(synced, monkeypatch, ids):
    """Merging a long session's 28 lessons once came back with an empty list, and the session lost all of them."""
    conn, cfg = synced["conn"], synced["cfg"]
    cfg.analysis.chunk_chars = 400
    monkeypatch.setenv("FAKE_REDUCE_IDS", ids)
    analyze_session(conn, cfg, SID)
    parts = sum("<transcript_part" in c["prompt_head"] for c in fake_log(synced))
    kept = [r[0] for r in conn.execute("SELECT title FROM knowledge WHERE session_id=? ORDER BY id", (SID,))]
    assert kept == [f"Part {i} gotcha" for i in range(1, parts + 1)]


def test_usage_limit_pauses_the_worker(synced, monkeypatch):
    conn, cfg = synced["conn"], synced["cfg"]
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    with pytest.raises(UsageLimitError):
        analyze_session(conn, cfg, SID)
    assert conn.execute("SELECT analysis_status FROM sessions WHERE id=?", (SID,)).fetchone()[0] == "pending"
    report = run_worker(cfg, synthesize=False, export=None)
    assert report.paused_until and report.failed
    assert kv_get(conn, PAUSE_KEY)
    # while paused, the worker does not call Claude again
    before = len(fake_log(synced))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    report = run_worker(cfg, synthesize=False, export=None)
    assert report.paused_until and len(fake_log(synced)) == before


def test_worker_end_to_end(synced):
    cfg, conn = synced["cfg"], synced["conn"]
    assert pending_sessions(conn, cfg, 10) == [SID]
    report = run_worker(cfg, export=export_markdown)
    assert report.analyzed == [SID] and not report.failed
    assert CWD in report.synthesized
    kb = conn.execute("SELECT * FROM project_kb WHERE project_path=?", (CWD,)).fetchone()
    assert kb and "Demo app overview." in kb["markdown"] and "Token TTL unit mismatch." in kb["markdown"]
    assert report.exported > 0
    notes = list((cfg.notes_dir / "Sessions").rglob("*.md"))
    assert len(notes) == 2  # the transcript session + the history-only one
    text = next(n for n in notes if SID[:8] in n.name).read_text()
    assert text.startswith("---\nsession_id: " + SID) and "Token TTL compared" in text and SECRET not in text
    assert (cfg.notes_dir / "Home.md").exists() and (cfg.notes_dir / "Projects" / "demo-app.md").exists()
    # nothing left to do: a second run is a no-op
    again = run_worker(cfg, export=export_markdown)
    assert not again.analyzed and again.exported == 0


def test_synthesis_threshold_and_global(synced):
    cfg, conn = synced["cfg"], synced["conn"]
    analyze_session(conn, cfg, SID)
    assert CWD in projects_needing_synthesis(conn, cfg)
    synthesize_project(conn, cfg, CWD)
    assert CWD not in projects_needing_synthesis(conn, cfg)
    synthesize_project(conn, cfg, GLOBAL)
    assert conn.execute("SELECT COUNT(*) FROM project_kb WHERE project_path=?", (GLOBAL,)).fetchone()[0] == 1


def test_parse_json_object_variants():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}
    assert parse_json_object('{"parameter": {"title": "t", "summary": "s"}}') == {"title": "t", "summary": "s"}
    assert parse_json_object("not json") is None


def test_normalize_analysis_defaults():
    n = normalize_analysis({"title": "t"})
    assert n["outcome"] == "unclear" and n["knowledge"] == [] and n["sentiment"] == "unclear"


def test_redaction_patterns():
    text = ("key sk-ant-api03-abcdefghijklmnopqrstuvwxyz ghp_" + "a" * 36 + " AKIAABCDEFGHIJKLMNOP "  # gitleaks:allow (a fake key the test needs)
            "postgres://user:hunter2pass@db:5432/x password=supersecret1 dapi" + "0" * 32 + " Bearer abcdefghijklmnopqrstuvwxyz123")
    out = redact(text)
    for leaked in ("sk-ant-api03", "ghp_aaaa", "AKIAABCD", "hunter2pass", "supersecret1", "dapi0000", "abcdefghijklmnopqrstuvwxyz123"):
        assert leaked not in out, leaked
    assert "postgres://user:[REDACTED]@db" in out


def test_redaction_of_http_credentials():
    cases = {  # fake credentials the test needs
        "curl -H 'Authorization: Basic dXNlcjpodW50ZXIy' https://x": "curl -H 'Authorization: Basic [REDACTED]' https://x",  # gitleaks:allow
        '{"Authorization": "Bearer short123"}': '{"Authorization": "Bearer [REDACTED]"}',  # gitleaks:allow
        "Proxy-Authorization: Basic YWRhOmxvdmVsYWNl": "Proxy-Authorization: Basic [REDACTED]",  # gitleaks:allow
        "curl -s -u ada:lovelace1 https://api.example.com": "curl -s -u ada:[REDACTED] https://api.example.com",  # gitleaks:allow
        "curl --user=ada:lovelace1 x": "curl --user=ada:[REDACTED] x",  # gitleaks:allow
        "Cookie: session=abc123; theme=dark": "Cookie: [REDACTED]",
        "set-cookie: sid=s%3Axyz; Path=/; HttpOnly\nnext line": "set-cookie: [REDACTED]\nnext line",
        '"Cookie": "_gh_sess=Zm9vYmFy"': '"Cookie": "[REDACTED]"',
        "curl --cookie 'sid=xyz; a=b' https://x": "curl --cookie '[REDACTED]' https://x",
    }
    for text, want in cases.items():
        assert redact(text) == want, text
    for prose in ("a basic understanding of the API", "authorization: required for every call", "the Cookie: header",
                  "Set-Cookie is sent by the server", "curl -u ada https://x (prompts for the password)",
                  "cookie: crumbs everywhere"):
        assert redact(prose) == prose, prose


def test_redaction_of_prefixed_names():
    """An environment variable's name carries a prefix: DB_PASSWORD is a password as much as password is."""
    cases = {  # fake credentials the test needs
        "DB_PASSWORD=hunter2hunter2": "DB_PASSWORD=[REDACTED]",  # gitleaks:allow
        "POSTGRES_PASSWORD: s3cretvalue": "POSTGRES_PASSWORD: [REDACTED]",  # gitleaks:allow
        "PGPASSWORD=s3cretvalue psql": "PGPASSWORD=[REDACTED] psql",  # gitleaks:allow
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI": "AWS_SECRET_ACCESS_KEY=[REDACTED]",  # gitleaks:allow
        "export SLACK_SIGNING_SECRET=8f742231b10e": "export SLACK_SIGNING_SECRET=[REDACTED]",  # gitleaks:allow
        "my-app-api-key: abcdef123": "my-app-api-key: [REDACTED]",  # gitleaks:allow
    }
    for text, want in cases.items():
        assert redact(text) == want, text
    for kept in ("PASSWORD_FILE=/run/secrets/db", "TOKEN_TTL=3600", "secretary: Ada", "max_tokens: 4096"):  # not a secret's own name
        assert redact(kept) == kept, kept


def test_weekly_review(synced):
    from interlatch.knowledge.reviews import generate_review, review_ready, week_bounds

    conn, cfg = synced["conn"], synced["cfg"]
    key = week_bounds("2026-W38")[0]
    assert key == "2026-W38"
    data = generate_review(conn, cfg, key)
    assert data["headline"] == "Fixed login" and data["tldr"] == ["Login fixed", "TTL units learned", "Logout next"]
    row = conn.execute("SELECT * FROM reviews WHERE period=?", (key,)).fetchone()
    assert row["n_sessions"] == 1 and "## Suggestions" in row["markdown"] and "- [ ] logout" in row["markdown"]
    assert "- Login fixed" in row["markdown"]
    from interlatch.knowledge.reviews import week_glance

    glance = week_glance(conn, row["start"], row["end"])
    assert len(glance["daily"]) == 7 and sum(glance["daily"]) > 0 and glance["projects"][0]["sessions"] == 1
    assert review_ready(conn, key) == (False, "exists")
    from interlatch.interfaces.exports.vault import export_markdown

    export_markdown(conn, cfg)
    assert (cfg.notes_dir / "Reviews" / f"{key}.md").exists()


def test_parse_json_object_local_repairs():
    broken = '{"a": "regex \\d+ and C:\\Users\\x", "b": "line1\nline2", "c": "ok \\"quoted\\""}'
    assert parse_json_object(broken) == {"a": "regex \\d+ and C:\\Users\\x", "b": "line1\nline2", "c": 'ok "quoted"'}


def test_hung_call_hits_wall_clock_deadline(synced, monkeypatch):
    import time

    from interlatch.analysis.runners import process
    from interlatch.analysis.runners.base import LLMError
    from interlatch.analysis.runners.claude import ClaudeRunner

    monkeypatch.setattr(process, "POLL_S", 0.2)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")
    cfg = synced["cfg"]
    cfg.analysis.timeout_seconds = 1
    t0 = time.monotonic()
    with pytest.raises(LLMError, match="timed out"):
        ClaudeRunner(cfg).run("x", {"type": "object"}, system="s")
    assert time.monotonic() - t0 < 10  # the child was killed, not waited for


def test_sleep_interrupted_call_is_requeued_without_penalty(synced, monkeypatch):
    from interlatch.analysis.runners import process
    from interlatch.analysis.runners.base import SleepInterruptedError

    monkeypatch.setattr(process, "POLL_S", 0.2)
    monkeypatch.setattr(process, "SLEEP_GRACE_S", -1)  # simulate: wall clock ran ahead of the monotonic clock
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")
    conn, cfg = synced["conn"], synced["cfg"]
    with pytest.raises(SleepInterruptedError):
        analyze_session(conn, cfg, SID)
    row = conn.execute("SELECT analysis_status, analysis_attempts FROM sessions WHERE id=?", (SID,)).fetchone()
    assert tuple(row) == ("pending", 0)


def test_ctrl_c_drops_the_queued_sessions(synced, monkeypatch):
    import time

    started, messages = [], []

    def slow(conn, cfg, sid, runner, model=None):
        started.append(sid)
        time.sleep(0.2)

    def interrupted(futures):
        raise KeyboardInterrupt
        yield

    monkeypatch.setattr("interlatch.analysis.session.analyze_session", slow)
    monkeypatch.setattr("interlatch.analysis.worker.as_completed", interrupted)
    synced["cfg"].analysis.concurrency = 1
    with pytest.raises(KeyboardInterrupt):
        run_worker(synced["cfg"], session_ids=[SID] * 5, progress=messages.append)
    assert len(started) <= 1 and "stopping: finishing the sessions already in progress…" in messages


def test_synthesis_waits_for_queued_sessions(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    analyze_session(conn, cfg, SID)
    conn.execute("INSERT INTO sessions(id, source, project_path, analysis_status) VALUES ('q1', 'transcript', ?, 'pending')", (CWD,))
    conn.commit()
    assert CWD not in projects_needing_synthesis(conn, cfg)  # one more session of this project is still queued
    assert CWD in projects_needing_synthesis(conn, cfg, force=True)
    conn.execute("UPDATE sessions SET analysis_status = 'done' WHERE id = 'q1'")
    conn.commit()
    assert CWD in projects_needing_synthesis(conn, cfg)


def test_kb_titles_and_tldr_survive_normalizing_and_rendering():
    from interlatch.knowledge.synthesize import normalize_kb, render_kb_markdown

    data = normalize_kb({"tldr": ["Branch first", " ", "Verify in a browser", "Keep tests green", "dropped"],
                         "overview": "o", "superseded_ids": [],
                         "sections": [{"title": "Prefs", "items": [{"title": "Never commit to main", "text": "Use a branch.", "sources": [3]},
                                                                  {"text": "Untitled bullet", "sources": []}]}]})
    assert data["tldr"] == ["Branch first", "Verify in a browser", "Keep tests green"]
    assert data["sections"][0]["items"][0]["title"] == "Never commit to main" and "title" not in data["sections"][0]["items"][1]
    md = render_kb_markdown("P", data, n_items=2, model=None)
    assert "- **Never commit to main**: Use a branch. <sub>[k3]</sub>" in md and "- Untitled bullet" in md and "- Branch first" in md
    assert "tldr" not in normalize_kb({"overview": "o", "sections": []})  # older knowledge bases have none
