"""Which coding project a web chat most likely belongs to (chat_links.py): rules, screening, analysis, and you."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from interlatch.projects import chat_links
from interlatch.projects.chat_links import Linker, decide, link_of, link_pending, suggest
from interlatch.storage.sqlite import connect as db_connect

AC, WM, APP = "/home/me/agents-chronicle", "/home/me/watchme", "/home/me/app"


def add(conn, sid, source, path, name=None, title="", text="", sig="v1", started="2026-10-01T00:00:00Z"):
    conn.execute("INSERT INTO sessions(id, source, agent, project_path, project_name, title, files_sig, started_at) "
                 "VALUES (?,?,?,?,?,?,?,?)", (sid, source, "claude", path, name, title, sig, started))
    if text:
        conn.execute("INSERT INTO events(session_id, seq, kind, text) VALUES (?, 1, 'prompt', ?)", (sid, text))
    conn.commit()


def chat(conn, sid, text="", path="claude.ai", **kw):
    add(conn, sid, "claude-ai-export", path, text=text, **kw)


def term(conn, word, *paths):
    tid = conn.execute("INSERT INTO glossary(term, norm) VALUES (?, ?)", (word, word.lower())).lastrowid
    for p in paths:
        conn.execute("INSERT INTO glossary_usage(term_id, project_path) VALUES (?, ?)", (tid, p))


@pytest.fixture()
def db(tmp_path):
    conn = db_connect(tmp_path / "db.sqlite")
    add(conn, "s-ac", "transcript", AC, "agents-chronicle", started="2026-10-20T00:00:00Z")
    add(conn, "s-wm", "transcript", WM, "watchme", started="2026-10-10T00:00:00Z")
    add(conn, "s-app", "transcript", APP, "app")
    conn.execute("INSERT INTO machines(id, name, repos_json) VALUES ('m1', 'mac', ?)",
                 (json.dumps({AC: [AC, "github.com/chatixia-ai/interlatch"]}),))
    term(conn, "Innerlatch", AC)
    term(conn, "Arena", AC)
    term(conn, "HeatGuard", WM)
    term(conn, "pytest", AC, WM)  # used by both: tells them apart from nothing
    conn.commit()
    return conn


def test_projects_and_their_names(db):
    lk = Linker(db)
    assert lk.projects[AC].aliases == {"agents-chronicle", "interlatch"}  # its repository's name too
    assert lk.projects[AC].terms == {"innerlatch", "arena"} and lk.projects[WM].terms == {"heatguard"}
    assert list(lk.choices()) == ["agents-chronicle", "watchme", "app"]  # most recent first
    assert "claude.ai" not in lk.projects  # chats are no projects


@pytest.mark.parametrize("site, text, want", [
    ("claude.ai/Agents Chronicle", "", (AC, "high", "site:claude.ai:Agents Chronicle")),  # its claude.ai project's name
    ("chatgpt.com/interlatch", "", (AC, "high", "site:chatgpt.com:interlatch")),
    ("claude.ai", "How should interlatch store Innerlatch quests?", (AC, "medium", "mentions:interlatch|innerlatch")),
    ("claude.ai", "The build in /home/me/agents-chronicle fails since the Arena change and Innerlatch",
     (AC, "high", "mentions:agents-chronicle|arena|innerlatch")),
    ("claude.ai", "pytest fails in interlatch and in watchme", None),  # two projects alike: no guess
    ("claude.ai", "my app crashes on start", None),  # a name too common to tell by
    ("claude.ai", "What is Innerlatch?", None),  # one term is not enough
    ("claude.ai/Recipes", "Okra for a toddler", None),
])
def test_rules(db, site, text, want):
    assert Linker(db).rule(site, text) == want


def test_rules_run_on_chats_once_and_again_when_they_change(db):
    chat(db, "c1", "Refactor the interlatch Innerlatch page")
    chat(db, "c2", "Okra recipes")
    chat(db, "c3", "", path="claude.ai/watchme")
    assert link_pending(db) == 2
    row = lambda sid: db.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert (row("c1")["link_project"], row("c1")["link_by"], row("c1")["link_state"]) == (AC, "rules", "suggested")
    assert row("c2")["link_by"] is None and row("c2")["link_sig"] == "v1"  # checked, nothing found
    assert row("c3")["link_project"] == WM
    assert row("s-ac")["link_sig"] is None  # coding sessions are not chats
    assert link_pending(db) == 0  # nothing changed

    decide(db, "c3", AC)  # yours stays, whatever the chat says later
    db.execute("UPDATE sessions SET files_sig = 'v2' WHERE id IN ('c1', 'c3')")
    db.execute("UPDATE events SET text = 'HeatGuard alerts in watchme' WHERE session_id = 'c1'")
    db.commit()
    link_pending(db)
    assert (row("c1")["link_project"], row("c1")["link_sig"]) == (WM, "v2")
    assert (row("c3")["link_project"], row("c3")["link_by"], row("c3")["link_state"]) == (AC, "you", "confirmed")


def test_suggestions_give_way_to_surer_ones(db):
    for sid in ("c1", "c2", "c3"):
        chat(db, sid)
    choices = Linker(db).choices()
    get = lambda sid: tuple(db.execute("SELECT link_project, link_state, link_by, link_confidence FROM sessions "
                                       "WHERE id = ?", (sid,)).fetchone())
    suggest(db, "c1", "watchme", choices, chat_links.BY_SCREEN, "low")
    assert get("c1") == (WM, "suggested", "screen", "low")
    suggest(db, "c1", "agents-chronicle", choices, chat_links.BY_SCREEN)  # screening fills only what is open
    assert get("c1")[0] == WM
    suggest(db, "c1", "agents-chronicle", choices, chat_links.BY_ANALYSIS, "high")  # analysis corrects it
    assert get("c1") == (AC, "suggested", "analysis", "high")
    suggest(db, "c2", "none", choices, chat_links.BY_ANALYSIS, "high")
    assert get("c2") == (None, "suggested", "analysis", "high")
    suggest(db, "c3", "a project it made up", choices, chat_links.BY_ANALYSIS)  # not a choice: nothing said
    assert get("c3") == (None, None, None, None)
    decide(db, "c1", "")
    suggest(db, "c1", "watchme", choices, chat_links.BY_ANALYSIS, "high")
    assert get("c1")[:3] == (None, "none", "you")


def test_decide_takes_only_chats_and_your_projects(db):
    chat(db, "c1")
    with pytest.raises(ValueError):
        decide(db, "s-ac", WM)  # a coding session has its own project
    with pytest.raises(ValueError):
        decide(db, "c1", "/home/me/elsewhere")
    with pytest.raises(ValueError):
        decide(db, "c1", "claude.ai")  # a chat's own place is no project
    out = decide(db, "c1", AC)
    assert (out["project"], out["name"], out["state"], out["by"]) == (AC, "agents-chronicle", "confirmed", "you")
    assert {c["path"] for c in out["choices"]} == {AC, WM, APP}
    assert link_of(db, "c1")["state"] == "confirmed"


# ------------------------------------------------------------------ with the dashboard, screening and analysis
from conftest import CWD  # noqa: E402
from test_screen import gpt_chat, write_export  # noqa: E402


def _serve(env):
    from interlatch.interfaces.web.app import App
    from interlatch.interfaces.web.handler import make_handler

    app = App(env["cfg"])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    httpd.RequestHandlerClass = make_handler(app, httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _call(url, body=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method="GET" if body is None else "POST",
                                 headers={"X-Interlatch": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_confirm_on_the_session_page_puts_the_chat_on_the_project_page(env):
    conn = db_connect(env["cfg"].db_path)
    add(conn, "s-demo", "transcript", CWD, "demo-app")
    chat(conn, "c-demo", "How do I make demo-app deploy faster?")
    conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, fingerprint) "
                 "VALUES ('c-demo', 'claude.ai', 'claude.ai', 'decision', 'Deploy demo-app with make ship', 'fp-chat')")
    conn.commit()
    link_pending(conn)
    httpd, base = _serve(env)
    try:
        status, s = _call(f"{base}/api/sessions/c-demo")
        assert status == 200 and (s["link"]["project"], s["link"]["state"], s["link"]["by"]) == (CWD, "suggested", "rules")
        project = lambda: _call(f"{base}/api/project?path={urllib.request.quote(CWD)}")[1]
        assert not project()["chats"]  # a suggestion alone doesn't put it there
        assert "Deploy demo-app with make ship" not in [k["title"] for k in project()["knowledge"]]

        assert _call(f"{base}/api/sessions/c-demo/link", {"project": CWD})[1]["state"] == "confirmed"
        p = project()
        assert [c["id"] for c in p["chats"]] == ["c-demo"]
        assert "Deploy demo-app with make ship" in [k["title"] for k in p["knowledge"]]
        # the lesson itself stays the chat's: where it is shared does not change
        assert conn.execute("SELECT project_path FROM knowledge WHERE fingerprint = 'fp-chat'").fetchone()[0] == "claude.ai"

        assert _call(f"{base}/api/sessions/c-demo/link", {"project": ""})[1]["state"] == "none"
        assert not project()["chats"]
        assert _call(f"{base}/api/sessions/s-demo/link", {"project": CWD})[0] == 400  # not a chat
        assert _call(f"{base}/api/sessions/c-demo/link", {"project": 3})[0] == 400
        assert _call(f"{base}/api/sessions/s-demo")[1]["link"] is None
    finally:
        httpd.shutdown()


def test_screening_names_each_chats_project(env):
    from interlatch.analysis.screen import screen_chats
    from interlatch.ingestion.chat_import import import_export

    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    add(conn, "s-demo", "transcript", CWD, "demo-app")
    import_export(cfg, conn, write_export(env["tmp"] / "chatgpt.zip"))
    screen_chats(cfg, conn)
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM sessions WHERE source = 'chatgpt-export'")}
    deploy = rows["c-deploy"]
    assert (deploy["link_project"], deploy["link_by"], deploy["link_confidence"]) == (CWD, "screen", "medium")
    assert (rows["c-okra"]["link_project"], rows["c-okra"]["link_state"]) == (None, "suggested")  # likely none
    assert rows["c-chore"]["link_by"] is None  # settled by a screening rule: no model saw it


def test_a_chats_analysis_names_its_project(env, monkeypatch):
    from interlatch.analysis.session import analyze_session
    from interlatch.ingestion.chat_import import import_export

    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    add(conn, "s-demo", "transcript", CWD, "demo-app")
    import_export(cfg, conn, write_export(env["tmp"] / "chatgpt.zip", [gpt_chat(
        "c-long", "Deploy pipeline", "Our deploy to Azure fails with exit 137 after the build step, why? " * 6)]))
    log = env["tmp"] / "fake.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    analyze_session(conn, cfg, "c-long")
    row = conn.execute("SELECT link_project, link_by, link_state FROM sessions WHERE id = 'c-long'").fetchone()
    assert tuple(row) == (CWD, "analysis", "suggested")
    call = json.loads(log.read_text().splitlines()[-1])
    assert "The developer's projects (for `project`): demo-app" in call["prompt_head"]
    assert '"project"' in " ".join(call["args"])  # the schema asks for it

    from interlatch.analysis.session import ANALYSIS_SCHEMA, REDUCE_SCHEMA

    assert "project" not in ANALYSIS_SCHEMA["properties"] and "project" not in REDUCE_SCHEMA["properties"]  # coding sessions
