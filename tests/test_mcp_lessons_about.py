"""The MCP tools read lesson links: the glossary lists the lessons about a term, search puts them first."""

from interlatch.analysis.session import analyze_session
from interlatch.application.search import search_knowledge
from interlatch.interfaces.mcp import Tools
from interlatch.knowledge.glossary import build_glossary
from interlatch.knowledge.links import link_lessons

from conftest import CWD, SID


def _lesson(conn, title, body="b", stage="provisional"):
    cur = conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, source, "
                       "fingerprint, status, stage) VALUES (?, ?, 'demo-app', 'gotcha', ?, ?, 'project', 'analysis', ?, "
                       "'active', ?)", (SID, CWD, title, body, title, stage))
    return cur.lastrowid


def _archive(synced, monkeypatch, parts=None):
    from interlatch.knowledge import links

    monkeypatch.setattr(links, "system_parts", lambda conn, cfg: parts or {})
    conn, cfg = synced["conn"], synced["cfg"]
    analyze_session(conn, cfg, SID)
    build_glossary(conn, cfg, CWD)
    return conn, cfg


def test_the_glossary_lists_the_lessons_about_a_term(synced, monkeypatch):
    conn, cfg = _archive(synced, monkeypatch)
    _lesson(conn, "pytest needs -p no:cacheprovider in CI", "CI caches broke pytest runs.", stage="established")
    _lesson(conn, "Login tests are slow", "They call pytest twice.")  # mentions pytest, isn't about it
    conn.commit()
    link_lessons(conn, cfg)
    reply = Tools(cfg).glossary(term="py.test")  # by an alias, too
    about = reply.split("Lessons about it (1):")[1].strip().splitlines()
    assert about[0].startswith("- [gotcha · established") and "pytest needs -p no:cacheprovider in CI — demo-app" in about[0]
    assert about[-1] == "1 more mention it. search_knowledge finds them."


def test_a_name_only_the_systems_map_knows(synced, monkeypatch):
    parts = {CWD: [{"label": "app-demo-prod", "kind": "deployed"}]}
    conn, cfg = _archive(synced, monkeypatch, parts)
    _lesson(conn, "app-demo-prod needs WEBSITES_PORT=8000", "Without it the container never starts.")
    conn.commit()
    link_lessons(conn, cfg)
    reply = Tools(cfg).glossary(term="app-demo-prod")
    assert reply.startswith("'app-demo-prod' is not in the glossary, but lessons are about it.")
    assert "app-demo-prod needs WEBSITES_PORT=8000" in reply
    assert Tools(cfg).glossary(term="nothing-like-it") == "'nothing-like-it' is not in the glossary."


def test_search_puts_lessons_about_a_name_first(synced, monkeypatch):
    conn, cfg = _archive(synced, monkeypatch)
    mention = _lesson(conn, "Pinned flaky login test", "pytest retried it twice.", stage="canonical")
    about = _lesson(conn, "pytest timeouts in CI", "Raise the timeout.")
    conn.commit()
    link_lessons(conn, cfg)
    ids = [k["id"] for k in search_knowledge(conn, "pytest", glossary=True)]
    assert ids.index(about) < ids.index(mention)  # about it beats a canonical lesson that only mentions it
    plain = [k["id"] for k in search_knowledge(conn, "pytest")]
    assert plain.index(mention) < plain.index(about)  # without the glossary, trust orders as before


def test_lessons_about_a_term_across_projects_say_where_and_narrow_by_project(synced, monkeypatch):
    conn, cfg = _archive(synced, monkeypatch)
    _lesson(conn, "pytest fixtures leak state", "Reset them.")
    other = "/Users/test/Projects/other-app"
    conn.execute("INSERT INTO glossary_usage(term_id, project_path) SELECT id, ? FROM glossary WHERE norm = 'pytest'", (other,))
    conn.execute("INSERT INTO knowledge(project_path, project_name, kind, title, body, scope, source, fingerprint, status) "
                 "VALUES (?, 'other-app', 'gotcha', 'pytest needs a conftest here', 'b', 'project', 'analysis', 'o1', 'active')",
                 (other,))
    conn.commit()
    link_lessons(conn, cfg)
    tools = Tools(cfg)
    reply = tools.glossary(term="pytest")
    assert "Lessons about it (2):" in reply
    assert "By project: demo-app 1, other-app 1. Pass project= (with this term, or to search_knowledge) for one project's." in reply
    one = tools.glossary(term="pytest", project=other)
    assert "Lessons about it (1):" in one and "pytest needs a conftest here" in one and "By project" not in one
    assert "pytest fixtures leak state" not in one
