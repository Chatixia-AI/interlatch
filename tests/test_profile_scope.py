"""What agents see once named profiles exist (projects.scope): in a Personal folder only Personal's sessions, in a
profile's folder only that profile's, in a waiting folder nothing, through every MCP tool and the session-start
context. Canaries planted in each table must not cross."""

import ast
import os
from pathlib import Path

import pytest

from interlatch.application import profiles as ops
from interlatch.core.profiles import WAITING
from interlatch.projects import scope as scope_mod
from interlatch.storage.sqlite import connect

from conftest import CWD, SID

MINE = "/Users/test/Projects/mine"
MINE_SID = "mine-0000-1111-2222-333333333333"
# content only: the tools echo what they were asked for ("'ACME_TERM' is not in the glossary")
ACME = ("ACME_CANARY", "ACME_TERM definition", "ACME_TERM use")
PERSONAL = ("PERSONAL_CANARY", "PERSONAL_TERM definition")
MIXED = "MIXED_TERM definition"


def _plant(conn):
    """The synced demo session (in CWD) becomes Acme's below; a second session, in MINE, stays Personal's. Each gets a
    canary in every table an agent can read."""
    row = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (SID,)).fetchone())
    conn.execute("UPDATE sessions SET title = 'ACME_CANARY billing', llm_title = 'ACME_CANARY billing', "
                 "summary = 'ACME_CANARY summary', project_name = 'acme-billing', analysis_status = 'done' WHERE id = ?", (SID,))
    row.update(id=MINE_SID, project_path=MINE, project_name="mine", title="PERSONAL_CANARY notes",
               llm_title="PERSONAL_CANARY notes", summary="PERSONAL_CANARY summary", analysis_status="done",
               machine_path=None)
    cols = list(row)
    conn.execute(f"INSERT INTO sessions({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])
    for sid, word in ((SID, "ACME_CANARY"), (MINE_SID, "PERSONAL_CANARY")):
        conn.execute("INSERT INTO events(session_id, seq, ts, role, kind, searchable, text) VALUES (?, 9999, "
                     "'2026-09-20T10:05:00Z', 'user', 'prompt', 1, ?)", (sid, f"{word} prompt about invoices"))
        conn.execute("INSERT INTO artifacts(session_id, key, kind, title, path, ts) VALUES (?, ?, 'doc', ?, ?, "
                     "'2026-09-20T10:05:00Z')", (sid, f"k-{word}", f"{word} design doc", f"/tmp/{word}.md"))
    for sid, path, word in ((SID, CWD, "ACME_CANARY"), (MINE_SID, MINE, "PERSONAL_CANARY")):
        conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, confidence, "
                     "source, fingerprint, created_at, updated_at, status) VALUES (?, ?, 'p', 'gotcha', ?, 'invoices body', "
                     "'project', 'high', 'analysis', ?, '2026-09-20', '2026-09-20', 'active')",
                     (sid, path, f"{word} lesson invoices", f"fp-{word}"))
        conn.execute("INSERT INTO project_kb(project_path, project_name, updated_at, model, knowledge_max_id, n_items, "
                     "overview, markdown, kb_json) VALUES (?, 'p', '2026-09-20', 'm', 1, 1, ?, ?, '{}')",
                     (path, f"{word} overview", f"# {word} knowledge base"))
    conn.execute("INSERT INTO project_kb(project_path, project_name, updated_at, model, knowledge_max_id, n_items, "
                 "overview, markdown, kb_json) VALUES ('__global__', 'global', '2026-09-20', 'm', 1, 1, 'o', "
                 "'# ACME_CANARY in the global playbook', '{}')")
    for term, source in (("ACME_TERM", CWD), ("PERSONAL_TERM", MINE), ("MIXED_TERM", MINE)):
        conn.execute("INSERT INTO glossary(term, norm, category, definition, definition_source, top_sessions_json) "
                     "VALUES (?, ?, 'tool', ?, ?, ?)", (term, term.lower(), f"{term} definition", source,
                                                         '[{"id": "%s", "title": "ACME_CANARY"}]' % SID))
        tid = conn.execute("SELECT id FROM glossary WHERE term = ?", (term,)).fetchone()[0]
        conn.execute("INSERT INTO glossary_usage(term_id, project_path, context) VALUES (?, ?, ?)", (tid, source, f"{term} use"))
        if term == "MIXED_TERM":  # also seen in Acme's project: its definition may carry Acme's details
            conn.execute("INSERT INTO glossary_usage(term_id, project_path, context) VALUES (?, ?, 'ACME_CANARY use')",
                         (tid, CWD))
    conn.commit()


