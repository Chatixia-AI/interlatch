"""The Systems map: every folder your agents worked in, as a system with the parts it runs and the systems and services
it connects to, drawn only from evidence, with no model call.

Two kinds of evidence. A project's manifests, read-only and only these files: package.json, pyproject.toml,
requirements.txt, Cargo.toml, go.mod, compose files, Dockerfiles, Terraform (*.tf), GitHub workflows, vite configs,
.env.example and a few deploy configs (firebase.json, wrangler.toml, databricks.yml, host.json, ...). And what sessions
did: the commands they ran (servers started, hosts and clouds reached, machines ssh'd into), the files they touched
(also in other projects), and how the glossary says one project uses another.

Systems are grouped by the folders they live in (Work › AI-BPO › Cosmo). Inside a system, parts sit in five roles: ways
in (UIs, CLIs, extensions), code (APIs, workers, packages), data (databases and files), delivery (CI, Terraform) and
what it runs on or uses (deployed apps, servers, clouds, APIs). Every part, connection and link keeps the evidence that
put it there."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

from .redact import redact
from .util import one_line

CHAT_SOURCES = ("chatgpt-export", "claude-ai-export")
NOT_PROJECTS = ("", "Desktop", "Downloads", "Documents")  # folders under home that hold sessions but are no project
SKIP_DIRS = {"node_modules", ".venv", "venv", "env", ".git", "dist", "build", "out", ".next", ".nuxt", "__pycache__",
             ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", "vendor", "coverage", "site-packages",
             ".playwright-mcp", ".idea", ".vscode", "tmp", ".cache", ".turbo", ".svelte-kit", "Library", "Pods",
             "artifacts", "archive", "logs", "output", "outputs", "screenshots", "Temp"}
NON_CODE_DIRS = {"docs", "doc", "meetings", "tests", "test", "assets", "images", "img", "public", "static", "examples",
                 "notebooks", "scripts", ".github", "data", "fixtures", "packaging", "infra", "deploy", "terraform"}
MAX_DEPTH = 2  # manifests are read from the project root and two folder levels down
ROLES = ("way_in", "code", "data", "delivery", "runtime")

# ------------------------------------------------------------------ what dependencies say
UI_DEPS = {"react": "React", "vue": "Vue", "svelte": "Svelte", "@sveltejs/kit": "SvelteKit", "next": "Next.js",
           "nuxt": "Nuxt", "@angular/core": "Angular", "solid-js": "Solid", "astro": "Astro", "preact": "Preact",
           "lit": "Lit", "vite": "Vite", "three": "Three.js", "streamlit": "Streamlit", "gradio": "Gradio", "dash": "Dash",
           "nicegui": "NiceGUI", "reflex": "Reflex", "textual": "Textual"}
API_DEPS = {"fastapi": "FastAPI", "flask": "Flask", "django": "Django", "starlette": "Starlette", "aiohttp": "aiohttp",
            "sanic": "Sanic", "litestar": "Litestar", "quart": "Quart", "tornado": "Tornado", "express": "Express",
            "fastify": "Fastify", "hono": "Hono", "koa": "Koa", "@nestjs/core": "NestJS", "axum": "Axum",
            "actix-web": "Actix", "warp": "warp", "rocket": "Rocket", "github.com/gin-gonic/gin": "Gin",
            "github.com/labstack/echo/v4": "Echo", "github.com/gofiber/fiber/v2": "Fiber"}
DESKTOP_DEPS = {"electron": "Electron", "@tauri-apps/api": "Tauri", "tauri": "Tauri", "pywebview": "pywebview"}
MCP_DEPS = {"mcp": "MCP", "fastmcp": "MCP", "@modelcontextprotocol/sdk": "MCP", "rmcp": "MCP"}
WORKER_DEPS = {"celery": "Celery", "rq": "RQ", "dramatiq": "Dramatiq", "arq": "arq", "bullmq": "BullMQ", "apscheduler": "APScheduler"}
CLI_DEPS = {"typer": "Typer", "click": "Click", "clap": "clap", "github.com/spf13/cobra": "Cobra", "commander": "Commander"}
# a dependency that talks to something outside the code: (label, role) — data for stores, runtime for services
SERVICE_DEPS = {
    "openai": ("OpenAI API", "runtime"), "anthropic": ("Anthropic API", "runtime"), "@anthropic-ai/sdk": ("Anthropic API", "runtime"),
    "claude-agent-sdk": ("Claude Agent SDK", "runtime"), "@anthropic-ai/claude-agent-sdk": ("Claude Agent SDK", "runtime"),
    "google-genai": ("Gemini API", "runtime"), "google-generativeai": ("Gemini API", "runtime"), "@google/genai": ("Gemini API", "runtime"),
    "ibm-watsonx-ai": ("watsonx.ai", "runtime"), "litellm": ("LiteLLM", "runtime"), "ollama": ("Ollama", "runtime"),
    "langfuse": ("Langfuse", "runtime"), "mlflow": ("MLflow", "runtime"), "sentry-sdk": ("Sentry", "runtime"),
    "databricks-sdk": ("Databricks", "runtime"), "databricks-sql-connector": ("Databricks SQL", "runtime"),
    "databricks-connect": ("Databricks", "runtime"), "azure-ai-documentintelligence": ("Azure Document Intelligence", "runtime"),
    "azure-ai-formrecognizer": ("Azure Document Intelligence", "runtime"), "azure-search-documents": ("Azure AI Search", "runtime"),
    "azure-functions": ("Azure Functions", "runtime"), "azure-identity": ("Azure (Entra ID)", "runtime"),
    "azure-storage-blob": ("Azure Blob Storage", "data"), "azure-cosmos": ("Azure Cosmos DB", "data"),
    "@azure/storage-blob": ("Azure Blob Storage", "data"), "ibm-cos-sdk": ("IBM Cloud Object Storage", "data"),
    "boto3": ("AWS", "runtime"), "firebase-admin": ("Firebase", "runtime"), "firebase": ("Firebase", "runtime"),
    "supabase": ("Supabase", "data"), "@supabase/supabase-js": ("Supabase", "data"), "stripe": ("Stripe", "runtime"),
    "slack-sdk": ("Slack", "runtime"), "slack-bolt": ("Slack", "runtime"), "@slack/bolt": ("Slack", "runtime"),
    "@slack/web-api": ("Slack", "runtime"), "pysnow": ("ServiceNow", "runtime"),
    "psycopg": ("PostgreSQL", "data"), "psycopg2": ("PostgreSQL", "data"), "psycopg2-binary": ("PostgreSQL", "data"),
    "asyncpg": ("PostgreSQL", "data"), "pg": ("PostgreSQL", "data"), "postgres": ("PostgreSQL", "data"),
    "duckdb": ("DuckDB", "data"), "aiosqlite": ("SQLite", "data"), "better-sqlite3": ("SQLite", "data"),
    "redis": ("Redis", "data"), "ioredis": ("Redis", "data"), "pymongo": ("MongoDB", "data"), "motor": ("MongoDB", "data"),
    "mongoose": ("MongoDB", "data"), "chromadb": ("Chroma", "data"), "qdrant-client": ("Qdrant", "data"),
    "pinecone": ("Pinecone", "data"), "pinecone-client": ("Pinecone", "data"), "elasticsearch": ("Elasticsearch", "data"),
    "pymysql": ("MySQL", "data"), "mysql2": ("MySQL", "data"), "neo4j": ("Neo4j", "data"), "kafka-python": ("Kafka", "runtime"),
    "pika": ("RabbitMQ", "runtime"),
}
STORE_IMAGES = {"postgres": "PostgreSQL", "pgvector": "PostgreSQL", "timescale": "PostgreSQL", "mysql": "MySQL",
                "mariadb": "MariaDB", "redis": "Redis", "valkey": "Valkey", "mongo": "MongoDB", "clickhouse": "ClickHouse",
                "minio": "MinIO", "qdrant": "Qdrant", "elasticsearch": "Elasticsearch", "opensearch": "OpenSearch",
                "neo4j": "Neo4j", "rabbitmq": "RabbitMQ", "kafka": "Kafka", "localstack": "LocalStack", "azurite": "Azurite"}

# ------------------------------------------------------------------ what commands say
SERVICE_HOSTS = {"Azure OpenAI", "AI Services", "Workspace"}  # a host the code calls, not one it is deployed to
DATA_HOSTS = {"Blob Storage", "Project"}
HOSTS = [  # (suffix, platform, what lives there)
    (".scm.azurewebsites.net", "Azure", "App Service"), (".azurewebsites.net", "Azure", "App Service"),
    (".azurecontainerapps.io", "Azure", "Container App"), (".azurestaticapps.net", "Azure", "Static Web App"),
    (".openai.azure.com", "Azure", "Azure OpenAI"), (".cognitiveservices.azure.com", "Azure", "AI Services"),
    (".blob.core.windows.net", "Azure", "Blob Storage"), (".azuredatabricks.net", "Databricks", "Workspace"),
    (".databricksapps.com", "Databricks", "Databricks App"), (".cloud.databricks.com", "Databricks", "Workspace"),
    (".codeengine.appdomain.cloud", "IBM Cloud", "Code Engine"), (".appdomain.cloud", "IBM Cloud", "App"),
    (".web.app", "Firebase", "Hosting"), (".firebaseapp.com", "Firebase", "Hosting"),
    (".github.io", "GitHub", "Pages"), (".pages.dev", "Cloudflare", "Pages"), (".workers.dev", "Cloudflare", "Worker"),
    (".vercel.app", "Vercel", "Deployment"), (".netlify.app", "Netlify", "Site"), (".fly.dev", "Fly.io", "App"),
    (".onrender.com", "Render", "Service"), (".herokuapp.com", "Heroku", "App"), (".run.app", "Google Cloud", "Cloud Run"),
    (".supabase.co", "Supabase", "Project"),
]
HOST_RE = re.compile(r"\b((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+?(?:"
                     + "|".join(re.escape(s[1:]) for s, _, _ in HOSTS) + r"))\b", re.I)
CLIS = {  # first word of a command -> (platform, role)
    "az": ("Azure", "runtime"), "func": ("Azure Functions", "runtime"), "ibmcloud": ("IBM Cloud", "runtime"),
    "databricks": ("Databricks", "runtime"), "firebase": ("Firebase", "runtime"), "gcloud": ("Google Cloud", "runtime"),
    "wrangler": ("Cloudflare", "runtime"), "vercel": ("Vercel", "runtime"), "flyctl": ("Fly.io", "runtime"),
    "kubectl": ("Kubernetes", "runtime"), "oc": ("OpenShift", "runtime"), "helm": ("Kubernetes", "runtime"),
    "aws": ("AWS", "runtime"), "ollama": ("Ollama", "runtime"), "launchctl": ("launchd (macOS)", "runtime"),
    "terraform": ("Terraform", "delivery"), "psql": ("PostgreSQL", "data"), "duckdb": ("DuckDB", "data"),
    "sqlite3": ("SQLite", "data"),
}
NOT_SERVERS = {"curl", "wget", "http", "xh", "lsof", "kill", "pkill", "pgrep", "ps", "grep", "rg", "sed", "awk", "cat",
               "echo", "printf", "open", "nc", "ss", "netstat", "head", "tail", "sleep", "test", "for", "while", "if"}
PORT_FLAG = re.compile(r"(?:--port|--http-port|--server\.port)[ =](\d{4,5})\b|\bPORT=(\d{4,5})\b|http\.server\s+(\d{4,5})\b"
                       r"|--bind[ =][\w.\[\]]*:(\d{4,5})\b|\s-p\s+(\d{4,5}):\d{2,5}\b")
LOCAL_URL = re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):(\d{4,5})(/[\w./-]*)?")
SERVER_HINTS = {"uvicorn": "api", "gunicorn": "api", "hypercorn": "api", "flask": "api", "fastapi": "api",
                "vite": "ui", "next": "ui", "astro": "ui", "streamlit": "ui"}
ENV_PREFIX = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|\S*)\s+)+")
DIR_FLAG = re.compile(r"(?:--directory|--project|--prefix|--cwd|--dir|-C)[ =](\S+)")


# =====================================================================================
# small readers
# =====================================================================================
def _yaml_lite(text: str):
    """Enough YAML for compose files: nested maps, lists of scalars or maps, quoted scalars and [a, b] lists.
    Anchors, multi-documents and block scalars beyond their first line are not needed and not read."""
    lines = []
    for raw in text.splitlines():
        s = re.sub(r"(^|\s)#.*$", "", raw) if "#" in raw and not re.search(r"['\"][^'\"]*#", raw) else raw
        if s.strip() and s.strip() != "---":
            lines.append((len(s) - len(s.lstrip(" ")), s.strip()))

    def scalar(v: str):
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            return [scalar(x) for x in v[1:-1].split(",") if x.strip()]
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            return v[1:-1]
        return v

    def block(i: int, ind: int):
        if i >= len(lines):
            return None, i
        if lines[i][1].startswith("- ") or lines[i][1] == "-":
            out = []
            while i < len(lines) and lines[i][0] == ind and (lines[i][1].startswith("- ") or lines[i][1] == "-"):
                item = lines[i][1][2:].strip()
                if re.match(r"^[\w.-]+:(\s|$)", item):  # a map that starts on the dash line
                    lines[i] = (ind + 2, item)
                    val, i = mapping(i, ind + 2)
                    out.append(val)
                else:
                    out.append(scalar(item))
                    i += 1
            return out, i
        return mapping(i, ind)

    def mapping(i: int, ind: int):
        out: dict = {}
        while i < len(lines) and lines[i][0] == ind and not lines[i][1].startswith("- "):
            m = re.match(r"^(['\"]?)([^'\":]+)\1\s*:(?:\s+(.*))?$", lines[i][1])
            if not m:
                i += 1
                continue
            key, rest = m.group(2).strip(), (m.group(3) or "").strip()
            i += 1
            if rest and rest[0] not in "|>":
                out[key] = scalar(rest)
            elif i < len(lines) and (lines[i][0] > ind or (lines[i][0] == ind and lines[i][1].startswith("- "))):
                out[key], i = block(i, lines[i][0])
                if rest and rest[0] in "|>" and isinstance(out[key], dict):
                    out[key] = ""
            else:
                out[key] = None
        return out, i

    try:
        return block(0, lines[0][0])[0] if lines else None
    except (IndexError, RecursionError):
        return None


def _dep_name(spec: str) -> str:
    return re.split(r"[\s<>=!~\[;(@]", spec.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _read(path: Path, limit: int = 400_000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(errors="replace")
    except OSError:
        return ""


def repo_root(path: Path, home: Path) -> Path | None:
    """The git repository a folder belongs to: its main checkout when the folder is a worktree."""
    for d in [path, *path.parents]:
        if d == home or d == d.parent:
            return None
        g = d / ".git"
        if g.is_dir():
            return d
        if g.is_file():  # a worktree or submodule: "gitdir: <main>/.git/worktrees/<name>"
            m = re.match(r"gitdir:\s*(.+)", _read(g).strip())
            if m:
                gd = Path(m.group(1))
                gd = gd if gd.is_absolute() else (d / gd).resolve()
                if gd.parent.name == "worktrees" and gd.parent.parent.name == ".git":
                    return gd.parent.parent.parent
            return d
    return None


def git_remote(root: Path) -> dict | None:
    """origin's host and repo, without credentials: {"host", "repo", "url"}."""
    text = _read(root / ".git" / "config")
    m = re.search(r'\[remote "origin"\][^\[]*?\burl\s*=\s*(\S+)', text)
    if not m:
        return None
    url = m.group(1)
    if "://" in url:
        rest = url.split("://", 1)[1]
        rest = rest.split("@", 1)[1] if "@" in rest.split("/", 1)[0] else rest
        host, _, path = rest.partition("/")
    else:  # scp-like: git@host:org/repo.git
        rest = url.split("@", 1)[-1]
        host, _, path = rest.partition(":")
    path = re.sub(r"\.git$", "", path.replace("/_git/", "/")).strip("/")
    host = host.split(":")[0].lower()
    if not host or not path:
        return None
    return {"host": host, "repo": path, "url": f"https://{host}/{path}"}


