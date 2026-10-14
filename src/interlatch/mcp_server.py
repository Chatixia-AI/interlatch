"""MCP server (stdio, JSON-RPC 2.0) that lets coding agents and other MCP clients query the Interlatch vault."""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta

from . import __version__
from .config import Config
from .agents import AGENTS, short_name, speaker
from .artifacts import KINDS as ARTIFACT_KINDS
from .artifacts import STATUS as ARTIFACT_STATUS
from .db import connect
from .hub import team_from
from .ladder import stage_label
from .redact import redact
from .search import search_knowledge, search_sessions
from .synthesize import GLOBAL, kb_for_path
from .util import human_duration, local_str, one_line, to_iso, truncate, utcnow
from .views import case_lines, project_labels, resolve_session_id, session_markdown, session_record

SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = (
    "Interlatch is the user's local archive of every past coding-agent session (Claude Code, Codex, GitHub Copilot, IBM Bob, Google Antigravity), "
    "plus chats imported from claude.ai and ChatGPT and tasks run in Codex Cloud, with AI-extracted knowledge "
    "(fixes, gotchas, decisions, project facts, commands, preferences), per-project knowledge bases, and the artifacts "
    "sessions made (documents, pages, diagrams, decks, published links, pull requests, commits). "
    "Use it to recall how something was solved before, why a decision was made, how a project is run or "
    "deployed, what the user prefers, or where an earlier deliverable is, before re-deriving it."
)

_PROJECT_PROP = {"type": "string", "description": "Project path or name. Defaults to all projects."}
_AGENT_PROP = {"type": "string", "enum": list(AGENTS),
               "description": "Only sessions from this agent: " + ", ".join(f"{k} ({v[0]})" for k, v in AGENTS.items())
               + ". Defaults to all."}
