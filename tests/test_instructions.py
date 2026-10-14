"""Instruction files: Chronicle's managed block, routing, overlap and sensitivity checks, and knowledge candidates."""

import subprocess

import pytest

from interlatch import instructions as ins
from interlatch.instructions import BEGIN, END

from friction_fixture import ago, make_archive


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    yield from make_archive(tmp_path, monkeypatch)

NINJA = "<!-- agent-ninja-START -->\n- ninja rule\n<!-- agent-ninja-END -->\n"


def test_render_creates_a_block_in_an_empty_file():
    out = ins.render_file("", [("k1", "Quote every glob.")])
    assert out == f"{BEGIN}\n- Quote every glob. <!-- interlatch:k1 -->\n{END}\n"


def test_render_appends_after_existing_text_and_leaves_it_alone():
    old = "# Project\n\nKeep tests hermetic.\n"
    out = ins.render_file(old, [("k1", "Rule one.")])
    assert out.startswith(old) and out.endswith(f"\n\n{BEGIN}\n- Rule one. <!-- interlatch:k1 -->\n{END}\n")


def test_render_rewrites_an_existing_block_in_place():
    old = f"# A\n\n{BEGIN}\n- old <!-- interlatch:k1 -->\n{END}\n\n## Later section\ntext\n"
    out = ins.render_file(old, [("k1", "new"), ("k2", "two")])
    assert out == f"# A\n\n{BEGIN}\n- new <!-- interlatch:k1 -->\n- two <!-- interlatch:k2 -->\n{END}\n\n## Later section\ntext\n"


def test_block_goes_after_another_tools_block_and_never_inside_it():
    old = "# Rules\n\n" + NINJA
    out = ins.render_file(old, [("k", "x")])
    assert out.index("agent-ninja-END") < out.index(BEGIN)
    assert out.startswith(old)
    stranded = f"<!-- agent-ninja-START -->\n- ninja\n{BEGIN}\n- x <!-- interlatch:k -->\n{END}\n<!-- agent-ninja-END -->\n"
    moved = ins.render_file(stranded, [("k", "y")])
    assert moved == "<!-- agent-ninja-START -->\n- ninja\n<!-- agent-ninja-END -->\n\n" \
                    f"{BEGIN}\n- y <!-- interlatch:k -->\n{END}\n"


def test_merge_is_idempotent_and_removing_the_last_line_removes_the_block():
    old = "# Project\n\n" + NINJA
    once = ins.merge(old, [("a", "Rule A."), ("b", "Rule B.")])
    assert ins.merge(once, [("a", "Rule A."), ("b", "Rule B.")]) == once
    assert ins.parse_block(once)[0] == [("a", "Rule A."), ("b", "Rule B.")]
    edited = ins.merge(once, [("a", "Rule A, edited.")])
    assert ins.parse_block(edited)[0] == [("a", "Rule A, edited."), ("b", "Rule B.")]
    one_left = ins.merge(edited, [], {"a"})
    assert ins.parse_block(one_left)[0] == [("b", "Rule B.")]
    assert ins.merge(one_left, [], {"b"}) == old
    assert ins.merge(ins.merge("", [("a", "x")]), [], {"a"}) == ""


def test_lines_people_typed_into_the_block_are_kept():
    old = f"{BEGIN}\n- mine, no marker\n- x <!-- interlatch:k -->\n{END}\n"
    out = ins.merge(old, [], {"k"})
    assert out == f"{BEGIN}\n- mine, no marker\n{END}\n"


def test_keys_with_double_dashes_and_markup_in_text_survive():
    out = ins.render_file("", [("knowledge:1:/p/my--repo/CLAUDE.md", "Use <!-- this --> carefully")])
    assert "--repo" not in out.split("interlatch:", 1)[1].split(" -->")[0]
    assert ins.parse_block(out)[0] == [("knowledge:1:/p/my--repo/CLAUDE.md", "Use this carefully")]


def test_write_lines_creates_backs_up_and_replaces_atomically(tmp_path):
    from interlatch.core.config import load_config

    cfg = load_config(tmp_path / "chronicle")
    target = tmp_path / "proj" / "CLAUDE.md"
    ins.write_lines(target, [("a", "Rule A.")], cfg=cfg)
    assert ins.read_block(target) == [("a", "Rule A.")]
    assert not (cfg.home / "backups").exists()  # nothing to back up the first time
    target.write_text("# Mine\n" + target.read_text())
    ins.write_lines(target, [("b", "Rule B.")], cfg=cfg)
    assert target.read_text().startswith("# Mine\n")
    backups = list((cfg.home / "backups").glob("CLAUDE.md.*.bak"))
    assert len(backups) == 1 and "# Mine" in backups[0].read_text() and "Rule B" not in backups[0].read_text()
    assert [p.name for p in target.parent.iterdir()] == ["CLAUDE.md"]  # no temp file left behind
    ins.write_lines(target, [], {"a", "b"}, cfg=cfg)
    assert target.read_text() == "# Mine\n"


