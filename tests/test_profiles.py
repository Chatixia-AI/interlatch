"""Named profiles (core.profiles, projects.owners, application.profiles): who owns each session, and that Personal's hub
gets only Personal's. The design: docs/profiles.md."""

import gzip
import json
import os
import random

import pytest

from interlatch.application import profiles as ops
from interlatch.core.config import load_config, set_config_value
from interlatch.core.profiles import (PERSONAL, WAITING, Profile, Registry, RegistryError, load_registry, route,
                                      save_registry)
from interlatch.projects import owners
from interlatch.sharing import client
from interlatch.sharing.protocol import HubError
from interlatch.sharing.push import push
from interlatch.storage.sqlite import connect

from test_hub import SPOKE_CWD, SPOKE_SID, _analyzed_on_spoke
from hubs_fixture import group, hub_with
from test_team import teamenv  # noqa: F401  (the fixture)


def _reg(tmp_path, **profiles) -> Registry:
    reg = Registry(path=tmp_path / "profiles.toml")
    for i, (pid, (owns, remotes)) in enumerate(profiles.items()):
        reg.profiles[pid] = Profile(id=pid, label=pid.title(), home=tmp_path / "profiles" / pid, owns=owns,
                                    remotes=remotes, port=11525 + i)
    return reg


# ------------------------------------------------------------------ the routing matrix
def test_no_profiles_everything_is_personal(tmp_path):
    assert route(None, "/w/acme/app").owner == PERSONAL
    assert route(Registry(path=tmp_path / "p.toml"), "/w/acme/app", "github.com/acme/app").owner == PERSONAL


def test_the_longest_owned_folder_wins_both_ways(tmp_path):
    reg = _reg(tmp_path, acme=(["/w/acme"], []), oss=(["/w/acme/oss"], []))
    assert route(reg, "/w/acme/app").owner == "acme"
    assert route(reg, "/w/acme/oss/lib").owner == "oss"
    assert route(reg, "/w/acme").owner == "acme"
    assert route(reg, "/w/acmeish").owner == PERSONAL  # a prefix of the name is not inside it
    assert route(reg, "/w").owner == PERSONAL
    assert route(reg, None).owner == PERSONAL  # chats with no folder


def test_a_repository_two_profiles_claim_waits(tmp_path):
    reg = _reg(tmp_path, acme=([], ["github.com/acme/*"]), globex=([], ["github.com/*/app"]))
    r = route(reg, "/w/elsewhere", "github.com/acme/app")
    assert r.owner == WAITING and r.rule == "conflict"
    assert route(reg, "/w/elsewhere", "github.com/acme/lib").owner == "acme"
    assert route(reg, "/w/elsewhere", "github.com/globex/app").owner == "globex"


def test_a_folder_whose_repository_another_profile_claims_waits(tmp_path):
    reg = _reg(tmp_path, acme=(["/w/acme"], []), globex=([], ["github.com/globex/*"]))
    assert route(reg, "/w/acme/x", "github.com/globex/x").owner == WAITING
    assert route(reg, "/w/acme/x", "github.com/acme/x").owner == "acme"


def test_a_repository_seen_in_a_profiles_folder_is_that_profiles_elsewhere(tmp_path):
    reg = _reg(tmp_path, acme=(["/w/acme"], []))
    known = {"github.com/acme/app": {"acme"}}
    assert route(reg, "/tmp/clone", "github.com/acme/app", known=known).owner == "acme"
    assert route(reg, "/tmp/clone", "github.com/acme/app", known={"github.com/acme/app": {"gone"}}).owner == PERSONAL


def test_a_choice_settles_and_beats_a_conflict_but_not_a_narrower_folder(tmp_path):
    reg = _reg(tmp_path, acme=(["/w/acme"], []), globex=([], ["github.com/globex/*"]))
    reg.choices["/w/acme/x"] = "acme"
    assert route(reg, "/w/acme/x/sub", "github.com/globex/x").owner == "acme"
    reg.choices["/w/acme"] = PERSONAL
    assert route(reg, "/w/acme/y").owner == PERSONAL  # a choice wins a tie with an owned folder
    reg.profiles["globex"].owns.append("/w/acme/deep")
    assert route(reg, "/w/acme/deep/z").owner == "globex"  # a narrower owned folder wins
    reg.choices["/w/old"] = WAITING
    assert route(reg, "/w/old/app").rule == "held"
    reg.choices["/w/gone"] = "removed-profile"
    assert route(reg, "/w/gone/app").owner == WAITING  # never falls back to Personal


