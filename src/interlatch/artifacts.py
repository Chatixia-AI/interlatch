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
_EXT_KIND = {
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
_OFFICE_PATH_RE = re.compile(r"""([^\s'"`<>|;&(){}\[\]]+\.(?:pptx|odp|docx|odt|xlsx|ods|pdf))(?![\w.])""", re.I)
_BATCH = 8  # more files of one type saved into one folder than this is data being processed (downloads, a cache), not deliverables
_HASH_NAME_RE = re.compile(r"^[0-9a-f]{32,}$")
_NOT_MADE_HERE = tuple(os.path.expanduser(p) for p in ("~/Library/", "/Library/", "/System/", "/Applications/", "/private/var/folders/",
                                                       "/var/folders/"))
_GENERATED_IMAGE_RE = re.compile(r"(/[^\s\"'`]*/\.codex/generated_images/[^\s\"'`]+\.(?:png|jpe?g|webp))")
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
    kind = _EXT_KIND.get(os.path.splitext(low)[1])
    if kind in ("page", "diagram", "image", "sheet") and _SOURCE_DIRS.intersection(dirs):
        return None
    return kind


def _digest(content) -> tuple[int | None, str | None]:
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
    size, sha = _digest(content)
    _record(ps, {"key": f"file:{path}", "kind": kind, "action": "created" if created else "rewritten",
                 "title": os.path.basename(path), "path": path, "url": None, "size": size, "sha256": sha, "ts": ts,
                 "agent_id": agent_id, "tool_use_id": tool_use_id, "meta": {"where": where} if where else {}})


def _link(ps, kind: str, url: str, title: str | None, *, action: str, ts, agent_id: str, tool_use_id, meta=None) -> None:
    if not isinstance(url, str) or not re.match(r"https?://", url, re.I):
        return  # a transcript states the link: one the dashboard can't open as a page (javascript:, say) isn't kept
    _record(ps, {"key": f"url:{url}", "kind": kind, "action": action, "title": one_line(safe_text(title or ""), 200) or None,
                 "path": None, "url": url, "size": None, "sha256": None, "ts": ts, "agent_id": agent_id,
                 "tool_use_id": tool_use_id, "meta": dict(meta or {})})


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
        for raw in _OFFICE_PATH_RE.findall(f"{cmd}\n{text}"):
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
        for path in dict.fromkeys(_GENERATED_IMAGE_RE.findall(text)):
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
    size, sha = _digest(content)
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


# ---------------------------------------------------------------------------------------------------- reading them back
STATUS = {"present": "on disk", "changed": "changed since", "gone": "gone", "link": "link", "commit": "commit",
          "chat": "in the chat", "elsewhere": "another machine"}
_HASHES: dict[tuple, str] = {}
PREVIEW_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
                 ".svg": "image/svg+xml"}
PREVIEW_MAX = 25_000_000
THUMB_EXT = {".pptx", ".odp", ".docx", ".odt", ".xlsx", ".ods", ".pdf"}  # Quick Look draws their first page


def can_thumbnail() -> bool:
    import shutil
    import sys

    return sys.platform == "darwin" and bool(shutil.which("qlmanage"))  # (path, mtime, size) -> sha256, so a gallery page does not re-read unchanged files


def status_of(row: dict, local_machine: str | None = None) -> str:
    """Where an artifact stands now: still on disk as written, changed since, gone; or a link, a commit, a chat's file."""
    if row["kind"] in ("pr", "published"):
        return "link"
    if row["kind"] == "commit":
        return "commit"
    if (row.get("meta") or {}).get("where") or row.get("agent") in ("claude-ai", "chatgpt"):
        return "chat"
    if local_machine and row.get("machine_id") and row["machine_id"] != local_machine:
        return "elsewhere"
    path = row.get("path")
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return "gone"
    if not row.get("sha256") or st.st_size > 20_000_000:
        return "present"
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _HASHES:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            return "present"
        _HASHES[key] = h.hexdigest()
    return "present" if _HASHES[key] == row["sha256"] else "changed"


def query(conn, *, kind: str | None = None, project: str | None = None, agent: str | None = None, q: str | None = None,
          session_id: str | None = None, local_machine: str | None = None) -> tuple[list[dict], dict[str, int]]:
    """Artifacts newest first, with where each stands now, and the count of each kind (before the kind filter).
    Across sessions the same file, link or commit is one item (its latest version) that knows how many sessions made it."""
    where, params = [], []
    if session_id:
        where.append("a.session_id = ?")
        params.append(session_id)
    if project:
        where.append("s.project_path = ?")
        params.append(project)
    if agent:
        where.append("s.agent = ?")
        params.append(agent)
    if q:
        where.append("(LOWER(COALESCE(a.title, '')) LIKE ? OR LOWER(COALESCE(a.path, '')) LIKE ? OR LOWER(COALESCE(a.url, '')) LIKE ?)")
        params += [f"%{q.lower()}%"] * 3
    rows = conn.execute(
        "SELECT a.*, s.project_path, s.project_name, s.agent, s.title AS session_title, s.machine_id, s.archive_path FROM artifacts a "
        f"JOIN sessions s ON s.id = a.session_id {'WHERE ' + ' AND '.join(where) if where else ''} "
        "ORDER BY COALESCE(a.ts, s.started_at) DESC, a.id DESC", params).fetchall()
    items: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
        key = d["key"] if not session_id else f"{d['key']}#{d['id']}"
        first = items.get(key)
        if first is None:
            d["session_ids"] = [d["session_id"]]
            items[key] = d
        else:  # an older version from another session
            if d["session_id"] not in first["session_ids"]:
                first["session_ids"].append(d["session_id"])
            first["versions"] += d["versions"] or 1
    counts: dict[str, int] = {}
    for d in items.values():
        counts[d["kind"]] = counts.get(d["kind"], 0) + 1
    out = [d for d in items.values() if not kind or d["kind"] == kind]
    for d in out:
        d["sessions"] = len(d.pop("session_ids"))
        d["status"] = status_of(d, local_machine)
        d["preview"] = previewable(d)
        d["openable"] = openable(d)
        d.pop("machine_id", None)
        d.pop("archive_path", None)
    return out, counts


