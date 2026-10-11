"""Codex Cloud tasks (chatgpt.com/codex), read through the codex CLI with the user's own Codex login.

`codex cloud list --json` gives each task's title, environment (the repository), status, change counts and last
update; `codex cloud diff <id>` gives its diff. The CLI does not give a task's conversation, so a cloud task becomes
a session without prompts: the title, the files it changed and the diff, with a link to the task. Interlatch never
reads Codex's login state itself; the CLI does the talking.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time

from .copilot_parser import _Builder, finish_session
from .hooks import internal_env
from .parser import FileStat, ParsedSession
from .util import safe_text

log = logging.getLogger("interlatch.codex_cloud")

CLOUD_PARSER_VERSION = 1
STATUS_KEY = "codex-cloud:status"  # kv: the last listing's outcome, for the Sources page
MAX_PAGES = 25  # 20 tasks a page
_DIFF_FILE_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")


class CloudError(Exception):
    """The codex CLI could not list tasks (not logged in, offline, an old CLI without `codex cloud`)."""


def _run(binary: str, *args: str, timeout: float = 120) -> str:
    try:
        proc = subprocess.run([binary, "cloud", *args], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=internal_env())
    except subprocess.TimeoutExpired as exc:
        raise CloudError(f"codex cloud {args[0]} timed out") from exc
    except OSError as exc:
        raise CloudError(f"could not run codex: {exc}") from exc
    if proc.returncode:
        msg = (proc.stderr or proc.stdout).strip().splitlines()
        raise CloudError(msg[-1][:300] if msg else f"codex cloud {args[0]} exited with {proc.returncode}")
    return proc.stdout


def list_tasks(binary: str) -> list[dict]:
    """Every task the account can see, newest first."""
    tasks, cursor = [], None
    for _ in range(MAX_PAGES):
        out = _run(binary, "list", "--json", "--limit", "20", *(["--cursor", cursor] if cursor else []))
        try:
            page = json.loads(out)
        except ValueError as exc:
            raise CloudError("codex cloud list did not return JSON (update the Codex CLI)") from exc
        tasks += [t for t in page.get("tasks") or [] if isinstance(t, dict) and t.get("id")]
        cursor = page.get("cursor")
        if not cursor:
            break
    return tasks


def task_diff(binary: str, task_id: str) -> str | None:
    """The task's diff, or None when it has none yet (still running) or the CLI fails."""
    try:
        return _run(binary, "diff", task_id) or None
    except CloudError as exc:
        log.info("no diff for %s: %s", task_id, exc)
        return None


def diff_files(diff: str | None) -> dict[str, tuple[int, int]]:
    """Lines added and removed per file of a unified git diff."""
    files: dict[str, list[int]] = {}
    cur = None
    for line in (diff or "").splitlines():
        m = _DIFF_FILE_RE.match(line)
        if m:
            cur = files.setdefault(m.group(2), [0, 0])
        elif cur is None or line.startswith(("+++", "---")):
            continue
        elif line.startswith("+"):
            cur[0] += 1
        elif line.startswith("-"):
            cur[1] += 1
    return {p: (a, r) for p, (a, r) in files.items()}


def _ms(ts: str | None) -> str | None:
    """Codex Cloud times have nanoseconds; the rest of Interlatch stores milliseconds ("…:28.652Z")."""
    return re.sub(r"(\.\d{3})\d+", r"\1", ts) if ts else None


def task_signature(task: dict) -> str:
    return f"{task.get('updated_at')}|{task.get('status')}|{task.get('attempt_total')}|v{CLOUD_PARSER_VERSION}"


def parse_cloud_task(task: dict, diff: str | None, project_path: str | None) -> ParsedSession:
    ps = ParsedSession(id=task["id"])
    ps.project_path = project_path
    ps.entrypoint = "codex:cloud"
    ps.custom_title = safe_text(task.get("title") or "") or None
    ps.artifacts.append({"title": "Open in Codex Cloud", "url": task.get("url"), "ts": task.get("updated_at")})
    ts = _ms(task.get("updated_at"))
    b = _Builder(ps)
    env = task.get("environment_label") or task.get("environment_id") or "no environment"
    s = task.get("summary") or {}
    head = f"Codex Cloud task · {env} · {task.get('status') or 'unknown'}"
    if s.get("files_changed"):
        head += f" · +{s.get('lines_added', 0)} −{s.get('lines_removed', 0)} in {s['files_changed']} files"
    if (task.get("attempt_total") or 1) > 1:
        head += f" · {task['attempt_total']} attempts"
    b.event(ts, "system", "meta", head)
    files = diff_files(diff)
    for path, (added, removed) in files.items():
        ps.files[path] = FileStat(edits=1, lines_added=added, lines_removed=removed)
    if files:
        listing = "\n".join(f"- {p} (+{a} −{r})" for p, (a, r) in sorted(files.items()))
        b.event(ts, "assistant", "text", f"Changed {len(files)} file{'s' if len(files) != 1 else ''}:\n{listing}")
        b.event(ts, "assistant", "text", f"```diff\n{diff.rstrip()}\n```")
    b.event(ts, "system", "meta", "Codex's CLI does not share a cloud task's conversation; open the task for it.")
    return finish_session(ps, [ts] if ts else [])


def save_status(conn, **status) -> None:
    from .db import kv_set

    kv_set(conn, STATUS_KEY, json.dumps({**status, "checked_at": time.time()}))


def load_status(conn) -> dict:
    from .db import kv_get

    try:
        return json.loads(kv_get(conn, STATUS_KEY) or "{}")
    except ValueError:
        return {}
