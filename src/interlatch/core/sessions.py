"""What every source's parser produces (a ParsedSession: events, tool calls, API calls, files, subagents), and
the limits all parsers apply to the text they keep."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .i18n import lang, tr
from .util import one_line

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
    "guidance": 8_000,
}
SEARCHABLE_KINDS = {"prompt", "text", "tool_use", "command", "bash_input"}
# What Interlatch's SessionStart hook (runtime/hooks.py) put in front of a session opens with one of these, also under
# the name before the rename. The parser keeps that context as a `guidance` event, so the analysis can tell a lesson
# the session found from one it was told (analysis.digest.session_guidance).
GUIDANCE_OPENING = re.compile(r"^(?:(?:Interlatch|Chronicle) (?:knowledge base for |notes from past coding-agent sessions)"
                              r"|From teammates' sessions in this project|Recent sessions here: )")

IDLE_GAP_CAP_S = 15 * 60


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


def best_title(row: dict) -> str:
    for key in ("llm_title", "custom_title", "ai_title"):
        if row.get(key):
            return one_line(row[key], 120)
    return one_line(row.get("first_prompt") or "(no prompt)", 90)


# why a session was not analyzed, as ingest, analyze and chat_import store it (the analyzer's own errors aside)
ANALYSIS_REASONS = ("too little content", "too few prompts", "excluded project", "session continued during analysis",
                    "session continued after analysis", "history only (transcript deleted before Interlatch)")
NOT_ANALYZED_CHAT = "imported {label} chat: not analyzed automatically (Analyze now, or import with --analyze)"
NOT_ANALYZED_CAPTURED = "captured {label} chat: not analyzed automatically (Analyze now, or screen chats on Sources)"
RENAMED_REASONS = {  # what ingest stored before Chronicle became Interlatch, shown as it reads now
    "history only (transcript deleted before Chronicle)": "history only (transcript deleted before Interlatch)"}


def analysis_reason_text(reason: str | None) -> str | None:
    """A stored analysis_reason in the dashboard viewer's language (i18n), when it is one Interlatch writes itself."""
    reason = RENAMED_REASONS.get(reason, reason)
    if not reason or lang.get() == "en":
        return reason
    m = re.fullmatch(r"imported (.+) chat: not analyzed automatically \(Analyze now, or import with --analyze\)", reason)
    if m:
        return tr(NOT_ANALYZED_CHAT, label=m.group(1))
    m = re.fullmatch(r"captured (.+) chat: not analyzed automatically \(Analyze now, or screen chats on Sources\)", reason)
    if m:
        return tr(NOT_ANALYZED_CAPTURED, label=m.group(1))
    return tr(reason) if reason in ANALYSIS_REASONS else reason
