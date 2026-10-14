"""Parse Claude Code transcripts (.jsonl, optionally .gz) into structured session data.

Layout handled (Claude Code 2.x):
  projects/<dir>/<session>.jsonl                          main thread
  projects/<dir>/<session>/subagents/agent-<id>.jsonl     subagent threads (+ .meta.json)
  projects/<dir>/<session>/subagents/workflows/wf_*/...   workflow subagents
  projects/<dir>/<session>/workflows/wf_*.json            workflow run records
Older transcripts that inline subagent messages with isSidechain=true are also supported.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import artifacts
from .pricing import normalize_model, usage_cost
from .util import iter_jsonl, one_line, parse_ts, safe_text, to_iso, truncate

PARSER_VERSION = 3  # 3: artifacts (files, pages, PRs, commits a session made); 2: replays and API retries count once

TEXT_LIMITS = {
    "prompt": 20_000,
    "text": 20_000,
    "thinking": 4_000,
    "tool_use": 2_000,
    "tool_result": 2_000,
    "tool_error": 4_000,
    "command": 4_000,
    "command_output": 1_500,
    "notification": 800,
    "meta": 400,
    "compact_summary": 12_000,
    "bash_input": 2_000,
    "bash_output": 1_500,
    "api_error": 600,
    "notice": 600,
    "compact": 300,
    "attachment": 300,
}
SEARCHABLE_KINDS = {"prompt", "text", "tool_use", "command", "bash_input"}
READ_TOOLS = {"Read", "NotebookRead"}
EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit"}
WRITE_TOOLS = {"Write"}
AGENT_TOOLS = {"Agent", "Task"}
IDLE_GAP_CAP_S = 15 * 60

_WRAPPER_TAGS = ("ide_opened_file", "ide_selection", "ide_diagnostics", "system-reminder")
_WRAPPER_RE = re.compile(r"<(%s)\b[^>]*>.*?</\1>" % "|".join(_WRAPPER_TAGS), re.S)
_CMD_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_CMD_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_TAG_RE = re.compile(r"</?[a-z][\w-]*>")


# ------------------------------------------------------------------ data model
@dataclass
class Event:
    agent_id: str
    seq: int
    ts: str | None
    role: str
    kind: str
    text: str
    tool_name: str | None = None
    tool_use_id: str | None = None
    is_error: bool = False
    meta: dict | None = None

    @property
    def searchable(self) -> bool:
        return self.kind in SEARCHABLE_KINDS or (self.kind == "tool_result" and self.is_error)


@dataclass
class ToolCall:
    agent_id: str
    tool_use_id: str | None
    ts: str | None
    name: str
    mcp_server: str | None
    summary: str
    file_path: str | None = None
    command: str | None = None
    is_error: bool = False
    duration_ms: int | None = None
    result_chars: int | None = None
    input: dict | None = None


@dataclass
class ApiCall:
    agent_id: str
    msg_id: str
    ts: str | None
    model: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    speed: str | None = None

    @property
    def context_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass
class FileStat:
    reads: int = 0
    edits: int = 0
    writes: int = 0
    lines_added: int = 0
    lines_removed: int = 0


@dataclass
class Subagent:
    agent_id: str
    agent_type: str | None = None
    description: str | None = None
    workflow_id: str | None = None
    phase: str | None = None
    tool_use_id: str | None = None
    model: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    n_tool_calls: int = 0
    n_tool_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    est_cost_usd: float = 0.0


@dataclass
class ParsedSession:
    id: str
    project_path: str | None = None
    git_branch: str | None = None
    git_remote: str | None = None  # the repository's remote URL, when the transcript records it (Codex does)
    cc_version: str | None = None
    entrypoint: str | None = None
    permission_mode: str | None = None
    ai_title: str | None = None
    custom_title: str | None = None
    first_prompt: str | None = None
    last_prompt: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_s: float = 0.0
    active_s: float = 0.0
    events: list[Event] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    api_calls: list[ApiCall] = field(default_factory=list)
    files: dict[str, FileStat] = field(default_factory=dict)
    subagents: dict[str, Subagent] = field(default_factory=dict)
    models: Counter = field(default_factory=Counter)
    tools: Counter = field(default_factory=Counter)
    skills: Counter = field(default_factory=Counter)
    mcp_servers: Counter = field(default_factory=Counter)
    commands: Counter = field(default_factory=Counter)
    branches: Counter = field(default_factory=Counter)
    hooks: dict = field(default_factory=dict)
    prs: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    outputs: list = field(default_factory=list)  # what the session made (artifacts.py), for the artifacts table
    workflows: list = field(default_factory=list)
    n_prompts: int = 0
    n_interrupts: int = 0
    n_compactions: int = 0
    n_api_errors: int = 0
    n_images: int = 0
    n_result_images: int = 0  # images tools returned (screenshots, image files read)
    parse_errors: int = 0
    cc_cost_usd: float | None = None
    remote_control: bool = False
    _timestamps: list = field(default_factory=list, repr=False)
    _cc_costs: dict = field(default_factory=dict, repr=False)
    _skill_calls: Counter = field(default_factory=Counter, repr=False)
    _seen_uuids: set = field(default_factory=set, repr=False)
    _seen_tool_ids: set = field(default_factory=set, repr=False)
    _seen_result_ids: set = field(default_factory=set, repr=False)
    _mentions: dict = field(default_factory=dict, repr=False)  # office/PDF paths tools named (artifacts.py)

    # ---------- aggregates
    def main_calls(self) -> list[ApiCall]:
        return [c for c in self.api_calls if not c.agent_id]

    @property
    def n_api_calls(self) -> int:
        return len(self.main_calls())

    @property
    def n_tool_errors(self) -> int:
        return sum(1 for t in self.tool_calls if t.is_error)

    @property
    def peak_context(self) -> int:
        return max((c.context_tokens for c in self.main_calls()), default=0)

    def totals(self) -> dict:
        t = Counter()
        for c in self.api_calls:
            t["input_tokens"] += c.input_tokens
            t["output_tokens"] += c.output_tokens
            t["cache_read_tokens"] += c.cache_read_tokens
            t["cache_write_tokens"] += c.cache_write_tokens
            t["cost"] += c.cost_usd
            if c.agent_id:
                t["sub_tokens"] += c.input_tokens + c.output_tokens + c.cache_read_tokens + c.cache_write_tokens
                t["sub_cost"] += c.cost_usd
        return dict(t)

    @property
    def primary_model(self) -> str | None:
        main = Counter()
        for c in self.main_calls():
            if c.model:
                main[c.model] += c.output_tokens + 1
        pool = main or self.models
        return pool.most_common(1)[0][0] if pool else None

    @property
    def lines_added(self) -> int:
        return sum(f.lines_added for f in self.files.values())

    @property
    def lines_removed(self) -> int:
        return sum(f.lines_removed for f in self.files.values())


# ------------------------------------------------------------------ helpers
def _content_blocks(content) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _result_text(content) -> tuple[str, int]:
    """Flatten a tool_result content payload into text; returns (text, n_images)."""
    if isinstance(content, str):
        return content, 0
    parts, images = [], 0
    for b in _content_blocks(content):
        t = b.get("type")
        if t == "text":
            parts.append(b.get("text") or "")
        elif t == "image":
            images += 1
            parts.append("[image]")
        elif t == "tool_reference":
            parts.append(f"[tool: {b.get('tool_name') or b.get('name') or '?'}]")
        elif t == "document":
            parts.append("[document]")
    return "\n".join(parts), images


def clean_prompt(text: str) -> str:
    text = _WRAPPER_RE.sub("", text)
    return text.strip()


def classify_user_text(entry: dict, text: str) -> str:
    origin = (entry.get("origin") or {}).get("kind")
    t = text.lstrip()
    if entry.get("isCompactSummary") or t.startswith("This session is being continued from a previous conversation"):
        return "compact_summary"
    if t.startswith("<command-name>") or t.startswith("<command-message>"):
        return "command"
    if t.startswith(("<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat>")):
        return "command_output"
    if t.startswith("[Request interrupted by user"):
        return "interrupt"
    if t.startswith("<bash-input>"):
        return "bash_input"
    if t.startswith(("<bash-stdout>", "<bash-stderr>")):
        return "bash_output"
    if origin in ("task-notification", "coordinator", "peer") or t.startswith(("<task-notification>", "[SYSTEM NOTIFICATION")):
        return "notification"
    if entry.get("isMeta") or t.startswith("Caveat:"):
        return "meta"
    return "prompt"


def parse_command(text: str) -> tuple[str, str]:
    name = (_CMD_NAME_RE.search(text) or [None, ""])[1].strip()
    args = (_CMD_ARGS_RE.search(text) or [None, ""])[1].strip()
    return name, args


def _rel(path, root: str | None) -> str | None:
    if not path:
        return None
    path = safe_text(path)
    if root and path.startswith(root.rstrip("/") + "/"):
        return path[len(root.rstrip("/")) + 1 :]
    return path


def summarize_tool_use(name: str, inp: dict, cwd: str | None = None) -> tuple[str, str | None, str | None]:
    """One-line human summary of a tool call; also returns (file_path, command) when relevant.

    Tool inputs come from the model and can have unexpected types; a bad value must never lose the session.
    """
    try:
        return _summarize_tool_use(name, inp if isinstance(inp, dict) else {}, cwd)
    except Exception:
        fp = inp.get("file_path") if isinstance(inp, dict) and isinstance(inp.get("file_path"), str) else None
        return name, fp, None


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _summarize_tool_use(name: str, inp: dict, cwd: str | None) -> tuple[str, str | None, str | None]:
    fp = inp.get("file_path") or inp.get("notebook_path")
    fp = safe_text(fp) if fp else None
    if name == "Bash":
        cmd = safe_text(inp.get("command") or "")
        return one_line(cmd, 240), None, cmd
    if name in READ_TOOLS | EDIT_TOOLS | WRITE_TOOLS:
        extra = ""
        if name == "Read" and (inp.get("offset") or inp.get("limit")):
            extra = f" [{_int(inp.get('offset'))}:{_int(inp.get('offset')) + _int(inp.get('limit'))}]"
        if name == "MultiEdit":
            extra = f" ({len(inp.get('edits') or [])} edits)"
        return f"{_rel(fp, cwd)}{extra}", fp, None
    if name == "Grep":
        where = inp.get("path") or inp.get("glob") or ""
        return one_line(f"/{inp.get('pattern', '')}/ {_rel(where, cwd) or ''}", 200), None, None
    if name == "Glob":
        return one_line(f"{inp.get('pattern', '')} {_rel(inp.get('path'), cwd) or ''}", 200), None, None
    if name == "WebFetch":
        return one_line(inp.get("url") or "", 200), None, None
    if name == "WebSearch":
        return one_line(inp.get("query") or "", 200), None, None
    if name in AGENT_TOOLS:
        kind = inp.get("subagent_type") or "agent"
        return one_line(f"{kind}: {inp.get('description') or inp.get('prompt') or ''}", 200), None, None
    if name == "Skill":
        return one_line(f"{inp.get('skill') or inp.get('command') or ''} {inp.get('args') or ''}", 200), None, None
    if name == "TodoWrite":
        todos = inp.get("todos") or []
        done = sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "completed")
        return f"{len(todos)} todos ({done} done)", None, None
    if name == "AskUserQuestion":
        qs = inp.get("questions") or []
        q = qs[0].get("question") if qs and isinstance(qs[0], dict) else ""
        return one_line(q, 200), None, None
    if name == "Workflow":
        return one_line(inp.get("name") or inp.get("description") or "workflow script", 200), None, None
    if name == "Artifact":
        return one_line(f"{inp.get('action') or 'publish'} {inp.get('file_path') or inp.get('url') or ''}", 200), None, None
    if name.startswith("mcp__"):
        args = json.dumps(inp, ensure_ascii=False)[:160] if inp else ""
        return one_line(f"{name.split('__', 2)[-1]} {args}", 200), None, None
    if not inp:
        return name, None, None
    compact = ", ".join(f"{k}={one_line(str(v), 60)}" for k, v in list(inp.items())[:3])
    return one_line(compact, 200), None, None


def patch_stats(tur) -> tuple[int, int]:
    """Lines added/removed from an Edit/Write toolUseResult."""
    if not isinstance(tur, dict):
        return 0, 0
    added = removed = 0
    patch = tur.get("structuredPatch")
    if isinstance(patch, list) and patch:
        for hunk in patch:
            for line in (hunk or {}).get("lines") or []:
                if isinstance(line, str):
                    if line.startswith("+"):
                        added += 1
                    elif line.startswith("-"):
                        removed += 1
        return added, removed
    if tur.get("type") == "create" and isinstance(tur.get("content"), str):
        return tur["content"].count("\n") + (0 if tur["content"].endswith("\n") else 1), 0
    return 0, 0


# ------------------------------------------------------------------ thread parser
class _Thread:
    """Parses one transcript file (main thread or a subagent) into the shared ParsedSession."""

    def __init__(self, ps: ParsedSession, agent_id: str = ""):
        self.ps = ps
        self.agent_id = agent_id
        self.seq = 0
        self.pending: dict[str, tuple[ToolCall, datetime | None]] = {}
        self.usage: dict[str, dict] = {}  # msg_id -> {model, usage, ts, speed}
        self.first_ts: str | None = None
        self.last_ts: str | None = None
        self.n_tool_calls = 0
        self.n_tool_errors = 0
        self.model_seen: str | None = None
        self.api_error_uuids: set[str] = set()

    # -------------------------------------------------------------- events
    def _event(self, ts, role, kind, text, *, limit_key=None, **kw) -> Event:
        limit = TEXT_LIMITS.get(limit_key or kind, 2_000)
        ev = Event(self.agent_id, self.seq, ts, role, kind, truncate(text or "", limit), **kw)
        self.seq += 1
        self.ps.events.append(ev)
        return ev

    def _touch(self, ts: str | None) -> None:
        if not ts:
            return
        if self.first_ts is None or ts < self.first_ts:
            self.first_ts = ts
        if self.last_ts is None or ts > self.last_ts:
            self.last_ts = ts
        self.ps._timestamps.append(ts)

    # -------------------------------------------------------------- feed
    def feed(self, d: dict) -> None:
        """Process one transcript line; a malformed line is skipped, never fatal for the session."""
        # Claude Code can append the whole conversation chain to the transcript again (same uuids, original
        # timestamps; seen after an artifact publish or a resume). The first copy is complete, the replay is not.
        uuid = d.get("uuid")
        if isinstance(uuid, str):
            if uuid in self.ps._seen_uuids:
                return
            self.ps._seen_uuids.add(uuid)
        try:
            self._feed(d)
        except Exception as exc:  # unexpected shapes from future Claude Code versions or odd model output
            self.ps.parse_errors += 1
            if self.ps.parse_errors <= 3:
                import logging

                logging.getLogger("interlatch.parser").warning("skipped a malformed line in %s: %r", self.ps.id, exc)

    def _feed(self, d: dict) -> None:
        t = d.get("type")
        ts = d.get("timestamp")
        ps = self.ps
        if t in ("user", "assistant", "system", "attachment"):
            self._touch(ts)
            if not self.agent_id:
                if d.get("cwd") and not ps.project_path:
                    ps.project_path = d["cwd"]
                if d.get("gitBranch"):
                    ps.branches[d["gitBranch"]] += 1
                    ps.git_branch = d["gitBranch"]
                if d.get("version"):
                    ps.cc_version = d["version"]
                if d.get("entrypoint"):
                    ps.entrypoint = d["entrypoint"]
        if t == "user":
            if d.get("permissionMode") and not self.agent_id:
                ps.permission_mode = d["permissionMode"]
            self._user(d, ts)
        elif t == "assistant":
            self._assistant(d, ts)
        elif t == "system":
            self._system(d, ts)
        elif t == "attachment":
            self._attachment(d, ts)
        elif t == "ai-title" and not self.agent_id:
            ps.ai_title = safe_text(d.get("aiTitle")) or ps.ai_title
        elif t == "custom-title" and not self.agent_id:
            ps.custom_title = safe_text(d.get("customTitle") or d.get("title")) or ps.custom_title
        elif t == "summary" and not self.agent_id and d.get("summary") and not ps.ai_title:
            ps.ai_title = safe_text(d["summary"])
        elif t == "pr-link":
            pr = {"number": d.get("prNumber"), "url": d.get("prUrl"), "repo": d.get("prRepository"), "ts": d.get("timestamp")}
            if pr["url"] and all(p.get("url") != pr["url"] for p in ps.prs):
                ps.prs.append(pr)
        elif t == "frame-link" and d.get("frameUrl"):
            art = {"title": d.get("title"), "url": d.get("frameUrl"), "ts": d.get("timestamp")}
            if all(a.get("url") != art["url"] for a in ps.artifacts):
                ps.artifacts.append(art)
        elif t == "cost-state":
            key = d.get("startTime") or "?"
            ps._cc_costs[key] = d.get("totalCostUSD") or 0.0
        elif t == "bridge-session":
            ps.remote_control = True

    # -------------------------------------------------------------- user
    def _user(self, d: dict, ts: str | None) -> None:
        ps = self.ps
        msg = d.get("message") or {}
        blocks = _content_blocks(msg.get("content"))
        results = [b for b in blocks if b.get("type") == "tool_result"]
        if results:
            tur = d.get("toolUseResult")
            for b in results:
                self._tool_result(b, tur if len(results) == 1 else None, ts)
            return
        texts, images = [], 0
        for b in blocks:
            if b.get("type") == "text":
                txt = b.get("text") or ""
                if txt.startswith("[Image: ") or txt.startswith("[Image #"):
                    continue
                texts.append(txt)
            elif b.get("type") == "image":
                images += 1
        text = "\n\n".join(texts)
        kind = classify_user_text(d, text)
        if kind == "prompt":
            self._prompt(clean_prompt(text), ts, images=images, meta=None)
        elif kind == "command":
            name, args = parse_command(text)
            if name:
                ps.commands[name] += 1
            self._event(ts, "user", "command", f"{name} {args}".strip() or one_line(text, 200))
        elif kind == "interrupt":
            if not self.agent_id:
                ps.n_interrupts += 1
            self._event(ts, "user", "interrupt", text.strip())
        elif kind == "bash_input":
            self._event(ts, "user", "bash_input", _TAG_RE.sub("", text).strip())
        elif kind == "meta":
            skill = re.match(r"Base directory for this skill: (\S+)", text.strip())
            if skill:
                ps.skills[Path(skill.group(1)).name] += 1
                self._event(ts, "user", "meta", f"[skill loaded: {Path(skill.group(1)).name}]")
            else:
                self._event(ts, "user", "meta", text)
        elif kind == "command_output":
            self._event(ts, "user", "command_output", _TAG_RE.sub("", text).strip())
        else:
            self._event(ts, "user", kind, text)

    def _prompt(self, text: str, ts: str | None, *, images: int = 0, meta: dict | None = None) -> None:
        ps = self.ps
        text = safe_text(text)
        if images:
            ps.n_images += images
            text = (text + "\n" if text else "") + " ".join(["[image]"] * images)
        if not text:
            return
        if self.agent_id:
            # a subagent's task prompt is recorded, but it is not a human prompt
            self._event(ts, "user", "prompt", text, meta={"subagent": True, **(meta or {})})
            return
        ps.n_prompts += 1
        if ps.first_prompt is None:
            ps.first_prompt = text
        ps.last_prompt = text
        self._event(ts, "user", "prompt", text, meta=meta)

    def _tool_result(self, block: dict, tur, ts: str | None) -> None:
        ps = self.ps
        tid = block.get("tool_use_id")
        if tid:
            if tid in ps._seen_result_ids:
                return  # the same result recorded twice (a replayed record under a new uuid)
            ps._seen_result_ids.add(tid)
        text, images = _result_text(block.get("content"))
        ps.n_result_images += images
        is_error = bool(block.get("is_error"))
        call, started = self.pending.pop(tid, (None, None))
        name = call.name if call else None
        if call:
            call.is_error = is_error
            call.result_chars = len(text)
            end = parse_ts(ts)
            if started and end:
                call.duration_ms = max(int((end - started).total_seconds() * 1000), 0)
            if is_error:
                self.n_tool_errors += 1
            elif call.file_path:
                fs = ps.files.setdefault(call.file_path, FileStat())
                if name in READ_TOOLS:
                    fs.reads += 1
                elif name in EDIT_TOOLS | WRITE_TOOLS:
                    if name in WRITE_TOOLS:
                        fs.writes += 1
                    else:
                        fs.edits += 1
                    a, r = patch_stats(tur)
                    fs.lines_added += a
                    fs.lines_removed += r
            if not is_error:
                if name in WRITE_TOOLS and call.file_path:
                    artifacts.file_written(ps, call.file_path, content=(call.input or {}).get("content"),
                                           created=not (isinstance(tur, dict) and tur.get("type") == "update"),
                                           ts=ts, agent_id=self.agent_id, tool_use_id=tid)
                artifacts.from_tool(ps, name, call.input, text, ts=ts, agent_id=self.agent_id, tool_use_id=tid,
                                    command=call.command, tur=tur)
            if name in AGENT_TOOLS and isinstance(tur, dict) and tur.get("agentId"):
                sub = ps.subagents.setdefault(tur["agentId"], Subagent(tur["agentId"]))
                sub.tool_use_id = sub.tool_use_id or tid
                sub.agent_type = sub.agent_type or tur.get("agentType")
                sub.model = sub.model or normalize_model(tur.get("resolvedModel"))
        self._event(
            ts,
            "user",
            "tool_result",
            text,
            limit_key="tool_error" if is_error else "tool_result",
            tool_name=name,
            tool_use_id=tid,
            is_error=is_error,
        )

    # -------------------------------------------------------------- assistant
    def _assistant(self, d: dict, ts: str | None) -> None:
        msg = d.get("message") or {}
        model = msg.get("model")
        msg_id = msg.get("id") or d.get("requestId") or d.get("uuid")
        synthetic = not model or model.startswith("<")
        if not synthetic:
            norm = normalize_model(model)
            self.model_seen = norm
            usage = msg.get("usage")
            if usage and msg_id:
                prev = self.usage.get(msg_id)
                self.usage[msg_id] = {
                    "model": norm,
                    "usage": usage,
                    "ts": prev["ts"] if prev else ts,
                    "speed": usage.get("speed"),
                }
        if d.get("isApiErrorMessage"):
            text, _ = _result_text(msg.get("content"))
            self._event(ts, "assistant", "api_error", text)
            return
        for b in _content_blocks(msg.get("content")):
            bt = b.get("type")
            if bt == "text":
                txt = (b.get("text") or "").strip()
                if not txt:
                    continue
                self._event(ts, "assistant", "notice" if synthetic else "text", txt, meta={"model": normalize_model(model)} if not synthetic else None)
            elif bt == "thinking":
                txt = (b.get("thinking") or "").strip()
                if txt:
                    self._event(ts, "assistant", "thinking", txt)
            elif bt == "tool_use":
                self._tool_use(b, ts)

    def _tool_use(self, b: dict, ts: str | None) -> None:
        ps = self.ps
        if b.get("id"):
            if b["id"] in ps._seen_tool_ids:
                return  # one tool call, recorded twice
            ps._seen_tool_ids.add(b["id"])
        name = b.get("name") or "?"
        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
        summary, fp, cmd = summarize_tool_use(name, inp, ps.project_path)
        mcp = name.split("__")[1] if name.startswith("mcp__") and name.count("__") >= 2 else None
        name = safe_text(name)
        call = ToolCall(self.agent_id, b.get("id"), ts, name, mcp, safe_text(summary), fp, cmd, input=inp)
        ps.tool_calls.append(call)
        ps.tools[name] += 1
        self.n_tool_calls += 1
        if mcp:
            ps.mcp_servers[mcp] += 1
        if name == "Skill" and (inp.get("skill") or inp.get("command")):
            ps._skill_calls[str(inp.get("skill") or inp.get("command")).lstrip("/")] += 1
        if b.get("id"):
            self.pending[b["id"]] = (call, parse_ts(ts))
        try:
            compact_input = truncate(json.dumps(inp, ensure_ascii=False, default=str), 1_500) if inp else None
        except (TypeError, ValueError):
            compact_input = None
        self._event(
            ts,
            "assistant",
            "tool_use",
            f"{name}: {summary}" if summary and summary != name else name,
            tool_name=name,
            tool_use_id=b.get("id"),
            meta={"input": compact_input} if compact_input else None,
        )

    # -------------------------------------------------------------- system / attachment
    def _system(self, d: dict, ts: str | None) -> None:
        ps = self.ps
        sub = d.get("subtype")
        if sub == "compact_boundary":
            meta = d.get("compactMetadata") or {}
            if not self.agent_id:
                ps.n_compactions += 1
            self._event(
                ts,
                "system",
                "compact",
                f"Context compacted ({meta.get('trigger', '?')}, {meta.get('preTokens', '?')} tokens before)",
                meta={"trigger": meta.get("trigger"), "pre_tokens": meta.get("preTokens")},
            )
        elif sub == "api_error":
            # each retry of a failing request is logged too, parented to the attempt before it: count the failure once
            retry = (d.get("retryAttempt") or 1) > 1 and d.get("parentUuid") in self.api_error_uuids
            if d.get("uuid"):
                self.api_error_uuids.add(d["uuid"])
            if not retry:
                ps.n_api_errors += 1
                err = d.get("error") or {}
                self._event(ts, "system", "api_error", err.get("formatted") or err.get("message") or "API error")
        elif sub in ("informational", "model_refusal_fallback", "model_refusal_no_fallback", "local_command"):
            self._event(ts, "system", "notice", d.get("content") or sub)

    def _attachment(self, d: dict, ts: str | None) -> None:
        ps = self.ps
        a = d.get("attachment") or {}
        at = a.get("type")
        if at == "queued_command":
            prompt = a.get("prompt")
            text = prompt if isinstance(prompt, str) else "\n".join(
                b.get("text", "") for b in _content_blocks(prompt) if b.get("type") == "text"
            )
            mode = a.get("commandMode")
            if mode == "prompt" and not a.get("isMeta"):
                self._prompt(clean_prompt(text), ts, meta={"queued": True})
            elif mode == "task-notification" or (a.get("origin") or {}).get("kind") in ("coordinator", "peer"):
                self._event(ts, "user", "notification", text)
        elif at in ("hook_success", "hook_additional_context") and not self.agent_id:
            name = a.get("hookName") or a.get("hookEvent") or "hook"
            h = ps.hooks.setdefault(name, {"count": 0, "ms": 0})
            h["count"] += 1
            h["ms"] += a.get("durationMs") or 0
        elif at == "invoked_skills":
            for s in a.get("skills") or []:
                if isinstance(s, dict) and s.get("name") and s["name"] not in ps.skills:
                    ps.skills[s["name"]] = 1
        elif at == "file" and a.get("filename"):
            self._event(ts, "user", "attachment", f"[attached file: {_rel(a.get('filename'), ps.project_path)}]")

    # -------------------------------------------------------------- finish
    def finish(self) -> None:
        ps = self.ps
        for msg_id, info in self.usage.items():
            u = info["usage"]
            breakdown = u.get("cache_creation") or {}
            cache_write = u.get("cache_creation_input_tokens") or (
                (breakdown.get("ephemeral_5m_input_tokens") or 0) + (breakdown.get("ephemeral_1h_input_tokens") or 0)
            )
            call = ApiCall(
                self.agent_id,
                msg_id,
                info["ts"],
                info["model"],
                input_tokens=u.get("input_tokens") or 0,
                output_tokens=u.get("output_tokens") or 0,
                cache_read_tokens=u.get("cache_read_input_tokens") or 0,
                cache_write_tokens=cache_write,
                cost_usd=usage_cost(info["model"], u, info.get("speed")),
                speed=info.get("speed"),
            )
            ps.api_calls.append(call)
            if info["model"]:
                ps.models[info["model"]] += call.output_tokens
        # tool calls that never got a result (crash / interrupt) stay without duration
        if self.agent_id:
            sub = ps.subagents.setdefault(self.agent_id, Subagent(self.agent_id))
            sub.started_at = self.first_ts
            sub.ended_at = self.last_ts
            sub.n_tool_calls = self.n_tool_calls
            sub.n_tool_errors = self.n_tool_errors
            sub.model = sub.model or self.model_seen
            for c in ps.api_calls:
                if c.agent_id == self.agent_id:
                    sub.input_tokens += c.input_tokens
                    sub.output_tokens += c.output_tokens
                    sub.cache_read_tokens += c.cache_read_tokens
                    sub.cache_write_tokens += c.cache_write_tokens
                    sub.est_cost_usd += c.cost_usd


# ------------------------------------------------------------------ entry points
def session_dir_for(main_path: Path) -> Path:
    name = main_path.name
    for suffix in (".jsonl.gz", ".jsonl"):
        if name.endswith(suffix):
            return main_path.with_name(name[: -len(suffix)])
    return main_path.with_suffix("")


def _session_id_from(main_path: Path) -> str:
    return session_dir_for(main_path).name


def subagent_files(session_dir: Path) -> list[Path]:
    if not session_dir.is_dir():
        return []
    sub = session_dir / "subagents"
    if not sub.is_dir():
        return []
    found = [p for p in sub.rglob("agent-*.jsonl*") if p.name.endswith((".jsonl", ".jsonl.gz"))]
    return sorted(found)


def _load_meta(agent_file: Path) -> dict:
    base = agent_file.name.split(".jsonl")[0]
    meta_path = agent_file.with_name(base + ".meta.json")
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())
        except (OSError, ValueError):
            return {}
    return {}


def _workflow_records(session_dir: Path) -> list[dict]:
    out = []
    wf_dir = session_dir / "workflows"
    if not wf_dir.is_dir():
        return out
    for p in sorted(wf_dir.glob("wf_*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        out.append(
            {
                "id": d.get("runId") or p.stem,
                "name": d.get("workflowName"),
                "summary": one_line(d.get("summary") or "", 300),
                "status": d.get("status"),
                "agents": d.get("agentCount"),
                "tokens": d.get("totalTokens"),
                "tool_calls": d.get("totalToolCalls"),
                "duration_ms": d.get("durationMs"),
                "ts": d.get("timestamp"),
            }
        )
    return out


def parse_session(main_path: Path, session_dir: Path | None = None) -> ParsedSession:
    """Parse a main transcript plus its subagent/workflow files."""
    ps = ParsedSession(id=_session_id_from(main_path))
    main = _Thread(ps)
    legacy_threads: dict[str, _Thread] = {}
    for d in iter_jsonl(main_path):
        if d.get("isSidechain") and d.get("type") in ("user", "assistant", "system", "attachment"):
            aid = d.get("agentId") or "sidechain"
            thread = legacy_threads.setdefault(aid, _Thread(ps, aid))
            thread.feed(d)
            continue
        main.feed(d)
    main.finish()
    for thread in legacy_threads.values():
        thread.finish()

    session_dir = session_dir or session_dir_for(main_path)
    for f in subagent_files(session_dir):
        agent_id = f.name.split(".jsonl")[0].removeprefix("agent-")
        meta = _load_meta(f)
        sub = ps.subagents.setdefault(agent_id, Subagent(agent_id))
        sub.agent_type = safe_text(meta.get("agentType")) or sub.agent_type
        sub.description = safe_text(meta.get("description")) or sub.description
        sub.tool_use_id = meta.get("toolUseId") or sub.tool_use_id
        sub.phase = meta.get("workflowPhase") or sub.phase
        if f.parent.name.startswith("wf_"):
            sub.workflow_id = f.parent.name
        thread = _Thread(ps, agent_id)
        for d in iter_jsonl(f):
            thread.feed(d)
        thread.finish()
    ps.workflows = _workflow_records(session_dir)

    # session-level aggregates
    stamps = sorted(ps._timestamps)
    if stamps:
        ps.started_at, ps.ended_at = stamps[0], stamps[-1]
        start, end = parse_ts(stamps[0]), parse_ts(stamps[-1])
        if start and end:
            ps.duration_s = (end - start).total_seconds()
        active, prev = 0.0, None
        for s in stamps:
            cur = parse_ts(s)
            if cur and prev:
                active += min(max((cur - prev).total_seconds(), 0.0), IDLE_GAP_CAP_S)
            prev = cur or prev
        ps.active_s = active
    if ps._cc_costs:
        ps.cc_cost_usd = float(sum(ps._cc_costs.values()))
    # a Skill tool call and the skill body it injects describe the same load: count the larger
    for name, n in ps._skill_calls.items():
        key = next((k for k in ps.skills if k == name or k.endswith(":" + name) or name.endswith(":" + k)), name)
        ps.skills[key] = max(ps.skills.get(key, 0), n)
    ps.events.sort(key=lambda e: (e.agent_id != "", e.agent_id, e.seq))
    return ps


def parse_history(path: Path) -> dict[str, dict]:
    """Group ~/.claude/history.jsonl prompt records by session id."""
    sessions: dict[str, dict] = {}
    for d in iter_jsonl(path):
        sid = d.get("sessionId")
        if not sid:
            continue
        ts = parse_ts(d.get("timestamp"))
        rec = sessions.setdefault(sid, {"project": d.get("project"), "prompts": []})
        rec["prompts"].append((to_iso(ts), d.get("display") or ""))
    for rec in sessions.values():
        rec["prompts"].sort(key=lambda p: p[0] or "")
    return sessions
