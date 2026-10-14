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

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/webp", ".webp")

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
