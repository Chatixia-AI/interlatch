"""Search through the glossary: a word that names a term also finds the term's other names, and an empty MCP reply
says which words the glossary knows and how much was searched."""

from interlatch.analysis.session import analyze_session
from interlatch.application.search import added_names, query_groups, search_all, search_knowledge, search_sessions
from interlatch.interfaces.mcp import Tools
from interlatch.knowledge.glossary import build_glossary

from conftest import CWD, SID


def _archive(synced):
    """The test session analyzed, its glossary built, and Token TTL given a name the user might type
    ("token lifetime", which no transcript holds) and one the model made up."""
    conn, cfg = synced["conn"], synced["cfg"]
    analyze_session(conn, cfg, SID)
    build_glossary(conn, cfg, CWD)
    conn.execute("UPDATE glossary SET aliases_json = '[\"TTL\", \"token lifetime\", \"expiry window\"]', "
                 "alias_seen_json = '{\"TTL\": 1, \"token lifetime\": 0, \"expiry window\": 0}' WHERE norm = 'token ttl'")
    conn.commit()
    return conn, cfg


def test_a_run_of_words_that_names_a_term_is_one_group_with_its_used_names(synced):
    conn, _ = _archive(synced)
    groups = query_groups(conn, "Token lifetime units", glossary=True)
    assert [(g["text"], g["names"]) for g in groups] == [
        ("Token lifetime", ["Token lifetime", "Token TTL", "TTL"]),  # the longest run, then the term and its used aliases
        ("units", ["units"])]
    assert groups[0]["term"]["term"] == "Token TTL" and groups[1]["term"] is None
    assert added_names(groups) == ["Token TTL", "TTL"]
    # an alias only the model wrote finds nothing in the transcripts: never added
    assert query_groups(conn, "Token TTL", glossary=True)[0]["names"] == ["Token TTL", "TTL"]
    # a word too short for the trigram index stays on its own (LIKE); without `glossary` nothing is looked up
    assert [g["names"] for g in query_groups(conn, "ms ttl", glossary=True)] == [["ms"], ["ttl", "Token TTL"]]
    assert [g["names"] for g in query_groups(conn, "token lifetime")] == [["token"], ["lifetime"]]


def test_search_finds_lessons_and_sessions_by_another_name(synced):
    conn, _ = _archive(synced)
    assert search_knowledge(conn, "token lifetime") == []
    found = search_knowledge(conn, "token lifetime", glossary=True)
    assert [k["title"] for k in found] == ["Token TTL compared in seconds vs ms"]
    assert search_knowledge(conn, "token lifetime seconds", glossary=True)  # every group must match...
    assert search_knowledge(conn, "token lifetime kubernetes", glossary=True) == []  # ...so this one finds nothing
    assert search_sessions(conn, "token lifetime") == []
    sessions = search_sessions(conn, "token lifetime", glossary=True)
    assert [s["session_id"] for s in sessions] == [SID] and any("«TTL»" in sn["text"] for sn in sessions[0]["snippets"])
    assert search_all(conn, "token lifetime")["total"] == 0  # the dashboard's search is unchanged


def test_mcp_replies_say_what_was_added_and_why_nothing_was_found(synced):
    conn, cfg = _archive(synced)
    tools = Tools(cfg)
    reply = tools.search_knowledge("token lifetime")
    assert reply.startswith("1 knowledge item(s) for 'token lifetime' (also searched: Token TTL, TTL, from the glossary):")
    assert "(also searched: Token TTL, TTL, from the glossary):" in tools.search_sessions("token lifetime")

    empty = tools.search_knowledge("token lifetime kubernetes", project="demo-app").splitlines()
    assert empty[0] == "No knowledge found for 'token lifetime kubernetes' (also searched: Token TTL, TTL, from the glossary)."
    assert empty[1] == "- 'token lifetime' is the glossary term **Token TTL**: Lifetime of an auth token"
    assert empty[2].startswith("- Not in the glossary: 'kubernetes'. The archive may use another name for it")
    lessons = conn.execute("SELECT COUNT(*) FROM knowledge WHERE status = 'active' AND project_path = ?", (CWD,)).fetchone()[0]
    assert empty[3] == (f"Searched {lessons} lessons in demo-app, from 1 of 1 sessions analyzed. "
                        "search_sessions searches the transcripts themselves.")
    assert tools.search_knowledge("zzqx", kind="fix").splitlines()[0] == "No fix knowledge found for 'zzqx'."
    n = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert tools.search_sessions("zzqx").splitlines()[-1] == f"Searched the transcripts of {n} sessions across all projects."
    assert tools.search_sessions("zzqx", project="/nowhere").splitlines()[-1] == \
        "Interlatch has no sessions recorded in /nowhere."
    bob = tools.search_sessions("zzqx", agent="bob").splitlines()
    assert bob[0] == "No Bob sessions matched 'zzqx'." and bob[-1] == "Searched the transcripts of 0 Bob sessions across all projects."
