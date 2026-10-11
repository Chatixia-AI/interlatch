"""Parse Google Antigravity conversations (~/.gemini/antigravity) into the same model as Claude Code sessions.

Antigravity keeps each conversation's own store in conversations/<id>.pb (encrypted; older versions) or
conversations/<id>.db (SQLite of protobuf blobs; newer versions). Both versions also write a plain step log next to
the agent's artifacts, which is what is read here:

  brain/<id>/.system_generated/logs/transcript_full.jsonl   one step per line, untruncated (newer versions)
  brain/<id>/.system_generated/logs/transcript.jsonl        the same with long fields cut (older versions: the only one)
  brain/<id>/.system_generated/logs/chunks/<name>/*.jsonl   a copy of each log in ~100 KB pieces, cut mid-line
  annotations/<id>.pbtxt                                    title:"..." (newer versions)

Steps are written as they finish, so they are sorted by step_index. A MODEL PLANNER_RESPONSE carries the reply, its
thinking, token usage and tool calls; the MODEL steps after it are those calls' results, in order. Where the .db
exists, its trajectory metadata adds the workspace folder, git remote and branch, and its generation metadata the
model id. Nothing in ~/.gemini is written to.
"""

from __future__ import annotations

import codecs
import json
import logging
import re
import sqlite3
from collections import Counter, deque
from pathlib import Path

from .copilot_parser import _Builder, estimate_cost, finish_session, uri_path
from .parser import ApiCall, ParsedSession
from .pricing import normalize_model
from .util import one_line, parse_ts

log = logging.getLogger("interlatch.antigravity")

ANTIGRAVITY_PARSER_VERSION = 1
LOGS = Path(".system_generated") / "logs"
_REQUEST_RE = re.compile(r"<USER_REQUEST>(.*?)</USER_REQUEST>", re.S)
_BLOCK_RE = re.compile(r"<(USER_REQUEST|ADDITIONAL_METADATA|USER_SETTINGS_CHANGE)>.*?</\1>", re.S)
_ARTIFACT_URI_RE = re.compile(r"file://\S*?/brain/[^/\s]+/")
_MODEL_PICK_RE = re.compile(r"`Model Selection` from .*? to (.+?)\.(?:\s|$)", re.S)
_SYSTEM_RE = re.compile(r".*<SYSTEM_MESSAGE>\s*(?:\[Message\][^\n]*?content=)?(.*?)\s*</SYSTEM_MESSAGE>", re.S)  # the last opening tag: the preamble names the tag too
_STAMPS_RE = re.compile(r"\s*Created At: (\S+)\s*(?:Completed At: (\S+))?\s*")
_TITLE_RE = re.compile(r'title:\s*"((?:[^"\\]|\\.)*)"')
_MODEL_ID_RE = re.compile(r"^[a-z][a-z0-9.-]*\d[a-z0-9.-]*$")
_PATH_KEYS = ("absolutePath", "targetFile", "file")


# ------------------------------------------------------------------ store layout
def conversation_dirs(home: Path) -> list[Path]:
    """brain/<id> folders that have a step log."""
    brain = home / "brain"
    if not brain.is_dir():
        return []
    return sorted(d for d in brain.iterdir() if d.is_dir() and transcript_files(d))


def transcript_files(conv: Path) -> list[Path]:
    """The step log (the untruncated one when there is one), or else its chunks, which read as one file joined in order."""
    logs = conv / LOGS
    for name in ("transcript_full", "transcript"):
        if (logs / f"{name}.jsonl").exists():
            return [logs / f"{name}.jsonl"]
        chunks = sorted((logs / "chunks" / name).glob("*.jsonl"))
        if chunks:
            return chunks
    return []


def stored_conversations(home: Path) -> list[str]:
    """Ids of every conversation Antigravity keeps, readable or not."""
    d = home / "conversations"
    return sorted({p.stem for p in d.glob("*") if p.suffix in (".pb", ".db")}) if d.is_dir() else []


def load_title(home: Path, cid: str) -> str | None:
    try:
        text = (home / "annotations" / f"{cid}.pbtxt").read_text(errors="replace")
    except OSError:
        return None
    m = _TITLE_RE.search(text)
    if not m:
        return None
    try:  # text-format protobuf: C escapes, octal for non-ASCII bytes
        return codecs.escape_decode(m.group(1).encode())[0].decode("utf-8", "replace").strip() or None
    except ValueError:
        return m.group(1).strip() or None


