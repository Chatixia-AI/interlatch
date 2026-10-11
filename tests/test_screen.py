"""Screening imported chats: which are worth a full analysis (screen.py)."""

import json
import threading
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

from chronicle.chat_import import import_export, import_status
from chronicle.cli import main
from chronicle.db import connect as db_connect
from chronicle.screen import candidates, queue, rule_verdict, screen_chats, screen_status

PASTED = "The quarterly report shows revenue grew in every region, led by the new subscription plans. " * 4


def gpt_chat(cid, title, prompt, reply="Here is what I found.", t=1746090000.0, update=None):
    node = lambda nid, parent, kids, role, text, at: (nid, {"id": nid, "parent": parent, "children": kids, "message": {
        "id": nid, "author": {"role": role}, "create_time": at, "content": {"content_type": "text", "parts": [text]},
        "recipient": "all", "metadata": {}}})
    mapping = dict([("root", {"id": "root", "parent": None, "children": ["u1"], "message": None}),
                    node("u1", "root", ["a1"] if reply else [], "user", prompt, t)]
                   + ([node("a1", "u1", [], "assistant", reply, t + 10)] if reply else []))
    return {"title": title, "create_time": t, "update_time": update or t + 10, "conversation_id": cid, "id": cid,
            "current_node": "a1" if reply else "u1", "mapping": mapping}


CHATS = [
    gpt_chat("c-deploy", "Deploy pipeline for our app", "Our deploy to Azure fails with exit 137 after the build step. " * 3,
             t=1746090000.0),
    gpt_chat("c-maybe", "Maybe a pandas question", "How do I group this dataframe by week and sum the totals? " * 4,
             t=1746080000.0),
    gpt_chat("c-okra", "Okra recipe", "My okra plants grew huge pods, how should I cook them for a toddler? " * 3,
             t=1746070000.0),
    gpt_chat("c-chore", "Translate to Japanese", "Translate the following into Japanese:\n\n" + PASTED, t=1746060000.0),
    gpt_chat("c-left", "An unanswered one", "Explain the difference between our two staging clusters and why. " * 3,
             t=1746050000.0),
    gpt_chat("c-short", "Hi", "hi", reply="Hello!", t=1746040000.0),
]


def write_export(path, chats=CHATS):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("conversations.json", json.dumps(chats))
    return path


def test_rules_settle_only_what_is_certain():
    row = lambda prompt, reply="ok", n=1, chars=1000: {"first_prompt": prompt, "reply": reply, "n_prompts": n, "chars": chars}
    assert rule_verdict(row("Make me a logo", reply=None))[0] == "no reply"
    assert rule_verdict(row("hi", chars=40))[0] == "too short"
    assert rule_verdict(row("下記の文章を要約せよ。\n" + PASTED))[0] == "text chore"
    assert rule_verdict(row("Translate the following into Japanese:\n" + PASTED, n=2))[0] == "text chore"
    # left to the model: code after the instruction, a longer chat, an instruction with nothing pasted, a question
    assert rule_verdict(row("Translate this Python to Go:\n```python\ndef f(x):\n    return x\n```\n" + PASTED)) is None
    assert rule_verdict(row("Summarize the following:\n" + PASTED, n=5)) is None
    assert rule_verdict(row("how do I translate my app with i18next?")) is None
    assert rule_verdict(row("Why does our deploy fail with exit 137?")) is None


