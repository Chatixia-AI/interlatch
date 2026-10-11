"""Run a headless coding agent with structured output: Claude Code (`claude -p`) or Codex (`codex exec`); or, through
providers.py, a model provider's API.

Agent runs use the developer's own install and login of the chosen agent (`analysis.backend`).
Each call is isolated: no session is persisted (so analyses never show up as sessions), user hooks,
plugins, MCP servers and instruction files are not loaded, and no tools are available, so the model
can only answer. Codex cannot turn every tool off by flag, so its event stream is also checked: a
reply produced after any tool call is discarded.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config

log = logging.getLogger("interlatch.llm")

INTERNAL_ENV = "INTERLATCH_INTERNAL"  # hooks.py: the sessions of the agents this runs are not recorded


class LLMError(RuntimeError):
    pass


class UsageLimitError(LLMError):
    """Rate/usage limit or auth problem: pause all analysis for a while."""


class BudgetExceededError(LLMError):
    pass


class SleepInterruptedError(LLMError):
    """The machine slept during the call; the connection is dead. Retry without counting a failure."""


SLEEP_GRACE_S = 120  # wall-clock time not seen by the monotonic clock = the machine was asleep
POLL_S = 10


@dataclass
class LLMResult:
    data: dict
    cost_usd: float
    duration_ms: int
    model: str | None
    input_tokens: int
    output_tokens: int


_LIMIT_MARKERS = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "overloaded",
    "429",
    "credit balance",
    "please run /login",
    "invalid api key",
    "not logged in",
    "oauth token",
    "authentication",
    "unauthorized",
    "refresh token",
    "log in again",
)

AGENTS = {"claude": "Claude Code", "codex": "Codex", "bob": "IBM Bob"}  # backends that run a coding agent's CLI
# every analysis.backend: the agents, then the model providers' APIs (providers.PROVIDERS)
BACKENDS = {**AGENTS, "anthropic": "Anthropic API", "bedrock": "Amazon Bedrock", "openai": "OpenAI API",
            "azure": "Azure OpenAI", "openrouter": "OpenRouter", "ollama": "Ollama", "openai-compatible": "OpenAI-compatible"}

# What a prompt says instead of (or besides) its English-writing rule when [analysis] language is "ja"
JAPANESE = (
    "Write all prose in Japanese: natural and concise. Short fields may be fragments and need not use です/ます. Put a "
    "half-width space between Japanese and Latin letters or digits. Keep identifiers, commands, file paths, config keys, "
    "URLs, error messages, quotes and code verbatim, in their original language. JSON keys and every enum or code value "
    "the schema lists (kind, scope, confidence, outcome, reason, verdict and the like) stay exactly as listed, in English."
)


def written_in(cfg: Config, system: str, english: str = "", note: str = "") -> str:
    """`system` in the language Interlatch writes in ([analysis] language). English leaves it exactly as it is; Japanese
    swaps its English-writing sentence (`english`) for JAPANESE and `note`, or appends them when it has none."""
    if cfg.analysis.language != "ja":
        return system
    rule = " ".join(x for x in (JAPANESE, note) if x)
    return system.replace(english, rule) if english and english in system else f"{system}\n\n{rule}"


class Runner:
    """Asks the model for one JSON object per call; subclasses run a specific agent's CLI."""

    name = ""
    label = ""
    cli = ""  # the command as shown in messages

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.bin: str | None = None

    def available(self) -> bool:
        return bool(self.bin)

    def unavailable_reason(self) -> str:
        """Why it cannot run now ("" when it can), as a sentence for the dashboard and the CLI."""
        from .i18n import tr

        return "" if self.available() else tr("{label} (`{cli}`) was not found", label=self.label, cli=self.cli.split()[0])

    def where(self) -> str:
        """The executable, or the endpoint, that does the analysis."""
        return self.bin or ""

    def local(self) -> bool:
        """Transcripts stay on this computer. A coding agent sends them to its own provider."""
        return False

    @property
    def chunk_chars(self) -> int:
        """Characters of condensed transcript per call."""
        return self.cfg.analysis.chunk_chars

    def describe(self) -> dict:
        return {"name": self.name, "label": self.label, "kind": "agent", "available": self.available(),
                "reason": self.unavailable_reason(), "path": self.bin, "model": self.model_label(), "local": False,
                "settings": self.agent_settings()}

    def agent_settings(self) -> dict:
        """The settings the dashboard lets you change for this agent (server.AGENT_SETTINGS), with their values."""
        return {}

    def model_label(self, model: str | None = None) -> str:
        raise NotImplementedError

    def _call(self, prompt: str, *, system: str, model: str | None, effort: str | None, timeout: int | None,
              max_budget_usd: float | None) -> tuple[LLMResult, str]:
        raise NotImplementedError

    def run(self, prompt: str, schema: dict, *, system: str, model: str | None = None,
            effort: str | None = None, timeout: int | None = None, max_budget_usd: float | None = None) -> LLMResult:
        """Ask for a JSON object conforming to `schema` (given in the system prompt) and parse it.

        Plain JSON text is used instead of --json-schema: forcing a tool call for large payloads makes
        the model wrap or truncate fields and the CLI then silently regenerates the whole answer.
        """
        full_system = (
            f"{system}\n\nReply with a single JSON object and nothing else: no prose, no code fences. "
            f"It must conform to this JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}"
        )
        res, text = self._call(prompt, system=full_system, model=model, effort=effort, timeout=timeout,
                               max_budget_usd=max_budget_usd)
        data = parse_json_object(text)
        if data is None:  # one repair pass on the raw text, without the transcript
            log.warning("unparseable JSON from model, attempting repair (%d chars)", len(text))
            repair, fixed = self._call(
                f"<broken_json>\n{text}\n</broken_json>\n\nThe text above was meant to be one JSON object but does "
                "not parse. Return the same content as a single valid JSON object only.",
                system="You repair malformed JSON. Output only the corrected JSON object.",
                model=model, effort="low", timeout=timeout, max_budget_usd=max_budget_usd,
            )
            data = parse_json_object(fixed)
            res.cost_usd += repair.cost_usd
            res.duration_ms += repair.duration_ms
            if data is None:
                raise LLMError(f"model did not return valid JSON ({len(text)} chars): {text[:200]} … {text[-150:]}")
        res.data = data
        return res