# ------------------------------------------------------------------ conversation database (newer versions)
def _varint(b: bytes, i: int) -> tuple[int, int]:
    out = shift = 0
    while True:
        byte = b[i]
        i += 1
        out |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return out, i


def _fields(b: bytes) -> dict[int, list]:
    """One protobuf message's fields (number -> values; length-delimited ones stay bytes), or {} if it is not one."""
    out: dict[int, list] = {}
    i = 0
    try:
        while i < len(b):
            key, i = _varint(b, i)
            num, wire = key >> 3, key & 7
            if wire == 0:
                v, i = _varint(b, i)
            elif wire == 2:
                n, i = _varint(b, i)
                v, i = b[i:i + n], i + n
            elif wire in (1, 5):
                n = 8 if wire == 1 else 4
                v, i = b[i:i + n], i + n
            else:
                return {}
            if num == 0 or i > len(b):
                return {}
            out.setdefault(num, []).append(v)
    except IndexError:
        return {}
    return out


def _pb_text(blob, *path: int) -> str | None:
    """The string at a field path (first value at each level), or None."""
    for num in path:
        vals = _fields(blob).get(num) if isinstance(blob, bytes) else None
        if not vals or not isinstance(vals[0], bytes):
            return None
        blob = vals[0]
    try:
        return blob.decode() or None
    except UnicodeDecodeError:
        return None


def load_db_meta(db: Path) -> dict:
    """Workspace folder, git remote and branch, and model id from a conversation database; {} if absent or unreadable."""
    if not db.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            meta = conn.execute("SELECT data FROM trajectory_metadata_blob").fetchone()
            gens = conn.execute("SELECT data FROM gen_metadata ORDER BY idx").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("antigravity conversation db unreadable: %s: %s", db, exc)
        return {}
    out = {}
    if meta and isinstance(meta[0], bytes):
        out = {"workspace": uri_path(_pb_text(meta[0], 1, 1)), "remote": _pb_text(meta[0], 1, 3, 2),
               "branch": _pb_text(meta[0], 1, 4)}
    models = Counter(m for (g,) in gens if isinstance(g, bytes) and (m := _pb_text(g, 1, 19)) and _MODEL_ID_RE.match(m))
    if models:
        out["model"] = models.most_common(1)[0][0]
    return {k: v for k, v in out.items() if v}


# ------------------------------------------------------------------ steps
def load_steps(conv: Path) -> tuple[list[dict], int]:
    """The conversation's steps in order, and how many lines did not parse."""
    data = b"".join(f.read_bytes() for f in transcript_files(conv)).decode("utf-8", "replace")
    lines = data.split("\n")
    if not data.endswith("\n"):
        lines.pop()  # a step still being written
    steps: dict[int, dict] = {}
    bad = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(row, dict) and isinstance(row.get("step_index"), int):
            steps[row["step_index"]] = row
    return [steps[k] for k in sorted(steps)], bad


def _prompt_text(content: str) -> str:
    """The user's words: the USER_REQUEST block, after any preamble (an artifact review: "Comments on artifact URI ...")."""
    m = _REQUEST_RE.search(content)
    request = m.group(1).strip() if m else ""
    preamble = _ARTIFACT_URI_RE.sub("", _BLOCK_RE.sub("", content)).strip()
    return "\n\n".join(p for p in (preamble, request) if p)


def _model_from_label(label: str) -> str | None:
    """'Gemini 3.8 Flash (High)' -> 'gemini-3.8-flash'."""
    return re.sub(r"\s+", "-", re.sub(r"\(.*?\)", "", label).strip().lower()) or None


def _json_value(v):
    try:
        return json.loads(v)
    except (TypeError, ValueError):  # not JSON, or a long value the truncated log cut short
        return v


def _args(raw) -> dict:
    """Tool arguments under the key names tool_summary reads. Older versions JSON-encode every value; those are decoded."""
    if not isinstance(raw, dict):
        return {}
    args = dict(raw)
    if any(isinstance(v, str) and len(v) > 1 and v[0] == v[-1] == '"' and isinstance(_json_value(v), str) for v in args.values()):
        args = {k: _json_value(v) for k, v in args.items()}
    out = {("description" if k == "toolSummary" else k[:1].lower() + k[1:]): v for k, v in args.items() if k != "toolAction"}
    path = next((out[k] for k in _PATH_KEYS if isinstance(out.get(k), str) and out[k]), None)
    if path and "filePath" not in out:
        out["filePath"] = path
    elif isinstance(out.get("directoryPath"), str) and "path" not in out:
        out["path"] = out["directoryPath"]  # shown as the call's summary; not counted as a file
    return out


