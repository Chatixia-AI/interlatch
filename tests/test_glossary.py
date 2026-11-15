import json
import os
import re
import subprocess
import sys

from interlatch.analysis.session import analyze_session
from interlatch.interfaces.exports.vault import export_markdown
from interlatch.knowledge.glossary import build_glossary, glossary_entries, lookup
from interlatch.knowledge.synthesize import GLOBAL

from conftest import CWD, SID
from interlatch.interfaces.web import api


def _build(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    analyze_session(conn, cfg, SID)
    return conn, cfg, build_glossary(conn, cfg, CWD)


def test_project_glossary_is_stored_with_usage_and_stats(synced):
    conn, cfg, n = _build(synced)
    assert n == 2  # empty and duplicate terms are dropped
    entries = {e["term"]: e for e in glossary_entries(conn)}
    assert set(entries) == {"pytest", "Token TTL"}
    py = entries["pytest"]
    assert py["category"] == "tool" and py["aliases"] == ["py.test"] and py["related"] == ["Token TTL"]
    assert py["usage"][0]["project_path"] == CWD and py["usage"][0]["context"] == "runs the login tests"
    assert py["n_sessions"] == 1 and py["top_sessions"][0]["id"] == SID  # mentioned in the transcript
    ttl = entries["Token TTL"]
    assert 999999 not in ttl["usage"][0]["sources"]  # unknown source ids are discarded


def test_global_pass_merges_and_its_definition_wins(synced):
    conn, cfg, _ = _build(synced)
    build_glossary(conn, cfg, GLOBAL)
    py = lookup(conn, "pytest")
    assert {u["project_path"] for u in py["usage"]} == {CWD, GLOBAL}
    assert py["definition"] == "Python test runner used everywhere" and py["definition_source"] == GLOBAL
    build_glossary(conn, cfg, CWD)  # a later project pass keeps the cross-project definition
    assert lookup(conn, "pytest")["definition"] == "Python test runner used everywhere"
    assert conn.execute("SELECT COUNT(*) FROM glossary WHERE norm = 'pytest'").fetchone()[0] == 1


def test_lookup_by_alias_and_rebuild_drops_orphans(synced):
    conn, cfg, _ = _build(synced)
    assert lookup(conn, "ttl")["term"] == "Token TTL"
    assert lookup(conn, "py.test")["term"] == "pytest"
    conn.execute("DELETE FROM glossary_usage WHERE term_id = (SELECT id FROM glossary WHERE norm = 'token ttl')")
    conn.commit()
    build_glossary(conn, cfg, GLOBAL)  # any rebuild sweeps terms no project uses any more... unless re-added
    assert {e["term"] for e in glossary_entries(conn)} == {"pytest"}


def test_glossary_everywhere_else(synced):
    conn, cfg, _ = _build(synced)
    export_markdown(conn, cfg)
    text = (cfg.notes_dir / "Glossary.md").read_text()
    assert "### pytest" in text and "runs the login tests" in text and "[[Glossary]]" in (cfg.notes_dir / "Home.md").read_text()
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "glossary", "arguments": {"term": "TTL"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "glossary", "arguments": {"project": "demo-app"}}}]
    out = subprocess.run([sys.executable, "-m", "interlatch", "mcp"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                         capture_output=True, text=True, timeout=60, env={**os.environ})
    replies = {r["id"]: r["result"]["content"][0]["text"] for r in map(json.loads, out.stdout.splitlines())}
    assert "Token TTL" in replies[1] and "Lifetime of an auth token" in replies[1]
    assert "**pytest**" in replies[2]


def test_parallel_glossary_builds(synced):
    from interlatch.knowledge.glossary import build_glossaries

    conn, cfg, _ = _build(synced)
    results = build_glossaries(cfg, [CWD, GLOBAL, "/no/knowledge/here"], concurrency=3)
    assert results[CWD] == "up to date" and results[GLOBAL] == 1 and results["/no/knowledge/here"] == "no knowledge"
    assert build_glossaries(cfg, [CWD, GLOBAL], concurrency=3, full=True) == {CWD: 2, GLOBAL: 1}
    assert conn.execute("SELECT COUNT(*) FROM glossary").fetchone()[0] == 2


def test_map_data_links_terms_to_projects_and_knowledge(synced):
    from interlatch.knowledge.glossary import map_data

    conn, cfg, _ = _build(synced)
    data = map_data(conn)
    assert [p["path"] for p in data["projects"]] == [CWD]
    assert data["projects"][0]["sessions"] == 1 and data["projects"][0]["label"] == "demo-app"
    terms = {t["term"]: t for t in data["terms"]}
    assert terms["pytest"]["projects"] == [{"path": CWD, "context": "runs the login tests"}]
    assert terms["pytest"]["related"] == ["Token TTL"]
    for t in terms.values():  # every source is a live knowledge item, shipped alongside
        assert all(i in data["knowledge"] for i in t["knowledge"])
    sourced = [t for t in terms.values() if t["knowledge"]]
    assert sourced and data["knowledge"][sourced[0]["knowledge"][0]]["kind"]
    conn.execute("UPDATE knowledge SET status = 'dismissed'")
    assert all(not t["knowledge"] for t in map_data(conn)["terms"])  # dismissed items drop out


def test_themes_group_a_category_and_rerun_only_when_it_changes(synced):
    from interlatch.knowledge.glossary import build_themes, map_data, themes_due

    conn, cfg, _ = _build(synced)
    for name in ("ruff", "mypy"):  # two more tools the fake Claude leaves ungrouped
        conn.execute("INSERT INTO glossary(term, norm, category, definition) VALUES (?, ?, 'tool', 'a tool')", (name, name))
    conn.commit()
    assert themes_due(conn, min_terms=3) == ["tool"]
    assert build_themes(cfg, ["tool"]) == {"tool": 2}
    themes = {r["name"]: r for r in conn.execute("SELECT * FROM glossary_themes WHERE category = 'tool'")}
    assert set(themes) == {"Testing", "Other"}  # duplicate and nameless themes dropped, leftovers in Other
    assert themes["Testing"]["description"] == "How tests run" and themes["Testing"]["n_terms"] == 1
    assert themes["Other"]["n_terms"] == 2
    by_term = dict(conn.execute("SELECT term, theme FROM glossary WHERE category = 'tool'").fetchall())
    assert by_term == {"pytest": "Testing", "ruff": "Other", "mypy": "Other"}
    assert conn.execute("SELECT COUNT(*) FROM analyses WHERE kind = 'themes'").fetchone()[0] == 1
    assert themes_due(conn, min_terms=3) == []  # unchanged since grouped
    conn.execute("INSERT INTO glossary(term, norm, category, definition) VALUES ('black', 'black', 'tool', 'formatter')")
    conn.commit()
    assert themes_due(conn, min_terms=3) == ["tool"]
    data = map_data(conn)
    assert [t["name"] for t in data["themes"]["tool"]] == ["Other", "Testing"]  # biggest first
    pytest_term = next(t for t in data["terms"] if t["term"] == "pytest")
    assert pytest_term["theme"] == "Testing" and pytest_term["agents"] == ["claude"]
    assert pytest_term["sessions"][0]["id"] == SID and pytest_term["sessions"][0]["title"]


def test_dashboard_themes_job_groups_due_categories(synced):
    import time

    from interlatch.interfaces.web.app import App

    conn, cfg, _ = _build(synced)
    for name in [f"tool{i}" for i in range(30)]:  # a category big enough to be due
        conn.execute("INSERT INTO glossary(term, norm, category, definition) VALUES (?, ?, 'tool', 'a tool')", (name, name))
    conn.commit()
    app = App(cfg)
    assert api.knowledge.map(app.context())["themes_due"] == ["tool"]
    assert api.knowledge.action_themes(app.context(), force=False) is True
    for _ in range(100):
        job = app.jobs.snapshot()["themes"]
        if job["state"] != "running":
            break
        time.sleep(0.1)
    assert job["state"] == "done" and job["result"] == "themes for 1/1 categories"
    assert api.knowledge.map(app.context())["themes_due"] == [] and "tool" in api.knowledge.map(app.context())["themes"]


def _term(conn, term, aliases, seen=None):
    from interlatch.core.util import dumps
    from interlatch.knowledge.glossary import norm

    conn.execute("INSERT INTO glossary(term, norm, aliases_json, alias_seen_json, category, definition, definition_source) "
                 "VALUES (?,?,?,?,'concept','d',?)", (term, norm(term), dumps(aliases), dumps(seen) if seen is not None else None, CWD))
    conn.commit()


def _new(term, aliases):
    return {"term": term, "aliases": aliases, "category": "concept", "definition": "d", "context": "", "related": [], "sources": []}


def test_alias_evidence_counts_the_sessions_that_used_each_alias(synced):
    from interlatch.knowledge.glossary import alias_evidence, also, alias_split

    conn, cfg, _ = _build(synced)
    text = conn.execute("SELECT text FROM events WHERE text LIKE '%pytest%' LIMIT 1").fetchone()[0]
    assert "pytest" in text.lower()  # the transcript says pytest, never "py.test" or the made-up names below
    seen = alias_evidence(conn, ["PYTEST", "py-test-runner-nobody-typed", "pt", "py test runner nobody typed"])
    assert seen == {"PYTEST": 1, "py-test-runner-nobody-typed": 0, "py test runner nobody typed": 0}  # "pt": too short to search
    conn.execute("UPDATE events SET text = text || ' the write ahead log' WHERE id = (SELECT MIN(id) FROM events WHERE text LIKE '%pytest%')")
    conn.execute("INSERT INTO events_fts(events_fts) VALUES ('rebuild')")  # transcripts are never edited: no update trigger
    assert alias_evidence(conn, ["write-ahead log"]) == {"write-ahead log": 1}  # punctuation as spaces counts
    py = lookup(conn, "pytest")  # a build records it for the terms it wrote
    assert py["aliases"] == ["py.test"] and py["alias_seen"] == {"py.test": 0}
    assert alias_split(py) == ([], ["py.test"]) and also(py) == "added by the model, not seen in sessions: py.test"
    assert also({"aliases": ["a", "bb", "ccc"], "alias_seen": {"a": 2, "ccc": 0}}) == \
        "also: a, bb; added by the model, not seen in sessions: ccc"
    assert also({"aliases": []}) == ""


def test_a_shared_alias_joins_two_terms_only_when_sessions_used_it(synced):
    from interlatch.knowledge.glossary import _upsert_term

    conn = synced["conn"]
    _term(conn, "Azure Container Apps", ["ACA", "container platform"], {"ACA": 3, "container platform": 0})
    aca = conn.execute("SELECT id FROM glossary WHERE norm = 'azure container apps'").fetchone()[0]
    # an alias only the model wrote is no reason to call two terms one
    other = _upsert_term(conn, _new("Kubernetes", ["container platform"]), source=CWD)
    assert other != aca
    # an alias sessions used is
    assert _upsert_term(conn, _new("Container Apps", ["aca"]), source=CWD) == aca
    # and so is the new term's own name, whatever the evidence: two passes named the same thing
    assert _upsert_term(conn, _new("Container platform", []), source=CWD) == aca
    names = json.loads(conn.execute("SELECT aliases_json FROM glossary WHERE id = ?", (aca,)).fetchone()[0])
    assert names == ["ACA", "container platform", "Container Apps"]
    # a term nobody measured yet joins on its name only
    _term(conn, "Write-ahead log", ["WAL", "journal"])
    assert _upsert_term(conn, _new("Journaling", ["journal"]), source=CWD) not in (aca, other) and \
        conn.execute("SELECT COUNT(*) FROM glossary WHERE norm = 'journaling'").fetchone()[0] == 1


def test_unseen_aliases_go_first_when_a_term_has_too_many(synced):
    from interlatch.knowledge.glossary import MAX_ALIASES, _upsert_term

    conn = synced["conn"]
    old = [f"alias{i}" for i in range(MAX_ALIASES)]
    _term(conn, "Big term", old, {a: (0 if i < 2 else 1) for i, a in enumerate(old)})
    _upsert_term(conn, _new("Big term", ["fresh name"]), source=CWD)
    kept = json.loads(conn.execute("SELECT aliases_json FROM glossary WHERE norm = 'big term'").fetchone()[0])
    assert kept == [*old[2:], "fresh name", "alias0"]  # one unseen name made room, the used ones all stay


def test_a_build_measures_terms_that_have_no_alias_evidence_yet(synced):
    conn, cfg, _ = _build(synced)
    _term(conn, "Old term", ["pytest", "never typed anywhere"])  # as an archive from before alias evidence holds it
    conn.execute("INSERT INTO glossary_usage(term_id, project_path) SELECT id, ? FROM glossary WHERE norm = 'old term'", (GLOBAL,))
    conn.commit()
    build_glossary(conn, cfg, CWD, full=True)
    assert lookup(conn, "Old term")["alias_seen"] == {"pytest": 1, "never typed anywhere": 0}


def test_alias_evidence_reaches_the_map_the_mcp_reply_and_the_export(synced):
    from interlatch.interfaces.mcp import Tools
    from interlatch.knowledge.glossary import map_data

    conn, cfg, _ = _build(synced)
    conn.execute("UPDATE glossary SET aliases_json = '[\"py.test\", \"pytest runner\"]', "
                 "alias_seen_json = '{\"py.test\": 4, \"pytest runner\": 0}' WHERE norm = 'pytest'")
    conn.commit()
    term = next(t for t in map_data(conn)["terms"] if t["term"] == "pytest")
    assert term["alias_seen"] == {"py.test": 4, "pytest runner": 0}
    reply = Tools(cfg).glossary(term="pytest")
    assert reply.splitlines()[0] == "**pytest** (tool) — also: py.test; added by the model, not seen in sessions: pytest runner"
    export_markdown(conn, cfg)
    assert "also: py.test; added by the model, not seen in sessions: pytest runner" in (cfg.notes_dir / "Glossary.md").read_text()


def _tools(conn, *names):
    for name in names:
        conn.execute("INSERT INTO glossary(term, norm, category, definition) VALUES (?, ?, 'tool', 'a tool')", (name, name))
    conn.commit()


def _themed(conn):
    return {r["name"]: (r["description"], r["n_terms"]) for r in conn.execute("SELECT * FROM glossary_themes WHERE category = 'tool'")}


def test_new_terms_are_filed_into_the_existing_themes(synced):
    from conftest import fake_log
    from interlatch.knowledge.glossary import build_themes, themes_due

    conn, cfg, _ = _build(synced)
    _tools(conn, "ruff", "mypy", "pylint", "flake8", "bandit")
    assert build_themes(cfg, ["tool"]) == {"tool": 2}  # first time: the whole category, Testing and Other
    before = dict(conn.execute("SELECT term, theme FROM glossary WHERE category = 'tool'").fetchall())
    _tools(conn, "black", "isort", "tox", "nox")  # 4 new of 10: filed, not regrouped
    assert themes_due(conn, min_terms=3) == ["tool"]
    assert build_themes(cfg) == {} and build_themes(cfg, ["tool"]) == {"tool": 3}  # the default needs 25 terms
    prompt = fake_log(synced)[-1]["prompt_head"]
    assert "File the new terms" in prompt and '"name": "Testing"' in prompt and '"examples": ["pytest"]' in prompt
    new_terms = prompt.split("<new_terms>")[1]
    assert all(f'"term": "{n}"' in new_terms for n in ("black", "isort", "tox", "nox")) and "ruff" not in new_terms
    after = dict(conn.execute("SELECT term, theme FROM glossary WHERE category = 'tool'").fetchall())
    assert {t: after[t] for t in before} == before  # placed terms stay put
    assert {t: after[t] for t in ("black", "isort", "tox", "nox")} == \
        {"black": "Other", "isort": "Formatting", "tox": "Formatting", "nox": "Other"}  # "OTHER", the biggest, is Other
    assert _themed(conn) == {"Testing": ("How tests run", 1), "Other": ("Terms that fit none of the other themes.", 7),
                             "Formatting": ("Code style tools", 2)}
    assert themes_due(conn, min_terms=3) == []
    assert conn.execute("SELECT COUNT(*) FROM analyses WHERE kind = 'themes'").fetchone()[0] == 2


def test_a_mostly_new_category_or_force_regroups_and_dropped_terms_need_no_call(synced):
    from conftest import fake_log
    from interlatch.knowledge.glossary import build_themes, themes_due

    conn, cfg, _ = _build(synced)
    _tools(conn, "ruff", "mypy")
    build_themes(cfg, ["tool"])
    _tools(conn, "black", "isort", "tox", "nox")  # 4 new of 7: more than half, grouped afresh
    build_themes(cfg, ["tool"])
    assert "Group the terms into themes" in fake_log(synced)[-1]["prompt_head"]
    assert _themed(conn) == {"Testing": ("How tests run", 1), "Other": ("Terms that fit none of the other themes.", 6)}
    build_themes(cfg, ["tool"], force=True)  # nothing new, but forced: from scratch
    assert "Group the terms into themes" in fake_log(synced)[-1]["prompt_head"]
    calls = len(fake_log(synced))
    build_glossary(conn, cfg, CWD, full=True)  # drops the tools no project uses: their themes are recounted, no themes call
    assert [c for c in fake_log(synced)[calls:] if "themes" in c["prompt_head"]] == []
    assert _themed(conn) == {"Testing": ("How tests run", 1)} and "tool" not in themes_due(conn, min_terms=1)


def _lesson(conn, title, project=CWD):
    cur = conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, confidence, "
                       "source, fingerprint, created_at, updated_at, status) VALUES (?, ?, 'demo-app', 'gotcha', ?, 'b', "
                       "'project', 'high', 'analysis', ?, '2026-11-15', '2026-11-15', 'active')", (SID, project, title, title))
    conn.commit()
    return cur.lastrowid


