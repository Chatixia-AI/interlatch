from pathlib import Path

from interlatch.sources.claude.parser import classify_user_text, parse_session, patch_stats, summarize_tool_use
from interlatch.core.pricing import gemini_price_for, is_gemini_model, normalize_model, usage_cost
from interlatch.sources.builder import estimate_cost

from conftest import CWD, SID, SUB


def test_parse_session_counts(env):
    ps = parse_session(env["main"])
    assert ps.id == SID
    assert ps.project_path == CWD
    assert ps.git_branch == "main"
    assert ps.ai_title == "Fix login token expiry"
    # the human prompt plus the prompt queued while Claude was working
    assert ps.n_prompts == 2
    assert ps.first_prompt.startswith("Fix the failing login test")
    assert "ide_opened_file" not in ps.first_prompt
    assert ps.last_prompt == "also check the logout flow"
    assert ps.n_interrupts == 1
    assert ps.n_compactions == 1
    assert ps.commands["/compact"] == 1
    assert ps.prs[0]["number"] == 7
    assert ps.cc_cost_usd == 0.5
    assert ps.permission_mode == "auto"


def test_usage_is_deduplicated_per_message(env):
    ps = parse_session(env["main"])
    main_calls = [c for c in ps.api_calls if not c.agent_id]
    # msg_1 (3 lines) + msg_2 + msg_3 + msg_4 + msg_6; the <synthetic> message has no usage
    assert len(main_calls) == 5
    first = next(c for c in main_calls if c.msg_id == "msg_1")
    assert first.output_tokens == 40  # the final streamed value, not a sum of 5+20+40
    assert first.cache_write_tokens == 500
    assert first.ts == "2026-09-20T10:00:05.000Z"
    assert ps.peak_context == 5 + 2000


def test_tools_files_and_errors(env):
    ps = parse_session(env["main"])
    names = [t.name for t in ps.tool_calls if not t.agent_id]
    assert names == ["Bash", "Edit", "Read", "Agent"]
    bash = ps.tool_calls[0]
    assert bash.command.startswith("pytest") and bash.duration_ms == 13000 and not bash.is_error
    read = next(t for t in ps.tool_calls if t.name == "Read")
    assert read.is_error
    assert ps.n_tool_errors == 1
    auth = ps.files[f"{CWD}/auth.py"]
    assert (auth.edits, auth.lines_added, auth.lines_removed) == (1, 2, 1)
    assert f"{CWD}/missing.py" not in ps.files  # failed reads are not counted as touched


def test_subagent_thread(env):
    ps = parse_session(env["main"])
    sub = ps.subagents[SUB]
    assert sub.agent_type == "Explore"
    assert sub.description == "Find logout code"
    assert sub.tool_use_id == "toolu_4"
    assert sub.n_tool_calls == 1
    assert sub.model == "claude-opus-5-5"  # resolved model from the Agent result
    assert sub.output_tokens == 20
    sub_events = [e for e in ps.events if e.agent_id == SUB]
    assert sub_events[0].kind == "prompt" and sub_events[0].meta == {"subagent": True}
    totals = ps.totals()
    assert totals["sub_tokens"] == 120 and totals["sub_cost"] > 0


def test_events_and_searchability(env):
    ps = parse_session(env["main"])
    kinds = [e.kind for e in ps.events if not e.agent_id]
    assert kinds.count("prompt") == 2
    assert "compact" in kinds and "interrupt" in kinds and "command" in kinds and "compact_summary" in kinds
    assert "notice" in kinds  # the <synthetic> message
    err = next(e for e in ps.events if e.kind == "tool_result" and e.is_error)
    assert err.searchable and err.tool_name == "Read"
    ok = next(e for e in ps.events if e.kind == "tool_result" and not e.is_error)
    assert not ok.searchable


def test_active_time_caps_idle_gaps(tmp_path):
    lines = [
        '{"type":"user","timestamp":"2026-01-01T10:00:00Z","origin":{"kind":"human"},"message":{"role":"user","content":"hi"}}',
        '{"type":"user","timestamp":"2026-01-01T10:05:00Z","origin":{"kind":"human"},"message":{"role":"user","content":"again"}}',
        '{"type":"user","timestamp":"2026-01-01T15:00:00Z","origin":{"kind":"human"},"message":{"role":"user","content":"later"}}',
    ]
    p = tmp_path / "abc.jsonl"
    p.write_text("\n".join(lines))
    ps = parse_session(p)
    assert ps.duration_s == 5 * 3600
    assert ps.active_s == 5 * 60 + 15 * 60


