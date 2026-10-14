import json
import os
import subprocess
import sys

from interlatch.analysis.session import analyze_session
from interlatch.export_md import export_markdown
from interlatch.glossary import build_glossary, glossary_entries, lookup
from interlatch.synthesize import GLOBAL

from conftest import CWD, SID


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
    from interlatch.glossary import build_glossaries

    conn, cfg, _ = _build(synced)
    results = build_glossaries(cfg, [CWD, GLOBAL, "/no/knowledge/here"], concurrency=3)
    assert results[CWD] == 2 and results[GLOBAL] == 1 and results["/no/knowledge/here"] == "no knowledge"
    assert conn.execute("SELECT COUNT(*) FROM glossary").fetchone()[0] == 2


def test_map_data_links_terms_to_projects_and_knowledge(synced):
    from interlatch.glossary import map_data

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
    from interlatch.glossary import build_themes, map_data, themes_due

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

    from interlatch.server import App

    conn, cfg, _ = _build(synced)
    for name in [f"tool{i}" for i in range(30)]:  # a category big enough to be due
        conn.execute("INSERT INTO glossary(term, norm, category, definition) VALUES (?, ?, 'tool', 'a tool')", (name, name))
    conn.commit()
    app = App(cfg)
    assert app.map()["themes_due"] == ["tool"]
    assert app.action_themes(force=False) is True
    for _ in range(100):
        job = app.jobs.snapshot()["themes"]
        if job["state"] != "running":
            break
        time.sleep(0.1)
    assert job["state"] == "done" and job["result"] == "themes for 1/1 categories"
    assert app.map()["themes_due"] == [] and "tool" in app.map()["themes"]
