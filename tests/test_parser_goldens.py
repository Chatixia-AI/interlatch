"""Safety net: the full parsed output of every agent's parser, compared to tests/goldens/parsers/<agent>.json.

Each test parses every session its agent's fixture writes (conftest.py, codex_fixture.py, copilot_fixture.py,
antigravity_fixture.py) and serializes the whole ParsedSession: every field (events, tool calls, API calls with
tokens and cost, file stats, subagents, counters, timestamps) and the aggregates the database is filled from. A parser
refactor that changes any of it fails here. For a deliberate change, rerun with UPDATE_GOLDENS=1 and review the diff.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from pathlib import Path
from typing import Any

from interlatch.antigravity_parser import conversation_dirs, load_db_meta, load_title, parse_antigravity_conversation
from interlatch.bob_parser import load_tasks, parse_bob_task
from interlatch.codex_parser import parse_codex_session
from interlatch.copilot_parser import (agent_session_dirs, chat_files, load_usage, parse_copilot_agent,
                                      parse_vscode_chat, workspace_folder)
from interlatch.parser import parse_session
from interlatch.core.sessions import ParsedSession

from antigravity_fixture import NEW_ID as AGY_ID, write_antigravity_home
from codex_fixture import CODE_ID, CODEX_ID, code_mode_rollout, write_codex_home
from conftest import CWD, SID, write_fake_claude_tree
from copilot_fixture import BOB_TOOLS, CHAT_ID, write_bob_home, write_copilot_home, write_vscode_user
from golden import assert_golden

SKIP = ("_seen_",)  # dedupe bookkeeping while parsing, not output


def _plain(value) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)
                if not f.name.startswith(SKIP)}
    if isinstance(value, Counter):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, float):
        return round(value, 9)
    return value


def _canonical(ps: ParsedSession) -> dict[str, Any]:
    """Every field of the session, plus the aggregates ingest stores (totals, primary model, peak context)."""
    return {
        **_plain(ps),
        "derived": _plain({
            "n_api_calls": ps.n_api_calls,
            "n_tool_errors": ps.n_tool_errors,
            "peak_context": ps.peak_context,
            "totals": ps.totals(),
            "primary_model": ps.primary_model,
            "lines_added": ps.lines_added,
            "lines_removed": ps.lines_removed,
        }),
    }


def _scrub(value, root: Path):
    """The test's temporary folder, in any spelling, as <TMP>: the golden is the same on every machine."""
    spellings = sorted({str(root), str(root.resolve()), root.as_posix(), root.resolve().as_posix(),
                        str(root).replace("\\", "\\\\"), str(root.resolve()).replace("\\", "\\\\")}, key=len, reverse=True)

    def fix(v):
        if isinstance(v, str):
            for s in spellings:
                if s in v:
                    v = v.replace(s, "<TMP>")
            return v.replace("\\", "/") if "<TMP>" in v else v
        if isinstance(v, dict):
            return {fix(k): fix(x) for k, x in v.items()}
        if isinstance(v, list):
            return [fix(x) for x in v]
        return v

    return fix(value)


def _golden(agent: str, sessions: dict[str, ParsedSession | None], root: Path) -> dict[str, Any]:
    data = _scrub({sid: None if ps is None else _canonical(ps) for sid, ps in sessions.items()}, root)
    assert_golden(f"parsers/{agent}.json", data)
    return data


def test_golden_claude_code(tmp_path):
    main_file = write_fake_claude_tree(tmp_path / "claude")
    data = _golden("claude_code", {SID: parse_session(main_file)}, tmp_path)[SID]

    # a few readable anchors, so a regenerated golden can't quietly lose the basics
    assert data["ai_title"] == "Fix login token expiry" and data["project_path"] == CWD
    assert [t["name"] for t in data["tool_calls"]] == ["Bash", "Edit", "Read", "Agent", "Grep"]
    assert data["subagents"] and data["derived"]["n_tool_errors"] == 1


def test_golden_codex(tmp_path):
    home = write_codex_home(tmp_path / "codex")
    code_mode = home / "sessions" / f"rollout-2026-09-22T09-00-00-{CODE_ID}.jsonl"  # code mode's exec and wait calls
    code_mode.write_text("\n".join(code_mode_rollout()) + "\n")
    files = sorted((home / "sessions").rglob("*.jsonl"))
    sessions = {f.name: parse_codex_session(f) for f in files}
    data = _golden("codex", sessions, tmp_path)

    main = next(d for d in data.values() if d and d["id"] == CODEX_ID)
    assert main["git_branch"] == "feat/flaky" and main["derived"]["primary_model"] == "gpt-5.5"
    assert len(files) == 6


def test_golden_copilot(tmp_path):
    vscode_user = write_vscode_user(tmp_path / "vscode")
    chats = {f"vscode:{f.name}": parse_vscode_chat(f, workspace_folder(f)) for f in sorted(chat_files(vscode_user))}
    home = write_copilot_home(tmp_path / "copilot")
    usage = load_usage(home)
    agents = {f"cli:{d.name}": parse_copilot_agent(d, usage.get(d.name)) for d in sorted(agent_session_dirs(home))}
    data = _golden("copilot", {**chats, **agents}, tmp_path)

    chat = data[f"vscode:{CHAT_ID}.jsonl"]
    assert chat["custom_title"] == "Health endpoint"
    assert [t["name"] for t in chat["tool_calls"]] == ["read_file", "run_in_terminal"]
    assert agents, "the Copilot CLI fixture wrote no sessions"


def test_golden_bob(tmp_path):
    tasks = load_tasks(write_bob_home(tmp_path / "bob"))
    data = _golden("bob", {t["id"]: parse_bob_task(t) for t in sorted(tasks, key=lambda t: t["id"])}, tmp_path)

    tools = data[BOB_TOOLS]
    assert tools["custom_title"] == "Explainer page" and tools["subagents"]
    assert any("explainer.html" in path for path in tools["files"])


def test_golden_antigravity(tmp_path):
    home = write_antigravity_home(tmp_path / "agy")
    sessions = {}
    for conv in sorted(conversation_dirs(home)):
        cid = conv.name
        sessions[cid] = parse_antigravity_conversation(conv, title=load_title(home, cid),
                                                       db_meta=load_db_meta(home / "conversations" / f"{cid}.db"))
    data = _golden("antigravity", sessions, tmp_path)

    new = data[AGY_ID]
    assert new["entrypoint"] == "antigravity" and new["ai_title"] == "Fix the failing test あ"
    assert [t["name"] for t in new["tool_calls"]] == ["run_command", "view_file", "replace_file_content"]
    assert new["api_calls"][1]["input_tokens"] == 200 and new["api_calls"][1]["cache_read_tokens"] == 1000
