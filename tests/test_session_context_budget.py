"""The SessionStart text fits its limit by leaving out whole items, least useful first, and says what it left out; a
cut never lands mid-line, and nothing in it skips redaction."""

import dataclasses

from interlatch.knowledge import ladder
from interlatch.runtime.hooks import build_session_context

from conftest import CWD

TOKEN = "ghp_" + "a1B2c3D4e5" * 4


def _note(conn, n: int, title: str) -> None:
    sid = f"budget-{n:02d}"
    started = f"2026-09-{n + 1:02d}T10:00:00Z"
    conn.execute("INSERT INTO sessions(id, project_path, project_name, started_at, title, outcome, source, agent, "
                 "analysis_status) VALUES (?, ?, 'demo-app', ?, ?, 'completed', 'claude', 'claude', 'done')",
                 (sid, CWD, started, f"Session {n:02d}"))
    cur = conn.execute(
        "INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, confidence, evidence, "
        "basis, source, fingerprint, created_at, updated_at, stage) VALUES (?, ?, 'demo-app', 'gotcha', ?, 'b', "
        "'project', 'high', 'e', 'observed', 'analysis', ?, ?, ?, 'provisional')",
        (sid, CWD, title, f"fp-{sid}", started, started))
    ladder.refresh(conn, [cur.lastrowid])


def _many(conn, count: int = 10) -> None:
    for n in range(count):
        _note(conn, n, f"Lesson {n:02d}: " + "the build needs the cache warmed before the integration run " * 2)
    conn.commit()


def _limited(cfg, limit: int):
    return dataclasses.replace(cfg, inject_max_chars=limit)


def test_a_tight_limit_leaves_out_whole_items_and_says_so(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    _many(conn)
    full = build_session_context(cfg, CWD)
    assert "Not shown here" not in full
    tight = build_session_context(_limited(cfg, 900), CWD)
    assert len(tight) <= 900
    *body, footer = tight.splitlines()
    assert footer.startswith("Not shown here: ") and "search_knowledge" in footer
    assert set(body) <= set(full.splitlines()), "every line kept is a whole line of the full text"
    assert body[0].startswith("Interlatch notes from past coding-agent sessions")
    assert "Lesson 09" in tight and "Lesson 00" not in tight, "the newest notes rank first and stay"


def test_the_same_limit_gives_the_same_text_every_time(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    _many(conn)
    texts = {build_session_context(_limited(cfg, 700), CWD) for _ in range(3)}
    assert len(texts) == 1


def test_the_oldest_recent_session_goes_before_any_note(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    _many(conn, 3)
    full = build_session_context(cfg, CWD)
    recent = next(line for line in full.splitlines() if line.startswith("Recent sessions here: "))
    sessions = recent.removeprefix("Recent sessions here: ").split("; ")
    assert len(sessions) == 3
    trimmed = build_session_context(_limited(cfg, len(full) - 1), CWD)
    assert sessions[-1] not in trimmed and sessions[0] in trimmed, "the oldest goes first, the newest stays"
    assert "session" in trimmed.splitlines()[-1] and "note" not in trimmed.splitlines()[-1]
    assert all(f"Lesson {n:02d}" in trimmed for n in range(3))


def test_a_limit_too_small_for_anything_is_still_kept(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    _many(conn)
    assert len(build_session_context(_limited(cfg, 40), CWD)) <= 40


def test_a_secret_stored_before_its_rule_existed_is_redacted_on_the_way_out(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    _note(conn, 1, f"Use the token {TOKEN} for the deploy")
    conn.execute("UPDATE sessions SET title = ? WHERE id = 'budget-01'", (f"Deploy with {TOKEN}",))
    conn.commit()
    text = build_session_context(cfg, CWD)
    assert TOKEN not in text
    assert text.count("[REDACTED:github-token]") == 2


def test_fit_drops_the_lowest_value_first_and_on_a_tie_the_later_one():
    from interlatch.core.budget import Block, counted, fit

    blocks = [Block("A:", [(5, "note", "- a1"), (3, "note", "- a2")]),
              Block("Recent: ", [(3, "session", "r1"), (1, "session", "r2")], inline=True)]
    footer = lambda left: f"left: {counted(left, ('note', 'session'))}" if left else "all here"  # noqa: E731
    full = fit(["Head"], blocks, footer, 1000)
    assert full == "Head\nA:\n- a1\n- a2\nRecent: r1; r2\nall here"
    # r2 (1) goes first; then r1 and a2 tie at 3, and the later block's r1 goes before a2
    two_out = "Head\nA:\n- a1\n- a2\nleft: 2 sessions"
    assert fit(["Head"], blocks, footer, len(two_out)) == two_out
