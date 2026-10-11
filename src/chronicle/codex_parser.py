"""Parse OpenAI Codex (CLI / VS Code / Desktop) session logs, "rollouts", into the same model as Claude Code sessions.

Layout (Codex 0.12x+):
  ~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl      one file per thread
  ~/.codex/session_index.jsonl                                       thread titles
  ~/.codex/external_agent_session_imports.json                       Claude Code sessions imported by Codex Desktop
  ~/.codex/memories/*.md, memories/rollout_summaries/*.md            Codex's own memory notes
Every line is {"timestamp", "type", "payload", "ordinal"}: session_meta, turn_context, response_item,
event_msg, token_usage_record, compacted, world_state, ... Subagent threads are separate rollouts whose
session_meta.source names the parent thread.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Sequence

from .parser import (
    IDLE_GAP_CAP_S,
    TEXT_LIMITS,
    ApiCall,
    Event,
    FileStat,
    ParsedSession,
    Subagent,
    ToolCall,
    _TAG_RE,
    clean_prompt,
    parse_command,
)
from . import artifacts
from .pricing import normalize_model, openai_cost
from .util import iter_jsonl, one_line, parse_ts, safe_text, truncate

log = logging.getLogger("interlatch.codex")

CODEX_PARSER_VERSION = 4  # 4: artifacts (files created, PRs, commits, generated images)
ROLLOUT_RE = re.compile(r"^rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-([0-9a-f-]{36})\.jsonl(\.gz)?$")
_UUID_RE = re.compile(r"^[0-9a-f-]{36}$")
_IDE_REQUEST_RE = re.compile(r"^## My request( for Codex)?:[ \t]*$", re.M)
_PATCH_FILE_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$", re.M)
_EXIT_CODE_RE = re.compile(r"(?:^|\n)\s*(?:Exit code|exit code|Process exited with code|exited with code)[: ]+(\d+)")

# Code mode (Codex 0.142+): the exec tool runs a JS script that calls the real tools as functions,
#   const r = await tools.exec_command({cmd: "npm test"}); text(r);
# and returns "Script completed|failed" with one JSON chunk ({"chunk_id", "exit_code", "output"}) per result.
# A long script answers "Script running with cell ID N"; a later wait({cell_id: N}) returns its result.
_JS_SCAN_RE = re.compile(r"[\"'`]|//|/\*|(?<![\w$.])tools\.([A-Za-z_]\w*)\s*\(")
_JS_STOP = {q: re.compile(r"[\\%s]" % q) for q in "\"'`"}
_JS_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0", "\n": ""}
_JS_FIELDS = {k: re.compile(r"(?<![\w$])[\"']?%s[\"']?\s*:\s*([\"'`])" % k)
              for k in ("cmd", "path", "url", "q", "query", "message", "prompt", "chars")}
_JS_PATCH_RE = re.compile(r"([\"'`])\*\*\* Begin Patch")
_HEX = re.compile(r"[0-9a-fA-F]+")
_CELL_RE = re.compile(r"Script running with cell ID (\S+)")
_CHUNK_RE = re.compile(r'\{"chunk_id"')
_DECODER = json.JSONDecoder()


# ------------------------------------------------------------------ discovery
def rollout_files(codex_dir: Path) -> list[Path]:
    root = codex_dir / "sessions"
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("rollout-*.jsonl*") if ROLLOUT_RE.match(p.name))


def rollout_id(path: Path) -> str | None:
    m = ROLLOUT_RE.match(path.name)
    return m.group(2) if m else None


def read_meta(path: Path) -> dict:
    """The session_meta payload (first line) of a rollout."""
    for d in iter_jsonl(path):
        return d.get("payload") or {} if d.get("type") == "session_meta" else {}
    return {}


def parent_thread_id(meta: dict) -> str | None:
    src = meta.get("source")
    if isinstance(src, dict):
        spawn = (src.get("subagent") or {}).get("thread_spawn") or {}
        return spawn.get("parent_thread_id")
    return None


def load_titles(codex_dir: Path) -> dict[str, str]:
    titles: dict[str, str] = {}
    path = codex_dir / "session_index.jsonl"
    if path.exists():
        for d in iter_jsonl(path):
            if d.get("id") and d.get("thread_name"):
                titles[d["id"]] = one_line(safe_text(d["thread_name"]), 160)
    return titles


def load_imports(codex_dir: Path) -> dict[str, dict]:
    """imported_thread_id -> {claude_id, title, source_path} for Claude Code sessions that Codex Desktop imported."""
    out: dict[str, dict] = {}
    path = codex_dir / "external_agent_session_imports.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return out
    for r in data.get("records") or [] if isinstance(data, dict) else []:
        if not isinstance(r, dict) or not r.get("imported_thread_id"):
            continue
        stem = Path(r.get("source_path") or "").stem
        out[r["imported_thread_id"]] = {
            "claude_id": stem if _UUID_RE.match(stem) else None,
            "title": safe_text(r.get("title") or ""),
            "source_path": r.get("source_path") or "",
            "imported_at": r.get("imported_at"),
        }
    return out


# ------------------------------------------------------------------ classification
def classify_codex_user(text: str) -> tuple[str, str]:
    """-> (kind, text). Unwraps Codex's IDE-context envelope so only the developer's request counts as the prompt."""
    t = text.lstrip()
    if t.startswith("# Context from my IDE"):
        m = _IDE_REQUEST_RE.search(t)
        return ("prompt", t[m.end():].strip()) if m else ("meta", "[IDE context]")
    if t.startswith(("<environment_context", "<recommended_plugins", "# AGENTS.md", "<skill", "<user_instructions",
                     "<permissions", "<app_instructions", "<collaboration_mode")):
        return "meta", one_line(t, 120)
    if t.startswith("<turn_aborted"):
        return "meta", "[turn aborted]"  # the event_msg carries the interrupt
    if t.startswith(("<task-notification", "[SYSTEM NOTIFICATION")):
        return "notification", t
    if t.startswith(("<command-name>", "<command-message")):
        return "command", t
    if t.startswith(("<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat")):
        return "command_output", _TAG_RE.sub("", t).strip()
    if t.startswith("[Request interrupted by user"):
        return "interrupt", t
    if t.startswith("This session is being continued from a previous conversation"):
        return "compact_summary", t
    cleaned = clean_prompt(t)
    return ("prompt", cleaned) if cleaned else ("meta", "[IDE context]")


def _output_text(output) -> tuple[str, int, bool | None]:
    """Flatten a tool output -> (text, n_images, is_error or None when the output does not say)."""
    images = 0
    if isinstance(output, list):
        parts = []
        for c in output:
            if not isinstance(c, dict):
                continue
            if c.get("type") in ("input_text", "output_text", "text"):
                parts.append(c.get("text") or "")
            elif c.get("type") == "input_image":
                images += 1
                parts.append("[image]")
        text = "\n".join(parts)
    elif isinstance(output, str):
        text = output
    elif output is None:
        text = ""
    else:
        text = json.dumps(output, ensure_ascii=False)
    err: bool | None = None
    codes: list[int] = []
    if '{"chunk_id"' in text:
        text, codes = _render_chunks(text)
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            code = (obj.get("metadata") or {}).get("exit_code") if isinstance(obj.get("metadata"), dict) else None
            if isinstance(code, int):
                err = code != 0
            if isinstance(obj.get("output"), str):
                text = obj["output"]
    m = _EXIT_CODE_RE.search(text[:600])
    if m:
        err = int(m.group(1)) != 0
    if codes:
        err = any(codes)
    low = text[:80].lower()
    if low.startswith(("apply_patch verification failed", "error:", "failed to", "script failed", "aborted by user")):
        err = True
    return text, images, err


def _js_string(src: str, start: int) -> tuple[str | None, int]:
    """Decode the JS string literal opening at src[start] -> (value or None if it never closes, index after it)."""
    stop = _JS_STOP.get(src[start])
    if stop is None:
        return None, start + 1
    out, i = [], start + 1
    while True:
        m = stop.search(src, i)
        if not m:
            return None, len(src)
        out.append(src[i:m.start()])
        i = m.start()
        if src[i] != "\\":
            break
        nxt = src[i + 1:i + 2]
        width = 4 if nxt == "u" else 2 if nxt == "x" else 0
        if width and _HEX.fullmatch(src, i + 2, i + 2 + width):
            out.append(chr(int(src[i + 2:i + 2 + width], 16)))
            i += 2 + width
        else:
            out.append(_JS_ESCAPES.get(nxt, nxt))
            i += 2
    text = "".join(out)
    try:  # 😀 escapes arrive as two halves of a surrogate pair
        text = text.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeError:
        pass
    return text, i + 1


def _code_mode_calls(src: str) -> list[tuple[str, dict]]:
    """The tools a code-mode exec script calls, in order -> [(tool, {string argument: value})].
    String literals and comments are skipped, so a command that merely mentions tools.x( is not a call."""
    found, i = [], 0
    while (m := _JS_SCAN_RE.search(src, i)) is not None:
        tok = m.group(0)
        if m.group(1):
            found.append((m.group(1), m.end()))
            i = m.end()
        elif tok == "//":
            j = src.find("\n", m.end())
            i = len(src) if j < 0 else j
        elif tok == "/*":
            j = src.find("*/", m.end())
            i = len(src) if j < 0 else j + 2
        else:
            i = _js_string(src, m.start())[1]
    calls = []
    for n, (name, pos) in enumerate(found):
        seg = src[pos:found[n + 1][1] if n + 1 < len(found) else len(src)]
        fields = {}
        for key, rx in _JS_FIELDS.items():
            fm = rx.search(seg)
            value = _js_string(seg, fm.start(1))[0] if fm else None
            if value:
                fields[key] = value
        calls.append((name, fields))
    return calls


def _js_tool_name(raw: str) -> tuple[str, str | None]:
    """Code-mode function name -> (tool name as function calls spell it, MCP server)."""
    if raw.startswith("mcp__"):
        parts = raw.split("__")
        return raw, parts[1] if len(parts) > 2 else None
    namespace, sep, base = raw.partition("__")
    return (f"{namespace}.{base}", None) if sep else (raw, None)


def _render_chunks(text: str) -> tuple[str, list[int]]:
    """Replace code-mode JSON result chunks with their output -> (readable text, exit codes)."""
    out, codes, pos = [], [], 0
    for m in _CHUNK_RE.finditer(text):
        if m.start() < pos:
            continue
        try:
            obj, end = _DECODER.raw_decode(text, m.start())
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        out.append(text[pos:m.start()])
        code = obj.get("exit_code")
        if isinstance(code, int):
            codes.append(code)
            out.append(f"Exit code: {code}\n")
        out.append(obj["output"] if isinstance(obj.get("output"), str) else "")
        pos = end
    out.append(text[pos:])
    return "".join(out), codes


def _patch_files(patch: str, cwd: str | None) -> dict[str, tuple[str, int, int]]:
    """apply_patch text -> {path: (op, lines_added, lines_removed)}."""
    files: dict[str, tuple[str, int, int]] = {}
    current = None
    for line in patch.splitlines():
        m = _PATCH_FILE_RE.match(line)
        if m:
            op, path = m.group(1), m.group(2).strip()
            if cwd and not path.startswith("/"):
                path = f"{cwd.rstrip('/')}/{path}"
            current = path
            files[current] = (op, 0, 0)
            continue
        if current is None or line.startswith("***"):
            continue
        op, added, removed = files[current]
        if line.startswith("+"):
            files[current] = (op, added + 1, removed)
        elif line.startswith("-"):
            files[current] = (op, added, removed + 1)
    return files


# ------------------------------------------------------------------ thread parser
class _CodexThread:
    def __init__(self, ps: ParsedSession, agent_id: str = ""):
        self.ps = ps
        self.agent_id = agent_id
        self.seq = 0
        self.pending: dict[str, tuple[ToolCall, str | None]] = {}
        self.model: str | None = None
        self.cwd: str | None = None
        self.first_ts: str | None = None
        self.last_ts: str | None = None
        self.n_tool_calls = 0
        self.n_tool_errors = 0
        self.n_usage = 0
        self.task_prompt: str | None = None
        self.usage_calls: list[ApiCall] = []  # from token_usage_record (newer Codex)
        self.tc_calls: list[ApiCall] = []     # from event_msg/token_count (older Codex), used only as a fallback
        self.last_total = 0
        # file edits: newer Codex reports them as FileChange items, older as apply_patch calls; never count both
        self.patch_ops: list[tuple[str, str, int, int, ToolCall]] = []  # + the call, to skip a patch that failed
        self.change_ops: list[tuple[str, str, int, int, str | None, str | None]] = []  # + when, and a new file's content
        self.cells: dict[str, tuple[ToolCall, str | None]] = {}  # code-mode cell id -> the call whose script still runs

    # ---------- helpers
    def _fail(self, call: ToolCall) -> None:
        if not call.is_error:
            call.is_error = True
            self.n_tool_errors += 1

    def _event(self, ts, role, kind, text, *, limit_key=None, **kw) -> Event:
        ev = Event(self.agent_id, self.seq, ts, role, kind, truncate(safe_text(text), TEXT_LIMITS.get(limit_key or kind, 2_000)), **kw)
        self.seq += 1
        self.ps.events.append(ev)
        return ev

    def _touch(self, ts: str | None) -> None:
        if not ts:
            return
        if self.first_ts is None or ts < self.first_ts:
            self.first_ts = ts
        if self.last_ts is None or ts > self.last_ts:
            self.last_ts = ts
        self.ps._timestamps.append(ts)

    # ---------- feed
    def feed_file(self, path: Path) -> None:
        for d in iter_jsonl(path):
            self.feed(d)
        self.finish()

    def feed(self, d: dict) -> None:
        try:
            self._feed(d)
        except Exception as exc:  # unexpected shapes must never lose the session
            self.ps.parse_errors += 1
            if self.ps.parse_errors <= 3:
                log.warning("skipped a malformed rollout line in %s: %r", self.ps.id, exc)

    def _feed(self, d: dict) -> None:
        t = d.get("type")
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        ts = d.get("timestamp")
        if t in ("session_meta", "turn_context", "response_item", "event_msg", "token_usage_record", "compacted"):
            self._touch(ts)
        if t == "session_meta":
            self._meta(p)
        elif t == "turn_context":
            self.model = normalize_model(p.get("model")) or self.model
            if p.get("cwd"):
                self.cwd = p["cwd"]
                if not self.agent_id and not self.ps.project_path:
                    self.ps.project_path = p["cwd"]
            policy = p.get("approval_policy")
            if not self.agent_id and policy:
                self.ps.permission_mode = policy if isinstance(policy, str) else one_line(json.dumps(policy), 60)
        elif t == "response_item":
            self._item(p, ts)
        elif t == "event_msg":
            self._event_msg(p, ts)
        elif t == "token_usage_record":
            self._usage(p, ts)
        elif t == "compacted":
            if not self.agent_id:
                self.ps.n_compactions += 1
            self._event(ts, "system", "compact", f"Context compacted (window {p.get('window_number', '?')})",
                        meta={"trigger": "auto", "window": p.get("window_number")})

    def _meta(self, p: dict) -> None:
        ps = self.ps
        if self.agent_id:
            return
        ps.project_path = p.get("cwd") or ps.project_path
        ps.cc_version = p.get("cli_version") or ps.cc_version
        ps.entrypoint = f"codex:{p.get('originator') or 'codex'}"
        git = p.get("git") if isinstance(p.get("git"), dict) else {}
        if git.get("branch"):
            ps.git_branch = git["branch"]
            ps.branches[git["branch"]] += 1
        if isinstance(git.get("repository_url"), str):
            ps.git_remote = git["repository_url"]
        if p.get("timestamp"):
            self._touch(p["timestamp"])

    # ---------- response items
    def _item(self, p: dict, ts: str | None) -> None:
        pt = p.get("type")
        if pt == "message":
            self._message(p, ts)
        elif pt == "reasoning":
            summary = p.get("summary") or []
            text = "\n".join(s.get("text", "") for s in summary if isinstance(s, dict)).strip()
            if text:
                self._event(ts, "assistant", "thinking", text)
        elif pt in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
            self._tool_use(p, ts)
        elif pt in ("function_call_output", "custom_tool_call_output", "tool_search_output"):
            self._tool_result(p, ts)
        elif pt == "agent_message":
            text = "\n".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict)).strip()
            self._event(ts, "user", "notification", f"[{p.get('author') or 'agent'} → {p.get('recipient') or 'agent'}] {text}")

    def _message(self, p: dict, ts: str | None) -> None:
        role = p.get("role")
        content = p.get("content") or []
        texts = [c.get("text") or "" for c in content if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text")]
        images = sum(1 for c in content if isinstance(c, dict) and c.get("type") == "input_image")
        text = "\n".join(texts).strip()
        if role == "assistant":
            if text:
                self._event(ts, "assistant", "text", text, meta={"model": self.model, "phase": p.get("phase")} if self.model or p.get("phase") else None)
            return
        if role != "user":
            return  # developer/system instructions
        kind, body = classify_codex_user(text) if text else ("prompt", "")
        if kind == "prompt":
            self._prompt(body, ts, images=images)
        elif kind == "command":
            name, args = parse_command(body)
            if name and not self.agent_id:
                self.ps.commands[name] += 1
            self._event(ts, "user", "command", f"{name} {args}".strip() or one_line(body, 200))
        elif kind == "interrupt":
            if not self.agent_id:
                self.ps.n_interrupts += 1
            self._event(ts, "user", "interrupt", body)
        else:
            self._event(ts, "user", kind, body)

    def _prompt(self, text: str, ts: str | None, *, images: int = 0) -> None:
        ps = self.ps
        if images:
            ps.n_images += images
            text = (text + "\n" if text else "") + " ".join(["[image]"] * images)
        if not text:
            return
        if self.agent_id:
            if self.task_prompt is None:
                self.task_prompt = text
            self._event(ts, "user", "prompt", text, meta={"subagent": True})
            return
        ps.n_prompts += 1
        if ps.first_prompt is None:
            ps.first_prompt = text
        ps.last_prompt = text
        self._event(ts, "user", "prompt", text)

    # ---------- tools
    def _tool_use(self, p: dict, ts: str | None) -> None:
        ps = self.ps
        pt = p.get("type")
        call_id = p.get("call_id") or p.get("id")
        args: dict = {}
        raw = p.get("arguments") if pt == "function_call" else p.get("input")
        if isinstance(raw, str) and pt == "function_call":
            try:
                args = json.loads(raw)
            except ValueError:
                args = {"raw": raw}
        elif isinstance(raw, dict):
            args = raw
        namespace, base = p.get("namespace"), p.get("name") or pt
        if pt == "web_search_call":
            base, args = "web_search", {"query": (p.get("action") or {}).get("query") if isinstance(p.get("action"), dict) else ""}
        elif pt == "tool_search_call":
            base, args = "tool_search", p.get("arguments") if isinstance(p.get("arguments"), dict) else {}
        mcp = namespace[5:] if isinstance(namespace, str) and namespace.startswith("mcp__") else None
        name = f"{namespace}__{base}" if mcp else (f"{namespace}.{base}" if namespace else base)
        inner = _code_mode_calls(raw) if pt == "custom_tool_call" and base == "exec" and isinstance(raw, str) else []
        if inner:
            name, mcp, summary, fp, cmd, file_ops, reads = self._code_mode(raw, inner)
            args = {"script": raw}
        else:
            summary, fp, cmd, file_ops = self._summarize(base, raw, args)
            reads = [fp] if fp and base in ("view_image", "read_file") else []
        call = ToolCall(self.agent_id, call_id, ts, safe_text(name), mcp, safe_text(summary), fp, cmd, input=args or None)
        ps.tool_calls.append(call)
        ps.tools[name] += 1
        self.n_tool_calls += 1
        if mcp:
            ps.mcp_servers[mcp] += 1
        for path, (op, added, removed) in file_ops.items():
            self.patch_ops.append((path, op.lower(), added, removed, call))
        for path in reads:
            ps.files.setdefault(path, FileStat()).reads += 1
        if call_id:
            self.pending[call_id] = (call, ts)
        shown = summary if summary and summary != name else ""
        self._event(ts, "assistant", "tool_use", f"{name}: {shown}" if shown else name, tool_name=name, tool_use_id=call_id,
                    meta={"input": truncate(json.dumps(args, ensure_ascii=False, default=str), 1_500)} if args else None)

    def _code_mode(self, src: str, inner: list[tuple[str, dict]]):
        """A code-mode exec script -> (name, mcp server, summary, file_path, command, patch file ops, files read).
        It is named after the tool it calls; a script mixing several tools stays "exec"."""
        cwd = self.cwd or self.ps.project_path
        root = (cwd or "").rstrip("/") + "/"
        ops: dict[str, tuple[str, int, int]] = {}
        for m in _JS_PATCH_RE.finditer(src):
            patch = _js_string(src, m.start(1))[0] or ""
            for path, (op, added, removed) in _patch_files(safe_text(patch), cwd).items():
                prev = ops.get(path)
                ops[path] = (prev[0], prev[1] + added, prev[2] + removed) if prev else (op, added, removed)
        names = [_js_tool_name(n) for n, _ in inner]
        cmds = [f["cmd"] for _, f in inner if f.get("cmd")]
        reads = [f["path"] for n, f in inner if n == "view_image" and f.get("path")]
        detail = ", ".join(f"{p[len(root):] if p.startswith(root) else p} (+{a}/-{r})" for p, (_, a, r) in list(ops.items())[:6]) \
            or next((f[k] for _, f in inner for k in _JS_FIELDS if f.get(k)), "")
        distinct = list(dict.fromkeys(names))
        if len(distinct) == 1:
            (name, mcp), more = distinct[0], len(inner) - 1
            summary = detail + (f" (+{more} more)" if more else "")
        else:
            name, mcp = "exec", None
            counts = {n: sum(1 for m, _ in names if m == n) for n, _ in distinct}
            summary = ", ".join(f"{n} ×{c}" if c > 1 else n for n, c in counts.items()) + (f": {detail}" if detail else "")
        cmd = safe_text("\n".join(cmds)) if cmds else None
        return name, mcp, one_line(safe_text(summary), 240), next(iter(ops), None) or (reads[0] if reads else None), cmd, ops, reads

    def _summarize(self, base: str, raw, args: dict) -> tuple[str, str | None, str | None, dict]:
        """-> (summary, file_path, command, patch file ops)."""
        if base == "wait" and "cell_id" in args:
            return f"cell {args['cell_id']}", None, None, {}
        if base in ("exec", "exec_command", "shell", "container.exec"):
            # function calls carry JSON arguments ({"cmd": ...}); the custom exec tool carries the raw command text
            cmd = args.get("cmd") or args.get("command") or (raw if isinstance(raw, str) and not args else "")
            if isinstance(cmd, list):
                cmd = " ".join(str(c) for c in cmd)
            cmd = safe_text(cmd)
            return one_line(cmd, 240), None, cmd, {}
        if base == "apply_patch":
            patch = raw if isinstance(raw, str) else args.get("patch") or args.get("input") or ""
            ops = _patch_files(safe_text(patch), self.cwd or self.ps.project_path)
            root = (self.cwd or self.ps.project_path or "").rstrip("/") + "/"
            shown = ", ".join(f"{p[len(root):] if p.startswith(root) else p} (+{a}/-{r})" for p, (_, a, r) in list(ops.items())[:6])
            return one_line(shown or "patch", 240), next(iter(ops), None), None, ops
        if base == "write_stdin":
            return one_line(safe_text(args.get("chars") or ""), 200), None, None, {}
        if base == "view_image":
            return safe_text(args.get("path") or ""), safe_text(args.get("path") or "") or None, None, {}
        if base == "update_plan":
            plan = args.get("plan") or []
            done = sum(1 for s in plan if isinstance(s, dict) and s.get("status") == "completed")
            return f"{len(plan)} steps ({done} done)", None, None, {}
        if base == "spawn_agent":
            return one_line(safe_text(args.get("message") or args.get("prompt") or args.get("task") or "agent"), 200), None, None, {}
        if base in ("web_search", "run"):
            q = args.get("query") or args.get("search_query") or args
            return one_line(safe_text(q if isinstance(q, str) else json.dumps(q, ensure_ascii=False)), 200), None, None, {}
        if not args:
            return base, None, None, {}
        compact = ", ".join(f"{k}={one_line(safe_text(v), 60)}" for k, v in list(args.items())[:3])
        return one_line(compact, 200), None, None, {}

    def _tool_result(self, p: dict, ts: str | None) -> None:
        call_id = p.get("call_id") or p.get("id")
        text, images, err = _output_text(p.get("output") if p.get("type") != "tool_search_output" else p.get("tools"))
        self.ps.n_result_images += images
        call, started = self.pending.pop(call_id, (None, None))
        is_error = bool(err)
        if call:
            call.result_chars = len(text)
            end, start = parse_ts(ts), parse_ts(started)
            if start and end:
                call.duration_ms = max(int((end - start).total_seconds() * 1000), 0)
            owner, cell = call, _CELL_RE.match(text)
            if cell and call.name != "wait":
                self.cells[cell.group(1)] = (call, started)
            elif not cell and call.name == "wait" and isinstance(call.input, dict):
                # the script finished: its result belongs to the call that started it, not to the wait
                origin = self.cells.pop(str(call.input.get("cell_id")), None)
                if origin:
                    owner, origin_start = origin[0], parse_ts(origin[1])
                    if origin_start and end:
                        owner.duration_ms = max(int((end - origin_start).total_seconds() * 1000), 0)
            if is_error:
                self._fail(owner)
            elif not cell:
                artifacts.from_tool(self.ps, owner.name, owner.input, text, ts=ts, agent_id=self.agent_id,
                                    tool_use_id=owner.tool_use_id, command=owner.command)
        self._event(ts, "user", "tool_result", text, limit_key="tool_error" if is_error else "tool_result",
                    tool_name=call.name if call else None, tool_use_id=call_id, is_error=is_error)

    # ---------- events & usage
    def _event_msg(self, p: dict, ts: str | None) -> None:
        pt = p.get("type")
        if pt == "turn_aborted":
            if not self.agent_id:
                self.ps.n_interrupts += 1
            self._event(ts, "user", "interrupt", f"[Turn aborted: {p.get('reason') or 'interrupted'}]")
        elif pt == "task_complete" and p.get("error"):
            self.ps.n_api_errors += 1
            self._event(ts, "system", "api_error", one_line(safe_text(p["error"]), 300))
        elif pt == "patch_apply_end" and p.get("call_id") in self.pending and p.get("success") is False:
            self._fail(self.pending[p["call_id"]][0])
        elif pt == "item_completed" and isinstance(p.get("item"), dict):
            self._item_completed(p["item"], ts)
        elif pt == "token_count" and isinstance(p.get("info"), dict):
            info = p["info"]
            total = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
            last = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
            tt = total.get("total_tokens") or 0
            if tt > self.last_total and last:  # the same totals are re-emitted; count each response once
                self.last_total = tt
                self.tc_calls.append(self._api_call(last, f"tc#{len(self.tc_calls) + 1}", ts))

    def _item_completed(self, item: dict, ts: str | None = None) -> None:
        """Newer Codex: FileChange items carry the edits, CommandExecution items the real exit codes."""
        kind = item.get("type")
        if kind == "FileChange" and isinstance(item.get("changes"), dict):
            root = (self.cwd or self.ps.project_path or "").rstrip("/")
            for path, change in item["changes"].items():
                if not isinstance(change, dict):
                    continue
                path = safe_text(path)
                if root and not path.startswith("/"):
                    path = f"{root}/{path}"
                op = str(change.get("type") or "update").lower()
                added = removed = 0
                if isinstance(change.get("unified_diff"), str):
                    for line in change["unified_diff"].splitlines():
                        if line.startswith("+") and not line.startswith("+++"):
                            added += 1
                        elif line.startswith("-") and not line.startswith("---"):
                            removed += 1
                elif isinstance(change.get("content"), str):
                    lines = change["content"].count("\n") + (0 if change["content"].endswith("\n") else 1)
                    added, removed = (lines, 0) if op == "add" else (0, lines if op == "delete" else 0)
                self.change_ops.append((path, op, added, removed, ts, change.get("content") if op == "add" else None))
        elif kind == "CommandExecution":
            self._command_result(item)

    def _command_result(self, item: dict) -> None:
        """Mark the tool call that ran this command as failed when Codex reports a failure."""
        code = item.get("exit_code")
        failed = item.get("status") == "failed" or (isinstance(code, int) and code != 0)
        if not failed:
            return
        command = safe_text(item.get("command") if isinstance(item.get("command"), str) else " ".join(map(str, item.get("command") or [])))
        recent = [c for c in self.ps.tool_calls[-40:] if c.agent_id == self.agent_id and c.command is not None]
        match = next((c for c in reversed(recent) if command and c.command and (c.command in command or command in c.command)), None)
        match = match or (recent[-1] if recent else None)
        if match:
            self._fail(match)

    def _usage(self, p: dict, ts: str | None) -> None:
        u = p.get("usage") if isinstance(p.get("usage"), dict) else {}
        if not u:
            return
        self.n_usage += 1
        self.usage_calls.append(self._api_call(u, p.get("response_id") or f"{p.get('turn_id') or 'turn'}#{self.n_usage}", ts))

    def _api_call(self, u: dict, msg_id: str, ts: str | None) -> ApiCall:
        total_in = u.get("input_tokens") or 0
        cached = u.get("cached_input_tokens") or 0
        uncached = max(total_in - cached, 0)
        out = u.get("output_tokens") or 0
        return ApiCall(self.agent_id, msg_id, ts, self.model, input_tokens=uncached, output_tokens=out,
                       cache_read_tokens=cached, cache_write_tokens=u.get("cache_write_input_tokens") or 0,
                       cost_usd=openai_cost(self.model, uncached, cached, out))

    def finish(self) -> None:
        ps = self.ps
        for path, op, added, removed, *how in self.change_ops or self.patch_ops:
            if op == "add" and len(how) == 2:  # a FileChange: applied, with the new file's content
                artifacts.file_written(ps, path, content=how[1], ts=how[0], agent_id=self.agent_id)
            elif op == "add" and not how[0].is_error:
                artifacts.file_written(ps, path, ts=how[0].ts, agent_id=self.agent_id, tool_use_id=how[0].tool_use_id)
            fs = ps.files.setdefault(path, FileStat())
            if op == "add":
                fs.writes += 1
            else:
                fs.edits += 1
            fs.lines_added += added
            fs.lines_removed += removed
        calls = self.usage_calls or self.tc_calls
        seen = {c.msg_id for c in ps.api_calls if c.agent_id == self.agent_id}
        for c in calls:
            if c.msg_id in seen:
                continue
            ps.api_calls.append(c)
            if c.model:
                ps.models[c.model] += c.output_tokens
        if not self.agent_id:
            return
        sub = ps.subagents.setdefault(self.agent_id, Subagent(self.agent_id))
        sub.agent_type = sub.agent_type or "codex-agent"
        sub.description = sub.description or one_line(self.task_prompt or "", 120) or None
        sub.started_at, sub.ended_at = self.first_ts, self.last_ts
        sub.n_tool_calls, sub.n_tool_errors = self.n_tool_calls, self.n_tool_errors
        sub.model = sub.model or self.model
        for c in ps.api_calls:
            if c.agent_id == self.agent_id:
                sub.input_tokens += c.input_tokens
                sub.output_tokens += c.output_tokens
                sub.cache_read_tokens += c.cache_read_tokens
                sub.cache_write_tokens += c.cache_write_tokens
                sub.est_cost_usd += c.cost_usd


# ------------------------------------------------------------------ entry point
def parse_codex_session(main: Path, subagent_files: Sequence[Path] = (), *, title: str | None = None) -> ParsedSession:
    ps = ParsedSession(id=rollout_id(main) or main.stem)
    ps.entrypoint = "codex"
    _CodexThread(ps).feed_file(main)
    for f in subagent_files:
        aid = rollout_id(f) or f.stem
        ps.subagents.setdefault(aid, Subagent(aid, agent_type="codex-agent"))
        _CodexThread(ps, aid).feed_file(f)
    if title:
        ps.ai_title = title
    stamps = sorted(ps._timestamps)
    if stamps:
        ps.started_at, ps.ended_at = stamps[0], stamps[-1]
        start, end = parse_ts(stamps[0]), parse_ts(stamps[-1])
        if start and end:
            ps.duration_s = (end - start).total_seconds()
        active, prev = 0.0, None
        for s in stamps:
            cur = parse_ts(s)
            if cur and prev:
                active += min(max((cur - prev).total_seconds(), 0.0), IDLE_GAP_CAP_S)
            prev = cur or prev
        ps.active_s = active
    ps.events.sort(key=lambda e: (e.agent_id != "", e.agent_id, e.seq))
    return ps
