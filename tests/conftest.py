"""Test fixtures: an isolated Interlatch home + a fake ~/.claude with a synthetic transcript."""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

SID = "11111111-2222-3333-4444-555555555555"
SUB = "a0123456789abcdef"
CWD = "/Users/test/Projects/demo-app"
PROJECT_DIR = "-Users-test-Projects-demo-app"
SECRET = "sk-ant-api03-SECRETSECRETSECRETSECRETSECRET"


def _line(**kw) -> str:
    base = {
        "isSidechain": False, "userType": "external", "entrypoint": "cli", "cwd": CWD, "sessionId": SID,
        "version": "2.1.282", "gitBranch": "main",
    }
    base.update(kw)
    return json.dumps(base)


def _usage(inp, out, read=0, w5=0, w1=0):
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": w5 + w1,
            "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}}


def main_transcript() -> list[str]:
    L = []
    L.append(json.dumps({"type": "mode", "mode": "normal", "sessionId": SID}))
    L.append(_line(type="user", uuid="u1", parentUuid=None, timestamp="2026-09-20T10:00:00.000Z", origin={"kind": "human"},
                   permissionMode="auto",
                   message={"role": "user", "content": [
                       {"type": "text", "text": "<ide_opened_file>The user opened app.py</ide_opened_file>"},
                       {"type": "text", "text": f"Fix the failing login test. My key is {SECRET}"}]}))
    # one API response split over three lines, usage growing as it streams
    for i, (block, out) in enumerate([
        ({"type": "thinking", "thinking": "", "signature": "x"}, 5),
        ({"type": "text", "text": "I'll run the tests first."}, 20),
        ({"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "pytest tests/test_login.py -x", "description": "Run tests"}}, 40),
    ]):
        L.append(_line(type="assistant", uuid=f"a1-{i}", timestamp=f"2026-09-20T10:00:0{5 + i}.000Z", requestId="req1",
                       message={"id": "msg_1", "model": "claude-opus-5-5", "role": "assistant", "content": [block],
                                "usage": _usage(10, out, read=1000, w5=500)}))
    L.append(_line(type="user", uuid="u2", timestamp="2026-09-20T10:00:20.000Z",
                   message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "1 failed: AssertionError token expired", "is_error": False}]},
                   toolUseResult={"stdout": "1 failed", "stderr": "", "interrupted": False, "isImage": False}))
    L.append(_line(type="assistant", uuid="a2", timestamp="2026-09-20T10:01:00.000Z",
                   message={"id": "msg_2", "model": "claude-opus-5-5", "role": "assistant", "content": [
                       {"type": "tool_use", "id": "toolu_2", "name": "Edit", "input": {"file_path": f"{CWD}/auth.py", "old_string": "a", "new_string": "b"}}],
                       "usage": _usage(5, 30, read=1600)}))
    L.append(_line(type="user", uuid="u3", timestamp="2026-09-20T10:01:02.000Z",
                   message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": "The file was updated"}]},
                   toolUseResult={"filePath": f"{CWD}/auth.py", "structuredPatch": [{"lines": [" ctx", "-old", "+new1", "+new2"]}]}))
    L.append(_line(type="assistant", uuid="a3", timestamp="2026-09-20T10:01:10.000Z",
                   message={"id": "msg_3", "model": "claude-opus-5-5", "role": "assistant", "content": [
                       {"type": "tool_use", "id": "toolu_3", "name": "Read", "input": {"file_path": f"{CWD}/missing.py"}}],
                       "usage": _usage(5, 10, read=1700)}))
    L.append(_line(type="user", uuid="u4", timestamp="2026-09-20T10:01:11.000Z",
                   message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_3", "content": "File does not exist.", "is_error": True}]}))
    L.append(_line(type="attachment", uuid="at1", timestamp="2026-09-20T10:01:30.000Z",
                   attachment={"type": "queued_command", "prompt": [{"type": "text", "text": "also check the logout flow"}],
                               "commandMode": "prompt", "origin": {"kind": "human"}}))
    L.append(_line(type="assistant", uuid="a4", timestamp="2026-09-20T10:02:00.000Z",
                   message={"id": "msg_4", "model": "claude-opus-5-5", "role": "assistant", "content": [
                       {"type": "tool_use", "id": "toolu_4", "name": "Agent", "input": {"subagent_type": "Explore", "description": "Find logout code", "prompt": "find it"}}],
                       "usage": _usage(5, 25, read=1800)}))
    L.append(_line(type="user", uuid="u5", timestamp="2026-09-20T10:03:00.000Z",
                   message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_4", "content": [{"type": "text", "text": "Logout lives in session.py"}]}]},
                   toolUseResult={"status": "completed", "agentId": SUB, "agentType": "Explore", "resolvedModel": "claude-opus-5-5[1m]"}))
    L.append(_line(type="user", uuid="u6", timestamp="2026-09-20T10:04:00.000Z", message={"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}))
    L.append(_line(type="user", uuid="u7", timestamp="2026-09-20T10:05:00.000Z",
                   message={"role": "user", "content": "<command-name>/compact</command-name>\n<command-message>compact</command-message>\n<command-args></command-args>"}))
    L.append(_line(type="system", subtype="compact_boundary", uuid="s1", timestamp="2026-09-20T10:05:30.000Z", content="Conversation compacted",
                   compactMetadata={"trigger": "manual", "preTokens": 150000}))
    L.append(_line(type="user", uuid="u8", timestamp="2026-09-20T10:05:31.000Z", isCompactSummary=True,
                   message={"role": "user", "content": "This session is being continued from a previous conversation that ran out of context."}))
    L.append(_line(type="assistant", uuid="a5", timestamp="2026-09-20T10:06:00.000Z",
                   message={"id": "msg_5", "model": "<synthetic>", "role": "assistant", "content": [{"type": "text", "text": "No response requested."}],
                            "usage": _usage(0, 0)}))
    L.append(_line(type="assistant", uuid="a6", timestamp="2026-09-20T10:07:00.000Z",
                   message={"id": "msg_6", "model": "claude-opus-5-5", "role": "assistant", "content": [
                       {"type": "text", "text": "Fixed: the token TTL was compared in seconds vs ms. Tests pass."}], "usage": _usage(5, 50, read=2000)}))
    L.append(json.dumps({"type": "ai-title", "sessionId": SID, "aiTitle": "Fix login token expiry"}))
    L.append(json.dumps({"type": "pr-link", "sessionId": SID, "prNumber": 7, "prUrl": "https://github.com/x/y/pull/7", "prRepository": "x/y", "timestamp": "2026-09-20T10:08:00.000Z"}))
    L.append(json.dumps({"type": "cost-state", "sessionId": SID, "totalCostUSD": 0.5, "startTime": 1}))
    L.append('{"type": "user", "torn line')  # a partially written last line must be ignored
    return L


def subagent_transcript() -> list[str]:
    return [
        _line(type="user", uuid="s-u1", isSidechain=True, agentId=SUB, timestamp="2026-09-20T10:02:05.000Z",
              message={"role": "user", "content": "find it"}),
        _line(type="assistant", uuid="s-a1", isSidechain=True, agentId=SUB, timestamp="2026-09-20T10:02:10.000Z",
              message={"id": "msg_s1", "model": "claude-haiku-4-5-20251001", "role": "assistant", "content": [
                  {"type": "tool_use", "id": "toolu_s1", "name": "Grep", "input": {"pattern": "logout"}}], "usage": _usage(100, 20)}),
        _line(type="user", uuid="s-u2", isSidechain=True, agentId=SUB, timestamp="2026-09-20T10:02:12.000Z",
              message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_s1", "content": "session.py:10"}]}),
    ]


def write_fake_claude_tree(claude_dir: Path) -> Path:
    proj = claude_dir / "projects" / PROJECT_DIR
    proj.mkdir(parents=True, exist_ok=True)
    main = proj / f"{SID}.jsonl"
    main.write_text("\n".join(main_transcript()) + "\n")
    sub_dir = proj / SID / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / f"agent-{SUB}.jsonl").write_text("\n".join(subagent_transcript()) + "\n")
    (sub_dir / f"agent-{SUB}.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Find logout code", "toolUseId": "toolu_4"}))
    mem = proj / "memory"
    mem.mkdir(exist_ok=True)
    (mem / "MEMORY.md").write_text("- [Deploy](deploy.md)\n")
    (mem / "deploy.md").write_text(textwrap.dedent("""\
        ---
        name: deploy
        description: demo-app deploys with `make ship` to Fly.io
        metadata:
          type: project
        ---

        Run `make ship`; it builds the image and runs flyctl deploy.
        """))
    (claude_dir / "history.jsonl").write_text("\n".join([
        json.dumps({"display": "set up git on this machine", "timestamp": 1774931730912, "project": "/Users/test", "sessionId": "old-session-1"}),
        json.dumps({"display": "it says xcode-select missing", "timestamp": 1774931773838, "project": "/Users/test", "sessionId": "old-session-1"}),
        json.dumps({"display": "hello", "timestamp": 1774931773838, "project": CWD, "sessionId": SID}),
    ]) + "\n")
    return main


FAKE_CLAUDE = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps({"args": args, "prompt_chars": len(prompt), "prompt_head": prompt[:4000]}) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
system = args[args.index("--system-prompt") + 1] if "--system-prompt" in args else ""
if mode == "hang":
    import time
    time.sleep(30)
if mode == "limit":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "Claude AI usage limit reached|1790000000"}))
    sys.exit(1)
