"""Parse OpenCode sessions (~/.local/share/opencode/opencode.db) into the same model as Claude Code sessions.

OpenCode 1.2 and later keep every session in one SQLite database (opencode-<channel>.db for preview builds). 1.x:

  session   one row per session: directory, title, version, parent_id (set for a subagent's own session)
  message   one row per message; `data` is its JSON without id and sessionID (role, time, modelID, tokens, error)
  part      one row per part of a message; `data` is the part's JSON (text, reasoning, tool, step-finish, ...)

2.x (the desktop app and CLI from 2.0) replaced them with:

  session_v2       the same session row (title may be null)
  session_message  one row per entry, in `seq` order, typed: user, assistant (its reasoning, text and tool calls,
                   each call with its input, result and timing, plus the call's tokens and cost), shell (a command
                   the user ran), compaction, idle (the turn ended: succeeded, failed or interrupted), ...

2.x rows are turned into 1.x's shape (_from_v2) and read by the same code. Times are milliseconds since the epoch.
A subagent (1.x `task`, 2.x `subagent`) runs in a child session whose parent_id is the session that started it;
children are read into their root session as subagents. Only these tables are read, read-only: the database also
holds credentials and accounts, which are never opened. Before 1.2 OpenCode kept JSON files under storage/; 1.2
copied them into the database, so they are not read here.
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
import sqlite3
from collections import Counter
from pathlib import Path

from .builder import SessionBuilder, estimate_cost, finish_session, ms_to_iso
from .codex.parser import _patch_files
from ..core.sessions import ApiCall, FileStat, ParsedSession, Subagent
from ..core.pricing import normalize_model
from ..core.util import one_line, safe_text

log = logging.getLogger("interlatch.opencode")

OPENCODE_PARSER_VERSION = 1
_DEFAULT_TITLE_RE = re.compile(r"^(?:New|Child) session - \d{4}-\d\d-\d\dT")
_SUBAGENT_TITLE_RE = re.compile(r"\s*\(@[\w-]+ subagent\)$")
_MCP_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


# ------------------------------------------------------------------ database
def opencode_dbs(home: Path) -> list[Path]:
    """opencode.db, and the databases of preview builds (opencode-<channel>.db)."""
    return sorted(home.glob("opencode*.db")) if home.is_dir() else []


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


# per layout: (session table, message table, part table or None, format)
_LAYOUTS = (("session_v2", "session_message", None, 2), ("session", "message", "part", 1))


def list_sessions(db: Path) -> list[dict]:
    """Every session row, each with `_sig` (what changes when any of its messages does) and `_format` (1 or 2), oldest
    first. A session in both layouts is read from 2.x's. [] if unreadable."""
    out: dict[str, dict] = {}
    try:
        conn = _connect(db)
        try:
            tables = _tables(conn)
            for sess, msg, part, fmt in _LAYOUTS:
                if sess not in tables or msg not in tables:
                    continue
                msgs = {r[0]: f"{r[1]}:{r[2]}:{r[3]}" for r in conn.execute(
                    f"SELECT session_id, COUNT(*), COALESCE(SUM(LENGTH(data)), 0), MAX(time_updated) FROM {msg} GROUP BY session_id")}
                parts = {r[0]: f"{r[1]}:{r[2]}:{r[3]}" for r in conn.execute(
                    f"SELECT session_id, COUNT(*), COALESCE(SUM(LENGTH(data)), 0), MAX(time_updated) FROM {part} "
                    "GROUP BY session_id")} if part in tables else {}
                for r in conn.execute(f"SELECT * FROM {sess}"):
                    s = dict(r)
                    if s["id"] in out:
                        continue
                    s["_format"] = fmt
                    s["_sig"] = f"{s['id']}:{fmt}:{s.get('time_updated')}:{s.get('title')}:{msgs.get(s['id'], '0')}:{parts.get(s['id'], '0')}"
                    out[s["id"]] = s
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("opencode database unreadable: %s: %s", db, exc)
        return []
    return sorted(out.values(), key=lambda s: (s.get("time_created") or 0, s["id"]))


def root_folders(db: Path) -> list[str]:
    """The folder of each session that is not a subagent's and has a prompt (what the Sources page counts)."""
    seen: dict[str, str] = {}
    try:
        conn = _connect(db)
        try:
            tables = _tables(conn)
            for sess, msg, _, fmt in _LAYOUTS:
                if sess in tables and msg in tables:
                    prompt = "m.type = 'user'" if fmt == 2 else "1"
                    for r in conn.execute(f"SELECT id, directory FROM {sess} s WHERE parent_id IS NULL AND EXISTS "
                                          f"(SELECT 1 FROM {msg} m WHERE m.session_id = s.id AND {prompt})"):
                        seen.setdefault(r[0], r[1] or "")
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return list(seen.values())


