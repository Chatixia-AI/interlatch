"""Security review fixes: on a hub, each computer's sessions stay its own; uploads can't inflate past a limit; a hub
without people lets no other device in; a transcript can't inject response headers or javascript: links; an invite
can't take over another person's computer; and a few hardening steps (git flags, the app's launcher, folder modes)."""

import gzip
import json
import os
import re
import stat
import subprocess
import textwrap
import urllib.error
import urllib.request

import pytest

from chronicle import hub, people
from chronicle.db import connect

from test_hub import SPOKE_CWD, SPOKE_SID, _analyzed_on_spoke, _make_spoke, _serve
from test_people_web import REMOTE, TAILNET, _call, _raw

MACHINE = "11111111-2222-4333-8444-555555555555"


@pytest.fixture()
def dashboard(synced, monkeypatch):
    """The `synced` computer as a hub serving its dashboard on a free port; no people yet."""
    monkeypatch.setattr(hub.IngestTrigger, "request", lambda self: None)
    hub.new_token(synced["cfg"])
    app, httpd, url = _serve(synced["cfg"])
    synced.update(app=app, url=url)
    yield synced
    httpd.shutdown()


@pytest.fixture()
def two_spokes(synced, monkeypatch):
    """The `synced` computer as a hub with two spokes, A and B, whose own sessions happen to share one id."""
    monkeypatch.setattr(hub.IngestTrigger, "request", lambda self: None)
    token = hub.new_token(synced["cfg"])
    app, httpd, url = _serve(synced["cfg"])
    a, _, a_main = _make_spoke(synced["tmp"], url, token)
    (synced["tmp"] / "b").mkdir()
    b, _, b_main = _make_spoke(synced["tmp"] / "b", url, token)
    synced.update(url=url, token=token, a=a, b=b, a_main=a_main, b_main=b_main)
    yield synced
    httpd.shutdown()


