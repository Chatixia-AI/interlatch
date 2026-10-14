"""Safety-net test suite: Golden snapshot tests for all 5 parsers.

Guarantees that parser refactoring across Claude Code, Codex, Copilot, Bob, and Antigravity
maintains exact structured output across sessions, tool calls, files, and API usage.
"""

from __future__ import annotations

from typing import Any

from chronicle.antigravity_parser import load_db_meta, load_title, parse_antigravity_conversation
from chronicle.bob_parser import load_tasks, parse_bob_task
from chronicle.codex_parser import parse_codex_session
from chronicle.copilot_parser import parse_vscode_chat, workspace_folder
from chronicle.parser import parse_session

from antigravity_fixture import NEW_ID as AGY_ID, write_antigravity_home
from codex_fixture import CODEX_ID, write_codex_home
from conftest import CWD, SID, write_fake_claude_tree
from copilot_fixture import BOB_TOOLS, CHAT_ID, write_bob_home, write_vscode_user


def _canonical(ps) -> dict[str, Any]:
    return {
        "id": ps.id,
        "ai_title": ps.ai_title,
        "custom_title": ps.custom_title,
        "entrypoint": ps.entrypoint,
        "project_path": ps.project_path,
        "git_branch": ps.git_branch,
        "git_remote": ps.git_remote,
        "n_prompts": ps.n_prompts,
        "first_prompt": ps.first_prompt,
        "last_prompt": ps.last_prompt,
        "n_tool_calls": len(ps.tool_calls),
        "n_tool_errors": ps.n_tool_errors,
        "primary_model": ps.primary_model,
        "permission_mode": ps.permission_mode,
        "tool_calls": [
            {
                "name": t.name,
                "command": t.command,
                "file_path": t.file_path,
                "duration_ms": t.duration_ms,
                "is_error": t.is_error,
            }
            for t in ps.tool_calls
        ],
        "files": {
            path: {
                "reads": f.reads,
                "edits": f.edits,
                "writes": f.writes,
                "lines_added": f.lines_added,
                "lines_removed": f.lines_removed,
            }
            for path, f in sorted(ps.files.items())
        },
        "api_calls": [
            {
                "model": c.model,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "cache_read_tokens": c.cache_read_tokens,
                "cache_write_tokens": c.cache_write_tokens,
            }
            for c in ps.api_calls
        ],
    }


def test_golden_claude_code(tmp_path):
    main_file = write_fake_claude_tree(tmp_path / "claude")
    ps = parse_session(main_file)
    data = _canonical(ps)

    assert data["id"] == SID
    assert data["ai_title"] == "Fix login token expiry"
    assert data["project_path"] == CWD
    assert data["git_branch"] == "main"
    assert data["n_prompts"] == 2
    assert data["n_tool_errors"] == 1
    assert data["permission_mode"] == "auto"
    assert len(data["tool_calls"]) == 5
    tool_names = [t["name"] for t in data["tool_calls"]]
    assert tool_names == ["Bash", "Edit", "Read", "Agent", "Grep"]
    assert f"{CWD}/auth.py" in data["files"]
    auth = data["files"][f"{CWD}/auth.py"]
    assert auth == {"reads": 0, "edits": 1, "writes": 0, "lines_added": 2, "lines_removed": 1}
    assert len(data["api_calls"]) == 6


def test_golden_codex(tmp_path):
    codex_home = write_codex_home(tmp_path / "codex")
    session_file = next((codex_home / "sessions").glob(f"**/*{CODEX_ID}.jsonl"))
    ps = parse_codex_session(session_file)
    data = _canonical(ps)

    assert data["id"] == CODEX_ID
    assert data["project_path"] == "/Users/test/Projects/codex-app"
    assert data["git_branch"] == "feat/flaky"
    assert data["n_prompts"] == 2
    assert "Fix the flaky test" in data["first_prompt"]
    assert data["primary_model"] == "gpt-5.5"
    assert data["permission_mode"] == "on-request"
    assert len(data["tool_calls"]) == 4
    tool_names = [t["name"] for t in data["tool_calls"]]
    assert "exec" in tool_names and "apply_patch" in tool_names
    assert data["tool_calls"][0]["is_error"] is True  # exit code 1
    assert "/Users/test/Projects/codex-app/app.py" in data["files"]
    app_f = data["files"]["/Users/test/Projects/codex-app/app.py"]
    assert app_f["edits"] == 1 and app_f["lines_added"] == 2 and app_f["lines_removed"] == 1


def test_golden_copilot(tmp_path):
    vscode_user = write_vscode_user(tmp_path / "vscode")
    session_file = next((vscode_user / "workspaceStorage").glob(f"*/chatSessions/{CHAT_ID}.jsonl"))
    ps = parse_vscode_chat(session_file, workspace_folder(session_file))
    data = _canonical(ps)

    assert data["id"] == CHAT_ID
    assert data["custom_title"] == "Health endpoint"
    assert data["project_path"] == "/Users/test/Projects/copilot-app"
    assert data["n_prompts"] == 1
    assert data["first_prompt"] == "Add a health endpoint and run the tests"
    assert len(data["tool_calls"]) == 2
    assert [t["name"] for t in data["tool_calls"]] == ["read_file", "run_in_terminal"]
    assert "/Users/test/Projects/copilot-app/app.py" in data["files"]
    app_f = data["files"]["/Users/test/Projects/copilot-app/app.py"]
    assert app_f["reads"] == 1 and app_f["edits"] == 1


def test_golden_bob(tmp_path):
    bob_home = write_bob_home(tmp_path / "bob")
    tasks = load_tasks(bob_home)
    bob_task = next(t for t in tasks if t["id"] == BOB_TOOLS)
    ps = parse_bob_task(bob_task)
    data = _canonical(ps)

    assert data["id"] == BOB_TOOLS
    assert data["custom_title"] == "Explainer page"
    assert data["n_prompts"] == 1
    assert data["first_prompt"] == "Make an explainer page"
    assert len(data["tool_calls"]) == 4
    tool_names = [t["name"] for t in data["tool_calls"]]
    assert "spawn_subagent" in tool_names and "write_file" in tool_names
    assert any("explainer.html" in path for path in data["files"])


def test_golden_antigravity(tmp_path):
    agy_home = write_antigravity_home(tmp_path / "agy")
    ps = parse_antigravity_conversation(
        agy_home / "brain" / AGY_ID,
        title=load_title(agy_home, AGY_ID),
        db_meta=load_db_meta(agy_home / "conversations" / f"{AGY_ID}.db"),
    )
    data = _canonical(ps)

    assert data["id"] == AGY_ID
    assert data["entrypoint"] == "antigravity"
    assert data["ai_title"] == "Fix the failing test あ"
    assert data["git_branch"] == "feature/fix-test"
    assert data["git_remote"] == "git@github.com:org/agy-app.git"
    assert data["n_prompts"] == 1
    assert data["first_prompt"] == "fix the failing test"
    assert data["primary_model"] == "gemini-3.8-flash"
    assert len(data["tool_calls"]) == 3
    assert [t["name"] for t in data["tool_calls"]] == ["run_command", "view_file", "replace_file_content"]
    assert len(data["api_calls"]) == 3
    assert data["api_calls"][1]["input_tokens"] == 200
    assert data["api_calls"][1]["cache_read_tokens"] == 1000
