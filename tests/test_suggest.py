"""Suggestions: refresh upserts, approval, preview, undo, and the ~/.claude.json change."""

import json
import sqlite3

import pytest

from interlatch.knowledge import instructions, suggest
from interlatch.storage.sqlite import connect
from interlatch.storage.schema import SCHEMA_VERSION
from interlatch.knowledge.instructions import BEGIN

from friction_fixture import ago, make_archive


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    yield from make_archive(tmp_path, monkeypatch)


def _spread(a, cause, n=3, *, days=1, agent="claude"):
    """The same cause in n sessions of n different projects."""
    for i in range(n):
        p = a.project(f"proj{i}")
        sid = f"{cause}-{agent}-{i}"
        a.session(sid, p, agent=agent, days=days)
        a.cause(sid, cause, ago(days))


def _by_key(conn):
    return {s["key"]: s for s in suggest.list_suggestions(conn)}


def _playwright_json(home, args=None):
    path = home / ".claude.json"
    path.write_text(json.dumps({"numStartups": 7, "mcpServers": {
        "playwright": {"type": "stdio", "command": "npx", "args": args or ["-y", "@playwright/mcp@latest"]},
        "chronicle": {"command": "chronicle", "args": ["mcp"]}}}, indent=2) + "\n")
    return path