def make_runner(cfg: Config) -> Runner:
    backend = cfg.analysis.backend
    if backend in BACKENDS and backend not in AGENTS:
        from .providers import ApiRunner

        return ApiRunner(cfg, backend)
    if backend == "bob":
        return BobRunner(cfg)
    return CodexRunner(cfg) if backend == "codex" else ClaudeRunner(cfg)


class ClaudeRunner(Runner):
    name, label, cli = "claude", "Claude Code", "claude -p"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.bin = cfg.claude_bin()

    def model_label(self, model: str | None = None) -> str:
        return model or self.cfg.analysis.model

    def agent_settings(self) -> dict:
        a = self.cfg.analysis
        return {"model": a.model, "synthesis_model": self.cfg.synthesis.model, "screen_model": a.screen_model,
                "effort": a.effort}

    def _call(self, prompt: str, *, system: str, model: str | None, effort: str | None, timeout: int | None,
              max_budget_usd: float | None) -> tuple[LLMResult, str]:
        if not self.bin:
            raise LLMError("claude executable not found (set analysis.claude_bin in config.toml)")
        a = self.cfg.analysis
        cmd = [
            self.bin, "-p",
            "--output-format", "json",
            "--model", model or a.model,
            "--no-session-persistence",
            "--safe-mode",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--tools", "",
            "--system-prompt", system,
            "--max-budget-usd", f"{max_budget_usd or a.max_budget_usd:.2f}",
        ]
        if effort or a.effort:
            cmd += ["--effort", effort or a.effort]
        env = {**os.environ, INTERNAL_ENV: "1"}
        workdir = self.cfg.home / "workdir"
        workdir.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        stdout, stderr, returncode = _run_sleep_aware(cmd, prompt, cwd=workdir, env=env,
                                                      timeout=timeout or a.timeout_seconds, name=self.cli)
        elapsed = int((time.monotonic() - t0) * 1000)
        proc = subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
        out = (proc.stdout or "").strip()
        payload = None
        if out:
            try:
                payload = json.loads(out if out.startswith("{") else out.splitlines()[-1])
            except ValueError:
                payload = None
        if payload is None:
            msg = (proc.stderr or out or f"exit {proc.returncode}").strip()[:800]
            if any(m in msg.lower() for m in _LIMIT_MARKERS):
                raise UsageLimitError(msg)
            raise LLMError(f"claude -p failed: {msg}")
        subtype = payload.get("subtype")
        text = str(payload.get("result") or "")
        if payload.get("is_error") or (subtype and subtype != "success"):
            detail = f"{subtype}: {text or payload.get('api_error_status') or ''}".strip()[:800]
            if subtype and "budget" in subtype:
                raise BudgetExceededError(detail)
            if payload.get("api_error_status") in (429, 529) or any(m in detail.lower() for m in _LIMIT_MARKERS):
                raise UsageLimitError(detail)
            raise LLMError(detail)
        usage = payload.get("usage") or {}
        model_usage = payload.get("modelUsage") or {}
        models = sorted(model_usage, key=lambda m: -(model_usage[m].get("outputTokens") or 0))
        return LLMResult(
            data={},
            cost_usd=float(payload.get("total_cost_usd") or 0.0),
            duration_ms=int(payload.get("duration_ms") or elapsed),
            model=models[0] if models else (model or a.model),
            input_tokens=(usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0),
            output_tokens=usage.get("output_tokens") or 0,
        ), text


