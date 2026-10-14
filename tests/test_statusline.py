"""The status-line collector records real context and plan usage, and leaves the user's status line as it was."""

import io
import json
import subprocess
import sys
from datetime import datetime, timezone

from interlatch import statusline
from interlatch.install import install_statusline, settings_path, statusline_installed, uninstall_statusline
from interlatch.server import App

from conftest import SID

T0 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
RESET_A, RESET_B = 1790000000, 1790018000  # two 5-hour windows


def _payload(ctx=8, five=None, seven=None, reset=RESET_A, sid=SID):
    p = {"session_id": sid, "transcript_path": "/t.jsonl", "cwd": "/w", "version": "2.1.282",
         "model": {"id": "claude-opus-5-5", "display_name": "Opus"}, "cost": {"total_cost_usd": 0.42},
         "context_window": {"context_window_size": 200000, "used_percentage": ctx}}
    if five is not None:
        p["rate_limits"] = {"five_hour": {"used_percentage": five, "resets_at": reset}}
        if seven is not None:
            p["rate_limits"]["seven_day"] = {"used_percentage": seven, "resets_at": reset + 500000}
    return p


def test_merge_tracks_peaks_and_how_far_each_window_moved():
    s = statusline.merge({}, _payload(ctx=10, five=20, seven=40), T0)
    s = statusline.merge(s, _payload(ctx=60, five=26, seven=41), T0)
    s = statusline.merge(s, _payload(ctx=15), T0)  # rate_limits absent this time: earlier readings are kept
    s = statusline.merge(s, _payload(ctx=20, five=3, reset=RESET_B), T0)  # a new 5-hour window began
    s = statusline.merge(s, _payload(ctx=25, five=5, reset=RESET_B), T0)
    assert s["context"] == {"size": 200000, "used_pct": 25, "peak_pct": 60}
    assert s["limits"]["five_hour"]["moved_pct"] == 8.0  # 20→26 in the first window, 3→5 in the second
    assert s["limits"]["seven_day"]["moved_pct"] == 1.0
    assert s["cc_cost_usd"] == 0.42 and s["model"] == "claude-opus-5-5"
    assert statusline.merge({}, {"session_id": "x", "context_window": {"used_percentage": None},
                                 "rate_limits": {"five_hour": {"used_percentage": "junk", "resets_at": RESET_A}}}, T0).get("limits") is None


def _run(env, payload, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
    out = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out, write_through=True))
    code = statusline.main()
    sys.stdout.flush()
    return code, out.getvalue().decode()


def test_without_a_status_line_it_prints_its_own(env, monkeypatch):
    code, out = _run(env, _payload(ctx=8, five=23.5, seven=41), monkeypatch)
    assert code == 0 and out == "Opus · 8% context · 5h 24% · 7d 41%"
    rec = json.loads((statusline.samples_dir() / f"{SID}.json").read_text())
    assert rec["limits"]["five_hour"]["used_pct"] == 23.5


def test_it_runs_the_users_own_status_line_unchanged(env, monkeypatch):
    statusline.state_dir().mkdir(parents=True, exist_ok=True)
    statusline.wrapped_path().write_text(json.dumps({"type": "command", "command": "cat > /dev/null; printf 'my line'"}))
    assert _run(env, _payload(), monkeypatch) == (0, "my line")
    assert (statusline.samples_dir() / f"{SID}.json").exists()


def test_install_wraps_and_uninstall_restores(env):
    cfg = env["cfg"]
    path = settings_path(cfg)
    path.write_text(json.dumps({"statusLine": {"type": "command", "command": "~/bin/mine.sh", "padding": 1}, "model": "opus"}))
    install_statusline(cfg, "/usr/local/bin/chronicle")
    settings = json.loads(path.read_text())
    assert settings["statusLine"] == {"type": "command", "command": "/usr/local/bin/chronicle statusline", "padding": 1}
    assert statusline_installed(cfg) and json.loads(statusline.wrapped_path().read_text())["command"] == "~/bin/mine.sh"
    install_statusline(cfg, "/opt/chronicle")  # re-install: the path moves, the saved status line stays
    assert json.loads(statusline.wrapped_path().read_text())["command"] == "~/bin/mine.sh"
    uninstall_statusline(cfg)
    assert json.loads(path.read_text()) == {"statusLine": {"type": "command", "command": "~/bin/mine.sh", "padding": 1}, "model": "opus"}
    assert not statusline.wrapped_path().exists() and uninstall_statusline(cfg) == []


def test_uninstall_without_an_original_removes_it(env):
    cfg = env["cfg"]
    path = settings_path(cfg)
    path.write_text("{}")
    install_statusline(cfg, "/usr/local/bin/chronicle")
    uninstall_statusline(cfg)
    assert json.loads(path.read_text()) == {}


def test_sync_imports_records_and_the_dashboard_shows_them(synced):
    from interlatch.ingest import sync

    conn, cfg = synced["conn"], synced["cfg"]
    s = statusline.merge({}, _payload(ctx=30, five=20, seven=40, reset=4102444800), T0)  # resets in 2100
    s = statusline.merge(s, _payload(ctx=70, five=32, seven=42, reset=4102444800), T0)
    statusline._write_atomic(statusline.samples_dir() / f"{SID}.json", s)
    statusline._write_atomic(statusline.samples_dir() / "not-synced-yet.json", statusline.merge({}, _payload(sid="later"), T0))
    sync(cfg, conn)
    usage = statusline.session_usage(conn.execute("SELECT statusline_json FROM sessions WHERE id = ?", (SID,)).fetchone()[0])
    assert usage["context_peak_pct"] == 70 and usage["limits_moved"] == {"five_hour": 12.0, "seven_day": 2.0}
    assert (statusline.samples_dir() / "not-synced-yet.json").exists()  # kept for when its session arrives

    app = App(cfg)
    assert app.session(SID)["usage"]["limits_moved"]["five_hour"] == 12.0
    plan = app.status()["statusline"]["plan"]
    assert plan["limits"]["five_hour"]["used_pct"] == 32 and plan["as_of"] == "2026-09-20T10:00:00Z"
    other = conn.execute("SELECT id FROM sessions WHERE id != ? LIMIT 1", (SID,)).fetchone()
    if other:
        assert app.session(other[0])["usage"] is None  # not recorded stays unknown


def test_expired_windows_are_not_reported(synced):
    from interlatch.storage.kv import kv_set

    conn = synced["conn"]
    kv_set(conn, "plan_usage_latest", json.dumps({"as_of": "2026-01-01T00:00:00Z", "limits": {
        "five_hour": {"used_pct": 90, "resets_at": "2026-01-01T05:00:00Z"},
        "seven_day": {"used_pct": 10, "resets_at": "2100-01-01T00:00:00Z"}}}))
    assert list(statusline.plan_usage(conn)["limits"]) == ["seven_day"]


def test_the_command_is_fast_and_never_fails(env, tmp_path):
    payload = json.dumps(_payload(five=5)).encode()
    for data in (payload, b"not json", b""):
        done = subprocess.run([sys.executable, "-m", "interlatch", "statusline"], input=data, capture_output=True, timeout=20,
                              check=False)
        assert done.returncode == 0, done.stderr
    assert b"argparse" not in done.stderr
