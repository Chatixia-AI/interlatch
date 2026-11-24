"""Lesson links: each lesson linked, with no model, to the glossary terms and Systems map parts it names."""

import json
import os
import subprocess
import sys

from interlatch.analysis.session import analyze_session
from interlatch.knowledge.glossary import build_glossary
from interlatch.knowledge.links import Names, find, lessons_about, link_lessons
from interlatch.knowledge.synthesize import GLOBAL

from conftest import CWD, SID

NAMES = {"token ttl": ("Token TTL", "Token TTL", "term"), "ttl": ("TTL", "Token TTL", "term"),
         "ariba": ("Ariba", "SAP Ariba", "term"), "ariba api": ("Ariba API", "Ariba API", "term"),
         "status": ("/status", "/status", "term"), "llm py": ("llm.py", "llm.py", "term"),
         "databricks": ("Databricks", "Databricks", "term"), "発注管理": ("発注管理", "発注管理Agent", "term")}


def _names(found):
    return {(f["name"], f["place"]) for f in found}


def test_find_names_on_word_boundaries_title_first():
    found = find("Token TTL compared in seconds", "The throttle keeps the TTL of each token.", NAMES)
    assert _names(found) == {("Token TTL", "title"), ("TTL", "body")}  # "ttl" not inside "throttle"
    assert found[0]["target"] == "Token TTL" and found[0]["basis"] == "term"
    assert find("発注管理の遅延", None, NAMES)[0]["target"] == "発注管理Agent"  # no word spaces in Japanese


def test_a_shorter_name_inside_a_longer_one_counts_only_on_its_own():
    assert _names(find("Ariba API rate limits", None, NAMES)) == {("Ariba API", "title")}
    assert _names(find("Ariba API limits, and Ariba realms", None, NAMES)) == {("Ariba API", "title"), ("Ariba", "title")}


def test_a_name_with_punctuation_is_found_only_as_written():
    assert find("Check the status page", None, NAMES) == []  # "/status" is not the word "status"
    assert _names(find("backend/llm.py calls the model", None, NAMES)) == {("llm.py", "title")}
    assert find("the llm py wrapper", None, NAMES) == []


def test_the_projects_own_name_is_taken_out_first():
    assert find("databricks-sandbox repo hooks", None, NAMES, "/work/databricks-sandbox") == []
    assert _names(find("databricks-sandbox runs Databricks jobs", None, NAMES, "/work/databricks-sandbox")) == \
        {("Databricks", "title")}


