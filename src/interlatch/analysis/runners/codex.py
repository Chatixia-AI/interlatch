"""Analysis through Codex: `codex exec` in a read-only sandbox, its answer read from the JSON event stream."""

from __future__ import annotations

import json
import os
import tempfile
import time

from ...core.config import Config
from ...core.process import internal_env
from .base import CLAUDE_MODEL, LIMIT_MARKERS, LLMError, LLMResult, Runner, UsageLimitError
from .process import run_sleep_aware

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


class CodexRunner(Runner):
    name, label, cli = "codex", "Codex", "codex exec"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.bin = cfg.codex_bin()

    def _model(self, model: str | None) -> str | None:
        """A model given for this call, unless it is a Claude name (callers pass the Claude defaults)."""
        if model and not CLAUDE_MODEL.match(model):
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
        env = internal_env()  # the sessions of the agents this runs are not recorded
        t0 = time.monotonic()
        try:
            stdout, stderr, returncode = run_sleep_aware(cmd, prompt, cwd=workdir, env=env,
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
            if any(m in msg.lower() for m in LIMIT_MARKERS):
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