if "imported chat history" in system:  # screening (screen.py): the verdict follows the title; "unanswered" is left out
    found = __import__("re").findall(r"^#(\d+) · .*? · .*? · (.*)$", prompt, __import__("re").M)
    data = {"chats": [{"n": int(n), "verdict": "analyze" if "deploy" in t.lower() else "maybe" if "maybe" in t.lower() else "skip",
                       "topic": t.lower()[:20], "reason": f"because: {t}"} for n, t in found if "unanswered" not in t]
            + [{"n": 999, "verdict": "analyze", "topic": "x", "reason": "out of range"}, {"n": 1, "verdict": "bogus"}]}
elif "into themes" in system:
    ids = [int(x) for x in __import__("re").findall(r'"id": (\d+)', prompt)]
    data = {"themes": [{"name": "Testing", "description": "How tests run", "term_ids": ids[:1] + [999999]},
                       {"name": "Testing", "description": "a duplicate name merges", "term_ids": ids[:1]},
                       {"name": "", "description": "dropped: no name", "term_ids": ids}]}
elif "glossary" in system:
    ids = [int(x) for x in __import__("re").findall(r'"id": (\d+)', prompt)]
    everywhere = "cross-project" in system
    data = {"terms": [
        {"term": "pytest", "aliases": ["py.test"], "category": "tool", "definition": "Python test runner" + (" used everywhere" if everywhere else ""),
         "context": "runs the login tests" if not everywhere else "the default test runner", "related": ["Token TTL"], "sources": ids[:1]},
        {"term": "Token TTL", "aliases": ["TTL"], "category": "concept", "definition": "Lifetime of an auth token",
         "context": "compared in seconds vs ms", "related": [], "sources": ids[:2] + [999999]},
        {"term": "", "category": "tool", "definition": "dropped: empty term"},
        {"term": "pytest", "category": "tool", "definition": "duplicate in the same reply is dropped"},
    ][: (1 if everywhere else 4)]}