def session_trees(sessions: list[dict]) -> list[tuple[dict, list[dict]]]:
    """[(root session, its descendants oldest first)]: a subagent's session belongs to the session that started it."""
    by_id = {s["id"]: s for s in sessions}

    def root_of(s: dict) -> str:
        seen = {s["id"]}
        while s.get("parent_id") in by_id and s["parent_id"] not in seen:
            s = by_id[s["parent_id"]]
            seen.add(s["id"])
        return s["id"]

    children: dict[str, list[dict]] = {}
    for s in sessions:
        rid = root_of(s)
        if rid != s["id"]:
            children.setdefault(rid, []).append(s)
    return [(s, children.get(s["id"], [])) for s in sessions if root_of(s) == s["id"]]


def load_messages(db: Path, session_ids: list[str]) -> tuple[dict[str, list[dict]], int]:
    """{session id: [message JSON with its id, time and "parts"]} oldest first, and how many rows did not parse."""
    marks = ", ".join("?" for _ in session_ids)
    out: dict[str, list[dict]] = {sid: [] for sid in session_ids}
    by_msg: dict[str, dict] = {}
    bad = 0
    conn = _connect(db)
    try:
        for r in conn.execute(f"SELECT id, session_id, time_created, data FROM message WHERE session_id IN ({marks}) "
                              "ORDER BY time_created, id", session_ids):
            m = _json(r["data"])
            if m is None:
                bad += 1
                continue
            m.update(id=r["id"], parts=[])
            if not isinstance(m.get("time"), dict):
                m["time"] = {"created": r["time_created"]}
            by_msg[r["id"]] = m
            out[r["session_id"]].append(m)
        for r in conn.execute(f"SELECT id, message_id, time_created, data FROM part WHERE session_id IN ({marks}) "
                              "ORDER BY message_id, id", session_ids):
            p = _json(r["data"])
            if p is None:
                bad += 1
                continue
            if r["message_id"] in by_msg:
                p.update(id=r["id"], _created=r["time_created"])
                by_msg[r["message_id"]]["parts"].append(p)
    finally:
        conn.close()
    return out, bad


def load_messages_v2(db: Path, session_ids: list[str]) -> tuple[dict[str, list[dict]], int]:
    """2.x: {session id: [session_message rows as {id, type, seq, data}]} in seq order, and how many did not parse."""
    marks = ", ".join("?" for _ in session_ids)
    out: dict[str, list[dict]] = {sid: [] for sid in session_ids}
    bad = 0
    conn = _connect(db)
    try:
        for r in conn.execute(f"SELECT id, session_id, type, seq, time_created, data FROM session_message "
                              f"WHERE session_id IN ({marks}) ORDER BY session_id, seq", session_ids):
            d = _json(r["data"])
            if d is None:
                bad += 1
                continue
            out[r["session_id"]].append({"id": r["id"], "type": r["type"], "seq": r["seq"],
                                         "time_created": r["time_created"], "data": d})
    finally:
        conn.close()
    return out, bad


def load_tree(db: Path, root: dict, children: list[dict]) -> dict:
    """A root session and its subagents' sessions as one document: what is parsed, and what the archive keeps (2.x
    rows as OpenCode wrote them, with "format": 2)."""
    sessions = [root, *children]
    msgs: dict[str, list[dict]] = {}
    bad = 0
    for fmt, load in ((1, load_messages), (2, load_messages_v2)):
        ids = [s["id"] for s in sessions if s.get("_format", 1) == fmt]
        if ids:
            got, n = load(db, ids)
            msgs.update(got)
            bad += n

    def entry(s: dict) -> dict:
        return {"info": {k: v for k, v in s.items() if not k.startswith("_")}, "format": s.get("_format", 1),
                "messages": msgs.get(s["id"], [])}

    return {**entry(root), "parse_errors": bad, "children": [entry(c) for c in children]}


def _json(raw) -> dict | None:
    try:
        v = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


