"""A session as Markdown: its numbers, what it was about, the lessons it taught, and its prompts; the vault
export, session exports, the CLI and the MCP server all write it this way."""

from __future__ import annotations

from ...core.agents import full_name
from ...core.redact import redact
from ...core.util import human_cost, human_count, human_duration, loads, local_str, one_line

OUTCOME_ICON = {
    "completed": "✅", "partial": "🟡", "blocked": "⛔", "abandoned": "🗑️", "exploratory": "🧭", "unclear": "❔",
}
KIND_ICON = {
    "fix": "🐛", "gotcha": "⚠️", "learning": "💡", "decision": "🧭", "pattern": "🧩", "command": "⌨️",
    "fact": "📌", "preference": "🙋", "reference": "🔗", "todo": "☑️",
}


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
