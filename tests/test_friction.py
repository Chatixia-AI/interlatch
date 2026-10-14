"""What goes wrong: the friction catalog, noise separation, dedupe and the cause scan."""

import pytest

from interlatch import friction

from friction_fixture import ERRORS, ago, make_archive


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    yield from make_archive(tmp_path, monkeypatch)


@pytest.mark.parametrize("cause", sorted(c for c in ERRORS if c != "auto-mode-retry"))
def test_catalog_matches_real_error_strings(cause):
    tool, text, command = ERRORS[cause]
    assert cause in friction.match_error(text, tool, command)


def test_classifier_denial_is_recognized_but_only_its_retry_is_waste(archive):
    tool, text, command = ERRORS["auto-mode-retry"]
    assert friction.match_error(text, tool, command) == ["auto-mode-retry"]
    a = archive["a"]
    p = a.project("app")
    a.session("once", p)
    a.cause("once", "auto-mode-retry", ago(1))
    a.result("once", ago(1, -1), "Bash", "ok", is_error=0)
    a.session("twice", p)
    a.cause("twice", "auto-mode-retry", ago(1))
    a.cause("twice", "auto-mode-retry", ago(1, -1))
    causes = {c["id"]: c for c in friction.scan(archive["conn"])}
    assert causes["auto-mode-retry"]["sessions"] == 1
    assert causes["auto-mode-retry"]["immediate_repeats"] == 1


def test_more_real_strings():
    m = friction.match_error
    assert "edit-stale-context" in m("<tool_use_error>String to replace not found in file.\nString: from app import x", "Edit")
    assert "edit-stale-context" in m("<tool_use_error>File has been modified since read, either by the user or by a "
                                     "linter. Read it again before attempting to write it.</tool_use_error>", "Write")
    assert "playwright-stale-refs" in m("Error: locator.click: Error: strict mode violation: getByRole('button', "
                                        "{ name: '1×' }) resolved to 2 elements", "mcp__playwright__browser_run_code_unsafe")
    assert "playwright-output-roots" in m("Error: page.goto: Access to file: URLs is blocked", "mcp__playwright__browser_navigate")
    assert "playwright-output-roots" in m("File does not exist.", "Read", "/Users/t/p/.playwright-mcp/shot.png")
    assert "timeout-missing" in m("Exit code 127\n", "Bash", "cd x && timeout 30 pytest")
    assert "zsh-equals" in m("Exit code 1\nfoo\n(eval):1: === not found", "Bash", "cat a; echo ===; cat b")
    assert "stale-dev-server" in m("Error: listen EADDRINUSE: address already in use 127.0.0.1:5173", "Bash", "pnpm dev")
    # the Playwright signature in some other tool's output is not a Playwright failure
    assert m("Exit code 1\n... is outside allowed roots ...", "Bash", "grep -rn 'outside allowed roots' notes/") == []
    # a missing module inside the project's own test run is the dev loop, not ad-hoc system Python
    assert m("E   ModuleNotFoundError: No module named 'app'", "Bash", "uv run pytest -q") == ["expected-test-failures"]


def test_noise_is_separated_and_waste_wins():
    m = friction.match_error
    assert m("Exit code 1\nFAILED tests/test_x.py::test_a - assert 1 == 2\n1 failed, 3 passed", "Bash",
             "uv run pytest -q") == ["expected-test-failures"]
    assert m("Exit code 2\nsrc/a.ts(3,1): error TS2345: Argument of type", "exec_command", "pnpm exec tsc --noEmit") == \
        ["expected-test-failures"]
    assert m("Exit code 1\n...file contents...", "Bash", "cat a.py; sed -n '1,20p' b.py; grep -n foo c.py") == \
        ["read-chain-nonzero"]
    assert m("The user doesn't want to proceed with this tool use. The tool use was rejected", "Bash", "rm -rf x") == \
        ["user-rejected"]
    assert m("API Error: 529 {\"type\":\"overloaded_error\"}", "Bash") == ["provider-platform"]
    # a read chain that fails on zsh globbing is the zsh cause, not noise
    assert m("Exit code 1\n(eval):1: no matches found: out*.png", "Bash", "ls out*.png; cat a") == ["zsh-nomatch"]
    assert m("Exit code 1\nsomething odd", "Bash", "python3 build.py") == []


