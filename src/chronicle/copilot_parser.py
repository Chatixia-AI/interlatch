"""Parse GitHub Copilot sessions into the same model as Claude Code sessions.

Two stores:
  VS Code Copilot Chat   <VS Code>/User/workspaceStorage/<hash>/chatSessions/<id>.jsonl (+ workspace.json naming the folder)
                         <VS Code>/User/globalStorage/emptyWindowChatSessions/<id>.jsonl
      Each file is an initial state ({"kind": 0, "v": {...}}) followed by patches: kind 1 sets the value at key
      path "k", kind 2 appends "v" to the array at "k" (truncated to index "i" first).
  Copilot agent          ~/.copilot/session-state/<id>/events.jsonl + workspace.yaml (Copilot CLI, and VS Code's
                         Copilot agent host); per-call token usage in ~/.copilot/session-store.db
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import artifacts
from .parser import IDLE_GAP_CAP_S, TEXT_LIMITS, ApiCall, Event, FileStat, ParsedSession, ToolCall, clean_prompt
from .pricing import is_openai_model, normalize_model, openai_cost, usage_cost
from .util import one_line, parse_ts, safe_text, truncate

log = logging.getLogger("interlatch.copilot")

COPILOT_PARSER_VERSION = 2  # 2: artifacts (files created, PRs, commits)
_TERMINAL_TOOLS = {"run_in_terminal", "bash", "shell", "powershell", "run_command", "execute_command"}
_READ_TOOLS = {"read_file", "view", "read", "get_file", "open_file", "view_file"}
_EDIT_TOOLS = {"replace_string_in_file", "multi_replace_string_in_file", "insert_edit_into_file", "edit", "str_replace",
               "apply_patch", "edit_file", "apply_diff", "search_and_replace", "insert_content", "edit_notebook_file",
               "replace_file_content", "multi_replace_file_content"}
_WRITE_TOOLS = {"create_file", "write_file", "create", "write_to_file"}


# ------------------------------------------------------------------ shared helpers
def ms_to_iso(ms) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def shift_iso(ts: str | None, ms: float) -> str | None:
    dt = parse_ts(ts)
    return (dt + timedelta(milliseconds=ms)).isoformat(timespec="milliseconds").replace("+00:00", "Z") if dt else ts


def uri_path(uri) -> str | None:
    """file:///Users/x or a VS Code URI object -> /Users/x."""
    if isinstance(uri, dict):
        return uri.get("fsPath") or uri.get("path")
    if isinstance(uri, str) and uri.startswith("file:"):
        return unquote(urlparse(uri).path) or None
    if isinstance(uri, str) and uri.startswith("vscode-remote:"):  # vscode-remote://ssh-remote+host/path -> host:/path
        u = urlparse(uri)
        host = unquote(u.netloc).split("+", 1)[-1]
        return f"{host}:{unquote(u.path)}" if host else unquote(u.path)
    return uri or None


def estimate_cost(model: str | None, uncached: int, cache_read: int, cache_write: int, output: int) -> float:
    """API list-price equivalent (Copilot itself is billed per seat / premium request)."""
    m = normalize_model(model)
    if not m:
        return 0.0
    if is_openai_model(m):
        return openai_cost(m, uncached + cache_write, cache_read, output)
    return usage_cost(re.sub(r"(\d)\.(\d)", r"\1-\2", m), {"input_tokens": uncached, "output_tokens": output,
                                                           "cache_read_input_tokens": cache_read,
                                                           "cache_creation_input_tokens": cache_write})


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {"input": v}
        except ValueError:
            return {"input": raw}
    return {}


