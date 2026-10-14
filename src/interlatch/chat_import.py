"""Import chats from a claude.ai or ChatGPT data export as sessions (`interlatch import`, Sources › Import export…).

Both exports are a .zip with conversations.json; the format is recognized from its contents. Only the chats
(conversations.json, and claude.ai's projects.json for project names) are read and archived; the account files
(users.json, user.json) and ChatGPT's chat.html are never opened. Imports are repeatable: a newer export updates the
chats that changed and adds new ones.

Imported chats are not analyzed automatically (years of chats would use up the Claude plan's limits in one go):
screen them (screen.py) to queue the ones worth it, analyze one with **Analyze now**, or import with `--analyze` to
queue them all.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from . import chatgpt_export, claude_export
from .util import utcnow_iso
from .views import NOT_ANALYZED_CHAT

log = logging.getLogger("interlatch.chat_import")

_CONVERSATIONS_RE = re.compile(r"^conversations(?:-\d+)?\.json$")  # large exports may be split
_LOCAL_HEADER = struct.Struct("<4s5H3I2H")  # signature, version, flags, method, time, date, crc, sizes, name/extra lengths


@dataclass(frozen=True)
class Format:
    key: str  # kv and archive name
    label: str
    module: object

    @property
    def agent(self) -> str:
        return self.module.AGENT

    @property
    def source(self) -> str:
        return self.module.SOURCE

    @property
    def last_import_key(self) -> str:
        return f"{self.key}:last-import"

    @property
    def not_analyzed(self) -> str:
        return NOT_ANALYZED_CHAT.format(label=self.label)


CLAUDE_AI = Format("claude-ai", "claude.ai", claude_export)
CHATGPT = Format("chatgpt", "ChatGPT", chatgpt_export)
FORMATS = (CLAUDE_AI, CHATGPT)


class ExportError(Exception):
    """The file is not an export Interlatch can read."""


@dataclass
class Export:
    format: Format
    conversations: list[dict]
    files: dict[str, bytes]  # what gets archived: the conversations file(s), claude.ai's projects.json
    note: str | None = None  # read from a zip cut off after its chats


def _wanted(name: str) -> bool:
    return bool(_CONVERSATIONS_RE.match(name)) or name == "projects.json"


def _files(path: Path, name: str) -> tuple[dict[str, bytes], str | None]:
    """The chat files of an export .zip, its unpacked folder, or a conversations.json, and a note when the zip was cut off."""
    path = Path(path).expanduser()
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = {Path(n).name: n for n in z.namelist() if not n.startswith("__MACOSX/")}
            return {n: z.read(full) for n, full in sorted(names.items()) if _wanted(n)}, None
    if path.is_dir():
        return {p.name: p.read_bytes() for p in sorted(path.iterdir()) if p.is_file() and _wanted(p.name)}, None
    if path.suffix == ".json" and path.is_file():
        sibling = path.with_name("projects.json")
        return {"conversations.json": path.read_bytes(), **({"projects.json": sibling.read_bytes()} if sibling.is_file() else {})}, None
    if path.is_file():
        with open(path, "rb") as f:
            if f.read(4) == b"PK\x03\x04":
                return _cut_off_zip(path, name)
    if path.exists():
        raise ExportError(f"{name} is not a chat export: give the .zip, its unpacked folder, or conversations.json")
    raise ExportError(f"{path} not found")


def _zip64_size(extra: bytes) -> int:
    """The compressed size from a local header's zip64 extra field (original size first, then compressed)."""
    i = 0
    while i + 4 <= len(extra):
        tag, n = struct.unpack_from("<HH", extra, i)
        if tag == 1 and n >= 16:
            return struct.unpack_from("<Q", extra, i + 12)[0]
        i += 4 + n
    return 0xFFFFFFFF