def test_an_owner_once_given_is_kept_but_waiting_is_routed_again(tmp_path):
    reg = _reg(tmp_path, acme=(["/w/acme"], []), globex=(["/w/acme/inner"], []))
    assert route(reg, "/w/acme/inner/app", recorded="acme").owner == "acme"  # a Globex folder added around it later
    assert route(reg, "/w/acme/inner/app", recorded=WAITING).owner == "globex"
    assert route(reg, "/w/acme/inner/app", recorded="removed-profile").owner == "globex"


def test_every_folder_has_exactly_one_owner_and_personal_shares_only_its_own(tmp_path):
    """A property test: random profiles, folders and remotes. Each session gets one owner (Personal, one profile, or
    waiting), the same answer every time, and Personal's hub filter (off_hub) agrees with it."""
    rnd = random.Random(7)
    words = ["acme", "globex", "app", "lib", "oss", "deep"]
    for _ in range(200):
        n = rnd.randint(1, 3)
        reg = _reg(tmp_path, **{f"p{i}": ([f"/w/{'/'.join(rnd.sample(words, rnd.randint(1, 2)))}"
                                          for _ in range(rnd.randint(0, 2))],
                                         [f"github.com/{rnd.choice(words)}/*"] * rnd.randint(0, 1)) for i in range(n)})
        for f in rnd.sample(words, 2):
            reg.choices[f"/w/{f}"] = rnd.choice([PERSONAL, WAITING, "p0"])
        for _ in range(20):
            folder = f"/w/{'/'.join(rnd.choices(words, k=rnd.randint(1, 4)))}"
            remote = rnd.choice([None, f"github.com/{rnd.choice(words)}/{rnd.choice(words)}"])
            r = route(reg, folder, remote)
            assert r.owner in {PERSONAL, WAITING, *reg.profiles}
            assert route(reg, folder, remote) == r


# ------------------------------------------------------------------ the registry file
def test_the_registry_round_trips_and_is_private(tmp_path):
    reg = _reg(tmp_path, p7k2m9qa=(["/w/acme"], ["github.com/acme-corp/*"]))
    reg.profiles["p7k2m9qa"].label = 'Acme "Corp"'
    reg.choices['/w/odd "dir"'] = WAITING
    save_registry(reg)
    again = load_registry(tmp_path)
    assert again.profiles["p7k2m9qa"].label == 'Acme "Corp"' and again.profiles["p7k2m9qa"].owns == ["/w/acme"]
    assert again.profiles["p7k2m9qa"].remotes == ["github.com/acme-corp/*"]
    assert again.choices == {'/w/odd "dir"': WAITING}
    if os.name == "posix":
        assert (tmp_path / "profiles.toml").stat().st_mode & 0o077 == 0


def test_an_unreadable_registry_shares_nothing(tmp_path):
    (tmp_path / "profiles.toml").write_text("[profiles.x\nbroken")
    cfg = load_config(tmp_path)
    with pytest.raises(RegistryError):
        cfg.off_hub("/anything")


# ------------------------------------------------------------------ with a hub: Personal's hub gets only Personal's
def _bodies(monkeypatch) -> list[bytes]:
    """Every request body a HubClient sends, as bytes (gzip opened)."""
    sent: list[bytes] = []
    request = client.HubClient.request

    def spy(self, method, path, **kw):
        body = kw.get("body")
        if isinstance(body, (bytes, bytearray)):
            try:
                body = gzip.decompress(body)
            except OSError:
                pass
            sent.append(bytes(body))
        elif body is not None:
            sent.append(json.dumps(body).encode())
        return request(self, method, path, **kw)

    monkeypatch.setattr(client.HubClient, "request", spy)
    return sent


def _owner(cfg, sid=SPOKE_SID):
    conn = connect(cfg.db_path)
    try:
        return owners.owner_of(conn, sid)
    finally:
        conn.close()