def _usage(conn, term):
    row = conn.execute("SELECT u.* FROM glossary_usage u JOIN glossary g ON g.id = u.term_id WHERE g.term = ? "
                       "AND u.project_path = ?", (term, CWD)).fetchone()
    return row and {"context": row["context"], "sources": json.loads(row["sources_json"])}


def test_a_later_pass_reads_only_the_knowledge_added_since(synced):
    from conftest import fake_log
    from interlatch.knowledge.glossary import SEEN_KEY
    from interlatch.storage.kv import kv_get

    conn, cfg, _ = _build(synced)
    newest = conn.execute("SELECT MAX(id) FROM knowledge WHERE project_path = ?", (CWD,)).fetchone()[0]
    assert kv_get(conn, SEEN_KEY + CWD) == str(newest)
    before = _usage(conn, "pytest")["sources"]
    calls = len(fake_log(synced))
    assert build_glossary(conn, cfg, CWD) == 0 and len(fake_log(synced)) == calls  # nothing new: no call

    new = _lesson(conn, "Redis cache keys need a prefix")
    assert build_glossary(conn, cfg, CWD) == 2
    call = fake_log(synced)[-1]
    prompt = call["prompt_head"]
    assert prompt.rstrip().endswith("Update the glossary.") and "<knowledge_base>" not in prompt
    assert '"term": "pytest"' in prompt.split("<existing_terms>")[1].split("</existing_terms>")[0]
    assert [int(i) for i in re.findall(r'"id": (\d+)', prompt.split("<knowledge_items>")[1])] == [new]
    assert any("already exists" in a for a in call["args"])  # the update note is in the system prompt
    assert _usage(conn, "pytest")["sources"] == sorted({*before, new})  # sources add up
    assert kv_get(conn, SEEN_KEY + CWD) == str(new)