def tool_summary(name: str, args: dict) -> tuple[str, str | None, str | None]:
    """-> (summary, file_path, command)."""
    base = name.rsplit(".", 1)[-1].lower()
    if base == "artifacts" and isinstance(args.get("id"), str):  # claude.ai's: its "command" is create/update/rewrite, not a shell
        return one_line(safe_text(f"{args.get('command') or 'update'} {args.get('title') or args['id']}"), 200), None, None
    cmd = args.get("command") or args.get("cmd") or args.get("commandLine")
    if base in _TERMINAL_TOOLS or (cmd and isinstance(cmd, str)):
        cmd = safe_text(cmd if isinstance(cmd, str) else json.dumps(cmd))
        return one_line(cmd, 240), None, cmd
    fp = args.get("filePath") or args.get("file_path") or args.get("path") or args.get("uri")
    if isinstance(fp, str) and fp:
        fp = uri_path(fp)
        return safe_text(fp), safe_text(fp), None
    for key in ("query", "pattern", "url", "prompt", "description", "explanation"):
        if isinstance(args.get(key), str) and args[key]:
            return one_line(safe_text(args[key]), 200), None, None
    compact = ", ".join(f"{k}={one_line(safe_text(v), 50)}" for k, v in list(args.items())[:3])
    return one_line(compact or name, 200), None, None


def count_file_op(ps: ParsedSession, name: str, fp: str | None) -> None:
    if not fp:
        return
    base = name.rsplit(".", 1)[-1].lower()
    fs = ps.files.setdefault(fp, FileStat())
    if base in _READ_TOOLS:
        fs.reads += 1
    elif base in _WRITE_TOOLS:
        fs.writes += 1
    elif base in _EDIT_TOOLS:
        fs.edits += 1


def finish_session(ps: ParsedSession, stamps: list[str]) -> ParsedSession:
    stamps = sorted(s for s in stamps if s)
    if stamps:
        ps.started_at, ps.ended_at = stamps[0], stamps[-1]
        start, end = parse_ts(stamps[0]), parse_ts(stamps[-1])
        if start and end:
            ps.duration_s = max((end - start).total_seconds(), 0.0)
        active, prev = 0.0, None
        for s in stamps:
            cur = parse_ts(s)
            if cur and prev:
                active += min(max((cur - prev).total_seconds(), 0.0), IDLE_GAP_CAP_S)
            prev = cur or prev
        ps.active_s = active
    for c in ps.api_calls:
        if c.model:
            ps.models[c.model] += c.output_tokens or 1
    return ps


class _Builder:
    """Appends events in order with sequence numbers."""

    def __init__(self, ps: ParsedSession, where: str | None = None, agent_id: str = ""):
        self.ps, self.seq, self.stamps = ps, 0, []
        self.where = where  # files the agent writes live there (claude.ai's sandbox), not on this machine
        self.agent_id = agent_id  # a subagent's events and tool calls; "" is the main thread

    def event(self, ts, role, kind, text, **kw) -> Event:
        ev = Event(self.agent_id, self.seq, ts, role, kind, truncate(safe_text(text), TEXT_LIMITS.get(kw.pop("limit_key", None) or kind, 2_000)), **kw)
        self.seq += 1
        self.ps.events.append(ev)
        if ts:
            self.stamps.append(ts)
        return ev

    def prompt(self, ts, text: str) -> None:
        text = clean_prompt(safe_text(text or ""))
        if not text:
            return
        self.ps.n_prompts += 1
        self.ps.first_prompt = self.ps.first_prompt or text
        self.ps.last_prompt = text
        self.event(ts, "user", "prompt", text)

    def tool(self, ts, call_id, name, args: dict, *, result=None, is_error=False, duration_ms=None, mcp=None) -> ToolCall:
        summary, fp, cmd = tool_summary(name, args)
        call = ToolCall(self.agent_id, call_id, ts, safe_text(name), mcp, safe_text(summary), fp, cmd, is_error=bool(is_error),
                        duration_ms=duration_ms, result_chars=len(result) if isinstance(result, str) else None)
        self.ps.tool_calls.append(call)
        self.ps.tools[call.name] += 1
        if mcp:
            self.ps.mcp_servers[mcp] += 1
        count_file_op(self.ps, name, fp)
        if not is_error:
            if fp and name.rsplit(".", 1)[-1].lower() in _WRITE_TOOLS:
                content = args.get("content") if isinstance(args.get("content"), str) else args.get("file_text")
                artifacts.file_written(self.ps, fp, content=content, ts=ts, agent_id=self.agent_id, tool_use_id=call_id,
                                       where=self.where)
            artifacts.from_tool(self.ps, call.name, args, result if isinstance(result, str) else "", ts=ts,
                                agent_id=self.agent_id, tool_use_id=call_id, command=cmd)
        self.event(ts, "assistant", "tool_use", f"{call.name}: {summary}" if summary and summary != name else call.name,
                   tool_name=call.name, tool_use_id=call_id, meta={"input": truncate(json.dumps(args, ensure_ascii=False, default=str), 1_500)} if args else None)
        if result is not None:
            self.event(shift_iso(ts, duration_ms or 0), "user", "tool_result", result, limit_key="tool_error" if is_error else "tool_result",
                       tool_name=call.name, tool_use_id=call_id, is_error=bool(is_error))
        return call


