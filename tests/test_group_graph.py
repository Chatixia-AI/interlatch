"""A group's knowledge graph (knowledge.group_graph): candidate links between the terms its projects use, read from the
glossary's related lists, and drawn only after the analyzer has checked them (the Analyze button)."""

import json
import time

from interlatch.interfaces.web import api
from interlatch.interfaces.web.app import App
from interlatch.knowledge import group_graph
from interlatch.projects import groups

A, B, OUT = "/work/aktio/api", "/work/aktio/batch", "/work/other"


def _world(conn):
    for i, p in enumerate((A, B, OUT)):
        conn.execute("INSERT INTO sessions(id, project_path, project_name, started_at) VALUES (?, ?, ?, ?)",
                     (f"gg-{i}", p, p.rsplit("/", 1)[-1], "2026-10-01T00:00:00Z"))
    terms = [  # term, category, related, sessions, projects
        ("Quote API", "component", ["Postgres", "Batch runner", "pytest"], 9, [A]),
        ("Postgres", "data", ["Quote API"], 7, [A, B]),
        ("Batch runner", "component", ["Postgres", "Quote API"], 5, [B]),
        ("pytest", "tool", ["quote api"], 1, [A]),  # an alias-insensitive match
        ("main.py", "file", ["Quote API"], 20, [A]),  # files never link
        ("Elsewhere", "concept", ["Quote API"], 30, [OUT]),  # not the group's
    ]
    for n, (term, cat, related, sessions, paths) in enumerate(terms, 1):
        conn.execute("INSERT INTO glossary(id, term, norm, category, definition, related_json, n_sessions, aliases_json) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, '[]')", (n, term, term.lower(), cat, f"{term} def", json.dumps(related), sessions))
        for p in paths:
            conn.execute("INSERT INTO glossary_usage(term_id, project_path, context, sources_json) VALUES (?, ?, 'ctx', '[]')", (n, p))
    conn.commit()
    return groups.save(conn, None, "Aktio", ["/work/aktio"])


def test_candidates_are_the_groups_related_terms_once_each(synced):
    conn = synced["conn"]
    gid = _world(conn)
    terms, pairs = group_graph.candidates(conn, groups.paths_in(conn, gid))
    assert sorted(t["term"] for t in terms.values()) == ["Batch runner", "Postgres", "Quote API", "pytest"]
    assert terms[2]["projects"] == [A, B]
    # most discussed first; Quote API–Postgres listed by both sides once; nothing to main.py or Elsewhere
    assert pairs == [(1, 2), (1, 3), (2, 3), (1, 4)]


def test_nothing_is_drawn_until_analyzed_then_only_the_kept_links(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    gid = _world(conn)
    paths = groups.paths_in(conn, gid)
    before = group_graph.graph(conn, gid, paths)
    assert before == {"analysis": None, "terms": 4, "candidates": 4, "calls": 1}

    rec = group_graph.analyze(cfg, conn, gid, paths)
    # link 0 reads from its b side, link 1 dropped, link 2 keeps a's side when `from` is not in the pair
    assert rec["links"] == [{"from": 2, "to": 1, "label": "stores data in"}, {"from": 2, "to": 3, "label": "part of"}]
    assert conn.execute("SELECT COUNT(*) FROM analyses WHERE kind = 'group_graph' AND target = ?", (str(gid),)).fetchone()[0] == 1

    after = group_graph.graph(conn, gid, paths)
    assert after["links"] == rec["links"] and after["analysis"]["checked"] == 4 and after["analysis"]["unchecked"] == 0
    assert {t["term"] for t in after["terms"]} == {"Quote API", "Postgres", "Batch runner", "pytest", "main.py"}  # the mindmap hides files itself
    assert {p["path"] for p in after["projects"]} == {A, B}  # only the group's projects, no "Everywhere"
    pg = next(t for t in after["terms"] if t["term"] == "Postgres")
    assert {u["path"] for u in pg["projects"]} == {A, B}

    # a new related term since: one unchecked link, and a gone term takes its links with it
    conn.execute("UPDATE glossary SET related_json = '[\"Quote API\", \"Postgres\"]' WHERE term = 'pytest'")
    conn.execute("DELETE FROM glossary_usage WHERE term_id = 3")
    conn.commit()
    later = group_graph.graph(conn, gid, paths)
    assert later["analysis"]["unchecked"] == 1
    assert later["links"] == [{"from": 2, "to": 1, "label": "stores data in"}]


def test_the_analyze_button_runs_one_job_per_group(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    gid = _world(conn)
    app = App(cfg)
    ctx = app.context()
    assert api.projects.action_group_graph(ctx, 999999) is None
    assert api.projects.group_graph(ctx, 999999) is None
    assert api.projects.group_graph(ctx, gid)["analysis"] is None
    assert api.projects.action_group_graph(ctx, gid) is True
    for _ in range(100):
        job = app.jobs.snapshot()[f"group-graph:{gid}"]
        if job["state"] != "running":
            break
        time.sleep(0.1)
    assert job["state"] == "done" and job["result"] == "kept 2 of 4 links"
    got = api.projects.group_graph(app.context(), gid)
    assert got["name"] == "Aktio" and len(got["links"]) == 2 and got["analysis"]["model"]
