"""This computer's identity: an id made once and a name (machine.json), which archives, hubs and mirrors
record it by."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import uuid

from .config import Config

MACHINE_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _computer_name() -> str:
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=3)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    name = socket.gethostname()
    return name[:-6] if name.endswith(".local") else name


def local_machine(cfg: Config) -> dict:
    """This computer's id and name, made once and kept in <home>/machine.json (rename it there)."""
    path = cfg.home / "machine.json"
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and MACHINE_RE.match(str(data.get("id", ""))):
            return {"id": data["id"], "name": str(data.get("name") or _computer_name())}
    except (OSError, ValueError):
        pass
    data = {"id": str(uuid.uuid4()), "name": _computer_name()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    return data


def local_machine_id(cfg: Config) -> str:
    return local_machine(cfg)["id"]