def test_schema_has_the_suggestions_table(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.execute("CREATE TABLE knowledge (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL)")
    raw.execute("PRAGMA user_version = 8")
    raw.commit()
    raw.close()
    conn = connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(suggestions)")}
    assert {"key", "kind", "origin", "target_path", "text", "evidence_json", "warnings_json", "status", "seen_at",
            "applied_text", "dismissed_reason"} <= cols
    assert "idx_suggestions_status" in {r[1] for r in conn.execute("PRAGMA index_list(suggestions)")}
    assert {r[1] for r in conn.execute("PRAGMA table_info(suggestion_scopes)")} == {"subject", "scope", "updated_at"}


def test_refresh_proposes_fixes_for_recurring_causes(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    _spread(a, "zsh-nomatch")
    _spread(a, "sleep-polling-blocked", n=1)  # one session in one project: not recurring yet
    assert suggest.refresh(conn, cfg) == {"new": 2, "updated": 0, "stale": 0}
    got = _by_key(conn)
    line = got["friction:zsh-nomatch:claude:user"]
    assert line["kind"] == "instruction" and line["agent"] == "claude" and line["project_path"] is None
    assert line["target_path"] == str(home / ".claude" / "CLAUDE.md")
    assert line["evidence"]["line"].startswith("seen in 3 sessions across 3 projects · still happening · last ")
    assert line["evidence"]["examples"] and line["warnings"]["sensitive"] == []
    env = got["friction:zsh-nomatch:all:user:environment"]
    assert env["kind"] == "environment" and env["target_path"] is None and "NO_NOMATCH" in env["text"]
    assert not (home / ".claude" / "CLAUDE.md").exists()  # nothing is written without approval
    assert suggest.count_unseen(conn) == 2
    assert suggest.mark_seen(conn) == 2 and suggest.count_unseen(conn) == 0
    assert suggest.counts(conn) == {"new": 2, "applied": 0, "dismissed": 0, "stale": 0, "done": 0}


def test_codex_causes_go_to_codex_and_one_project_causes_to_the_project(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    _spread(a, "edit-stale-context", agent="codex")
    p = a.project("solo")
    for i in range(3):
        a.session(f"cwd{i}", p)
        a.cause(f"cwd{i}", "cwd-drift", ago(1))
    suggest.refresh(conn, cfg)
    got = _by_key(conn)
    assert got["friction:edit-stale-context:codex:user"]["target_path"] == str(home / ".codex" / "AGENTS.md")
    assert "friction:edit-stale-context:claude:user" not in got
    local = got[f"friction:cwd-drift:claude:{p}"]
    assert local["project_path"] == p and local["target_path"] == f"{p}/CLAUDE.md"


def test_refresh_never_resurrects_dismissed_keeps_edits_and_goes_stale(archive):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    _spread(a, "zsh-nomatch")
    _spread(a, "timeout-missing")
    suggest.refresh(conn, cfg)
    got = _by_key(conn)
    zsh, timeout_env = got["friction:zsh-nomatch:claude:user"], got["friction:timeout-missing:all:user:environment"]
    assert suggest.dismiss(conn, timeout_env["id"], "I use gtimeout")["ok"]
    assert suggest.edit_text(conn, zsh["id"], "Quote globs; this shell is zsh.")["ok"]
    assert suggest.refresh(conn, cfg) == {"new": 0, "updated": 0, "stale": 0}
    got = _by_key(conn)
    assert got["friction:timeout-missing:all:user:environment"]["status"] == "dismissed"
    assert got["friction:timeout-missing:all:user:environment"]["dismissed_reason"] == "I use gtimeout"
    assert got["friction:zsh-nomatch:claude:user"]["text"] == "Quote globs; this shell is zsh."

    conn.execute("UPDATE events SET ts = ? WHERE text LIKE '%no matches found%'", (ago(40),))  # it stopped happening
    report = suggest.refresh(conn, cfg)
    got = _by_key(conn)
    assert report["stale"] == 2 and got["friction:zsh-nomatch:claude:user"]["status"] == "stale"
    assert got["friction:timeout-missing:all:user:environment"]["status"] == "dismissed"

    conn.execute("UPDATE events SET ts = ? WHERE text LIKE '%no matches found%'", (ago(1),))  # and it is back
    assert suggest.refresh(conn, cfg)["new"] == 2
    back = _by_key(conn)["friction:zsh-nomatch:claude:user"]
    assert back["status"] == "new" and back["seen_at"] is None and back["text"] == "Quote globs; this shell is zsh."


def test_preview_apply_unapply_an_instruction(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    _spread(a, "zsh-nomatch")
    suggest.refresh(conn, cfg)
    s = _by_key(conn)["friction:zsh-nomatch:claude:user"]
    target = home / ".claude" / "CLAUDE.md"
    target.write_text("# My rules\n\n<!-- agent-ninja-START -->\n- ninja\n<!-- agent-ninja-END -->\n")
    before = target.read_text()

    pv = suggest.preview(conn, cfg, s["id"], text="Quote every glob, zsh is the shell. Mail me at ops@corp.example.jp")
    assert pv["ok"] and pv["path"] == str(target) and "+- Quote every glob, zsh is the shell." in pv["diff"]
    assert pv["warnings"]["sensitive"] == ["email: ops@corp.example.jp"]
    assert target.read_text() == before  # a preview writes nothing

    res = suggest.apply(conn, cfg, s["id"])
    assert res["ok"] and res["path"] == str(target) and f"+{BEGIN}" in res["diff"]
    text = target.read_text()
    assert text.startswith(before) and text.index("agent-ninja-END") < text.index(BEGIN)
    assert "<!-- interlatch:friction:zsh-nomatch -->" in text and s["key"] not in text  # the marker carries no path
    row = suggest.get(conn, s["id"])
    assert row["status"] == "applied" and row["applied_text"] == s["text"] and row["applied_at"]
    backups = list((cfg.home / "backups").glob("CLAUDE.md.*.bak"))
    assert len(backups) == 1 and backups[0].read_text() == before
    assert suggest.apply(conn, cfg, s["id"])["diff"] == ""  # applying twice changes nothing

    assert suggest.unapply(conn, cfg, s["id"])["ok"]
    assert target.read_text() == before
    assert suggest.get(conn, s["id"])["status"] == "new"

    edited = suggest.apply(conn, cfg, s["id"], text="Quote globs:\n zsh.")
    assert edited["ok"] and "- Quote globs: zsh. <!-- interlatch:" in target.read_text()
    assert suggest.get(conn, s["id"])["text"] == "Quote globs: zsh."


def test_applied_friction_tracks_sessions_since(archive):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    _spread(a, "zsh-nomatch", days=5)
    suggest.refresh(conn, cfg)
    s = _by_key(conn)["friction:zsh-nomatch:claude:user"]
    suggest.apply(conn, cfg, s["id"])
    conn.execute("UPDATE suggestions SET applied_at = ? WHERE id = ?", (ago(3), s["id"]))
    p = a.project("later")
    a.session("after", p, days=1)
    a.cause("after", "zsh-nomatch", ago(1))
    suggest.refresh(conn, cfg)
    row = suggest.get(conn, s["id"])
    assert row["status"] == "applied" and row["evidence"]["sessions_since_applied"] == 1


def test_config_change_to_claude_json(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    path = _playwright_json(home)
    _spread(a, "playwright-output-roots")
    _spread(a, "playwright-browser-in-use")
    suggest.refresh(conn, cfg)
    got = _by_key(conn)
    roots = got["friction:playwright-output-roots:claude:user:config"]
    assert roots["kind"] == "config" and roots["target_path"] == str(path)
    assert roots["text"].startswith('mcpServers.playwright.args += ["--isolated", "--output-dir", ')
    assert str(home) in roots["text"]  # an absolute directory: MCP args get no shell expansion

    before = path.read_text()
    pv = suggest.preview(conn, cfg, roots["id"])
    assert pv["ok"] and '+        "--isolated",' in pv["diff"] and path.read_text() == before
    res = suggest.apply(conn, cfg, roots["id"])
    assert res["ok"]
    data = json.loads(path.read_text())
    assert data["numStartups"] == 7 and data["mcpServers"]["chronicle"] == {"command": "chronicle", "args": ["mcp"]}
    args = data["mcpServers"]["playwright"]["args"]
    assert args[:2] == ["-y", "@playwright/mcp@latest"] and args[2:4] == ["--isolated", "--output-dir"]
    assert [b.read_text() for b in (cfg.home / "backups").glob(".claude.json.*.bak")] == [before]

    # --isolated is there now, so the other cause's config change is satisfied and goes stale
    suggest.refresh(conn, cfg)
    assert _by_key(conn)["friction:playwright-browser-in-use:claude:user:config"]["status"] == "stale"
    assert suggest.unapply(conn, cfg, roots["id"])["ok"]
    assert json.loads(path.read_text())["mcpServers"]["playwright"]["args"] == ["-y", "@playwright/mcp@latest"]


def test_no_config_suggestion_without_a_playwright_server(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    _spread(a, "playwright-browser-in-use")
    suggest.refresh(conn, cfg)
    assert not [k for k in _by_key(conn) if k.endswith(":config")]
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {}}))
    suggest.refresh(conn, cfg)
    assert not [k for k in _by_key(conn) if k.endswith(":config")]


def test_missing_args_keeps_flags_already_set():
    assert suggest.missing_args(["--output-dir", "/x"], ["--isolated", "--output-dir", "/y"]) == ["--isolated"]
    assert suggest.parse_config_change('mcpServers.playwright.args += ["--isolated"]') == ("playwright", ["--isolated"])


def test_environment_steps_are_never_run(archive):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    _spread(a, "timeout-missing")
    suggest.refresh(conn, cfg)
    env = _by_key(conn)["friction:timeout-missing:all:user:environment"]
    assert "brew install coreutils" in env["text"]
    pv = suggest.preview(conn, cfg, env["id"])
    assert pv["command"] == env["text"] and pv["diff"] == ""
    assert not suggest.apply(conn, cfg, env["id"])["ok"]
    assert suggest.mark_done(conn, env["id"])["ok"]
    assert suggest.get(conn, env["id"])["status"] == "done"
    assert suggest.refresh(conn, cfg)["new"] == 0 and suggest.get(conn, env["id"])["status"] == "done"
    instruction = _by_key(conn)["friction:timeout-missing:claude:user"]
    assert not suggest.mark_done(conn, instruction["id"])["ok"]


def test_knowledge_candidates_join_the_queue(archive):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    p = a.project("app")
    a.session("k", p)
    conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, confidence, source, "
                 "fingerprint, created_at, updated_at, stage) VALUES ('k', ?, 'app', 'gotcha', "
                 "'Never run migrations against staging', 'Staging is shared.', 'high', 'analysis', 'fp', ?, ?, "
                 "'provisional')", (p, ago(1), ago(1)))
    suggest.refresh(conn, cfg)
    (s,) = suggest.list_suggestions(conn, project=p)
    assert s["origin"] == "knowledge" and s["key"].startswith("knowledge:") and s["target_path"] == f"{p}/CLAUDE.md"
    res = suggest.apply(conn, cfg, s["id"])
    assert res["ok"] and "**Never run migrations against staging**: Staging is shared." in (a.root / "projects" / "app" /
                                                                                           "CLAUDE.md").read_text()
    # once the line is in the file, the next refresh keeps it applied rather than proposing it again
    suggest.refresh(conn, cfg)
    assert suggest.get(conn, s["id"])["status"] == "applied"


def test_after_sync_respects_settings(archive, monkeypatch):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    _spread(a, "zsh-nomatch")
    posted = []
    monkeypatch.setattr("interlatch.notify.post", lambda title, msg: posted.append((title, msg)) or True)
    cfg.suggestions_enabled = False
    assert suggest.after_sync(conn, cfg) is None and suggest.counts(conn)["new"] == 0
    cfg.suggestions_enabled, cfg.suggestions_notify = True, True
    assert suggest.after_sync(conn, cfg)["new"] == 2
    assert posted == [("Interlatch", "2 new suggestions. Open the dashboard > Suggestions")]
    assert suggest.after_sync(conn, cfg)["new"] == 0 and len(posted) == 1


def _lesson(conn, sid, project, title, days):
    conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, confidence, source, "
                 "fingerprint, status, created_at, updated_at, stage) VALUES (?, ?, 'app', 'gotcha', ?, 'Staging is shared.', "
                 "'high', 'analysis', ?, 'active', ?, ?, 'provisional')", (sid, project, title, f"fp-{sid}", ago(days), ago(days)))


@pytest.mark.parametrize("decision", ["dismiss", "apply"])
def test_a_relearned_lesson_is_not_proposed_again(archive, decision):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    p = a.project("app")
    a.session("k1", p)
    a.session("k2", p)
    _lesson(conn, "k1", p, "Never run migrations against the shared staging database", 30)
    suggest.refresh(conn, cfg)
    (s,) = suggest.list_suggestions(conn)
    assert (suggest.dismiss(conn, s["id"]) if decision == "dismiss" else suggest.apply(conn, cfg, s["id"]))["ok"]
    _lesson(conn, "k2", p, "Never run migrations against the shared staging DB", 1)  # learned again, scoring higher
    suggest.refresh(conn, cfg)
    assert [x["id"] for x in suggest.list_suggestions(conn)] == [s["id"]]
    conn.execute("UPDATE knowledge SET status = 'superseded' WHERE session_id = 'k1'")  # the first copy goes away
    suggest.refresh(conn, cfg)
    assert [x["id"] for x in suggest.list_suggestions(conn)] == [s["id"]]
    assert suggest.get(conn, s["id"])["status"] == ("dismissed" if decision == "dismiss" else "applied")
    claude_md = a.root / "projects" / "app" / "CLAUDE.md"
    assert (claude_md.read_text() if claude_md.exists() else "").count("<!-- interlatch:") == (decision == "apply")


def test_project_lines_carry_no_path_and_survive_spaces(archive):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    p = a.project("My Projects app")
    for i in range(3):
        a.session(f"cwd{i}", p)
        a.cause(f"cwd{i}", "cwd-drift", ago(1))
    suggest.refresh(conn, cfg)
    s = _by_key(conn)[f"friction:cwd-drift:claude:{p}"]
    target = a.root / "projects" / "My Projects app" / "CLAUDE.md"
    assert suggest.apply(conn, cfg, s["id"])["ok"]
    text = target.read_text()
    assert "<!-- interlatch:friction:cwd-drift -->" in text and str(a.root) not in text
    assert suggest.apply(conn, cfg, s["id"], text="Edited: cd with absolute paths.")["ok"]
    assert target.read_text().count("<!-- interlatch:") == 1
    assert suggest.unapply(conn, cfg, s["id"])["ok"] and not target.exists()  # Chronicle made it, so it goes


def test_undoing_a_config_change_keeps_flags_you_had(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    path = _playwright_json(home, ["-y", "@playwright/mcp@latest", "--isolated"])
    _spread(a, "playwright-output-roots")
    suggest.refresh(conn, cfg)
    roots = _by_key(conn)["friction:playwright-output-roots:claude:user:config"]
    res = suggest.apply(conn, cfg, roots["id"])
    assert res["ok"] and res["applied_text"].startswith('mcpServers.playwright.args += ["--output-dir", ')
    assert json.loads(path.read_text())["mcpServers"]["playwright"]["args"][2:4] == ["--isolated", "--output-dir"]
    assert suggest.unapply(conn, cfg, roots["id"])["ok"]
    assert json.loads(path.read_text())["mcpServers"]["playwright"]["args"] == ["-y", "@playwright/mcp@latest", "--isolated"]


def test_refresh_never_moves_an_applied_line(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    _spread(a, "zsh-nomatch")
    suggest.refresh(conn, cfg)
    s = _by_key(conn)["friction:zsh-nomatch:claude:user"]
    target = home / ".claude" / "CLAUDE.md"
    assert suggest.apply(conn, cfg, s["id"])["ok"] and "interlatch:" in target.read_text()
    cfg.claude_dirs = [home / "elsewhere"]  # another process routes user-level lines somewhere else
    suggest.refresh(conn, cfg)
    assert suggest.get(conn, s["id"])["target_path"] == str(target)
    assert suggest.unapply(conn, cfg, s["id"])["ok"] and target.read_text() == ""


def test_apply_refuses_a_project_that_is_gone(archive):
    import shutil

    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    p = a.project("solo")
    for i in range(3):
        a.session(f"cwd{i}", p)
        a.cause(f"cwd{i}", "cwd-drift", ago(1))
    suggest.refresh(conn, cfg)
    s = _by_key(conn)[f"friction:cwd-drift:claude:{p}"]
    shutil.rmtree(p)
    res = suggest.apply(conn, cfg, s["id"])
    assert not res["ok"] and "no longer exists" in res["error"]
    assert not suggest.preview(conn, cfg, s["id"])["ok"]
    from pathlib import Path

    assert not Path(p).exists() and suggest.get(conn, s["id"])["status"] == "new"


def test_apply_refuses_a_damaged_block(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    _spread(a, "zsh-nomatch")
    suggest.refresh(conn, cfg)
    s = _by_key(conn)["friction:zsh-nomatch:claude:user"]
    target = home / ".claude" / "CLAUDE.md"
    damaged = f"# Mine\n{BEGIN}\n- stray\n\n## Keep\n"
    target.write_text(damaged)
    res = suggest.apply(conn, cfg, s["id"])
    assert not res["ok"] and "malformed interlatch block" in res["error"] and str(target) in res["error"]
    assert target.read_text() == damaged and suggest.get(conn, s["id"])["status"] == "new"


def test_undo_deletes_a_file_chronicle_created_but_keeps_yours(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    _spread(a, "zsh-nomatch")
    suggest.refresh(conn, cfg)
    s = _by_key(conn)["friction:zsh-nomatch:claude:user"]
    target = home / ".claude" / "CLAUDE.md"
    assert not target.exists()
    assert suggest.apply(conn, cfg, s["id"])["ok"] and target.exists()
    assert suggest.apply(conn, cfg, s["id"])["ok"]  # applying again must not forget that Chronicle made the file
    assert suggest.unapply(conn, cfg, s["id"])["ok"]
    assert not target.exists()  # no empty file left behind

    target.write_text("# Mine\n")
    assert suggest.apply(conn, cfg, s["id"])["ok"] and suggest.unapply(conn, cfg, s["id"])["ok"]
    assert target.read_text() == "# Mine\n"


def test_an_applied_suggestion_must_be_undone_before_dismissing(archive):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    _spread(a, "zsh-nomatch")
    suggest.refresh(conn, cfg)
    s = _by_key(conn)["friction:zsh-nomatch:claude:user"]
    suggest.apply(conn, cfg, s["id"])
    res = suggest.dismiss(conn, s["id"])
    assert not res["ok"] and "undo" in res["error"]
    assert suggest.get(conn, s["id"])["status"] == "applied"


@pytest.mark.parametrize("text", [
    'mcpServers.chronicle.args += ["--isolated"]',                       # not the Playwright server
    'mcpServers.playwright.args += ["--allow-unrestricted-file-access"]',  # a flag Chronicle never proposes
    'mcpServers.playwright.args += ["--output-dir", "relative/dir"]',     # not an absolute path
    'mcpServers.playwright.args += ["--isolated", "--output-dir"]',       # missing its value
])
def test_an_edited_config_change_cannot_widen_what_is_written(archive, text):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    path = _playwright_json(home)
    _spread(a, "playwright-output-roots")
    suggest.refresh(conn, cfg)
    s = _by_key(conn)["friction:playwright-output-roots:claude:user:config"]
    before = path.read_text()
    res = suggest.apply(conn, cfg, s["id"], text=text)
    assert not res["ok"] and path.read_text() == before


def _know(conn, sid, project, title, *, kind="gotcha", scope="project", stage="provisional"):
    conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, confidence, "
                 "source, fingerprint, status, created_at, updated_at, stage) VALUES (?, ?, 'app', ?, ?, "
                 "'Keeps the work predictable.', ?, 'high', 'analysis', ?, 'active', ?, ?, ?)",
                 (sid, project, kind, title, scope, f"fp-{sid}-{title}", ago(1), ago(1), stage))


def test_a_preference_about_you_goes_to_the_user_level_file(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    p = a.project("app")
    a.session("k", p)
    _know(conn, "k", p, "Never profile unrelated running processes", kind="preference", scope="global")
    _know(conn, "k", p, "Never deploy the billing worker on Fridays", scope="global")  # useful elsewhere, not about you
    _know(conn, "k", p, "Always answer in British English", kind="preference")  # the analysis tied it to this project
    suggest.refresh(conn, cfg)
    assert {s["title"]: s["target_path"] for s in suggest.list_suggestions(conn)} == {
        "Never profile unrelated running processes": str(home / ".claude" / "CLAUDE.md"),
        "Never deploy the billing worker on Fridays": f"{p}/CLAUDE.md",
        "Always answer in British English": f"{p}/CLAUDE.md"}


def test_a_line_you_move_stays_moved_and_takes_its_lesson_along(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    p, q = a.project("app"), a.project("web")
    a.session("k1", p)
    a.session("k2", q)
    for sid, project in (("k1", p), ("k2", q)):  # one lesson, learned in two projects: a line in each
        _know(conn, sid, project, "Prefer small focused commits with clear messages", kind="preference")
    suggest.refresh(conn, cfg)
    cards = suggest.list_suggestions(conn)
    assert sorted(s["project_path"] for s in cards) == sorted([p, q])
    assert suggest.edit_text(conn, cards[0]["id"], "Small, focused commits.")["ok"]
    got = suggest.move(conn, cfg, cards[0]["id"], "user")
    assert got == {"ok": True, "moved": 1, "targets": [str(home / ".claude" / "CLAUDE.md")]}
    (card,) = suggest.list_suggestions(conn)  # both project cards gone, not stale
    assert card["project_path"] is None and card["status"] == "new" and card["text"] == "Small, focused commits."
    assert suggest.refresh(conn, cfg) == {"new": 0, "updated": 0, "stale": 0}
    assert [s["id"] for s in suggest.list_suggestions(conn)] == [card["id"]]
    assert suggest.move(conn, cfg, card["id"], "user") == {"ok": False, "error": "it is already there"}

    assert suggest.move(conn, cfg, card["id"], "project")["moved"] == 2
    back = suggest.list_suggestions(conn)
    assert sorted(s["project_path"] for s in back) == sorted([p, q]) and suggest.refresh(conn, cfg)["stale"] == 0
    assert suggest.apply(conn, cfg, back[0]["id"])["ok"]
    assert not suggest.move(conn, cfg, back[0]["id"], "user")["ok"]  # an applied line stays where it was written


def test_moving_a_line_for_a_recurring_failure(archive):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    p = a.project("solo")
    for i in range(3):
        a.session(f"cwd{i}", p)
        a.cause(f"cwd{i}", "cwd-drift", ago(1))
    suggest.refresh(conn, cfg)
    local = _by_key(conn)[f"friction:cwd-drift:claude:{p}"]
    assert suggest.move(conn, cfg, local["id"], "user")["moved"] == 1
    suggest.refresh(conn, cfg)
    got = _by_key(conn)
    assert got["friction:cwd-drift:claude:user"]["status"] == "new" and f"friction:cwd-drift:claude:{p}" not in got


def test_lines_past_the_limit_wait_their_turn_without_going_stale(archive):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    p = a.project("app")
    a.session("k", p)
    for title in ("Build the docs site with mkdocs build --strict", "Regenerate protobuf stubs with buf generate",
                  "Seed the local database using make seed", "Lint stylesheets with stylelint",
                  "Rotate the signing key with vault write transit", "Preview emails through mailpit on port 8025",
                  "Profile slow queries with EXPLAIN ANALYZE", "Package the chart with helm package",
                  "Export translations via lingui extract"):
        _know(conn, "k", p, title, kind="command")
    suggest.refresh(conn, cfg)
    shown = suggest.list_suggestions(conn)
    assert len(shown) == instructions.MAX_PER_FILE
    _know(conn, "k", p, "Reset the emulator with adb emu kill", kind="command", stage="established")  # outranks one
    suggest.refresh(conn, cfg)
    assert len(suggest.list_suggestions(conn)) == instructions.MAX_PER_FILE and suggest.counts(conn)["stale"] == 0
    # what you dismiss stops taking a place, so one that was waiting comes up
    before = {s["title"] for s in suggest.list_suggestions(conn)}
    assert suggest.dismiss(conn, shown[0]["id"])["ok"]
    suggest.refresh(conn, cfg)
    now = suggest.list_suggestions(conn, status="new")
    assert len(now) == instructions.MAX_PER_FILE and {s["title"] for s in now} - before
