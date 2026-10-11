"""Configuration: paths, defaults, and the user-editable config.toml."""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_TOML = """\
# Interlatch configuration.
# Edit freely; changes apply on the next sync / worker run.

[sources]
# Claude Code config directories to scan (each one contains projects/).
claude_dirs = ["~/.claude"]
# OpenAI Codex homes to scan (sessions, memories, and the Claude sessions Codex Desktop imported).
# Empty = not connected; `interlatch connect codex` (or the dashboard's Sources page) fills it in.
codex_dirs = []
# Codex Cloud tasks (chatgpt.com/codex), listed through the codex CLI on every sync: title, repository, diff.
# Off by default because it goes online; `interlatch connect codex-cloud`.
codex_cloud = false
# GitHub Copilot: Copilot agent homes (~/.copilot) and VS Code User directories (Copilot Chat). `interlatch connect copilot`.
copilot_dirs = []
# IBM Bob homes (~/.bob). `interlatch connect bob`.
bob_dirs = []
# Google Antigravity homes (~/.gemini/antigravity). `interlatch connect antigravity`.
antigravity_dirs = []
# Recover prompts of sessions whose transcripts Claude Code already deleted (from history.jsonl).
import_history = true
# Import Claude's auto-memory files (projects/*/memory/*.md) as knowledge.
import_memory = true
# Project paths (glob patterns) to ignore entirely, e.g. ["/Users/me/secret/*"].
exclude_projects = []

[analysis]
# Analyze sessions automatically once they go idle.
auto = true
# What does the analysis. A coding agent: "claude" (Claude Code, `claude -p`) or "codex" (OpenAI Codex, `codex exec`)
# through your own login, or "bob" (IBM Bob Shell, `bob run`, with a Bob API key). Or a model provider's API, set up under [providers.<name>] below: "anthropic",
# "bedrock", "openai", "azure", "openrouter", "ollama" (models on this computer), or "openai-compatible".
backend = "claude"
# Claude model (backend "claude").
model = "sonnet"
effort = "medium"
# Spend cap per `claude -p` call, in API-equivalent USD (Codex reports tokens only).
max_budget_usd = 3.0
# A session must be idle this long (or have ended) before it is analyzed.
idle_minutes = 20
# Sessions with fewer human prompts than this are skipped.
min_prompts = 1
# Max session analyses per worker run (the background job runs every 15 minutes).
max_per_run = 6
# Parallel analysis processes.
concurrency = 2
# Also analyze sessions recorded before Interlatch was installed (newest first, within max_per_run).
backfill = true
# Characters of condensed transcript per call; longer sessions are map-reduced.
chunk_chars = 150000
timeout_seconds = 900
# Path to the claude executable (auto-detected when empty).
claude_bin = ""
# Codex model (backend "codex"); empty uses Codex's default.
codex_model = ""
# Path to the codex executable (auto-detected when empty).
codex_bin = ""
# Path to IBM Bob Shell's bob executable (auto-detected when empty). Its API key: `interlatch config set-key bob`.
bob_bin = ""
# Model that screens imported chats (`interlatch screen`): it reads only each chat's opening.
screen_model = "haiku"
# Language Interlatch writes in: summaries, knowledge, knowledge bases, the playbook, glossary definitions, weekly reviews,
# screening reasons, and the lines it proposes for CLAUDE.md / AGENTS.md. "en" or "ja". Applies to sessions analyzed
# from now on.
language = "en"

[synthesis]
# Consolidate per-project knowledge bases once enough new knowledge accumulates.
auto = true
model = "sonnet"
min_new_items = 3

[export]
# Mirror everything into an Obsidian-compatible Markdown vault.
markdown = true
# Where the vault lives (empty = <interlatch home>/notes).
notes_dir = ""

[server]
host = "127.0.0.1"
port = 11524
# Other names the dashboard answers to besides 127.0.0.1 and localhost, e.g. its Tailscale name
# ("pc.tail1234.ts.net"). `interlatch tailnet on` sets this.
allowed_hosts = []
# Reached by one of those names through Tailscale Serve: only these Tailscale logins get in
# (empty = everyone on your tailnet). `interlatch tailnet on` sets it to yours.
allowed_users = []
# Company sign-in in front of the dashboard (an auth proxy such as oauth2-proxy or Azure's Easy Auth): the request
# header that names the signed-in person's email, e.g. "X-Forwarded-Email". Only trusted from trusted_proxies, and
# only for people added on this hub (people.py). Empty: people sign in with an invite or sign-in link.
auth_header = ""
trusted_proxies = ["127.0.0.1", "::1"]
# A reverse proxy on this computer forwards to the dashboard (HTTPS for a team hub): then no request counts as made
# at this computer itself, so nobody is an admin just by coming through the proxy. Admins sign in, or use the CLI.
behind_proxy = false
# macOS: the dashboard that opens at login also shows Interlatch's icon in the menu bar (needs the `app` extra).
# Off unless you turn it on: `interlatch install` asks, or `interlatch install --menu-bar`.
menu_bar = false

[hub]
# On a computer that sends its sessions to another one (the hub): the hub's address. Set by `interlatch hub join`.
url = ""
# On the hub: folders on the other computers that hold the same projects as a folder here,
# e.g. { "/home/me/code" = "/Users/me/Projects" }. Projects are also matched by their git remote.
path_map = {}
# On a computer that sends to a hub: folders here whose sessions belong to a project on the hub, folder and all
# below it. Set by `interlatch hub add-folder <folder> --project <name>`.
folders = {}
# On a computer that sends to a hub: what it sends. "everything": its transcripts, and the hub records and analyzes
# them. "knowledge": this computer keeps recording and analyzing with its own Claude Code (or Codex) login and sends
# only each session's details, summary and project lessons; transcripts and personal lessons stay here.
share = "everything"
# On a computer that shares knowledge with a hub: false shares only sessions the hub files under one of its projects
# (in a folder added with `interlatch hub add-folder`, or in a repository whose git remote the hub files there); the
# rest stay here. true shares sessions from every folder. Transcripts (share = "everything") always go in full.
all_folders = false
# On a computer that shares knowledge with a hub: projects on the hub (their paths there) it left. It no longer shares
# sessions filed under them or gets their teammates' lessons; what it already shared stays on the hub. Set by
# `interlatch hub leave --project <name>`, emptied again by `interlatch hub rejoin --project <name>`.
left = []
# On the hub: what it takes from the computers that send to it. "everything": transcripts, or knowledge from those that
# share knowledge. "knowledge": summaries and project lessons only, from every computer; one that sends transcripts is
# turned away until it shares knowledge (`interlatch config set hub.share knowledge`). Any other value counts as
# "knowledge".
accept = "everything"
# On the hub: also keep the team's record in Postgres ("postgres"): what computers share with share = "knowledge",
# lessons merged across them, and an audit log. Computers that share get their teammates' lessons for their projects
# back. The connection (PGHOST, PGDATABASE, PGUSER, PGPASSWORD, ...) is read from team-store.env in Interlatch's
# folder; the Postgres driver comes with the team extra: uv tool install 'interlatch[team]'.
store = ""
# On the hub, once it has people (`interlatch hub invite`): whether computers may still send with the hub's one shared
# token instead of a token of their own. Turn off when everyone has joined with an invite.
shared_token = true
# On the hub: its address as the other computers and browsers reach it (e.g. "https://interlatch.example.internal"),
# for the join commands and sign-in links it hands out. `interlatch hub enable --url` sets it.
address = ""
# On the hub: the name its dashboard shows, e.g. "Resona team". Empty: this computer's name.
name = ""
# On the hub: it runs on a server for the team and records no sessions of its own (the Docker image sets this). Its
# dashboard then leaves out what only a person's own computer needs, and its admins set up projects there by name.
dedicated = false

[mirror]
# Keep a copy of your archive in a Postgres database you choose (on this computer, in Docker, or in the cloud), for
# SQL, BI tools and a copy kept elsewhere: "postgres". Written after every background run; Interlatch keeps working
# from its own database and never reads the copy back. The connection (PGHOST, PGDATABASE, PGUSER, PGPASSWORD, ...) is
# read from mirror.env in Interlatch's folder; the driver comes with: uv tool install 'interlatch[postgres]'.
to = ""
# What the copy holds. "knowledge": session details, summaries and analyses, lessons, knowledge bases, reviews,
# glossary, artifacts, token usage and files touched; no prompts or transcripts. "everything": prompts and transcripts
# too, with secrets redacted. Sessions in excluded projects never go.
include = "knowledge"
# The Postgres schema it writes to. One computer per schema.
schema = "chronicle"

[inject]
# Inject a short digest of the project's knowledge base into new sessions (SessionStart hook).
session_start = false
max_chars = 3000

[updates]
# Ask pypi.org for the latest version once a day while the dashboard is open (off: only when you click
# Check for updates). Sends nothing about you.
check_daily = false
# Show a desktop notification when a new version is out: the background sync asks pypi.org once a day and
# notifies once per release. Sends nothing about you.
notify = false

[suggestions]
# Propose fixes for what keeps going wrong (lines for CLAUDE.md / AGENTS.md, config changes, setup steps), refreshed
# after every background sync. Nothing is written until you approve a suggestion; `interlatch suggest`.
enabled = true
# Show a desktop notification when new suggestions arrive.
notify = false

[systems]
# The Systems map reads a few manifest files in each project folder (package.json, pyproject.toml, compose files,
# Dockerfiles, Terraform, CI workflows, vite configs, .env.example, deploy configs), read-only and nothing else.
# Off: the map uses only what sessions recorded.
read_manifests = true

# Model providers for analysis.backend = "<name>". Keys: base_url, model (analysis and knowledge bases),
# small_model (screening imported chats), max_output_tokens, chunk_chars (characters of transcript per call), and
# region + profile (bedrock), resource (azure), num_ctx (ollama). API keys are not kept here: the dashboard
# (Status › Analysis) or `interlatch config set-key <name>` stores them in provider-keys.json, readable by you only;
# the provider's usual variable (OPENAI_API_KEY, ...) is read when none is stored. For example:
# [providers.ollama]
# model = "qwen3:30b"
# num_ctx = 32768
"""