# Codex features that give the model a tool (or load code that could). Unknown names are ignored by
# Codex, so the list can name features that only some versions have.
CODEX_FEATURES_OFF = (
    "shell_tool", "unified_exec", "shell_snapshot", "js_repl", "code_mode", "code_mode_host", "view_image",
    "image_generation", "multi_agent", "multi_agent_v2", "multi_agent_mode", "apps", "plugins", "browser_use",
    "browser_use_external", "in_app_browser", "computer_use", "tool_suggest", "skill_search", "memories",
    "goals", "hooks", "request_permissions_tool",
)
# Event items that are not a tool call. Anything else (command_execution, file_change, mcp_tool_call,
# web_search, collab tool calls, ...) means the model acted, and its reply is discarded.
CODEX_ANSWER_ITEMS = {"agent_message", "reasoning", "error", "todo_list"}
_CODEX_EFFORT = {"max": "xhigh"}
_CLAUDE_MODEL = re.compile(r"^(claude|sonnet|opus|haiku|fable)", re.I)


class CodexRunner(Runner):
    name, label, cli = "codex", "Codex", "codex exec"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.bin = cfg.codex_bin()

    def _model(self, model: str | None) -> str | None:
        """A model given for this call, unless it is a Claude name (callers pass the Claude defaults)."""
        if model and not _CLAUDE_MODEL.match(model):
            return model
        return self.cfg.analysis.codex_model or None

    def model_label(self, model: str | None = None) -> str:
        return self._model(model) or "Codex default model"

    def agent_settings(self) -> dict:
        return {"model": self.cfg.analysis.codex_model, "effort": self.cfg.analysis.effort}

    def _call(self, prompt: str, *, system: str, model: str | None, effort: str | None, timeout: int | None,
              max_budget_usd: float | None) -> tuple[LLMResult, str]:
        if not self.bin:
            raise LLMError("codex executable not found (set analysis.codex_bin in config.toml)")
        a = self.cfg.analysis
        workdir = self.cfg.home / "workdir" / "codex"  # empty: no repository, no AGENTS.md
        workdir.mkdir(parents=True, exist_ok=True)
        model = self._model(model)
        effort = effort or a.effort
        # the system prompt replaces Codex's own coding-agent instructions
        with tempfile.NamedTemporaryFile("w", dir=self.cfg.home / "workdir", prefix="codex-system-", suffix=".md",
                                         delete=False, encoding="utf-8") as fh:
            fh.write(system)
        cmd = [
            self.bin, "exec",
            "--ephemeral",  # no rollout file: the analysis never shows up as a Codex session
            "--ignore-user-config",  # no user MCP servers, profiles or instructions
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--color", "never",
            "--json",
            "-c", 'web_search="disabled"',
            "-c", "project_doc_max_bytes=0",
            "-c", "skills.include_instructions=false",
            "-c", f"model_instructions_file={json.dumps(fh.name)}",
        ]
        for feature in CODEX_FEATURES_OFF:
            cmd += ["-c", f"features.{feature}=false"]
        if model:
            cmd += ["-m", model]
        if effort:
            cmd += ["-c", f'model_reasoning_effort="{_CODEX_EFFORT.get(effort, effort)}"']
        cmd.append("-")  # the prompt comes on stdin
        env = {**os.environ, INTERNAL_ENV: "1"}
        t0 = time.monotonic()
        try:
            stdout, stderr, returncode = _run_sleep_aware(cmd, prompt, cwd=workdir, env=env,
                                                          timeout=timeout or a.timeout_seconds, name=self.cli)
        finally:
            os.unlink(fh.name)
        elapsed = int((time.monotonic() - t0) * 1000)
        text, usage, failures, notices, tools = "", {}, [], [], []
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            kind = event.get("type")
            if item and item.get("type") not in CODEX_ANSWER_ITEMS:
                tools.append(str(item.get("type")))
            if kind == "item.completed" and item.get("type") == "agent_message":
                text = str(item.get("text") or "")
            elif kind == "turn.completed":
                usage = event.get("usage") or {}
            elif kind == "turn.failed":
                failures.append(str((event.get("error") or {}).get("message") or "turn failed"))
            elif kind == "error":  # also sent for retries ("Reconnecting... 2/5"); fatal only without a reply
                notices.append(str(event.get("message") or "error"))
        if tools:
            raise LLMError(f"codex used a tool ({', '.join(sorted(set(tools)))}); the reply was discarded")
        if failures or returncode != 0 or not text:
            failures += notices
            noise = ("models_manager", "codex_core::tools::router")  # warnings Codex logs on every run
            err = "\n".join(l for l in stderr.splitlines() if l.strip() and not any(n in l for n in noise))
            msg = ("; ".join(failures) or err or f"exit {returncode}, no reply").strip()[:800]
            if any(m in msg.lower() for m in _LIMIT_MARKERS):
                raise UsageLimitError(msg)
            raise LLMError(f"codex exec failed: {msg}")
        return LLMResult(
            data={},
            cost_usd=0.0,  # Codex reports tokens, not a price; it draws on the ChatGPT plan or API key
            duration_ms=elapsed,
            model=f"codex:{model}" if model else "codex",
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0) + int(usage.get("reasoning_output_tokens") or 0),
        ), text


