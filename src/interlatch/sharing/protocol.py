"""What a hub and the computers that join it agree on: the protocol version, the columns a shared session and its
analysis carry, size limits, how folders and ids are checked, and the errors either side raises."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import quote

from ..core.paths import is_abs_path, normalize_project_path
from ..projects.routing import MAX_FOLDERS

PROTOCOL = 1

MAX_FILE_BYTES = 4 << 30  # one transcript; far above any real one
ANALYSES_FILE = "analyses.json.gz"
MAX_SESSIONS_JSON = 256 << 20  # one batch of shared sessions, unpacked
MAX_ANALYSES_JSON = 1 << 30  # every analysis a computer has, unpacked
ANALYSES_MAX_AGE = 30 * 86400  # imported analyses whose sessions never arrive are dropped after this

# The analysis a spoke's own Interlatch already wrote, carried over when it joins (see export_analyses).
ANALYSIS_COLS = ("analysis_status", "analysis_reason", "analyzed_at", "analysis_model", "analyzed_prompts", "llm_title",
                 "summary", "goal", "outcome", "outcome_note", "sentiment", "work_types_json", "tags_json",
                 "highlights_json", "open_threads_json", "friction_json", "analysis_json")
KNOWLEDGE_COLS = ("kind", "title", "body", "tags_json", "scope", "confidence", "evidence", "source", "agent",
                  "source_ref", "fingerprint", "status", "pinned", "created_at", "updated_at", "case_json")

# [hub] share = "knowledge": what a computer sends about a session it analyzed itself. Never its prompts, commands,
# file paths or transcript; the hub stores it with source 'remote' and never analyzes it again (receive_sessions).
SHARED_COLS = ("agent", "started_at", "ended_at", "duration_s", "active_s", "git_branch", "cc_version", "primary_model",
               "models_json", "tools_json", "skills_json", "mcp_json", "prs_json", "n_prompts", "n_api_calls",
               "n_tool_calls", "n_tool_errors", "n_interrupts", "n_compactions", "n_api_errors", "n_subagents", "n_files",
               "lines_added", "lines_removed", "input_tokens", "output_tokens", "cache_read_tokens",
               "cache_write_tokens", "sub_tokens", "est_cost_usd", "sub_cost_usd", "peak_context", "ended_flag")
SHARED_AGENTS = ("claude", "codex", "copilot", "bob", "antigravity", "opencode")  # coding agents; imported chats stay personal
SHARE_BATCH = 200
SHARE_KV = "hub-share:"  # + machine id: what that computer sends, as it last said
SESSION_ID_RE = re.compile(r"^[\w.:-]{1,128}$")

TEAM_SOURCE = "team"  # knowledge.source of a teammate's lesson the hub sent; fingerprint "team:<lesson id on the hub>"


class HubError(Exception):
    pass


class HubUnreachable(HubError):
    pass


class UnknownInvite(HubError):
    """An invite code the hub never issued: the server counts it against the address that sent it (people.CodeAttempts)."""


class FolderError(HubError):
    """Why a folder can't be added or a project left: a template and its values, so the dashboard can show it in the
    viewer's language (`shown`) and the command line in English."""

    def __init__(self, template: str, **values):
        super().__init__(template.format(**values) if values else template)
        self.template, self.values = template, values

    def shown(self) -> str:
        from ..core.i18n import tr

        return tr(self.template, **self.values)


class HubUnauthorized(HubError):
    pass


def clean_left(raw) -> list[str]:
    """A spoke's [hub] left as the hub accepts it: absolute project paths, a bounded number of them."""
    if not isinstance(raw, list):
        return []
    return sorted({normalize_project_path(x) for x in raw[:MAX_FOLDERS]
                   if isinstance(x, str) and is_abs_path(x) and len(x) <= 1024 and "\0" not in x})


PROJECT_NAME_RULE = "A project's name is up to 80 characters, without / or \\, and doesn't start with a dot."


def _check_protocol(hello: dict) -> None:
    if int(hello.get("protocol", 0)) != PROTOCOL:
        raise HubError(f"the hub runs version {hello.get('version')}, which speaks a different protocol; "
                       "update both computers to the same version")


def safe_rel(rel: str) -> PurePosixPath | None:
    """A relative path inside a root, or None for anything that could escape it."""
    if not rel or "\\" in rel or "\x00" in rel or rel.startswith("/"):
        return None
    p = PurePosixPath(rel)
    if any(part in ("", ".", "..") or part.startswith(".") for part in p.parts):
        return None
    return p


def join_command(url: str, token: str) -> str:
    return f"interlatch hub join {url} --token={quote(token, safe='-_')}"  # "=": a token may start with "-"


def invite_command(address: str, code: str) -> str:
    """What an invited person runs on their computer: it joins as them and shares knowledge only."""
    return f"interlatch hub join {address} --code {code} --share knowledge"


def invite_link(address: str, code: str) -> str:
    """The same invite opened in a browser: the hub's dashboard, without a computer joining."""
    return f"{address}/signin?code={quote(code, safe='-_')}"
