"""Parse IBM Bob tasks (~/.bob/db/bob.db) into the same model as Claude Code sessions.

bob.db holds `tasks` (directory, title, git info, costs JSON, status) and `messages` (one chat message per row in
`data`). Tool calls come in two shapes: OpenAI-style `tool_calls[].function` answered by a `tool_call_id`, and Bob's
own `toolCalls[]` ({id, name, arguments}) answered by a tool message whose `toolUsage.signature` names the call.
A subagent's whole conversation rides inside its spawn_subagent result (`messages`, `_meta.subagentId`). Each
assistant message reports its own spend in `_meta.spend` (cost, contextTokens). The database also stores login
state elsewhere in ~/.bob; only these two tables are read, read-only.
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
import sqlite3
from pathlib import Path

from .copilot_parser import _Builder, _args, estimate_cost, finish_session, ms_to_iso, uri_path
from .parser import ApiCall, FileStat, ParsedSession, Subagent
from .pricing import normalize_model
from .util import one_line, safe_text

log = logging.getLogger("interlatch.bob")

BOB_PARSER_VERSION = 2  # 2: Bob's own toolCalls shape, subagents, per-message spend, relative paths
_ENV_RE = re.compile(r"<environment_details>.*?</environment_details>", re.S)
_TASK_RE = re.compile(r"^\s*<(?:task|user_message)>(.*?)</(?:task|user_message)>\s*$", re.S)


def bob_db(home: Path) -> Path:
    return home / "db" / "bob.db"


def load_tasks(home: Path) -> list[dict]:
    """Tasks with their messages, oldest message first. Read-only; returns [] if the database is unreadable."""
    db = bob_db(home)
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks ORDER BY created_at")]
        msgs: dict[str, list[dict]] = {}
        for r in conn.execute("SELECT id, task_id, role, data, created_at FROM messages ORDER BY created_at, id"):
            msgs.setdefault(r["task_id"], []).append(dict(r))
        conn.close()
    except sqlite3.Error as exc:
        log.warning("bob.db unreadable: %s", exc)
        return []
    for t in tasks:
        t["messages"] = msgs.get(t["id"], [])
    return tasks


def task_folder(task: dict) -> str | None:
    """The folder a task ran in: its directory, else its project (a file: URI)."""
    return uri_path(task.get("directory") or task.get("project_id") or "") or None


def task_signature(task: dict) -> str:
    last = task["messages"][-1] if task["messages"] else {}
    return f"{task.get('updated_at')}:{len(task['messages'])}:{last.get('id')}:{task.get('status')}"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text", "output_text"))
    return ""


def _user_text(content) -> str:
    text = _ENV_RE.sub("", _text(content)).strip()
    m = _TASK_RE.match(text)
    return m.group(1).strip() if m else text


def _calls(d: dict) -> list[tuple[str | None, str, dict]]:
    """(call id, tool name, arguments) for each tool call in an assistant message, in either shape."""
    out = []
    for tc in d.get("tool_calls") or []:
        fn = tc.get("function") if isinstance(tc, dict) else None
        if isinstance(fn, dict):
            out.append((tc.get("id"), fn.get("name") or "tool", _args(fn.get("arguments"))))
    for tc in d.get("toolCalls") or []:
        if isinstance(tc, dict):
            out.append((tc.get("id"), tc.get("name") or "tool", _args(tc.get("arguments"))))
    return out


def _signature(d: dict) -> dict:
    usage = d.get("toolUsage")
    sig = usage.get("signature") if isinstance(usage, dict) else None
    return sig if isinstance(sig, dict) else {}


def _meta(d: dict) -> dict:
    return d["_meta"] if isinstance(d.get("_meta"), dict) else {}


def _absolute(args: dict, cwd: str | None) -> dict:
    """Bob passes workspace-relative paths (docs/x.html); resolve them against the task's directory."""
    if not cwd or not cwd.startswith("/"):
        return args
    out = dict(args)
    for key in ("path", "file_path", "filePath"):
        v = out.get(key)
        if isinstance(v, str) and v and not v.startswith(("/", "~")) and ":" not in v:
            out[key] = posixpath.normpath(posixpath.join(cwd, v))
    return out


def _patch_lines(patch: str) -> tuple[int, int]:
    """Lines added and removed in a unified diff (the ---/+++ file headers before the first hunk aren't lines)."""
    added = removed = 0
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            added += 1
        elif in_hunk and line.startswith("-"):
            removed += 1
    return added, removed


def _count_changes(ps: ParsedSession, changes: dict, counted: str | None) -> None:
    """A tool result's `_meta.changes` ({file URI: {before, after, patch}}): what the call did to each file on disk."""
    for uri, change in changes.items():
        path = uri_path(uri)
        if not path or not isinstance(change, dict):
            continue
        fs = ps.files.setdefault(path, FileStat())
        if path != counted:  # the call's own file was already counted as a write or an edit
            fs.edits += 1
        if isinstance(change.get("patch"), str):
            added, removed = _patch_lines(change["patch"])
            fs.lines_added += added
            fs.lines_removed += removed