# ------------------------------------------------------------------ VS Code Copilot Chat
def chat_files(user_dir: Path) -> list[Path]:
    """Chat session logs under a VS Code User directory."""
    out = sorted(user_dir.glob("workspaceStorage/*/chatSessions/*.jsonl"))
    out += sorted(user_dir.glob("globalStorage/emptyWindowChatSessions/*.jsonl"))
    return out


def workspace_folder(chat_file: Path) -> str | None:
    """The folder a workspaceStorage hash belongs to (workspace.json next to chatSessions/)."""
    ws = chat_file.parent.parent / "workspace.json"
    try:
        data = json.loads(ws.read_text())
    except (OSError, ValueError):
        return None
    return uri_path(data.get("folder") or data.get("workspace"))


def load_chat(path: Path) -> dict | None:
    """Replay the patch log into the final session state."""
    state = None
    with open(path, errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                op = json.loads(line)
            except ValueError:
                continue
            try:
                if op.get("kind") == 0:
                    state = op.get("v")
                elif state is not None:
                    keys = op.get("k") or []
                    target = state
                    for k in keys[:-1] if op.get("kind") == 1 else keys:
                        target = target[k]
                    if op.get("kind") == 1:
                        target[keys[-1]] = op.get("v")
                    elif op.get("kind") == 2 and isinstance(target, list):
                        if isinstance(op.get("i"), int):
                            del target[op["i"]:]
                        target.extend(op.get("v") or [])
            except (KeyError, IndexError, TypeError):
                continue  # a patch for a path we could not rebuild; the rest of the log still applies
    return state if isinstance(state, dict) else None


def chat_has_requests(path: Path) -> bool:
    state = load_chat(path)
    return bool(state and state.get("requests"))


def _md(part) -> str:
    v = part.get("value") if isinstance(part, dict) else None
    if isinstance(v, dict):
        v = v.get("value")
    return v if isinstance(v, str) else ""


def _result_text(res) -> str:
    """toolCallResults entry -> text."""
    if isinstance(res, dict):
        items = res.get("content") or []
        parts = []
        for it in items if isinstance(items, list) else [items]:
            v = it.get("value") if isinstance(it, dict) else it
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, dict) and isinstance(v.get("node"), dict):
                parts.append("[structured result]")
        return "\n".join(parts)
    return res if isinstance(res, str) else ""


def _edit_lines(edits) -> tuple[int, int]:
    added = removed = 0
    for group in edits or []:
        for e in group if isinstance(group, list) else [group]:
            if not isinstance(e, dict):
                continue
            text = e.get("text") or ""
            rng = e.get("range") or {}
            if text:
                added += text.count("\n") + (0 if text.endswith("\n") else 1)
            s, t = rng.get("startLineNumber"), rng.get("endLineNumber")
            if isinstance(s, int) and isinstance(t, int) and (t > s or rng.get("endColumn", 1) > rng.get("startColumn", 1)):
                removed += t - s + 1
    return added, removed


