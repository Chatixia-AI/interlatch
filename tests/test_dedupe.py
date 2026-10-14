"""Duplicated transcript records: Claude Code replays a session's chain into the same file, and logs API retries."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from interlatch.storage.sqlite import connect
from interlatch.ingestion.service import sync
from interlatch.sources.claude.parser import PARSER_VERSION, parse_session

from conftest import CWD, SID, _line, main_transcript


def replayed_transcript() -> list[str]:
    """The fixture transcript, then the whole chain again as Claude Code re-appends it (seen after an artifact
    publish or a resume): same uuids and timestamps, a `slug` added, and tool results' stdout dropped."""
    lines = main_transcript()[:-1]  # without the torn last line
    replay = []
    for line in lines:
        d = json.loads(line)
        if not d.get("uuid"):
            continue
        d["slug"] = "linked-booping-spring"
        if isinstance(d.get("toolUseResult"), dict):
            d["toolUseResult"] = {"stdout": "", "stderr": "", "interrupted": False, "isImage": False}
        replay.append(json.dumps(d))
    return [*lines, json.dumps({"type": "frame-link", "sessionId": SID, "timestamp": "2026-09-20T10:08:30.000Z"}), *replay]


def api_retries() -> list[str]:
    """Two failed requests: one retried twice (three records chained by parentUuid), one that failed once."""
    err = {"message": "Connection error.", "formatted": "Can't reach the API server (ENOTFOUND)"}
    return [
        _line(type="system", subtype="api_error", uuid="e1", parentUuid="u1", timestamp="2026-09-20T10:00:01.000Z",
              level="error", error=err, retryAttempt=1, maxRetries=10, retryInMs=500),
        _line(type="system", subtype="api_error", uuid="e2", parentUuid="e1", timestamp="2026-09-20T10:00:02.000Z",
              level="error", error=err, retryAttempt=2, maxRetries=10, retryInMs=1000),
        _line(type="system", subtype="api_error", uuid="e3", parentUuid="e2", timestamp="2026-09-20T10:00:03.000Z",
              level="error", error=err, retryAttempt=3, maxRetries=10, retryInMs=2000),
        _line(type="system", subtype="api_error", uuid="e4", parentUuid="u2", timestamp="2026-09-20T10:00:30.000Z",
              level="error", error={"message": "Overloaded"}, retryAttempt=1, maxRetries=10, retryInMs=500),
    ]