def test_an_update_drops_uses_whose_sources_all_went_inactive(synced):
    conn, cfg, _ = _build(synced)
    old = _lesson(conn, "Legacy cache layer")
    for term, sources in (("Legacy cache", [old]), ("Unsourced", [])):
        cur = conn.execute("INSERT INTO glossary(term, norm, category, definition) VALUES (?, ?, 'concept', 'd')",
                           (term, term.lower()))
        conn.execute("INSERT INTO glossary_usage(term_id, project_path, sources_json) VALUES (?, ?, ?)",
                     (cur.lastrowid, CWD, json.dumps(sources)))
    conn.execute("UPDATE knowledge SET status = 'dismissed' WHERE id = ?", (old,))
    conn.commit()
    _lesson(conn, "Something new")
    build_glossary(conn, cfg, CWD)
    assert lookup(conn, "Legacy cache") is None  # its only source was dismissed: the use and then the term go
    assert _usage(conn, "Unsourced") == {"context": None, "sources": []}  # never cited a source: kept
    assert old not in _usage(conn, "pytest")["sources"]


def test_too_much_new_knowledge_or_no_terms_left_means_a_full_pass(synced, monkeypatch):
    from conftest import fake_log
    from interlatch.knowledge import glossary

    conn, cfg, _ = _build(synced)
    monkeypatch.setattr(glossary, "UPDATE_LIMIT", 1)
    _lesson(conn, "One")
    _lesson(conn, "Two")
    build_glossary(conn, cfg, CWD)
    assert fake_log(synced)[-1]["prompt_head"].rstrip().endswith("Write the glossary.")
    conn.execute("DELETE FROM glossary_usage WHERE project_path = ?", (CWD,))  # e.g. a hub removed the project
    conn.commit()
    _lesson(conn, "Three")
    build_glossary(conn, cfg, CWD)
    assert fake_log(synced)[-1]["prompt_head"].rstrip().endswith("Write the glossary.")