# =====================================================================================
# a system's parts, from its manifests
# =====================================================================================
class Draft:
    """One system's parts and connections while they are being gathered."""

    def __init__(self, root: Path | None, label: str):
        self.root, self.label = root, label
        self.parts: dict[str, dict] = {}
        self.edges: dict[tuple[str, str], dict] = {}
        self.ports_unplaced: Counter = Counter()

    def part(self, pid: str, label: str, kind: str, role: str, **extra) -> dict:
        p = self.parts.get(pid)
        if p is None:
            p = self.parts[pid] = {"id": pid, "label": label, "kind": kind, "role": role, "folder": extra.pop("folder", None),
                                   "stack": [], "ports": Counter(), "calls": Counter(), "evidence": [],
                                   "activity": {"sessions": set(), "edits": 0, "last": None}, **extra}
        return p

    def evidence(self, p: dict, ev: dict) -> None:
        key = (ev.get("kind"), ev.get("text"))
        for old in p["evidence"]:
            if (old.get("kind"), old.get("text")) == key:
                return
        p["evidence"].append(ev)

    def stack(self, p: dict, *names: str) -> None:
        for n in names:
            if n and n not in p["stack"]:
                p["stack"].append(n)

    def edge(self, a: str, b: str, label: str, ev: dict | None = None, kind: str = "uses") -> None:
        if a == b or a not in self.parts or b not in self.parts:
            return
        e = self.edges.setdefault((a, b), {"from": a, "to": b, "label": label, "kind": kind, "evidence": []})
        if ev and ev not in e["evidence"]:
            e["evidence"].append(ev)

    def service(self, label: str, role: str, ev: dict, platform: str | None = None) -> dict:
        p = self.part(f"svc:{label.lower()}", label, "store" if role == "data" else "external", role, platform=platform)
        self.evidence(p, ev)
        return p

    def folder_part(self, rel: str) -> dict | None:
        """The part whose folder holds `rel` (a path relative to the root), deepest first."""
        best = None
        for p in self.parts.values():
            f = p.get("folder")
            if f is None:
                continue
            if f == "" or rel == f or rel.startswith(f + "/"):
                if best is None or len(f) > len(best["folder"]):
                    best = p
        return best


