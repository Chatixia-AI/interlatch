"""Settings: updates, the analysis backend, agents and model providers, the language knowledge is written in, the
menu-bar icon."""

from __future__ import annotations

import json
import re
import threading

from ....analysis.runners.factory import BACKENDS, make_runner
from ....core.config import LANGUAGES
from ....core.i18n import tr
from ....storage.sqlite import connect
from ..app import log
from ..context import Context
from .common import runner_for


def update_info(ctx: Context, remote: bool) -> dict:
    from ....runtime.update import check, recall, remember

    recall(ctx.conn)
    info = check(remote, detail=True)
    if remote:
        remember(ctx.conn)
    return {**info, "check_daily": ctx.cfg.update_check_daily, "notify": ctx.cfg.update_notify}


def _client_value(value) -> str:
    """An editor's name and version as a client sends it, kept to a short plain line for the report's table."""
    return re.sub(r"[^\w .()+-]", "", str(value or ""))[:60].strip()


def feedback(ctx: Context, q: dict, full: bool) -> dict:
    """Settings › Feedback (and the VS Code extension's Send Feedback): the GitHub page to open, with this install's
    details filled in. `full`: the system's details too (here, or an admin of a hub); a hub's other people get its
    version and part only, which the dashboard shows them anyway."""
    from .... import __version__
    from ....core import i18n
    from ....runtime.feedback import KINDS, details, hub_role, report

    kind = q.get("kind") if q.get("kind") in KINDS else "bug"
    rows = details(ctx.cfg, ctx.conn) if full else [("Interlatch", __version__), ("Hub", hub_role(ctx.cfg))]
    if q.get("client") == "vscode":
        via = "the VS Code extension"
        rows += [(label, v) for label, key in (("Editor", "editor"), ("Extension", "extension"))
                 if (v := _client_value(q.get(key)))]
    else:
        via = "the dashboard"
        rows.append(("Dashboard language", i18n.lang.get()))
    return report(kind, rows, via=via)


def _maybe_check_daily(ctx: Context) -> None:
    """With [updates] check_daily on, ask PyPI once a day, in the background, while a dashboard polls."""
    from ....runtime.update import check_due, fetch_latest, remember

    if not ctx.cfg.update_check_daily or not check_due() or not ctx.state.update_check.acquire(blocking=False):
        return

    def run():
        conn = None
        try:
            fetch_latest()
            conn = connect(ctx.cfg.db_path)
            remember(conn)
        except Exception:
            log.exception("daily update check failed")
        finally:
            if conn is not None:
                conn.close()
            ctx.state.update_check.release()

    threading.Thread(target=run, name="update-check", daemon=True).start()


def action_update_setting(ctx: Context, key: str, on: bool) -> dict:
    """Status › Updates switches: check_daily (while the dashboard is open) and notify (from the background)."""
    from ....core.config import load_config, set_config_value

    set_config_value(ctx.cfg, "updates", key, "true" if on else "false")
    ctx.cfg = load_config(ctx.cfg.home)
    _maybe_check_daily(ctx)
    return update_info(ctx, remote=False)


def analysis_backends(ctx: Context) -> dict:
    """What analyzes sessions, and every choice: the coding agents (installed or not) and the model providers
    (set up or not). Never an API key, only where one comes from."""
    return {"backend": make_runner(ctx.cfg).name,
            "choices": [runner_for(ctx, name).describe() for name in BACKENDS],
            "language": ctx.cfg.analysis.language,  # what Interlatch writes knowledge in, not the dashboard's language
            "languages": [{"code": code, "label": label} for code, label in LANGUAGES.items()]}


def action_backend(ctx: Context, backend: str) -> dict:
    from ....core.config import load_config, set_config_value

    if backend not in BACKENDS:
        return {"error": tr("unknown analysis backend {backend!r}", backend=backend)}
    set_config_value(ctx.cfg, "analysis", "backend", json.dumps(backend))
    ctx.cfg = load_config(ctx.cfg.home)
    return analysis_backends(ctx)


def action_agent(ctx: Context, body: dict) -> dict:
    """Change a coding agent's model and effort (AGENT_SETTINGS). An empty Codex or OpenCode model means that agent's
    default."""
    from ....core.config import load_config, set_config_value

    name = str(body.get("agent") or "")
    allowed = AGENT_SETTINGS.get(name)
    if not allowed:
        return {"error": tr("unknown analysis backend {backend!r}", backend=name)}
    values = body.get("settings") if isinstance(body.get("settings"), dict) else {}
    refused = []
    for k, v in values.items():
        v = str(v if v is not None else "").strip()
        ok = k in allowed and len(v) <= 120 and not any(c.isspace() for c in v)
        if k == "effort":
            ok = ok and v in EFFORTS
        elif name == "opencode":
            ok = ok and (not v or "/" in v.strip("/"))  # provider/model, or empty for OpenCode's default
        elif not (name == "codex" and k == "model"):
            ok = ok and bool(v)  # Claude needs a model for each job
        if not ok:
            refused.append(k)
            continue
        set_config_value(ctx.cfg, *allowed[k], json.dumps(v))
    ctx.cfg = load_config(ctx.cfg.home)
    out = analysis_backends(ctx)
    if refused:
        out["error"] = tr("not saved: {keys}", keys=", ".join(refused))
    return out