LANGUAGES = {"en": "English", "ja": "日本語"}  # [analysis] language: code -> its own name, as the picker shows it
SHARE_MODES = ("everything", "knowledge")  # [hub] share, and [hub] accept on the hub
STORES = ("", "postgres")  # [hub] store
MIRRORS = ("", "postgres")  # [mirror] to
MIRROR_INCLUDES = ("knowledge", "everything")  # [mirror] include


ENV_PREFIXES = ("INTERLATCH_", "CHRONICLE_")  # Interlatch was called Chronicle: its variables still count


def env(name: str, default: str | None = None, *, environ: Mapping[str, str] | None = None) -> str | None:
    """The variable INTERLATCH_<name>, else CHRONICLE_<name> (its name before the rename), else `default`. An empty one
    counts as unset (a compose file passes INTERLATCH_X empty to a hub whose .env still says CHRONICLE_X)."""
    environ = os.environ if environ is None else environ
    return next((environ[p + name] for p in ENV_PREFIXES if environ.get(p + name)), default)


def default_home() -> Path:
    return Path("~/.interlatch").expanduser()


def legacy_home() -> Path:
    """Where Chronicle kept everything before the rename; `interlatch migrate` (migrate.py) moves it to default_home()."""
    return Path("~/.claude-chronicle").expanduser()