# ------------------------------------------------------------------ config (MCP servers)
def _strip_jsonc(text: str) -> str:
    """JSON with comments and trailing commas (opencode.jsonc) -> JSON."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
            continue
        elif text.startswith("/*", i):
            i = text.find("*/", i + 2)
            i = n if i < 0 else i + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def config_files(config_dir: Path) -> list[Path]:
    """OpenCode's global config files, in the order it merges them."""
    return [config_dir / name for name in ("config.json", "opencode.json", "opencode.jsonc")]


def mcp_servers(config_dir: Path) -> list[str]:
    """The MCP servers OpenCode's global config names, as its tool names spell them (server_tool)."""
    names: list[str] = []
    for f in config_files(config_dir):
        try:
            data = json.loads(_strip_jsonc(f.read_text()))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("mcp"), dict):
            names += [_MCP_NAME_RE.sub("_", k) for k in data["mcp"]]
    return list(dict.fromkeys(names))


# ------------------------------------------------------------------ parsing
def _ms(v) -> int | None:
    return v if isinstance(v, (int, float)) and v > 0 else None


def _diff_lines(diff: str) -> tuple[int, int]:
    """Lines added and removed in a unified diff (not its ---/+++ headers)."""
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _mcp_of(name: str, servers: list[str]) -> str | None:
    return next((s for s in sorted(servers, key=len, reverse=True) if name.startswith(f"{s}_")), None)


def _error_text(err) -> str:
    if not isinstance(err, dict):
        return safe_text(str(err or "error"))
    data = err.get("data") if isinstance(err.get("data"), dict) else {}
    msg = data.get("message") or ""
    return safe_text(f"{err.get('name') or 'Error'}: {msg}" if msg else str(err.get("name") or "Error"))


# ------------------------------------------------------------------ 2.x rows -> 1.x messages
def _v2_error(err) -> dict | None:
    """2.x's {type, message} -> 1.x's {name, data: {message}}; an abort keeps 1.x's name, so it reads as an interrupt."""
    if not isinstance(err, dict):
        return None
    kind = str(err.get("type") or "Error")
    name = "MessageAbortedError" if "abort" in kind.lower() else kind
    return {"name": name, "data": {"message": str(err.get("message") or "")}}


def _v2_tool(c: dict) -> dict:
    state = c.get("state") if isinstance(c.get("state"), dict) else {}
    status = state.get("status")
    time = c.get("time") if isinstance(c.get("time"), dict) else {}
    contents = state.get("content") if isinstance(state.get("content"), list) else []
    output = "\n".join(x["text"] if x.get("type") == "text" else f"[file: {x.get('name') or x.get('uri')}]"
                       for x in contents if isinstance(x, dict) and x.get("type") in ("text", "file"))
    out = {"status": status if status in ("completed", "error") else "running",
           "input": state["input"] if isinstance(state.get("input"), dict) else {},
           "metadata": state.get("metadata") if isinstance(state.get("metadata"), dict) else {},
           "time": {"start": time.get("ran") or time.get("created"), "end": time.get("completed")}}
    if status == "completed":
        out["output"] = output
    elif status == "error":
        err = state.get("error") if isinstance(state.get("error"), dict) else {}
        out["error"] = str(err.get("message") or output or "error")
    return {"type": "tool", "callID": c.get("id"), "tool": c.get("name"), "state": out}


