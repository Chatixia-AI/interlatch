"""Condense a stored session into a transcript digest sized for an LLM call.

Three detail levels are tried in order (3 = rich, 1 = skeleton). If even the compact
level exceeds the budget, the digest is split at prompt boundaries for map-reduce analysis.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass

from .redact import redact
from .agents import full_name, speaker
from .util import human_count, human_duration, local_str, loads, one_line, truncate

CAPS = {
    # level: (prompt, assistant text, error result, ok result for Bash, agent result, notification)
    3: (6000, 3000, 800, 300, 1500, 200),
    2: (3000, 1200, 300, 0, 700, 0),
    1: (1500, 400, 150, 0, 300, 0),
}
COLLAPSIBLE = {"Read", "Grep", "Glob", "ToolSearch", "NotebookRead", "LS"}


@dataclass
class Digest:
    level: int
    text: str
    chunks: list[str]

    @property
    def chars(self) -> int:
        return len(self.text)


def agent_label(agent: str | None) -> str:
    return full_name(agent)


def assistant_name(agent: str | None) -> str:
    return speaker(agent)


def session_header(s: dict, files: list[dict]) -> str:
    tools = loads(s.get("tools_json"), {}) or {}
    top_tools = ", ".join(f"{k}×{v}" for k, v in list(tools.items())[:10])
    edited = [f for f in files if f["edits"] or f["writes"]]
    edited.sort(key=lambda f: -(f["lines_added"] + f["lines_removed"]))
    edited_txt = ", ".join(f"{f['path']} (+{f['lines_added']}/-{f['lines_removed']})" for f in edited[:15])
    lines = [
        f"agent: {agent_label(s.get('agent'))}" + (" (session imported into Codex)" if s.get("source") == "codex-import" else ""),
        f"project: {s.get('project_name')} ({s.get('project_path')})",
        f"git branch: {s.get('git_branch') or '-'}",
        f"started: {local_str(s.get('started_at'))}  wall: {human_duration(s.get('duration_s'))}  active: {human_duration(s.get('active_s'))}",
        f"model: {s.get('primary_model') or '-'}  prompts: {s.get('n_prompts')}  tool calls: {s.get('n_tool_calls')} "
        f"({s.get('n_tool_errors')} errors)  subagents: {s.get('n_subagents')}  compactions: {s.get('n_compactions')}",
        f"tokens: {human_count((s.get('input_tokens') or 0) + (s.get('output_tokens') or 0) + (s.get('cache_read_tokens') or 0) + (s.get('cache_write_tokens') or 0))}",
        f"tools: {top_tools or '-'}",
        f"files changed: {edited_txt or '-'}  (total +{s.get('lines_added') or 0}/-{s.get('lines_removed') or 0})",
    ]
    prs = loads(s.get("prs_json"), []) or []
    if prs:
        lines.append("pull requests: " + ", ".join(p.get("url") or "" for p in prs))
    if s.get("ai_title"):
        lines.append(f"auto title: {s['ai_title']}")
    return "\n".join(lines)


def _render(events: list[dict], calls: dict, level: int, who: str = "CLAUDE") -> list[str]:
    cap_prompt, cap_text, cap_err, cap_ok, cap_agent, cap_note = CAPS[level]
    out: list[str] = []
    n_prompt = 0
    run_name, run_items = None, []  # collapsing consecutive read-only tool calls
    turn_tools: Counter = Counter()

    def flush_run():
        nonlocal run_name, run_items
        if run_name and run_items:
            if len(run_items) == 1:
                out.append(f"  → {run_name}: {run_items[0]}")
            else:
                shown = "; ".join(run_items[:6]) + ("; …" if len(run_items) > 6 else "")
                out.append(f"  → {run_name} ×{len(run_items)}: {shown}")
        run_name, run_items = None, []

    def flush_turn():
        if level == 1 and turn_tools:
            out.append("  → tools: " + ", ".join(f"{k}×{v}" for k, v in turn_tools.most_common(8)))
            turn_tools.clear()

    for e in events:
        kind = e["kind"]
        if kind != "tool_use" or level == 1:
            flush_run()
        if kind == "prompt":
            flush_turn()
            n_prompt += 1
            queued = " (sent while the agent was working)" if (loads(e.get("meta_json"), {}) or {}).get("queued") else ""
            out.append(f"\n### [{n_prompt}] USER {local_str(e['ts'], '%m-%d %H:%M')}{queued}")
            out.append(truncate(e["text"], cap_prompt))
        elif kind == "text":
            out.append(f"{who}: " + truncate(e["text"], cap_text))
        elif kind == "tool_use":
            name = e.get("tool_name") or "tool"
            summary = e["text"].split(": ", 1)[1] if ": " in e["text"] else e["text"]
            if level == 1:
                turn_tools[name] += 1
                continue
            if level == 2 and name in COLLAPSIBLE:
                if run_name != name:
                    flush_run()
                    run_name = name
                run_items.append(one_line(summary, 90))
                continue
            flush_run()
            out.append(f"  → {name}: {one_line(summary, 240)}")
        elif kind == "tool_result":
            name = e.get("tool_name") or ""
            if e["is_error"]:
                out.append("    ✗ " + one_line(e["text"], cap_err))
            elif name in ("Agent", "Task") and cap_agent:
                out.append("    ⮑ subagent report: " + truncate(e["text"], cap_agent))
            elif name in ("Bash", "exec", "exec_command") and cap_ok and e["text"].strip():
                out.append("    ⮑ " + one_line(e["text"], cap_ok))
            elif name == "AskUserQuestion":
                out.append("    ⮑ user answered: " + one_line(e["text"], 400))
        elif kind == "command":
            out.append(f"\nUSER ran command: {one_line(e['text'], 300)}")
        elif kind == "bash_input":
            out.append(f"\nUSER ran shell command: {one_line(e['text'], 300)}")
        elif kind == "interrupt":
            out.append("[user interrupted Claude]")
        elif kind == "compact":
            out.append("[context window compacted]")
        elif kind == "api_error" and level == 3:
            out.append(f"[API error: {one_line(e['text'], 160)}]")
        elif kind == "notification" and cap_note:
            out.append(f"[notification: {one_line(e['text'], cap_note)}]")
        elif kind == "meta" and e["text"].startswith("[skill loaded"):
            out.append(e["text"])
        elif kind == "attachment":
            out.append(e["text"])
    flush_run()
    flush_turn()
    return out


def _split(lines: list[str], budget: int) -> list[str]:
    chunks, cur, size = [], [], 0
    for line in lines:
        starts_turn = line.startswith("\n### [")
        if cur and size + len(line) > budget and (starts_turn or size > budget * 1.15):
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def build_digest(conn: sqlite3.Connection, session_id: str, budget: int) -> tuple[str, Digest]:
    s = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())
    files = [dict(r) for r in conn.execute(
        "SELECT path, reads, edits, writes, lines_added, lines_removed FROM session_files WHERE session_id = ?", (session_id,)
    )]
    root = (s.get("project_path") or "").rstrip("/") + "/"
    for f in files:
        if f["path"].startswith(root):
            f["path"] = f["path"][len(root):]
    header = redact(session_header(s, files))
    events = [dict(r) for r in conn.execute(
        "SELECT seq, ts, kind, tool_name, is_error, text, meta_json FROM events "
        "WHERE session_id = ? AND agent_id = '' ORDER BY seq", (session_id,)
    )]
    # Preference: full detail in one call > compact in one call > compact map-reduce (<= 4 chunks)
    # > skeleton in one call > skeleton map-reduce (<= 10 chunks, middle sampled).
    who = assistant_name(s.get("agent"))
    for level in (3, 2):
        lines = _render(events, {}, level, who)
        text = redact("\n".join(lines).strip())
        if len(text) <= budget:
            return header, Digest(level, text, [text])
    if len(text) <= budget * 4:
        return header, Digest(2, text, [redact(c) for c in _split(lines, budget)])
    lines = _render(events, {}, 1, who)
    text = redact("\n".join(lines).strip())
    if len(text) <= budget:
        return header, Digest(1, text, [text])
    chunks = [redact(c) for c in _split(lines, budget)]
    if len(chunks) > 10:  # keep the opening, the ending and an even sample of the middle
        chunks = [chunks[0], *chunks[1:-1][:: max(1, (len(chunks) - 2) // 8)][:8], chunks[-1]]
    return header, Digest(1, text, chunks)