def _scan(root: Path) -> dict[str, list[Path]]:
    """Manifest files under root, by folder (relative), at most MAX_DEPTH levels down."""
    found: dict[str, list[Path]] = defaultdict(list)
    names = {"package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod", "docker-compose.yml",
             "docker-compose.yaml", "compose.yml", "compose.yaml", "Dockerfile", "firebase.json", "wrangler.toml",
             "databricks.yml", "app.yaml", "host.json", "vercel.json", "fly.toml", "render.yaml", "vite.config.ts",
             "vite.config.js", "vite.config.mjs", ".env.example", ".env.sample", "Procfile"}
    stack, seen = [(root, 0)], 0
    while stack and seen < 400:
        d, depth = stack.pop()
        seen += 1
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        rel = "" if d == root else d.relative_to(root).as_posix()
        for e in entries:
            try:
                if e.is_dir():
                    if depth < MAX_DEPTH and e.name not in SKIP_DIRS and not e.name.startswith(".") \
                            and not e.name.endswith(".worktrees") and not e.is_symlink():
                        stack.append((e, depth + 1))
                elif e.name in names or e.suffix == ".tf" or (e.name.startswith("Dockerfile.") and e.is_file()):
                    found[rel].append(e)
            except OSError:
                continue
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        found[".github/workflows"] = sorted(p for p in wf.iterdir() if p.suffix in (".yml", ".yaml"))
    return found


def _code_part(d: Draft, rel: str, deps: dict[str, str], *, lang: str, name: str | None, has_cli: bool,
               manifest: str, extra_kinds: tuple[str, ...] = ()) -> dict:
    """A folder with a package manifest: what it is, from what it depends on."""
    hit = lambda table: [v for k, v in table.items() if k in deps]  # noqa: E731
    ui, api, desk, mcp, worker = hit(UI_DEPS), hit(API_DEPS), hit(DESKTOP_DEPS), hit(MCP_DEPS), hit(WORKER_DEPS)
    kinds = [k for k, on in (("extension", "extension" in extra_kinds), ("ui", bool(ui)), ("api", bool(api)),
                              ("cli", has_cli), ("desktop", bool(desk)), ("mcp", bool(mcp)), ("worker", bool(worker))) if on]
    if "ui" in kinds and "api" in kinds and rel.rsplit("/", 1)[-1].lower() in ("backend", "api", "server", "service", "services"):
        kinds.remove("api")
        kinds.insert(0, "api")  # a backend that also renders a Streamlit page is still the backend
    kind = kinds[0] if kinds else "library"
    role = "way_in" if kind in ("ui", "cli", "extension", "desktop", "mcp") else "code"
    label = d.label if rel == "" else rel.rsplit("/", 1)[-1]
    p = d.part(f"dir:{rel}", label, kind, role, folder=rel, package=name)
    d.stack(p, lang, *ui[:2], *api[:1], *desk[:1], *(["MCP"] if mcp else []), *worker[:1], *hit(CLI_DEPS)[:1])
    if kinds[1:]:
        p["also"] = sorted(set(p.get("also", [])) | set(kinds[1:]))
    found = [k for k in deps if k in UI_DEPS or k in API_DEPS or k in DESKTOP_DEPS or k in MCP_DEPS or k in WORKER_DEPS]
    d.evidence(p, {"kind": "manifest", "file": manifest,
                   "text": f"{manifest}" + (f": {', '.join(found[:6])}" if found else "") + (" · has a command line" if has_cli else "")})
    for dep, (label_, role_) in SERVICE_DEPS.items():
        if dep in deps:
            svc = d.service(label_, role_, {"kind": "manifest", "file": manifest, "text": f"{dep} in {manifest}"})
            d.edge(p["id"], svc["id"], "stores in" if role_ == "data" else "uses", {"kind": "manifest", "file": manifest, "text": f"{dep} in {manifest}"})
    return p


def read_manifests(d: Draft) -> None:
    root = d.root
    found = _scan(root)
    compose: list[tuple[str, Path]] = []
    vites: list[tuple[str, Path]] = []
    envs: list[tuple[str, Path]] = []
    tfs: dict[str, list[Path]] = defaultdict(list)
    for rel, files in sorted(found.items()):
        if rel == ".github/workflows":
            continue
        by = {f.name: f for f in files}
        man = lambda name, rel=rel: f"{rel}/{name}" if rel else name  # noqa: E731
        p = None
        if "package.json" in by:
            try:
                pkg = json.loads(_read(by["package.json"]) or "{}")
            except ValueError:
                pkg = {}
            if isinstance(pkg, dict):
                deps = {k.lower(): "" for sec in ("dependencies", "devDependencies", "peerDependencies")
                        for k in (pkg.get(sec) or {}) if isinstance(pkg.get(sec), dict)}
                lang = "TypeScript" if "typescript" in deps else "JavaScript"
                ext = ("extension",) if isinstance(pkg.get("engines"), dict) and "vscode" in pkg["engines"] else ()
                p = _code_part(d, rel, deps, lang=lang, name=pkg.get("name"), has_cli=bool(pkg.get("bin")),
                               manifest=man("package.json"), extra_kinds=ext)
        if "pyproject.toml" in by or "requirements.txt" in by:
            deps: dict[str, str] = {}
            name, has_cli = None, False
            if "pyproject.toml" in by:
                try:
                    py = tomllib.loads(_read(by["pyproject.toml"]))
                except tomllib.TOMLDecodeError:
                    py = {}
                proj = py.get("project") or {}
                name = proj.get("name")
                specs = list(proj.get("dependencies") or [])
                for group in list((proj.get("optional-dependencies") or {}).values()) + list((py.get("dependency-groups") or {}).values()):
                    specs += [s for s in group if isinstance(s, str)]
                poetry = (py.get("tool") or {}).get("poetry") or {}
                specs += list((poetry.get("dependencies") or {}).keys())
                deps = {_dep_name(s): "" for s in specs if isinstance(s, str)}
                has_cli = bool(proj.get("scripts") or poetry.get("scripts"))
            if "requirements.txt" in by and not deps:
                deps = {_dep_name(ln): "" for ln in _read(by["requirements.txt"]).splitlines()
                        if ln.strip() and not ln.lstrip().startswith(("#", "-"))}
            if p is None or deps:
                p = _code_part(d, rel, deps, lang="Python", name=name, has_cli=has_cli,
                               manifest=man("pyproject.toml" if "pyproject.toml" in by else "requirements.txt"))
        if "Cargo.toml" in by and p is None:
            try:
                cargo = tomllib.loads(_read(by["Cargo.toml"]))
            except tomllib.TOMLDecodeError:
                cargo = {}
            if cargo.get("package"):
                deps = {k.lower(): "" for k in (cargo.get("dependencies") or {})}
                p = _code_part(d, rel, deps, lang="Rust", name=(cargo.get("package") or {}).get("name"),
                               has_cli=bool(cargo.get("bin")) or "clap" in deps, manifest=man("Cargo.toml"))
        if "go.mod" in by and p is None:
            text = _read(by["go.mod"])
            deps = {m.lower(): "" for m in re.findall(r"^\s*(?:require\s+)?([\w.-]+\.[\w./-]+)\s+v", text, re.M)}
            mod = re.search(r"^module\s+(\S+)", text, re.M)
            p = _code_part(d, rel, deps, lang="Go", name=mod.group(1) if mod else None,
                           has_cli="github.com/spf13/cobra" in deps, manifest=man("go.mod"))
        docker = [f for f in files if f.name == "Dockerfile" or f.name.startswith("Dockerfile.")]
        if docker:
            target = p or d.folder_part(rel) if rel else p
            if target is None and rel not in ("deploy", "docker", ".devcontainer"):
                target = d.part(f"dir:{rel}", rel.rsplit("/", 1)[-1] if rel else d.label, "service", "code", folder=rel)
            if target is None:  # deploy/Dockerfile with no package at the root: the app's image, built from the repo
                target = d.part("image:", f"{d.label} image", "image", "delivery")
            target = target or d.folder_part("") or d.part("dir:", d.label, "service", "code", folder="")
            d.stack(target, "Docker")
            if target["kind"] == "library":  # a package that ships as a container runs as a service
                target["kind"] = "service"
            target.setdefault("dockerfiles", []).append(man(docker[0].name))
            for port in re.findall(r"^\s*EXPOSE\s+(\d{4,5})", _read(docker[0]), re.M):
                target["ports"][int(port)] += 1
            d.evidence(target, {"kind": "manifest", "file": man(docker[0].name), "text": f"built as a container image ({man(docker[0].name)})"})
        if "host.json" in by and p is not None:
            d.stack(p, "Azure Functions")
            fn = d.part(f"host:fn:{rel or d.label}", f"{p['label']} functions", "deployed", "runtime", platform="Azure")
            d.evidence(fn, {"kind": "manifest", "file": man("host.json"), "text": f"an Azure Functions app ({man('host.json')})"})
            d.edge(p["id"], fn["id"], "deployed as")
        for fname, (label, plat) in {"firebase.json": ("Firebase Hosting", "Firebase"), "wrangler.toml": ("Cloudflare Worker", "Cloudflare"),
                                     "databricks.yml": ("Databricks bundle", "Databricks"), "vercel.json": ("Vercel", "Vercel"),
                                     "fly.toml": ("Fly.io app", "Fly.io"), "render.yaml": ("Render", "Render"),
                                     "app.yaml": ("Databricks App" if "command:" in _read(by.get("app.yaml", root / "-")) else "App Engine",
                                                  "Databricks" if "command:" in _read(by.get("app.yaml", root / "-")) else "Google Cloud")}.items():
            if fname in by:
                tgt = d.part(f"deploy:{label.lower()}", label, "deployed", "runtime", platform=plat)
                d.evidence(tgt, {"kind": "manifest", "file": man(fname), "text": f"deploy config {man(fname)}"})
                src = p or d.folder_part(rel) or d.folder_part("")
                if src:
                    d.edge(src["id"], tgt["id"], "deploys to", {"kind": "manifest", "file": man(fname), "text": man(fname)})
        for f in files:
            if f.name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
                compose.append((rel, f))
            elif f.name.startswith("vite.config."):
                vites.append((rel, f))
            elif f.name in (".env.example", ".env.sample"):
                envs.append((rel, f))
            elif f.suffix == ".tf":
                tfs[rel].append(f)
    for rel, f in compose:
        _read_compose(d, rel, f)
    for rel, f in vites:
        _read_vite(d, rel, f)
    for rel, f in envs:
        _read_env(d, rel, f)
    for rel, files in tfs.items():
        _read_terraform(d, rel, files)
    if found.get(".github/workflows"):
        _read_workflows(d, found[".github/workflows"])


