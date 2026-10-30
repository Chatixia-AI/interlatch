"""What a session made: the documents, pages, diagrams, decks, images, published links, pull requests and commits an
agent produced. Each comes from an explicit signal in the transcript (a file the agent created, a page it published,
a PR it opened, a commit it made), never from guessing at the filesystem. Parsers call these while they read a
transcript, when they still hold a tool's full input; ingest stores the result in the `artifacts` table.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from .util import one_line, safe_text

KINDS = ("doc", "page", "diagram", "deck", "sheet", "image", "published", "pr", "commit")
EXT_KIND = {
    ".md": "doc", ".markdown": "doc", ".mdx": "doc", ".rst": "doc", ".pdf": "doc", ".docx": "doc", ".odt": "doc", ".rtf": "doc",
    ".html": "page", ".htm": "page",
    ".svg": "diagram", ".excalidraw": "diagram", ".drawio": "diagram", ".mmd": "diagram", ".mermaid": "diagram", ".puml": "diagram",
    ".pptx": "deck", ".odp": "deck",  # not .key: it names private keys as often as Keynote decks
    ".xlsx": "sheet", ".ods": "sheet", ".csv": "sheet",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
}
# where writing a file is housekeeping, not a deliverable: agent config and memory, dependencies, build output, fixtures
_SKIP_DIRS = {".claude", ".codex", ".git", "node_modules", ".venv", "venv", "site-packages", "__pycache__", ".playwright-mcp",
              "dist", "build", ".next", "coverage", "fixtures", "__snapshots__", "testdata"}
_SKIP_NAMES = {"claude.md", "agents.md", "gemini.md", "memory.md", "changelog.md"}
# an HTML page, SVG or image inside an app's source tree is part of the app (a template, an icon), not something made to be read
_SOURCE_DIRS = {"src", "app", "public", "static", "templates", "components", "pages", "views", "layouts", "web", "frontend",
                "client", "media", "icons", "lib", "resources"}
# claude.ai artifact types -> kind
_CLAUDE_AI_TYPES = {"text/markdown": "doc", "text/html": "page", "image/svg+xml": "diagram", "application/vnd.ant.mermaid": "diagram",
                    "application/vnd.ant.react": "page"}
_MIME_KIND = {"text/markdown": "doc", "text/html": "page", "image/svg+xml": "diagram", "application/pdf": "doc",
              "application/vnd.openxmlformats-officedocument.presentationml.presentation": "deck",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "doc",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "sheet", "text/csv": "sheet"}

_PR_URL_RE = re.compile(r"https://([\w.-]+)/([\w.-]+/[\w.-]+)/pull/(\d+)")  # github.com or a GitHub Enterprise host
_COMMIT_RE = re.compile(r"^\[([^\]\s]+)(?: \(root-commit\))? ([0-9a-f]{7,40})\] (.+)$", re.M)
_ONELINE_RE = re.compile(r"^([0-9a-f]{7,40}) (.+)$", re.M)  # `git log --oneline -1` after a quiet commit
# git commit where a shell command starts (not inside a string that merely mentions it)
_GIT_COMMIT_RE = re.compile(r"(?:^|[;&|(]|\bthen\b|\bdo\b)\s*git(?:\s+-C\s+\S+)?\s+commit\b", re.M)
_MESSAGE_RE = re.compile(r"""\s-[a-z]*m\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)')""")
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\1\b", re.S)
_CLAUDE_URL_RE = re.compile(r"https://claude\.ai/(?:code/)?(?:artifact|public/artifacts)/[\w-]+")
_GOOGLE_URL_RE = re.compile(r"https://(?:docs|drive)\.google\.com/[^\s\"')\]]+")
_SLACK_CANVAS_RE = re.compile(r"https://[\w.-]+\.slack\.com/(?:docs|canvas)/[^\s\"')\]]+")
# office files and PDFs a script saves (python-pptx, a converter): the session names the path, and the file was created
# while the session ran (finish() checks); a file it only read existed before
_OFFICE_EXT = {".pptx", ".odp", ".docx", ".odt", ".xlsx", ".ods", ".pdf"}
_OFFICE_EXT_RE = re.compile(r"\.(?:pptx|odp|docx|odt|xlsx|ods|pdf)(?![\w.])", re.I)
_PATH_STOP = frozenset("'\"`<>|;&(){}[]")
_MAX_PATH = 1024  # characters a path runs back from its extension
_BATCH = 8  # more files of one type saved into one folder than this is data being processed (downloads, a cache), not deliverables
_HASH_NAME_RE = re.compile(r"^[0-9a-f]{32,}$")
_NOT_MADE_HERE = tuple(os.path.expanduser(p) for p in ("~/Library/", "/Library/", "/System/", "/Applications/", "/private/var/folders/",
                                                       "/var/folders/"))
