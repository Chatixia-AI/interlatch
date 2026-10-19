"""What agents see once named profiles exist (core.profiles): a connection scoped to one owner's sessions.

Personal records every session, so whatever Personal shows an agent must be limited to what the agent's folder owns:
an agent in a Personal folder sees Personal's sessions, one in Acme's folder only Acme's, one in a folder that waits
for you nothing. The connection gets TEMP views named like the tables they shadow (SQLite looks in the temp schema
first, as sharing/access.py does for hub viewers), so every query the MCP server or the session-start context makes is
scoped without being rewritten:

- sessions and their rows (events, tool calls, files, subagents, API calls, artifacts): the owner's sessions;
- knowledge: lessons from those sessions, notes about the owner's projects, teammates' lessons the owner's own hub
  sent (and, for Personal, notes about no project);
- project_kb, glossary: the owner's projects' knowledge bases and terms; nothing made across every project (the
  global playbook, the cross-project glossary pass, themes, weekly reviews), which mixes every profile's sessions;
- every other table reads as empty (DENY), and a table nobody classified yet does too.

The views read session_owner as each query runs, so a long-lived connection (the MCP server) stays current. A session
with no owner yet is nobody's. Full-text tables (events_fts, knowledge_fts) can't be shadowed and stay as they are:
every query that reads them joins the shadowed table they index (application/search.py).
"""

from __future__ import annotations

import sqlite3

from ..core.config import Config
from ..core.profiles import PERSONAL, WAITING, route

# by the session they belong to
BY_SESSION = ("events", "tool_calls", "session_files", "subagents", "api_calls", "artifacts")
# read as empty in a scope: another profile's content, made across every profile, or nothing agents need
DENY = ("analyses", "reviews", "glossary_themes", "machines", "people", "hub_projects", "people_codes", "people_tokens",
        "people_audit", "hub_news", "suggestions", "suggestion_scopes", "project_groups", "project_group_picks",
        "group_shares", "files_state", "kv", "session_owner")
# shadowed below with their own filter
FILTERED = ("sessions", "knowledge", "project_kb", "glossary", "glossary_usage", *BY_SESSION)
# full-text indexes: queried only joined with the table they index
FTS = ("events_fts", "knowledge_fts")


def owner_here(cfg: Config, conn: sqlite3.Connection, folder: str | None) -> str | None:
    """Who owns a new session in `folder` (an agent's working directory), or None when there are no named profiles
    (nothing is scoped). RegistryError goes up: the caller shows nothing."""
    reg = cfg.profiles
    if reg is None:
        return None
    from .owners import has_table, known_remotes
    from .routing import git_info

    info = git_info(folder) if folder else None
    known = known_remotes(conn) if has_table(conn) else {}
    return route(reg, folder, info[1] if info else None, known=known).owner


def _tables(conn: sqlite3.Connection) -> list[str]:
    """The archive's own tables (not FTS shadow tables), as the main schema has them."""
    names = [r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'")]
    shadow = tuple(f"{t}_" for t in FTS)
    return [n for n in names if not n.startswith(shadow) and n not in FTS and not n.startswith("sqlite_")]


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def scope(conn: sqlite3.Connection, owner: str) -> None:
    """Shadow this connection's tables with views of `owner`'s sessions only (the module docstring). `owner` WAITING,
    or one that owns nothing, sees an empty archive."""
    for t in _tables(conn) + ["session_owner"]:
        conn.execute(f"DROP VIEW IF EXISTS temp.{t}")
    has_owners = conn.execute("SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = 'session_owner'").fetchone()
    if owner == WAITING or not has_owners:
        owned = "SELECT NULL WHERE 0"
        others = "SELECT id FROM main.sessions"
    else:
        owned = f"SELECT session_id FROM main.session_owner WHERE owner = {_q(owner)}"
        others = f"SELECT id FROM main.sessions WHERE id NOT IN ({owned})"  # another owner's, waiting, or none yet
    # a project is the owner's when its sessions are, and no other owner's session is filed under it
    paths = (f"SELECT project_path FROM main.sessions WHERE id IN ({owned}) AND project_path IS NOT NULL EXCEPT "
             f"SELECT project_path FROM main.sessions WHERE id IN ({others}) AND project_path IS NOT NULL")
    conn.execute(f"CREATE TEMP VIEW sessions AS SELECT * FROM main.sessions WHERE id IN ({owned})")
    for t in BY_SESSION:
        conn.execute(f"CREATE TEMP VIEW {t} AS SELECT * FROM main.{t} WHERE session_id IN ({owned})")
    # teammates' lessons from this owner's hub only (sharing.pull.team_prefix: "team:<id>" Personal's hub's,
    # "team:<profile>:<id>" a named profile's), filed under this owner's projects
    team = "fingerprint GLOB 'team:[0-9]*'" if owner == PERSONAL else f"fingerprint LIKE {_q(f'team:{owner}:%')}"
    personal_extra = "OR (session_id IS NULL AND project_path IS NULL AND source != 'team') " \
        if owner == PERSONAL else ""  # your own notes about no project
    conn.execute("CREATE TEMP VIEW knowledge AS SELECT * FROM main.knowledge WHERE "
                 f"(session_id IN ({owned})) OR (session_id IS NULL AND source != 'team' AND project_path IN ({paths})) "
                 f"OR (source = 'team' AND session_id IS NULL AND {team} AND project_path IN ({paths})) "
                 f"{personal_extra}")
    conn.execute(f"CREATE TEMP VIEW project_kb AS SELECT * FROM main.project_kb WHERE project_path IN ({paths})")
    conn.execute(f"CREATE TEMP VIEW glossary_usage AS SELECT * FROM main.glossary_usage WHERE project_path IN ({paths})")
    # a term written from the owner's projects only, without the sessions it was seen in elsewhere
    gcols = [r[1] for r in conn.execute("PRAGMA main.table_info(glossary)")]
    gsel = ", ".join("NULL AS top_sessions_json" if c == "top_sessions_json" else c for c in gcols)
    conn.execute(f"CREATE TEMP VIEW glossary AS SELECT {gsel} FROM main.glossary WHERE definition_source IN ({paths}) "
                 f"AND id NOT IN (SELECT term_id FROM main.glossary_usage WHERE project_path NOT IN ({paths}))")
    for t in _tables(conn):
        if t not in FILTERED:  # DENY, and anything added since that nobody classified
            conn.execute(f"CREATE TEMP VIEW {t} AS SELECT * FROM main.{t} WHERE 0")
    if not has_owners:
        conn.execute("CREATE TEMP VIEW session_owner AS SELECT NULL AS session_id, NULL AS owner WHERE 0")


def scoped_for(cfg: Config, conn: sqlite3.Connection, folder: str | None) -> str | None:
    """Scope `conn` to whoever owns `folder`, when named profiles exist. Returns that owner (None: not scoped). An
    unreadable profiles.toml scopes it to nothing (WAITING) rather than to everything."""
    from ..core.profiles import RegistryError

    try:
        owner = owner_here(cfg, conn, folder)
    except RegistryError:
        owner = WAITING
    if owner is not None:
        scope(conn, owner)
    return owner