def _from_v2(rows: list[dict]) -> list[dict]:
    """2.x session_message rows as the 1.x messages _Reader reads."""
    out = []
    for row in rows:
        d, kind = row.get("data") or {}, row.get("type")
        time = d.get("time") if isinstance(d.get("time"), dict) else {}
        created = time.get("created") or row.get("time_created")
        if kind == "user":
            parts = [{"type": "text", "text": d.get("text") or ""}]
            parts += [{"type": "file", "mime": f.get("mime")} for f in d.get("files") or [] if isinstance(f, dict)]
            out.append({"id": row["id"], "role": "user", "time": {"created": created}, "parts": parts})
        elif kind == "assistant":
            model = d.get("model") if isinstance(d.get("model"), dict) else {}
            parts = []
            for c in d.get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool":
                    parts.append(_v2_tool(c))
                elif c.get("type") in ("text", "reasoning"):
                    ct = c.get("time") if isinstance(c.get("time"), dict) else {}
                    parts.append({"type": c["type"], "text": c.get("text") or "", "time": {"start": ct.get("created")}})
            retry = d.get("retry")
            if isinstance(retry, dict):
                parts.insert(0, {"type": "retry", "error": _v2_error(retry.get("error")), "time": {"start": retry.get("at")}})
            out.append({"id": row["id"], "role": "assistant", "time": {"created": created, "completed": time.get("completed")},
                        "modelID": model.get("id"), "providerID": model.get("providerID"), "cost": d.get("cost"),
                        "tokens": d.get("tokens"), "error": _v2_error(d.get("error")), "parts": parts})
        elif kind == "shell":  # a command the user ran (`!ls`): a shell call with its output
            output = d.get("output") if isinstance(d.get("output"), dict) else {}
            tool = {"type": "tool", "callID": row["id"], "tool": "shell", "state": {
                "status": "completed", "input": {"command": d.get("command") or ""}, "output": output.get("output") or "",
                "time": {"start": created, "end": time.get("completed")}}}
            out.append({"id": row["id"], "role": "assistant", "time": {"created": created}, "parts": [tool]})
        elif kind == "compaction" and d.get("status") == "completed":
            model = d.get("model") if isinstance(d.get("model"), dict) else {}
            out.append({"id": row["id"], "role": "user", "time": {"created": created},
                        "parts": [{"type": "compaction", "auto": d.get("reason") == "auto"}]})
            out.append({"id": f"{row['id']}:summary", "role": "assistant", "summary": True, "time": {"created": created},
                        "modelID": model.get("id"), "cost": d.get("cost"), "tokens": d.get("tokens"),
                        "parts": [{"type": "text", "text": d.get("summary") or ""}]})
        elif kind == "idle" and d.get("outcome") in ("interrupted", "failed"):
            err = {"name": "MessageAbortedError", "data": {"message": "interrupted"}} if d["outcome"] == "interrupted" \
                else _v2_error(d.get("error")) or {"name": "Error", "data": {"message": "the turn failed"}}
            out.append({"id": row["id"], "role": "assistant", "time": {"created": created}, "error": err, "parts": []})
    return out


