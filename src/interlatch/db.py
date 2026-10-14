"""SQLite store: schema, migrations, and small query helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 18

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'transcript',      -- transcript | history | codex-import
    agent TEXT NOT NULL DEFAULT 'claude',           -- claude | codex
    claude_dir TEXT,
    project_dir TEXT,                               -- folder name under projects/
    project_path TEXT,                              -- working directory
    project_name TEXT,
    transcript_path TEXT,
    archive_path TEXT,
    source_present INTEGER NOT NULL DEFAULT 1,
    files_sig TEXT,
    parser_version INTEGER,
    ingested_at TEXT,
    title TEXT,
    ai_title TEXT,
    first_prompt TEXT,
    last_prompt TEXT,
    started_at TEXT,
    ended_at TEXT,
    duration_s REAL,
    active_s REAL,
    git_branch TEXT,
    cc_version TEXT,
    entrypoint TEXT,
    permission_mode TEXT,
    primary_model TEXT,
    models_json TEXT,
    tools_json TEXT,
    skills_json TEXT,
    mcp_json TEXT,
    commands_json TEXT,
    branches_json TEXT,
    prs_json TEXT,
    artifacts_json TEXT,
    workflows_json TEXT,
    hooks_json TEXT,
    n_prompts INTEGER DEFAULT 0,
    n_api_calls INTEGER DEFAULT 0,
    n_tool_calls INTEGER DEFAULT 0,
    n_tool_errors INTEGER DEFAULT 0,
    n_interrupts INTEGER DEFAULT 0,
    n_compactions INTEGER DEFAULT 0,
    n_api_errors INTEGER DEFAULT 0,
    n_subagents INTEGER DEFAULT 0,
    n_images INTEGER DEFAULT 0,
    n_result_images INTEGER DEFAULT 0,
    n_files INTEGER DEFAULT 0,
    n_events INTEGER DEFAULT 0,
    lines_added INTEGER DEFAULT 0,
    lines_removed INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    sub_tokens INTEGER DEFAULT 0,
    est_cost_usd REAL DEFAULT 0,
    sub_cost_usd REAL DEFAULT 0,
    cc_cost_usd REAL,
    peak_context INTEGER DEFAULT 0,
    ended_flag INTEGER NOT NULL DEFAULT 0,
    -- analysis
    analysis_status TEXT NOT NULL DEFAULT 'pending', -- pending|running|done|skipped|error|stale
    analysis_reason TEXT,
    analysis_attempts INTEGER NOT NULL DEFAULT 0,
    analysis_not_before TEXT,
    analyzed_at TEXT,
    analysis_model TEXT,
    analyzed_prompts INTEGER,
    llm_title TEXT,
    summary TEXT,
    goal TEXT,
    outcome TEXT,
    outcome_note TEXT,
    sentiment TEXT,
    work_types_json TEXT,
    tags_json TEXT,
    highlights_json TEXT,
    open_threads_json TEXT,
    friction_json TEXT,
    analysis_json TEXT,
    statusline_json TEXT,                          -- context and plan usage from Claude Code's status line (statusline.py)
    machine_id TEXT,                                -- the computer it ran on (machines.id)
    machine_path TEXT,                              -- project_path as that computer recorded it, when it differs
    -- screening of imported chats: is a full analysis worth it (screen.py)
    screen_verdict TEXT,                            -- analyze | maybe | skip
    screen_topic TEXT,
    screen_reason TEXT,
    screen_by TEXT,                                 -- 'rules', or the model that screened it
    screen_sig TEXT,                                -- files_sig when screened: a chat a newer export changed is screened again
    screened_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_path);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(analysis_status);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent);
CREATE INDEX IF NOT EXISTS idx_sessions_machine ON sessions(machine_id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL,
    ts TEXT,
    role TEXT,
    kind TEXT NOT NULL,
    tool_name TEXT,
    tool_use_id TEXT,
    is_error INTEGER NOT NULL DEFAULT 0,
    searchable INTEGER NOT NULL DEFAULT 0,
    text TEXT,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, agent_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind, ts);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    text, content='events', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events WHEN new.searchable = 1 BEGIN
    INSERT INTO events_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events WHEN old.searchable = 1 BEGIN
    INSERT INTO events_fts(events_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    tool_use_id TEXT,
    ts TEXT,
    name TEXT,
    mcp_server TEXT,
    summary TEXT,
    file_path TEXT,
    command TEXT,
    is_error INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    result_chars INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_name ON tool_calls(name);

CREATE TABLE IF NOT EXISTS session_files (
    session_id TEXT NOT NULL,
    path TEXT NOT NULL,
    reads INTEGER NOT NULL DEFAULT 0,
    edits INTEGER NOT NULL DEFAULT 0,
    writes INTEGER NOT NULL DEFAULT 0,
    lines_added INTEGER NOT NULL DEFAULT 0,
    lines_removed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, path)
);
CREATE INDEX IF NOT EXISTS idx_session_files_path ON session_files(path);

CREATE TABLE IF NOT EXISTS subagents (
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    agent_type TEXT,
    description TEXT,
    workflow_id TEXT,
    phase TEXT,
    tool_use_id TEXT,
    model TEXT,
    started_at TEXT,
    ended_at TEXT,
    n_tool_calls INTEGER DEFAULT 0,
    n_tool_errors INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    est_cost_usd REAL DEFAULT 0,
    PRIMARY KEY (session_id, agent_id)
);

CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    msg_id TEXT,
    ts TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    cost_usd REAL,
    speed TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_calls_session ON api_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_api_calls_ts ON api_calls(ts);

CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    project_path TEXT,
    project_name TEXT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    tags_json TEXT,
    scope TEXT NOT NULL DEFAULT 'project',
    confidence TEXT,
    evidence TEXT,
    source TEXT NOT NULL DEFAULT 'analysis',       -- analysis | memory | manual
    agent TEXT DEFAULT 'claude',                   -- which agent's session/memory produced it
    source_ref TEXT,
    fingerprint TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',         -- active | superseded | dismissed
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    stage TEXT NOT NULL DEFAULT 'provisional',      -- wip | provisional | established | canonical (see ladder.py)
    stage_reason TEXT,                             -- the evidence that earned the stage
    confirmed_json TEXT,                           -- session ids that state the same lesson
    superseded_by INTEGER,                         -- the item that replaced this one, when there is one
    superseded_reason TEXT,                        -- duplicate | outdated | contradicted
    superseded_at TEXT,
    case_json TEXT                                 -- lesson material (analyze.case_of): a fix, gotcha or decision's case file, and principle, checks, topics, diagram
);
CREATE INDEX IF NOT EXISTS idx_knowledge_session ON knowledge(session_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_project ON knowledge(project_path);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    title, body, tags_json, content='knowledge', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge BEGIN
    INSERT INTO knowledge_fts(rowid, title, body, tags_json) VALUES (new.id, new.title, new.body, new.tags_json);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body, tags_json)
    VALUES ('delete', old.id, old.title, old.body, old.tags_json);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE OF title, body, tags_json ON knowledge BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body, tags_json)
    VALUES ('delete', old.id, old.title, old.body, old.tags_json);
    INSERT INTO knowledge_fts(rowid, title, body, tags_json) VALUES (new.id, new.title, new.body, new.tags_json);
END;

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                            -- session | chunk | synthesis
    target TEXT,
    started_at TEXT,
    finished_at TEXT,
    model TEXT,
    status TEXT,
    error TEXT,
    input_chars INTEGER,
    chunks INTEGER,
    cost_usd REAL,
    duration_ms INTEGER,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_analyses_target ON analyses(target);

CREATE TABLE IF NOT EXISTS project_kb (
    project_path TEXT PRIMARY KEY,                 -- '__global__' for the cross-project playbook
    project_name TEXT,
    updated_at TEXT,
    model TEXT,
    knowledge_max_id INTEGER,
    n_items INTEGER,
    overview TEXT,
    markdown TEXT,
    kb_json TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    period TEXT PRIMARY KEY,                       -- ISO week, e.g. 2026-W39
    start TEXT,
    end TEXT,
    created_at TEXT,
    model TEXT,
    n_sessions INTEGER,
    markdown TEXT,
    review_json TEXT,
    stats_json TEXT
);

CREATE TABLE IF NOT EXISTS glossary (
    id INTEGER PRIMARY KEY,
    term TEXT NOT NULL,
    norm TEXT NOT NULL UNIQUE,
    aliases_json TEXT,
    category TEXT,
    definition TEXT,
    definition_source TEXT,                        -- project path that wrote it, or '__global__'
    related_json TEXT,
    n_sessions INTEGER DEFAULT 0,
    n_mentions INTEGER DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    top_sessions_json TEXT,
    updated_at TEXT,
    theme TEXT                                     -- sub-group within the category (build_themes), NULL if none
);

CREATE TABLE IF NOT EXISTS glossary_themes (
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    n_terms INTEGER DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (category, name)
);

CREATE TABLE IF NOT EXISTS glossary_usage (
    term_id INTEGER NOT NULL,
    project_path TEXT NOT NULL,                    -- '__global__' for the cross-project pass
    context TEXT,
    sources_json TEXT,
    updated_at TEXT,
    PRIMARY KEY (term_id, project_path)
);

CREATE TABLE IF NOT EXISTS machines (
    id TEXT PRIMARY KEY,                           -- a UUID each computer makes once (machine.json)
    name TEXT,
    platform TEXT,
    version TEXT,                                  -- its Interlatch version
    role TEXT,                                     -- this | spoke
    first_seen TEXT,
    last_seen TEXT,                                -- last time it said hello
    last_push TEXT,                                -- last time it sent a file
    files INTEGER DEFAULT 0,                       -- files received from it
    bytes INTEGER DEFAULT 0,
    repos_json TEXT,                               -- {cwd: [git top level, normalized remote]} it reported
    person_id INTEGER,                             -- whose computer it is (people.py), once it joined with an invite
    key_hash TEXT                                  -- sha256 of the key in its machine-key file, recorded the first time
                                                   -- it said hello: an invite that claims this id must show it
);

CREATE TABLE IF NOT EXISTS people (                -- people on a hub (people.py): who may send and see, with which role
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE,                             -- lower case; how a company sign-in (auth header) names them
    role TEXT NOT NULL,                            -- admin | member | readonly
    created_at TEXT NOT NULL,
    created_by INTEGER,
    removed_at TEXT,                               -- removed: tokens, sessions and invites stop working
    projects_json TEXT                             -- NULL: every project; else a JSON list of the project paths they see
);

CREATE TABLE IF NOT EXISTS hub_projects (          -- projects set up on a hub ahead of time (`interlatch hub project add`)
    path TEXT PRIMARY KEY,                         -- a folder on the hub computer: its sessions, and everything below it
    created_at TEXT NOT NULL,
    created_by TEXT                                -- "person:<id>" or "this computer"
);

CREATE TABLE IF NOT EXISTS people_codes (          -- one-time codes: invites, and short dashboard sign-in links
    code_hash TEXT PRIMARY KEY,                    -- sha256 of the code; the code itself is shown once
    person_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                            -- invite | signin
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    machine_id TEXT                                -- an invite an admin made for one computer the hub knows
);

CREATE TABLE IF NOT EXISTS people_tokens (         -- a computer's push token, or a browser's dashboard session
    token_hash TEXT PRIMARY KEY,                   -- sha256; the token itself lives only on the computer or in the cookie
    person_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                            -- computer | browser
    machine_id TEXT,                               -- the computer, for kind computer
    label TEXT,                                    -- e.g. the browser's user agent, shown to admins
    created_at TEXT NOT NULL,
    expires_at TEXT,                               -- browsers only
    last_used TEXT,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_people_tokens_person ON people_tokens(person_id);

CREATE TABLE IF NOT EXISTS people_audit (          -- who did what to people, roles and settings on this hub
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT,                                    -- "person:<id>", "this computer", or "legacy token"
    action TEXT NOT NULL,
    person_id INTEGER,
    detail TEXT                                    -- JSON
);

CREATE TABLE IF NOT EXISTS hub_news (              -- on a hub: what other computers sent lately, for the Team page (news.py)
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,                              -- last added to: a computer that keeps sending adds to its row
    since TEXT NOT NULL,
    kind TEXT NOT NULL,                            -- shared | joined
    machine_id TEXT NOT NULL,
    project_path TEXT,                             -- the project on this hub; NULL for joined
    sessions INTEGER NOT NULL DEFAULT 0,           -- sessions this hub had not had before
    lessons INTEGER NOT NULL DEFAULT 0             -- project lessons new to it
);
CREATE INDEX IF NOT EXISTS idx_hub_news_at ON hub_news(at);

CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,                      -- stable identity, e.g. friction:<cause>:<agent>:user, knowledge:<id>:<file>
    kind TEXT NOT NULL,                            -- instruction | config | environment
    origin TEXT NOT NULL,                          -- friction | knowledge
    cause_id TEXT,                                 -- friction.CATALOG id
    knowledge_id INTEGER,
    project_path TEXT,                             -- NULL = user level
    agent TEXT,                                    -- claude | codex | copilot | bob | antigravity | all
    target_path TEXT,                              -- the file the change goes to; NULL for environment steps
    title TEXT NOT NULL,
    text TEXT NOT NULL,                            -- the proposed line / config change / command (editable before apply)
    evidence_json TEXT,
    warnings_json TEXT,
    score REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',            -- new | applied | dismissed | stale | done (an environment step you did)
    created_at TEXT,
    updated_at TEXT,
    seen_at TEXT,
    applied_at TEXT,
    applied_text TEXT,
    dismissed_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_suggestions_status ON suggestions(status);

CREATE TABLE IF NOT EXISTS suggestion_scopes (
    subject TEXT PRIMARY KEY,                      -- knowledge:<id> (every item of the lesson) | cause:<friction.CATALOG id>
    scope TEXT NOT NULL,                           -- user | project: where you moved it, over the automatic choice
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (          -- what a session made (artifacts.py): files, pages, PRs, commits
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    tool_use_id TEXT,
    seq INTEGER,                                   -- the tool call's event, for a link into the transcript
    ts TEXT,
    key TEXT NOT NULL,                             -- the same file, link or commit across sessions
    kind TEXT NOT NULL,                            -- doc page diagram deck sheet image published pr commit
    action TEXT,                                   -- created rewritten updated published opened committed generated presented
    title TEXT,
    path TEXT,
    url TEXT,
    size INTEGER,
    sha256 TEXT,                                   -- of the content as the agent wrote it, when the transcript has it
    versions INTEGER NOT NULL DEFAULT 1,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_key ON artifacts(key, ts);
CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind, ts);

CREATE TABLE IF NOT EXISTS project_groups (      -- your own groups of projects (groups.py): how the dashboard lists them
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    folders_json TEXT NOT NULL DEFAULT '[]',      -- folder rules: a project under one joins, the longest rule winning
    created_at TEXT,
    hub_project TEXT                              -- shared as this project on the hub this computer sends to (hub.py)
);
CREATE TABLE IF NOT EXISTS project_group_picks (  -- a project put in a group by hand, over its folder rules
    project_path TEXT PRIMARY KEY,
    group_id INTEGER                              -- NULL: kept out of every group
);
CREATE TABLE IF NOT EXISTS group_shares (         -- every folder a group shared with the hub: still filed there when it
    path TEXT PRIMARY KEY,                        -- left the group, so what it sent stays in place (hub.group_routes)
    hub_project TEXT NOT NULL,
    group_id INTEGER,
    shared_at TEXT
);

CREATE TABLE IF NOT EXISTS files_state (
    path TEXT PRIMARY KEY,
    size INTEGER,
    mtime REAL,
    archive_path TEXT,
    archived_at TEXT
);
"""