@pytest.fixture()
def profiled(synced):
    cfg = synced["cfg"]
    _plant(synced["conn"])
    p, _ = ops.create(cfg, "Acme", owns=[CWD])
    synced.update(acme=p)
    return synced


def _tools(cfg, monkeypatch, cwd):
    from interlatch.interfaces.mcp import Tools

    monkeypatch.setattr(os, "getcwd", lambda: cwd)
    return Tools(cfg)


def _everything(tools) -> str:
    """Every MCP tool's answer, with the queries an agent would make, as one text."""
    out = [
        tools.search_sessions("invoices"), tools.search_sessions("CANARY"), tools.search_knowledge("invoices"),
        tools.search_knowledge("lesson"), tools.recent_sessions(days=36500, limit=50), tools.project_knowledge(),
        tools.project_knowledge("global"), tools.project_knowledge("acme-billing"), tools.project_knowledge(CWD),
        tools.glossary(), tools.glossary("ACME_TERM"), tools.glossary("MIXED_TERM"), tools.glossary("PERSONAL_TERM"),
        tools.find_artifacts(), tools.find_artifacts("design"), tools.get_session(SID), tools.get_session(MINE_SID),
        tools.get_transcript(SID), tools.get_transcript(MINE_SID),
    ]
    return "\n".join(out)


def test_an_agent_in_a_personal_folder_sees_only_personal(profiled, monkeypatch):
    tools = _tools(profiled["cfg"], monkeypatch, MINE)
    assert tools.owner == "personal"
    text = _everything(tools)
    assert "PERSONAL_CANARY" in text and "PERSONAL_TERM definition" in text
    for word in (*ACME, MIXED):
        assert word not in text, word
    assert "No unique session matches" in tools.get_session(SID)


def test_an_agent_in_a_profiles_folder_sees_only_that_profile(profiled, monkeypatch):
    tools = _tools(profiled["cfg"], monkeypatch, CWD + "/src")
    assert tools.owner == profiled["acme"].id
    text = _everything(tools)
    assert "ACME_CANARY lesson" in text and "ACME_TERM definition" in text and "# ACME_CANARY knowledge base" in text
    assert "in the global playbook" not in text  # made across every profile
    for word in (*PERSONAL, MIXED):
        assert word not in text, word


def test_an_agent_in_a_waiting_folder_sees_nothing(profiled, monkeypatch):
    from interlatch.interfaces.mcp import WAITING_TEXT, _handle

    ops.disown(profiled["cfg"], CWD)
    tools = _tools(profiled["cfg"], monkeypatch, CWD)
    assert tools.owner == WAITING
    text = _everything(tools)
    for word in ACME + PERSONAL:
        assert word not in text, word
    reply = _handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "recent_sessions", "arguments": {}}}, {"recent_sessions": tools.recent_sessions})
    assert reply["result"]["content"][0]["text"] == WAITING_TEXT


def test_session_start_context_is_scoped(profiled):
    from interlatch.runtime.hooks import build_session_context

    cfg = profiled["cfg"]
    mine = build_session_context(cfg, MINE) or ""
    assert "PERSONAL_CANARY" in mine and "ACME_CANARY" not in mine
    acme = build_session_context(cfg, CWD) or ""
    assert "ACME_CANARY" in acme and "PERSONAL_CANARY" not in acme
    ops.disown(cfg, CWD)
    assert build_session_context(cfg, CWD) is None