def test_a_profiles_folder_never_reaches_personals_hub(teamenv, monkeypatch):  # noqa: F811
    spoke, hub = teamenv["spoke"], teamenv["conn"]
    _analyzed_on_spoke(spoke, title="Acme work", at="2026-10-02T00:00:00Z", lessons=[("p-gotcha", "project", "gotcha")])
    p, moved = hub_with(spoke, "Acme", [SPOKE_CWD])
    assert moved.sessions == {PERSONAL: 1} and not moved.taken.get(PERSONAL) and _owner(spoke) == p.id
    sent = _bodies(monkeypatch)
    report = push(spoke)
    assert not report.errors and report.sent == 0
    assert hub.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == 0
    assert sent and not any(SPOKE_CWD.encode() in b or b"demo-app" in b or b"Acme work" in b for b in sent)

    # its group disconnected, it waits: still no hub; chosen for Personal it goes
    was, _ = ops.disconnect(spoke, "Acme work")
    assert was == "Acme" and _owner(spoke) == WAITING
    assert push(spoke).sent == 0
    ops.choose(spoke, SPOKE_CWD, "personal")
    assert _owner(spoke) == PERSONAL
    assert push(spoke).sent == 1
    assert hub.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == 1


def _on_hub(env, sid=SPOKE_SID) -> bool:
    """Whether the hub has the session: its own copy, and the team store's."""
    here = env["conn"].execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (sid,)).fetchone()[0] == 1
    assert here == (sid in env["fake"].sessions)
    return here


def test_a_session_that_moves_is_taken_back_from_its_hub_first(teamenv):  # noqa: F811
    spoke, hub = teamenv["spoke"], teamenv["conn"]
    _analyzed_on_spoke(spoke, title="Shared already", at="2026-10-02T00:00:00Z", lessons=[("p-gotcha", "project", "gotcha")])
    assert push(spoke).sent == 1 and _on_hub(teamenv)
    ops.add_hub(spoke, "Acme")
    gid = group(spoke, "Acme work", [SPOKE_CWD])
    preview = ops.connect(spoke, gid, "Acme", dry_run=True)
    assert preview.on_hub == {PERSONAL: 1} and _on_hub(teamenv) and load_registry(spoke.home).find("Acme").owns == []
    moved = ops.connect(spoke, gid, "Acme")
    assert moved.taken == {PERSONAL: 1} and not moved.kept and _owner(spoke) == load_registry(spoke.home).find("Acme").id
    assert not _on_hub(teamenv)  # gone from the hub's copy and the team store, its lessons with it
    assert not hub.execute("SELECT 1 FROM knowledge WHERE session_id = ?", (SPOKE_SID,)).fetchone()
    audit = hub.execute("SELECT action, detail FROM people_audit ORDER BY id DESC LIMIT 1").fetchone()
    assert audit["action"] == "withdraw" and json.loads(audit["detail"])["moved"] is True
    assert push(spoke).sent == 0 and not _on_hub(teamenv)  # and Personal's hub never gets it again

    # chosen for Personal again, it is shared again: taking back doesn't keep it out
    ops.disconnect(spoke, gid)
    ops.choose(spoke, SPOKE_CWD, "personal")
    assert push(spoke).sent == 1 and _on_hub(teamenv)


def test_a_hub_that_cant_be_asked_stops_the_move_unless_you_say(teamenv, monkeypatch):  # noqa: F811
    from interlatch.sharing.protocol import HubUnreachable

    spoke = teamenv["spoke"]
    _analyzed_on_spoke(spoke, title="Shared already", at="2026-10-02T00:00:00Z", lessons=[])
    assert push(spoke).sent == 1
    ops.add_hub(spoke, "Acme")
    gid = group(spoke, "Acme work", [SPOKE_CWD])

    def down(*a, **kw):
        raise HubUnreachable("can't reach the hub")

    with monkeypatch.context() as m:
        m.setattr(client, "withdraw_sessions", down)
        with pytest.raises(ops.ProfileError, match="take back the 1 sessions") as exc:
            ops.connect(spoke, gid, "Acme")
        assert exc.value.can_leave
        assert load_registry(spoke.home).find("Acme").owns == [] and _owner(spoke) == PERSONAL  # nothing changed
        moved = ops.connect(spoke, gid, "Acme", leave_on_hub=True)
    assert moved.kept == {PERSONAL: "can't reach the hub"} and not moved.taken and _on_hub(teamenv)
    assert _owner(spoke) == load_registry(spoke.home).find("Acme").id

    # reached again: the next send to Personal's hub takes it back before anything goes
    report = push(spoke)
    assert not report.errors and report.withdrawn == 1 and report.sent == 0 and not _on_hub(teamenv)
    assert "1 taken back" in report.summary()
    assert push(spoke).withdrawn == 0