# bounded: an unbounded run before the folder made a long text without spaces take time growing with its square
_GENERATED_IMAGE_RE = re.compile(r"(/[^\s\"'`]{0,1024}/\.codex/generated_images/[^\s\"'`]{1,512}\.(?:png|jpe?g|webp))")
_TITLE_ARG_RE = re.compile(r"""--title\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)')""")


def kind_for_path(path: str | None) -> str | None:
    """The kind of deliverable a file is, or None when writing it is ordinary work (code, config, agent memory)."""
    if not path:
        return None
    low = path.lower()
    parts = low.replace("\\", "/").split("/")
    dirs = parts[:-1]
    if _SKIP_DIRS.intersection(dirs) or parts[-1] in _SKIP_NAMES:
        return None
    if any(d.startswith(".") and d != ".github" for d in dirs):  # a tool's own folder (.cursor, .coworker…)
        return None
    kind = EXT_KIND.get(os.path.splitext(low)[1])
    if kind in ("page", "diagram", "image", "sheet") and _SOURCE_DIRS.intersection(dirs):
        return None
    return kind


def digest(content) -> tuple[int | None, str | None]:
    if not isinstance(content, str):
        return None, None
    data = content.encode("utf-8", "surrogatepass")
    return len(data), hashlib.sha256(data).hexdigest()


def _record(ps, art: dict) -> None:
    """Add an artifact; the same file written again or the same link published again is one artifact, one more version."""
    key = art["key"]
    for prev in ps.outputs:
        if prev["key"] == key:
            prev["versions"] += 1
            prev["ts"] = art.get("ts") or prev["ts"]
            for k in ("title", "size", "sha256", "url", "tool_use_id", "agent_id"):
                if art.get(k) is not None:
                    prev[k] = art[k]
            prev["meta"].update(art.get("meta") or {})
            return
    ps.outputs.append({"versions": 1, "meta": {}, **art})


def file_written(ps, path: str | None, *, content=None, created: bool = True, ts=None, agent_id: str = "",
                 tool_use_id: str | None = None, where: str | None = None) -> None:
    """A file the agent wrote whole (a new file, or a full rewrite). Edits to an existing file are not artifacts."""
    kind = kind_for_path(path)
    if not kind or (where == "claude.ai" and not path.startswith("/mnt/user-data/outputs/")):
        return  # claude.ai drafts in its sandbox's home; what it hands over goes to outputs/
    size, sha = digest(content)
    _record(ps, {"key": f"file:{path}", "kind": kind, "action": "created" if created else "rewritten",
                 "title": os.path.basename(path), "path": path, "url": None, "size": size, "sha256": sha, "ts": ts,
                 "agent_id": agent_id, "tool_use_id": tool_use_id, "meta": {"where": where} if where else {}})


def _link(ps, kind: str, url: str, title: str | None, *, action: str, ts, agent_id: str, tool_use_id, meta=None) -> None:
    if not isinstance(url, str) or not re.match(r"https?://", url, re.I):
        return  # a transcript states the link: one the dashboard can't open as a page (javascript:, say) isn't kept
    _record(ps, {"key": f"url:{url}", "kind": kind, "action": action, "title": one_line(safe_text(title or ""), 200) or None,
                 "path": None, "url": url, "size": None, "sha256": None, "ts": ts, "agent_id": agent_id,
                 "tool_use_id": tool_use_id, "meta": dict(meta or {})})


def office_paths(text: str) -> list[str]:
    """The office files and PDFs a text names (`report.pdf`, `/tmp/out/deck.pptx`), each the whole word that ends in
    the extension. Found from each extension back to the start of its word, so the time grows with the text's length:
    a regex trying every start took time growing with its square on a long run without spaces (a base64 blob in a
    chat), and hung the dashboard for hours on one ChatGPT chat."""
    out: list[str] = []
    last = -1  # where the previous path ended: a later extension in the same word makes it longer, as a greedy match
    for m in _OFFICE_EXT_RE.finditer(text):
        start = i = m.start()
        floor = max(0, i - _MAX_PATH)
        while start > floor and not (text[start - 1].isspace() or text[start - 1] in _PATH_STOP):
            start -= 1
        if start == i:
            continue  # nothing before the extension
        if out and start < last:
            out[-1] = text[start:m.end()]
        else:
            out.append(text[start:m.end()])
        last = m.end()
    return out