elif "weekly engineering review" in system:
    data = {"headline": "Fixed login", "tldr": ["Login fixed", "TTL units learned", "Logout next", "dropped: only 3 kept"], "summary": "A good week.", "themes": [{"title": "Auth", "detail": "Token work", "projects": ["demo-app"]}],
            "accomplishments": ["Fixed TTL bug"], "learnings": ["TTL units"], "open_threads": ["logout"], "friction": [], "suggestions": ["Add a CLAUDE.md rule"]}
elif "knowledge base" in system or "playbook" in system:
    ids = [int(x) for x in __import__("re").findall(r'"id": (\d+)', prompt)]
    data = {"overview": "Demo app overview.", "sections": [{"title": "Gotchas & fixes", "items": [{"text": "Token TTL unit mismatch.", "sources": ids[:2]}]}], "superseded_ids": []}
    if mode == "badkb":
        data = {"overview": 7, "sections": [{"title": "T", "items": ["plain string bullet", 3, {"text": "ok", "sources": ["2", "x"]}]}, "junk"], "superseded_ids": "nope"}
    if mode == "supersede":
        data["superseded_ids"] = ids
    if mode == "superseded-json":  # the current reply shape, driven by the test
        data.pop("superseded_ids")
        data["superseded"] = json.loads(os.environ["FAKE_SUPERSEDED"])
        data["sections"][0]["items"][0]["sources"] = json.loads(os.environ.get("FAKE_SOURCES", "null")) or ids[:2]
    if mode == "diagram":  # one supported connection, one that cites an item the model was never shown, one loose part
        data["diagram"] = {"nodes": [{"id": "api", "label": "Auth API", "kind": "component", "note": "auth.py", "sources": ids[:1]},
                                     {"id": "db", "label": "Token store", "kind": "store", "note": "", "sources": ids[:1]},
                                     {"id": "lone", "label": "Unconnected", "kind": "component", "note": "", "sources": ids[:1]}],
                           "edges": [{"from": "api", "to": "db", "label": "writes tokens", "sources": ids[:1]},
                                     {"from": "db", "to": "api", "label": "invented", "sources": [999999]}]}
