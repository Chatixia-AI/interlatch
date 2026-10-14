"""Instruction files (CLAUDE.md, AGENTS.md): Interlatch's managed block, where lines go, and which knowledge earns one.

Interlatch owns one block per file and nothing else in it:

    <!-- BEGIN interlatch -->
    - Re-read the exact lines right before every edit. <!-- interlatch:friction:edit-stale-context -->
    <!-- END interlatch -->

The block goes at the end of the file, after any other tool's managed block (agent-ninja-START/END and the like),
never inside one. Text outside the block is never touched; a write backs the old file up first and replaces it
atomically. Lines are only written when the user approves a suggestion (suggest.apply). A file whose block markers
are damaged (two BEGINs, an END without a BEGIN, ...) is refused rather than guessed at. A block Chronicle wrote
before the rename (`<!-- BEGIN chronicle -->`, `<!-- chronicle:... -->` markers) is read the same, and the next write
gives it the new markers (or `interlatch migrate` does, relabel()).
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from collections.abc import Callable

from .config import Config
from .i18n import tr
from .util import dumps, loads, one_line, parse_ts, utcnow

BEGIN = "<!-- BEGIN interlatch -->"
END = "<!-- END interlatch -->"
LEGACY_BEGIN = "<!-- BEGIN chronicle -->"
LEGACY_END = "<!-- END chronicle -->"
MAX_PER_FILE = 8
LINE_CHARS = 240
THEME_PROJECTS = 3  # a lesson relearned in this many projects belongs in the user-level file
JACCARD_DUPLICATE = 0.5
JACCARD_OVERLAP = 0.6
VISIBILITY_TTL_DAYS = 7

_LINE = re.compile(r"^- (.*?) <!-- (?:interlatch|chronicle):(\S+) -->\s*$")
_OTHER_BLOCKS = (re.compile(r"<!--\s*BEGIN[ :]+([\w.-]+)\s*-->"), re.compile(r"<!--\s*([\w.-]+)-START\s*-->"))


# ---------------------------------------------------------------- the managed block
class MalformedBlock(ValueError):
    """The file's block markers are not exactly one BEGIN followed by one END."""


def _enc(key: str) -> str:  # an HTML comment cannot contain "--", and the marker regex stops at whitespace
    key = re.sub(r"\s", lambda m: f"%{ord(m.group(0)):02X}", key.replace("%", "%25"))
    return key.replace("--", "-%2D")


def _dec(key: str) -> str:  # every % in an encoded key starts an escape
    return re.sub(r"%([0-9A-F]{2})", lambda m: chr(int(m.group(1), 16)), key)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("<!--", "").replace("-->", "")).strip()


def _block_span(lines: list[str]) -> tuple[int, int] | None:
    """Line indexes of our BEGIN and END markers, or None when there are none.

    Raises MalformedBlock unless there is exactly one BEGIN and one END after it: guessing where a damaged block
    ends would pull the user's own text into it.
    """
    begins = [i for i, l in enumerate(lines) if l.strip() in (BEGIN, LEGACY_BEGIN)]
    ends = [i for i, l in enumerate(lines) if l.strip() in (END, LEGACY_END)]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise MalformedBlock(tr("malformed interlatch block ({begins} BEGIN and {ends} END markers, expected one BEGIN "
                                "followed by one END)", begins=len(begins), ends=len(ends)))
    return begins[0], ends[0]


def _other_spans(lines: list[str]) -> list[tuple[int, int]]:
    """Other tools' managed blocks (BEGIN x / END x, x-START / x-END)."""
    spans = []
    for i, line in enumerate(lines):
        for rx in _OTHER_BLOCKS:
            m = rx.search(line)
            if not m or m.group(1).lower() in ("interlatch", "chronicle"):
                continue
            name = re.escape(m.group(1))
            end_rx = re.compile(rf"<!--\s*(END[ :]+{name}|{name}-END)\s*-->")
            end = next((j for j in range(i, len(lines)) if end_rx.search(lines[j])), None)
            if end is not None:
                spans.append((i, end))
    return spans