def parse_vscode_chat(path: Path, folder: str | None = None) -> ParsedSession | None:
    state = load_chat(path)
    if not state or not state.get("requests"):
        return None
    ps = ParsedSession(id=state.get("sessionId") or path.stem)
    ps.project_path = folder
    ps.entrypoint = "copilot:vscode-chat"
    ps.custom_title = state.get("customTitle") or None
    b = _Builder(ps)
    created = ms_to_iso(state.get("creationDate"))
    if created:
        b.stamps.append(created)
    for n, req in enumerate(state["requests"]):
        if not isinstance(req, dict):
            continue
        ts = ms_to_iso(req.get("timestamp")) or created
        msg = req.get("message") or {}
        b.prompt(ts, msg.get("text") if isinstance(msg, dict) else str(msg))
        mode = (req.get("modeInfo") or {}).get("modeName") or ((req.get("agent") or {}).get("id") or "")
        if mode and not ps.permission_mode:
            ps.permission_mode = str(mode)
        result = req.get("result") if isinstance(req.get("result"), dict) else {}
        meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        timings = result.get("timings") if isinstance(result.get("timings"), dict) else {}
        elapsed = timings.get("totalElapsed") or req.get("elapsedMs") or 0
        end_ts = shift_iso(ts, elapsed) if elapsed else ts
        model = normalize_model(meta.get("resolvedModel") or str(req.get("modelId") or "").split("/")[-1]) or None
        rounds = meta.get("toolCallRounds") if isinstance(meta.get("toolCallRounds"), list) else []
        results = meta.get("toolCallResults") if isinstance(meta.get("toolCallResults"), dict) else {}
        step = (elapsed / (len(rounds) + 1)) if rounds and elapsed else 0
        if rounds:  # the full agent loop: thinking, text and tool calls per model round
            for i, rnd in enumerate(rounds):
                rts = shift_iso(ts, step * i) if step else ts
                thinking = rnd.get("thinking")
                ttext = thinking.get("text") if isinstance(thinking, dict) else thinking
                if isinstance(ttext, list):
                    ttext = "\n".join(t for t in ttext if isinstance(t, str))
                if isinstance(ttext, str) and ttext.strip():
                    b.event(rts, "assistant", "thinking", ttext)
                if isinstance(rnd.get("response"), str) and rnd["response"].strip():
                    b.event(rts, "assistant", "text", rnd["response"], meta={"model": model} if model else None)
                for tc in rnd.get("toolCalls") or []:
                    if not isinstance(tc, dict):
                        continue
                    res = results.get(tc.get("id"))
                    text = _result_text(res)
                    err = isinstance(res, dict) and bool(res.get("isError"))
                    b.tool(rts, tc.get("id"), tc.get("name") or "tool", _args(tc.get("arguments")), result=text or None,
                           is_error=err or text.lower().startswith(("error", "failed")))
        else:  # older or failed requests keep only the rendered response parts
            for part in req.get("response") or []:
                kind = part.get("kind") if isinstance(part, dict) else None
                if kind == "thinking" and isinstance(part.get("value"), str) and part["value"].strip():
                    b.event(ts, "assistant", "thinking", part["value"])
                elif kind == "toolInvocationSerialized":
                    msg_text = _md(part.get("pastTenseMessage") or {}) or _md(part.get("invocationMessage") or {})
                    b.tool(ts, part.get("toolCallId"), part.get("toolId") or "tool", {"description": msg_text} if msg_text else {})
        final = "".join(_md(p) for p in req.get("response") or [] if isinstance(p, dict) and not p.get("kind"))
        if final.strip() and not (rounds and isinstance(rounds[-1].get("response"), str) and rounds[-1]["response"].strip()):
            b.event(end_ts, "assistant", "text", final, meta={"model": model} if model else None)
        for part in req.get("response") or []:
            if isinstance(part, dict) and part.get("kind") == "textEditGroup":
                fp = uri_path(part.get("uri"))
                if fp:
                    added, removed = _edit_lines(part.get("edits"))
                    fs = ps.files.setdefault(fp, FileStat())
                    fs.edits += 1
                    fs.lines_added += added
                    fs.lines_removed += removed
        for ev in req.get("editedFileEvents") or []:
            fp = uri_path((ev or {}).get("uri")) if isinstance(ev, dict) else None
            if fp and fp not in ps.files:
                ps.files[fp] = FileStat(edits=1)
        if req.get("isCanceled"):
            ps.n_interrupts += 1
            b.event(end_ts, "user", "interrupt", "[Request cancelled]")
        err = result.get("errorDetails")
        if isinstance(err, dict) and err.get("message"):
            ps.n_api_errors += 1
            b.event(end_ts, "system", "api_error", one_line(safe_text(err["message"]), 300))
        pt, ot = meta.get("promptTokens"), meta.get("outputTokens")
        if isinstance(pt, int) or isinstance(ot, int):
            # the chat log does not split cached from uncached input, so no cost estimate (it would overstate)
            ps.api_calls.append(ApiCall("", req.get("requestId") or f"req{n}", end_ts, model,
                                        input_tokens=pt or 0, output_tokens=ot or 0))
        if end_ts:
            b.stamps.append(end_ts)
    return finish_session(ps, b.stamps)