def home_override() -> Path | None:
    """The folder INTERLATCH_HOME (or CHRONICLE_HOME) names, unless that is one of the two default folders: launchd
    agents written before the rename name ~/.claude-chronicle, and that one is moved like an unset variable."""
    raw = env("HOME")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return None if os.path.abspath(path) in {str(default_home()), str(legacy_home())} else path


def chronicle_home() -> Path:
    """Interlatch's folder: INTERLATCH_HOME (or CHRONICLE_HOME), else ~/.interlatch, or ~/.claude-chronicle until
    that has been moved there."""
    override = home_override()
    if override is not None:
        return override
    new, old = default_home(), legacy_home()
    return old if not os.path.lexists(new) and old.is_dir() else new


@dataclass
class AnalysisConfig:
    auto: bool = True
    model: str = "sonnet"
    effort: str = "medium"
    max_budget_usd: float = 3.0
    idle_minutes: int = 20
    min_prompts: int = 1
    max_per_run: int = 6
    concurrency: int = 2
    backfill: bool = True
    chunk_chars: int = 150_000
    timeout_seconds: int = 900
    backend: str = "claude"
    claude_bin: str = ""
    codex_model: str = ""
    codex_bin: str = ""
    bob_bin: str = ""
    screen_model: str = "haiku"  # `interlatch screen`: sorts imported chats, reading only their openings
    language: str = "en"  # what Interlatch writes in (LANGUAGES); codes the parser reads stay English