elif "part " in prompt and "<transcript_part" in prompt:
    n = __import__("re").search(r'<transcript_part index="(\d+)"', prompt).group(1)
    data = {"summary": "part notes", "highlights": ["h"], "knowledge": [{"kind": "gotcha", "title": f"Part {n} gotcha", "body": "b", "tags": ["x"], "scope": "project", "confidence": "low", "evidence": "e"}], "open_threads": [], "friction": [], "tags": ["t"]}
elif "<part_notes>" in prompt:  # the merge names the items it keeps (FAKE_REDUCE_IDS), by default the last part's
    ids = __import__("re").findall(r'"id": "(p\d+\.\d+)"', prompt)
    data = {"title": "Long session", "summary": "merged", "goal": "g", "outcome": "completed", "outcome_note": "n",
            "work_types": [], "tags": [], "highlights": [], "open_threads": [], "friction": [], "sentiment": "neutral",
            "knowledge_ids": json.loads(os.environ["FAKE_REDUCE_IDS"]) if "FAKE_REDUCE_IDS" in os.environ else ids[-1:]}
else:
    data = {
        "title": "Fixed login token expiry bug",
        "summary": "The developer fixed a failing login test.",
        "goal": "Make login tests pass",
        "outcome": "completed",
        "outcome_note": "Tests pass.",
        "work_types": ["bugfix", "not-a-type"],
        "tags": ["Auth", "pytest"],
        "highlights": ["Found TTL unit mismatch"],
        "knowledge": [
            {"kind": "fix", "title": "Token TTL compared in seconds vs ms", "body": "auth.py compared TTL in seconds against ms timestamps.", "tags": ["auth"], "scope": "project", "confidence": "high", "evidence": "tests passed"},
            {"kind": "bogus-kind", "title": "Unknown kinds become learnings", "body": "x", "scope": "global", "confidence": "weird"},
            {"kind": "fix", "title": ""},
        ],
        "open_threads": ["check logout flow"],
        "friction": ["read a missing file", {"kind": "tool_error", "note": "Read failed"}],
        "sentiment": "positive",
    }
text = json.dumps(data)
if mode == "fenced":
    text = "Here you go:\n```json\n" + text + "\n```"
if mode == "wrapped":
    text = json.dumps({"parameter": data})
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text, "total_cost_usd": 0.01,
                  "duration_ms": 5, "usage": {"input_tokens": 10, "output_tokens": 20},
                  "modelUsage": {"claude-haiku-4-5": {"outputTokens": 1}, "claude-sonnet-5": {"outputTokens": 20}}}))
