"""Did an applied fix help? The share of sessions hitting its cause before and since, said honestly: a fix applied
together with another can't take the credit, few sessions are 'too early to tell', and a line that didn't help can
be taken out for good."""

from datetime import timedelta

import pytest

from interlatch.core.util import utcnow
from interlatch.knowledge import friction, suggest

from friction_fixture import ago, make_archive


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    yield from make_archive(tmp_path, monkeypatch)


def _sessions(a, prefix, days: list[float], hits: int, *, cause="zsh-nomatch", project=None, agent="claude"):
    """One session started `d` days ago for each d; the first `hits` of them hit the cause."""
    project = project or a.project("demo")
    for i, d in enumerate(days):
        a.session(f"{prefix}{i}", project, agent=agent, days=d)
        if i < hits:
            a.cause(f"{prefix}{i}", cause, ago(d - 0.01))


def _cause(conn, cid="zsh-nomatch"):
    return {c["id"]: c for c in friction._scan(conn)}[cid]


def _fix(days, **kw):
    return {"id": 1, "applied_at": ago(days), "agent": "claude", "project_path": None, "title": "Quote globs", **kw}


def test_a_fix_that_cut_the_cause_helped(archive):
    a, conn = archive["a"], archive["conn"]
    _sessions(a, "b", [20 - i * 0.5 for i in range(10)], 6)  # before: 6 of 10
    _sessions(a, "a", [9 - i * 0.5 for i in range(10)], 0)  # since: 0 of 10
    got = friction.effect(conn, _cause(conn), _fix(10))
    assert (got["verdict"], got["before"], got["after"], got["retire"]) == ("helped", [6, 10], [0, 10], False)


def test_a_drop_chance_explains_is_too_early_to_tell(archive):
    a, conn = archive["a"], archive["conn"]
    _sessions(a, "b", [20 - i for i in range(6)], 2)  # 2 of 6 ...
    _sessions(a, "a", [9 - i for i in range(5)], 0)  # ... to 0 of 5 halves the share, but proves nothing
    assert friction.effect(conn, _cause(conn), _fix(10))["verdict"] == "unclear"
    assert friction.fewer_by_chance(2, 6, 0, 5) > friction.HELPED_P
    assert friction.fewer_by_chance(6, 112, 1, 117) < friction.HELPED_P


def test_a_line_that_changed_nothing_is_offered_for_removal_after_three_weeks(archive):
    a, conn = archive["a"], archive["conn"]
    _sessions(a, "b", [40 - i for i in range(10)], 3)  # before: 3 of 10
    _sessions(a, "a", [24 - i * 2 for i in range(12)], 4)  # since: 4 of 12
    got = friction.effect(conn, _cause(conn), _fix(25))
    assert (got["verdict"], got["retire"]) == ("not_helping", True)
    assert got["days"] == 25
    young = friction.effect(conn, _cause(conn), _fix(25), now=utcnow() - timedelta(days=10))
    assert young["verdict"] == "not_helping" and not young["retire"]  # 15 days in: say so, don't offer removal yet


def test_few_sessions_or_no_baseline_is_too_early_to_tell(archive):
    a, conn = archive["a"], archive["conn"]
    _sessions(a, "b", [20 - i for i in range(10)], 5)
    _sessions(a, "a", [3, 2], 0)  # two sessions since
    assert friction.effect(conn, _cause(conn), _fix(5))["verdict"] == "unclear"


def test_fixes_applied_together_cant_take_the_credit(archive):
    a, conn = archive["a"], archive["conn"]
    _sessions(a, "b", [20 - i * 0.5 for i in range(10)], 6)
    _sessions(a, "a", [9 - i * 0.5 for i in range(10)], 0)
    config = {"id": 2, "applied_at": ago(10, minutes=1), "agent": "claude", "project_path": None, "title": "Give Playwright its own folder"}
    got = friction.effect(conn, _cause(conn), _fix(10), [config])
    assert (got["verdict"], got["with"]) == ("confounded", "Give Playwright its own folder")
    elsewhere = {**config, "project_path": "/elsewhere"}  # a project line elsewhere doesn't reach this project's line
    assert friction.effect(conn, _cause(conn), _fix(10, project_path=a.project("demo")), [elsewhere])["verdict"] == "helped"
    later = {**config, "applied_at": ago(1)}  # applied a week and more apart: each has its own before and after
    assert friction.effect(conn, _cause(conn), _fix(10), [later])["verdict"] == "helped"