def _post(url, path, params, body: bytes, token):
    req = urllib.request.Request(f"{url}{path}?{params}", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


# ------------------------------------------------------------------ H1: a shared session stays its computer's
def test_another_computer_cant_take_over_a_shared_session(two_spokes):
    cfg, conn, a, b = two_spokes["cfg"], two_spokes["conn"], two_spokes["a"], two_spokes["b"]
    for spoke in (a, b):
        spoke.hub_share, spoke.hub_all_folders = "knowledge", True
    a_id, b_id = hub.local_machine(a)["id"], hub.local_machine(b)["id"]
    _analyzed_on_spoke(a, title="A's work", at="2026-09-20T12:00:00Z", lessons=[("a-fact", "project", "fact")])
    assert hub.push(a).sent == 1
    _analyzed_on_spoke(b, title="B's version", at="2026-09-21T12:00:00Z", lessons=[("b-fact", "project", "fact")])
    hub.push(b)  # the same session id, from another computer
    row = conn.execute("SELECT machine_id, title FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert (row["machine_id"], row["title"]) == (a_id, "A's work")
    assert [r[0] for r in conn.execute("SELECT fingerprint FROM knowledge WHERE session_id = ?", (SPOKE_SID,))] == ["a-fact"]

    # and B can't take it back: withdraw removes only what the asking computer sent
    status, r = _post(two_spokes["url"], "/api/hub/withdraw", "", json.dumps(
        {"machine": b_id, "folder": SPOKE_CWD}).encode(), two_spokes["token"])
    assert status == 200 and r["sessions"] == 0
    targets = hub.purge_targets(conn, [a_id], projects=[SPOKE_CWD])
    with pytest.raises(hub.HubError, match="only the sessions it sent"):
        hub.purge(cfg, conn, targets, action="withdraw", keep_out=False, owner=b_id)
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == 1


# ------------------------------------------------------------------ H2: a sent transcript can't replace another's
def test_a_transcript_under_another_computers_session_id_is_not_stored(two_spokes):
    from chronicle.ingest import sync

    cfg, conn, a, b = two_spokes["cfg"], two_spokes["conn"], two_spokes["a"], two_spokes["b"]
    a_id = hub.local_machine(a)["id"]
    hub.push(a)
    sync(cfg, conn)
    before = [tuple(r) for r in conn.execute("SELECT seq, text FROM events WHERE session_id = ? ORDER BY seq", (SPOKE_SID,))]
    assert before
    with open(two_spokes["b_main"], "a") as fh:  # B's file under the same id, saying something else
        fh.write(json.dumps({"type": "user", "sessionId": SPOKE_SID, "cwd": SPOKE_CWD, "timestamp": "2026-09-20T11:00:00.000Z",
                             "message": {"role": "user", "content": "replaced by B"}}) + "\n")
    hub.push(b)
    b_id = hub.local_machine(b)["id"]
    received = cfg.machines_dir / b_id / "claude" / "projects" / SPOKE_CWD.replace("/", "-") / f"{SPOKE_SID}.jsonl"
    assert received.exists()
    report = sync(cfg, conn, only=received)  # B's file alone: a full sync would put A's back right after
    assert not report.errors
    assert conn.execute("SELECT machine_id FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == a_id
    after = [tuple(r) for r in conn.execute("SELECT seq, text FROM events WHERE session_id = ? ORDER BY seq", (SPOKE_SID,))]
    assert after == before


def test_a_computers_history_cant_replace_anothers_stub(synced):
    """History-only stubs: one computer's history.jsonl can't replace the prompts of a stub another computer's made."""
    from chronicle.ingest import import_history

    cfg, conn = synced["cfg"], synced["conn"]
    stub = conn.execute("SELECT id FROM sessions WHERE source = 'history'").fetchone()
    assert stub, "the fixture has a history-only session"
    sid = stub[0]
    before = [tuple(r) for r in conn.execute("SELECT seq, text FROM events WHERE session_id = ? ORDER BY seq", (sid,))]
    other = cfg.machines_dir / MACHINE / "claude"
    other.mkdir(parents=True)
    (other / "history.jsonl").write_text(json.dumps({"sessionId": sid, "display": "from another computer",
                                                     "timestamp": 1790000000000, "project": "/elsewhere"}) + "\n")
    import_history(conn, cfg, other / "history.jsonl")
    after = [tuple(r) for r in conn.execute("SELECT seq, text FROM events WHERE session_id = ? ORDER BY seq", (sid,))]
    assert after == before


# ------------------------------------------------------------------ H3: uploads inflate only so far
def test_a_gzip_bomb_is_refused(two_spokes, monkeypatch):
    monkeypatch.setattr(hub, "MAX_SESSIONS_JSON", 64 << 10)
    monkeypatch.setattr(hub, "MAX_ANALYSES_JSON", 64 << 10)
    machine = f"machine={hub.local_machine(two_spokes['a'])['id']}"
    bomb = gzip.compress(b'{"sessions": [], "pad": "' + b"0" * (1 << 20) + b'"}')
    assert len(bomb) < 4096
    for path in ("/api/hub/sessions", "/api/hub/analyses"):
        status, r = _post(two_spokes["url"], path, machine, bomb, two_spokes["token"])
        assert status == 400 and "too large once unpacked" in r["error"], path
    status, r = _post(two_spokes["url"], "/api/hub/sessions", machine, gzip.compress(b'{"sessions": []}'), two_spokes["token"])
    assert status == 200 and r["stored"] == 0
    status, r = _post(two_spokes["url"], "/api/hub/sessions", machine, gzip.compress(b'{"sessions": []}')[:-6],
                      two_spokes["token"])
    assert status == 400  # cut off


# ------------------------------------------------------------------ an analysis applies only to its own computer's session
def test_another_computers_analysis_doesnt_apply_to_a_session(two_spokes):
    from chronicle.ingest import sync

    cfg, conn, a, b = two_spokes["cfg"], two_spokes["conn"], two_spokes["a"], two_spokes["b"]
    a_id, b_id = hub.local_machine(a)["id"], hub.local_machine(b)["id"]
    hub.push(a)  # A's transcript: the hub ingests it as A's, analysis pending
    sync(cfg, conn)
    row = conn.execute("SELECT machine_id, analysis_status, title FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert row["machine_id"] == a_id and row["analysis_status"] != "done"
    title = row["title"]
    forged = {"version": 1, "sessions": [{
        "id": SPOKE_SID, "analysis_status": "done", "analyzed_prompts": 1, "llm_title": "B's title for A's session",
        "summary": "B wrote this",
        "knowledge": [{"kind": "fact", "title": "lesson b-injected", "body": "body", "scope": "project",
                       "source": "analysis", "fingerprint": "b-injected"}]}]}
    status, _ = _post(two_spokes["url"], "/api/hub/analyses", f"machine={b_id}", gzip.compress(json.dumps(forged).encode()),
                      two_spokes["token"])
    assert status == 200
    assert hub.apply_analyses(cfg, conn) == 0
    row = conn.execute("SELECT machine_id, analysis_status, title, summary FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert (row["machine_id"], row["title"], row["summary"]) == (a_id, title, None)
    assert row["analysis_status"] != "done"
    assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE session_id = ? AND fingerprint = 'b-injected'",
                        (SPOKE_SID,)).fetchone()[0] == 0
    assert not (cfg.machines_dir / b_id / hub.ANALYSES_FILE).exists()  # not kept waiting for a session of B's either

    # the same analysis from A, the session's own computer, applies
    forged["sessions"][0]["llm_title"] = "A's own title"
    status, _ = _post(two_spokes["url"], "/api/hub/analyses", f"machine={a_id}", gzip.compress(json.dumps(forged).encode()),
                      two_spokes["token"])
    assert status == 200 and hub.apply_analyses(cfg, conn) == 1
    assert conn.execute("SELECT title FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == "A's own title"


# ------------------------------------------------------------------ an invite can't claim a computer the hub knows (#92)
def test_an_invite_cant_claim_a_computer_the_hub_already_knows(two_spokes, capsys):
    from chronicle.cli import main

    cfg, conn, url, a, b = (two_spokes[k] for k in ("cfg", "conn", "url", "a", "b"))
    a_id, b_id = hub.local_machine(a)["id"], hub.local_machine(b)["id"]
    hub.push(a)  # A joined with the shared token and sends: the hub records its key with its hello
    assert conn.execute("SELECT key_hash FROM machines WHERE id = ?", (a_id,)).fetchone()[0]
    key_file = a.home / "machine-key"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600 and "key" not in hub.local_machine(a)

    mallory = people.add(conn, "Mallory", "mallory@example.com", "member", projects=None)
    code = people.invite(conn, mallory["id"])
    for key in (None, "x" * 43):  # A's id without A's key, or with another: refused, and the code stays unused
        status, r = _call(url, "/api/hub/join", {"code": code, "machine": a_id, "name": "not A", **({"key": key} if key else {})})
        assert status == 400 and "can't tell this is it" in r["error"]
    assert people.peek_invite(conn, code) and not people.computer_bound(conn, a_id)

    ada = people.add(conn, "Ada", "ada@example.com", "admin")  # A itself joins with an invite: it shows its key
    assert hub.redeem_invite(a, url, people.invite(conn, ada["id"]))["person"]["name"] == "Ada"
    assert people.computer_bound(conn, a_id)

    # B said hello with an older Chronicle, so no key is on record: joining says hello first, with the shared token
    hub.handshake(b)
    conn.execute("UPDATE machines SET key_hash = NULL WHERE id = ?", (b_id,))
    conn.commit()
    bob = people.add(conn, "Bob", "bob@example.com", "member", projects=None)
    assert hub.redeem_invite(b, url, people.invite(conn, bob["id"]))["person"]["name"] == "Bob"
    assert people.computer_bound(conn, b_id)

    # a computer known without a key that can't say hello any more: only an admin's invite for that computer
    old = "33333333-2222-4333-8444-555555555555"
    conn.execute("INSERT INTO machines(id, name, role) VALUES (?, 'old-box', 'spoke')", (old,))
    conn.commit()
    cy = people.add(conn, "Cy", "cy@example.com", "member", projects=None)
    status, r = _call(url, "/api/hub/join", {"code": people.invite(conn, cy["id"]), "machine": old, "key": "c" * 43})
    assert status == 400 and f"--computer {old}" in r["error"]
    capsys.readouterr()
    assert main(["hub", "invite", "Cy", "--email", "cy@example.com", "--computer", "nowhere"]) == 1
    assert "doesn't know a computer nowhere" in capsys.readouterr().out
    assert main(["hub", "invite", "Cy", "--email", "cy@example.com", "--computer", "old-box"]) == 0
    out = capsys.readouterr().out
    assert f"It joins only the computer old-box ({old})" in out
    for_old = re.search(r"\b[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}\b", out).group(0)
    status, r = _call(url, "/api/hub/join", {"code": for_old, "machine": "44444444-2222-4333-8444-555555555555"})
    assert status == 400 and "for another computer" in r["error"]  # it joins that one computer only, not a new one
    status, r = _call(url, "/api/hub/join", {"code": for_old, "machine": old})
    assert status == 200 and r["person"]["name"] == "Cy" and people.computer_bound(conn, old)


# ------------------------------------------------------------------ the shared token can't act as a person's computer
def test_the_shared_token_cant_act_as_a_persons_computer(two_spokes):
    cfg, conn, a = two_spokes["cfg"], two_spokes["conn"], two_spokes["a"]
    a_id = hub.local_machine(a)["id"]
    shared = f"Bearer {two_spokes['token']}"
    assert hub.authorize(cfg, conn, shared, a_id) == (True, None)  # nobody has joined as a person: any computer
    ada = people.add(conn, "Ada", "ada@example.com", "admin")
    _, ada_token = people.join_computer(conn, people.invite(conn, ada["id"]), a_id, "Ada's Mac")
    assert hub.authorize(cfg, conn, shared, a_id) == (False, None)  # Ada's computer: only her token sends as it
    assert hub.authorize(cfg, conn, shared, MACHINE) == (True, None)  # another computer still may, while the token is on
    assert hub.authorize(cfg, conn, f"Bearer {ada_token}", a_id)[1]["id"] == ada["id"]
    status, _ = _post(two_spokes["url"], "/api/hub/withdraw", "", json.dumps({"machine": a_id, "folder": SPOKE_CWD}).encode(),
                      two_spokes["token"])
    assert status == 401
    people.remove(conn, ada["id"])
    assert hub.authorize(cfg, conn, shared, a_id) == (True, None)  # once she is removed, the computer is nobody's again


# ------------------------------------------------------------------ H4: no people, no other device
def test_a_hub_without_people_lets_no_other_device_in(dashboard):
    url = dashboard["url"]
    code, r = _call(url, "/api/sessions", headers=REMOTE)
    assert code == 403 and r["nobody"] is True and "interlatch hub invite" in r["error"]
    for path, body in (("/api/update", {}), ("/api/sync", {}), ("/api/suggestions/1/apply", {})):
        assert _call(url, path, body, REMOTE)[0] == 403, path
    assert _call(url, "/api/sessions", headers={"X-Real-IP": "203.0.113.5"})[0] == 403  # a proxy on this computer
    assert _raw(url, "/", headers=REMOTE)[0] == 200  # the page itself, which says what to do
    assert _call(url, "/api/sessions", headers=TAILNET)[0] == 200  # a login Tailscale Serve vouched for
    assert _call(url, "/api/sessions")[0] == 200  # this computer itself
    ada = people.add(dashboard["conn"], "Ada", "ada@example.com", "admin")
    assert ada and _call(url, "/api/sessions", headers=REMOTE)[0] == 401  # with people: sign in


# ------------------------------------------------------------------ M1: no header injection from a file name
def test_a_file_name_cant_add_response_headers(dashboard, monkeypatch):
    app = dashboard["app"]
    monkeypatch.setattr(app, "artifact_open", lambda i: {
        "name": 'x.txt\r\nSet-Cookie: interlatch_session=evil\r\n\r\n<script>alert(1)</script>', "ctype": "text/plain",
        "inline": False, "body": b"hi", "policy": None, "source": "disk"})
    status, headers, body = _raw(dashboard["url"], "/api/artifacts/1/open")
    assert status == 200 and body == b"hi"
    assert "Set-Cookie" not in headers
    disposition = headers["Content-Disposition"]
    assert "\r" not in disposition and "\n" not in disposition and disposition.startswith('attachment; filename="x.txt__')


# ------------------------------------------------------------------ the dashboard page runs only its own scripts (#92)
def test_the_dashboard_page_runs_only_its_own_scripts(dashboard):
    from chronicle.server import WEB_DIR

    url = dashboard["url"]
    status, headers, page = _raw(url, "/")
    policy = headers["Content-Security-Policy"]
    assert status == 200 and policy.startswith("default-src 'self'; script-src 'self';")
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert "'unsafe-eval'" in _raw(url, "/?app=mac")[1]["Content-Security-Policy"]  # the app window: pywebview's bridge
    assert "unsafe-eval" not in _raw(url, "/?app=mac", headers=REMOTE)[1]["Content-Security-Policy"]  # not from elsewhere
    # what the policy refuses is not in the page: no inline script, event handler or style attribute
    html = page.decode()
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and not re.search(r"\son[a-z]+=", html) and " style=" not in html
    js = (WEB_DIR / "app.js").read_text()
    assert not re.search(r"\bstyle: [`\"']", js)  # a string becomes a style attribute: setStyle takes an object


# ------------------------------------------------------------------ M2: only http(s) links are kept
def test_a_transcript_link_is_kept_only_when_it_is_http(tmp_path):
    from test_artifacts import _claude, _tool

    ps = _claude(tmp_path, [
        *_tool(1, "Artifact", {"file_path": "/tmp/a.html"}, "ok", tur={"url": "javascript:fetch('/api/update')"}),
        *_tool(2, "Artifact", {"file_path": "/tmp/b.html"}, "ok", tur={"url": "https://claude.ai/code/artifact/abc-1"})])
    urls = [o.get("url") for o in ps.outputs if o.get("url")]
    assert urls == ["https://claude.ai/code/artifact/abc-1"]


# ------------------------------------------------------------------ M3: an invite can't take over a computer
def test_an_invite_cant_take_over_someone_elses_computer(synced):
    conn = synced["conn"]
    ada = people.add(conn, "Ada", "ada@example.com", "admin")
    eve = people.add(conn, "Eve", "eve@example.com", "admin", by=ada)
    _, ada_token = people.join_computer(conn, people.invite(conn, ada["id"]), MACHINE, "Ada's Mac")
    code = people.invite(conn, eve["id"])
    with pytest.raises(people.PeopleError, match="as someone else"):
        people.join_computer(conn, code, MACHINE, "Eve's claim")
    assert people.computer_person(conn, ada_token, MACHINE)["id"] == ada["id"]  # Ada's token still works
    assert people.peek_invite(conn, code)["id"] == eve["id"]  # and Eve's code wasn't used up
    _, again = people.join_computer(conn, people.invite(conn, ada["id"]), MACHINE, "Ada's Mac")  # Ada joins again
    assert people.computer_person(conn, again, MACHINE)["id"] == ada["id"]
    assert people.computer_person(conn, ada_token, MACHINE) is None  # her earlier token is replaced
    people.remove(conn, ada["id"], by=eve)  # once an admin removed Ada, the computer can join as Eve
    assert people.join_computer(conn, code, MACHINE, "Eve's Mac")[0]["id"] == eve["id"]


# ------------------------------------------------------------------ hardening
def test_git_runs_no_repository_programs(monkeypatch):
    from chronicle import server

    seen = []
    monkeypatch.setattr(server.subprocess, "run", lambda argv, **kw: seen.append(argv) or
                        subprocess.CompletedProcess(argv, 1, "", ""))
    server.git_ignored("/somewhere", ["/somewhere/a"])
    argv = seen[0]
    assert argv[:5] == ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]


def test_the_launcher_runs_only_an_app_in_applications(tmp_path, monkeypatch):
    from chronicle.install import write_shim

    monkeypatch.setenv("CHRONICLE_HOME", str(tmp_path / "home"))
    shim = write_shim(str(tmp_path / "gone" / "Chronicle"))
    fake = tmp_path / "Downloads" / "Chronicle.app" / "Contents" / "MacOS" / "Chronicle"
    fake.parent.mkdir(parents=True)
    marker = tmp_path / "ran"
    fake.write_text(f"#!/bin/sh\ntouch {marker}\n")
    fake.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "mdfind").write_text(f"#!/bin/sh\necho {fake.parents[2]}\n")  # Spotlight finds a look-alike
    (bin_dir / "mdfind").chmod(0o755)
    subprocess.run([str(shim)], env={**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}, capture_output=True, timeout=10)
    assert not marker.exists()


def test_chronicle_home_is_private(tmp_path):
    from chronicle.config import load_config

    home = tmp_path / "home"
    home.mkdir(mode=0o755)
    home.chmod(0o755)
    (home / "config.toml").write_text(textwrap.dedent("""\
        [sources]
        claude_dirs = []
        codex_dirs = []
        """))
    cfg = load_config(home)
    cfg.ensure_dirs()
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    conn = connect(cfg.db_path)
    conn.close()