def test_overlap_ignores_our_own_block():
    existing = ("# Rules\n- Always quote glob arguments in zsh: `grep --include='*.py'` or use rg\n\n"
                f"{BEGIN}\n- Use uv run with packages for throwaway python scripts <!-- interlatch:k -->\n{END}\n")
    assert ins.overlap("Always quote glob arguments in zsh (`grep --include='*.py'`), or use rg", existing)
    assert not ins.overlap("Use uv run with packages for throwaway python scripts", existing)
    assert not ins.overlap("Restart the backend after editing .env", existing)


def test_sensitive_hits():
    hits = ins.sensitive("ssh 192.168.1.20 then open app-prod.azurewebsites.net as ops@corp.example.jp, "
                         "sub 123e4567-e89b-12d3-a456-426614174000, key in /Users/alice/.ssh, api_key=abcd1234efgh")  # gitleaks:allow (a fake key the test needs)
    kinds = {h.split(":")[0] for h in hits}
    assert kinds == {"private-ip", "host", "email", "guid", "home-path", "token"}
    assert ins.sensitive("Run `pip install xlsx@0.20.3`, see github.com/x/y and docs.python.org; edit config.toml") == []


def test_target_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    home = tmp_path / "home"
    assert ins.target_for("claude", "user", None) == home / ".claude" / "CLAUDE.md"
    assert ins.target_for("codex", "user", None) == home / ".codex" / "AGENTS.md"
    assert ins.target_for("copilot", "user", None) is None and ins.target_for("bob", "user", None) is None
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    assert ins.target_for("claude", "user", None) == tmp_path / "cc" / "CLAUDE.md"
    assert ins.target_for("codex", "user", None) == tmp_path / "cx" / "AGENTS.md"

    proj = tmp_path / "proj"
    assert ins.target_for("claude", "project", str(proj)) is None  # never create a project directory
    proj.mkdir()
    assert ins.target_for("claude", "project", str(proj)) == proj / "CLAUDE.md"
    for agent in ("codex", "copilot", "bob"):
        assert ins.target_for(agent, "project", str(proj)) == proj / "AGENTS.md"
    (proj / "AGENTS.md").write_text("Read CLAUDE.md first; it holds every rule.\n")
    assert ins.target_for("codex", "project", str(proj)) == proj / "CLAUDE.md"
    assert ins.target_for("claude", "project", str(proj)) == proj / "CLAUDE.md"
    other = tmp_path / "other"
    other.mkdir()
    (other / "CLAUDE.md").write_text("@AGENTS.md\n")
    assert ins.target_for("claude", "project", str(other)) == other / "AGENTS.md"


def test_first_sentence_and_line_text():
    assert ins.first_sentence("When debugging (e.g. a slow model), do not run ps. Second.") == \
        "When debugging (e.g. a slow model), do not run ps."
    line = ins.line_text("Never stash in a shared tree", "Use a worktree instead.\nMore detail.")
    assert line == "**Never stash in a shared tree**: Use a worktree instead."
    assert len(ins.line_text("t" * 50, "word " * 200)) <= ins.LINE_CHARS


def test_changelog_titles_are_not_rules():
    assert ins.is_changelog("Fixed the login redirect")
    assert ins.is_changelog("Map search now returns all matches")
    assert ins.is_changelog("ADR-0081: Dashboard agents compose modals")
    assert not ins.is_changelog("Never deploy to the shared site without confirmation")
    assert ins.is_rule_like("Use uv run, not bare python")


def test_repo_visibility_without_a_remote(tmp_path, monkeypatch):
    monkeypatch.setattr("interlatch.instructions._VISIBILITY", {})
    plain = tmp_path / "plain"
    plain.mkdir()
    assert ins.repo_visibility(str(plain)) == "unknown"
    assert ins.repo_visibility(str(tmp_path / "missing")) == "unknown"
    repo = tmp_path / "repo"
    repo.mkdir()
    if subprocess.run(["git", "init", "-q", str(repo)], capture_output=True).returncode != 0:
        pytest.skip("git unavailable")
    assert ins.repo_visibility(str(repo)) == "private"
    (repo / "CLAUDE.md").write_text("x\n")
    assert ins.is_untracked(repo / "CLAUDE.md")
    assert not ins.is_untracked(repo / "AGENTS.md")  # a file that does not exist yet is just new