def test_a_session_counts_on_the_side_it_started_and_only_its_agent_and_project(archive):
    a, conn = archive["a"], archive["conn"]
    _sessions(a, "b", [20 - i * 0.5 for i in range(10)], 6)
    a.session("straddle", a.project("demo"), days=10.5)  # began before the fix, hit after it: before
    a.cause("straddle", "zsh-nomatch", ago(9))
    _sessions(a, "a", [9 - i * 0.5 for i in range(10)], 0)
    _sessions(a, "codex", [5, 4, 3, 2, 1], 5, agent="codex")  # Codex never reads CLAUDE.md
    _sessions(a, "other", [5, 4, 3, 2, 1], 5, project=a.project("other"))
    got = friction.effect(conn, _cause(conn), _fix(10, project_path=a.project("demo")))
    assert (got["before"], got["after"]) == ([7, 11], [0, 10])


def test_only_analyzed_sessions_count_once_any_are(archive):
    a, conn = archive["a"], archive["conn"]
    _sessions(a, "b", [20 - i * 0.5 for i in range(10)], 6)
    _sessions(a, "a", [9 - i * 0.5 for i in range(10)], 0)
    conn.execute("UPDATE sessions SET analysis_status = 'done' WHERE id LIKE 'b%' OR id IN ('a0', 'a1', 'a2')")
    assert friction.effect(conn, _cause(conn), _fix(10))["after"] == [0, 3]  # the rest may still bring friction notes


def test_refresh_says_whether_it_helped_and_retire_takes_the_line_out_for_good(archive):
    a, conn, cfg, home = archive["a"], archive["conn"], archive["cfg"], archive["home"]
    for i in range(3):  # recurring across projects: proposed for the user-level CLAUDE.md
        a.session(f"x{i}", a.project(f"p{i}"), days=40)
        a.cause(f"x{i}", "zsh-nomatch", ago(40))
    _sessions(a, "b", [38 - i for i in range(10)], 1)  # before: 4 of 13, with the three above
    _sessions(a, "a", [2 + i * 2 for i in range(12)], 4)  # still happening: hit 2 to 8 days ago
    suggest.refresh(conn, cfg)
    s = next(x for x in suggest.list_suggestions(conn) if x["key"] == "friction:zsh-nomatch:claude:user")
    assert suggest.apply(conn, cfg, s["id"])["ok"]
    conn.execute("UPDATE suggestions SET applied_at = ? WHERE id = ?", (ago(25), s["id"]))
    suggest.refresh(conn, cfg)
    row = suggest.get(conn, s["id"])
    assert row["evidence"]["effect"]["verdict"] == "not_helping" and row["evidence"]["effect"]["retire"]
    assert suggest.display(row)["evidence"]["effect_line"].startswith("Not helping: 4 of 13 sessions hit this")
    target = home / ".claude" / "CLAUDE.md"
    assert "interlatch:friction:zsh-nomatch" in target.read_text()

    got = suggest.retire(conn, cfg, s["id"])
    assert got["ok"] and got["retired"] and "zsh-nomatch" not in target.read_text()
    row = suggest.get(conn, s["id"])
    assert (row["status"], row["dismissed_reason"]) == ("dismissed", suggest.RETIRED_REASON)
    suggest.refresh(conn, cfg)  # the cause still happens, but a retired line is never proposed again
    assert suggest.get(conn, s["id"])["status"] == "dismissed"
    assert not suggest.retire(conn, cfg, s["id"])["ok"]  # only an applied instruction line
