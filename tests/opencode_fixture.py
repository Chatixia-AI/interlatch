"""A fake OpenCode data folder (~/.local/share/opencode): opencode.db with the session, message and part tables as
OpenCode 1.19 creates them (packages/core/src/database/schema.gen.ts), plus a credential table the reader must never
touch. The rows follow OpenCode's own record shapes (packages/schema/src/v1/session.ts)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

FOLDER = "/Users/me/oc-app"
ROOT_ID = "ses_ec135d17fffeYjAs6UkELWYu6R"
CHILD_ID = "ses_ec135c00fffeQq1Ab2Cd3Ef4Gh"
EMPTY_ID = "ses_ec135b00fffeEmptySession01"
T0 = 1_792_054_800_000  # ms

DDL = """
CREATE TABLE "project" ("id" text PRIMARY KEY, "worktree" text NOT NULL, "vcs" text, "name" text, "icon_url" text,
  "icon_url_override" text, "icon_color" text, "time_created" integer NOT NULL, "time_updated" integer NOT NULL,
  "time_initialized" integer, "sandboxes" text NOT NULL, "commands" text);
CREATE TABLE "session" ("id" text PRIMARY KEY, "project_id" text NOT NULL, "workspace_id" text, "parent_id" text,
  "slug" text NOT NULL, "directory" text NOT NULL, "path" text, "title" text NOT NULL, "version" text NOT NULL,
  "share_url" text, "summary_additions" integer, "summary_deletions" integer, "summary_files" integer,
  "summary_diffs" text, "metadata" text, "cost" real DEFAULT 0 NOT NULL, "tokens_input" integer DEFAULT 0 NOT NULL,
  "tokens_output" integer DEFAULT 0 NOT NULL, "tokens_reasoning" integer DEFAULT 0 NOT NULL,
  "tokens_cache_read" integer DEFAULT 0 NOT NULL, "tokens_cache_write" integer DEFAULT 0 NOT NULL, "revert" text,
  "permission" text, "agent" text, "model" text, "time_created" integer NOT NULL, "time_updated" integer NOT NULL,
  "time_compacting" integer, "time_archived" integer);
CREATE TABLE "message" ("id" text PRIMARY KEY, "session_id" text NOT NULL, "time_created" integer NOT NULL,
  "time_updated" integer NOT NULL, "data" text NOT NULL);
CREATE TABLE "part" ("id" text PRIMARY KEY, "message_id" text NOT NULL, "session_id" text NOT NULL,
  "time_created" integer NOT NULL, "time_updated" integer NOT NULL, "data" text NOT NULL);