# Bob Shell's tool groups (those its built-in modes use); every one is off for analysis.
BOB_TOOL_GROUPS = ("artifact", "browser", "edit", "execute", "mcp", "mode", "plan", "read", "skill", "subagent",
                   "subtask", "todo")
BOB_MODE = "interlatch-analyst"


class BobRunner(Runner):
    """IBM Bob Shell, headless (`bob run`). Bob has no flag for a system prompt or for running without tools, so each
    call gets a workspace of its own under workdir/bob holding a custom mode (.bob/custom_modes.yaml): Interlatch's
    instructions as the mode's role, and no tool groups. Every tool group, MCP and subagents are also switched off on
    the command line, and a reply that follows any tool call is discarded. Headless runs need a Bob API key (the
    app's sign-in is not used); Bob saves each run as a task in its database, which Interlatch's Bob import skips."""

    name, label, cli = "bob", "IBM Bob", "bob run"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.bin = cfg.bob_bin()

    def _key(self) -> tuple[str | None, str]:
        from .providers import key_source

        return key_source(self.cfg, "bob")

    def available(self) -> bool:
        return bool(self.bin) and bool(self._key()[0])

    def unavailable_reason(self) -> str:
        from .i18n import tr

        if self.bin and not self._key()[0]:
            return tr("{label}: add a Bob API key (BOB_API_KEY); headless runs don't use the app's sign-in", label=self.label)
        return super().unavailable_reason()

    def model_label(self, model: str | None = None) -> str:
        return "Bob's default model"

    def describe(self) -> dict:
        _, source = self._key()
        return {**super().describe(), "key": {"source": source, "env": "BOB_API_KEY", "needed": True, "alt": ""}}

    def _call(self, prompt: str, *, system: str, model: str | None, effort: str | None, timeout: int | None,
              max_budget_usd: float | None) -> tuple[LLMResult, str]:
        if not self.bin:
            raise LLMError("bob executable not found (set analysis.bob_bin in config.toml)")
        key, _ = self._key()
        if not key:
            raise UsageLimitError(self.unavailable_reason())
        a = self.cfg.analysis
        root = self.cfg.home / "workdir" / "bob"
        root.mkdir(parents=True, exist_ok=True)
        ws = Path(tempfile.mkdtemp(dir=root, prefix="run-"))  # one per call: calls run in parallel
        try:
            (ws / ".bob").mkdir()
            mode = {"customModes": [{"slug": BOB_MODE, "name": "Interlatch analyst", "roleDefinition": system, "groups": []}]}
            (ws / ".bob" / "custom_modes.yaml").write_text(json.dumps(mode))  # JSON is YAML
            cmd = [self.bin, "run", "--format", "stream-json", "--workspace", str(ws), "--mode", BOB_MODE,
                   "--max-turns", "1", "--disable-mcp", "--disable-subagents",
                   "--disable-tool-groups", ",".join(BOB_TOOL_GROUPS), "--log-level", "error",
                   "--max-cost", f"{max_budget_usd or a.max_budget_usd:.2f}"]
            env = {**os.environ, INTERNAL_ENV: "1", "BOB_API_KEY": key}
            t0 = time.monotonic()
            stdout, stderr, returncode = _run_sleep_aware(cmd, prompt, cwd=ws, env=env,
                                                          timeout=timeout or a.timeout_seconds, name=self.cli)
            elapsed = int((time.monotonic() - t0) * 1000)
        finally:
            shutil.rmtree(ws, ignore_errors=True)
        parts, tools, errors, result, acted = [], [], [], None, False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "message" and event.get("role") == "assistant":
                parts.append(str(event.get("content") or ""))  # the reply comes in pieces
            elif kind in ("tool_use", "tool_result"):  # the model acted
                acted = True
                if kind == "tool_use":
                    tools.append(str(event.get("tool_name") or "a tool"))
            elif kind == "result":
                result = event
            elif kind == "error":
                errors.append(str(event.get("message") or event.get("error") or "error"))
        stats = (result or {}).get("stats") or {}
        if acted or stats.get("tool_calls"):
            names = ", ".join(sorted(set(tools))) or f"{stats.get('tool_calls')} call(s)"
            raise LLMError(f"bob used a tool ({names}); the reply was discarded")
        text = "".join(parts)
        if returncode != 0 or not result or result.get("status") != "success" or not text:
            msg = "; ".join(errors) or str((result or {}).get("error") or "") or html.unescape(stderr).strip()
            msg = (msg or f"exit {returncode}, no reply")[:800]
            if any(m in msg.lower() for m in (*_LIMIT_MARKERS, "license", "api key")):
                raise UsageLimitError(msg)
            raise LLMError(f"bob run failed: {msg}")
        return LLMResult(
            data={},
            cost_usd=float(stats.get("session_costs") or 0.0),  # Bob reports its cost, not tokens
            duration_ms=int(stats.get("duration_ms") or elapsed),
            model="bob",
            input_tokens=0,
            output_tokens=0,
        ), text