def _read_compose(d: Draft, rel: str, f: Path) -> None:
    data = _yaml_lite(_read(f))
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return
    man = f"{rel}/{f.name}" if rel else f.name
    ids: dict[str, str] = {}
    for name, svc in services.items():
        svc = svc if isinstance(svc, dict) else {}
        image = str(svc.get("image") or "")
        build = svc.get("build")
        ctx = build.get("context") if isinstance(build, dict) else build
        dockerfile = build.get("dockerfile") if isinstance(build, dict) else None
        store = next((v for k, v in STORE_IMAGES.items() if re.search(rf"(^|/){k}\b", image.lower())), None)
        p = None
        if ctx:  # built from a folder: the folder of its Dockerfile when the context is the repo root
            src = Path(str(ctx)) / str(dockerfile) if dockerfile and "/" in str(dockerfile) else Path(str(ctx)) / "Dockerfile"
            folder = os.path.normpath((Path(rel) / src).parent.as_posix())
            folder = "" if folder in (".", "") or folder.startswith("..") else folder
            p = d.parts.get(f"dir:{folder}") or d.part(f"dir:{folder}", name if folder else d.label, "service", "code", folder=folder)
        elif store:
            p = d.part(f"svc:{store.lower()}", store, "store", "data")
            p["label"] = store
        else:
            p = d.part(f"compose:{name}", name, "service", "code")
            d.stack(p, image.split(":")[0].rsplit("/", 1)[-1] if image else None)
        ids[name] = p["id"]
        d.stack(p, "Docker")
        d.evidence(p, {"kind": "manifest", "file": man, "text": f"service “{name}” in {man}" + (f" ({image})" if image else "")})
        for spec in svc.get("ports") or []:
            m = re.match(r"^(?:[\d.]+:)?(\d{2,5})(?::\d{2,5})?", str(spec).strip("'\""))
            if m:
                p["ports"][int(m.group(1))] += 1
    for name, svc in services.items():
        deps = svc.get("depends_on") if isinstance(svc, dict) else None
        for dep in (deps if isinstance(deps, list) else list(deps or {})):
            if name in ids and dep in ids:
                d.edge(ids[name], ids[dep], "depends on", {"kind": "manifest", "file": man, "text": f"{name} depends_on {dep} in {man}"}, kind="depends")
        env = svc.get("environment") if isinstance(svc, dict) else None
        pairs = env.items() if isinstance(env, dict) else (str(x).split("=", 1) for x in env or [] if "=" in str(x))
        for key, value in pairs:  # http://registry:8080 in a service's environment: it calls that service
            for other in re.findall(r"://([\w-]+):\d{2,5}", str(value or "")):
                if other in ids and other != name and name in ids:
                    d.edge(ids[name], ids[other], "calls", {"kind": "manifest", "file": man, "text": f"{name}: {key}={one_line(str(value), 80)} in {man}"}, kind="calls")


def _owner_of(d: Draft, port: int) -> dict | None:
    owners = [p for p in d.parts.values() if p["ports"].get(port)]
    return max(owners, key=lambda p: (p["ports"][port], p["kind"] == "api" or "api" in p.get("also", []),
                                      len(p.get("folder") or ""))) if owners else None


def _read_vite(d: Draft, rel: str, f: Path) -> None:
    text = _read(f)
    man = f"{rel}/{f.name}" if rel else f.name
    ui = d.folder_part(rel) or d.part(f"dir:{rel}", rel.rsplit("/", 1)[-1] if rel else d.label, "ui", "way_in", folder=rel)
    m = re.search(r"\bserver\s*:\s*\{[^}]*?\bport\s*:\s*(\d{4,5})", text, re.S)
    if m:
        ui["ports"][int(m.group(1))] += 1
    for m in re.finditer(r"['\"](/[\w/-]*)['\"]\s*:", text):  # '/api': { target: env ?? 'http://127.0.0.1:8011' }
        port = re.search(r"https?://[\w.\[\]-]*:(\d{4,5})", text[m.end():m.end() + 240].split("}")[0])
        if port:
            ui.setdefault("proxies", []).append({"path": m.group(1), "port": int(port.group(1)), "file": man})


def _read_env(d: Draft, rel: str, f: Path) -> None:
    man = f"{rel}/{f.name}" if rel else f.name
    for key, scheme, port in re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*['\"]?(\w+)://[^\s'\"]*?:(\d{4,5})", _read(f), re.M):
        p = d.folder_part(rel)
        if p is None:
            continue
        if scheme.startswith("postgres"):
            svc = d.service("PostgreSQL", "data", {"kind": "manifest", "file": man, "text": f"{key} in {man}"})
            d.edge(p["id"], svc["id"], "stores in", {"kind": "manifest", "file": man, "text": f"{key} in {man}"})
        elif scheme.startswith("http"):
            p.setdefault("proxies", []).append({"path": key, "port": int(port), "file": man})


TF_SKIP = re.compile(r"^(random|null|local|time|tls|terraform|azuread|external)_|resource_group$|_(rule|rules|association|assignment|"
                     r"policy|setting|settings|secret|key|version|lock|role|member|binding|attachment|membership|config|"
                     r"configuration|link|record|permission|identity|access_policy|diagnostic_setting|consumer_group|"
                     r"authorization_rule|zone|subnet|ip|endpoint_connection|credential|certificate|token|password|slot|administrator|server_database)$"
                     r"|^azurerm_(service_plan|app_service_plan|storage_container|storage_share|storage_queue|storage_table)$")
TF_LABELS = {"linux_web_app": "App Service", "windows_web_app": "App Service", "app_service": "App Service",
             "service_plan": "App Service plan", "app_service_plan": "App Service plan",
             "linux_function_app": "Function App", "function_app": "Function App", "container_app": "Container App",
             "container_registry": "Container registry", "postgresql_flexible_server": "PostgreSQL",
             "postgresql_server": "PostgreSQL", "mssql_server": "SQL Server", "cosmosdb_account": "Cosmos DB",
             "storage_account": "Storage account", "key_vault": "Key Vault", "cognitive_account": "AI Services",
             "application_insights": "Application Insights", "log_analytics_workspace": "Log Analytics",
             "redis_cache": "Redis", "kubernetes_cluster": "Kubernetes", "static_web_app": "Static Web App",
             "code_engine_project": "Code Engine", "code_engine_app": "Code Engine app", "code_engine_job": "Code Engine job",
             "is_instance": "Virtual server", "linux_virtual_machine": "Virtual machine", "cos_bucket": "Object Storage",
             "databricks_workspace": "Databricks workspace", "postgresql_flexible_server_database": "PostgreSQL database"}
TF_PROVIDERS = {"azurerm": "Azure", "azuredevops": "Azure DevOps", "ibm": "IBM Cloud", "google": "Google Cloud",
                "aws": "AWS", "databricks": "Databricks", "cloudflare": "Cloudflare", "github": "GitHub", "docker": "Docker"}
DATA_TYPES = re.compile(r"postgres|sql|cosmos|storage_account|redis|bucket|database|mongo")


