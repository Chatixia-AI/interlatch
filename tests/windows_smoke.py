"""The Windows install for real, on CI's Windows runner: run after `uv tool install <wheel>`, with a Python outside the
project (`uv run` would put the project's own interlatch.exe first on PATH). Standard library only.

    python tests/windows_smoke.py

`interlatch install --yes`, then: the session it imported (Japanese text intact), the hook command Claude Code runs,
through Git Bash and through PowerShell, the two Task Scheduler tasks and the dashboard they start, the dashboard's
Update button while an MCP server holds interlatch.exe open, and `interlatch uninstall` ending it all. Every check
runs; it fails at the end if any failed.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

HOME = Path.home()
CLAUDE = HOME / ".claude"
DATA = HOME / ".interlatch"
BASE = "http://127.0.0.1:11524"
URL = f"{BASE}/api/jobs"
CWD = "C:\\work\\demo-app"
FAILED: list[str] = []


def check(ok: bool, what: str, detail: str = "") -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {what}" + (f"\n     {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        FAILED.append(what)
    return ok


def run(cmd, **kw) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    print(f"$ {cmd if isinstance(cmd, str) else ' '.join(cmd)}  -> {proc.returncode}\n{proc.stdout}{proc.stderr}", flush=True)
    return proc


def transcript(text: str) -> tuple[str, Path]:
    """A one-turn Claude Code session in ~/.claude/projects, as Claude Code on Windows names its folder."""
    sid = str(uuid.uuid4())
    base = {"isSidechain": False, "userType": "external", "entrypoint": "cli", "cwd": CWD, "sessionId": sid,
            "version": "2.1.300", "gitBranch": "main"}
    lines = [
        {**base, "type": "user", "uuid": "u1", "parentUuid": None, "timestamp": "2026-10-11T10:00:00.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": text}]}},
        {**base, "type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": "2026-10-11T10:00:05.000Z",
         "requestId": "req1", "message": {"id": "msg_1", "model": "claude-opus-5-5", "role": "assistant",
                                          "content": [{"type": "text", "text": "Done."}],
                                          "usage": {"input_tokens": 10, "output_tokens": 5}}},
    ]
    path = CLAUDE / "projects" / "C--work-demo-app" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
    return sid, path


def session(sid: str) -> dict | None:
    db = DATA / "chronicle.db"
    if not db.exists():
        return None
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT first_prompt, project_name, project_path FROM sessions WHERE id = ?", (sid,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return dict(row) if row else None


def wait_for(fn, seconds: float, every: float = 2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = fn()
        if found:
            return found
        time.sleep(every)
    return fn()


def api(path: str, body: dict | None = None) -> dict | None:
    """The dashboard's JSON, or None while it does not answer. A POST carries the header its page sends."""
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Chronicle": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def dashboard_up() -> bool:
    return api("/api/jobs") is not None