def test_read_chain_shape():
    assert friction.is_read_chain("cd /x && cat a.py | head -20; grep -n foo b.py")
    assert friction.is_read_chain("git status --short && git diff --stat")
    assert not friction.is_read_chain("cat a.py")  # a single command's exit status means something
    assert not friction.is_read_chain("sed -i '' 's/a/b/' x; cat x")
    assert not friction.is_read_chain("python3 x.py; cat y")
    assert not friction.is_read_chain("cat a > b; ls")
    assert not friction.is_read_chain("git commit -m x && git log -1")


def test_normalize_collapses_the_variable_parts():
    text = ("Script completed\nWall time 0.4 seconds\nOutput:\n\nExit code: 1\nFile /private/tmp/claude-501/-Users-a-p/"
            "0f8c1a2b-1111-2222-3333-444455556666/scratchpad/x.png not under /Users/adrian/Projects/demo/src at line 42")
    out = friction.normalize(text, "/Users/adrian/Projects/demo")
    assert out == "file <scratchpad>/x.png not under <project>/src at line n"
    assert friction.normalize("Port 8765 in /tmp/abc/x") == "port n in <tmp>"


def test_friction_notes_are_multi_label_and_bilingual():
    note = ("Multiple Bash command failures early on: `timeout` not found on macOS, `--include=*.ts` glob syntax not "
            "supported by the shell, and shell cwd being silently reset between calls")
    assert {"timeout-missing", "zsh-nomatch", "cwd-drift"} <= set(friction.match_note(note))
    ja = "grepコマンドで--include=*.mdオプションの引数展開がシェルにより失敗し(Exit code 1: no matches found)"
    assert "zsh-nomatch" in friction.match_note(ja)
    assert friction.match_note("Repeated 529 Overloaded errors blocked the session") == ["provider-platform"]
    assert friction.match_note("The developer changed their mind about the colour scheme") == []


def test_scan_dedupes_resumed_rows_and_skips_history(archive):
    a, conn = archive["a"], archive["conn"]
    p1, p2 = a.project("one"), a.project("two")
    ts = ago(2)
    a.session("s1", p1)
    tuid = a.cause("s1", "zsh-nomatch", ts)
    a.cause("s1", "zsh-nomatch", ts, tool_use_id=tuid, call=False)  # the same row again, from a resumed session
    a.session("s2", p2, agent="codex")
    a.cause("s2", "zsh-nomatch", ago(3))
    a.session("h", p2, source="history")
    a.cause("h", "zsh-nomatch", ago(3))
    c = {x["id"]: x for x in friction.scan(conn)}["zsh-nomatch"]
    assert (c["sessions"], c["occurrences"], c["errors"]) == (2, 2, 2)
    assert sorted(c["projects"]) == ["one", "two"] and sorted(c["agents"]) == ["claude", "codex"]
    assert c["still_happening"] and c["last_seen"] == ago(2)[:10]
    assert len(c["weekly"]) == friction.WEEKS and sum(w["sessions"] for w in c["weekly"]) == 2
    assert c["rate"] == 1.0  # every session in those projects hit it
    assert c["examples"][0]["session_id"] in ("s1", "s2") and "no matches found" in c["examples"][0]["note"]


def test_scan_counts_notes_and_errors_per_session(archive):
    a = archive["a"]
    p = a.project("app")
    a.session("n1", p, friction=["Bash grep --include=*.py failed: zsh no matches found; also `timeout` command not found"])
    a.session("n2", p, friction=["Playwright screenshot was 'outside allowed roots' again"])
    a.cause("n2", "playwright-output-roots", ago(1))
    a.cause("n2", "playwright-output-roots", ago(1, -1))
    causes = {c["id"]: c for c in friction.scan(archive["conn"])}
    assert causes["zsh-nomatch"]["sessions"] == 1 and causes["timeout-missing"]["sessions"] == 1
    roots = causes["playwright-output-roots"]
    assert (roots["sessions"], roots["errors"], roots["notes"], roots["immediate_repeats"]) == (1, 2, 1, 1)
    assert roots["examples"][0]["note"].startswith("Playwright screenshot")  # a readable note beats an error dump


