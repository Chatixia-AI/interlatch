"""Shared read models: session records, project labels, and Markdown renderings."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter

from .redact import redact
from .agents import full_name
from .i18n import lang, tr
from .util import human_cost, human_count, human_duration, local_str, loads, one_line

OUTCOME_ICON = {
    "completed": "✅", "partial": "🟡", "blocked": "⛔", "abandoned": "🗑️", "exploratory": "🧭", "unclear": "❔",
}
KIND_ICON = {
    "fix": "🐛", "gotcha": "⚠️", "learning": "💡", "decision": "🧭", "pattern": "🧩", "command": "⌨️",
    "fact": "📌", "preference": "🙋", "reference": "🔗", "todo": "☑️",
}


# why a session was not analyzed, as ingest, analyze and chat_import store it (the analyzer's own errors aside)
ANALYSIS_REASONS = ("too little content", "too few prompts", "excluded project", "session continued during analysis",
                    "session continued after analysis", "history only (transcript deleted before Interlatch)")
NOT_ANALYZED_CHAT = "imported {label} chat: not analyzed automatically (Analyze now, or import with --analyze)"
RENAMED_REASONS = {  # what ingest stored before Chronicle became Interlatch, shown as it reads now
    "history only (transcript deleted before Chronicle)": "history only (transcript deleted before Interlatch)"}


def reason_text(reason: str | None) -> str | None:
    """A stored analysis_reason in the dashboard viewer's language (i18n), when it is one Interlatch writes itself."""
    reason = RENAMED_REASONS.get(reason, reason)
    if not reason or lang.get() == "en":
        return reason
    m = re.fullmatch(r"imported (.+) chat: not analyzed automatically \(Analyze now, or import with --analyze\)", reason)
    if m:
        return tr(NOT_ANALYZED_CHAT, label=m.group(1))
    return tr(reason) if reason in ANALYSIS_REASONS else reason


def resolve_session_id(conn: sqlite3.Connection, ref: str) -> str | None:
    ref = ref.strip()
    if not ref:
        return None
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (ref,)).fetchone()
    if row:
        return row[0]
    matches = conn.execute("SELECT id FROM sessions WHERE id LIKE ? LIMIT 2", (ref + "%",)).fetchall()
    return matches[0][0] if len(matches) == 1 else None


def project_labels(conn: sqlite3.Connection) -> dict[str, str]:
    """Unique display label per project path (basename, disambiguated by parent when needed)."""
    paths = [r[0] for r in conn.execute("SELECT DISTINCT project_path FROM sessions WHERE project_path IS NOT NULL")]
    names = Counter(p.rstrip("/").rsplit("/", 1)[-1] for p in paths)
    labels = {}
    for p in paths:
        parts = p.rstrip("/").split("/")
        labels[p] = parts[-1] if names[parts[-1]] == 1 or len(parts) < 2 else f"{parts[-2]}-{parts[-1]}"
    return labels


def session_record(conn: sqlite3.Connection, sid: str) -> dict | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not row:
        return None
    s = dict(row)
    for key in ("models", "tools", "skills", "mcp", "commands", "branches", "hooks"):
        s[key] = loads(s.pop(f"{key}_json", None), {}) or {}
    for key in ("prs", "artifacts", "workflows", "work_types", "tags", "highlights", "open_threads", "friction"):
        s[key] = loads(s.pop(f"{key}_json", None), []) or []
    s.pop("analysis_json", None)
    s["files"] = [dict(r) for r in conn.execute(
        "SELECT path, reads, edits, writes, lines_added, lines_removed FROM session_files WHERE session_id = ? "
        "ORDER BY (edits + writes) DESC, (lines_added + lines_removed) DESC, reads DESC", (sid,))]
    s["subagents"] = [dict(r) for r in conn.execute(
        "SELECT * FROM subagents WHERE session_id = ? ORDER BY started_at", (sid,))]
    s["knowledge"] = [dict(r) for r in conn.execute(
        "SELECT * FROM knowledge WHERE session_id = ? AND status != 'dismissed' ORDER BY "
        "CASE kind WHEN 'fix' THEN 0 WHEN 'gotcha' THEN 1 WHEN 'decision' THEN 2 ELSE 3 END, id", (sid,))]
    for k in s["knowledge"]:
        k["tags"] = loads(k.pop("tags_json", None), []) or []
    s["prompts"] = [dict(r) for r in conn.execute(
        "SELECT seq, ts, kind, text FROM events WHERE session_id = ? AND agent_id = '' AND kind IN ('prompt', 'command') "
        "ORDER BY seq", (sid,))]
    s["tool_errors"] = [dict(r) for r in conn.execute(
        "SELECT t.name, t.summary, e.text FROM tool_calls t LEFT JOIN events e ON e.session_id = t.session_id "
        "AND e.tool_use_id = t.tool_use_id AND e.kind = 'tool_result' WHERE t.session_id = ? AND t.is_error = 1 "
        "AND t.agent_id = '' ORDER BY t.ts LIMIT 50", (sid,))]
    s["total_tokens"] = sum(s.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))
    # the computer it ran on, named only when more than one sends sessions here (hub.py)
    machine = conn.execute("SELECT name, role, (SELECT COUNT(*) FROM machines) n FROM machines WHERE id = ?",
                           (s.get("machine_id"),)).fetchone()
    s["machine_name"] = machine["name"] if machine and machine["n"] > 1 else None
    return s


