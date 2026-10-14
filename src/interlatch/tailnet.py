"""Reach the dashboard from your phone and other computers through Tailscale Serve.

Serve gives this computer an HTTPS address inside your tailnet (https://<name>.<tailnet>.ts.net) and forwards it to
the dashboard on 127.0.0.1, which keeps listening on localhost only. Serve adds the visitor's Tailscale login in a
`Tailscale-User-Login` header (and drops one a client tries to send), which the dashboard checks against
`[server] allowed_users`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAC_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


class TailnetError(Exception):
    pass


def find_cli() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    return MAC_APP_CLI if Path(MAC_APP_CLI).exists() else None


@dataclass
class TailnetStatus:
    running: bool = False
    dns_name: str = ""          # this computer's name in the tailnet, e.g. pc.tail1234.ts.net
    login: str = ""             # the Tailscale login this computer is signed in as
    tailnet: str = ""
    ips: list[str] = field(default_factory=list)
    state: str = ""

    @property
    def url(self) -> str:
        return f"https://{self.dns_name}" if self.dns_name else ""


def parse_status(data: dict) -> TailnetStatus:
    me = data.get("Self") or {}
    users = data.get("User") or {}
    user = users.get(str(me.get("UserID"))) or {}
    return TailnetStatus(
        running=data.get("BackendState") == "Running",
        dns_name=str(me.get("DNSName") or "").rstrip(".").lower(),
        login=str(user.get("LoginName") or ""),
        tailnet=str((data.get("CurrentTailnet") or {}).get("Name") or ""),
        ips=[str(ip) for ip in me.get("TailscaleIPs") or []],
        state=str(data.get("BackendState") or ""),
    )


def status(cli: str | None = None) -> TailnetStatus:
    cli = cli or find_cli()
    if not cli:
        raise TailnetError("Tailscale is not installed (https://tailscale.com/download)")
    proc = subprocess.run([cli, "status", "--json"], capture_output=True, text=True, timeout=15)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise TailnetError((proc.stderr or proc.stdout).strip() or "`tailscale status` failed") from None
    return parse_status(data)


def serve_command(cli: str, port: int, *, off: bool = False) -> list[str]:
    if off:
        return [cli, "serve", "--https=443", "off"]
    return [cli, "serve", "--bg", "--https=443", f"http://127.0.0.1:{port}"]


def serve(cli: str, port: int, *, off: bool = False, interactive: bool = True) -> subprocess.CompletedProcess:
    """Turn Serve on (or off) for the dashboard. Interactive runs show Tailscale's own prompts, such as the link to
    enable HTTPS certificates for the tailnet the first time."""
    cmd = serve_command(cli, port, off=off)
    if interactive:
        return subprocess.run(cmd)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def serve_status(cli: str) -> dict:
    try:
        proc = subprocess.run([cli, "serve", "status", "--json"], capture_output=True, text=True, timeout=15)
        return json.loads(proc.stdout or "{}") or {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def serves_port(serve_json: dict, port: int) -> bool:
    """Whether Serve's config forwards some HTTPS address to the dashboard's port."""
    text = json.dumps(serve_json)
    return f"127.0.0.1:{port}" in text or f"localhost:{port}" in text
