"""[storage]: what Interlatch's folder holds, the size it warns at, and older sessions' transcripts kept in another
folder (ingestion/space.py): what moves and what stays, that a moved session still opens, and coming back."""

import gzip
import shutil
from pathlib import Path

import pytest

from interlatch.core.config import load_config, set_config_value
from interlatch.ingestion.service import sync
from interlatch.ingestion import space

from conftest import SID
from test_hub import _serve
from test_team import _call


@pytest.fixture()
def older(synced):
    """A synced archive with a folder for older sessions set: the fixture's session (2026-09-20) is past 30 days."""
    folder = synced["tmp"] / "older"
    folder.mkdir()
    set_config_value(synced["cfg"], "storage", "older_dir", f'"{folder}"')
    set_config_value(synced["cfg"], "storage", "older_after_days", "30")
    synced["cfg"] = load_config(synced["home"])
    synced["older"] = folder
    return synced


def _archive_path(conn) -> Path:
    return Path(conn.execute("SELECT archive_path FROM sessions WHERE id = ?", (SID,)).fetchone()[0])


def _gone(env) -> None:
    """The agent deleted its own transcript, and a sync noticed."""
    main = env["main"]
    shutil.rmtree(main.with_suffix(""), ignore_errors=True)
    main.unlink()
    sync(env["cfg"], env["conn"])
    assert env["conn"].execute("SELECT source_present FROM sessions WHERE id = ?", (SID,)).fetchone()[0] == 0


def test_config_defaults_and_typos(tmp_path):
    home = tmp_path / "h"
    cfg = load_config(home)
    assert (cfg.storage_older_dir, cfg.storage_older_days, cfg.storage_warn_gb) == (None, 90, 0.0)
    set_config_value(cfg, "storage", "older_after_days", '"soon"')
    set_config_value(cfg, "storage", "warn_gb", "-3")
    cfg = load_config(home)
    assert (cfg.storage_older_days, cfg.storage_warn_gb) == (90, 0.0)  # a typo never stops Interlatch


def test_usage_tells_the_parts_apart_and_warns_past_the_limit(synced):
    cfg = synced["cfg"]
    (cfg.home / "backups").mkdir()
    (cfg.home / "backups" / "snapshot.tar.gz").write_bytes(b"x" * 5000)
    used = space.usage(cfg, synced["conn"])
    sizes = {p["key"]: p["bytes"] for p in used["parts"]}
    assert sizes["backups"] == 5000 and sizes["database"] >= cfg.db_path.stat().st_size and sizes["archive"] > 0
    assert used["total"] == sum(sizes.values()) and not used["over"] and used["older"]["dir"] == ""
    cfg.storage_warn_gb = 1e-6  # about 1 KB
    assert space.usage(cfg)["over"]


def test_a_session_whose_transcript_is_still_there_stays(older):
    res = space.move_older(older["cfg"])
    assert res["sessions"] == 0 and _archive_path(older["conn"]).is_relative_to(older["cfg"].archive_dir)


def test_a_recent_session_stays(older):
    _gone(older)
    older["cfg"].storage_older_days = 36500
    assert space.move_older(older["cfg"])["sessions"] == 0


def test_an_older_session_moves_with_its_folder_and_still_opens(older):
    from interlatch.interfaces.exports.sessions import original_file
    from interlatch.sources.claude.files import subagent_files, session_dir_for

    conn, cfg = older["conn"], older["cfg"]
    _gone(older)
    before = _archive_path(conn)
    text = gzip.decompress(before.read_bytes())
    assert space.move_older(cfg, dry_run=True)["sessions"] == 1 and before.exists()  # says so, moves nothing
    res = space.move_older(cfg)
    assert res["sessions"] == 1 and res["bytes"] > 0 and not res["errors"]
    after = _archive_path(conn)
    assert after == older["older"] / before.relative_to(cfg.archive_dir) and after.is_file()
    assert not before.exists() and not session_dir_for(before).exists()
    assert subagent_files(session_dir_for(after))  # the folder beside it went along
    assert gzip.decompress(after.read_bytes()) == text
    assert original_file(conn, SID)[1] == text  # the export reads it where it is now
    assert conn.execute("SELECT COUNT(*) FROM files_state WHERE archive_path = ?", (str(after),)).fetchone()[0] == 1
    assert (before.parent / "memory" / "deploy.md").exists()  # not a session's: it stays
    assert space.usage(cfg, conn)["older"]["sessions"] == 1
    assert space.move_older(cfg)["sessions"] == 0  # nothing left to move
    assert sync(cfg, conn).errors == []  # a sync leaves a moved session alone
    assert _archive_path(conn) == after


