"""What a person limited to projects (people.projects_of) may see of a hub's dashboard.

Two layers, so one forgotten filter can't show another project:

1. Endpoints: only those in LIMITED_GET answer; everything else is refused before it runs (allowed()).
2. Data: the request's database connection gets TEMP views named like the tables they shadow (SQLite looks in the
   temp schema first), so every query an allowed endpoint makes sees only the person's projects (limit()):
   - sessions: theirs, analyzed, with only the columns a member's computer shares as knowledge (hub.SHARED_COLS,
     the analysis); prompts, paths, commands and prompt-made titles read as NULL
   - knowledge: project lessons from those sessions' analysis (never lessons about a person, never global ones)
   - project_kb: their projects' knowledge bases; api_calls: their sessions' token counts
   - hub_news: what computers sent to their projects (news.py), no computer joining
   - every other table that holds session or project content reads as empty (EMPTY), transcripts above all.

The temp views live only on that request's connection, which the server closes when the request ends.
"""

from __future__ import annotations

import re
import sqlite3

# GET endpoints a limited person may call; /api/sessions/<id> (the summary page) is matched separately
LIMITED_GET = frozenset({"/api/me", "/api/overview", "/api/team", "/api/team/who", "/api/team/news", "/api/sessions",
                         "/api/projects", "/api/project", "/api/knowledge", "/api/learning/interests", "/api/jobs", "/api/diagram"})
SESSION_PAGE = re.compile(r"/api/sessions/[\w-]+")

# tables whose rows are a session's content or another project's: read as empty
EMPTY = ("events", "tool_calls", "subagents", "session_files", "artifacts", "analyses", "glossary", "glossary_usage",
         "glossary_themes", "suggestions", "suggestion_scopes", "reviews", "files_state", "project_groups",
         "project_group_picks", "group_shares")

# sessions columns a limited person sees; the rest read as NULL
_ALWAYS = ("id", "source", "agent", "machine_id", "project_path", "project_name", "analysis_status", "llm_title")


def allowed(path: str) -> bool:
    return path in LIMITED_GET or bool(SESSION_PAGE.fullmatch(path))


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")]


def limit(conn: sqlite3.Connection, projects: list[str]) -> None:
    """Shadow the tables on this connection with views of `projects` only (see the module docstring)."""
    from .hub import ANALYSIS_COLS, SHARED_COLS

    lit = ", ".join(_quote(p) for p in projects) or "NULL"  # views can't take parameters: quoted literals
    visible = set(_ALWAYS) | set(SHARED_COLS) | (set(ANALYSIS_COLS) - {"analysis_json"})
    cols = []
    for c in _columns(conn, "sessions"):
        if c == "title":
            cols.append("llm_title AS title")  # the analysis' title; a title made from the first prompt stays hidden
        elif c == "source_present":
            cols.append("0 AS source_present")  # no transcript to open
        elif c in visible:
            cols.append(c)
        else:
            cols.append(f"NULL AS {c}")
    for t in ("sessions", "knowledge", "project_kb", "api_calls", "hub_news", *EMPTY):  # never a view left from before
        conn.execute(f"DROP VIEW IF EXISTS temp.{t}")
    mine = f"project_path IN ({lit}) AND analysis_status = 'done' AND source NOT IN ('history')"
    conn.execute(f"CREATE TEMP VIEW sessions AS SELECT {', '.join(cols)} FROM main.sessions WHERE {mine}")
    conn.execute("CREATE TEMP VIEW knowledge AS SELECT * FROM main.knowledge WHERE "
                 f"project_path IN ({lit}) AND source = 'analysis' AND scope = 'project' AND kind != 'preference' "
                 f"AND session_id IN (SELECT id FROM main.sessions WHERE {mine})")
    conn.execute(f"CREATE TEMP VIEW project_kb AS SELECT * FROM main.project_kb WHERE project_path IN ({lit})")
    conn.execute("CREATE TEMP VIEW api_calls AS SELECT * FROM main.api_calls WHERE "
                 f"session_id IN (SELECT id FROM main.sessions WHERE {mine})")
    conn.execute(f"CREATE TEMP VIEW hub_news AS SELECT * FROM main.hub_news WHERE kind = 'shared' AND project_path IN ({lit})")
    for t in EMPTY:
        if _columns(conn, t):
            conn.execute(f"CREATE TEMP VIEW {t} AS SELECT * FROM main.{t} WHERE 0")


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def session_page(s: dict) -> dict:
    """A session as a limited person sees it: what a member's computer shares as knowledge, nothing it ran on."""
    keep = {"id", "source", "agent", "title", "project_name", "project_path", "started_at", "ended_at", "duration_s",
            "active_s", "primary_model", "n_prompts", "n_tool_calls", "n_tool_errors", "n_subagents", "n_files",
            "lines_added", "lines_removed", "total_tokens", "est_cost_usd", "outcome", "outcome_note", "sentiment",
            "summary", "goal", "analysis_status", "analyzed_at", "llm_title", "work_types", "tags", "highlights",
            "open_threads", "knowledge", "machine_name"}
    out = {k: v for k, v in s.items() if k in keep}
    out.update(source_present=0, limited=True, prompts=[], files=[], subagents=[], tool_errors=[], markers=[],
               tool_stats=[], api_calls=[], analyses=[], outputs=[], agents=[], prs=[], artifacts=[], commands={},
               branches={}, hooks={}, models={}, tools={}, skills={}, mcp={}, workflows=[], friction=[],
               waiting=None, usage=None)
    return out