def _read_terraform(d: Draft, rel: str, files: list[Path]) -> None:
    infra = d.part(f"dir:{rel}", f"{rel.rsplit('/', 1)[-1]} (Terraform)" if rel else "Terraform", "infra", "delivery", folder=rel or None)
    d.stack(infra, "Terraform")
    for f in files:
        man = f"{rel}/{f.name}" if rel else f.name
        text = _read(f)
        for m in re.finditer(r'^resource\s+"([\w-]+)"\s+"([\w-]+)"\s*\{', text, re.M):
            rtype, rname = m.group(1), m.group(2)
            provider, _, short = rtype.partition("_")
            if TF_SKIP.search(rtype) or provider not in TF_PROVIDERS or not short:
                continue
            body = text[m.end():m.end() + 1200]
            lit = re.search(r'^\s*name\s*=\s*"([^"$]+)"', body, re.M)
            label = TF_LABELS.get(short, short.replace("_", " ").capitalize())
            role = "data" if DATA_TYPES.search(short) else "runtime"
            p = d.part(f"tf:{rtype}.{rname}", label, "store" if role == "data" else "deployed", role,
                       platform=TF_PROVIDERS[provider], resource=f"{rtype}.{rname}", name=lit.group(1) if lit else rname)
            d.evidence(p, {"kind": "manifest", "file": man, "text": f'resource "{rtype}" "{rname}" in {man}'})
            d.edge(infra["id"], p["id"], "provisions", kind="provisions")
    d.evidence(infra, {"kind": "manifest", "file": rel or ".", "text": f"{len(files)} Terraform file{'s' if len(files) != 1 else ''} in {rel or 'the root'}"})


WORKFLOW_TARGETS = [("PyPI", r"pypa/gh-action-pypi-publish|twine upload|uv publish"),
                    ("Firebase Hosting", r"FirebaseExtended/action-hosting-deploy|firebase deploy"),
                    ("GitHub Pages", r"actions/deploy-pages|peaceiris/actions-gh-pages"),
                    ("GitHub Releases", r"softprops/action-gh-release|gh release (create|upload)"),
                    ("Azure Web App", r"azure/webapps-deploy"), ("Azure Functions", r"Azure/functions-action"),
                    ("Azure Static Web Apps", r"Azure/static-web-apps-deploy"),
                    ("Container registry", r"docker/build-push-action|docker push"), ("npm", r"npm publish"),
                    ("VS Code Marketplace", r"vsce publish"), ("Cloudflare", r"cloudflare/wrangler-action|wrangler deploy"),
                    ("Vercel", r"vercel deploy|amondnet/vercel-action"), ("Databricks", r"databricks bundle deploy")]


WORKFLOW_PLATFORM = {"Firebase Hosting": "Firebase", "GitHub Pages": "GitHub", "GitHub Releases": "GitHub", "Azure Web App": "Azure",
                     "Azure Functions": "Azure", "Azure Static Web Apps": "Azure", "Cloudflare": "Cloudflare", "Vercel": "Vercel",
                     "Databricks": "Databricks"}


def _read_workflows(d: Draft, files: list[Path]) -> None:
    ci = d.part("ci:github", "GitHub Actions", "ci", "delivery")
    for f in files:
        text = _read(f)
        name = re.search(r"^name:\s*['\"]?(.+?)['\"]?\s*$", text, re.M)
        man = f".github/workflows/{f.name}"
        d.evidence(ci, {"kind": "manifest", "file": man, "text": f"workflow “{name.group(1) if name else f.stem}” ({man})"})
        for label, pat in WORKFLOW_TARGETS:
            if re.search(pat, text, re.I):
                tgt = d.part(f"deploy:{label.lower()}", label, "deployed", "runtime", platform=WORKFLOW_PLATFORM.get(label))
                d.evidence(tgt, {"kind": "manifest", "file": man, "text": f"{man} publishes here"})
                d.edge(ci["id"], tgt["id"], "deploys to", {"kind": "manifest", "file": man, "text": man}, kind="deploys")


# =====================================================================================
# what sessions did
# =====================================================================================
def _segments(cmd: str) -> list[str]:
    """Split a shell command into its simple commands (on ; && || | & and newlines), outside quotes."""
    out, cur, q, i = [], [], None, 0
    while i < len(cmd):
        c = cmd[i]
        if q:
            cur.append(c)
            if c == q:
                q = None
            elif c == "\\" and q == '"' and i + 1 < len(cmd):
                cur.append(cmd[i + 1])
                i += 1
        elif c in "'\"":
            q = c
            cur.append(c)
        elif c in ";|&\n":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]


def _verb(seg: str) -> tuple[str, str]:
    s = ENV_PREFIX.sub("", seg.lstrip("( ")).strip()
    while True:
        m = re.match(r"^(sudo|time|nohup|exec|command|env)\s+", s)
        if not m:
            break
        s = ENV_PREFIX.sub("", s[m.end():])
    word = s.split(None, 1)[0] if s else ""
    return word.rsplit("/", 1)[-1].lower(), s


def _ssh_target(verb: str, rest: str) -> str | None:
    args = rest.split()[1:]
    if verb == "ssh":
        i = 0
        while i < len(args):
            a = args[i]
            if a.startswith("-"):
                i += 2 if a in ("-i", "-o", "-p", "-F", "-l", "-J", "-L", "-R", "-D", "-b", "-c", "-e", "-m", "-O", "-S", "-W", "-w", "-E", "-B", "-I", "-Q") else 1
                continue
            host = a.split("@", 1)[-1]
            return host if re.match(r"^[\w.-]+$", host) and host not in ("localhost", "127.0.0.1") else None
        return None
    for a in args:  # scp / rsync: the side with host:path
        m = re.match(r"^(?:[\w.-]+@)?([\w.-]+):", a)
        if m and not a.startswith("-") and not re.match(r"^[A-Za-z]:\\", a):
            return m.group(1)
    return None


def _host_info(host: str) -> tuple[str, str, str]:
    host = host.lower()
    for suffix, platform, what in HOSTS:
        if host.endswith(suffix):
            return host[: -len(suffix)].split(".")[0], platform, what
    return host, "", ""


def _ev_command(cmd: str, sid: str, ts: str | None) -> dict:
    return {"kind": "command", "text": one_line(redact(cmd), 220), "session": sid, "ts": ts}


def _add_count(p: dict, key: str, sid: str, ev: dict, n_examples: int = 2, reach: bool = True) -> None:
    """Count one more sighting of `key` on a part, keeping a couple of example commands. `reach`: the command went
    there (curl, a cloud CLI, ssh), rather than only naming it (a test's fake URL, a grep)."""
    c = p.setdefault("seen", {}).setdefault(key, {"n": 0, "sessions": set(), "examples": [], "reach": False})
    c["reach"] = c.get("reach", False) or reach
    c["n"] += 1
    c["sessions"].add(sid)
    if len(c["examples"]) < n_examples and all(e["text"] != ev["text"] for e in c["examples"]):
        c["examples"].append(ev)


REACH_VERBS = {"curl", "wget", "http", "xh", "open", "ping", "dig", "nslookup", "nc", "ssh", "scp", "rsync", "npx", "playwright",
               "docker", "gh", "git"}
PLACEHOLDER_NAMES = {"example", "test", "demo", "foo", "bar", "your", "myapp", "my-app", "app", "localhost", "xxx", "placeholder"}
LITERALS = ("--port", "--http-port", "--server.port", "PORT=", "http.server", "--bind", " -p ", "localhost:", "127.0.0.1:",
            "0.0.0.0:", "[::1]:", *(s[1:] for s, _, _ in HOSTS))
CLI_WORD = re.compile(r"(?<![\w.-])(?:" + "|".join(sorted(CLIS)) + r"|ssh|scp|rsync)\s")
SCAN_CHARS = 4000  # servers, hosts and CLIs show in a command's head; the rest of a long one is a heredoc's file


def _interesting(text: str) -> bool:
    return any(x in text for x in LITERALS) or bool(CLI_WORD.search(text))


OUTSIDE: dict = {}


def _part_at(d: Draft, real_root: Path | None, cwd: Path | None, rest: str) -> dict | None:
    """The part a command ran in: its cd, a --directory/-C flag, or the first project folder in its arguments."""
    if real_root is None:
        return None
    for p in d.parts.values():  # cargo run -p x, uv run --package x, pnpm --filter x: that package's part
        pkg = p.get("package")
        if pkg and len(pkg) >= 4 and re.search(rf"(?:-p|--package|--filter|-F)[ =]['\"]?{re.escape(pkg)}\b", rest):
            return p
    cue = cwd
    m = DIR_FLAG.search(rest)
    if m and not m.group(1).startswith(("-", "$")):
        cue = (cue or real_root) / m.group(1).strip("'\"")
    if cue is None:  # a path in the arguments: backend/run.py, frontend/
        args = rest.split(None, 1)
        m = re.search(r"(?:^|\s)\.?/?([\w.-]+)/[\w./-]*", args[1] if len(args) > 1 else "")
        if m and (real_root / m.group(1)).is_dir():
            cue = real_root / m.group(1)
    if cue is None:  # no cd: agents run commands in the project's root
        return d.parts.get("dir:")
    if not cue.is_absolute():
        return None
    try:
        rel = cue.resolve().relative_to(real_root).as_posix()
    except ValueError:
        return OUTSIDE
    except OSError:
        return None
    return d.folder_part("" if rel == "." else rel)