def test_a_copy_that_does_not_match_moves_nothing(older, monkeypatch):
    conn, cfg = older["conn"], older["cfg"]
    _gone(older)
    before = _archive_path(conn)
    real = shutil.copyfile
    monkeypatch.setattr(space.shutil, "copyfile", lambda src, dst: real(src, dst) and Path(dst).write_bytes(b"torn"))
    res = space.move_older(cfg)
    assert res["sessions"] == 0 and "doesn't match" in res["errors"][0]
    assert _archive_path(conn) == before and before.is_file()
    assert not [p for p in older["older"].rglob("*") if p.is_file()]  # no half copy left behind


def test_a_folder_that_went_away_is_said_not_guessed(older):
    conn, cfg = older["conn"], older["cfg"]
    _gone(older)
    before = _archive_path(conn)
    older["older"].rmdir()
    res = space.move_older(cfg)
    assert res["sessions"] == 0 and "not a folder" in res["note"] and before.is_file()


def test_bringing_everything_back(older):
    conn, cfg = older["conn"], older["cfg"]
    _gone(older)
    before = _archive_path(conn)
    space.move_older(cfg)
    res = space.bring_back(cfg)
    assert res["sessions"] == 1 and _archive_path(conn) == before and before.is_file()
    assert not [p for p in older["older"].rglob("*") if p.is_file()]


def test_a_transcript_that_comes_back_is_not_archived_twice(older):
    """Restored from a backup: the copy in the older folder is the archive's, so no second copy is made here."""
    conn, cfg = older["conn"], older["cfg"]
    keep = older["tmp"] / "keep"
    shutil.copytree(older["main"].parent, keep)
    _gone(older)
    before = _archive_path(conn)
    space.move_older(cfg)
    shutil.copytree(keep, older["main"].parent, dirs_exist_ok=True)  # copystat: same size and time as before
    report = sync(cfg, conn)
    assert report.files_archived == 0 and not before.exists()
    assert _archive_path(conn).is_relative_to(older["older"])


def test_the_background_run_moves_them_and_says_so(older, caplog):
    from interlatch.application.workflows import run_worker

    cfg = older["cfg"]
    _gone(older)
    report = run_worker(cfg, analyze=False, synthesize=False, export=None)
    assert report.moved.startswith("1 moved") and "older sessions: 1 moved" in report.summary()
    assert run_worker(cfg, analyze=False, synthesize=False, export=None).moved is None  # nothing more: not mentioned
    cfg.storage_warn_gb = 1e-6
    with caplog.at_level("WARNING", logger="interlatch.worker"):
        run_worker(cfg, analyze=False, synthesize=False, export=None)
    assert "more than the" in caplog.text


def test_a_folder_inside_interlatchs_own_is_refused(env):
    cfg = env["cfg"]
    inside = cfg.home / "archive" / "old"
    inside.mkdir(parents=True)
    for bad, why in ((str(inside), "outside"), (str(cfg.home.parent), "outside"), ("relative/path", "full path"),
                     (str(env["tmp"] / "nowhere"), "not a folder")):
        with pytest.raises(space.StorageError, match=why):
            space.check_older_dir(cfg, bad)


def test_settings_from_the_dashboard(older):
    """Settings › Storage: the folder is checked before it is saved, and can't change while sessions are kept in it."""
    cfg, home = older["cfg"], older["home"]
    _gone(older)
    other = older["tmp"] / "other"
    other.mkdir()
    _app, httpd, url = _serve(cfg)
    try:
        code, sp = _call(url, "/api/storage")
        assert code == 200 and sp["older"]["dir"] == str(older["older"]) and sp["older"]["sessions"] == 0 and sp["total"] > 0
        r = _call(url, "/api/storage/save", {"older_dir": str(home / "archive"), "older_after_days": "30", "warn_gb": ""})[1]
        assert not r["ok"] and "outside" in r["error"]
        r = _call(url, "/api/storage/save", {"older_dir": str(older["older"]), "older_after_days": "0", "warn_gb": ""})[1]
        assert not r["ok"] and load_config(home).storage_older_days == 30
        r = _call(url, "/api/storage/save", {"older_dir": str(older["older"]), "older_after_days": "45", "warn_gb": "20"})[1]
        assert r["ok"] and r["space"]["warn_gb"] == 20
        now = load_config(home)
        assert (now.storage_older_days, now.storage_warn_gb) == (45, 20.0)
        space.move_older(now)
        r = _call(url, "/api/storage/save", {"older_dir": str(other), "older_after_days": "45", "warn_gb": "20"})[1]
        assert not r["ok"] and "bring them back" in r["error"] and load_config(home).storage_older_dir == older["older"]
        tailnet = {"X-Forwarded-For": "203.0.113.5", "Tailscale-User-Login": "me@github"}  # let in, but not here
        assert _call(url, "/api/storage", None, tailnet)[0] == 403
        assert _call(url, "/api/storage/move", {"back": True}, tailnet)[0] == 403
    finally:
        httpd.shutdown()