def action_provider(ctx: Context, body: dict) -> dict:
    """Save a model provider's settings ([providers.<name>]) and, when given, its API key ("" forgets it). For
    IBM Bob, only the key."""
    from ....analysis.runners.settings import KEY_ENVS, PROVIDERS, save_settings, set_key
    from ....core.config import load_config

    name = str(body.get("provider") or "")
    if name not in KEY_ENVS:
        return {"error": tr("unknown analysis backend {backend!r}", backend=name)}
    values = body.get("settings") if isinstance(body.get("settings"), dict) and name in PROVIDERS else {}
    refused = save_settings(ctx.cfg, name, values)
    if "key" in body and body["key"] is not None:
        set_key(ctx.cfg, name, str(body["key"]))
    ctx.cfg = load_config(ctx.cfg.home)
    out = analysis_backends(ctx)
    if refused:
        out["error"] = tr("not saved: {keys}", keys=", ".join(refused))
    return out


def action_provider_test(ctx: Context, name: str) -> dict:
    """One small call to a provider or agent, with its main model: do the endpoint, the key and the model work?"""
    from ....analysis.runners.base import LLMError

    if name not in BACKENDS:
        return {"error": tr("unknown analysis backend {backend!r}", backend=name)}
    runner = runner_for(ctx, name)
    if not runner.available():
        return {"error": runner.unavailable_reason()}
    try:
        res = runner.check()
    except LLMError as exc:
        return {"error": str(exc)[:600]}
    return {"ok": res.data.get("ok") is True, "model": res.model, "ms": res.duration_ms,
            "tokens": res.input_tokens + res.output_tokens}


def provider_models(ctx: Context, name: str) -> dict:
    from ....analysis.runners.settings import PROVIDERS

    if name not in PROVIDERS:
        return {"models": []}
    return {"models": runner_for(ctx, name).list_models()}


def action_language(ctx: Context, lang: str) -> dict:
    """[analysis] language: what summaries, knowledge and proposed lines are written in from now on."""
    from ....core.config import load_config, set_config_value

    if lang not in LANGUAGES:
        return {"error": tr("unknown language {lang!r}", lang=lang)}
    set_config_value(ctx.cfg, "analysis", "language", json.dumps(lang))
    ctx.cfg = load_config(ctx.cfg.home)
    return analysis_backends(ctx)


def action_weekly_day(ctx: Context, day: str) -> dict:
    """Settings › Re-analysis: [synthesis] weekly_day, the day weekly re-analyses run."""
    from ....core.config import load_config, set_config_value
    from ....knowledge.weekly import DAYS
    from .projects import reanalysis

    if day not in DAYS:
        return {**reanalysis(ctx), "error": tr("not saved: {keys}", keys="weekly_day")}
    set_config_value(ctx.cfg, "synthesis", "weekly_day", json.dumps(day))
    ctx.cfg = load_config(ctx.cfg.home)
    return reanalysis(ctx)


def action_game(ctx: Context, on: bool) -> dict:
    """Settings › Appearance's switch: game mode ([game] enabled), Innerlatch and its door. Off keeps what was played
    (the Party, the sign-in) and sends nothing more to the leaderboard."""
    from ....core.config import load_config, set_config_value

    set_config_value(ctx.cfg, "game", "enabled", "true" if on else "false")
    ctx.cfg = load_config(ctx.cfg.home)
    return {"game": ctx.cfg.game_enabled}


def action_menu_bar(ctx: Context, on: bool) -> dict:
    """Status › Recording's switch: the macOS menu-bar icon on or off, adding the `app` extra when it is missing."""
    from ....core.config import load_config
    from ....runtime.platforms import menubar

    info = menubar.setting_info(ctx.cfg)
    if not info["supported"]:
        return {"started": False, "error": tr("The menu-bar icon is for a command-line install on macOS")}
    if on and not info["app_extra"] and not info["can_install"]:
        return {"started": False, "error": tr("The menu-bar icon needs the app extra: {command}", command=info["command"])}
    restarts = menubar.restarts(on, info)
    busy = [k for k, j in ctx.jobs.snapshot().items() if j["state"] == "running" and k != "menu-bar"]
    if restarts and busy:  # the restart would cut the running job off
        return {"started": False, "error": tr("Wait for {job} to finish first", job=busy[0])}

    def job(progress):
        out = menubar.apply_setting(ctx.cfg, on, progress)
        ctx.cfg = load_config(ctx.cfg.home)
        return out

    return {"started": ctx.jobs.start("menu-bar", job), "restarts": restarts}


def action_update(ctx: Context) -> dict:
    from ....runtime.update import run_update

    busy = [k for k, j in ctx.jobs.snapshot().items() if j["state"] == "running" and k != "update"]
    if busy:  # updating restarts the dashboard, which would cut the running job off
        return {"started": False, "error": tr("Wait for {job} to finish first", job=busy[0])}
    return {"started": ctx.jobs.start("update", run_update)}


# What the dashboard may change for each coding agent: setting -> (config section, key). Effort is shared.
AGENT_SETTINGS = {
    "claude": {"model": ("analysis", "model"), "synthesis_model": ("synthesis", "model"),
               "screen_model": ("analysis", "screen_model"), "effort": ("analysis", "effort")},
    "codex": {"model": ("analysis", "codex_model"), "effort": ("analysis", "effort")},
    "opencode": {"model": ("analysis", "opencode_model")},
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