def test_a_new_session_is_seen_by_a_running_server_only_where_it_belongs(profiled, monkeypatch):
    """The MCP server keeps its connection for hours: the views read owners as each query runs."""
    tools = _tools(profiled["cfg"], monkeypatch, MINE)
    conn = connect(profiled["cfg"].db_path)
    row = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (SID,)).fetchone())
    row.update(id="late-acme", title="LATE_ACME", llm_title="LATE_ACME")
    cols = list(row)
    conn.execute(f"INSERT INTO sessions({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])
    from interlatch.projects.owners import assign

    assert assign(conn, profiled["cfg"], "late-acme", CWD) == profiled["acme"].id
    conn.commit()
    conn.close()
    assert "LATE_ACME" not in tools.recent_sessions(days=36500, limit=50)


def test_without_profiles_nothing_is_scoped(synced, monkeypatch):
    _plant(synced["conn"])
    tools = _tools(synced["cfg"], monkeypatch, MINE)
    assert tools.owner is None
    text = tools.recent_sessions(days=36500, limit=50)
    assert "ACME_CANARY" in text and "PERSONAL_CANARY" in text
    assert not tools.conn.execute("SELECT name FROM sqlite_temp_master WHERE type = 'view'").fetchall()


def test_an_unreadable_registry_shows_agents_nothing(profiled, monkeypatch):
    (profiled["cfg"].home / "profiles.toml").write_text("[profiles.broken\n")
    tools = _tools(profiled["cfg"], monkeypatch, MINE)
    assert tools.owner == WAITING
    assert "PERSONAL_CANARY" not in _everything(tools)


def test_every_table_is_classified(synced):
    """A new table must be put in FILTERED or DENY on purpose (unclassified ones read as empty anyway)."""
    from interlatch.projects.owners import ensure_table

    ensure_table(synced["conn"])
    tables = set(scope_mod._tables(synced["conn"]))
    assert tables - set(scope_mod.FILTERED) - set(scope_mod.DENY) == set()
    assert set(scope_mod.FILTERED) & set(scope_mod.DENY) == set()


AGENT_FACING = ("interfaces/mcp.py", "runtime/hooks.py")


def test_agent_facing_code_opens_the_archive_only_scoped():
    """In the modules that serve agents, every function that opens the archive scopes it (scoped_for)."""
    src = Path(__file__).resolve().parent.parent / "src" / "interlatch"
    wrong = []
    for rel in AGENT_FACING:
        tree = ast.parse((src / rel).read_text(encoding="utf-8"))
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            calls = [c.func for c in ast.walk(fn) if isinstance(c, ast.Call)]
            names = [getattr(f, "id", None) or getattr(f, "attr", None) for f in calls]
            if "connect" in names and "scoped_for" not in names:
                wrong.append(f"{rel}:{fn.lineno} {fn.name}")
    assert wrong == []


def test_tables_agents_dont_need_read_as_empty(profiled, monkeypatch):
    """What no agent needs (another profile's content, what is made across every profile, the hub's people) reads as
    empty on a scoped connection, whatever a future tool asks: also tables nobody classified."""
    conn = profiled["conn"]
    conn.execute("INSERT INTO reviews(period, markdown) VALUES ('2026-W39', 'ACME_CANARY week')")
    conn.execute("INSERT OR REPLACE INTO kv(key, value) VALUES ('git:/Users/test/Projects/demo-app', 'ACME_CANARY')")
    conn.execute("CREATE TABLE brand_new(x TEXT)")
    conn.execute("INSERT INTO brand_new VALUES ('ACME_CANARY')")
    conn.commit()
    tools = _tools(profiled["cfg"], monkeypatch, MINE)
    for t in (*scope_mod.DENY, "brand_new"):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone():
            assert tools.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
    assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1  # the archive itself is untouched