def parse_block(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(keyed lines, other lines) inside our block of this file text."""
    lines = (text or "").splitlines()
    span = _block_span(lines)
    if not span:
        return [], []
    keyed, other = [], []
    for line in lines[span[0] + 1:span[1]]:
        m = _LINE.match(line)
        if m:
            keyed.append((_dec(m.group(2)), m.group(1)))
        elif line.strip():
            other.append(line)
    return keyed, other


def render_file(old_text: str | None, entries: list[tuple[str, str]]) -> str:
    """The file with its Interlatch block holding exactly `entries` (key, text); pure.

    An existing block is rewritten in place unless it sits inside another tool's block, in which case it moves to
    the end. Lines a person typed into the block without a marker are kept. No entries and no such lines removes
    the block, along with the blank line written before it.
    """
    old_text = old_text or ""
    lines = old_text.splitlines()
    span = _block_span(lines)
    _keyed, other = parse_block(old_text)
    body = [f"- {_clean(text)} <!-- interlatch:{_enc(key)} -->" for key, text in entries] + other
    block = [BEGIN, *body, END] if body else []
    if span and not any(a < span[0] and span[1] < b for a, b in _other_spans(lines)):
        before, after = lines[:span[0]], lines[span[1] + 1:]
        if not block:
            if before and not before[-1].strip() and (not after or not after[0].strip()):
                before.pop()  # the separator written with the block
            out = before + after
            while out and not out[-1].strip():
                out.pop()
            return ("\n".join(out) + "\n") if out else ""
        return "\n".join(before + block + after) + "\n"
    if span:  # stranded inside another block: take it out, then append
        lines = lines[:span[0]] + lines[span[1] + 1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if not block:
        return ("\n".join(lines) + "\n") if lines else ""
    return "\n".join(lines + ([""] if lines else []) + block) + "\n"


def relabel(text: str) -> str:
    """The file text with a block Chronicle wrote given Interlatch's markers, and nothing else changed; pure. Raises
    MalformedBlock for a damaged block."""
    lines = (text or "").splitlines(keepends=True)
    span = _block_span([line.rstrip("\r\n") for line in lines])
    if not span:
        return text
    a, b = span
    for i in range(a, b + 1):
        lines[i] = (lines[i].replace(LEGACY_BEGIN, BEGIN).replace(LEGACY_END, END)
                    .replace("<!-- chronicle:", "<!-- interlatch:"))
    return "".join(lines)


def read_block(path: Path) -> list[tuple[str, str]]:
    """The keyed lines of the file's block ([] when the file is missing); raises MalformedBlock."""
    try:
        return parse_block(Path(path).read_text())[0]
    except OSError:
        return []


def merge(old_text: str | None, add: list[tuple[str, str]], remove_keys: set[str] | None = None) -> str:
    """New file text with `add` lines added or replaced (by key) and `remove_keys` removed; pure."""
    remove_keys = set(remove_keys or ())
    current = [(k, t) for k, t in parse_block(old_text or "")[0] if k not in remove_keys]
    new = dict(add)
    entries = [(k, new.pop(k, t)) for k, t in current]
    entries += [(k, t) for k, t in add if k in new and k not in remove_keys]
    return render_file(old_text, entries)


def write_lines(path: Path, add: list[tuple[str, str]], remove_keys: set[str] | None = None, *,
                cfg: Config | None = None) -> str:
    """Apply `merge` to the file on disk: back up the old file, then replace it atomically. Returns the new text."""
    path = Path(path)
    old = path.read_text() if path.exists() else ""
    new = merge_file(path, old, add, remove_keys)
    if new == old:
        return new
    write_atomic(path, new, cfg=cfg)
    return new


def merge_file(path: Path, old_text: str | None, add: list[tuple[str, str]], remove_keys: set[str] | None = None) -> str:
    """`merge`, with the file named in the error when its block is damaged."""
    try:
        return merge(old_text, add, remove_keys)
    except MalformedBlock as exc:
        raise MalformedBlock(tr("{error} in {path}; fix it by hand", error=exc, path=path)) from None


def write_atomic(path: Path, text: str, *, cfg: Config | None = None, mkdir: bool = True) -> Path | None:
    """Back the file up (install._backup), then write it through a temp file and a rename. Returns the backup.

    A symlinked file (dotfiles) is written where it points, so the link stays a link. With mkdir=False a missing
    directory is an error rather than created (a project that was moved or deleted is never re-created).
    """
    from .install import _backup

    if cfg is None:
        from .config import load_config

        cfg = load_config(create=False)
    path = Path(os.path.realpath(path))
    if not path.parent.is_dir():
        if not mkdir:
            raise FileNotFoundError(tr("{path} no longer exists", path=path.parent))
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if b"\r\n" in path.read_bytes():  # the merge works on \n lines; give a CRLF file its endings back
            text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    except OSError:
        pass
    backup = _backup(cfg, path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            fh.write(text)
        if path.exists():
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return backup


# ---------------------------------------------------------------- where a line goes
# Interlatch's own source settings come first, so the CLI (with your shell's environment) and the background agents
# (with only PATH, HOME and INTERLATCH_HOME) route a user-level line to the same file.
def claude_home(cfg: Config | None = None) -> Path:
    if cfg is not None and cfg.claude_dirs:
        return Path(cfg.claude_dirs[0]).expanduser()
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()


def codex_home(cfg: Config | None = None) -> Path:
    if cfg is not None and cfg.codex_dirs:
        return Path(cfg.codex_dirs[0]).expanduser()
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()


def _mentions(path: Path, needle: str) -> bool:
    try:
        return needle in path.read_text()
    except OSError:
        return False


def target_for(agent: str | None, scope: str, project_path: str | None, cfg: Config | None = None) -> Path | None:
    """The instruction file an agent reads for this scope, or None when there is none to write.

    A project's AGENTS.md that points at CLAUDE.md (or a CLAUDE.md that imports @AGENTS.md) makes the two one file,
    so the line is written once, to the file the other defers to.
    """
    if scope == "user":
        if agent == "claude":
            return claude_home(cfg) / "CLAUDE.md"
        if agent == "codex":
            return codex_home(cfg) / "AGENTS.md"
        return None
    if not project_path:
        return None
    root = Path(project_path)
    home = Path.home()
    if not root.is_dir() or root == home or root in home.parents:
        return None
    claude_md, agents_md = root / "CLAUDE.md", root / "AGENTS.md"
    if agent == "claude":
        if not claude_md.exists() and agents_md.exists() and _mentions(agents_md, "CLAUDE.md"):
            return claude_md
        if claude_md.exists() and _mentions(claude_md, "@AGENTS.md"):
            return agents_md
        return claude_md
    if agent in ("codex", "copilot", "bob", "antigravity"):
        if agents_md.exists() and _mentions(agents_md, "CLAUDE.md"):
            return claude_md
        return agents_md
    return None


# ---------------------------------------------------------------- overlap and sensitivity
_STOP = {"this", "that", "with", "from", "when", "then", "than", "into", "your", "have", "will", "they", "them", "their",
         "only", "also", "each", "every", "before", "after", "never", "always", "should", "must", "does", "dont", "instead"}


_CJK_WORD = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u30a0-\u30ff々]{2,}")  # Japanese content words: kanji and katakana


def tokens(text: str | None) -> set[str]:
    """Identifiers, paths and words of 4+ characters, lowercased, and the two-character pieces of Japanese words, so
    that Japanese lines compare too (the overlap vocabulary)."""
    out = set()
    for t in re.findall(r"[A-Za-z0-9_./~:@-]+", (text or "").lower()):
        t = t.strip(".:-/")
        if len(t) >= 4 and t not in _STOP:
            out.add(t)
    for word in _CJK_WORD.findall(text or ""):
        out.update(word[i:i + 2] for i in range(len(word) - 1))
    return out


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def overlap(text: str, existing_file_text: str | None) -> bool:
    """True when a line outside Interlatch's block already says this (token Jaccard >= 0.6)."""
    lines = (existing_file_text or "").splitlines()
    try:
        span = _block_span(lines)
    except MalformedBlock:
        span = None  # compare against every line; applying will refuse this file anyway
    if span:
        lines = lines[:span[0]] + lines[span[1] + 1:]
    want = tokens(re.sub(r"\*\*", "", text))
    return any(jaccard(want, tokens(line)) >= JACCARD_OVERLAP for line in lines if line.strip())


PUBLIC_HOSTS = ("github.com", "githubusercontent.com", "gitlab.com", "pypi.org", "python.org", "npmjs.com", "npmjs.org",
                "nodejs.org", "anthropic.com", "claude.ai", "claude.com", "openai.com", "chatgpt.com", "google.com",
                "microsoft.com", "apple.com", "mozilla.org", "wikipedia.org", "stackoverflow.com", "example.com",
                "example.org", "astral.sh", "brew.sh", "playwright.dev", "readthedocs.io", "docker.com", "localhost")
_HOST_TLDS = "com|net|org|io|dev|cloud|internal|local|corp|lan|jp|co|ai|sh|biz|info|xyz|tech|site|online|us|uk|de|eu"
_SENSITIVE = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("guid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("private-ip", re.compile(r"\b(10\.\d{1,3}|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("home-path", re.compile(r"/(Users|home)/[^/\s`'\"]+/")),
]
_HOST = re.compile(rf"\b((?:[a-z0-9](?:[a-z0-9-]{{0,61}}[a-z0-9])?\.)+(?:{_HOST_TLDS}))\b", re.I)


def sensitive(text: str | None) -> list[str]:
    """What in this line should not land in a shared file unread: 'kind: match' strings."""
    text = text or ""
    hits: list[str] = []
    for kind, rx in _SENSITIVE:
        for m in rx.finditer(text):
            hits.append(f"{kind}: {m.group(0)}")
    for m in _HOST.finditer(text):
        host = m.group(1).lower()
        if "@" in text[max(0, m.start() - 1):m.start()]:
            continue  # the domain of an email, already reported
        if any(host == h or host.endswith("." + h) for h in PUBLIC_HOSTS):
            continue
        if host.count(".") == 1 and host.endswith(".sh") and "://" not in text[max(0, m.start() - 3):m.start()]:
            continue  # a shell script (build.sh), not a host
        hits.append(f"host: {host}")
    from .redact import _PATTERNS

    for rx, _repl in _PATTERNS:
        m = rx.search(text)
        if m:
            hits.append(f"token: {m.group(0)[:12]}…")
    seen: set = set()
    return [h for h in hits if not (h in seen or seen.add(h))]


def _git(cwd: Path, *args: str, timeout: float = 5) -> subprocess.CompletedProcess | None:
    if not shutil.which("git"):
        return None
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


_VISIBILITY: dict[str, str] = {}  # per process run


def repo_visibility(project_path: str | None, conn: sqlite3.Connection | None = None) -> str:
    """'public', 'private' or 'unknown' for the project's origin remote; gh answers for github.com remotes.

    Cached for this run, and in the kv table for a week when a connection is given. Never raises.
    """
    if not project_path or not Path(project_path).is_dir():
        return "unknown"
    if project_path in _VISIBILITY:
        return _VISIBILITY[project_path]
    key = f"repo_visibility:{project_path}"
    if conn is not None:
        from .db import kv_get

        cached = loads(kv_get(conn, key), None)
        at = parse_ts((cached or {}).get("at"))
        if cached and at and at > utcnow() - timedelta(days=VISIBILITY_TTL_DAYS):
            _VISIBILITY[project_path] = cached["v"]
            return cached["v"]
    value = _visibility_now(Path(project_path))
    _VISIBILITY[project_path] = value
    if conn is not None and value != "unknown":
        from .db import kv_set

        try:
            kv_set(conn, key, dumps({"v": value, "at": utcnow().isoformat()}))
        except sqlite3.Error:  # a read-only connection: the per-run cache still holds it
            pass
    return value


def _gh_bin() -> str | None:
    return shutil.which("gh")


def _visibility_now(root: Path) -> str:
    proc = _git(root, "remote", "get-url", "origin")
    if proc is None:
        return "unknown"
    if proc.returncode != 0:
        inside = _git(root, "rev-parse", "--is-inside-work-tree")
        return "private" if inside is not None and inside.returncode == 0 else "unknown"  # a repo with no remote
    url = proc.stdout.strip()
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url)
    gh = _gh_bin()
    if not m or not gh:
        return "unknown"
    try:
        out = subprocess.run([gh, "repo", "view", f"{m.group(1)}/{m.group(2)}", "--json", "visibility", "-q", ".visibility"],
                             capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    vis = out.stdout.strip().lower() if out.returncode == 0 else ""
    return "public" if vis == "public" else ("private" if vis in ("private", "internal") else "unknown")


def is_untracked(path: Path) -> bool:
    """The file exists in a git work tree but git does not track it (a line there never shows up in a diff)."""
    path = Path(path)
    if not path.is_file():
        return False
    inside = _git(path.parent, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.returncode != 0 or inside.stdout.strip() != "true":
        return False
    tracked = _git(path.parent, "ls-files", "--error-unmatch", path.name)
    return tracked is not None and tracked.returncode != 0


def warnings_for(text: str, target: Path | None, project_path: str | None, conn: sqlite3.Connection | None = None) -> dict:
    project_scope = bool(project_path)
    return {
        "sensitive": sensitive(text),
        "public_repo": project_scope and repo_visibility(project_path, conn) == "public",
        "untracked": bool(project_scope and target is not None and is_untracked(target)),
        "overlap": False,
    }


# ---------------------------------------------------------------- knowledge candidates
KINDS = ("gotcha", "fix", "command", "preference", "decision")
_CHANGELOG = re.compile(
    r"^(fixed|fix:|fixes|added|adds|shipped|implemented|introduced|replaced|redesigned|refactored|removed|renamed|migrated|"
    r"switched|moved|created|built|closed|resolved|upgraded|updated|extended|rewrote|restructured|enabled|disabled|"
    r"released|bumped|merged|deployed|landed|completed|finished|adr-\d+)\b|\bnow (\w+s|is|has|uses|does|returns)\b|"
    r"\b(was|were) (fixed|added|replaced|removed|closed)\b|\bclosed via\b"
    r"|(修正|追加|実装|削除|変更|移行|導入|更新|置き換え|対応)(した|しました)?。?$|(した|しました)。?$", re.I)  # and in Japanese
_NARRATIVE = re.compile(r"\b(returned|crashed|broke|caused|accepted|was shadowed|were showing|regressed|failed)\b"
                        r"|失敗した|クラッシュした|壊れた|原因だった|表示されていた", re.I)
_RULE = re.compile(r"\b(must|never|always|don'?t|do not|avoid|only|requires?|prefer|instead of|should|needs? to|not)\b"
                   r"|必ず|常に|決して|禁止|べき|必要|ではなく|代わりに|しない|使わない|ないこと|避け", re.I)
_STRONG_RULE = re.compile(r"\b(must|never|always|don'?t|do not|avoid|use .{1,40} (not|instead of))\b"
                          r"|必ず|常に|決して|禁止|しないこと|使わない|ではなく.{1,40}(を使う|にする)", re.I)
_ABBREV = re.compile(r"\b(e\.g|i\.e|vs|etc|cf|approx|incl)\.$", re.I)


def is_rule_like(title: str, body: str | None = "") -> bool:
    """Imperative or invariant wording ('never', 'use X not Y', 'requires') rather than a changelog entry."""
    return bool(_RULE.search(title or "") or _RULE.search(first_sentence(body)))


def is_changelog(title: str) -> bool:
    return bool(_CHANGELOG.search((title or "").strip()))


def first_sentence(text: str | None) -> str:
    flat = re.sub(r"\s+", " ", (text or "").replace("```", " ")).strip()
    for m in re.finditer(r"[.!?](?=\s|$)|。", flat):
        head = flat[:m.end()]
        if not _ABBREV.search(head) and not re.search(r"\b\w\.$", head):  # "e.g." or a lone initial is not an end
            return head.strip()
    return flat


def line_text(title: str, body: str | None) -> str:
    sentence = first_sentence(body)
    text = f"**{_clean(title).rstrip('.')}**" + (f": {sentence}" if sentence else "")
    return one_line(_clean(text), LINE_CHARS)


def _eligible(r: dict) -> bool:
    if r["kind"] not in KINDS or r["source"] == "memory":
        return False
    if r["confidence"] != "high" and r["stage"] not in ("established", "canonical"):
        return False
    title = r["title"] or ""
    if is_changelog(title):
        return False
    if r["kind"] in ("fix", "decision"):
        if not is_rule_like(title, r["body"]) or (_NARRATIVE.search(title) and not _STRONG_RULE.search(title)):
            return False
    return True


def _worth_a_line(item: dict, sessions: int) -> bool:
    """One session's provisional lesson earns a line only when it is a command, a preference, or worded as a rule."""
    if sessions >= 2 or item["stage"] in ("established", "canonical"):
        return True
    return item["kind"] in ("command", "preference") or bool(_STRONG_RULE.search(item["title"] or ""))


def _recency(ts: str | None) -> float:
    dt = parse_ts(ts)
    if not dt:
        return 0.0
    age = (utcnow() - dt).days
    return 1.0 if age <= 14 else max(0.0, 1.0 - (age - 14) / 106)


def _clusters(items: list[dict]) -> list[list[int]]:
    """Near-duplicate groups by title tokens (Jaccard >= 0.5); indexes into items."""
    toks = [tokens(i["title"]) for i in items]
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    index: dict[str, list[int]] = defaultdict(list)
    for i, ts in enumerate(toks):
        for t in ts:
            index[t].append(i)
    for i, ts in enumerate(toks):
        seen: set[int] = set()
        for t in ts:
            for j in index[t]:
                if j <= i or j in seen:
                    continue
                seen.add(j)
                if jaccard(ts, toks[j]) >= JACCARD_DUPLICATE:
                    parent[find(i)] = find(j)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(items)):
        groups[find(i)].append(i)
    return list(groups.values())


def _project_agents(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Agents that work in each project often enough to deserve its instruction file (>= 3 sessions or 20%)."""
    counts: dict[str, dict[str, int]] = defaultdict(dict)
    for r in conn.execute("SELECT project_path, agent, COUNT(*) AS n FROM sessions WHERE source = 'transcript' "
                          "AND project_path IS NOT NULL GROUP BY project_path, agent"):
        counts[r["project_path"]][r["agent"]] = r["n"]
    out = {}
    for path, by in counts.items():
        total = sum(by.values())
        out[path] = [a for a, n in sorted(by.items(), key=lambda x: -x[1]) if n >= 3 or n / total >= 0.2]
    return out


def scope_choices(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """Where you moved a lesson or a cause, over the automatic choice: {subject: (when, "user" | "project")}."""
    return {r["subject"]: (r["updated_at"] or "", r["scope"]) for r in conn.execute("SELECT * FROM suggestion_scopes")}


def chosen_scope(choices: dict[str, tuple[str, str]], subjects) -> str | None:
    """Your latest choice for any of these subjects (a lesson's items, a cause), or None."""
    got = [choices[x] for x in subjects if x in choices]
    return max(got)[1] if got else None


def about_you(members: list[dict]) -> bool:
    """A preference the analysis judged useful beyond its project: how you work, not how the project works."""
    return any(m["kind"] == "preference" and m["scope"] == "global" for m in members)


def candidates(conn: sqlite3.Connection, cfg: Config | None = None, decided: Callable[[dict], bool] | None = None,
               waiting: list[dict] | None = None) -> list[dict]:
    """Suggestion dicts for knowledge worth an instruction line, best first, at most 8 per target file. A candidate
    `decided` says was dismissed or applied already is still returned, so its record keeps following the lesson, but
    it takes none of the 8 places, and neither does a lesson you moved. Those left over go to `waiting`."""
    from .friction import match_note
    from .ladder import STAGE_RANK, confirmations

    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM knowledge WHERE status = 'active' AND source IN ('analysis', 'manual') "
        f"AND kind IN ({','.join('?' * len(KINDS))})", KINDS)]
    items = [r for r in rows if _eligible(r)]
    # a lesson the friction catalog already covers is proposed from there, with its evidence
    items = [r for r in items if not [c for c in match_note(f"{r['title']}. {first_sentence(r['body'])}")
                                      if not c.startswith("provider")]]
    titles = {r["session_id"]: r["title"] for r in conn.execute(
        "SELECT id AS session_id, COALESCE(llm_title, title, ai_title) AS title FROM sessions")}
    agents_by_project = _project_agents(conn)
    choices = scope_choices(conn)
    groups = _clusters(items)

    out: list[dict] = []
    for group in groups:
        members = [items[i] for i in group]
        choice = chosen_scope(choices, [f"knowledge:{m['id']}" for m in members])
        sessions: set[str] = set()
        for m in members:
            sessions.update(confirmations(m))
        members = [m for m in members if _worth_a_line(m, len(sessions))]
        if not members:
            continue
        projects = {m["project_path"] for m in members if m["project_path"]}
        for m in members:
            m["_score"] = (STAGE_RANK.get(m["stage"] or "provisional", 1) + len(sessions) + _recency(m["updated_at"] or m["created_at"])
                           + (0.5 if is_rule_like(m["title"], m["body"]) else 0.0))
        best = max(members, key=lambda m: (m["_score"], m["id"]))
        evidence = {"knowledge_ids": [m["id"] for m in members], "sessions": len(sessions), "projects": len(projects),
                    "project_names": sorted({m["project_name"] for m in members if m["project_name"]})[:5],
                    "stage": best["stage"], "kind": best["kind"],
                    "examples": [{"session_id": s, "title": titles.get(s) or ""} for s in list(sessions)[:3]]}
        text = line_text(best["title"], best["body"])
        # the key names the oldest member, not the best: a lesson relearned later (a newer, higher-scoring duplicate)
        # must keep the identity a dismissal or an approval was recorded under
        # one line in the user-level file for a lesson relearned across projects or a preference about you; you can move it
        user_level = choice == "user" if choice else (len(projects) >= THEME_PROJECTS or about_you(members))
        if user_level:
            agents = sorted({m["agent"] or "claude" for m in members} & {"claude", "codex"}) or ["claude"]
            anchor = min(m["id"] for m in members)
            for agent in agents:
                target = target_for(agent, "user", None, cfg)
                if target:
                    out.append({**_candidate(best, text, target, None, agent, evidence, anchor), "chosen": bool(choice)})
            continue
        for project in projects:
            mine = [m for m in members if m["project_path"] == project]
            pick = max(mine, key=lambda m: (m["_score"], m["id"]))
            line = line_text(pick["title"], pick["body"])
            by_target: dict[Path, list[str]] = defaultdict(list)
            for agent in agents_by_project.get(project) or ["claude"]:
                target = target_for(agent, "project", project, cfg)
                if target:
                    by_target[target].append(agent)
            anchor = min(m["id"] for m in mine)
            for target, agents in by_target.items():
                agent = agents[0] if len(agents) == 1 else "all"
                out.append({**_candidate(pick, line, target, project, agent, evidence, anchor), "chosen": bool(choice)})

    # overlap with what the file already says, the per-file cap, then warnings for what is left
    files: dict[Path, str] = {}
    kept: list[dict] = []
    per_file: dict[str, int] = defaultdict(int)
    for c in sorted(out, key=lambda c: -c["score"]):
        target = Path(c["target_path"])
        if target not in files:
            try:
                files[target] = target.read_text()
            except OSError:
                files[target] = ""
        if overlap(c["text"], files[target]):
            continue
        if not (c["chosen"] or (decided and decided(c))):
            if per_file[c["target_path"]] >= MAX_PER_FILE:
                if waiting is not None:
                    waiting.append(c)
                continue
            per_file[c["target_path"]] += 1
        c["warnings"] = warnings_for(c["text"], target, c["project_path"], conn)
        kept.append(c)
    return kept


def _candidate(item: dict, text: str, target: Path, project: str | None, agent: str, evidence: dict,
               anchor: int) -> dict:
    where = "user level" if project is None else (item["project_name"] or Path(project).name)
    stage = item["stage"] or "provisional"
    line = f"{stage} · confirmed in {evidence['sessions']} session{'s' if evidence['sessions'] != 1 else ''}"
    if evidence["projects"] > 1:
        line += f" across {evidence['projects']} projects"
    return {
        "key": f"knowledge:{anchor}:{target}",
        "kind": "instruction",
        "origin": "knowledge",
        "cause_id": None,
        "knowledge_id": anchor,
        "project_path": project,
        "agent": agent,
        "target_path": str(target),
        "title": one_line(item["title"], 160),
        "text": text,
        "evidence": {**evidence, "line": f"{line} · {where}", "generated_text": text},
        "warnings": {"sensitive": [], "public_repo": False, "untracked": False, "overlap": False},
        "score": round(item["_score"], 3),
    }