def _feed(b: _Builder, msgs: list[tuple[str | None, dict]], cwd: str | None) -> None:
    """Replay one thread's messages (the task's, or a subagent's) into `b`."""
    ps = b.ps
    results: dict[str, dict] = {}
    for _, d in msgs:
        if d.get("role") != "tool":
            continue
        sig = _signature(d)
        cid = d.get("tool_call_id") or sig.get("id")
        if cid:
            results[cid] = d
    for ts, d in msgs:
        role = d.get("role")
        if role == "system":
            b.event(ts, "system", "meta", f"[system prompt, {len(_text(d.get('content'))):,} chars]")
        elif role == "user":
            if b.agent_id:  # a subagent's brief, not a prompt the user typed
                b.event(ts, "user", "prompt", _user_text(d.get("content")))
            else:
                b.prompt(ts, _user_text(d.get("content")))
        elif role == "assistant":
            reasoning = d.get("reasoning") or d.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                b.event(ts, "assistant", "thinking", reasoning)
            text = _text(d.get("content"))
            if text.strip():
                b.event(ts, "assistant", "text", text)
            spend = _meta(d).get("spend")
            if isinstance(spend, dict) and (spend.get("cost") or spend.get("contextTokens")):
                # Bob reports what each call cost and how big its context was, but not the token split:
                # the context counts as input, and the cost is Bob's own figure rather than an estimate
                ps.api_calls.append(ApiCall(b.agent_id, str(d.get("id") or ""), ts, None,
                                            input_tokens=int(spend.get("contextTokens") or 0),
                                            cost_usd=float(spend.get("cost") or 0)))
            for cid, name, args in _calls(d):
                res = results.get(cid) or {}
                text = _text(res.get("content")) if res else None
                sig = _signature(res)
                is_error = bool(sig["isError"]) if "isError" in sig else bool(text) and text.lower().startswith(("error", "failed"))
                rmeta = _meta(res)
                call = b.tool(ts, cid, name, _absolute(args, cwd), result=text, is_error=is_error,
                              duration_ms=rmeta.get("durationMs") if isinstance(rmeta.get("durationMs"), int) else None)
                if not is_error and isinstance(rmeta.get("changes"), dict):
                    _count_changes(ps, rmeta["changes"], call.file_path)
                sub = res.get("messages")
                if rmeta.get("subagentId") and isinstance(sub, list):
                    _subagent(b, str(rmeta["subagentId"]), rmeta, args, cid, sub, cwd)


def _thread(rows: list[tuple[int | None, object]]) -> list[tuple[str | None, dict]]:
    """[(created_at ms, raw data)] -> [(iso timestamp, message dict)], skipping rows that aren't JSON objects."""
    out = []
    for created_at, raw in rows:
        try:
            d = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if isinstance(d, dict):
            out.append((ms_to_iso(_meta(d).get("timestamp") or created_at), d))
    return out


def _subagent(parent: _Builder, aid: str, meta: dict, args: dict, call_id, msgs: list, cwd: str | None) -> None:
    ps = parent.ps
    sub = ps.subagents.setdefault(aid, Subagent(aid))
    sub.agent_type = meta.get("agentType") or args.get("name") or None
    sub.description = one_line(safe_text(str(args.get("description") or "")), 120) or None
    sub.tool_use_id = call_id
    b = _Builder(ps, agent_id=aid)
    _feed(b, _thread([(None, m) for m in msgs]), cwd)
    parent.stamps.extend(b.stamps)
    stamps = sorted(s for s in b.stamps if s)
    if stamps:
        sub.started_at, sub.ended_at = stamps[0], stamps[-1]
    calls = [c for c in ps.tool_calls if c.agent_id == aid]
    sub.n_tool_calls, sub.n_tool_errors = len(calls), sum(c.is_error for c in calls)
    for c in ps.api_calls:
        if c.agent_id == aid:
            sub.input_tokens += c.input_tokens
            sub.est_cost_usd += c.cost_usd


def parse_bob_task(task: dict) -> ParsedSession | None:
    msgs = task.get("messages") or []
    if not any(m["role"] == "user" for m in msgs):
        return None
    ps = ParsedSession(id=task["id"])
    ps.project_path = task_folder(task)
    ps.git_branch = task.get("git_branch") or None
    ps.cc_version = task.get("version") or None
    ps.entrypoint = f"bob:{task.get('task_type') or 'normal'}"
    ps.custom_title = task.get("title") or None
    b = _Builder(ps)
    for ms in (task.get("created_at"), task.get("updated_at")):
        if ms_to_iso(ms):
            b.stamps.append(ms_to_iso(ms))
    thread = _thread([(m.get("created_at"), m["data"]) for m in msgs])
    ps.parse_errors += len(msgs) - len(thread)
    _feed(b, thread, ps.project_path)
    if task.get("status") == "error" and task.get("last_error"):
        ps.n_api_errors += 1
        b.event(b.stamps[-1] if b.stamps else None, "system", "api_error", safe_text(str(task["last_error"]))[:300])
    try:
        costs = json.loads(task.get("costs") or "{}")
    except ValueError:
        costs = {}
    if isinstance(costs, dict) and not ps.api_calls:  # per-message spend, when Bob wrote it, already covers the task
        model = normalize_model(costs.get("model"))
        inp, out, cr, cw = (int(costs.get(k) or 0) for k in ("input", "output", "cacheRead", "cacheWrite"))
        cost = float(costs.get("cost") or 0)
        if inp or out or cr or cw or cost:
            ps.api_calls.append(ApiCall("", "task-total", ms_to_iso(task.get("updated_at")), model, input_tokens=inp,
                                        output_tokens=out, cache_read_tokens=cr, cache_write_tokens=cw,
                                        cost_usd=cost or estimate_cost(model, inp, cr, cw, out)))
    return finish_session(ps, b.stamps)