def connect(db_path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if readonly and db_path.exists():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    else:
        conn = sqlite3.connect(db_path, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create or upgrade the schema. Every statement is idempotent, so additive migrations just re-run it."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    statements = list(_statements(SCHEMA))
    tables = [st for st in statements if st.upper().startswith("CREATE TABLE")]
    # tables first, then columns an older database lacks, then indexes/triggers that may reference them
    for st in tables:
        conn.execute(st)
    _add_missing_columns(conn)
    for st in statements:
        if st not in tables:
            conn.execute(st)
    if current < 8:  # knowledge gained a maturity stage: derive it for existing items from what they already carry
        from .ladder import refresh_all

        refresh_all(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _statements(script: str):
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf.strip()
            buf = ""


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS never alters an existing table: add any column SCHEMA declares that the DB lacks."""
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(SCHEMA)
        tables = [r[0] for r in ref.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE '%fts%' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for _cid, name, ctype, notnull, default, _pk in ref.execute(f"PRAGMA table_info({table})"):
                if name in have:
                    continue
                decl = f"{name} {ctype}".strip()
                if default is not None:
                    decl += f" DEFAULT {default}" + (" NOT NULL" if notnull else "")
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {decl}")
    finally:
        ref.close()


def kv_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def kv_set(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute(
        "INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query (AND of quoted phrases, trigram needs >= 3 chars)."""
    import re

    terms = [t for t in re.findall(r'"[^"]+"|\S+', text.strip())]
    out = []
    for t in terms:
        t = t.strip('"').replace('"', '""')
        if len(t) >= 3:
            out.append(f'"{t}"')
    return " AND ".join(out) if out else None


def short_terms(text: str) -> list[str]:
    """Search terms too short for the trigram index (handled with LIKE)."""
    import re

    return [t.strip('"') for t in re.findall(r'"[^"]+"|\S+', text.strip()) if 0 < len(t.strip('"')) < 3]