def from_tool(ps, name: str, inp, text: str | None, *, ts=None, agent_id: str = "", tool_use_id: str | None = None,
              command: str | None = None, tur=None) -> None:
    """Artifacts a successful tool call made, read from its input and result: a published page, a PR, a commit, an image.
    `tur` is Claude Code's structured toolUseResult, when there is one."""
    inp = inp if isinstance(inp, dict) else {}
    text = text or ""
    tur = tur if isinstance(tur, dict) else {}
    base = name.rsplit("__", 1)[-1].rsplit(".", 1)[-1].lower()
    at = {"ts": ts, "agent_id": agent_id, "tool_use_id": tool_use_id}

    # a page published to claude.ai (Claude Code's Artifact tool); reading or listing artifacts made nothing
    if name == "Artifact" and (inp.get("action") or "publish") == "publish" and not inp.get("asset"):
        url = tur.get("url") if isinstance(tur.get("url"), str) else next(iter(_CLAUDE_URL_RE.findall(text)), None)
        if url:
            meta = {k: tur[k] for k in ("artifact_id", "version") if tur.get(k)}
            if isinstance(tur.get("path") or inp.get("file_path"), str):
                meta["source"] = tur.get("path") or inp.get("file_path")
            _link(ps, "published", url, tur.get("title") or inp.get("title") or inp.get("file_path"), action="published",
                  meta=meta, **at)
    elif "claude_docs" in name.lower() and base in ("batch", "create"):
        for url in dict.fromkeys(_CLAUDE_URL_RE.findall(text)):
            create = (inp.get("container") or {}).get("create") if isinstance(inp.get("container"), dict) else None
            title = create.get("name") if isinstance(create, dict) else None
            _link(ps, "published", url, title or "Claude doc", action="published", **at)
    elif "google_drive" in name.lower() and base in ("create_file", "copy_file"):
        for url in dict.fromkeys(_GOOGLE_URL_RE.findall(text)):
            _link(ps, "published", url, inp.get("title") or inp.get("name") or "Google Drive file", action="published", **at)
    elif base == "slack_create_canvas":
        for url in dict.fromkeys(_SLACK_CANVAS_RE.findall(text)):
            _link(ps, "published", url, inp.get("title") or "Slack canvas", action="published", **at)

    cmd = command or ""
    mentions = getattr(ps, "_mentions", None)
    if mentions is not None:
        for raw in office_paths(f"{cmd}\n{text}"):
            mentions.setdefault(raw, (ts, agent_id, tool_use_id))

    # pull requests: `gh pr create` in a shell, or a GitHub tool that opens one
    if "gh pr create" in cmd or base in ("create_pull_request", "create_pr"):
        m = _TITLE_ARG_RE.search(cmd)
        title = (m.group(1) or m.group(2)) if m else inp.get("title")
        for host, repo, number in dict.fromkeys(_PR_URL_RE.findall(text)):
            url = f"https://{host}/{repo}/pull/{number}"
            _link(ps, "pr", url, title or f"{repo}#{number}", action="opened", meta={"repo": repo, "number": int(number)}, **at)

    # commits: git's "[branch sha] subject" line, a `git log --oneline` after a quiet commit, or Claude Code's own record
    git = tur.get("gitOperation") if isinstance(tur.get("gitOperation"), dict) else {}
    if _commit_at(cmd) is not None or git.get("commit"):
        commits = list(_COMMIT_RE.findall(text))
        if not commits and "git log" in cmd:
            commits = [("", sha, subj) for sha, subj in _ONELINE_RE.findall(text)[:1]]  # the branch is not printed: don't guess
        c = git.get("commit") if isinstance(git.get("commit"), dict) else None
        if c and c.get("sha") and not any(s.startswith(c["sha"]) or c["sha"].startswith(s) for _, s, _ in commits):
            commits.append((c.get("branch") or "", c["sha"], ""))
        subject = commit_message(cmd)
        for branch, sha, subj in commits:
            _record(ps, {"key": f"commit:{sha[:12]}", "kind": "commit", "action": "committed",
                         "title": one_line(safe_text(subj or subject or ""), 200) or f"commit {sha[:7]}", "path": None,
                         "url": None, "size": None, "sha256": None, "meta": {"sha": sha, "branch": branch}, **at})
        if not commits and subject:  # a quiet commit that printed no hash: it succeeded, so it happened
            _record(ps, {"key": f"commit:{getattr(ps, 'id', '')}:{tool_use_id}", "kind": "commit", "action": "committed",
                         "title": one_line(safe_text(subject), 200), "path": None, "url": None, "size": None,
                         "sha256": None, "meta": {}, **at})

    # images an image model generated (Codex's image_gen tool, called directly or from a code-mode script)
    script = inp.get("script") if isinstance(inp.get("script"), str) else ""
    if "imagegen" in base.replace("_", "") or "imagegen(" in script.replace("_", ""):
        prompt = inp.get("prompt") if isinstance(inp.get("prompt"), str) else _script_prompt(script)
        for path in dict.fromkeys(_GENERATED_IMAGE_RE.findall(text) if ".codex/generated_images/" in text else ()):
            _record(ps, {"key": f"file:{path}", "kind": "image", "action": "generated",
                         "title": prompt_title(prompt) or os.path.basename(path),
                         "path": path, "url": None, "size": None, "sha256": None,
                         "meta": {"prompt": one_line(prompt, 300)} if prompt else {}, **at})