def read_commands(d: Draft, rows: list[tuple[str, str | None, str]]) -> None:
    """What the commands sessions ran say about this system: the servers its parts run on, where it is deployed,
    which clouds, machines and databases it reaches."""
    root = d.root
    real_root = root.resolve() if root is not None else None
    for sid, ts, cmd in rows:
        cmd = cmd[:SCAN_CHARS]
        if not _interesting(cmd):
            continue
        cwd: Path | None = None
        for seg in _segments(cmd):
            verb, rest = _verb(seg)
            if verb == "cd":
                arg = rest.split(None, 1)[1].strip().strip("'\"") if len(rest.split(None, 1)) > 1 else ""
                if arg and not arg.startswith(("$", "-", "~")):
                    target = Path(arg) if arg.startswith("/") else ((cwd or root) / arg if root else None)
                    cwd = target
                continue
            if not _interesting(seg):
                continue
            part = _part_at(d, real_root, cwd, rest)
            if part is OUTSIDE:  # cd'd into another project: what runs there is that project's
                continue
            ev = _ev_command(seg, sid, ts)
            # servers it started
            if verb not in NOT_SERVERS:
                for m in PORT_FLAG.finditer(" " + seg):
                    port = int(next(g for g in m.groups() if g))
                    owner = part
                    hint = next((v for k, v in SERVER_HINTS.items() if re.search(rf"\b{k}\b", seg)), None)
                    if hint is None and verb in ("pnpm", "npm", "yarn", "bun", "npx") and re.search(r"\b(dev|preview|start|serve)\b", seg):
                        hint = "ui"
                    if (owner is None or owner.get("folder") == "") and hint:
                        cands = [p for p in d.parts.values() if p["kind"] == hint or hint in p.get("also", [])]
                        owner = cands[0] if len(cands) == 1 else owner
                    if owner is None:
                        d.ports_unplaced[port] += 1
                    else:
                        owner["ports"][port] += 1
                        _add_count(owner, f"port:{port}", sid, ev)
            for m in LOCAL_URL.finditer(seg):
                if verb in ("curl", "wget", "http", "xh", "open") or "fetch(" in seg or "requests." in seg:
                    owner = _owner_of(d, int(m.group(1)))
                    if owner:
                        path = (m.group(2) or "/").split("?")[0]
                        owner["calls"][("/" + path.strip("/").split("/")[0]) if path.strip("/") else "/"] += 1
            # where it is deployed
            for host in {h.lower() for h in HOST_RE.findall(seg)}:
                name, platform, what = _host_info(host)
                if not platform:
                    continue
                if len(name) < 3 or name in PLACEHOLDER_NAMES:
                    continue
                kind = "store" if what in DATA_HOSTS else "external" if what in SERVICE_HOSTS else "deployed"
                p = d.part(f"host:{platform}:{name}", name, kind, "data" if kind == "store" else "runtime", platform=platform, what=what)
                _add_count(p, "host", sid, ev, reach=verb in REACH_VERBS or verb in CLIS)
            # clouds, machines and databases it reaches
            if verb in CLIS:
                platform, role = CLIS[verb]
                if verb == "psql":
                    m = re.search(r"(?:-p\s*|:)(\d{4,5})\b", seg)
                    svc = d.part("svc:postgresql", "PostgreSQL", "store", "data")
                    if m:
                        svc["ports"][int(m.group(1))] += 0
                    _add_count(svc, f"cli:{verb}", sid, ev)
                    continue
                if verb == "duckdb":
                    m = re.search(r"([\w./-]+\.(?:duckdb|db))\b", seg)
                    svc = d.part(f"file:{m.group(1).rsplit('/', 1)[-1]}" if m else "svc:duckdb",
                                 m.group(1).rsplit("/", 1)[-1] if m else "DuckDB", "store", "data")
                    d.stack(svc, "DuckDB")
                    _add_count(svc, f"cli:{verb}", sid, ev)
                    continue
                if verb == "terraform":
                    infra = next((p for p in d.parts.values() if p["kind"] == "infra"), None) or d.part("infra:terraform", "Terraform", "infra", "delivery")
                    _add_count(infra, f"cli:{verb}", sid, ev)
                    continue
                if verb == "launchctl" and "com." not in seg:
                    continue
                p = d.part(f"platform:{platform}", platform, "store" if role == "data" else "platform", role, platform=platform)
                _add_count(p, f"cli:{verb}", sid, ev)
            elif verb in ("ssh", "scp", "rsync"):
                host = _ssh_target(verb, rest)
                if host and not host.endswith(("github.com", "github.ibm.com", "gitlab.com")):
                    p = d.part(f"server:{host}", host, "server", "runtime")
                    _add_count(p, f"cli:{verb}", sid, ev)


def _merge(d: Draft, keep: dict, drop: dict) -> None:
    """Fold `drop` into `keep`: evidence, sightings, ports, stack and every edge that touched it."""
    for ev in drop["evidence"]:
        d.evidence(keep, ev)
    for key, c in drop.get("seen", {}).items():
        tc = keep.setdefault("seen", {}).setdefault(key, {"n": 0, "sessions": set(), "examples": [], "reach": c.get("reach", True)})
        tc["n"] += c["n"]
        tc["sessions"] |= c["sessions"]
        tc["examples"] = (tc["examples"] + c["examples"])[:3]
    keep["ports"].update(drop["ports"])
    d.stack(keep, *drop["stack"])
    for k in ("resource", "name"):
        if drop.get(k) and not keep.get(k):
            keep[k] = drop[k]
    for (a, b), e in list(d.edges.items()):
        if drop["id"] in (a, b):
            del d.edges[(a, b)]
            na, nb = keep["id"] if a == drop["id"] else a, keep["id"] if b == drop["id"] else b
            if na != nb:
                ne = d.edges.setdefault((na, nb), {**e, "from": na, "to": nb, "evidence": []})
                ne["evidence"] += [x for x in e["evidence"] if x not in ne["evidence"]]
    del d.parts[drop["id"]]


def _merge_duplicates(d: Draft) -> None:
    """One node per real thing: the App Service Terraform declares and the one commands reached; the PostgreSQL a
    dependency names and the one Terraform provisions."""
    tf = [p for p in d.parts.values() if p["id"].startswith("tf:")]
    hosts = [p for p in d.parts.values() if p["id"].startswith("host:")]
    for t in tf:
        same = [h for h in hosts if h.get("platform") == t.get("platform") and h.get("what") == t["label"] and h["id"] in d.parts]
        if len(same) == 1 and sum(1 for x in tf if x["label"] == t["label"]) == 1:
            _merge(d, same[0], t)
    by_label: dict[str, list[dict]] = defaultdict(list)
    for p in list(d.parts.values()):
        if p["role"] == "data" and p["kind"] == "store" and not p["id"].startswith(("file:", "dir:", "compose:")):
            by_label[p["label"]].append(p)
    for same in by_label.values():
        if len(same) > 1:  # keep the most specific: Terraform's or a host, then the dependency's
            same.sort(key=lambda p: (not p["id"].startswith("tf:"), not p["id"].startswith("host:")))
            for drop in same[1:]:
                _merge(d, same[0], drop)
    runtime: dict[str, list[dict]] = defaultdict(list)  # "Databricks" from the CLI, a dependency and a deploy target: one
    for p in list(d.parts.values()):
        if p["role"] == "runtime" and not p["id"].startswith(("host:", "tf:", "server:")):
            runtime[p["label"].lower()].append(p)
    rank = {"deployed": 0, "external": 1, "platform": 2}
    for same in runtime.values():
        if len(same) > 1:
            same.sort(key=lambda p: rank.get(p["kind"], 3))
            for drop in same[1:]:
                _merge(d, same[0], drop)


def _connect_runtime(d: Draft) -> None:
    """Edges no single manifest states: the UI to the API it proxies to, the code to where it is deployed, the
    servers and clouds it runs on."""
    parts = d.parts
    code = [p for p in parts.values() if p["role"] in ("way_in", "code") and p["kind"] not in ("library", "extension")]
    for p in list(parts.values()):
        for prox in p.get("proxies", []):
            owner = _owner_of(d, prox["port"])
            if owner is None:
                apis = [q for q in parts.values() if q["kind"] == "api" or "api" in q.get("also", [])]
                owner = apis[0] if len(apis) == 1 else None
            if owner is not None and owner is not p:
                d.edge(p["id"], owner["id"], f"calls {prox['path']}" if prox["path"].startswith("/") else "calls",
                       {"kind": "manifest", "file": prox["file"], "text": f"{prox['path']} → :{prox['port']} in {prox['file']}"}, kind="calls")
    deployable = [p for p in code if p.get("dockerfiles")] or [p for p in code if p["kind"] in ("api", "ui", "service")] or code
    root_part = parts.get("dir:")
    images = [p for p in parts.values() if p["kind"] == "image"]
    if len(images) == 1:  # the repo's one container image is what runs where it is deployed
        for p in list(parts.values()):
            if p["kind"] == "deployed" and p.get("what") in ("App Service", "Container App", "Code Engine", "Cloud Run", "App", "Service"):
                d.edge(images[0]["id"], p["id"], "deployed as", kind="deploys")
    for p in list(parts.values()):
        # only when one part can be meant; otherwise the node stands on its own evidence, unconnected
        src = deployable[0] if len(deployable) == 1 else root_part
        if src is None or any(e["to"] == p["id"] for e in d.edges.values()):
            continue
        if p["kind"] in ("deployed", "server"):
            d.edge(src["id"], p["id"], "runs on" if p["kind"] == "server" else "deployed as", kind="deploys")
        elif p["kind"] in ("external", "store") and p["id"].startswith("host:"):
            d.edge(src["id"], p["id"], "stores in" if p["kind"] == "store" else "uses", kind="uses")
        elif p["kind"] == "platform":
            d.edge(src["id"], p["id"], "uses", kind="uses")