def _cut_off_zip(path: Path, name: str) -> tuple[dict[str, bytes], str]:
    """The chat files of a zip without its end, as an interrupted download leaves it, read entry by entry.

    `zipfile` needs the central directory, the last thing in a zip, so a partial download looks like no zip at all
    even when every chat is there: ChatGPT puts conversations-*.json near the start and gigabytes of attachments
    after them. The chats are accepted only when the cut falls in a later file Interlatch does not read.
    """
    size = path.stat().st_size
    files: dict[str, bytes] = {}
    cut_in = None  # the entry the file ends in
    redownload = f"{name} is an incomplete download ({size / 1e9:.2f} GB, the end of the zip is missing)"
    with open(path, "rb") as f:
        while True:
            head = f.read(_LOCAL_HEADER.size)
            if head[:4] == b"PK\x01\x02":  # the central directory: every entry was read, only the zip's end is damaged
                cut_in = ""
                break
            if len(head) < _LOCAL_HEADER.size or head[:4] != b"PK\x03\x04":
                break
            _, _, flags, method, _, _, crc, csize, _, nlen, xlen = _LOCAL_HEADER.unpack(head)
            raw, extra = f.read(nlen), f.read(xlen)
            entry = Path(raw.decode("utf-8", "replace")).name
            if len(raw) < nlen or len(extra) < xlen:
                cut_in = entry
                break
            if flags & 0x9:  # encrypted, or the size only follows the data: the next entry cannot be found
                break
            if csize == 0xFFFFFFFF:
                csize = _zip64_size(extra)
            if f.tell() + csize > size:
                cut_in = entry
                break
            if not (_wanted(entry) and not raw.startswith(b"__MACOSX/")):
                f.seek(csize, 1)
                continue
            data = f.read(csize)
            try:
                data = data if method == 0 else zlib.decompress(data, -15) if method == 8 else None
            except zlib.error:
                data = None
            if data is None or zlib.crc32(data) != crc:
                raise ExportError(f"{redownload}, and {entry} in it is damaged: download the export again")
            files[entry] = data
    shards = [n for n in files if _CONVERSATIONS_RE.match(n)]
    if cut_in is None or not shards or _wanted(cut_in):
        where = f"inside {cut_in}" if cut_in and _wanted(cut_in) else "before all its chats could be read"
        raise ExportError(f"{redownload}: it is cut off {where}. Download the export again")
    note = ("the end of the zip is damaged, but every chat was read" if not cut_in else
            f"incomplete download, cut off at {size / 1e9:.2f} GB in {cut_in}, after the chats: all {len(shards)} "
            f"conversations file{'s' if len(shards) > 1 else ''} read")
    log.warning("%s: %s", name, note)
    return files, note


def _folders(path: Path) -> set[str]:
    try:
        with zipfile.ZipFile(path) as z:
            return {n.split("/")[0] for n in z.namelist() if "/" in n}
    except (OSError, zipfile.BadZipFile):
        return set()


def read_export(path: Path, name: str | None = None) -> Export:
    name = name or Path(path).name
    files, note = _files(path, name)
    shards = [n for n in files if _CONVERSATIONS_RE.match(n)]
    if not shards and "projects" in _folders(path):
        raise ExportError(f"{name} holds your claude.ai projects, not chats, and the export does not say which project a "
                          "chat belongs to: there is nothing to import from it. The chats are in conversations-000.zip.")
    if not shards:
        raise ExportError(f"no conversations.json in {name}: is it a claude.ai or ChatGPT data export? "
                          "(claude.ai sends several zips; the chats are in conversations-000.zip)")
    conversations: list[dict] = []
    for name in shards:
        try:
            data = json.loads(files[name])
        except ValueError as exc:
            raise ExportError(f"{name} is not valid JSON") from exc
        if isinstance(data, dict):
            data = data.get("conversations") or []
        if not isinstance(data, list):
            raise ExportError(f"{name} is not a list of conversations")
        conversations += [c for c in data if isinstance(c, dict)]
    fmt = CHATGPT if chatgpt_export.is_chatgpt(conversations) else CLAUDE_AI
    if fmt is CLAUDE_AI and conversations and not claude_export.is_claude(conversations):
        raise ExportError("conversations.json is neither a claude.ai nor a ChatGPT export")
    if fmt is CHATGPT:
        files.pop("projects.json", None)
    return Export(fmt, conversations, files, note)