_PROMPT_RE = re.compile(r"""prompt\s*[:=]\s*([`'"])(.*?)(?<!\\)\1""", re.S)


def _script_prompt(script: str) -> str | None:
    """The prompt a code-mode script gives the image generator (`prompt: "…"` or `const prompt = `…``)."""
    m = _PROMPT_RE.search(script or "")
    return m.group(2).replace("\\n", "\n") if m else None


def prompt_title(prompt: str | None) -> str | None:
    """A generated image's name from its prompt: the "Asset type:" it asked for, or the prompt's first sentence."""
    if not prompt:
        return None
    m = re.search(r"^\s*Asset type:\s*(.+)$", prompt, re.M)
    line = m.group(1) if m else next((ln for ln in prompt.splitlines() if ln.strip()), "")
    first = re.split(r"(?<=[.!?])\s", line.strip(), maxsplit=1)[0].rstrip(".")
    return one_line(safe_text(first), 90) or None


def _commit_at(cmd: str) -> int | None:
    """Where a `git commit` the command runs starts; one in a heredoc's body is text being written, not run."""
    bodies = [(m.start(2), m.end(2)) for m in _HEREDOC_RE.finditer(cmd)]
    return next((m.start() for m in _GIT_COMMIT_RE.finditer(cmd) if not any(a <= m.start() < b for a, b in bodies)), None)


def commit_message(cmd: str) -> str | None:
    """The subject line a `git commit` command sets: -m "...", -m "$(cat <<'EOF' ...)", or -F - <<'EOF'."""
    at = _commit_at(cmd)
    if at is None:
        return None
    cmd = cmd[at:]
    m = _MESSAGE_RE.search(cmd)
    body = (m.group(1) or m.group(2) or "") if m else ""
    if (not body and re.match(r"[^\n;&|]*\s(?:-F\s*-|--file[= ]-)(?:\s|$)", cmd)) or body.startswith("$(cat <<"):
        h = _HEREDOC_RE.search(cmd)
        body = h.group(2) if h else ""
    first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    return first if first and not first.startswith("$(") else None


def claude_ai_artifact(ps, inp: dict, *, ts=None, tool_use_id: str | None = None) -> None:
    """claude.ai's artifacts tool: one artifact per id, every create, rewrite or update a version of it."""
    aid = inp.get("id")
    if not isinstance(aid, str) or not aid:
        return
    kind = _CLAUDE_AI_TYPES.get(inp.get("type") or "", "doc")
    content = inp.get("content") if inp.get("command") in ("create", "rewrite") else None
    size, sha = digest(content)
    _record(ps, {"key": f"claude-ai:{ps.id}:{aid}", "kind": kind, "action": "created" if inp.get("command") == "create" else "updated",
                 "title": one_line(safe_text(inp.get("title") or ""), 200) or None, "path": None, "url": None,
                 "size": size, "sha256": sha, "ts": ts, "agent_id": "", "tool_use_id": tool_use_id,
                 "meta": {"artifact_id": aid, "type": inp.get("type"), "where": "claude.ai"}})


def claude_ai_presented(ps, content, *, ts=None, tool_use_id: str | None = None) -> None:
    """Files claude.ai's present_files handed to the user (they live in claude.ai's sandbox, not on this machine)."""
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "local_resource" or not isinstance(block.get("file_path"), str):
            continue
        path = block["file_path"]
        kind = kind_for_path(path) or _MIME_KIND.get(block.get("mime_type") or "")
        if not kind:
            continue
        _record(ps, {"key": f"file:{path}", "kind": kind, "action": "presented",
                     "title": one_line(safe_text(block.get("name") or os.path.basename(path)), 200), "path": path, "url": None,
                     "size": None, "sha256": None, "ts": ts, "agent_id": "", "tool_use_id": tool_use_id,
                     "meta": {"where": "claude.ai"}})