def _fallback_parts(d: Draft, folder_activity: Counter) -> None:
    """No manifest describes the code: the top folders sessions edited stand in for its parts."""
    if any(p["role"] in ("way_in", "code") for p in d.parts.values()):
        return
    tops = [f for f, _ in folder_activity.most_common() if f and f not in NON_CODE_DIRS and not f.startswith(".")][:6]
    for f in tops:
        d.part(f"dir:{f}", f, "component", "code", folder=f)
    if not tops:
        d.part("dir:", d.label, "component", "code", folder="")


# =====================================================================================
# the whole map
# =====================================================================================
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


GENERIC_NAMES = {"sandbox", "projects", "personal", "docs", "app", "api", "backend", "frontend", "web", "server", "client",
                 "test", "tests", "demo", "work", "main", "core", "agent", "agents", "worker", "home", "src", "data"}


def build(conn: sqlite3.Connection, cfg, *, read_manifests_: bool | None = None) -> dict:
    """The Systems map: groups, systems with their parts, and the links between systems, with the evidence for each."""
    t0 = time.time()
    home = Path.home()
    read_files = cfg.systems_read_manifests if read_manifests_ is None else read_manifests_
    projects_root = home / "Projects"
    rows = conn.execute(
        f"SELECT project_path, COUNT(*) n, MIN(started_at) first, MAX(started_at) last, SUM(active_s) active_s "
        f"FROM sessions WHERE source NOT IN ({','.join('?' * len(CHAT_SOURCES))}) AND project_path IS NOT NULL "
        f"AND project_path != '' GROUP BY project_path", CHAT_SOURCES).fetchall()
    stats = {r["project_path"]: dict(r) for r in rows if not cfg.is_excluded(r["project_path"])}

    # 1. which system (or group) each project folder belongs to
    local, remote, named = {}, {}, {}
    for p in stats:
        if re.match(r"^[\w.-]+:/", p):
            remote[p] = p.split(":", 1)
        elif p.startswith("/"):
            local[p] = Path(p)
        else:
            named[p] = p
    key_of: dict[str, str] = {}
    is_repo: dict[str, bool] = {}
    for p, path in local.items():
        rel_home = path.relative_to(home).as_posix() if path.is_relative_to(home) else None
        if rel_home is not None and (rel_home in NOT_PROJECTS or rel_home == "."):
            key_of[p] = "loose:" + str(path)
            continue
        root = repo_root(path, home) if path.exists() else None
        key = str(root) if root else p
        key_of[p] = key
        is_repo[key] = is_repo.get(key, False) or bool(root)
    keys = sorted({k for k in key_of.values() if not k.startswith("loose:")})
    group_keys = {k for k in keys if not is_repo.get(k) and any(o != k and o.startswith(k + "/") for o in keys)}
    for p, k in list(key_of.items()):
        if k in group_keys:
            key_of[p] = "loose:" + k
    sys_keys = [k for k in keys if k not in group_keys]

    # 2. one draft per system, with what its manifests say
    drafts: dict[str, Draft] = {}
    meta: dict[str, dict] = {}
    for k in sys_keys:
        root = Path(k)
        label = root.name
        d = drafts[k] = Draft(root if root.exists() else None, label)
        meta[k] = {"paths": [], "remote_runs": [], "git": git_remote(root) if root.exists() else None, "on_disk": root.exists()}
        if read_files and root.exists():
            try:
                read_manifests(d)
            except (OSError, ValueError, RecursionError):
                pass
    labels = Counter(d.label for d in drafts.values())
    for k, d in drafts.items():
        if labels[d.label] > 1:
            d.label = f"{Path(k).parent.name}/{d.label}"
        for p in d.parts.values():
            if p["id"] == "dir:":
                p["label"] = d.label
    for p, k in key_of.items():
        if k in meta:
            meta[k]["paths"].append(p)
    by_name: dict[str, str] = {}
    for k in drafts:
        for n in {Path(k).name, (meta[k]["git"] or {}).get("repo", "").rsplit("/", 1)[-1]}:
            if n:
                by_name.setdefault(_norm(n), k)
    for p, (host, path) in remote.items():  # a project run on another machine: the same code, deployed there
        k = by_name.get(_norm(Path(path).name))
        if k:
            meta[k]["remote_runs"].append({"host": host, "path": path, "sessions": stats[p]["n"]})
            key_of[p] = k
            d = drafts[k]
            srv = d.part(f"server:{host}", host, "server", "runtime")
            d.evidence(srv, {"kind": "remote", "text": f"{stats[p]['n']} session{'s' if stats[p]['n'] != 1 else ''} ran in {p}"})
        else:
            key_of[p] = p
            drafts[p] = Draft(None, f"{Path(path).name} @ {host}")
            meta[p] = {"paths": [p], "remote_runs": [], "git": None, "on_disk": False}
            sys_keys.append(p)
    for p in named:  # a cloud task names a repo, not a folder
        k = by_name.get(_norm(p.rsplit("/", 1)[-1]))
        if k:
            key_of[p] = k
            meta[k]["paths"].append(p)
        else:
            key_of[p] = p
            drafts[p] = Draft(None, p)
            meta[p] = {"paths": [p], "remote_runs": [], "git": None, "on_disk": False}
            sys_keys.append(p)

    # 3. sessions: who worked where, and what they ran
    sess_sys: dict[str, str] = {}
    agents: dict[str, Counter] = defaultdict(Counter)
    for r in conn.execute(f"SELECT id, project_path, agent, started_at FROM sessions WHERE source NOT IN "
                          f"({','.join('?' * len(CHAT_SOURCES))})", CHAT_SOURCES):
        k = key_of.get(r["project_path"] or "")
        if k:
            sess_sys[r["id"]] = k
            agents[k][r["agent"] or "claude"] += 1
    started = {r[0]: r[1] for r in conn.execute("SELECT id, started_at FROM sessions")}
    cmds: dict[str, list] = defaultdict(list)
    for sid, ts, cmd in conn.execute("SELECT session_id, ts, command FROM tool_calls WHERE command IS NOT NULL AND command != ''"):
        k = sess_sys.get(sid)
        if k in drafts:
            cmds[k].append((sid, ts, cmd))
    for k, rows_ in cmds.items():
        read_commands(drafts[k], rows_)

    # 4. files: activity per part, and files touched in another system
    roots = sorted(((k, Path(k)) for k in drafts if k.startswith("/")), key=lambda x: -len(str(x[1])))
    activity: dict[str, Counter] = defaultdict(Counter)
    cross: dict[tuple[str, str], dict] = {}
    for sid, path, edits, writes in conn.execute("SELECT session_id, path, edits, writes FROM session_files"):
        k = sess_sys.get(sid)
        if not k or not path or not path.startswith("/"):
            continue
        owner = next((rk for rk, rp in roots if path.startswith(str(rp) + "/")), None)
        if owner is None:
            continue
        rel = path[len(owner) + 1:]
        if owner == k:
            changed = (edits or 0) + (writes or 0)
            activity[k][rel.split("/", 1)[0] if "/" in rel else ""] += 1
            part = drafts[k].folder_part(rel.rsplit("/", 1)[0] if "/" in rel else "")
            if part is not None:
                a = part["activity"]
                a["sessions"].add(sid)
                a["edits"] += changed
                ts = started.get(sid)
                if ts and (a["last"] is None or ts > a["last"]):
                    a["last"] = ts
        elif k in drafts:
            c = cross.setdefault((k, owner), {"sessions": set(), "files": set(), "edits": 0, "examples": []})
            c["sessions"].add(sid)
            c["files"].add(rel)
            c["edits"] += (edits or 0) + (writes or 0)
    for k, d in drafts.items():
        for pid in [pid for pid, p in d.parts.items() if pid.startswith("host:") and not p["evidence"]
                    and not (p["seen"]["host"]["reach"] or len(p["seen"]["host"]["sessions"]) >= 2)]:
            del d.parts[pid]  # named once in a command that did not go there: a test's URL, not where it runs
        _fallback_parts(d, activity[k])
        _merge_duplicates(d)
        _connect_runtime(d)

    # 5. how the glossary says one project uses another
    names: dict[str, str] = {}
    for k, d in drafts.items():
        cand = {Path(k).name if k.startswith("/") else k, d.label}
        cand |= {p.get("package") or "" for p in d.parts.values() if p.get("id") == "dir:"}
        cand.add((meta[k]["git"] or {}).get("repo", "").rsplit("/", 1)[-1])
        for n in cand:
            nn = _norm(n or "")
            if len(nn) >= 4 and nn not in GENERIC_NAMES:
                names.setdefault(nn, k)
    mentions: dict[tuple[str, str], list] = defaultdict(list)
    # the term itself, not its aliases: the glossary's aliases are loose ("SAP Ariba" may list the repo that talks to it)
    for r in conn.execute("SELECT g.term, g.category, u.project_path, u.context FROM glossary g "
                          "JOIN glossary_usage u ON u.term_id = g.id WHERE g.category IN ('system', 'service', 'component')"):
        target = names.get(_norm(r["term"] or ""))
        src = key_of.get(r["project_path"])
        if target and src and src != target and src in drafts:
            mentions[(src, target)].append({"kind": "glossary", "term": r["term"], "text": one_line(r["context"] or "", 300)})

    # 6. groups: the folders systems live in
    def group_of(k: str) -> list[str]:
        if not k.startswith("/"):
            return ["Elsewhere"]
        path = Path(k)
        if path.is_relative_to(projects_root):
            return list(path.relative_to(projects_root).parts[:-1])
        if path.is_relative_to(home):
            return list(path.relative_to(home).parts[:-1])
        return list(path.parts[1:-1])

    groups: dict[str, dict] = {"": {"id": "", "label": "", "parent": None, "systems": [], "groups": [], "loose": 0}}

    def ensure(segs: list[str]) -> str:
        gid = "/".join(segs)
        if gid not in groups:
            parent = ensure(segs[:-1])
            groups[gid] = {"id": gid, "label": segs[-1], "parent": parent, "systems": [], "groups": [], "loose": 0}
            groups[parent]["groups"].append(gid)
        return gid

    sys_group = {k: ensure(group_of(k)) for k in sys_keys}
    for k, gid in sys_group.items():
        groups[gid]["systems"].append(k)
    for p, k in key_of.items():
        if k.startswith("loose:"):
            loose = Path(k[6:])
            segs = list(loose.relative_to(projects_root).parts) if loose.is_relative_to(projects_root) else []
            gid = "/".join(segs)
            groups.get(gid, groups[""])["loose"] += stats[p]["n"]
    # flatten: a folder holding a single system and nothing else is just that system's address
    changed = True
    while changed:
        changed = False
        for gid, g in list(groups.items()):
            if gid and g["parent"] and not g["groups"] and len(g["systems"]) <= 1 and not g["loose"]:
                parent = groups[g["parent"]]
                parent["systems"] += g["systems"]
                for k in g["systems"]:
                    sys_group[k] = parent["id"]
                parent["groups"].remove(gid)
                del groups[gid]
                changed = True
            elif gid and not g["systems"] and len(g["groups"]) == 1 and not g["loose"]:
                child = groups[g["groups"][0]]
                child["label"] = f"{g['label']} › {child['label']}"
                child["parent"] = g["parent"]
                parent = groups[g["parent"]]
                parent["groups"][parent["groups"].index(gid)] = child["id"]
                del groups[gid]
                changed = True

    # 7. finish: plain data for the dashboard
    systems = []
    for k in sys_keys:
        d, m = drafts[k], meta[k]
        st = [stats[p] for p in m["paths"] if p in stats] + [stats[p] for p, kk in key_of.items() if kk == k and p in remote]
        parts = [_finish_part(p) for p in d.parts.values()]
        systems.append({
            "id": k, "label": d.label, "group": sys_group[k], "path": k if k.startswith("/") else None,
            "project_paths": m["paths"], "on_disk": m["on_disk"], "git": m["git"], "remote_runs": m["remote_runs"],
            "sessions": sum(s["n"] for s in st), "active_s": sum(s["active_s"] or 0 for s in st),
            "first": min((s["first"] for s in st if s["first"]), default=None),
            "last": max((s["last"] for s in st if s["last"]), default=None),
            "agents": dict(agents[k].most_common()),
            "stack": _system_stack(parts), "parts": parts, "edges": list(d.edges.values()),
            "ports_unplaced": [{"port": p, "n": n} for p, n in d.ports_unplaced.most_common(6)],
            "folders": [{"folder": f or "(root files)", "files": n} for f, n in activity[k].most_common(14)
                        if f not in SKIP_DIRS and not (f.startswith(".") and f != ".github") and not re.search(r"[${}*]", f)][:12],
        })
    links = []
    for (a, b), c in cross.items():
        links.append({"from": a, "to": b, "kind": "files", "sessions": len(c["sessions"]), "files": len(c["files"]),
                      "edits": c["edits"], "evidence": [{"kind": "files", "text": f"{len(c['sessions'])} session{'s' if len(c['sessions']) != 1 else ''} "
                                                         f"{'edited' if c['edits'] else 'read'} {len(c['files'])} file{'s' if len(c['files']) != 1 else ''}",
                                                         "files": sorted(c["files"])[:8], "sessions": sorted(c["sessions"])[:6]}]})
    for (a, b), evs in mentions.items():
        links.append({"from": a, "to": b, "kind": "mentions", "evidence": evs[:4]})
    return {"groups": list(groups.values()), "systems": systems, "links": links, "built_s": round(time.time() - t0, 2),
            "read_manifests": read_files}