@dataclass
class SynthesisConfig:
    auto: bool = True
    model: str = "sonnet"
    min_new_items: int = 3


@dataclass
class Config:
    home: Path
    claude_dirs: list[Path] = field(default_factory=list)
    codex_dirs: list[Path] = field(default_factory=list)
    codex_cloud: bool = False
    copilot_dirs: list[Path] = field(default_factory=list)
    bob_dirs: list[Path] = field(default_factory=list)
    antigravity_dirs: list[Path] = field(default_factory=list)
    import_history: bool = True
    import_memory: bool = True
    exclude_projects: list[str] = field(default_factory=list)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    synthesis: SynthesisConfig = field(default_factory=SynthesisConfig)
    export_markdown: bool = True
    notes_dir: Path = Path("~/.interlatch/notes")
    server_host: str = "127.0.0.1"
    server_port: int = 11524
    server_allowed_hosts: list[str] = field(default_factory=list)
    server_allowed_users: list[str] = field(default_factory=list)
    server_auth_header: str = ""
    server_trusted_proxies: list[str] = field(default_factory=lambda: ["127.0.0.1", "::1"])
    server_behind_proxy: bool = False
    server_menu_bar: bool = False
    hub_url: str = ""
    hub_path_map: dict[str, str] = field(default_factory=dict)
    hub_folders: dict[str, str] = field(default_factory=dict)
    hub_share: str = "everything"
    hub_all_folders: bool = False
    hub_left: list[str] = field(default_factory=list)
    hub_accept: str = "everything"
    hub_store: str = ""
    hub_shared_token: bool = True
    hub_address: str = ""
    hub_name: str = ""
    hub_dedicated: bool = False
    mirror_to: str = ""
    mirror_include: str = "knowledge"
    mirror_schema: str = "chronicle"
    inject_session_start: bool = False
    inject_max_chars: int = 3000
    update_check_daily: bool = False
    update_notify: bool = False
    suggestions_enabled: bool = True
    suggestions_notify: bool = False
    systems_read_manifests: bool = True
    providers: dict[str, dict] = field(default_factory=dict)  # [providers.<name>]: see providers.py
    loaded: tuple[int, int] | None = field(default=None, repr=False, compare=False)  # config.toml when read: current()

    # ---- derived paths -------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.home / "chronicle.db"

    @property
    def archive_dir(self) -> Path:
        return self.home / "archive"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def locks_dir(self) -> Path:
        return self.home / "locks"

    @property
    def config_path(self) -> Path:
        return self.home / "config.toml"

    @property
    def machines_dir(self) -> Path:
        """On a hub: the files other computers sent, one folder per computer."""
        return self.home / "machines"

    @property
    def is_spoke(self) -> bool:
        """This computer has joined a hub (`[hub] url`), whatever it sends it."""
        return bool(self.hub_url)

    @property
    def sends_files(self) -> bool:
        """It sends its transcripts to the hub, which records and analyzes them, instead of doing that itself."""
        return self.is_spoke and self.hub_share != "knowledge"

    @property
    def shares_knowledge(self) -> bool:
        """It records and analyzes its own sessions and sends the hub only what was learned (`[hub] share`)."""
        return self.is_spoke and self.hub_share == "knowledge"

    def ensure_dirs(self) -> None:
        for d in (self.home, self.archive_dir, self.logs_dir, self.locks_dir):
            d.mkdir(parents=True, exist_ok=True)
        # every transcript, the hub's tokens and API keys live under it: this user's only, whatever the umask
        try:
            st = self.home.stat()
            if os.name == "posix" and st.st_uid == os.getuid() and st.st_mode & 0o077:
                self.home.chmod(st.st_mode & 0o700)
        except OSError:
            pass

    def is_excluded(self, project_path: str | None) -> bool:
        if not project_path:
            return False
        return any(fnmatch.fnmatch(project_path, pat) for pat in self.exclude_projects)

    def claude_bin(self) -> str | None:
        if self.analysis.claude_bin:
            return str(Path(self.analysis.claude_bin).expanduser())
        found = shutil.which("claude")
        if found:
            return found
        for candidate in ("~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
            p = Path(candidate).expanduser()
            if p.exists():
                return str(p)
        return None

    def codex_bin(self) -> str | None:
        if self.analysis.codex_bin:
            return str(Path(self.analysis.codex_bin).expanduser())
        found = shutil.which("codex")
        if found:
            return found
        for candidate in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex", "~/.local/bin/codex"):
            p = Path(candidate).expanduser()
            if p.exists():
                return str(p)
        return None

    def bob_bin(self) -> str | None:
        """IBM Bob Shell (npm package bobshell), which installs as `bob`."""
        if self.analysis.bob_bin:
            return str(Path(self.analysis.bob_bin).expanduser())
        found = shutil.which("bob")
        if found:
            return found
        for candidate in ("/opt/homebrew/bin/bob", "/usr/local/bin/bob", "~/.local/bin/bob", "~/.npm-global/bin/bob"):
            p = Path(candidate).expanduser()
            if p.exists():
                return str(p)
        return None

    def is_internal_path(self, path: str | None) -> bool:
        """`path` is in Interlatch's own working folder, where agents run its analyses: not a project of yours. Runs from
        before the rename name the working folder in ~/.claude-chronicle."""
        if not path:
            return False
        work = self.home / "workdir"
        return any(str(path).rstrip("/") == str(w) or str(path).startswith(f"{w}/")
                   for w in {str(work), os.path.realpath(work), str(legacy_home() / "workdir")})


def _section(data: dict, name: str) -> dict:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def _pick(cls, values: dict):
    allowed = cls.__dataclass_fields__.keys()
    return cls(**{k: v for k, v in values.items() if k in allowed})


def load_config(home: Path | None = None, *, create: bool = True) -> Config:
    home = (home or chronicle_home()).expanduser()
    path = home / "config.toml"
    if create and not path.exists():
        home.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TOML)
    data: dict = {}
    loaded = _stamp(path)  # before reading: an edit made while it is read is picked up by the next current()
    if path.exists():
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:  # keep running on a broken edit, loudly
            import logging

            logging.getLogger("interlatch").error("config.toml is invalid (%s); using defaults", exc)
    sources = _section(data, "sources")
    export = _section(data, "export")
    server = _section(data, "server")
    inject = _section(data, "inject")
    hub = _section(data, "hub")
    path_map = hub.get("path_map")
    folders = hub.get("folders")
    share = str(hub.get("share") or "everything").strip().lower()
    accept = str(hub.get("accept") or "everything").strip().lower()
    store = str(hub.get("store") or "").strip().lower()
    mirror = _section(data, "mirror")
    mirror_to = str(mirror.get("to") or "").strip().lower()
    mirror_include = str(mirror.get("include") or "knowledge").strip().lower()
    mirror_schema = str(mirror.get("schema") or "chronicle").strip()

    env_dirs = env("CLAUDE_DIRS")
    raw_dirs = env_dirs.split(os.pathsep) if env_dirs else sources.get("claude_dirs", ["~/.claude"])

    cfg = Config(
        home=home,
        claude_dirs=[Path(d).expanduser() for d in raw_dirs],
        codex_dirs=[Path(d).expanduser() for d in sources.get("codex_dirs", [])],
        codex_cloud=bool(sources.get("codex_cloud", False)),
        copilot_dirs=[Path(d).expanduser() for d in sources.get("copilot_dirs", [])],
        bob_dirs=[Path(d).expanduser() for d in sources.get("bob_dirs", [])],
        antigravity_dirs=[Path(d).expanduser() for d in sources.get("antigravity_dirs", [])],
        import_history=bool(sources.get("import_history", True)),
        import_memory=bool(sources.get("import_memory", True)),
        exclude_projects=list(sources.get("exclude_projects", [])),
        analysis=_pick(AnalysisConfig, _section(data, "analysis")),
        synthesis=_pick(SynthesisConfig, _section(data, "synthesis")),
        export_markdown=bool(export.get("markdown", True)),
        notes_dir=Path(export.get("notes_dir") or (home / "notes")).expanduser(),
        server_host=str(server.get("host", "127.0.0.1")),
        server_port=int(server.get("port", 11524)),
        server_allowed_hosts=[str(h).strip().lower() for h in server.get("allowed_hosts") or [] if str(h).strip()],
        server_allowed_users=[str(u).strip() for u in server.get("allowed_users") or [] if str(u).strip()],
        server_auth_header=str(server.get("auth_header") or "").strip(),
        server_behind_proxy=bool(server.get("behind_proxy", False)),
        server_menu_bar=bool(server.get("menu_bar", False)),
        server_trusted_proxies=[str(x).strip() for x in server.get("trusted_proxies", ["127.0.0.1", "::1"]) or [] if str(x).strip()],
        hub_url=str(hub.get("url") or "").strip().rstrip("/"),
        hub_path_map={str(k).rstrip("/"): str(v).rstrip("/") for k, v in path_map.items()} if isinstance(path_map, dict) else {},
        hub_folders={str(Path(str(k)).expanduser()).rstrip("/") or "/": str(v).rstrip("/") for k, v in folders.items()
                     if str(k).strip() and str(v).strip()} if isinstance(folders, dict) else {},
        hub_share=share if share in SHARE_MODES else "everything",
        hub_all_folders=hub.get("all_folders") is True,  # only an explicit true shares every folder
        hub_left=sorted({str(x).rstrip("/") for x in hub.get("left") or [] if str(x).startswith("/")})
        if isinstance(hub.get("left"), list) else [],
        hub_accept="everything" if accept == "everything" else "knowledge",  # a typo never lets transcripts in
        hub_store=store if store in STORES else "",
        hub_shared_token=bool(hub.get("shared_token", True)),
        hub_address=str(hub.get("address") or "").strip().rstrip("/"),
        hub_name=str(hub.get("name") or "").strip()[:80],
        hub_dedicated=hub.get("dedicated") is True,
        mirror_to=mirror_to if mirror_to in MIRRORS else "",
        mirror_include="everything" if mirror_include == "everything" else "knowledge",  # a typo never sends transcripts
        mirror_schema=mirror_schema if mirror_schema.isidentifier() and mirror_schema.isascii() else "chronicle",
        inject_session_start=bool(inject.get("session_start", False)),
        inject_max_chars=int(inject.get("max_chars", 3000)),
        update_check_daily=bool(_section(data, "updates").get("check_daily", False)),
        update_notify=bool(_section(data, "updates").get("notify", False)),
        suggestions_enabled=bool(_section(data, "suggestions").get("enabled", True)),
        suggestions_notify=bool(_section(data, "suggestions").get("notify", False)),
        systems_read_manifests=bool(_section(data, "systems").get("read_manifests", True)),
        providers={str(k): v for k, v in _section(data, "providers").items() if isinstance(v, dict)},
        loaded=loaded,
    )
    if cfg.analysis.language not in LANGUAGES:
        import logging

        logging.getLogger("interlatch").warning("[analysis] language %r is not one of %s; using \"en\"",
                                               cfg.analysis.language, ", ".join(LANGUAGES))
        cfg.analysis.language = "en"
    if store not in STORES:
        import logging

        logging.getLogger("interlatch").warning("[hub] store %r is not one of: \"postgres\", or empty; keeping the "
                                               "team's record in the hub's SQLite only", store)
    for name, value, allowed in (("to", mirror_to, MIRRORS), ("include", mirror_include, MIRROR_INCLUDES)):
        if value not in allowed:
            import logging

            logging.getLogger("interlatch").warning("[mirror] %s %r is not one of: %s; using %r", name, value,
                                                   ", ".join(f'"{x}"' for x in allowed), getattr(cfg, f"mirror_{name}"))
    if cfg.mirror_schema != mirror_schema:
        import logging

        logging.getLogger("interlatch").warning("[mirror] schema %r is not a plain name (letters, digits, _); using "
                                               "\"chronicle\"", mirror_schema)
    if accept not in SHARE_MODES:
        import logging

        logging.getLogger("interlatch").warning("[hub] accept %r is not one of: \"everything\", \"knowledge\"; taking "
                                               "knowledge only", accept)
    return cfg


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def current(cfg: Config) -> Config:
    """`cfg` as config.toml says now. A job that runs for minutes holds the config it started with; what it then tells
    the hub (the folders this computer shares, the projects it left) must be what the person set since. A config
    that was not read from the file (tests) is kept as it is."""
    if cfg.loaded is None or _stamp(cfg.config_path) == cfg.loaded:
        return cfg
    return load_config(cfg.home, create=False)