'''


@pytest.fixture(autouse=True)
def no_real_home(tmp_path_factory, monkeypatch):
    """No test reads or writes this computer's own state: HOME is a scratch folder (so ~/.interlatch,
    ~/.claude-chronicle, ~/.claude.json, ~/.codex and the rest are too), and no INTERLATCH_* or CHRONICLE_* variable
    from the shell that runs the tests reaches them. A test that wants one sets it."""
    for name in list(os.environ):
        if name.startswith(("INTERLATCH_", "CHRONICLE_")) and name != "CHRONICLE_TEST_PG":
            monkeypatch.delenv(name)
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)  # which launchd agent this process is: none
    home = str(tmp_path_factory.mktemp("user-home"))
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)  # Windows' home folder (Path.home())
    monkeypatch.setenv("APPDATA", str(Path(home) / "AppData" / "Roaming"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)  # Linux apps' settings: under HOME, as above


AGENT_MANAGERS = ("launchctl", "systemctl", "schtasks")


@pytest.fixture(autouse=True)
def no_real_background_agents(tmp_path_factory, monkeypatch):
    """No test reaches this computer's own launchd, systemd or Task Scheduler agents: `chronicle install --no-ui` in a
    test once booted out the developer's real dashboard. Agent files go to a scratch folder, and launchctl, systemctl
    and schtasks are never run (a test that wants to see those calls patches subprocess.run itself, over this)."""
    import subprocess

    from chronicle import install

    agents = tmp_path_factory.mktemp("agents")
    monkeypatch.setattr(install, "plist_path", lambda label=install.LAUNCHD_LABEL: agents / f"{label}.plist")
    monkeypatch.setattr(install, "systemd_dir", lambda: agents / "systemd")
    real_run = subprocess.run

    def run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and Path(str(cmd[0])).name in AGENT_MANAGERS:
            print_ = "print" in cmd or "is-active" in cmd or "/Query" in cmd  # asking about an agent: there is none
            return subprocess.CompletedProcess(cmd, 113 if print_ else 0, "" if kwargs.get("text") else b"",
                                               "" if kwargs.get("text") else b"")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    real_popen = subprocess.Popen

    class Popen(real_popen):  # update.restart() asks launchd to restart the dashboard with a detached launchctl
        def __init__(self, cmd, *args, **kwargs):
            if isinstance(cmd, (list, tuple)) and cmd and Path(str(cmd[0])).name in AGENT_MANAGERS:
                cmd = [sys.executable, "-c", ""]
            super().__init__(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Popen)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "chronicle"
    claude_dir = tmp_path / "claude"
    main = write_fake_claude_tree(claude_dir)
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text(FAKE_CLAUDE.replace("{python}", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    home.mkdir()
    (home / "config.toml").write_text(textwrap.dedent(f"""\
        [sources]
        claude_dirs = ["{claude_dir}"]
        codex_dirs = []
        [analysis]
        auto = true
        claude_bin = "{fake}"
        idle_minutes = 0
        concurrency = 1
        [synthesis]
        min_new_items = 1
        """))
    monkeypatch.setenv("INTERLATCH_HOME", str(home))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "fake_claude.log"))
    # never read the real Copilot / VS Code / Bob / Antigravity stores from tests
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "no-copilot"))
    monkeypatch.setenv("BOB_HOME", str(tmp_path / "no-bob"))
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(tmp_path / "no-antigravity"))
    monkeypatch.setattr("chronicle.connectors.vscode_user_dirs", lambda: [])
    from chronicle.config import load_config

    cfg = load_config(home)
    cfg.ensure_dirs()
    return {"cfg": cfg, "home": home, "claude_dir": claude_dir, "main": main, "tmp": tmp_path, "fake": fake}


@pytest.fixture()
def synced(env):
    from chronicle.db import connect
    from chronicle.ingest import sync

    conn = connect(env["cfg"].db_path)
    report = sync(env["cfg"], conn)
    env["conn"] = conn
    env["report"] = report
    yield env
    conn.close()


def fake_log(env) -> list[dict]:
    path = env["tmp"] / "fake_claude.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


os.environ.setdefault("TZ", "UTC")