def _finish_part(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k not in ("ports", "calls", "seen", "activity", "proxies")}
    out["ports"] = [{"port": port, "n": n} for port, n in p["ports"].most_common(4) if n]
    out["calls"] = [{"path": path, "n": n} for path, n in p["calls"].most_common(4)]
    a = p["activity"]
    out["activity"] = {"sessions": len(a["sessions"]), "edits": a["edits"], "last": a["last"]}
    ev = list(p["evidence"])
    for key, c in sorted(p.get("seen", {}).items(), key=lambda kv: -kv[1]["n"]):
        label = ("reached in" if key == "host" else f"{key[4:]} in" if key.startswith("cli:")
                 else f"started on :{key[5:]} in" if key.startswith("port:") else "seen in")
        ev.append({"kind": "commands", "text": f"{label} {c['n']} command{'s' if c['n'] != 1 else ''} · {len(c['sessions'])} session{'s' if len(c['sessions']) != 1 else ''}",
                   "key": key, "n": c["n"], "sessions": len(c["sessions"]), "examples": c["examples"]})
    out["evidence"] = ev
    out["weight"] = sum(c["n"] for c in p.get("seen", {}).values()) + len(a["sessions"]) + len(p["evidence"])
    return out


def _system_stack(parts: list[dict]) -> list[str]:
    seen: Counter = Counter()
    for p in parts:
        if p["role"] in ("way_in", "code"):
            for s in p.get("stack", []):
                seen[s] += 1
    return [s for s, _ in seen.most_common(6)]


# =====================================================================================
# cache: the map is rebuilt when sessions, files or the glossary change, or every few minutes for manifests
# =====================================================================================
_CACHE: dict = {"sig": None, "at": 0.0, "data": None}
_LOCK = threading.Lock()  # one build at a time: requests that arrive meanwhile wait for it
TTL = 300


def signature(conn: sqlite3.Connection, cfg) -> tuple:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return (q("SELECT COUNT(*) FROM sessions"), q("SELECT MAX(id) FROM tool_calls"), q("SELECT COUNT(*) FROM session_files"),
            q("SELECT MAX(updated_at) FROM glossary_usage"), cfg.systems_read_manifests, tuple(cfg.exclude_projects))


def cached(conn: sqlite3.Connection, cfg) -> dict:
    with _LOCK:
        sig = signature(conn, cfg)
        if _CACHE["data"] is None or _CACHE["sig"] != sig or time.time() - _CACHE["at"] > TTL:
            _CACHE.update(sig=sig, at=time.time(), data=build(conn, cfg))
        return _CACHE["data"]


def landscape(data: dict) -> dict:
    """The map without each system's parts: what the top level draws."""
    out = []
    for s in data["systems"]:
        light = {k: v for k, v in s.items() if k not in ("parts", "edges", "folders", "ports_unplaced")}
        light["n_parts"] = sum(1 for p in s["parts"] if p["role"] in ("way_in", "code"))
        light["deployed"] = [{"label": p["label"], "platform": p.get("platform"), "what": p.get("what") or p.get("kind")}
                             for p in s["parts"] if p["kind"] in ("deployed", "server")][:6]
        light["platforms"] = sorted({p["platform"] for p in s["parts"] if p.get("platform")})
        light["services"] = sorted({p["label"] for p in s["parts"] if p["kind"] in ("external", "store") and p["id"].startswith("svc:")})
        out.append(light)
    return {**{k: v for k, v in data.items() if k != "systems"}, "systems": out}


def system(data: dict, key: str) -> dict | None:
    s = next((s for s in data["systems"] if s["id"] == key or key in s["project_paths"]), None)
    if s is None:
        return None
    links = [ln for ln in data["links"] if s["id"] in (ln["from"], ln["to"])]
    labels = {x["id"]: x["label"] for x in data["systems"]}
    return {**s, "links": [{**ln, "other": labels.get(ln["to"] if ln["from"] == s["id"] else ln["from"]),
                            "direction": "out" if ln["from"] == s["id"] else "in"} for ln in links]}


def system_for_project(data: dict, project_path: str) -> str | None:
    return next((s["id"] for s in data["systems"] if project_path in s["project_paths"]), None)