# ------------------------------------------------------------------ Copilot agent (CLI / VS Code agent host)
def agent_session_dirs(home: Path) -> list[Path]:
    root = home / "session-state"
    return sorted(p for p in root.iterdir() if (p / "events.jsonl").is_file()) if root.is_dir() else []


def read_workspace_yaml(session_dir: Path) -> dict:
    """workspace.yaml is flat `key: value` lines."""
    out = {}
    try:
        for line in (session_dir / "workspace.yaml").read_text(errors="replace").splitlines():
            if ":" in line and not line.startswith((" ", "-", "#")):
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def load_usage(home: Path) -> dict[str, list[dict]]:
    """session id -> per-call token usage rows from session-store.db (read-only)."""
    db = home / "session-store.db"
    if not db.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT session_id, agent_id, model, input_tokens, output_tokens, cache_read_tokens, "
                            "cache_write_tokens, duration_ms, created_at FROM assistant_usage_events ORDER BY id").fetchall()
        conn.close()
    except sqlite3.Error as exc:
        log.warning("copilot session-store unreadable: %s", exc)
        return {}
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["session_id"], []).append(dict(r))
    return out


def parse_copilot_agent(session_dir: Path, usage: list[dict] | None = None) -> ParsedSession:
    ws = read_workspace_yaml(session_dir)
    ps = ParsedSession(id=ws.get("id") or session_dir.name)
    ps.project_path = ws.get("cwd") or None
    ps.git_branch = ws.get("branch") or None
    client = ws.get("client_name") or "cli"
    ps.entrypoint = f"copilot:{client}"
    if ws.get("name") and ws.get("user_named") == "true":
        ps.custom_title = ws["name"]
    b = _Builder(ps)
    pending: dict[str, tuple[str | None, str, dict]] = {}
    lines_added = lines_removed = 0
    modified: list[str] = []
    with open(session_dir / "events.jsonl", errors="replace") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                ps.parse_errors += 1
                continue
            t, ts = ev.get("type"), ev.get("timestamp")
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            try:
                if t in ("session.start", "session.resume"):
                    ctx = d.get("context") if isinstance(d.get("context"), dict) else {}
                    ps.project_path = ps.project_path or ctx.get("cwd")
                    ps.cc_version = ps.cc_version or d.get("copilotVersion")
                    b.stamps.append(ts)
                elif t == "user.message":
                    b.prompt(ts, d.get("content") or "")
                elif t == "assistant.message":
                    model = normalize_model(d.get("model"))
                    if isinstance(d.get("reasoningText"), str) and d["reasoningText"].strip():
                        b.event(ts, "assistant", "thinking", d["reasoningText"])
                    if isinstance(d.get("content"), str) and d["content"].strip():
                        b.event(ts, "assistant", "text", d["content"], meta={"model": model, "phase": d.get("phase")})
                elif t == "tool.execution_start":
                    pending[d.get("toolCallId")] = (ts, d.get("toolName") or "tool", _args(d.get("arguments")))
                elif t == "tool.execution_complete":
                    start_ts, name, args = pending.pop(d.get("toolCallId"), (ts, "tool", {}))
                    res = d.get("result")
                    text = res.get("content") if isinstance(res, dict) else res
                    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str) if text else ""
                    s, e = parse_ts(start_ts), parse_ts(ts)
                    dur = int((e - s).total_seconds() * 1000) if s and e else None
                    b.tool(start_ts, d.get("toolCallId"), name, args, result=text, is_error=d.get("success") is False,
                           duration_ms=dur)
                elif t == "skill.invoked" and d.get("name"):
                    ps.skills[str(d["name"])] += 1
                elif t in ("abort", "session.abort"):
                    ps.n_interrupts += 1
                    b.event(ts, "user", "interrupt", "[Interrupted]")
                elif t == "session.error":
                    ps.n_api_errors += 1
                    b.event(ts, "system", "api_error", one_line(safe_text(str(d.get("message") or d)), 300))
                elif t in ("session.compaction_complete", "session.compaction"):
                    ps.n_compactions += 1
                    b.event(ts, "system", "compact", "Context compacted")
                elif t == "session.shutdown":
                    changes = d.get("codeChanges") if isinstance(d.get("codeChanges"), dict) else {}
                    lines_added = max(lines_added, changes.get("linesAdded") or 0)
                    lines_removed = max(lines_removed, changes.get("linesRemoved") or 0)
                    modified = changes.get("filesModified") or modified
                    b.stamps.append(ts)
            except Exception as exc:  # an unexpected event shape must never lose the session
                ps.parse_errors += 1
                if ps.parse_errors <= 3:
                    log.warning("skipped a Copilot event in %s: %r", ps.id, exc)
    for s_ts, name, args in pending.values():  # started but never completed (session ended mid-call)
        b.tool(s_ts, None, name, args)
    for fp in modified if isinstance(modified, list) else []:
        if isinstance(fp, str):
            ps.files.setdefault(fp, FileStat(edits=1))
    # Copilot reports line totals per session, not per file: book them on the first edited file so session sums hold
    if (lines_added or lines_removed) and ps.files and not any(f.lines_added or f.lines_removed for f in ps.files.values()):
        edited = [f for f in ps.files.values() if f.edits or f.writes] or list(ps.files.values())
        edited[0].lines_added, edited[0].lines_removed = lines_added, lines_removed
    for i, u in enumerate(usage or []):
        inp, cr, cw = u.get("input_tokens") or 0, u.get("cache_read_tokens") or 0, u.get("cache_write_tokens") or 0
        uncached = max(inp - cr - cw, 0)
        model = normalize_model(u.get("model"))
        ps.api_calls.append(ApiCall("", f"use{i}", u.get("created_at"), model,
                                    input_tokens=uncached, output_tokens=u.get("output_tokens") or 0,
                                    cache_read_tokens=cr, cache_write_tokens=cw,
                                    cost_usd=estimate_cost(model, uncached, cr, cw, u.get("output_tokens") or 0)))
    return finish_session(ps, b.stamps)
