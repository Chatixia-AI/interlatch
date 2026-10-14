"""Home's Agents card (/api/overview `agents`): each agent's totals for the period, whichever agent Home is filtered to;
an agent idle in the period but used in the project scope still has a row, with zeros; `records` names the metrics an
agent's sessions carry at all, so a chat export shows a dash for tokens rather than a 0."""

from datetime import timedelta

from interlatch.server import App
from interlatch.core.util import to_iso, utcnow

CHAT = "c1a7c1a7-0000-4000-8000-000000000001"
OLD = "c0de0000-0000-4000-8000-000000000002"
PROJECT = "/work/agents-card"


def _session(conn, sid, agent, *, days_ago, project=PROJECT, **cols):
    at = to_iso(utcnow() - timedelta(days=days_ago))
    names = ["id", "source", "agent", "project_path", "project_name", "started_at", "ended_at", *cols]
    conn.execute(f"INSERT INTO sessions({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                 [sid, "transcript", agent, project, project.rsplit("/", 1)[-1], at, at, *cols.values()])


def _rows(synced, **q):
    return {r["agent"]: r for r in App(synced["cfg"]).overview(q)["agents"]}


def test_every_agent_with_its_totals_and_what_it_records(synced):
    conn = synced["conn"]
    _session(conn, CHAT, "chatgpt", days_ago=2, n_prompts=4, active_s=50_000)
    _session(conn, OLD, "codex", days_ago=200, n_prompts=3, active_s=600, n_tool_calls=9, n_tool_errors=1,
             input_tokens=1000, output_tokens=200, est_cost_usd=0.5)
    conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, source, "
                 "fingerprint, created_at, updated_at, status) VALUES (?, ?, 'agents-card', 'gotcha', 't', 'b', "
                 "'project', 'analysis', 'f', ?, ?, 'active')", (CHAT, PROJECT, to_iso(utcnow()), to_iso(utcnow())))
    conn.commit()

    rows = _rows(synced, days="90", agent="claude")  # the card shows every agent, whichever one Home is filtered to
    assert set(rows) == {"claude", "chatgpt", "codex"}
    claude, chat, codex = rows["claude"], rows["chatgpt"], rows["codex"]
    assert claude["sessions"] >= 1 and claude["tokens"] > 0 and set(claude["records"]) >= {"tool_calls", "tokens", "cost"}
    # a chat export: prompts and time, nothing that would read as "0 tokens, $0"
    assert chat["sessions"] == 1 and chat["prompts"] == 4 and chat["knowledge"] == 1 and chat["records"] == []
    # idle in the last 90 days: a row of zeros, when it was last used, and what it records when it is used
    assert codex["sessions"] == 0 and codex["tool_calls"] == 0 and codex["cost"] == 0 and codex["knowledge"] == 0
    assert codex["last"] and codex["records"] == ["tool_calls", "tokens", "cost"]
    assert list(rows) == sorted(rows, key=lambda a: -rows[a]["active_s"])  # busiest first

    every = _rows(synced, days="all")
    assert every["codex"]["sessions"] == 1 and every["codex"]["tool_errors"] == 1 and every["codex"]["tokens"] == 1200


def test_a_project_shows_the_agents_used_in_it(synced):
    conn = synced["conn"]
    _session(conn, CHAT, "chatgpt", days_ago=2, n_prompts=4)
    _session(conn, OLD, "codex", days_ago=200, project="/work/elsewhere", input_tokens=10)
    conn.commit()

    rows = _rows(synced, days="90", project=PROJECT)
    assert set(rows) == {"chatgpt"}  # Codex was only used elsewhere, the fake Claude transcripts in other folders
    assert _rows(synced, days="90", project="/work/elsewhere")["codex"]["records"] == ["tokens"]