def test_old_causes_are_not_still_happening_and_windows_filter(archive):
    a = archive["a"]
    p = a.project("app")
    a.session("old", p, days=40)
    a.cause("old", "cwd-drift", ago(40))
    c = {x["id"]: x for x in friction.scan(archive["conn"])}["cwd-drift"]
    assert not c["still_happening"]
    assert "cwd-drift" not in {x["id"] for x in friction.scan(archive["conn"], days=14)}
    assert friction.scan(archive["conn"], project="/nowhere") == []


def test_unmatched_recurring_notes_become_other(archive):
    a = archive["a"]
    p = a.project("app")
    for i in range(3):
        a.session(f"o{i}", p, friction=[f"Vendor sandbox rejected the invoice upload with code {400 + i}"])
    a.session("o9", p, friction=["Something else entirely happened here today"])
    other = [c for c in friction.scan(archive["conn"]) if c["category"] == "other"]
    assert len(other) == 1 and other[0]["sessions"] == 3 and not other[0]["fixes"]


def test_concurrent_sessions_from_overlap(archive):
    a = archive["a"]
    p, q = a.project("shared"), a.project("alone")
    a.session("me", p)
    a.session("peer", p)
    ts = ago(1)
    a.cause("me", "edit-stale-context", ts)
    a.tool_use("peer", ago(1, 5))  # the other session was busy five minutes earlier
    a.session("solo", q)
    a.cause("solo", "edit-stale-context", ago(1))
    a.session("far", q, days=3)
    a.tool_use("far", ago(3))
    c = {x["id"]: x for x in friction.scan(archive["conn"])}
    assert c["concurrent-sessions"]["sessions"] == 1
    assert c["concurrent-sessions"]["examples"][0]["session_id"] == "me"
    assert c["edit-stale-context"]["sessions"] == 2


def test_report_hides_noise_unless_asked(archive):
    a = archive["a"]
    p = a.project("app")
    a.session("t", p)
    a.result("t", ago(1), "Bash", "Exit code 1\n2 failed, 10 passed", command="uv run pytest -q")
    a.result("t", ago(1, -1), "Bash", "Exit code 1\n", command="cat a; grep x b")
    a.cause("t", "sleep-polling-blocked", ago(1, -2))
    rep = friction.report(archive["conn"])
    assert [c["id"] for c in rep["causes"]] == ["sleep-polling-blocked"]
    assert rep["noise_summary"] == {"occurrences": 2, "sessions": 1}
    assert {c["id"] for c in friction.report(archive["conn"], noise=True)["causes"]} >= {
        "expected-test-failures", "read-chain-nonzero"}
    assert all("_sessions" not in c for c in rep["causes"])


def test_tool_error_rates_count_duplicates_once(archive):
    a, conn = archive["a"], archive["conn"]
    p = a.project("app")
    a.session("r", p)
    ts = ago(1)
    a.result("r", ts, "apply_patch", "Failed to find expected lines", tool_use_id="x1")
    conn.execute("INSERT INTO tool_calls(session_id, tool_use_id, ts, name, is_error) VALUES ('r', 'x1', ?, 'apply_patch', 1)",
                 (ts,))
    for i in range(3):
        a.result("r", ago(1, -i - 1), "apply_patch", "ok", is_error=0)
    rates = {r["tool"]: r for r in friction.tool_error_rates(conn)}
    assert rates["apply_patch"] == {"tool": "apply_patch", "calls": 4, "errors": 1, "rate": 0.25}


def test_every_wasteful_cause_has_a_concrete_fix_and_noise_has_none():
    for c in friction.CATALOG:
        if c["noise"]:
            assert not c["fixes"]
            continue
        assert c["fixes"], c["id"]
        for fix in c["fixes"]:
            assert fix["kind"] in ("instruction", "config", "environment")
            assert fix["scope"] in ("user", "project") and fix["agents"] and fix["title"] and fix["text"]