class _Reader:
    def __init__(self, ps: ParsedSession, servers: list[str], cwd: str | None):
        self.ps, self.servers, self.cwd = ps, servers, cwd
        self.links: dict[str, tuple[str, dict]] = {}  # child session id -> (task call id, its input)
        self.cwds: Counter = Counter()

    def feed(self, b: SessionBuilder, messages: list[dict]) -> None:
        for m in messages:
            time = m.get("time") if isinstance(m.get("time"), dict) else {}
            ts = ms_to_iso(_ms(time.get("created")))
            parts = [p for p in m.get("parts") or [] if isinstance(p, dict)]
            if m.get("role") == "user":
                self._user(b, ts, parts)
            elif m.get("role") == "assistant":
                self._assistant(b, m, ts, parts)
            if (end := ms_to_iso(_ms(time.get("completed")))):
                b.stamps.append(end)

    def _user(self, b: SessionBuilder, ts, parts: list[dict]) -> None:
        texts = [p["text"] for p in parts if p.get("type") == "text" and isinstance(p.get("text"), str)
                 and not p.get("synthetic") and not p.get("ignored")]
        for p in parts:
            kind = p.get("type")
            if kind == "file" and str(p.get("mime") or "").startswith("image/"):
                self.ps.n_images += 1
            elif kind == "subtask" and isinstance(p.get("prompt"), str):
                texts.append(p["prompt"])
            elif kind == "compaction":
                self.ps.n_compactions += 1
                b.event(ts, "system", "compact", "Context compacted" + (" (auto)" if p.get("auto") else ""))
        text = "\n\n".join(t for t in texts if t.strip())
        if not text:
            return
        if b.agent_id:  # a subagent's brief, not a prompt the user typed
            b.event(ts, "user", "prompt", text)
        else:
            b.prompt(ts, text)

    def _assistant(self, b: SessionBuilder, m: dict, ts, parts: list[dict]) -> None:
        ps = self.ps
        model = normalize_model(m.get("modelID"))
        path = m.get("path") if isinstance(m.get("path"), dict) else {}
        if isinstance(path.get("cwd"), str) and path["cwd"]:
            self.cwds[path["cwd"]] += 1
        steps = 0
        for p in parts:
            kind = p.get("type")
            pt = p.get("time") if isinstance(p.get("time"), dict) else {}
            pts = ms_to_iso(_ms(pt.get("start")) or _ms(p.get("_created"))) or ts
            if kind == "reasoning" and isinstance(p.get("text"), str) and p["text"].strip():
                b.event(pts, "assistant", "thinking", p["text"])
            elif kind == "text" and isinstance(p.get("text"), str) and p["text"].strip() and not p.get("synthetic"):
                if m.get("summary") is True:  # a compaction's summary of the conversation so far
                    b.event(pts, "user", "compact_summary", p["text"])
                else:
                    b.event(pts, "assistant", "text", p["text"])
            elif kind == "tool":
                self._tool(b, p, pts)
            elif kind == "step-finish":
                steps += 1
                self._usage(b, f"{m.get('id')}:{p.get('id')}", ms_to_iso(_ms(p.get("_created"))) or ts, model,
                            p.get("tokens"), p.get("cost"))
            elif kind == "retry":
                ps.n_api_errors += 1
                b.event(pts, "system", "api_error", one_line(_error_text(p.get("error")), 300))
        if not steps:  # no step-finish parts: the message's own totals
            self._usage(b, str(m.get("id") or ""), ts, model, m.get("tokens"), m.get("cost"))
        err = m.get("error")
        if isinstance(err, dict):
            end = ms_to_iso(_ms((m.get("time") or {}).get("completed"))) or ts
            if err.get("name") == "MessageAbortedError":
                ps.n_interrupts += 1
                b.event(end, "user", "interrupt", "[Interrupted]")
            else:
                ps.n_api_errors += 1
                b.event(end, "system", "api_error", one_line(_error_text(err), 300))

    def _usage(self, b: SessionBuilder, msg_id: str, ts, model, tokens, cost) -> None:
        if not isinstance(tokens, dict):
            return
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        inp, out, reasoning = (int(tokens.get(k) or 0) for k in ("input", "output", "reasoning"))
        cr, cw = int(cache.get("read") or 0), int(cache.get("write") or 0)
        out += reasoning
        if not (inp or out or cr or cw):
            return
        # OpenCode's own figure when it priced the call (a subscription or free model costs 0 there): else list price
        spent = float(cost) if isinstance(cost, (int, float)) and cost > 0 else estimate_cost(model, inp, cr, cw, out)
        self.ps.api_calls.append(ApiCall(b.agent_id, msg_id, ts, model, input_tokens=inp, output_tokens=out,
                                         cache_read_tokens=cr, cache_write_tokens=cw, cost_usd=spent))

    def _tool(self, b: SessionBuilder, p: dict, ts) -> None:
        ps = self.ps
        state = p.get("state") if isinstance(p.get("state"), dict) else {}
        name = str(p.get("tool") or "tool")
        args = dict(state["input"]) if isinstance(state.get("input"), dict) else {}
        status = state.get("status")
        is_error = status == "error"
        result = (state.get("error") if is_error else state.get("output")) if status in ("completed", "error") else None
        result = safe_text(str(result)) if result is not None else None
        st = state.get("time") if isinstance(state.get("time"), dict) else {}
        start, end = _ms(st.get("start")), _ms(st.get("end"))
        meta = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        if isinstance(args.get("path"), str) and "filePath" not in args:  # 2.x: relative to the session's folder
            args["filePath" if name in ("read", "edit", "write") else "path"] = self._absolute(args.pop("path"))
        ops = {}
        if name in ("apply_patch", "patch") and isinstance(args.get("patchText"), str):
            ops = _patch_files(safe_text(args["patchText"]), self.cwd)  # its paths are relative to the session's folder
            if ops:
                args.setdefault("filePath", next(iter(ops)))
        if isinstance(args.get("workdir"), str) and args["workdir"]:
            self.cwds[args["workdir"]] += 1
        call = b.tool(ms_to_iso(start) or ts, p.get("callID") or p.get("id"), name, args, result=result,
                      is_error=is_error, duration_ms=int(end - start) if start and end and end >= start else None,
                      mcp=_mcp_of(name, self.servers))
        if is_error:
            return
        diffs = [f for f in meta.get("files") or [] if isinstance(f, dict) and isinstance(f.get("file"), str)]
        if diffs:  # 2.x: each file the call changed, with its line counts
            for f in diffs:
                path = self._absolute(f["file"])
                fs = ps.files.setdefault(path, FileStat())
                if path != call.file_path or name not in ("edit", "write", "apply_patch"):  # else tool() counted it
                    if f.get("status") == "added":
                        fs.writes += 1
                    else:
                        fs.edits += 1
                fs.lines_added += int(f.get("additions") or 0)
                fs.lines_removed += int(f.get("deletions") or 0)
        elif ops:
            for path, (op, added, removed) in ops.items():
                fs = ps.files.setdefault(path, FileStat())
                if path == call.file_path and name == "apply_patch":
                    fs.edits -= 1  # tool() counted the first file as an edit: count it below as what it was
                if op == "Add":
                    fs.writes += 1
                else:
                    fs.edits += 1
                fs.lines_added += added
                fs.lines_removed += removed
        elif call.file_path and name in ("edit", "multiedit", "write"):
            fs = ps.files.setdefault(call.file_path, FileStat())
            if isinstance(meta.get("diff"), str):
                added, removed = _diff_lines(meta["diff"])
            elif name == "write":
                added, removed = len(str(args.get("content") or "").splitlines()), 0
            else:
                added, removed = len(str(args.get("newString") or "").splitlines()), len(str(args.get("oldString") or "").splitlines())
            fs.lines_added += added
            fs.lines_removed += removed
        child = meta.get("sessionId") or meta.get("sessionID")  # 1.x task, 2.x subagent
        if name in ("task", "subagent") and isinstance(child, str):
            self.links[child] = (call.tool_use_id, args)

    def _absolute(self, path: str) -> str:
        if self.cwd and path and not path.startswith(("/", "~")) and not re.match(r"^[A-Za-z]:[/\\]", path):
            return posixpath.normpath(posixpath.join(self.cwd, path))
        return path

    def subagent(self, parent: SessionBuilder, child: dict) -> None:
        ps, info = self.ps, child["info"]
        aid = str(info["id"])
        call_id, args = self.links.get(aid, (None, {}))
        sub = ps.subagents.setdefault(aid, Subagent(aid))
        sub.agent_type = args.get("subagent_type") or args.get("agent") or info.get("agent") or None
        title = info.get("title") if isinstance(info.get("title"), str) and not _DEFAULT_TITLE_RE.match(info["title"]) else None
        sub.description = one_line(safe_text(str(args.get("description") or _SUBAGENT_TITLE_RE.sub("", title or ""))), 120) or None
        sub.tool_use_id = call_id
        b = SessionBuilder(ps, agent_id=aid)
        self.feed(b, _from_v2(child["messages"]) if child.get("format") == 2 else child["messages"])
        parent.stamps.extend(b.stamps)
        stamps = sorted(s for s in b.stamps if s)
        if stamps:
            sub.started_at, sub.ended_at = stamps[0], stamps[-1]
        calls = [c for c in ps.tool_calls if c.agent_id == aid]
        sub.n_tool_calls, sub.n_tool_errors = len(calls), sum(c.is_error for c in calls)
        models: Counter = Counter()
        for c in ps.api_calls:
            if c.agent_id == aid:
                sub.input_tokens += c.input_tokens
                sub.output_tokens += c.output_tokens
                sub.cache_read_tokens += c.cache_read_tokens
                sub.cache_write_tokens += c.cache_write_tokens
                sub.est_cost_usd += c.cost_usd
                if c.model:
                    models[c.model] += c.output_tokens or 1
        sub.model = models.most_common(1)[0][0] if models else None