def toml_table(d: dict[str, str]) -> str:
    """An inline TOML table, for set_config_value (JSON strings are valid TOML basic strings)."""
    return "{ " + ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in d.items()) + " }" if d else "{}"


def set_config_value(cfg: "Config", section: str, key: str, value: str) -> None:
    """Set `key = value` (value is a TOML literal) inside [section] of config.toml, keeping comments and layout.

    Replaces an existing key (including a multi-line array), inserts into an existing section, or appends the
    section. The result is validated before it is written, so a bad edit never corrupts the file.
    """
    import re

    path = cfg.config_path
    lines = (path.read_text() if path.exists() else DEFAULT_CONFIG_TOML).splitlines()
    header = next((i for i, l in enumerate(lines) if l.strip() == f"[{section}]"), None)
    new_line = f"{key} = {value}"
    if header is None:
        lines += ["", f"[{section}]", new_line]
    else:
        end = next((j for j in range(header + 1, len(lines)) if lines[j].lstrip().startswith("[")), len(lines))
        at = next((j for j in range(header + 1, end) if re.match(rf"^\s*{re.escape(key)}\s*=", lines[j])), None)
        if at is None:
            last = max([j for j in range(header, end) if lines[j].strip()], default=header)
            lines.insert(last + 1, new_line)
        else:
            stop = at
            if "[" in lines[at].split("=", 1)[1] and "]" not in lines[at].split("=", 1)[1]:
                stop = next((j for j in range(at + 1, end) if "]" in lines[j]), at)
            lines[at:stop + 1] = [new_line]
    text = "\n".join(lines).rstrip() + "\n"
    tomllib.loads(text)  # raises before anything is written
    path.write_text(text)


def remove_config_value(cfg: "Config", section: str, key: str) -> None:
    """Remove `key` from [section] of config.toml (a no-op when it is not there), keeping everything else."""
    import re

    path = cfg.config_path
    if not path.exists():
        return
    lines = path.read_text().splitlines()
    header = next((i for i, l in enumerate(lines) if l.strip() == f"[{section}]"), None)
    if header is None:
        return
    end = next((j for j in range(header + 1, len(lines)) if lines[j].lstrip().startswith("[")), len(lines))
    at = next((j for j in range(header + 1, end) if re.match(rf"^\s*{re.escape(key)}\s*=", lines[j])), None)
    if at is None:
        return
    del lines[at]
    text = "\n".join(lines).rstrip() + "\n"
    tomllib.loads(text)
    path.write_text(text)