def _result(step: dict) -> tuple[str, int | None]:
    """A result step's text without its Created At / Completed At header, and how long the call took (ms)."""
    text = step.get("content") if isinstance(step.get("content"), str) else str(step.get("error") or "")
    m = _STAMPS_RE.match(text)
    if not m:
        return text, None
    start, end = parse_ts(m.group(1)), parse_ts(m.group(2))
    return text[m.end():], int((end - start).total_seconds() * 1000) if start and end else None


def _system_text(content: str) -> str:
    m = _SYSTEM_RE.search(content)
    return (m.group(1) if m else content).strip()


def parse_antigravity_conversation(conv: Path, *, title: str | None = None, db_meta: dict | None = None) -> ParsedSession | None:
    """One brain/<id> folder as a session; None when it never got a prompt."""
    steps, bad = load_steps(conv)
    if not any(s.get("type") == "USER_INPUT" for s in steps):
        return None
    meta = db_meta or {}
    ps = ParsedSession(id=conv.name)
    ps.entrypoint = "antigravity"
    ps.ai_title = title
    ps.git_branch = meta.get("branch")
    ps.git_remote = meta.get("remote")
    ps.parse_errors = bad
    b = _Builder(ps)
    model = meta.get("model")  # the database's model id; a model picked in the conversation takes over from there
    pending: deque = deque()  # tool calls waiting for their result steps
    cwds: Counter = Counter()

    def flush() -> None:
        while pending:
            ts, call_id, name, args = pending.popleft()
            b.tool(ts, call_id, name, args)

    for s in steps:
        ts, kind, source = s.get("created_at"), s.get("type"), s.get("source")
        content = s.get("content") if isinstance(s.get("content"), str) else ""
        if kind == "USER_INPUT":
            flush()
            pick = _MODEL_PICK_RE.search(content)
            if pick:
                model = _model_from_label(pick.group(1)) or model
            b.prompt(ts, _prompt_text(content))
        elif kind == "PLANNER_RESPONSE":
            flush()
            if isinstance(s.get("thinking"), str) and s["thinking"].strip():
                b.event(ts, "assistant", "thinking", s["thinking"])
            if content.strip():
                b.event(ts, "assistant", "text", content)
            inp, cached, out = (int(s.get(k) or 0) for k in ("input_tokens", "cache_read_tokens", "output_tokens"))
            if inp or cached or out:
                m = normalize_model(model)
                ps.api_calls.append(ApiCall("", f"step-{s['step_index']}", ts, m, input_tokens=inp, output_tokens=out,
                                            cache_read_tokens=cached, cost_usd=estimate_cost(m, inp, cached, 0, out)))
            for i, tc in enumerate(s.get("tool_calls") or []):
                if isinstance(tc, dict):
                    args = _args(tc.get("args"))
                    if isinstance(args.get("cwd"), str) and args["cwd"]:
                        cwds[args["cwd"]] += 1
                    pending.append((ts, f"{s['step_index']}.{i}", tc.get("name") or "tool", args))
        elif kind == "ERROR_MESSAGE":  # the model's last tool call could not be run
            text = one_line(s.get("error") or content, 300)
            if pending:
                call_ts, call_id, name, args = pending.popleft()
                b.tool(call_ts, call_id, name, args, result=text, is_error=True)
            else:
                ps.n_api_errors += 1
                b.event(ts, "system", "api_error", text)
        elif source == "SYSTEM":
            if kind == "SYSTEM_MESSAGE" and content.strip():  # e.g. a background command finished
                b.event(ts, "system", "notification", _system_text(content))
        elif source == "MODEL":  # the result of the oldest call still waiting for one
            text, ms = _result(s)
            failed = s.get("status") == "ERROR" or bool(s.get("error"))
            if pending:
                call_ts, call_id, name, args = pending.popleft()
                b.tool(call_ts, call_id, name, args, result=text, is_error=failed, duration_ms=ms)
            elif text.strip():
                b.event(ts, "user", "tool_result", text, is_error=failed, limit_key="tool_error" if failed else "tool_result")
    flush()
    ps.project_path = meta.get("workspace") or (cwds.most_common(1)[0][0] if cwds else None)
    # keep files the agent read or changed, leaving out listed folders and its own task/plan/walkthrough artifacts
    ps.files = {fp: st for fp, st in ps.files.items() if (st.reads or st.edits or st.writes) and not fp.startswith(f"{conv}/")}
    ps = finish_session(ps, b.stamps)
    if model and not ps.models:  # older logs carry no token counts
        ps.models[normalize_model(model)] += 1
    return ps