CREATE TABLE "credential" ("id" text PRIMARY KEY, "provider_id" text NOT NULL, "data" text NOT NULL);
"""


def _session(db, sid, *, parent=None, title, directory=FOLDER, agent="build", t=T0):
    db.execute("INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, agent, time_created, "
               "time_updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (sid, "4b0ea68d7af9", parent, "quiet-river", directory, title, "1.19.0", agent, t, t + 60_000))


class _Writer:
    def __init__(self, db, sid):
        self.db, self.sid, self.n = db, sid, 0

    def message(self, data: dict, parts: list[dict]) -> str:
        self.n += 1
        mid = f"msg_{self.sid[4:12]}{self.n:04d}"
        created = data["time"]["created"]
        self.db.execute("INSERT INTO message VALUES (?,?,?,?,?)", (mid, self.sid, created, created, json.dumps(data)))
        for i, p in enumerate(parts):
            self.db.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                            (f"prt_{mid[4:]}{i:03d}", mid, self.sid, created + i, created + i, json.dumps(p)))
        return mid


def _user(t, text, **extra):
    return ({"role": "user", "time": {"created": t}, "agent": "build",
             "model": {"providerID": "anthropic", "modelID": "claude-sonnet-4-5"}},
            [{"type": "text", "text": text}, *extra.get("parts", [])])


def _assistant(t, parts, *, model="claude-sonnet-4-5", provider="anthropic", end=None, **extra):
    return ({"role": "assistant", "time": {"created": t, "completed": end or t + 5000}, "parentID": "msg_x",
             "modelID": model, "providerID": provider, "mode": "build", "agent": "build",
             "path": {"cwd": FOLDER, "root": FOLDER}, "cost": 0, "tokens": {"input": 0, "output": 0, "reasoning": 0,
                                                                          "cache": {"read": 0, "write": 0}}, **extra},
            parts)


def _tool(call_id, tool, inp, *, output="", start, ms=100, error=None, metadata=None):
    state = {"status": "error", "input": inp, "error": error, "time": {"start": start, "end": start + ms}} if error else \
        {"status": "completed", "input": inp, "output": output, "title": tool, "metadata": metadata or {},
         "time": {"start": start, "end": start + ms}}
    return {"type": "tool", "callID": call_id, "tool": tool, "state": state}


def _finish(cost, inp, out, cache_read=0, reasoning=0, reason="tool-calls"):
    return {"type": "step-finish", "reason": reason, "cost": cost,
            "tokens": {"input": inp, "output": out, "reasoning": reasoning, "cache": {"read": cache_read, "write": 0}}}


PATCH = """*** Begin Patch
*** Add File: docs/notes.md
+# Notes
+login waits for ready
*** Update File: src/app.ts
@@
-const delay = 100
+const delay = 0
*** End Patch"""


def write_opencode_home(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "opencode.db")
    db.executescript(DDL)
    db.execute("INSERT INTO credential VALUES ('c1', 'anthropic', '{\"key\": \"must-never-be-read\"}')")
    _session(db, ROOT_ID, title="Fix flaky login test")
    _session(db, CHILD_ID, parent=ROOT_ID, title="Find auth helpers (@explore subagent)", agent="explore", t=T0 + 20_000)
    _session(db, EMPTY_ID, title="New session - 2026-10-14T09:00:00.000Z", t=T0 - 86_400_000)

    w = _Writer(db, ROOT_ID)
    w.message(*_user(T0, "fix the flaky login test", parts=[
        {"type": "file", "mime": "image/png", "filename": "fail.png", "url": "data:image/png;base64,AAAA"},
        {"type": "text", "text": "Called the Read tool with the following input: ...", "synthetic": True}]))
    w.message(*_assistant(T0 + 1000, [
        {"type": "step-start", "snapshot": "9f1c2e"},
        {"type": "reasoning", "text": "The test sleeps instead of waiting.", "time": {"start": T0 + 1100, "end": T0 + 1200}},
        {"type": "text", "text": "Let me look at the test.", "time": {"start": T0 + 1300, "end": T0 + 1400}},
        _tool("toolu_01", "read", {"filePath": f"{FOLDER}/test/login.test.ts"}, output="<file>await sleep(100)</file>",
              start=T0 + 1500),
        _tool("toolu_02", "bash", {"command": "npm test -- login", "description": "Run the login test"},
              output="1 failing", start=T0 + 1700, ms=2400),
        _finish(0.0123, 9100, 120, cache_read=20100),
        {"type": "step-start", "snapshot": "a71b"},
        _tool("toolu_03", "edit", {"filePath": f"{FOLDER}/test/login.test.ts", "oldString": "await sleep(100)",
                                   "newString": "await waitFor(ready)"}, output="Edit applied successfully.",
              start=T0 + 4500, metadata={"diff": "--- a\n+++ b\n@@ -1 +1 @@\n-await sleep(100)\n+await waitFor(ready)\n"}),
        _tool("toolu_04", "task", {"description": "Find auth helpers", "prompt": "Where are the auth helpers?",
                                   "subagent_type": "explore"}, output="In src/auth.", start=T0 + 19_000, ms=9000,
              metadata={"sessionId": CHILD_ID}),
        _tool("toolu_05", "interlatch_search_sessions", {"query": "flaky login"}, output="2 sessions", start=T0 + 29_000),
        _finish(0, 9800, 300, cache_read=21000),  # OpenCode priced it at 0 (a subscription): list price instead
        {"type": "patch", "hash": "a71b", "files": [f"{FOLDER}/test/login.test.ts"]},
    ], end=T0 + 30_000))
    w.message(*_user(T0 + 40_000, "now use gpt and write notes"))
    w.message(*_assistant(T0 + 41_000, [
        {"type": "retry", "attempt": 1, "error": {"name": "APIError", "data": {"message": "Overloaded", "isRetryable": True}},
         "time": {"created": T0 + 41_100}},
        _tool("call_06", "apply_patch", {"patchText": PATCH}, output="Success.", start=T0 + 42_000),
        _tool("call_07", "write", {"filePath": f"{FOLDER}/README.md", "content": "# App\nRun npm test\n"},
              output="Wrote file successfully.", start=T0 + 43_000),
        _tool("call_08", "bash", {"command": "rm -rf build"}, error="Permission denied: rm", start=T0 + 44_000),
        _finish(0, 5000, 200, reasoning=50, reason="stop"),
    ], model="gpt-5.5", provider="openai", end=T0 + 45_000))
    w.message(*_user(T0 + 50_000, "stop"))
    w.message(*_assistant(T0 + 51_000, [{"type": "text", "text": "Stopp"}],
                          error={"name": "MessageAbortedError", "data": {"message": "aborted"}}))

    c = _Writer(db, CHILD_ID)
    c.message(*_user(T0 + 20_000, "Where are the auth helpers?"))
    c.message(*_assistant(T0 + 21_000, [
        _tool("toolu_c1", "grep", {"pattern": "export function auth", "path": f"{FOLDER}/src"}, output="src/auth.ts:3",
              start=T0 + 21_500),
        {"type": "text", "text": "In src/auth."},
        _finish(0.002, 3000, 40, reason="stop"),
    ], model="claude-haiku-4-5", end=T0 + 27_000))

    _Writer(db, EMPTY_ID)  # a session opened and never used: no messages
    db.commit()
    db.close()
    return root


def write_opencode_config(config_dir: Path) -> Path:
    """A global opencode.jsonc with comments, naming the MCP servers the tool names start with."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "opencode.jsonc").write_text("""{
  // my servers
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "interlatch": {"type": "local", "command": ["interlatch", "mcp"]}, /* ours */
    "my.docs": {"type": "remote", "url": "https://example.com/mcp"},
  },
}
""")
    return config_dir


# ------------------------------------------------------------------ OpenCode 2.x (session_v2 + session_message)
V2_ROOT = "ses_ec03ba8bfffePPT0LDoFMif7n3"
V2_CHILD = "ses_ec03b000fffeChildSession01"
V2_FOLDER = "/Users/me/Default Project"

DDL_V2 = """
CREATE TABLE `session_v2` (`id` text PRIMARY KEY, `project_id` text NOT NULL, `workspace_id` text, `parent_id` text,
  `fork_session_id` text, `fork_boundary` text, `slug` text NOT NULL, `directory` text NOT NULL, `path` text,
  `title` text, `version` text NOT NULL, `share_url` text, `summary_additions` integer, `summary_deletions` integer,
  `summary_files` integer, `summary_diffs` text, `metadata` text, `cost` real DEFAULT 0 NOT NULL,
  `tokens_input` integer DEFAULT 0 NOT NULL, `tokens_output` integer DEFAULT 0 NOT NULL,
  `tokens_reasoning` integer DEFAULT 0 NOT NULL, `tokens_cache_read` integer DEFAULT 0 NOT NULL,
  `tokens_cache_write` integer DEFAULT 0 NOT NULL, `revert` text, `permission` text, `agent` text, `model` text,
  `time_created` integer NOT NULL, `time_updated` integer NOT NULL, `time_idle` integer, `time_viewed` integer,
  `idle_outcome` text, `time_compacting` integer, `time_archived` integer, `time_suspended` integer,
  `resume_attempts` integer DEFAULT 0 NOT NULL);
