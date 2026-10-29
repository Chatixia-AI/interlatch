"""The browser extension that sends claude.ai and chatgpt.com chats to the dashboard as you use the sites (its files:
src/interlatch/browser_extension/, the server side: chat_import.capture).

It ships inside the package, so `uv tool upgrade` brings a newer one, but the package's folder moves when uv
reinstalls with another Python. Chrome loads an unpacked extension from one folder for good, so the dashboard keeps a
copy in <interlatch home>/browser-extension and brings it up to this version's files; Chrome reads them again when
it restarts, or with the reload button on chrome://extensions.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("interlatch.browser_extension")

BUNDLED = Path(__file__).resolve().parent.parent / "browser_extension"
FOLDER = "browser-extension"


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"} if root.is_dir() else {}


def folder(cfg) -> Path | None:
    """The folder to load in Chrome (Load unpacked), holding this version's files; None when this install has none."""
    want = _files(BUNDLED)
    if "manifest.json" not in want:
        return None
    dst = cfg.home / FOLDER
    try:
        have = _files(dst)
        if have != want:
            for rel in have.keys() - want.keys():
                (dst / rel).unlink(missing_ok=True)
            for rel, data in want.items():
                if have.get(rel) != data:
                    (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                    (dst / rel).write_bytes(data)
    except OSError as exc:
        log.warning("could not update the browser extension in %s: %s", dst, exc)
    return dst