def _build(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    analyze_session(conn, cfg, SID)
    build_glossary(conn, cfg, CWD)
    return conn, cfg


def test_names_of_a_project(synced):
    conn, _ = _build(synced)
    for term, alias, source in (("Elsewhere", "elsewhere-alias", "/other/project"), ("Everywhere", "loose", GLOBAL)):
        cur = conn.execute("INSERT INTO glossary(term, norm, aliases_json, category, definition) VALUES (?, ?, ?, 'tool', 'd')",
                           (term, term.lower(), json.dumps([alias])))
        conn.execute("INSERT INTO glossary_usage(term_id, project_path) VALUES (?, ?)", (cur.lastrowid, source))
    parts = {CWD: [{"label": "app-coc-quote", "kind": "deployed", "name": "app-coc-quote"},
                   {"label": "Key Vault", "kind": "deployed", "name": "dashboard", "resource": "azurerm_key_vault.dashboard"},
                   {"label": "backend", "kind": "api"},
                   {"label": "demo-app", "kind": "deployed"}]}
    names = Names(conn, parts).of(CWD)
    assert names["token ttl"] == ("Token TTL", "Token TTL", "term") and names["ttl"] == ("TTL", "Token TTL", "term")
    assert "elsewhere" not in names  # another project's term
    assert names["everywhere"][2] == "term" and "loose" not in names  # a cross-project term: its own name, no aliases
    assert names["app coc quote"] == ("app-coc-quote", "app-coc-quote", "part") and names["key vault"][1] == "Key Vault"
    assert "dashboard" not in names  # a Terraform resource named only by its HCL label
    assert "backend" not in names  # a part named after the project's own folder
    assert "demo app" not in names  # the project's own name
    conn.execute("UPDATE glossary SET alias_seen_json = '{\"TTL\": 0}' WHERE norm = 'token ttl'")
    assert "ttl" not in Names(conn, parts).of(CWD)  # an alias no session used


def _links(conn):
    return {(r["knowledge_id"], r["name"], r["place"]) for r in conn.execute("SELECT * FROM knowledge_links")}


def test_link_lessons_links_new_ones_and_keeps_up_with_reanalysis(synced, monkeypatch):
    from interlatch.knowledge import links

    monkeypatch.setattr(links, "system_parts", lambda conn, cfg: {})
    conn, cfg = _build(synced)
    assert link_lessons(conn, cfg) > 0
    lesson = conn.execute("SELECT id FROM knowledge WHERE title = 'Token TTL compared in seconds vs ms'").fetchone()[0]
    assert (lesson, "Token TTL", "title") in _links(conn)
    assert lessons_about(conn, ["token-ttl"]) == [lesson] and lessons_about(conn, ["TTL"]) == [lesson]  # by alias too
    assert link_lessons(conn, cfg) == 0  # nothing new

    cur = conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, source, "
                       "fingerprint, status) VALUES (?, ?, 'demo-app', 'fact', 'pytest runs with -x', 'b', 'project', "
                       "'analysis', 'new-one', 'active')", (SID, CWD))
    conn.commit()
    assert link_lessons(conn, cfg) == 1 and (cur.lastrowid, "pytest", "title") in _links(conn)

    analyze_session(conn, cfg, SID)  # analyzed again: its lessons are written again, here under the same ids
    link_lessons(conn, cfg)
    ids = {r[0] for r in conn.execute("SELECT id FROM knowledge WHERE status = 'active'")}
    assert {k for k, _, _ in _links(conn)} <= ids  # no link left to a lesson that is gone
    assert lessons_about(conn, ["Token TTL"])

    # a lesson whose text changed under the same id is linked again from its new text
    conn.execute("UPDATE knowledge SET title = 'pytest timeouts' WHERE id = ?", (lesson,))
    conn.commit()
    assert link_lessons(conn, cfg) == 1
    assert lessons_about(conn, ["Token TTL"]) == [] and lessons_about(conn, ["pytest"]) and \
        lesson in lessons_about(conn, ["pytest"])

    before = _links(conn)
    conn.execute("DELETE FROM knowledge_links")
    conn.commit()
    link_lessons(conn, cfg, full=True)
    assert _links(conn) == before  # a full rebuild gives the same links


def test_a_glossary_refresh_relinks_its_projects_lessons(synced, monkeypatch):
    from interlatch.knowledge import links

    monkeypatch.setattr(links, "system_parts", lambda conn, cfg: {})
    conn, cfg = _build(synced)
    link_lessons(conn, cfg)
    cur = conn.execute("INSERT INTO glossary(term, norm, category, definition) VALUES ('seconds', 'seconds', 'concept', 'd')")
    conn.execute("INSERT INTO glossary_usage(term_id, project_path) VALUES (?, ?)", (cur.lastrowid, CWD))
    conn.commit()
    assert not lessons_about(conn, ["seconds"])
    link_lessons(conn, cfg, projects=[CWD])
    assert lessons_about(conn, ["seconds"])


def test_dismissed_lessons_lose_their_links(synced, monkeypatch):
    from interlatch.knowledge import links

    monkeypatch.setattr(links, "system_parts", lambda conn, cfg: {})
    conn, cfg = _build(synced)
    link_lessons(conn, cfg)
    conn.execute("UPDATE knowledge SET status = 'dismissed' WHERE title = 'Token TTL compared in seconds vs ms'")
    conn.commit()
    link_lessons(conn, cfg)
    assert lessons_about(conn, ["Token TTL"]) == []


def test_the_systems_map_parts_are_linked(synced):
    conn, cfg = _build(synced)
    from interlatch.knowledge.links import system_parts

    parts = system_parts(conn, cfg)
    assert isinstance(parts, dict)  # the real map builds; its parts are names like any other
    link_lessons(conn, cfg, full=True)


def test_relink_from_the_command_line(synced):
    _build(synced)
    out = subprocess.run([sys.executable, "-m", "interlatch", "knowledge", "--relink"], capture_output=True, text=True,
                         timeout=120, env={**os.environ})
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("linked ") and "are about something" in out.stdout
