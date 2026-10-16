"""Checks run as the Japanese-named user after the documented install (user.ps1). Standard library only, ASCII source.

    python ja_check.py prepare   # a Claude Code session in a Japanese-named folder, before `interlatch install`
    python ja_check.py check     # what install did: the session, the hooks through Git Bash and PowerShell, the tasks

Prints one line per check and writes C:\\qa\\ja_check.json.
"""

from __future__ import annotations

import ctypes
import json
import locale
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

HOME = Path.home()
CLAUDE = HOME / ".claude"
DATA = HOME / ".interlatch"
CWD = "C:\\\u4f5c\u696d\\\u30a2\u30d7\u30ea"  # C:\<sagyou>\<apuri>
PROMPT = "\u30ed\u30b0\u30a4\u30f3\u306e\u30c6\u30b9\u30c8\u3092\u76f4\u3057\u3066"  # "fix the login test"
PROJECT_DIR = "".join(c if c.isascii() and c.isalnum() else "-" for c in CWD)  # as Claude Code names it
RESULTS: list[dict] = []


def check(ok: bool, what: str, detail: str = "") -> bool:
    RESULTS.append({"ok": bool(ok), "what": what, "detail": detail})
    print(f"{'ok  ' if ok else 'FAIL'} {what}" + (f"\n     {detail}" if detail else ""), flush=True)
    return ok


def transcript(text: str) -> tuple[str, Path]:
    sid = str(uuid.uuid4())
    base = {"isSidechain": False, "userType": "external", "entrypoint": "cli", "cwd": CWD, "sessionId": sid,
            "version": "2.1.300", "gitBranch": "main"}
    lines = [
        {**base, "type": "user", "uuid": "u1", "parentUuid": None, "timestamp": "2026-10-16T10:00:00.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": text}]}},
        {**base, "type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": "2026-10-16T10:00:05.000Z",
         "requestId": "req1", "message": {"id": "msg_1", "model": "claude-opus-5-5", "role": "assistant",
                                          "content": [{"type": "text", "text": "\u76f4\u3057\u307e\u3057\u305f\u3002"}],
                                          "usage": {"input_tokens": 10, "output_tokens": 5}}},
    ]
    path = CLAUDE / "projects" / PROJECT_DIR / f"{sid}.jsonl"
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


def wait_for(fn, seconds: float):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if found := fn():
            return found
        time.sleep(2)
    return fn()


def raw(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, **kw)


def describe(data: bytes) -> str:
    """How these bytes read: as UTF-8 if they are, else as cp932, with the first non-ASCII bytes."""
    try:
        return "utf-8: " + data.decode("utf-8")[:400]
    except UnicodeDecodeError:
        odd = bytes(b for b in data if b > 0x7F)[:24].hex(" ")
        return f"NOT utf-8 (bytes {odd}); as cp932: " + data.decode("cp932", "replace")[:400]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    phase = sys.argv[1]
    if phase == "prepare":
        sid, path = transcript(PROMPT)
        (Path("C:/qa") / "first_session.txt").write_text(sid, encoding="ascii")
        print(f"wrote {path}")
        return 0

    k32 = ctypes.windll.kernel32
    print(f"python {sys.version.split()[0]} utf8_mode={sys.flags.utf8_mode} locale={locale.getpreferredencoding(False)} "
          f"acp={k32.GetACP()} oemcp={k32.GetOEMCP()} console_out_cp={k32.GetConsoleOutputCP()} home={HOME}")
    exe = shutil.which("interlatch")
    check(bool(exe), "interlatch is on PATH", str(exe))

    first = (Path("C:/qa") / "first_session.txt").read_text(encoding="ascii").strip()
    s = session(first)
    check(bool(s), "install imported the session in the Japanese-named folder", repr(s))
    if s:
        check(s["first_prompt"] == PROMPT, "its Japanese prompt is intact", repr(s["first_prompt"]))
        check(s["project_path"] == CWD, "its project path is the Japanese folder", repr(s["project_path"]))

    settings_file = CLAUDE / "settings.json"
    settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    try:
        command = settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
    except (KeyError, IndexError, TypeError):
        command = ""
    check(bool(command), "install added the SessionEnd hook", repr(command) or json.dumps(settings)[:300])

    git_bash = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Git" / "bin" / "bash.exe"
    for shell, argv in (("Git Bash", [str(git_bash), "-c", command]),
                        ("PowerShell", ["powershell", "-NoProfile", "-Command", command])):
        if not command:
            break
        sid, path = transcript(f"{PROMPT} ({shell})")
        payload = json.dumps({"session_id": sid, "transcript_path": str(path), "cwd": CWD,
                              "hook_event_name": "SessionEnd", "reason": "exit"}, ensure_ascii=False).encode("utf-8")
        proc = raw(argv, input=payload)
        check(proc.returncode == 0, f"the hook runs in {shell}",
              f"exit {proc.returncode}; stdout {describe(proc.stdout)}; stderr {describe(proc.stderr)}")
        got = wait_for(lambda sid=sid: session(sid), 90)
        check(bool(got) and got["first_prompt"].startswith(PROMPT), f"the session the hook ({shell}) named was recorded",
              repr(got))

    for task in ("\\Interlatch\\Sync", "\\Interlatch\\Dashboard"):
        proc = raw(["schtasks", "/Query", "/TN", task, "/V", "/FO", "CSV", "/NH"])
        check(proc.returncode == 0, f"task {task} was registered by a standard user",
              f"stdout {describe(proc.stdout)}; stderr {describe(proc.stderr)}")
    proc = raw(["schtasks", "/Query", "/TN", "\\Interlatch\\NoSuchTask"])
    print("schtasks error bytes:", describe(proc.stderr))

    (Path("C:/qa") / "ja_check.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = [r["what"] for r in RESULTS if not r["ok"]]
    print(f"\n{len(failed)} failed" + (": " + "; ".join(failed) if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