def _knowledge(conn, kid_session, project, title, body, *, kind="gotcha", confidence="high", source="analysis",
               stage="provisional", agent="claude"):
    from pathlib import Path

    cur = conn.execute(
        "INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, confidence, source, agent, "
        "fingerprint, status, created_at, updated_at, stage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (kid_session, project, Path(project).name if project else None, kind, title, body, confidence, source, agent,
         f"fp-{kid_session}-{title}", ago(2), ago(2), stage))
    return cur.lastrowid


def test_candidates_pick_rules_and_route_them(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    p = a.project("app")
    a.session("k1", p)
    a.session("k2", p)
    good = _knowledge(conn, "k1", p, "Never run migrations against the shared staging database",
                      "Staging is shared with QA. Use a local copy.")
    _knowledge(conn, "k1", p, "Fixed the login redirect loop", "It now works.", kind="fix")
    _knowledge(conn, "k1", p, "Interesting fact about Vite", "It is fast.")  # one session, not worded as a rule
    _knowledge(conn, "k1", p, "Never trust memory items", "From memory.", source="memory")
    _knowledge(conn, "k1", p, "Avoid low-confidence guesses", "Unsure.", confidence="low")
    _knowledge(conn, "k2", p, "How to run the app locally", "Run `make dev` from the repo root.", kind="command")
    out = ins.candidates(conn, cfg)
    by_title = {c["title"]: c for c in out}
    assert set(by_title) == {"Never run migrations against the shared staging database", "How to run the app locally"}
    c = by_title["Never run migrations against the shared staging database"]
    assert c["key"] == f"knowledge:{good}:{p}/CLAUDE.md" and c["target_path"] == f"{p}/CLAUDE.md"
    assert c["text"] == "**Never run migrations against the shared staging database**: Staging is shared with QA."
    assert c["origin"] == "knowledge" and c["kind"] == "instruction" and c["project_path"] == p
    assert c["warnings"] == {"sensitive": [], "public_repo": False, "untracked": False, "overlap": False}
    assert "confirmed in 1 session" in c["evidence"]["line"] and c["evidence"]["generated_text"] == c["text"]
    assert not (home / ".claude" / "CLAUDE.md").exists()  # nothing is written by proposing


def test_candidates_skip_what_the_file_already_says_and_cap_per_file(archive):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    p = a.project("app")
    a.session("s", p)
    (archive["tmp"] / "projects" / "app" / "CLAUDE.md").write_text(
        "- Never run migrations against the shared staging database: staging is shared with QA\n")
    _knowledge(conn, "s", p, "Never run migrations against the shared staging database", "Staging is shared with QA.")
    topics = ["schema", "lockfile", "fixtures", "vendored", "snapshots", "migrations-dir", "assets", "bundle", "openapi",
              "protobuf", "translations", "changelog"]
    for i, topic in enumerate(topics):
        _knowledge(conn, "s", p, f"Never hand-edit {topic}", f"Regenerate the {topic} with make {i}.")
    out = ins.candidates(conn, cfg)
    assert len(out) == ins.MAX_PER_FILE
    assert all("staging" not in c["title"] for c in out)


def test_cross_project_lessons_become_one_user_level_line_per_agent(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    for i, agent in enumerate(("claude", "codex", "claude")):
        p = a.project(f"repo{i}")
        a.session(f"x{i}", p, agent=agent)
        _knowledge(conn, f"x{i}", p, "Never kill launchd KeepAlive services with plain kill",
                   "Use launchctl kickstart -k instead.", agent=agent)
    out = ins.candidates(conn, cfg)
    assert {(c["agent"], c["target_path"], c["project_path"]) for c in out} == {
        ("claude", str(home / ".claude" / "CLAUDE.md"), None), ("codex", str(home / ".codex" / "AGENTS.md"), None)}
    assert all(c["evidence"]["sessions"] == 3 and c["evidence"]["projects"] == 3 for c in out)


@pytest.mark.parametrize("damaged", [
    f"# A\n\n{BEGIN}\n- stray\n\n## User section\n\nkeep me\n\n" + NINJA,  # an orphan BEGIN
    f"# A\n- stray\n{END}\n",  # an orphan END
    f"# A\n{END}\n- x <!-- interlatch:k -->\n{BEGIN}\n",  # END before BEGIN
    f"{BEGIN}\n- x <!-- interlatch:k -->\n{END}\n\n# Mine\n\n{BEGIN}\n- y <!-- interlatch:j -->\n{END}\n",  # two blocks
])
def test_a_damaged_block_is_refused_not_guessed(tmp_path, damaged):
    from interlatch.core.config import load_config

    with pytest.raises(ins.MalformedBlock):
        ins.merge(damaged, [("k1", "Rule.")])
    with pytest.raises(ins.MalformedBlock):
        ins.merge(damaged, [], {"k", "j"})
    target = tmp_path / "CLAUDE.md"
    target.write_text(damaged)
    with pytest.raises(ins.MalformedBlock, match=r"CLAUDE\.md; fix it by hand"):
        ins.write_lines(target, [("k1", "Rule.")], cfg=load_config(tmp_path / "chronicle"))
    assert target.read_text() == damaged
    assert ins.overlap("keep me", damaged) in (True, False)  # proposing never raises on a damaged file


def test_keys_with_spaces_round_trip():
    key = "friction:cwd-drift:claude:/Users/a/My Projects/app"
    out = ins.render_file("# Mine\n", [(key, "Use absolute paths.")])
    assert ins.parse_block(out) == ([(key, "Use absolute paths.")], [])
    assert ins.merge(out, [(key, "Edited.")]).count("<!-- interlatch:") == 1
    assert ins.merge(out, [], {key}) == "# Mine\n"


def test_writing_through_a_symlink_keeps_the_link(tmp_path):
    from interlatch.core.config import load_config

    real = tmp_path / "dotfiles" / "CLAUDE.md"
    real.parent.mkdir()
    real.write_text("# Mine\n")
    link = tmp_path / "claude" / "CLAUDE.md"
    link.parent.mkdir()
    link.symlink_to(real)
    ins.write_lines(link, [("k", "Rule.")], cfg=load_config(tmp_path / "chronicle"))
    assert link.is_symlink() and ins.read_block(real) == [("k", "Rule.")]
    assert sorted(p.name for p in link.parent.iterdir()) == ["CLAUDE.md"]  # no temp file next to the link


def test_write_atomic_never_recreates_a_missing_directory(tmp_path):
    from interlatch.core.config import load_config

    with pytest.raises(FileNotFoundError):
        ins.write_atomic(tmp_path / "gone" / "CLAUDE.md", "x\n", cfg=load_config(tmp_path / "chronicle"), mkdir=False)
    assert not (tmp_path / "gone").exists()


def test_user_level_routing_follows_chronicle_settings_over_the_shell(tmp_path, monkeypatch):
    from interlatch.core.config import load_config

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "from-shell"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-shell"))
    cfg = load_config(tmp_path / "chronicle")
    cfg.claude_dirs, cfg.codex_dirs = [tmp_path / "configured"], [tmp_path / "codex-configured"]
    assert ins.target_for("claude", "user", None, cfg) == tmp_path / "configured" / "CLAUDE.md"
    assert ins.target_for("codex", "user", None, cfg) == tmp_path / "codex-configured" / "AGENTS.md"
    cfg.codex_dirs = []
    assert ins.target_for("codex", "user", None, cfg) == tmp_path / "codex-shell" / "AGENTS.md"


def test_a_relearned_lesson_keeps_its_key(archive):
    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    p = a.project("app")
    a.session("k1", p)
    a.session("k2", p)
    first = _knowledge(conn, "k1", p, "Never run migrations against the shared staging database", "Staging is shared.")
    conn.execute("UPDATE knowledge SET created_at = ?, updated_at = ? WHERE id = ?", (ago(30), ago(30), first))
    (before,) = ins.candidates(conn, cfg)
    _knowledge(conn, "k2", p, "Never run migrations against the shared staging DB", "Staging is shared with QA.")
    (after,) = ins.candidates(conn, cfg)
    assert before["key"] == after["key"] == f"knowledge:{first}:{p}/CLAUDE.md" and after["knowledge_id"] == first
    assert after["evidence"]["sessions"] == 2


def test_crlf_files_keep_their_line_endings(tmp_path):
    from interlatch.core.config import load_config

    cfg = load_config(tmp_path / "chronicle")
    target = tmp_path / "proj" / "AGENTS.md"
    target.parent.mkdir()
    target.write_bytes(b"# Rules\r\n\r\n- mine\r\n")
    ins.write_lines(target, [("a", "Rule A.")], cfg=cfg)
    raw = target.read_bytes()
    assert raw.startswith(b"# Rules\r\n\r\n- mine\r\n") and b"Rule A." in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")  # every line ending is still CRLF


def test_the_home_folder_is_never_a_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ins.target_for("claude", "project", str(tmp_path)) is None
    assert ins.target_for("claude", "project", str(tmp_path.parent)) is None
    (tmp_path / "proj").mkdir()
    assert ins.target_for("claude", "project", str(tmp_path / "proj")) == tmp_path / "proj" / "CLAUDE.md"


def test_script_names_are_not_hosts():
    assert ins.sensitive("Run ./build.sh then deploy.sh") == []
    assert ins.sensitive("curl https://get.example.sh") == ["host: get.example.sh"]
    assert ins.sensitive("ssh build.internal.corp") == ["host: build.internal.corp"]