def previewable(a: dict) -> bool:
    """An image or SVG still on disk, or a deck, document, spreadsheet or PDF Quick Look can draw (on a Mac)."""
    ext = os.path.splitext(a.get("path") or "")[1].lower()
    return a["status"] in ("present", "changed") and (ext in PREVIEW_TYPES or (ext in THUMB_EXT and can_thumbnail()))


def quicklook_thumbnail(path: str, cache_dir, size: int = 800) -> bytes | None:
    """A PNG of a file's first page or slide, drawn by macOS Quick Look and kept until the file changes."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    try:
        st = os.stat(path)
    except OSError:
        return None
    out = Path(cache_dir) / (hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{size}".encode()).hexdigest() + ".png")
    if not out.exists():
        with tempfile.TemporaryDirectory() as tmp:
            try:
                subprocess.run(["qlmanage", "-t", "-s", str(size), "-o", tmp, path], capture_output=True, timeout=30,
                               stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                return None
            made = Path(tmp) / f"{os.path.basename(path)}.png"
            if not made.exists():
                return None
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(made, out)
    try:
        return out.read_bytes()
    except OSError:
        return None


def artifact_by_id(conn, artifact_id: int, local_machine: str | None = None) -> dict | None:
    """One recorded artifact with where it stands now; the only way a request reaches a file."""
    row = conn.execute("SELECT a.*, s.agent, s.machine_id, s.archive_path FROM artifacts a JOIN sessions s ON s.id = a.session_id "
                       "WHERE a.id = ?", (artifact_id,)).fetchone()
    if row is None:
        return None
    a = dict(row)
    a["meta"] = json.loads(a.pop("meta_json") or "{}")
    a["status"] = status_of(a, local_machine)
    return a


def preview_file(conn, artifact_id: int, local_machine: str | None = None, cache_dir=None) -> tuple[bytes, str] | None:
    """An artifact's picture by its id (only a file Interlatch recorded as an artifact, never a path a request names): an
    image or SVG as it is, or a Quick Look thumbnail of a deck, document, spreadsheet or PDF."""
    a = artifact_by_id(conn, artifact_id, local_machine)
    if a is None or a["status"] not in ("present", "changed"):
        return None
    ext = os.path.splitext(a.get("path") or "")[1].lower()
    if ext in THUMB_EXT and cache_dir and can_thumbnail():
        png = quicklook_thumbnail(a["path"], cache_dir)
        return (png, "image/png") if png else None
    ctype = PREVIEW_TYPES.get(ext)
    if not ctype:
        return None
    try:
        if os.path.getsize(a["path"]) > PREVIEW_MAX:
            return None
        with open(a["path"], "rb") as f:
            return f.read(), ctype
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------------- opening them
OPEN_MAX = 60_000_000
_TEXT_EXT = {".md", ".markdown", ".mdx", ".rst", ".csv", ".mmd", ".mermaid", ".puml", ".excalidraw", ".drawio", ".txt"}
# an agent's HTML page keeps its own scripts (charts, interactivity) but runs in a sandbox: an opaque origin, with no
# way into Interlatch's data, cookies or API
HTML_POLICY = "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms allow-modals allow-downloads"
IMAGE_POLICY = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"
TEXT_POLICY = "default-src 'none'; sandbox"


def openable(a: dict) -> bool:
    """Whether "Open" can show the file: it is on disk, or it is gone but the archived transcript holds what was written."""
    if not a.get("path") or a["kind"] in ("pr", "published", "commit"):
        return False
    if a["status"] in ("present", "changed"):
        return True
    return (a["status"] == "gone" and bool(a.get("sha256")) and a.get("agent") in ("claude", "codex")
            and bool(a.get("archive_path")) and os.path.exists(a["archive_path"]))


def written_content(a: dict) -> str | None:
    """The text a session wrote to a file, read back from the archived transcript (for a file no longer on disk)."""
    from pathlib import Path

    from .util import iter_jsonl

    archive, path, tid = a.get("archive_path"), a["path"], a.get("tool_use_id")
    if not archive or not os.path.exists(archive):
        return None
    found: list[str] = []
    if a.get("agent") == "claude":
        from .parser import session_dir_for, subagent_files

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
    exact = [c for c in found if a.get("sha256") and _digest(c)[1] == a["sha256"]]
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

    from .i18n import tr

    a = artifact_by_id(conn, artifact_id, local_machine)
    if a is None or a["status"] not in ("present", "changed"):
        return tr("that file is not on this computer")
    if os.path.splitext(a["path"])[1].lower() not in _EXT_KIND:
        return tr("that kind of file is not opened from here")
    if sys.platform == "darwin":
        cmd = ["open", "-R", a["path"]] if how == "reveal" else ["open", a["path"]]
    elif sys.platform.startswith("linux"):
        cmd = ["xdg-open", os.path.dirname(a["path"]) if how == "reveal" else a["path"]]
    elif sys.platform == "win32":
        from .windows import open_path

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