# every tool only reads the local vault: clients may run them without asking, and nothing reaches the network
TOOLS = [
    {
        "name": "search_knowledge",
        "annotations": {"title": "Search knowledge", "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "description": "Search knowledge extracted from past coding-agent sessions (fixes, gotchas, decisions, "
                       "facts, commands, preferences), including teammates' lessons when this computer shares with "
                       "a team hub. Best first stop for 'have we solved this before?'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to search for (substring match, 3+ characters work best)"},
                "project": _PROJECT_PROP,
                "kind": {"type": "string", "enum": ["fix", "gotcha", "learning", "decision", "pattern", "command",
                                                    "fact", "preference", "reference", "todo"]},
                "limit": {"type": "integer", "default": 15},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_sessions",
        "annotations": {"title": "Search sessions", "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "description": "Full-text search across past session transcripts (prompts, the agent's replies, tool calls) "
                       "and session summaries, imported claude.ai and ChatGPT chats included. Returns matching "
                       "sessions with snippets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "project": _PROJECT_PROP,
                "agent": _AGENT_PROP,
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_session",
        "annotations": {"title": "Get session", "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "description": "Overview of one past session: summary, outcome, knowledge, files changed, prompts.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string", "description": "Session id or unique prefix"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "get_transcript",
        "annotations": {"title": "Get transcript", "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "description": "Read part of a past session's conversation (user prompts and the agent's replies, optionally tool calls).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "offset": {"type": "integer", "default": 0, "description": "Index of the first message to return"},
                "limit": {"type": "integer", "default": 40},
                "include_tools": {"type": "boolean", "default": False},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "project_knowledge",
        "annotations": {"title": "Project knowledge", "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "description": "The synthesized knowledge base for a project (architecture, how to run/test/deploy, "
                       "gotchas, decisions, open threads). Defaults to the current working directory's project. "
                       "Use project='global' for the cross-project playbook.",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}},
    },
    {
        "name": "glossary",
        "annotations": {"title": "Glossary", "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "description": "The developer's own vocabulary: internal system and service names, acronyms, domain terms "
                       "(incl. translations). With a term: its definition, aliases, how each project uses it and where "
                       "it appears. Without: the glossary of a project (default: the current one).",
        "inputSchema": {
            "type": "object",
            "properties": {"term": {"type": "string"}, "project": {"type": "string"}},
        },
    },
    {
        "name": "find_artifacts",
        "annotations": {"title": "Find artifacts", "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "description": "Find what past sessions made: documents, HTML pages, diagrams, decks, spreadsheets, images, "
                       "published links (claude.ai artifacts, docs), pull requests and commits. Each result says where it "
                       "is (path or URL), whether the file is still on disk as written, and which session made it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words in the title, path or URL. Defaults to everything."},
                "kind": {"type": "string", "enum": list(ARTIFACT_KINDS)},
                "project": _PROJECT_PROP,
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "recent_sessions",
        "annotations": {"title": "Recent sessions", "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "description": "List recent sessions, optionally for one project or one agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "agent": _AGENT_PROP,
                "days": {"type": "integer", "default": 14},
                "limit": {"type": "integer", "default": 15},
            },
        },
    },
]


class Tools:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.conn = connect(cfg.db_path)
        self.cwd = os.getcwd()

    def _resolve_project(self, project: str | None) -> str | None:
        if not project:
            return None
        row = self.conn.execute(
            "SELECT project_path FROM sessions WHERE project_path = ? OR project_name = ? LIMIT 1", (project, project)
        ).fetchone()
        if row:
            return row[0]
        for path, label in project_labels(self.conn).items():
            if label.lower() == project.lower() or path.lower().endswith("/" + project.lower()):
                return path
        return project

    def search_knowledge(self, query: str, project: str | None = None, kind: str | None = None, limit: int = 15) -> str:
        rows = search_knowledge(self.conn, query, project=self._resolve_project(project), kind=kind, limit=limit)
        if not rows:
            return f"No knowledge found for {query!r}."
        out = [f"{len(rows)} knowledge item(s) for {query!r}:\n"]
        for k in rows:
            src = f"session {k['session_id'][:8]} ({local_str(k.get('session_started'), '%Y-%m-%d')})" if k["session_id"] else k["source"]
            if k["source"] == "team":  # a teammate's lesson the team hub sent: whose sessions stated it
                src = "teammates' sessions" + (f" on {', '.join(who)}" if (who := team_from(k)) else "") + ", via the team hub"
            trust = stage_label(k) + (f" ({k['stage_reason']})" if k.get("stage_reason") else "")
            out.append(f"## [{k['kind']} · {stage_label(k)}] {k['title']}\n_{k.get('project_name') or '-'} · {trust} · "
                       f"{k.get('confidence') or '-'} confidence · {src}_\n\n{truncate(k.get('body') or '', 1500)}\n"
                       + "".join(f"{line}\n" for line in case_lines(k)))
        return "\n".join(out)

    def search_sessions(self, query: str, project: str | None = None, agent: str | None = None, limit: int = 10) -> str:
        rows = search_sessions(self.conn, query, project=self._resolve_project(project), limit=limit * 5 if agent else limit)
        ids = [s["session_id"] for s in rows]
        agents = dict(self.conn.execute(f"SELECT id, agent FROM sessions WHERE id IN ({','.join('?' * len(ids))})", ids)) if ids else {}
        if agent:
            rows = [s for s in rows if (agents.get(s["session_id"]) or "claude") == agent][:limit]
        if not rows:
            return f"No {short_name(agent) + ' ' if agent else ''}sessions matched {query!r}."
        out = [f"{len(rows)} session(s) matching {query!r}:\n"]
        for s in rows:
            out.append(f"- **{s['title']}** — `{s['session_id'][:8]}` · {short_name(agents.get(s['session_id']))} · {s.get('project_name')} · "
                       f"{local_str(s.get('started_at'), '%Y-%m-%d')} · {s.get('outcome') or 'not analyzed'} · {s['hits']} hit(s)")
            for sn in s["snippets"]:
                out.append(f"  - ({sn['kind']}) {sn['text']}")
        out.append("\nUse get_session or get_transcript with the session id for details.")
        return "\n".join(out)

    def get_session(self, session_id: str) -> str:
        sid = resolve_session_id(self.conn, session_id)
        if not sid:
            return f"No unique session matches {session_id!r}."
        return session_markdown(session_record(self.conn, sid))

    def get_transcript(self, session_id: str, offset: int = 0, limit: int = 40, include_tools: bool = False) -> str:
        sid = resolve_session_id(self.conn, session_id)
        if not sid:
            return f"No unique session matches {session_id!r}."
        kinds = ["prompt", "text", "command", "interrupt", "compact"] + (["tool_use", "tool_result"] if include_tools else [])
        rows = self.conn.execute(
            f"SELECT ts, kind, tool_name, is_error, text FROM events WHERE session_id = ? AND agent_id = '' "
            f"AND kind IN ({','.join('?' * len(kinds))}) ORDER BY seq LIMIT ? OFFSET ?",
            [sid, *kinds, limit, offset],
        ).fetchall()
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM events WHERE session_id = ? AND agent_id = '' AND kind IN ({','.join('?' * len(kinds))})",
            [sid, *kinds],
        ).fetchone()[0]
        agent = self.conn.execute("SELECT agent FROM sessions WHERE id = ?", (sid,)).fetchone()[0]
        label = {"prompt": "USER", "text": speaker(agent), "command": "COMMAND", "interrupt": "INTERRUPT",
                 "compact": "COMPACTION", "tool_use": "TOOL", "tool_result": "RESULT"}
        out = [f"Messages {offset}–{offset + len(rows) - 1} of {total} (session {sid[:8]}):\n"]
        for r in rows:
            text = truncate(r["text"] or "", 4000 if r["kind"] in ("prompt", "text") else 600)
            err = " (error)" if r["is_error"] else ""
            out.append(f"[{local_str(r['ts'], '%m-%d %H:%M')}] {label.get(r['kind'], r['kind'])}{err}: {text}\n")
        if offset + len(rows) < total:
            out.append(f"…more: call again with offset={offset + len(rows)}")
        return "\n".join(out)

    def project_knowledge(self, project: str | None = None) -> str:
        if project and project.lower() in ("global", GLOBAL):
            row = self.conn.execute("SELECT * FROM project_kb WHERE project_path = ?", (GLOBAL,)).fetchone()
            return row["markdown"] if row else "No global playbook has been synthesized yet."
        path = self._resolve_project(project) if project else None
        kb = None
        if path:
            row = self.conn.execute("SELECT * FROM project_kb WHERE project_path = ?", (path,)).fetchone()
            kb = dict(row) if row else None
        else:
            from .hooks import _project_here

            kb = kb_for_path(self.conn, self.cwd)
            path = kb["project_path"] if kb else _project_here(self.conn, self.cwd)
        if kb:
            return kb["markdown"]
        items = search_knowledge(self.conn, None, project=path, limit=25)
        if not items:
            return f"No knowledge recorded yet for {path}."
        lines = [f"No synthesized knowledge base yet for {path}; latest knowledge items:\n"]
        lines += [f"- [{k['kind']} · {stage_label(k)}] **{k['title']}** — {one_line(k.get('body') or '', 300)}"
                  + (" (from teammates)" if k["source"] == "team" else "") for k in items]
        return "\n".join(lines)

    def glossary(self, term: str | None = None, project: str | None = None) -> str:
        from .glossary import glossary_entries, lookup

        if term:
            e = lookup(self.conn, term)
            if not e:
                return f"{term!r} is not in the glossary."
            lines = [f"**{e['term']}** ({e['category']})" + (f" — also: {', '.join(e['aliases'])}" if e["aliases"] else ""),
                     "", e["definition"] or ""]
            for u in e["usage"]:
                if u["context"]:
                    lines.append(f"- {u['project_name']}: {u['context']}")
            if e["related"]:
                lines.append("Related: " + ", ".join(e["related"]))
            if e["n_sessions"]:
                lines.append(f"Mentioned in {e['n_sessions']} sessions ({local_str(e['first_seen'], '%Y-%m-%d')} → "
                             f"{local_str(e['last_seen'], '%Y-%m-%d')}): " + ", ".join(
                                 f"{s['title']} `{s['id'][:8]}`" for s in e["top_sessions"][:3]))
            return "\n".join(lines)
        path = self._resolve_project(project) if project else (kb_for_path(self.conn, self.cwd) or {}).get("project_path")
        entries = glossary_entries(self.conn, project=path) if path else glossary_entries(self.conn)
        if not entries:
            return "No glossary yet."
        return "\n".join(f"- **{e['term']}** ({e['category']}): {one_line(e['definition'] or '', 200)}" for e in entries[:120])

    def find_artifacts(self, query: str | None = None, kind: str | None = None, project: str | None = None, limit: int = 20) -> str:
        from .artifacts import query as find
        from .ingest import local_machine_id

        path = self._resolve_project(project) if project else None
        items, _ = find(self.conn, kind=kind or None, project=path, q=(query or "").strip() or None,
                        local_machine=local_machine_id(self.cfg))
        if not items:
            return "No artifacts match." if query or kind or project else "No artifacts recorded yet."
        lines = []
        for a in items[:limit]:
            where = a["url"] or a["path"] or (f"commit {a['meta']['sha'][:7]}" if a["meta"].get("sha") else "")
            more = f" · made in {a['sessions']} sessions" if a["sessions"] > 1 else ""
            lines.append(f"- [{a['kind']}] **{a['title'] or where}** · {where} · {ARTIFACT_STATUS[a['status']]} · "
                         f"{a['project_name'] or '–'} · {local_str(a['ts'])} · session `{a['session_id'][:8]}`{more}")
        if len(items) > limit:
            lines.append(f"… and {len(items) - limit} more")
        return "\n".join(lines)

    def recent_sessions(self, project: str | None = None, agent: str | None = None, days: int = 14, limit: int = 15) -> str:
        since = to_iso(utcnow() - timedelta(days=days))
        params: list = [since]
        sql = ("SELECT id, started_at, title, project_name, outcome, n_prompts, active_s, agent FROM sessions "
               "WHERE started_at >= ?")
        path = self._resolve_project(project)
        if path:
            sql += " AND project_path = ?"
            params.append(path)
        if agent:
            sql += " AND COALESCE(agent, 'claude') = ?"
            params.append(agent)
        rows = self.conn.execute(sql + " ORDER BY started_at DESC LIMIT ?", [*params, limit]).fetchall()
        if not rows:
            return "No sessions in that window."
        return "\n".join(
            f"- {local_str(r['started_at'])} · **{r['title']}** · {r['project_name']} · {short_name(r['agent'])} · "
            f"{r['outcome'] or 'not analyzed'} · "
            f"{r['n_prompts']} prompts · {human_duration(r['active_s'])} · `{r['id'][:8]}`" for r in rows
        )


def _write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve(cfg: Config) -> int:
    tools = Tools(cfg)
    handlers = {t["name"]: getattr(tools, t["name"]) for t in TOOLS}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if isinstance(msg, list):  # JSON-RPC batch (allowed by protocol 2025-03-26)
            replies = [r for r in (_handle(m, handlers) for m in msg) if r is not None]
            if replies:
                sys.stdout.write(json.dumps(replies, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            continue
        reply = _handle(msg, handlers)
        if reply is not None:
            _write(reply)
    return 0


def _handle(msg, handlers) -> dict | None:
    """Handle one JSON-RPC message; returns the response, or None for notifications."""
    if not isinstance(msg, dict) or "method" not in msg:
        return None
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if mid is None:  # notification (initialized, cancelled, ...)
        return None
    try:
        if method == "initialize":
            requested = params.get("protocolVersion")
            result = {
                "protocolVersion": requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "interlatch", "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            fn = handlers.get(name)
            if not fn:
                result = {"content": [{"type": "text", "text": f"Unknown tool {name}"}], "isError": True}
            else:
                try:
                    # results go into the client's conversation, i.e. to its model provider: same redaction as digests
                    text = redact(fn(**(params.get("arguments") or {})))
                    result = {"content": [{"type": "text", "text": text}], "isError": False}
                except TypeError as exc:
                    result = {"content": [{"type": "text", "text": f"Bad arguments: {exc}"}], "isError": True}
        elif method in ("resources/list", "resources/templates/list"):
            result = {"resources": [] if method == "resources/list" else [], "resourceTemplates": []}
        elif method == "prompts/list":
            result = {"prompts": []}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as exc:  # keep serving
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)[:500]}}
