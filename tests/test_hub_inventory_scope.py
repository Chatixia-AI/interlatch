"""What a member's computer tells its hub about itself: the hello's repositories, the teammates'-lessons request and
the files it uploads name nothing of a project excluded on it, and, sharing knowledge of the hub's projects only,
nothing of a repository the hub has no project for. Every request body is checked whole."""

import gzip
import json
import textwrap
from pathlib import Path

import pytest
from test_hub import hubenv  # noqa: F401, F811  (the fixture)

from interlatch.core.config import load_config
from interlatch.projects import routing
from interlatch.sharing.client import hello_info, spoke_roots
from interlatch.sharing.identity import write_token
from interlatch.sharing.protocol import PROTOCOL
from interlatch.sharing.push import export_analyses, push

SHARED = "/w/clients/acme/app"        # in a folder added to a hub project
KNOWN = "/w/oss/known-lib"            # its remote is one the hub files a project by
UNSHARED = "/w/side/UNSHARED-CANARY"  # neither: no project of the hub's
EXCLUDED = "/w/private/EXCLUDED-CANARY"  # [sources] exclude_projects
REMOTES = {SHARED: "example.invalid/acme/app", KNOWN: "example.invalid/oss/known-lib",
           UNSHARED: "example.invalid/me/UNSHARED-REMOTE", EXCLUDED: "example.invalid/me/EXCLUDED-REMOTE"}
CANARIES = ("UNSHARED-CANARY", "UNSHARED-REMOTE", "EXCLUDED-CANARY", "EXCLUDED-REMOTE")


class RecordingHub:
    """A hub that answers like a real one and keeps every request a computer sent it."""

    def __init__(self, remotes=None, scope=None):
        self.remotes = remotes if remotes is not None else {REMOTES[KNOWN]: "/hub/known-lib"}
        self.scope = scope
        self.requests: list[tuple[str, dict | None, bytes | None]] = []

    def close(self):
        pass

    def request(self, method, path, *, params=None, body=None, length=None, headers=None):
        raw = None
        if hasattr(body, "read"):
            body.seek(0)
            raw = gzip.decompress(body.read())
        elif isinstance(body, bytes):
            raw = gzip.decompress(body)
        self.requests.append((path, body if isinstance(body, dict) else None, raw))
        if path == "/api/hub/hello":
            return {"protocol": PROTOCOL, "version": "test", "hub": "test hub", "inventory": {}, "wants_analyses": True,
                    "projects": [], "remotes": dict(self.remotes), "folders": {}, "moved": 0, "knowledge": {},
                    "team": True, "scope": self.scope}
        if path == "/api/hub/lessons":
            return {"team": True, "version": "v1", "lessons": [], "places": {}}
        return {"ok": True}

    def bodies(self, path):
        return [b for p, b, _ in self.requests if p == path]

    def everything_sent(self) -> str:
        return "\n".join((json.dumps(b) if b is not None else "") + (raw or b"").decode(errors="replace")
                         for _p, b, raw in self.requests)


def _transcript(claude: Path, cwd: str, sid: str, text: str = "hi") -> None:
    proj = claude / "projects" / cwd.replace("/", "-")
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{sid}.jsonl").write_text(json.dumps({"type": "user", "sessionId": sid, "cwd": cwd,
                                                   "timestamp": "2026-09-20T10:00:00.000Z",
                                                   "message": {"role": "user", "content": text}}) + "\n")


@pytest.fixture()
def spoke(tmp_path, monkeypatch):
    """A computer that joined a hub, with Claude Code sessions in the four kinds of repository."""
    claude, home = tmp_path / "claude", tmp_path / "home"
    for i, cwd in enumerate((SHARED, KNOWN, UNSHARED, EXCLUDED)):
        _transcript(claude, cwd, f"0000000{i}-0000-0000-0000-000000000000", f"prompt in {cwd}")
    (claude / "history.jsonl").write_text("\n".join(json.dumps(
        {"display": f"history prompt in {cwd}", "timestamp": 1774931730912 + i, "project": cwd, "sessionId": f"old-{i}"})
        for i, cwd in enumerate((SHARED, KNOWN, UNSHARED, EXCLUDED))) + "\n")
    home.mkdir()
    (home / "config.toml").write_text(textwrap.dedent(f"""\
        [sources]
        claude_dirs = ["{claude}"]
        codex_dirs = []
        exclude_projects = ["/w/private/*"]
        [hub]
        url = "https://hub.example"
        share = "knowledge"
        [hub.folders]
        "/w/clients/acme" = "/hub/acme"
        """))
    cfg = load_config(home)
    cfg.ensure_dirs()
    write_token(cfg, "token")
    asked = []

    def git_info(path):
        asked.append(path)
        return (path, REMOTES[path]) if path in REMOTES else None

    monkeypatch.setattr(routing, "git_info", git_info)
    return {"cfg": cfg, "claude": claude, "git_asked": asked}


