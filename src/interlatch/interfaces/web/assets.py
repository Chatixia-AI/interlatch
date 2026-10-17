"""The dashboard page's own files (web/), read once when the dashboard starts, so the page always matches the code
answering its API; the build hash that tells open tabs to reload, and the policy the page runs under."""

from __future__ import annotations

import mimetypes
from pathlib import Path

WEB_DIR = Path(__file__).parents[2] / "web"  # the package's web/: interlatch/web

# The dashboard page's Content-Security-Policy: scripts, styles, images, fonts and requests from the dashboard itself
# only, no inline script or style (index.html loads boot.js; app.js sets styles through the CSSOM), no plugins, no
# <base>. A mistake that lets a transcript's text into the page as HTML can't run anything. The macOS app's window
# (?app=mac, at this computer) also allows eval: pywebview builds its bridge to the window (theme, drag, zoom) with
# new Function.
PAGE_CSP = "default-src 'self'; script-src 'self'{eval}; object-src 'none'; base-uri 'none'; form-action 'self'"

# The types of the dashboard's own files, fixed. mimetypes reads Windows' registry, where an editor's installer can
# map .js to text/plain: with nosniff, the browser then ran none of the page's scripts.
WEB_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".png": "image/png",
             ".svg": "image/svg+xml", ".webp": "image/webp", ".woff2": "font/woff2",
             ".webmanifest": "application/manifest+json"}


def web_type(name: str) -> str:
    """The Content-Type of the dashboard's file `name`."""
    ext = Path(name).suffix.lower()
    if ext in WEB_TYPES:
        return WEB_TYPES[ext]
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return ctype + "; charset=utf-8" if ctype.startswith("text/") else ctype

# The page is served from the files as they were when this process started, so it always matches the code
# answering its API calls: an upgrade (or an edit to an editable install) takes effect with the restart.
WEB_FILES = {p.name: p.read_bytes() for p in sorted(WEB_DIR.glob("*")) if p.is_file()}


def _ui_build() -> str:
    """Content hash of the web assets; open tabs reload themselves when it changes (after an upgrade)."""
    import hashlib

    h = hashlib.sha1()
    for body in WEB_FILES.values():
        h.update(body)
    return h.hexdigest()[:12]


UI_BUILD = _ui_build()