def _saved_files(ps) -> None:
    """Decks, documents, spreadsheets and PDFs a script saved: a path the session's tool calls named, whose file was
    created while the session ran (macOS keeps a file's creation time; elsewhere its last change stands in)."""
    from .util import parse_ts

    start, end = parse_ts(ps.started_at), parse_ts(ps.ended_at)
    if not start or not end or not getattr(ps, "_mentions", None):
        return
    lo, hi = start.timestamp() - 60, end.timestamp() + 600
    root = ps.project_path if ps.project_path and os.path.isabs(ps.project_path) else None
    found = []
    for raw, (ts, agent_id, tool_use_id) in ps._mentions.items():
        path = os.path.expanduser(raw.removeprefix("file://"))
        if not os.path.isabs(path):
            if not root:
                continue
            path = os.path.normpath(os.path.join(root, path))
        kind = kind_for_path(path)
        name, folders = os.path.basename(path), path.lower().split("/")[:-1]
        if not kind or os.path.splitext(path)[1].lower() not in _OFFICE_EXT or path.startswith(_NOT_MADE_HERE) \
                or name.startswith(("~$", ".~lock")) or _HASH_NAME_RE.match(os.path.splitext(name)[0].lower()) \
                or any("cache" in d or d == "attachments" for d in folders) \
                or any(o["key"] == f"file:{path}" for o in ps.outputs) or any(f[0] == path for f in found):
            continue  # an Office lock file, a cache entry, a downloaded attachment, or already counted
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not lo <= (getattr(st, "st_birthtime", None) or st.st_mtime) <= hi:
            continue  # it existed before the session (an input it read), or came later
        found.append((path, kind, st, ts, agent_id, tool_use_id))
    batches: dict[tuple, int] = {}
    for path, *_ in found:
        batch = (os.path.dirname(path), os.path.splitext(path)[1].lower())
        batches[batch] = batches.get(batch, 0) + 1
    for path, kind, st, ts, agent_id, tool_use_id in found:
        if batches[(os.path.dirname(path), os.path.splitext(path)[1].lower())] > _BATCH:
            continue
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            continue
        _record(ps, {"key": f"file:{path}", "kind": kind, "action": "saved", "title": os.path.basename(path), "path": path,
                     "url": None, "size": st.st_size, "sha256": h.hexdigest(), "ts": ts, "agent_id": agent_id,
                     "tool_use_id": tool_use_id, "meta": {}})


def finish(ps) -> None:
    """Fold in the links Claude Code records outside tool calls (pr-link, frame-link) and the files scripts saved, and
    share what tools revealed with the session's own PR and artifact lists, which the Markdown export and MCP read."""
    _saved_files(ps)
    for pr in ps.prs:
        if pr.get("url") and not any(o["key"] == f"url:{pr['url']}" for o in ps.outputs):
            repo, number = pr.get("repo"), pr.get("number")
            _link(ps, "pr", pr["url"], f"{repo}#{number}" if repo and number else None, action="opened", ts=pr.get("ts"),
                  agent_id="", tool_use_id=None, meta={k: v for k, v in (("repo", repo), ("number", number)) if v})
    for a in ps.artifacts:
        if a.get("url") and "claude.ai" in a["url"] and not any(o["key"] == f"url:{a['url']}" for o in ps.outputs):
            _link(ps, "published", a["url"], a.get("title"), action="published", ts=a.get("ts"), agent_id="", tool_use_id=None)
    known_prs = {p.get("url") for p in ps.prs}
    known_links = {a.get("url") for a in ps.artifacts}
    for o in ps.outputs:
        if o["kind"] == "pr" and o["url"] not in known_prs:
            ps.prs.append({"number": o["meta"].get("number"), "url": o["url"], "repo": o["meta"].get("repo"), "ts": o.get("ts")})
        elif o["kind"] == "published" and o["url"] not in known_links:
            ps.artifacts.append({"title": o.get("title"), "url": o["url"], "ts": o.get("ts")})


def meta_json(o: dict) -> str | None:
    meta = {k: v for k, v in (o.get("meta") or {}).items() if v not in (None, "", [], {})}
    return json.dumps(meta, ensure_ascii=False) if meta else None