def _no_canary(hub: RecordingHub, *canaries):
    sent = hub.everything_sent()
    for canary in canaries or CANARIES:
        assert canary not in sent, canary


def test_knowledge_of_the_hubs_projects_names_only_their_repositories(spoke):
    cfg = spoke["cfg"]
    hub = RecordingHub()
    report = push(cfg, client=hub)
    assert not report.errors
    hellos = hub.bodies("/api/hub/hello")
    # the first hello knows of no remote yet: the folder's repository only. The hub's answer files the known
    # remote's repository, so a second hello names it before anything is shared
    assert [sorted(h["repos"]) for h in hellos] == [[SHARED], sorted([SHARED, KNOWN])]
    assert hellos[1]["repos"] == {SHARED: [SHARED, REMOTES[SHARED]], KNOWN: [KNOWN, REMOTES[KNOWN]]}
    assert hellos[0]["folders"] == {"/w/clients/acme": "/hub/acme"} and hellos[0]["share"] == "knowledge"
    lessons = hub.bodies("/api/hub/lessons")
    assert len(lessons) == 1 and lessons[0]["remotes"] == sorted([REMOTES[SHARED], REMOTES[KNOWN]])
    assert lessons[0]["projects"] == ["/hub/acme"] and lessons[0]["left"] == []
    _no_canary(hub)
    assert EXCLUDED not in spoke["git_asked"]  # an excluded project's repository isn't even looked at

    hub2 = RecordingHub()
    push(cfg, client=hub2)  # the remotes the hub filed by last time: one hello names both
    assert [sorted(h["repos"]) for h in hub2.bodies("/api/hub/hello")] == [sorted([SHARED, KNOWN])]
    _no_canary(hub2)


def test_a_left_project_or_one_outside_the_persons_projects_names_nothing(spoke):
    cfg = spoke["cfg"]
    cfg.hub_left = ["/hub/known-lib"]
    hub = RecordingHub()
    push(cfg, client=hub)
    assert [sorted(h["repos"]) for h in hub.bodies("/api/hub/hello")] == [[SHARED]]
    assert hub.bodies("/api/hub/lessons")[0]["remotes"] == [REMOTES[SHARED]]
    assert (cfg.home / "hub-folders.json").is_file()
    assert "UNSHARED-CANARY" not in (cfg.home / "hub-folders.json").read_text()
    cfg.hub_left = []
    (cfg.home / "hub-folders.json").unlink()  # a first hello again: the hub hasn't said yet whom it limits
    hub = RecordingHub(scope=["/hub/known-lib"])  # this person sees only that project
    push(cfg, client=hub)
    assert [sorted(h["repos"]) for h in hub.bodies("/api/hub/hello")] == [[SHARED], [KNOWN]]
    hub = RecordingHub(scope=["/hub/known-lib"])
    push(cfg, client=hub)  # the scope the hub said last time holds from the first hello on
    assert [sorted(h["repos"]) for h in hub.bodies("/api/hub/hello")] == [[KNOWN]]


@pytest.mark.parametrize("extra", ["all_folders", "everything"])
def test_every_folder_or_transcripts_still_name_no_excluded_repository(spoke, extra):
    cfg = spoke["cfg"]
    if extra == "all_folders":
        cfg.hub_all_folders = True
    else:
        cfg.hub_share = "everything"
    hub = RecordingHub()
    push(cfg, client=hub)
    hellos = hub.bodies("/api/hub/hello")
    assert len(hellos) == 1 and sorted(hellos[0]["repos"]) == sorted([SHARED, KNOWN, UNSHARED])
    _no_canary(hub, "EXCLUDED-CANARY", "EXCLUDED-REMOTE")
    assert ("UNSHARED-CANARY" in hub.everything_sent()) is True  # every folder: it is named, as asked