CREATE TABLE `session_message` (`id` text PRIMARY KEY, `session_id` text NOT NULL, `type` text NOT NULL,
  `seq` integer NOT NULL, `time_created` integer NOT NULL, `time_updated` integer NOT NULL, `data` text NOT NULL);
CREATE TABLE `credential` (`id` text PRIMARY KEY, `data` text NOT NULL);
"""


def _v2_tool(cid, name, inp, *, start, ms=100, text="", error=None, metadata=None):
    state = {"status": "error", "input": inp, "error": {"type": "ToolFailure", "message": error}} if error else \
        {"status": "completed", "input": inp, "content": [{"type": "text", "text": text}], "metadata": metadata or {}}
    return {"type": "tool", "id": cid, "name": name, "executed": True, "state": state,
            "time": {"created": start - 10, "ran": start, "completed": start + ms}}


def write_opencode2_home(root: Path) -> Path:
    """OpenCode 2.x as the desktop app 2.0.29 writes it."""
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "opencode.db")
    db.executescript(DDL_V2)
    db.execute("INSERT INTO credential VALUES ('c1', '{\"key\": \"must-never-be-read\"}')")
    for sid, parent, title, t in ((V2_ROOT, None, "Greeting", T0), (V2_CHILD, V2_ROOT, None, T0 + 5_000)):
        db.execute("INSERT INTO session_v2 (id, project_id, parent_id, slug, directory, title, version, agent, model, "
                   "time_created, time_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, "global", parent, "brave-otter", V2_FOLDER, title, "2.0.29", "build",
                    json.dumps({"id": "cat-jam-free", "providerID": "opencode"}), t, t + 30_000))
    model = {"id": "claude-sonnet-4-5", "providerID": "anthropic", "variant": "default"}
    rows = {V2_ROOT: [
        ("user", {"metadata": {"displayText": "hio there"}, "time": {"created": T0}, "text": "hio there",
                  "files": [{"mime": "image/png", "name": "shot.png"}], "agents": []}),
        ("assistant", {"time": {"created": T0 + 100, "streamed": T0 + 1500, "completed": T0 + 2000}, "agent": "build",
                       "model": model, "content": [
                           {"type": "reasoning", "text": "A greeting; look at the project first.",
                            "time": {"created": T0 + 200, "completed": T0 + 300}},
                           {"type": "text", "text": "Hey! Let me look around."},
                           _v2_tool("call_1", "shell", {"command": "git status", "workdir": V2_FOLDER}, start=T0 + 400,
                                    ms=250, text="clean"),
                           _v2_tool("call_2", "read", {"path": "src/app.ts"}, start=T0 + 700, text="const a = 1"),
                           _v2_tool("call_3", "edit", {"path": "src/app.ts", "oldString": "1", "newString": "2"},
                                    start=T0 + 900, text="Edited", metadata={"files": [
                                        {"file": "src/app.ts", "status": "modified", "additions": 1, "deletions": 1}]}),
                           _v2_tool("call_4", "write", {"path": "notes.md", "content": "# Notes\n"}, start=T0 + 1100,
                                    text="Wrote", metadata={"files": [
                                        {"file": "notes.md", "status": "added", "additions": 1, "deletions": 0}]}),
                           _v2_tool("call_5", "shell", {"command": "rm -rf /"}, start=T0 + 1300, error="Permission denied"),
                       ], "finish": "tool-calls", "cost": 0.004,
                       "tokens": {"input": 1000, "output": 50, "reasoning": 10, "cache": {"read": 4000, "write": 0}}}),
        ("assistant", {"time": {"created": T0 + 3000, "completed": T0 + 12_000}, "agent": "build", "model": model,
                       "retry": {"attempt": 1, "at": T0 + 3100, "error": {"type": "APIError", "message": "Overloaded"}},
                       "content": [_v2_tool("call_6", "subagent", {"agent": "explore", "description": "Find the tests",
                                                                   "prompt": "Where are the tests?"},
                                            start=T0 + 4000, ms=8000, text="In test/",
                                            metadata={"sessionID": V2_CHILD, "status": "completed"})],
                       "cost": 0, "tokens": {"input": 1200, "output": 30, "reasoning": 0, "cache": {"read": 4100, "write": 0}}}),
        ("idle", {"time": {"created": T0 + 12_100}, "outcome": "succeeded"}),
        ("shell", {"time": {"created": T0 + 20_000, "completed": T0 + 20_300}, "shellID": "sh_1", "command": "ls",
                   "status": "exited", "exit": 0, "output": {"output": "src\n", "cursor": 4, "size": 4, "truncated": False}}),
        ("user", {"time": {"created": T0 + 21_000}, "text": "now summarize", "files": [], "agents": []}),
        ("compaction", {"time": {"created": T0 + 22_000}, "status": "completed", "reason": "auto",
                        "summary": "We looked at the project.", "recent": "", "model": model,
                        "tokens": {"input": 5000, "output": 100, "reasoning": 0, "cache": {"read": 0, "write": 0}}}),
        ("agent-switched", {"time": {"created": T0 + 23_000}, "agent": "plan", "previous": "build"}),
        ("idle", {"time": {"created": T0 + 24_000}, "outcome": "interrupted"}),
    ], V2_CHILD: [
        ("user", {"time": {"created": T0 + 5_000}, "text": "Where are the tests?", "files": [], "agents": []}),
        ("assistant", {"time": {"created": T0 + 5_100, "completed": T0 + 7_000}, "agent": "explore",
                       "model": {"id": "claude-haiku-4-5", "providerID": "anthropic"}, "content": [
                           _v2_tool("call_c1", "glob", {"pattern": "**/*.test.ts", "path": "test"}, start=T0 + 5_500,
                                    text="test/app.test.ts"),
                           {"type": "text", "text": "In test/"}],
                       "cost": 0.001, "tokens": {"input": 800, "output": 20, "reasoning": 0, "cache": {"read": 0, "write": 0}}}),
    ]}
    for sid, items in rows.items():
        for seq, (kind, data) in enumerate(items, start=1):
            created = data["time"]["created"]
            db.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                       (f"msg_{sid[4:10]}{seq:04d}", sid, kind, seq * 2, created, created, json.dumps(data)))
    db.commit()
    db.close()
    return root