def import_export(cfg, conn, path: Path, *, analyze: bool = False, progress=None, name: str | None = None) -> dict:
    """Import (or re-import) an export. `name` is the file's name when `path` is an upload's temporary copy."""
    from .db import kv_set
    from .ingest import _unchanged, forgotten_ids, store_parsed
    from .util import file_lock

    name = name or Path(path).name
    export = read_export(Path(path), name)
    fmt, mod = export.format, export.format.module
    projects = claude_export.project_names(export.files.get("projects.json")) if fmt is CLAUDE_AI else {}
    digest = hashlib.sha1(b"".join(export.files[k] for k in sorted(export.files))).hexdigest()[:12]
    dst = cfg.archive_dir / fmt.key / digest
    shard = next(n for n in export.files if _CONVERSATIONS_RE.match(n))
    archive = dst / f"{shard}.gz"
    if not archive.exists():  # the export's chats, kept for good
        dst.mkdir(parents=True, exist_ok=True)
        for fname, raw in export.files.items():
            with gzip.open(dst / f"{fname}.gz", "wb") as f:
                f.write(raw)
    counts = {"format": fmt.label, "conversations": len(export.conversations), "new": 0, "updated": 0, "unchanged": 0,
              "empty": 0, "excluded": 0, **({"note": export.note} if export.note else {})}
    with file_lock(cfg.locks_dir / "ingest.lock", timeout=900) as got:
        if not got:
            raise ExportError("a sync is running; try again when it finishes")
        skip = forgotten_ids(conn)
        for i, conv in enumerate(export.conversations):
            if progress and i % 50 == 0:
                progress(f"importing {fmt.label} chats… {i:,}/{len(export.conversations):,}")
            sid, sig = mod.conversation_id(conv), mod.conversation_signature(conv)
            if not sid:
                counts["empty"] += 1
                continue
            if sid in skip or _unchanged(conn, sid, sig, False):
                counts["unchanged"] += 1
                continue
            ps = mod.parse_conversation(conv, projects)
            if ps is None:
                counts["empty"] += 1
                continue
            if cfg.is_excluded(ps.project_path):
                counts["excluded"] += 1
                continue
            queued = conn.execute("SELECT 1 FROM sessions WHERE id = ? AND analysis_status = 'pending'", (sid,)).fetchone()
            result = store_parsed(conn, cfg, ps, claude_dir=dst, project_dir=None, transcript_path=archive,
                                  archive_path=archive, files_sig=sig, agent=fmt.agent, source=fmt.source)
            if not analyze and not queued:  # a chat already queued for analysis (screen --queue) stays queued
                conn.execute("UPDATE sessions SET analysis_status = 'skipped', analysis_reason = ? "
                             "WHERE id = ? AND analysis_status = 'pending'", (fmt.not_analyzed, sid))
            counts["new" if result == "new" else "updated"] += 1
            conn.commit()
        kv_set(conn, fmt.last_import_key, json.dumps({"at": utcnow_iso(), "file": name, **counts}))
        conn.commit()
    log.info("%s import from %s: %s", fmt.label, name, counts)
    return counts


def reparse_archived(cfg, conn, skip: set[str] = frozenset()) -> int:
    """Re-read, from the export each was last imported from (kept in the archive), the chats an older parser stored,
    after a parser upgrade; each keeps its analysis state. Returns how many chats were re-parsed."""
    from .ingest import store_parsed

    done = 0
    for fmt in FORMATS:
        mod = fmt.module
        tag = "|" + mod.conversation_signature({}).rsplit("|", 1)[-1]  # every signature ends with the parser version
        by_dir: dict[Path, set[str]] = {}
        for sid, sig, archive in conn.execute("SELECT id, files_sig, archive_path FROM sessions WHERE source = ?", (fmt.source,)):
            if sid not in skip and archive and not (sig or "").endswith(tag):
                by_dir.setdefault(Path(archive).parent, set()).add(sid)
        for d, stale in by_dir.items():
            projects_gz = d / "projects.json.gz"
            try:
                projects = claude_export.project_names(gzip.decompress(projects_gz.read_bytes())) \
                    if fmt is CLAUDE_AI and projects_gz.exists() else {}
            except OSError:
                projects = {}
            for shard in sorted(d.glob("conversations*.json.gz")):
                try:
                    data = json.loads(gzip.decompress(shard.read_bytes()))
                except (OSError, ValueError):
                    log.warning("could not read archived export %s", shard)
                    continue
                data = data.get("conversations") or [] if isinstance(data, dict) else data
                for conv in data if isinstance(data, list) else []:
                    sid = mod.conversation_id(conv) if isinstance(conv, dict) else None
                    if sid not in stale:
                        continue
                    stale.discard(sid)
                    ps = mod.parse_conversation(conv, projects)
                    if ps is None or cfg.is_excluded(ps.project_path):
                        continue
                    store_parsed(conn, cfg, ps, claude_dir=d, project_dir=None, transcript_path=shard, archive_path=shard,
                                 files_sig=mod.conversation_signature(conv), agent=fmt.agent, source=fmt.source)
                    conn.commit()
                    done += 1
    return done


def summary(counts: dict) -> str:
    parts = [f"{counts['new']} new", f"{counts['updated']} updated", f"{counts['unchanged']} unchanged"]
    if counts.get("empty"):
        parts.append(f"{counts['empty']} empty")
    if counts.get("excluded"):
        parts.append(f"{counts['excluded']} excluded")
    note = f" ({counts['note']})" if counts.get("note") else ""
    return f"{counts['format']} export: {counts['conversations']} chats · " + ", ".join(parts) + note


def import_status(conn) -> dict:
    """Per format: chats imported and analyzed, how screening sorted them, and the last import."""
    from .db import kv_get
    from .screen import screen_status

    out = {}
    for fmt in FORMATS:
        r = conn.execute("SELECT COUNT(*), SUM(analysis_status = 'done') FROM sessions WHERE source = ?", (fmt.source,)).fetchone()
        try:
            last = json.loads(kv_get(conn, fmt.last_import_key) or "null")
        except ValueError:
            last = None
        out[fmt.key] = {"label": fmt.label, "agent": fmt.agent, "sessions": r[0] or 0, "analyzed": r[1] or 0, "last_import": last,
                        "screen": screen_status(conn, fmt.source)}
    return out
