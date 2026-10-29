"""Sources: the coding agents Interlatch records and the MCP clients it serves, and importing and screening chat
exports."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from ....storage.sqlite import connect
from ..context import Context
from .status import action_sync


def connectors(ctx: Context) -> list[dict]:
    from ....runtime.connectors import all_status

    return all_status(ctx.cfg, ctx.conn)


def mcp_info() -> dict:
    """What the MCP page shows: the command that starts the server, its tools, and config to paste."""
    from ....runtime.connectors import mcp_server_entry
    from ....runtime.install import MCP_NAME, executable
    from ...mcp import INSTRUCTIONS, TOOLS

    entry = mcp_server_entry(executable())
    cmd, args = entry["command"], entry["args"]
    toml_args = ", ".join(json.dumps(a) for a in args)
    shell = " ".join(shlex.quote(x) for x in [cmd, *args])
    return {
        "command": cmd, "args": args, "instructions": INSTRUCTIONS,
        "tools": [{"name": t["name"], "description": t["description"],
                   "params": sorted(t["inputSchema"].get("properties", {}),  # required first, then as declared
                                    key=lambda k: k not in t["inputSchema"].get("required", [])),
                   "required": t["inputSchema"].get("required", [])} for t in TOOLS],
        "snippets": {
            "json": json.dumps({"mcpServers": {MCP_NAME: entry}}, indent=2),
            "vscode": json.dumps({"servers": {MCP_NAME: {"type": "stdio", **entry}}}, indent=2),
            "codex": f'[mcp_servers.{MCP_NAME}]\ncommand = {json.dumps(cmd)}\nargs = [{toml_args}]\n',
            "claude": f"claude mcp add --scope user {MCP_NAME} -- {shell}",
        },
    }


def mcp_clients() -> list[dict]:
    from ....runtime.connectors import mcp_clients_status

    return mcp_clients_status()


def action_connector(ctx: Context, name: str, action: str) -> dict:
    from ....core.config import load_config
    from ....runtime.connectors import MCP_CLIENTS, connect, disconnect
    from ....runtime.install import executable

    if name in MCP_CLIENTS:  # only another tool's MCP config changes: no config reload, nothing to sync
        return {"actions": connect(ctx.cfg, name, executable()) if action == "connect" else disconnect(ctx.cfg, name),
                "sync_started": False}
    if action == "connect":
        actions = connect(ctx.cfg, name, executable())
    elif action == "disconnect":
        actions = disconnect(ctx.cfg, name)
    else:
        raise KeyError(action)
    ctx.cfg = load_config(ctx.cfg.home)  # the connector rewrote config.toml
    started = action_sync(ctx) if action == "connect" else False
    return {"actions": actions, "sync_started": started}


def imports(ctx: Context, here: bool = False) -> dict:
    """Per chat format, its imports and captures; from this computer itself also the browser extension's folder."""
    from ....ingestion.chat_import import import_status
    from ....runtime import browser_extension

    out = import_status(ctx.conn)
    if here:
        ext = browser_extension.folder(ctx.cfg)
        for f in out.values():
            f["extension_dir"] = str(ext) if ext else None
    return out


def action_capture(ctx: Context, body: dict) -> tuple[dict, int]:
    """A chat the browser extension sends (chat_import.capture); a running sync asks it to send again later."""
    from ....ingestion.chat_import import CaptureBusy, ExportError, capture

    if not isinstance(body, dict):
        return {"error": "bad json"}, 400
    try:
        return capture(ctx.cfg, ctx.conn, str(body.get("format") or ""), body.get("conversation")), 200
    except CaptureBusy as exc:
        return {"error": str(exc), "retry": True}, 409
    except ExportError as exc:
        return {"error": str(exc)}, 400


def action_import(ctx: Context, upload: Path, name: str | None = None) -> bool:
    from ....ingestion.chat_import import import_export, summary

    def job(progress):
        conn = connect(ctx.cfg.db_path)
        try:
            return summary(import_export(ctx.cfg, conn, upload, progress=progress, name=name))
        finally:
            conn.close()
            upload.unlink(missing_ok=True)  # the zip also holds the account files (name, email): not kept

    return ctx.jobs.start("import", job)


def action_screen(ctx: Context, source: str | None, redo: bool = False) -> bool:
    """Screen imported chats in the background: which are worth analyzing (screen.py)."""
    from ....analysis.screen import screen_chats

    def job(progress):
        conn = connect(ctx.cfg.db_path)
        try:
            report = screen_chats(ctx.cfg, conn, source=source, redo=redo, progress=progress)
        finally:
            conn.close()
        if report.left and not report.screened:
            raise RuntimeError(report.error or "nothing could be screened")
        return report.summary()

    return ctx.jobs.start("screen", job)


def action_screen_queue(ctx: Context, source: str | None, maybe: bool = False) -> dict:
    from ....analysis.screen import queue

    return {"queued": queue(ctx.conn, source=source, maybe=maybe), "per_run": ctx.cfg.analysis.max_per_run}
