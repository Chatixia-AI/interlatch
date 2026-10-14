"""Synthetic sessions with failed tool results and friction notes, for the friction / suggestion engine."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from interlatch.core.util import to_iso, utcnow

# real error texts, as the archive stores them (from the mining of the real archive)
ERRORS = {
    "playwright-output-roots": ("mcp__playwright__browser_take_screenshot",
                                "### Error\nError: File access denied: /private/tmp/claude-501/-Users-test-Projects-demo/"
                                "e48254c8-bdb6-43fe-b4f9-0637b44f49a1/scratchpad/dd1.png is outside allowed roots. "
                                "Allowed roots: /Users/test/Projects/demo/.playwright-mcp, /Users/test/Projects/demo", ""),
    "playwright-browser-in-use": ("mcp__playwright__browser_navigate",
                                  "### Error\nError: Browser is already in use for /Users/test/Library/Caches/ms-playwright-mcp/"
                                  "mcp-chrome-5fa7364, use --isolated to run multiple instances of the same browser", ""),
    "playwright-stale-refs": ("mcp__playwright__browser_click",
                              "### Error\nError: Ref f1e553 not found in the current page snapshot. Try capturing new snapshot.", ""),
    "edit-stale-context": ("apply_patch",
                           "Script failed\nWall time 0.0 seconds\nOutput:\n\nScript error:\napply_patch verification failed: "
                           "Failed to find expected lines in /Users/test/Projects/demo/README.md:\n| a | b |", ""),
    "zsh-nomatch": ("Bash", "Exit code 1\n(eval):1: no matches found: --include=*.test.ts",
                    "grep -rln foo src --include=*.test.ts"),
    "zsh-equals": ("Bash", "Exit code 1\nmain.py\n(eval):1: ==== not found", "git diff --stat; echo ====; git status"),
    "sleep-polling-blocked": ("Bash", "<tool_use_error>Blocked: sleep 60 followed by: cat /tmp/x/tasks/b1.output. "
                                      "Use run_in_background</tool_use_error>", "sleep 60; cat /tmp/x/tasks/b1.output"),
    "timeout-missing": ("Bash", "Exit code 127\n(eval):1: command not found: timeout", "timeout 12 python3 -c 'print(1)'"),
    "bare-python-modules": ("Bash", "Exit code 1\nTraceback (most recent call last):\n  File \"<stdin>\", line 1, in <module>\n"
                                    "ModuleNotFoundError: No module named 'openpyxl'", "python3 - <<'EOF'\nimport openpyxl\nEOF"),
    "interactive-aliases": ("Bash", "Exit code 1\noverwrite app/agent/a2ui.py? (y/n [n]) not overwritten",
                            "cp app/agent/a2ui.py /tmp/a2ui_mine.py && cp /tmp/head.py app/agent/a2ui.py"),
    "stale-dev-server": ("exec_command", "Script completed\nWall time 0.1 seconds\nOutput:\n\nExit code: 1\n"
                                         "OSError: [Errno 48] Address already in use", "python3 server.py"),
    "cwd-drift": ("Bash", "Exit code 1\n(eval):cd:1: no such file or directory: backend", "cd backend && uv run pytest"),
    "auto-mode-retry": ("Bash", "Permission for this action was denied by the Claude Code auto mode classifier. Reason: "
                                "[Credential Exploration]. If you have other tasks that don't depend on this action, continue.",
                        "cat ~/.pypirc"),
    "bash-timeout": ("Bash", "Exit code 143\nCommand timed out after 3m 20s\nsigned out gate: Sign in", "node verify.mjs"),
}


def ago(days: float = 0, minutes: float = 0) -> str:
    return to_iso(utcnow() - timedelta(days=days, minutes=minutes))


class Archive:
    """Writes sessions, failed tool results and tool calls straight into a Chronicle database."""

    def __init__(self, conn, root: Path):
        self.conn, self.root, self.seq = conn, root, 0

    def project(self, name: str) -> str:
        path = self.root / "projects" / name
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def session(self, sid: str, project: str | None, *, agent: str = "claude", days: float = 1, source: str = "transcript",
                friction: list | None = None, title: str | None = None, hours: float = 1) -> str:
        start = utcnow() - timedelta(days=days)
        self.conn.execute(
            "INSERT INTO sessions(id, source, agent, project_path, project_name, started_at, ended_at, title, friction_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, source, agent, project, Path(project).name if project else None, to_iso(start),
             to_iso(start + timedelta(hours=hours)), title or f"session {sid}",
             json.dumps([{"kind": "tool_error", "note": n} for n in friction or []])))
        return sid

    def result(self, sid: str, ts: str, tool: str, text: str, *, is_error: int = 1, command: str = "",
               tool_use_id: str | None = None, file_path: str | None = None, call: bool = True) -> str:
        self.seq += 1
        tuid = tool_use_id or f"tu-{self.seq}"
        self.conn.execute(
            "INSERT INTO events(session_id, agent_id, seq, ts, role, kind, tool_name, tool_use_id, is_error, text) "
            "VALUES (?, '', ?, ?, 'user', 'tool_result', ?, ?, ?, ?)", (sid, self.seq, ts, tool, tuid, is_error, text))
        if call:
            self.conn.execute(
                "INSERT INTO tool_calls(session_id, tool_use_id, ts, name, command, file_path, is_error) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, tuid, ts, tool, command or None, file_path, is_error))
        return tuid

    def cause(self, sid: str, cause: str, ts: str, **kw) -> str:
        tool, text, command = ERRORS[cause]
        return self.result(sid, ts, tool, text, command=command, **kw)

    def tool_use(self, sid: str, ts: str, tool: str = "Bash"):
        self.seq += 1
        self.conn.execute("INSERT INTO events(session_id, agent_id, seq, ts, role, kind, tool_name, tool_use_id, text) "
                          "VALUES (?, '', ?, ?, 'assistant', 'tool_use', ?, ?, '')", (sid, self.seq, ts, tool, f"use-{self.seq}"))


def make_archive(tmp_path, monkeypatch):
    """A fresh database plus a sandboxed HOME (instruction files and ~/.claude.json live there); yields once."""
    from interlatch.core.config import load_config
    from interlatch.storage.sqlite import connect

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    chome = tmp_path / "chronicle"
    monkeypatch.setenv("INTERLATCH_HOME", str(chome))
    monkeypatch.setattr("interlatch.knowledge.instructions._gh_bin", lambda: None)  # never ask GitHub from tests
    monkeypatch.setattr("interlatch.knowledge.instructions._VISIBILITY", {})
    cfg = load_config(chome)
    conn = connect(cfg.db_path)
    yield {"conn": conn, "cfg": cfg, "home": home, "a": Archive(conn, tmp_path), "tmp": tmp_path}
    conn.close()
