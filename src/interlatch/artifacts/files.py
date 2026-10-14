"""Opening what a session made: its content for the dashboard's viewer (under a sandboxing policy), or the file
itself in its app or in Finder / Explorer."""

from __future__ import annotations

import os

from ..core.artifact_records import EXT_KIND, digest
from .queries import PREVIEW_TYPES, artifact_by_id, openable

OPEN_MAX = 60_000_000
_TEXT_EXT = {".md", ".markdown", ".mdx", ".rst", ".csv", ".mmd", ".mermaid", ".puml", ".excalidraw", ".drawio", ".txt"}
# an agent's HTML page keeps its own scripts (charts, interactivity) but runs in a sandbox: an opaque origin, with no
# way into Interlatch's data, cookies or API
HTML_POLICY = "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms allow-modals allow-downloads"
IMAGE_POLICY = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"
TEXT_POLICY = "default-src 'none'; sandbox"


def written_content(a: dict) -> str | None:
    """The text a session wrote to a file, read back from the archived transcript (for a file no longer on disk)."""
    from pathlib import Path

    from ..core.util import iter_jsonl

    archive, path, tid = a.get("archive_path"), a["path"], a.get("tool_use_id")
    if not archive or not os.path.exists(archive):
        return None
    found: list[str] = []
    if a.get("agent") == "claude":
        from ..sources.claude.files import session_dir_for, subagent_files

        main = Path(archive)
        for f in [main, *subagent_files(session_dir_for(main))]:
            for d in iter_jsonl(f):
                msg = d.get("message") if isinstance(d.get("message"), dict) else {}
                for b in msg.get("content") if d.get("type") == "assistant" and isinstance(msg.get("content"), list) else []:
                    if not isinstance(b, dict) or b.get("type") != "tool_use" or b.get("name") != "Write":
                        continue
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    if (b.get("id") == tid if tid else inp.get("file_path") == path) and isinstance(inp.get("content"), str):
                        found.append(inp["content"])
    elif a.get("agent") == "codex":  # a FileChange item carries a new file's content
        for d in iter_jsonl(Path(archive)):
            p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
            item = p.get("item") if p.get("type") == "item_completed" and isinstance(p.get("item"), dict) else {}
            if item.get("type") != "FileChange" or not isinstance(item.get("changes"), dict):
                continue
            for k, ch in item["changes"].items():
                if isinstance(ch, dict) and str(ch.get("type")).lower() == "add" and isinstance(ch.get("content"), str) \
                        and (k == path or path.endswith("/" + k)):
                    found.append(ch["content"])
    exact = [c for c in found if a.get("sha256") and digest(c)[1] == a["sha256"]]
    return (exact or found or [None])[-1]


def open_file(conn, artifact_id: int, local_machine: str | None = None) -> dict | None:
    """What "Open" shows: the file as it is on disk or, once it is gone, as the agent wrote it. Returns the bytes, their
    type, whether a browser can show them (else they download), and the content policy to send with them."""
    a = artifact_by_id(conn, artifact_id, local_machine)
    if a is None or not openable(a):
        return None
    data, source = None, "disk"
    if a["status"] in ("present", "changed"):
        try:
            if os.path.getsize(a["path"]) > OPEN_MAX:
                return None
            with open(a["path"], "rb") as f:
                data = f.read()
        except OSError:
            data = None
    if data is None:
        text = written_content(a)
        if text is None:
            return None
        data, source = text.encode("utf-8", "surrogatepass"), "archive"
    ext = os.path.splitext(a["path"])[1].lower()
    out = {"body": data, "name": os.path.basename(a["path"]), "source": source, "inline": True}
    if ext in (".html", ".htm"):
        return {**out, "ctype": "text/html; charset=utf-8", "policy": HTML_POLICY}
    if ext in PREVIEW_TYPES:
        return {**out, "ctype": PREVIEW_TYPES[ext], "policy": IMAGE_POLICY}
    if ext == ".pdf":  # the browser's PDF viewer refuses to run in a sandbox; a PDF's scripts are its own viewer's business
        return {**out, "ctype": "application/pdf", "policy": None}
    if ext in _TEXT_EXT:
        return {**out, "ctype": "text/plain; charset=utf-8", "policy": TEXT_POLICY}
    import mimetypes

    return {**out, "ctype": mimetypes.guess_type(a["path"])[0] or "application/octet-stream", "policy": "sandbox", "inline": False}


def reveal(conn, artifact_id: int, how: str, local_machine: str | None = None) -> str | None:
    """Open an artifact still on disk in its own app ("open"), or show it in Finder ("reveal"). Returns an error or None.
    Only a recorded artifact of a deliverable type (a document, page, deck…), never a program."""
    import subprocess
    import sys

    from ..core.i18n import tr

    a = artifact_by_id(conn, artifact_id, local_machine)
    if a is None or a["status"] not in ("present", "changed"):
        return tr("that file is not on this computer")
    if os.path.splitext(a["path"])[1].lower() not in EXT_KIND:
        return tr("that kind of file is not opened from here")
    if sys.platform == "darwin":
        cmd = ["open", "-R", a["path"]] if how == "reveal" else ["open", a["path"]]
    elif sys.platform.startswith("linux"):
        cmd = ["xdg-open", os.path.dirname(a["path"]) if how == "reveal" else a["path"]]
    elif sys.platform == "win32":
        from ..windows import open_path

        try:
            open_path(a["path"], reveal=how == "reveal")
        except OSError as exc:
            return tr("could not open it: {error}", error=exc)
        return None
    else:
        return tr("opening files is not supported on this system")
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        return tr("could not open it: {error}", error=exc)
    return None