def test_classify_user_text():
    assert classify_user_text({}, "<command-name>/foo</command-name>") == "command"
    assert classify_user_text({}, "[Request interrupted by user for tool use]") == "interrupt"
    assert classify_user_text({"origin": {"kind": "task-notification"}}, "<task-notification>x") == "notification"
    assert classify_user_text({"isMeta": True}, "Base directory for this skill: /x/y") == "meta"
    assert classify_user_text({"isCompactSummary": True}, "anything") == "compact_summary"
    assert classify_user_text({"origin": {"kind": "human"}}, "please fix it") == "prompt"
    assert classify_user_text({}, "<bash-input>ls</bash-input>") == "bash_input"


def test_summaries_and_patch_stats():
    assert summarize_tool_use("Read", {"file_path": "/p/a.py"}, "/p")[0] == "a.py"
    assert summarize_tool_use("Bash", {"command": "ls -la"})[2] == "ls -la"
    assert summarize_tool_use("mcp__playwright__browser_click", {"ref": "e1"})[0].startswith("browser_click")
    assert patch_stats({"type": "create", "content": "a\nb\nc\n"}) == (3, 0)
    assert patch_stats({"structuredPatch": [{"lines": ["+a", "-b", " c", "+d"]}]}) == (2, 1)


def test_pricing():
    assert normalize_model("claude-opus-5-5[1m]") == "claude-opus-5-5"
    assert normalize_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert normalize_model("us.anthropic.claude-sonnet-4-5-20250929-v1:0") == "claude-sonnet-4-5"
    assert normalize_model("<synthetic>") is None
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 1_000_000}}
    # opus 5.5: 4 + 20 + 0.20 + 4*1.25 + 4*2
    assert abs(usage_cost("claude-opus-5-5", usage) - (4 + 20 + 0.2 + 5 + 8)) < 1e-9
    assert abs(usage_cost("claude-opus-5-5", usage, speed="fast") - 2 * 37.2) < 1e-9
    assert usage_cost("claude-future-opus-9", {"output_tokens": 1_000_000}) == 25.0  # family fallback


def test_gemini_pricing():
    """Antigravity and Copilot on Gemini models: Google's list prices (they were $0, there being none)."""
    M = 1_000_000
    # gemini-3.8-flash, as Antigravity reports it: $0.75 uncached input, $0.075 cached, $3.75 output per 1M tokens
    assert abs(estimate_cost("gemini-3.8-flash", M, M, 0, M) - (0.75 + 0.075 + 3.75)) < 1e-9
    assert gemini_price_for("gemini-3-flash-preview") == ((0.50, 3.00, 0.05), True)  # a preview id: its model's price
    assert gemini_price_for("models/gemini-2.5-flash-lite") == ((0.10, 0.40, 0.01), True)  # not gemini-2.5-flash's
    assert gemini_price_for("google/gemini-2.5-pro")[0] == (1.25, 10.0, 0.125) and is_gemini_model("google/gemini-2.5-pro")
    # not listed: estimated at the listed model of its kind
    assert gemini_price_for("gemini-3.5-flash") == ((0.75, 3.75, 0.075), False)
    assert gemini_price_for("gemini-9-pro") == ((2.0, 12.0, 0.20), False)
    assert gemini_price_for("gemini-9-flash-lite")[0] == (0.30, 2.50, 0.03)
    assert gemini_price_for("gpt-5") == (None, True) and not is_gemini_model("claude-opus-5-5")
    assert estimate_cost("gpt-5", M, 0, 0, 0) == 1.25 and estimate_cost("claude-opus-5-5", M, 0, 0, 0) == 4.0  # unchanged


def test_gzip_archive_parses_identically(env, tmp_path):
    import gzip
    import shutil

    gz = tmp_path / f"{SID}.jsonl.gz"
    with open(env["main"], "rb") as fin, gzip.open(gz, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    sdir = tmp_path / SID
    shutil.copytree(Path(env["main"]).with_suffix(""), sdir)
    a, b = parse_session(env["main"]), parse_session(gz)
    assert (a.n_prompts, len(a.tool_calls), len(a.events)) == (b.n_prompts, len(b.tool_calls), len(b.events))