def test_a_hub_too_old_to_take_sessions_back_keeps_them_only_if_you_say(teamenv, monkeypatch):  # noqa: F811
    spoke = teamenv["spoke"]
    _analyzed_on_spoke(spoke, title="Shared already", at="2026-10-02T00:00:00Z", lessons=[])
    assert push(spoke).sent == 1
    ops.add_hub(spoke, "Acme")
    gid = group(spoke, "Acme work", [SPOKE_CWD])
    request = client.HubClient.request

    def old_hub(self, method, path, **kw):  # 0.28's withdraw: by folder only, so an unknown folder takes nothing
        if path == "/api/hub/withdraw":
            return {"sessions": 0, "project": None}
        return request(self, method, path, **kw)

    monkeypatch.setattr(client.HubClient, "request", old_hub)
    with pytest.raises(ops.ProfileError, match="older version") as exc:
        ops.connect(spoke, gid, "Acme")
    assert exc.value.can_leave and _owner(spoke) == PERSONAL
    moved = ops.connect(spoke, gid, "Acme", leave_on_hub=True)
    assert moved.too_old == [PERSONAL] and _on_hub(teamenv)
    report = push(spoke)  # it can't take it back: the send goes on, and says nothing of it
    assert not report.errors and report.withdrawn == 0 and _on_hub(teamenv)


def test_new_sessions_are_given_their_owner_as_they_are_recorded(teamenv):  # noqa: F811
    spoke = teamenv["spoke"]
    p, moved = hub_with(spoke, "Acme", [SPOKE_CWD])
    assert moved.sessions == {}  # nothing recorded yet
    _analyzed_on_spoke(spoke, title="New", at="2026-10-02T00:00:00Z", lessons=[])  # syncs it in
    assert _owner(spoke) == p.id
    assert push(spoke).sent == 0


def test_with_profiles_transcripts_never_go(teamenv):  # noqa: F811
    spoke = teamenv["spoke"]
    hub_with(spoke, "Acme", ["/nowhere/acme"])
    set_config_value(spoke, "hub", "share", '"everything"')
    spoke = load_config(spoke.home)
    with pytest.raises(HubError, match="knowledge only"):
        push(spoke)
    with pytest.raises(ops.ProfileError, match="share knowledge"):
        ops.add_hub(spoke, "Globex")


def test_one_profile_per_hub(teamenv):  # noqa: F811
    from interlatch.sharing.client import redeem_invite

    spoke = teamenv["spoke"]
    p, _ = hub_with(spoke, "Acme", ["/nowhere/acme"])
    acme = load_config(p.home)
    with pytest.raises(HubError, match="already sends to the hub at .* as Personal"):
        redeem_invite(acme, teamenv["url"], "ABCD-EFGH-JKLM")


def test_a_profiles_folder_is_a_hub_link_of_personal(tmp_path):
    """A profile's folder holds its hub link, token, computer identity and sharing state; what it shares comes from
    Personal's archive, with Personal's settings. An older Interlatch run there records and sends nothing."""
    import tomllib

    from interlatch.core.machine import local_machine_id

    cfg = load_config(tmp_path / "personal")
    p, _ = hub_with(cfg, "Acme", [str(tmp_path)])
    acme = load_config(p.home)
    assert p.home == cfg.home / "profiles" / p.id and acme.server_port == p.port == 11525
    assert acme.db_path == cfg.db_path and acme.archive_dir == cfg.archive_dir and acme.owner == p.id
    assert acme.claude_dirs == cfg.claude_dirs and acme.hub_share == "knowledge" and not acme.mirror_to
    assert local_machine_id(acme) != local_machine_id(cfg)  # its hub can't tell it is the same computer
    raw = tomllib.loads((p.home / "config.toml").read_text())
    assert raw["sources"]["claude_dirs"] == [] and raw["sources"]["exclude_projects"] == ["*"]
    assert ops.is_profile_home(p.home) and not ops.is_profile_home(cfg.home)
    with pytest.raises(ops.ProfileError, match="another hub's folder"):
        ops.add_hub(acme, "Nested")
    with pytest.raises(ops.ProfileError, match="would belong to both Acme and Globex"):
        hub_with(cfg, "Globex", [str(tmp_path)])
    with pytest.raises(ops.ProfileError, match="already a hub named"):
        ops.add_hub(cfg, "acme")