def test_screen_sorts_chats_and_queues_the_ones_worth_it(env):
    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    import_export(cfg, conn, write_export(env["tmp"] / "chatgpt.zip"))
    assert len(candidates(conn)) == 6

    report = screen_chats(cfg, conn)
    assert (report.counts["analyze"], report.counts["maybe"], report.counts["skip"]) == (1, 1, 3)
    assert report.by_rules == 2 and report.calls == 1 and report.left == 1  # the model left one out
    assert "1 worth analyzing" in report.summary() and "1 not screened" in report.summary()
    row = lambda cid: conn.execute("SELECT * FROM sessions WHERE id = ?", (cid,)).fetchone()
    deploy = row("c-deploy")
    assert deploy["screen_verdict"] == "analyze" and deploy["screen_reason"] == "because: Deploy pipeline for our app"
    assert deploy["screen_by"] == "claude-sonnet-5" and deploy["screen_sig"] == deploy["files_sig"]
    assert (row("c-chore")["screen_verdict"], row("c-chore")["screen_by"]) == ("skip", "rules")
    assert row("c-short")["screen_topic"] == "too short" and row("c-left")["screen_verdict"] is None
    assert conn.execute("SELECT COUNT(*) FROM analyses WHERE kind = 'screen'").fetchone()[0] == 1
    # screened chats are not asked about again; the one left out is
    assert [r["id"] for r in candidates(conn)] == ["c-left"]
    status = import_status(conn)["chatgpt"]["screen"]
    assert (status["analyze"], status["maybe"], status["skip"], status["unscreened"], status["to_queue"]) == (1, 1, 3, 1, 1)

    assert queue(conn) == 1 and row("c-deploy")["analysis_status"] == "pending"
    assert queue(conn, maybe=True) == 1 and row("c-maybe")["analysis_status"] == "pending"
    assert row("c-okra")["analysis_status"] == "skipped"
    assert screen_status(conn, "chatgpt-export")["queued"] == 2

    # a newer export changes the queued chat: it stays queued, and is screened again
    changed = [gpt_chat("c-deploy", "Deploy pipeline for our app", "Our deploy to Azure fails again. " * 5, update=1746099999.0),
               *CHATS[1:]]
    assert import_export(cfg, conn, write_export(env["tmp"] / "newer.zip", changed))["updated"] == 1
    assert row("c-deploy")["analysis_status"] == "pending"
    assert {r["id"] for r in candidates(conn)} == {"c-deploy", "c-left"}


def test_usage_limit_leaves_the_rest_for_later(env, monkeypatch):
    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    import_export(cfg, conn, write_export(env["tmp"] / "chatgpt.zip"))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    report = screen_chats(cfg, conn)
    assert report.screened == report.by_rules == 2 and report.left == 4 and "usage limit" in report.error
    assert len(candidates(conn)) == 4


def test_cli_screen(env, capsys):
    cfg = env["cfg"]
    write_export(env["tmp"] / "chatgpt.zip")
    assert main(["import", str(env["tmp"] / "chatgpt.zip")]) == 0
    assert "interlatch screen" in capsys.readouterr().out
    assert main(["screen", "--dry-run"]) == 0
    assert "6 chats to screen: 2 settled by rules, 4 for haiku in 1 call" in capsys.readouterr().out
    log = env["tmp"] / "fake_claude.log"
    assert not log.exists() or "imported chat history" not in log.read_text()  # a dry run sends nothing

    assert main(["screen"]) == 0
    out = capsys.readouterr().out
    assert "1 worth analyzing, 1 maybe, 3 not worth it" in out and "--queue" in out
    call = json.loads(log.read_text().splitlines()[-1])
    assert call["args"][call["args"].index("--model") + 1] == "haiku"
    assert "<developer>" in call["prompt_head"] and "Okra recipe" in call["prompt_head"]
    assert "Translate to Japanese" not in call["prompt_head"]  # settled by rules, never sent

    assert main(["screen", "--list", "analyze"]) == 0
    assert "Deploy pipeline" in capsys.readouterr().out
    assert main(["screen", "--list", "skip", "--json"]) == 0
    assert {r["id"] for r in json.loads(capsys.readouterr().out)} == {"c-okra", "c-chore", "c-short"}
    assert main(["screen", "--queue"]) == 0
    assert "queued 1 chat for analysis" in capsys.readouterr().out
    conn = db_connect(cfg.db_path)
    assert conn.execute("SELECT analysis_status FROM sessions WHERE id = 'c-deploy'").fetchone()[0] == "pending"


def test_dashboard_filters_and_queues(env):
    from chronicle.server import App, make_handler

    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    import_export(cfg, conn, write_export(env["tmp"] / "chatgpt.zip"))
    screen_chats(cfg, conn)
    app = App(cfg)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    get = lambda path: json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10))

    def post(path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                     headers={"X-Interlatch": "1", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    try:
        worth = get("/api/sessions?screen=analyze")
        assert [x["id"] for x in worth["items"]] == ["c-deploy"]
        assert worth["items"][0]["screen_reason"] == "because: Deploy pipeline for our app"
        assert [x["id"] for x in get("/api/sessions?screen=none")["items"]] == ["c-left"]
        assert get("/api/sessions?screen=skip")["total"] == 3
        assert get("/api/imports")["chatgpt"]["screen"]["to_queue"] == 1
        assert post("/api/screen/queue", {"source": "chatgpt", "maybe": True})["queued"] == 2
        assert get("/api/imports")["chatgpt"]["screen"]["queued"] == 2
    finally:
        httpd.shutdown()