def _write(tmp_path: Path, lines: list[str]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{SID}.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_replayed_chain_parses_like_the_original(tmp_path):
    original = parse_session(_write(tmp_path / "a", main_transcript()))
    replayed = parse_session(_write(tmp_path / "b", replayed_transcript()))
    assert replayed.n_prompts == original.n_prompts == 2
    assert len(replayed.tool_calls) == len(original.tool_calls)
    assert len(replayed.events) == len(original.events)
    assert (replayed.n_interrupts, replayed.n_compactions) == (original.n_interrupts, original.n_compactions) == (1, 1)
    assert not [t for t, n in Counter(t.tool_use_id for t in replayed.tool_calls).items() if n > 1]
    # the first copy wins: the replay's stripped tool results must not replace the real ones
    assert replayed.lines_added == original.lines_added == 2
    assert replayed.n_tool_errors == original.n_tool_errors == 1


def test_tool_call_recorded_twice_under_new_uuids(tmp_path):
    lines = main_transcript()[:-1]
    again = json.loads(next(line for line in lines if '"toolu_1"' in line and '"tool_use"' in line))
    result = json.loads(next(line for line in lines if '"toolu_1"' in line and '"tool_result"' in line))
    again["uuid"], result["uuid"] = "a1-copy", "u2-copy"
    ps = parse_session(_write(tmp_path, [*lines, json.dumps(again), json.dumps(result)]))
    assert [t.tool_use_id for t in ps.tool_calls].count("toolu_1") == 1
    results = [e for e in ps.events if e.kind == "tool_result" and e.tool_use_id == "toolu_1"]
    assert len(results) == 1


def test_api_retries_count_as_one_failure(tmp_path):
    lines = main_transcript()[:-1]
    ps = parse_session(_write(tmp_path, [*lines[:2], *api_retries(), *lines[2:]]))
    assert ps.n_api_errors == 2
    assert [e.text for e in ps.events if e.kind == "api_error"] == ["Can't reach the API server (ENOTFOUND)", "Overloaded"]


def test_sync_stores_each_record_once(env):
    env["main"].write_text("\n".join(replayed_transcript()) + "\n")
    conn = connect(env["cfg"].db_path)
    sync(env["cfg"], conn)
    dup_tools = conn.execute("SELECT COUNT(*) FROM (SELECT tool_use_id FROM tool_calls WHERE session_id = ? "
                             "GROUP BY agent_id, tool_use_id HAVING COUNT(*) > 1)", (SID,)).fetchone()[0]
    dup_events = conn.execute("SELECT COUNT(*) FROM (SELECT 1 FROM events WHERE session_id = ? "
                              "GROUP BY agent_id, ts, kind, text HAVING COUNT(*) > 1)", (SID,)).fetchone()[0]
    s = conn.execute("SELECT n_prompts, n_interrupts, n_tool_calls FROM sessions WHERE id = ?", (SID,)).fetchone()
    assert (dup_tools, dup_events) == (0, 0)
    assert (s["n_prompts"], s["n_interrupts"], s["n_tool_calls"]) == (2, 1, 5)


def _as_old_parser_left_it(conn) -> None:
    """What version 1 stored for a replayed session: every row twice, prompts double counted, already analyzed."""
    conn.execute("INSERT INTO tool_calls(session_id, agent_id, tool_use_id, ts, name, summary) "
                 "SELECT session_id, agent_id, tool_use_id, ts, name, summary FROM tool_calls WHERE session_id = ?", (SID,))
    conn.execute("UPDATE sessions SET parser_version = 1, n_prompts = 4, analysis_status = 'done', analyzed_prompts = 4, "
                 "llm_title = 'Fixed login token expiry', summary = 'kept' WHERE id = ?", (SID,))
    conn.execute("INSERT INTO knowledge(session_id, project_path, kind, title, body, fingerprint) "
                 "VALUES (?, ?, 'fix', 'Token TTL units', 'seconds vs ms', 'fp-dedupe')", (SID, CWD))
    conn.commit()


def test_parser_bump_repairs_stored_rows_and_keeps_analysis(env):
    env["main"].write_text("\n".join(replayed_transcript()) + "\n")
    conn = connect(env["cfg"].db_path)
    sync(env["cfg"], conn)
    _as_old_parser_left_it(conn)
    report = sync(env["cfg"], conn)  # the transcript is unchanged: only the parser version differs
    assert report.sessions_updated == 1 and not report.errors
    s = conn.execute("SELECT * FROM sessions WHERE id = ?", (SID,)).fetchone()
    assert s["parser_version"] == PARSER_VERSION and s["n_prompts"] == 2
    # the analysis covered every real prompt: it stays done, and later growth is measured from the real count
    assert (s["analysis_status"], s["analyzed_prompts"], s["summary"], s["title"]) == ("done", 2, "kept", "Fixed login token expiry")
    assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE fingerprint = 'fp-dedupe'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM tool_calls WHERE session_id = ?", (SID,)).fetchone()[0] == 5
    with open(env["main"], "a") as fh:
        fh.write(_line(type="user", uuid="u-new", timestamp="2026-09-20T11:00:00.000Z", origin={"kind": "human"},
                       message={"role": "user", "content": "one more thing"}) + "\n")
    sync(env["cfg"], conn)
    assert conn.execute("SELECT analysis_status FROM sessions WHERE id = ?", (SID,)).fetchone()[0] == "stale"


def test_parser_bump_repairs_sessions_kept_only_in_the_archive(env):
    env["main"].write_text("\n".join(replayed_transcript()) + "\n")
    conn = connect(env["cfg"].db_path)
    sync(env["cfg"], conn)
    _as_old_parser_left_it(conn)
    env["main"].unlink()  # Claude Code deleted the transcript; the vault's gzip copy remains
    report = sync(env["cfg"], conn)
    assert not report.errors and SID in report.touched
    s = conn.execute("SELECT * FROM sessions WHERE id = ?", (SID,)).fetchone()
    assert (s["parser_version"], s["n_prompts"], s["source_present"]) == (PARSER_VERSION, 2, 0)
    assert (s["analysis_status"], s["analyzed_prompts"]) == ("done", 2)
    assert s["transcript_path"] == str(env["main"]) and s["n_subagents"] == 1
    assert conn.execute("SELECT COUNT(*) FROM tool_calls WHERE session_id = ?", (SID,)).fetchone()[0] == 5
    assert sync(env["cfg"], conn).sessions_updated == 0  # repaired once, then left alone
