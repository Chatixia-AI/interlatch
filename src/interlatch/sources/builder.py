"""Building a ParsedSession from a source that is not a Claude Code transcript (Copilot, Bob, Antigravity, Codex
Cloud, chat exports): times, paths, tool summaries, cost estimates, and the session's totals once it is read."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

from ..core import artifact_records
from ..core.pricing import is_openai_model, normalize_model, openai_cost, usage_cost
from ..core.sessions import Event, FileStat, IDLE_GAP_CAP_S, ParsedSession, TEXT_LIMITS, ToolCall
from ..core.util import one_line, parse_ts, safe_text, truncate
from .prompts import clean_prompt

_TERMINAL_TOOLS = {"run_in_terminal", "bash", "shell", "powershell", "run_command", "execute_command"}
_READ_TOOLS = {"read_file", "view", "read", "get_file", "open_file", "view_file"}
_EDIT_TOOLS = {"replace_string_in_file", "multi_replace_string_in_file", "insert_edit_into_file", "edit", "str_replace",
               "apply_patch", "edit_file", "apply_diff", "search_and_replace", "insert_content", "edit_notebook_file",
               "replace_file_content", "multi_replace_file_content"}
_WRITE_TOOLS = {"create_file", "write_file", "create", "write_to_file"}


def ms_to_iso(ms) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def shift_iso(ts: str | None, ms: float) -> str | None:
    dt = parse_ts(ts)
    return (dt + timedelta(milliseconds=ms)).isoformat(timespec="milliseconds").replace("+00:00", "Z") if dt else ts


_URI_DRIVE = re.compile(r"/[A-Za-z]:(?:/|$)")  # a Windows drive as a URI's path spells it


def uri_path(uri) -> str | None:
    """file:///Users/x or a VS Code URI object -> /Users/x. On Windows file:///c%3A/x -> c:/x, not /c:/x."""
    if isinstance(uri, dict):
        p = uri.get("fsPath") or uri.get("path")
        return p[1:] if isinstance(p, str) and _URI_DRIVE.match(p) else p
    if isinstance(uri, str) and uri.startswith("file:"):
        p = unquote(urlparse(uri).path)
        return (p[1:] if _URI_DRIVE.match(p) else p) or None
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


def tool_args(raw) -> dict:
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


class SessionBuilder:
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
                artifact_records.file_written(self.ps, fp, content=content, ts=ts, agent_id=self.agent_id, tool_use_id=call_id,
                                       where=self.where)
            artifact_records.from_tool(self.ps, call.name, args, result if isinstance(result, str) else "", ts=ts,
                                agent_id=self.agent_id, tool_use_id=call_id, command=cmd)
        self.event(ts, "assistant", "tool_use", f"{call.name}: {summary}" if summary and summary != name else call.name,
                   tool_name=call.name, tool_use_id=call_id, meta={"input": truncate(json.dumps(args, ensure_ascii=False, default=str), 1_500)} if args else None)
        if result is not None:
            self.event(shift_iso(ts, duration_ms or 0), "user", "tool_result", result, limit_key="tool_error" if is_error else "tool_result",
                       tool_name=call.name, tool_use_id=call_id, is_error=bool(is_error))
        return call