def parse_opencode_session(doc: dict, *, servers: list[str] | None = None) -> ParsedSession | None:
    """A load_tree() document as a session; None when it never got a prompt."""
    info, messages = doc["info"], doc.get("messages") or []
    if doc.get("format") == 2:
        messages = _from_v2(messages)
    ps = ParsedSession(id=str(info["id"]))
    ps.entrypoint = "opencode"
    ps.cc_version = info.get("version") or None
    title = info.get("title")
    ps.ai_title = title.strip() if isinstance(title, str) and title.strip() and not _DEFAULT_TITLE_RE.match(title) else None
    ps.parse_errors = int(doc.get("parse_errors") or 0)
    directory = info.get("directory") if isinstance(info.get("directory"), str) else ""  # "" in sessions from before 1.2
    b = SessionBuilder(ps)
    r = _Reader(ps, servers or [], directory or None)
    r.feed(b, messages)
    if not ps.n_prompts:
        return None
    for child in doc.get("children") or []:
        r.subagent(b, child)
    if ms_to_iso(_ms(info.get("time_created"))):  # not time_updated: a rename or an archive moves it later
        b.stamps.append(ms_to_iso(_ms(info.get("time_created"))))
    ps.project_path = directory or (r.cwds.most_common(1)[0][0] if r.cwds else None)
    ps.files = {fp: st for fp, st in ps.files.items() if st.reads or st.edits or st.writes}  # not folders it searched
    return finish_session(ps, b.stamps)