def listener() -> str:
    """The pid of the process serving :11524 ("" for none). A dashboard restarting is seen by this changing, not by
    a gap: Windows retries a refused connection to localhost for about 2 seconds, longer than the restart takes."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-NetTCPConnection -LocalPort 11524 -State Listen "
                          "-ErrorAction SilentlyContinue).OwningProcess"], capture_output=True, text=True).stdout.split()
    return out[0] if out else ""


def update_job() -> dict | None:
    """The Update job once it has finished (done or error)."""
    job = ((api("/api/jobs") or {}).get("jobs") or {}).get("update")
    return job if job and job.get("state") != "running" else None


def hook_payload(sid: str, path: Path) -> str:
    return json.dumps({"session_id": sid, "transcript_path": str(path), "cwd": CWD, "hook_event_name": "SessionEnd",
                       "reason": "exit"})


def show_logs() -> None:
    for name in ("ui.err.log", "ui.out.log", "sync.err.log", "sync.out.log", "hooks.log"):
        log = DATA / "logs" / name
        if log.exists():
            print(f"--- {log}\n{log.read_text(encoding='utf-8', errors='replace')[-3000:]}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # the log is UTF-8; a pipe's default here is cp1252
    exe = shutil.which("interlatch")
    if not check(bool(exe), "interlatch is on PATH", os.environ.get("PATH", "")):
        return 1
    print(f"interlatch: {exe}")
    tools = run(["uv", "tool", "dir", "--bin"]).stdout.strip()
    if not check(Path(exe).parent.resolve() == Path(tools).resolve(), "it is the uv tool's interlatch", f"{exe} vs {tools}"):
        return 1
    run([exe, "--version"])

    first, _ = transcript("ログインのテストを直して")  # read in the ANSI code page, this would come back garbled
    proc = run([exe, "install", "--yes", "--no-mcp"])
    check(proc.returncode == 0, "interlatch install --yes")
    s = session(first)
    check(bool(s), "install imported the session already in ~/.claude", str(s))
    if s:
        check(s["first_prompt"] == "ログインのテストを直して", "its Japanese prompt is intact", repr(s["first_prompt"]))
        check(s["project_name"] == "demo-app", "its project is named after the folder", repr(s))

    settings = json.loads((CLAUDE / "settings.json").read_text(encoding="utf-8"))
    command = settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
    print(f"SessionEnd hook: {command}")
    check("\\" not in command and command.endswith(" hook session-end"), "the hook's command has no backslashes", command)
    check(Path(command.split()[0]).resolve() == Path(exe).resolve(), "the hook runs the uv tool's interlatch", command)

    # the hook as Claude Code runs it: Git Bash, or PowerShell without Git for Windows (a bare `bash` may be WSL's)
    git_bash = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Git" / "bin" / "bash.exe"
    for shell, argv in (("Git Bash", [str(git_bash), "-c", command]),
                        ("PowerShell", ["powershell", "-NoProfile", "-Command", command])):
        sid, path = transcript(f"recorded by the hook, through {shell}")
        proc = run(argv, input=hook_payload(sid, path))
        check(proc.returncode == 0, f"the hook runs in {shell}")
        check(bool(wait_for(lambda sid=sid: session(sid), 90)), f"the session the hook ({shell}) named was recorded")

    for task in ("\\Interlatch\\Sync", "\\Interlatch\\Dashboard"):
        check(run(["schtasks", "/Query", "/TN", task, "/V", "/FO", "LIST"]).returncode == 0, f"task {task} exists")
    up = wait_for(dashboard_up, 120)
    check(up, "the Dashboard task serves the dashboard at :11524")
    if not up:
        show_logs()
    proc = run([exe, "status"])
    check("background sync agent" in proc.stdout, "interlatch status reports the background agents")

    # the dashboard's Update button (uv tool upgrade --reinstall, for an install from a file) while Claude Code holds
    # the MCP server open (interlatch.exe mcp); then the dashboard restarts through its task's runner
    mcp = subprocess.Popen([exe, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        before = listener()
        started = api("/api/update", {})
        check(bool(started and started.get("started")), "the dashboard's Update starts", str(started))
        job = wait_for(update_job, 600, every=0.25)
        check(bool(job and job["state"] == "done"), "the update ran while interlatch.exe and the dashboard run", str(job))
        check(bool(wait_for(lambda: listener() not in ("", before), 90)), "the dashboard restarts after it",
              f"still pid {before}")
        check(wait_for(dashboard_up, 30), "... and answers")
    finally:
        mcp.stdin.close()
        mcp.wait(timeout=30)
    check(run([exe, "--version"]).returncode == 0, "interlatch still runs after the update")

    proc = run([exe, "uninstall"])
    check(proc.returncode == 0, "interlatch uninstall")
    for task in ("\\Interlatch\\Sync", "\\Interlatch\\Dashboard"):
        check(run(["schtasks", "/Query", "/TN", task]).returncode != 0, f"task {task} is gone")
    check(wait_for(lambda: not dashboard_up(), 30), "ending the task stopped the dashboard too")
    settings = json.loads((CLAUDE / "settings.json").read_text(encoding="utf-8"))
    check("hooks" not in settings, "the hooks are gone", json.dumps(settings))

    if FAILED:
        show_logs()
        print(f"\n{len(FAILED)} failed: " + "; ".join(FAILED))
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