def test_the_red_team_probe_hello_info_alone(spoke):
    """The red-team probe called hello_info itself: an excluded repository must be absent from that body whole."""
    cfg = spoke["cfg"]
    cfg.hub_folders = {**cfg.hub_folders, "/w/private/EXCLUDED-CANARY": "/hub/acme"}  # added, then excluded
    body = hello_info(cfg, spoke_roots(cfg))
    assert body["folders"] == {"/w/clients/acme": "/hub/acme"}
    sent = json.dumps(body)
    for canary in CANARIES:
        assert canary not in sent, canary
    cfg.hub_share = "everything"
    assert "EXCLUDED" not in json.dumps(hello_info(cfg, spoke_roots(cfg)))


def test_a_real_hub_stores_no_unshared_repository_and_still_files_by_remote(hubenv, monkeypatch):  # noqa: F811
    """The red-team probe against a hub: what it keeps of the hello (machines.repos_json) names no excluded or
    unshared repository, and a session in a repository whose remote the hub files somewhere goes there on the very
    first push, though the first hello couldn't name that repository yet."""
    from conftest import CWD
    from test_hub import SPOKE_CWD, SPOKE_SID, _analyzed_on_spoke

    from interlatch.core.machine import local_machine
    from interlatch.sharing.client import handshake
    from interlatch.storage.kv import kv_set

    conn, spoke = hubenv["conn"], hubenv["spoke"]
    excluded, unshared = "/home/test/private/EXCLUDED-CANARY", "/home/test/side/UNSHARED-CANARY"
    remotes = {SPOKE_CWD: "example.invalid/org/demo-app", excluded: "example.invalid/me/EXCLUDED-REMOTE",
               unshared: "example.invalid/me/UNSHARED-REMOTE"}
    for i, cwd in enumerate((excluded, unshared)):
        _transcript(hubenv["spoke_claude"], cwd, f"7777777{i}-0000-0000-0000-000000000000")
    tops = {SPOKE_CWD: "/home/test/code"}  # the session ran in a folder inside its repository
    monkeypatch.setattr(routing, "git_info", lambda p: (tops.get(p, p), remotes[p]) if p in remotes else None)
    kv_set(conn, f"git:{CWD}", json.dumps([CWD, remotes[SPOKE_CWD]]))  # the hub's own clone of that repository
    conn.commit()
    spoke.exclude_projects = ["/home/test/private/*"]

    def stored() -> str:
        return conn.execute("SELECT repos_json FROM machines WHERE id = ?", (local_machine(spoke)["id"],)).fetchone()[0]

    handshake(spoke)  # sends transcripts: every repository but an excluded one
    assert set(json.loads(stored())) == {SPOKE_CWD, unshared} and "EXCLUDED" not in stored()

    spoke.hub_share = "knowledge"
    (spoke.home / "hub-folders.json").unlink()  # as on a computer that never said hello before
    _analyzed_on_spoke(spoke, title="Shared", at="2026-09-20T12:00:00Z", lessons=[])
    report = push(spoke)
    assert report.sent == 1 and not report.errors
    assert json.loads(stored()) == {SPOKE_CWD: ["/home/test/code", remotes[SPOKE_CWD]]}
    # filed by its remote, at its own folder in the hub's clone: the hub knew the repository's top before it arrived
    assert conn.execute("SELECT project_path FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == f"{CWD}/demo-app"


def test_transcripts_leave_out_an_excluded_projects_lines(spoke):
    """Sending transcripts: history.jsonl goes without the excluded project's prompts; Codex's thread index and
    imports without its threads; Codex's memory notes, which name no project, stay here while anything is excluded."""
    cfg, tmp = spoke["cfg"], spoke["claude"].parent
    cfg.hub_share = "everything"
    codex = tmp / "codex"
    rollouts = codex / "sessions" / "2026" / "09" / "20"
    rollouts.mkdir(parents=True)
    ids = {"shared": "11111111-1111-1111-1111-111111111111", "excluded": "22222222-2222-2222-2222-222222222222"}
    for name, cwd in (("shared", SHARED), ("excluded", EXCLUDED)):
        (rollouts / f"rollout-2026-09-20T10-00-00-{ids[name]}.jsonl").write_text(json.dumps(
            {"timestamp": "2026-09-20T10:00:00Z", "type": "session_meta", "payload": {"id": ids[name], "cwd": cwd}}) + "\n")
    (codex / "session_index.jsonl").write_text(json.dumps({"id": ids["shared"], "thread_name": "Shared thread"}) + "\n"
                                               + json.dumps({"id": ids["excluded"], "thread_name": "EXCLUDED-TITLE"}) + "\n")
    (codex / "external_agent_session_imports.json").write_text(json.dumps({"records": [
        {"imported_thread_id": ids["shared"], "title": "fine", "source_path": f"/x/.claude/projects/-w-a/{ids['shared']}.jsonl"},
        {"imported_thread_id": ids["excluded"], "title": "EXCLUDED-IMPORT", "source_path": "/x/y.jsonl"},
        {"imported_thread_id": "other", "title": "EXCLUDED-BY-FOLDER",
         "source_path": "/x/.claude/projects/-w-private-EXCLUDED-CANARY/abc.jsonl"}]}))
    (codex / "memories").mkdir()
    (codex / "memories" / "notes.md").write_text("MEMORY-CANARY")
    cfg.codex_dirs = [codex]

    hub = RecordingHub()
    report = push(cfg, client=hub)
    assert not report.errors
    sent = hub.everything_sent()
    for canary in ("EXCLUDED-CANARY", "EXCLUDED-REMOTE", "EXCLUDED-TITLE", "EXCLUDED-IMPORT", "EXCLUDED-BY-FOLDER",
                   "MEMORY-CANARY", ids["excluded"]):
        assert canary not in sent, canary
    for wanted in (f"history prompt in {SHARED}", f"history prompt in {UNSHARED}", "Shared thread", "fine", SHARED):
        assert wanted in sent, wanted

    cfg.exclude_projects = []  # nothing excluded: everything goes whole, the memory notes too
    hub = RecordingHub()
    push(cfg, client=hub)
    assert "MEMORY-CANARY" in hub.everything_sent() and "EXCLUDED-TITLE" in hub.everything_sent()


def test_earlier_analyses_handed_over_leave_out_an_excluded_project(synced):
    """A computer that just joined hands its earlier analyses to the hub: none of an excluded project's."""
    from conftest import CWD

    cfg, conn = synced["cfg"], synced["conn"]
    conn.execute("UPDATE sessions SET analysis_status = 'done', analyzed_at = '2026-09-20T12:00:00Z', "
                 "summary = 'ANALYSIS-CANARY' WHERE project_path = ?", (CWD,))
    conn.commit()
    blob, n = export_analyses(cfg)
    assert n >= 1 and b"ANALYSIS-CANARY" in gzip.decompress(blob)
    cfg.exclude_projects = [CWD]
    blob, n = export_analyses(cfg)
    assert n == 0 and b"ANALYSIS-CANARY" not in gzip.decompress(blob)


@pytest.mark.parametrize("text, leaked", [
    ("interlatch hub join https://hub.example --code ABCD-EFGH-JKLM --share knowledge", "ABCD-EFGH-JKLM"),
    ("join with --code=WXYZ-2345-6789 now", "WXYZ-2345-6789"),
    ("interlatch hub join https://hub.example --code abcdefgh2345", "abcdefgh2345"),  # retyped: any case, no dashes
    ("open https://hub.example/signin?code=Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5 to sign in", "Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5"),
    ("https://hub.example/signin?code=Q2VKS3Bfa0xn-x_Y&next=%2Fproject", "Q2VKS3Bfa0xn-x_Y"),
    ("interlatch hub join https://hub.example --token abcdefghijklmnop123", "abcdefghijklmnop123"),  # gitleaks:allow (a fake token the test needs)
    ("the invite code is ABCD-EFGH-JKLM", "ABCD-EFGH-JKLM"),
])
def test_invite_codes_and_sign_in_links_are_redacted(text, leaked):
    from interlatch.core.redact import redact

    out = redact(text)
    assert leaked not in out and "[REDACTED" in out
    assert ("https://hub.example" in out) == ("https://hub.example" in text)  # the address stays: only the credential goes


def test_redaction_keeps_placeholders_and_prose():
    from interlatch.core.redact import redact

    for prose in ("interlatch hub join <hub address> --code <code>", "a code=1 review", "the --code flag",
                  "use ?code= in the link", "MIT-2026-X is a ticket"):
        assert redact(prose) == prose, prose