def _kill_group(proc: subprocess.Popen) -> None:
    import signal

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _run_sleep_aware(cmd: list[str], prompt: str, *, cwd, env, timeout: int, name: str = "claude -p") -> tuple[str, str, int]:
    """subprocess.run with a wall-clock deadline that also notices system sleep.

    subprocess timeouts use the monotonic clock, which stops while a Mac sleeps, so a call frozen
    by sleep (its HTTPS connection dead) would otherwise hang for its full timeout after wake.
    """
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=cwd, env=env, start_new_session=True)
    except OSError as exc:  # a configured path that does not exist, or is not executable
        raise LLMError(f"{name} could not start: {exc}") from exc
    wall0, mono0 = time.time(), time.monotonic()
    pending_input: str | None = prompt
    while True:
        try:
            out, err = proc.communicate(input=pending_input, timeout=POLL_S)
            return out or "", err or "", proc.returncode
        except subprocess.TimeoutExpired:
            pending_input = None  # input is only sent once; later calls just keep collecting output
            wall = time.time() - wall0
            slept = wall - (time.monotonic() - mono0)
            if slept > SLEEP_GRACE_S:
                _kill_group(proc)
                raise SleepInterruptedError(f"machine slept for {int(slept)}s during the call") from None
            if wall > timeout:
                _kill_group(proc)
                raise LLMError(f"{name} timed out after {int(wall)}s") from None
        except BaseException:
            _kill_group(proc)
            raise


_INVALID_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def parse_json_object(text: str) -> dict | None:
    """Parse a JSON object from model text, tolerating code fences and surrounding prose."""
    text = (text or "").strip()
    if not text:
        return None
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        candidates.append(fence.group(1))
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand, strict=False)  # tolerate raw control characters inside strings
        except ValueError:
            try:  # stray backslashes (regexes, Windows paths) are the usual culprit
                obj = json.loads(_INVALID_ESCAPE.sub(r"\\\\", cand), strict=False)
            except ValueError:
                continue
        if isinstance(obj, dict):
            # a model occasionally wraps the answer in a single key like "parameter"
            if len(obj) == 1 and isinstance(next(iter(obj.values())), dict):
                inner = next(iter(obj.values()))
                if len(inner) > 1:
                    return inner
            return obj
    return None