def _rel(path: str, root: str | None) -> str:
    if root and path.startswith(root.rstrip("/") + "/"):
        return path[len(root.rstrip("/")) + 1:]
    return path


def case_lines(k: dict) -> list[str]:
    """A fix, gotcha or decision's case file as Markdown lines: how it first showed, and the leads ruled out."""
    case = k.get("case") if isinstance(k.get("case"), dict) else loads(k.get("case_json"), None)
    if not isinstance(case, dict) or not case.get("scene"):
        return []
    out = [f"**Seen as:** {redact(case['scene'])}", ""]
    leads = [x for x in case.get("ruled_out") or [] if isinstance(x, dict) and x.get("lead")]
    if leads:
        out += ["**Ruled out:**", *(f"- {redact(x['lead'])}" + (f" ({redact(x['why'])})" if x.get("why") else "")
                                    for x in leads), ""]
    return out


def session_markdown(s: dict, *, frontmatter: bool = False, project_link: str | None = None,
                     include_prompts: bool = True, max_prompts: int = 60) -> str:
    """Render a session record (from session_record) as a Markdown overview."""
    out: list[str] = []
    if frontmatter:
        fm = {
            "session_id": s["id"],
            "agent": s.get("agent") or "claude",
            "project": s.get("project_name"),
            "project_path": s.get("project_path"),
            "date": local_str(s.get("started_at"), "%Y-%m-%d"),
            "started": local_str(s.get("started_at"), "%Y-%m-%dT%H:%M"),
            "ended": local_str(s.get("ended_at"), "%Y-%m-%dT%H:%M"),
            "duration": human_duration(s.get("duration_s")),
            "active": human_duration(s.get("active_s")),
            "branch": s.get("git_branch"),
            "model": s.get("primary_model"),
            "prompts": s.get("n_prompts"),
            "tool_calls": s.get("n_tool_calls"),
            "tokens": s.get("total_tokens"),
            "est_cost_usd": round(s.get("est_cost_usd") or 0, 2),
            "outcome": s.get("outcome"),
            "source": s.get("source"),
            "tags": s.get("tags") or [],
        }
        out.append("---")
        for k, v in fm.items():
            if v is None or v == "":
                continue
            if isinstance(v, list):
                out.append(f"{k}: [{', '.join(_yaml_str(x) for x in v)}]")
            else:
                out.append(f"{k}: {_yaml_str(v)}")
        out.append("---")
        out.append("")
    out.append(f"# {s.get('title') or '(untitled session)'}")
    out.append("")
    project = project_link or f"`{s.get('project_name')}`"
    meta = [
        f"**Project:** {project} (`{s.get('project_path')}`)" + (f" · branch `{s['git_branch']}`" if s.get("git_branch") else ""),
        f"**When:** {local_str(s.get('started_at'))} → {local_str(s.get('ended_at'), '%H:%M' if (s.get('started_at') or '')[:10] == (s.get('ended_at') or '')[:10] else '%Y-%m-%d %H:%M')}"
        f" · active {human_duration(s.get('active_s'))} (wall {human_duration(s.get('duration_s'))})",
    ]
    if s.get("source") in ("transcript", "codex-import"):
        agent = full_name(s.get("agent"))
        via = " (recovered from Codex's import of it)" if s.get("source") == "codex-import" else ""
        meta.append(f"**Agent:** {agent} {s.get('cc_version') or ''}{via}")
        meta.append(
            f"**Activity:** {s.get('n_prompts')} prompts · {s.get('n_tool_calls')} tool calls ({s.get('n_tool_errors')} failed) · "
            f"{s.get('n_subagents')} subagents · +{s.get('lines_added')}/−{s.get('lines_removed')} lines in {s.get('n_files')} files"
        )
        meta.append(
            f"**Usage:** {human_count(s.get('total_tokens'))} tokens · est. {human_cost(s.get('est_cost_usd'))} API-equivalent · "
            f"peak context {human_count(s.get('peak_context'))} · model {s.get('primary_model') or '-'}"
        )
    else:
        meta.append(f"**Recovered from prompt history:** {s.get('analysis_reason') or ''}")
    out.append("  \n".join(meta))
    out.append("")
    if s.get("summary"):
        outcome = s.get("outcome") or "unclear"
        out += [f"> {redact(s['summary'])}", ""]
        if s.get("goal"):
            out.append(f"**Goal:** {redact(s['goal'])}  ")
        out.append(f"**Outcome:** {OUTCOME_ICON.get(outcome, '')} {outcome}" + (f" — {redact(s['outcome_note'])}" if s.get("outcome_note") else ""))
        if s.get("tags"):
            out.append("  \n**Tags:** " + " ".join(f"#{t.replace(' ', '-')}" for t in s["tags"]))
        out.append("")
    elif s.get("source") != "history":
        out += [f"_Not analyzed yet ({s.get('analysis_status')}{': ' + s['analysis_reason'] if s.get('analysis_reason') else ''})._", ""]
    if s.get("highlights"):
        out += ["## Highlights", ""] + [f"- {redact(h)}" for h in s["highlights"]] + [""]
    if s.get("knowledge"):
        out += ["## Knowledge", ""]
        for k in s["knowledge"]:
            badge = f"{KIND_ICON.get(k['kind'], '•')} {k['kind']}"
            conf = f" · {k['confidence']} confidence" if k.get("confidence") else ""
            status = f" · _{k['status']}_" if k.get("status") and k["status"] != "active" else ""
            out += [f"### {redact(k['title'])}", f"<sub>{badge}{conf} · {k.get('scope')}{status}</sub>", "",
                    redact(k.get("body") or ""), "", *case_lines(k)]
    if s.get("open_threads"):
        out += ["## Open threads", ""] + [f"- [ ] {redact(t)}" for t in s["open_threads"]] + [""]
    if s.get("friction"):
        out += ["## Friction", ""] + [f"- **{f.get('kind')}**: {redact(f.get('note') or '')}" for f in s["friction"]] + [""]
    edited = [f for f in s.get("files") or [] if f["edits"] or f["writes"]]
    if edited:
        out += ["## Files changed", "", "| File | Edits | Lines |", "|---|---:|---:|"]
        for f in edited[:40]:
            out.append(f"| `{_rel(f['path'], s.get('project_path'))}` | {f['edits'] + f['writes']} | +{f['lines_added']}/−{f['lines_removed']} |")
        out.append("")
    if s.get("tools"):
        out += ["## Tools", "", " · ".join(f"{k} ×{v}" for k, v in list(s["tools"].items())[:20]), ""]
    extras = []
    if s.get("skills"):
        extras.append("**Skills:** " + ", ".join(s["skills"]))
    if s.get("mcp"):
        extras.append("**MCP servers:** " + ", ".join(s["mcp"]))
    if s.get("commands"):
        extras.append("**Slash commands:** " + ", ".join(s["commands"]))
    if s.get("prs"):
        extras.append("**Pull requests:** " + ", ".join(f"[{p.get('repo')}#{p.get('number')}]({p.get('url')})" for p in s["prs"]))
    if s.get("artifacts"):
        extras.append("**Artifacts:** " + ", ".join(f"[{a.get('title') or 'artifact'}]({a.get('url')})" for a in s["artifacts"]))
    if s.get("workflows"):
        extras.append("**Workflows:** " + ", ".join(f"{w.get('name')} ({w.get('status')})" for w in s["workflows"]))
    if extras:
        out += ["## Context", ""] + [e + "  " for e in extras] + [""]
    if s.get("subagents"):
        out += ["## Subagents", "", "| Type | Task | Tools | Cost |", "|---|---|---:|---:|"]
        for a in s["subagents"][:30]:
            out.append(f"| {a.get('agent_type') or '-'} | {one_line(a.get('description') or '', 80)} | {a.get('n_tool_calls')} | {human_cost(a.get('est_cost_usd'))} |")
        out.append("")
    if include_prompts and s.get("prompts"):
        out += ["## Prompts", ""]
        for i, p in enumerate(s["prompts"][:max_prompts], 1):
            text = redact(one_line(p["text"], 400))
            out.append(f"{i}. `{local_str(p['ts'], '%H:%M')}` {text}")
        if len(s["prompts"]) > max_prompts:
            out.append(f"\n_…{len(s['prompts']) - max_prompts} more prompts_")
        out.append("")
    out.append(f"<sub>session `{s['id']}` · {full_name(s.get('agent'))} "
               f"{s.get('cc_version') or '?'} · {s.get('entrypoint') or ''}</sub>")
    return "\n".join(out).rstrip() + "\n"


def _yaml_str(v) -> str:
    if isinstance(v, (int, float)):
        return str(v)
    text = str(v).replace("\n", " ")
    if any(c in text for c in ':#[]{},&*!|>\'"%@`') or text.strip() != text or not text:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text