def test_removing_a_profile_holds_its_folders(teamenv):  # noqa: F811
    spoke = teamenv["spoke"]
    _analyzed_on_spoke(spoke, title="Acme work", at="2026-10-02T00:00:00Z", lessons=[])
    hub_with(spoke, "Acme", [SPOKE_CWD])
    p, _ = ops.remove_hub(spoke, "Acme")
    assert _owner(spoke) == WAITING and load_registry(spoke.home).choices == {SPOKE_CWD: WAITING}
    assert p.home.is_dir()  # its data stays unless asked
    from interlatch.ingestion.service import sync

    conn = connect(spoke.db_path)
    sync(spoke, conn)  # waiting sessions are routed again: still held
    conn.close()
    assert _owner(spoke) == WAITING
    conn = connect(spoke.db_path)
    assert [w["folder"] for w in owners.waiting(conn)] == [SPOKE_CWD]
    conn.close()


def test_the_commands_add_a_hub_connect_a_group_and_run_for_it(tmp_path, monkeypatch, capsys):
    from interlatch.interfaces.cli.main import main

    monkeypatch.setenv("INTERLATCH_HOME", str(tmp_path / "personal"))
    assert main(["hubs", "add", "Acme"]) == 0
    assert main(["group", "create", "Client work", "--folder", str(tmp_path)]) == 0
    assert main(["group", "connect", "Client work", "Acme"]) == 0
    capsys.readouterr()
    assert main(["group", "where", str(tmp_path / "x")]) == 0
    assert "Acme" in capsys.readouterr().out
    assert main(["hubs"]) == 0 and "group Client work" in capsys.readouterr().out
    assert main(["group", "list"]) == 0 and "Client work → Acme" in capsys.readouterr().out
    p = load_registry(tmp_path / "personal").find("acme")
    assert main(["--hub", "Acme", "config", "path"]) == 0
    assert str(p.home) in capsys.readouterr().out
    assert main(["--hub", "Nope", "push"]) == 1  # from inside Acme too: Personal's registry is found
    assert "hubs: Acme" in capsys.readouterr().err
    assert main(["--hub", "Acme", "sync"]) == 2  # recording is Personal's
    assert "runs for Personal" in capsys.readouterr().err
    monkeypatch.setenv("INTERLATCH_HOME", str(tmp_path / "personal"))
    assert main(["group", "remove", "Client work"]) == 1  # connected: disconnect it first
    assert main(["group", "disconnect", "Client work"]) == 0
    assert main(["group", "where", str(tmp_path / "x")]) == 0
    assert "Waiting" in capsys.readouterr().out


def test_the_hello_names_no_folder_or_repository_a_profile_owns(tmp_path):
    """What a computer tells its hub about itself (the repositories it works in, the folders it added there) leaves
    out what a named profile owns, by folder and by remote pattern."""
    import subprocess

    from test_hub import _make_spoke

    repos = {}
    for name, remote in (("acme-app", "git@github.com:acme-corp/app.git"), ("own-app", "https://github.com/me/own.git")):
        repo = tmp_path / "work" / name
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", remote], check=True)
        repos[name] = str(repo)
    spoke, claude, _ = _make_spoke(tmp_path, "https://hub.example", "tok", cwd=repos["acme-app"],
                                   extra=f'share = "knowledge"\nfolders = {{ "{repos["acme-app"]}" = "/hub/acme", '
                                         f'"{repos["own-app"]}" = "/hub/own" }}\n')
    (claude / "projects" / repos["own-app"].replace("/", "-")).mkdir(parents=True)  # a second project, no session
    roots = client.spoke_roots(spoke)

    def hello():
        return json.dumps(client.hello_info(spoke, roots, repos=client.spoke_repos(spoke, roots), remotes={}))

    assert "acme-app" in hello()
    ops.add_hub(spoke, "Acme", remotes=["github.com/acme-corp/*"])  # by its remote alone
    sent = hello()
    assert "acme-app" not in sent and "acme-corp" not in sent and "/hub/acme" not in sent
    assert "own-app" in sent and "/hub/own" in sent


def test_a_broken_registry_stops_sharing_but_not_recording(teamenv):  # noqa: F811
    spoke = teamenv["spoke"]
    _analyzed_on_spoke(spoke, title="Before", at="2026-10-02T00:00:00Z", lessons=[])
    hub_with(spoke, "Acme", ["/nowhere/acme"])
    (spoke.home / "profiles.toml").write_text("[profiles.broken\n")
    with pytest.raises(HubError, match="nothing sent: .*profiles.toml can't be read"):
        push(spoke)
    assert teamenv["conn"].execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == 0
    _analyzed_on_spoke(spoke, title="Still recorded", at="2026-10-03T00:00:00Z", lessons=[])  # sync keeps working
    conn = connect(spoke.db_path)
    assert conn.execute("SELECT llm_title FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == "Still recorded"
    conn.close()
