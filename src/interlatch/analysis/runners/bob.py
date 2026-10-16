"""Analysis through IBM Bob's CLI, in a mode of Interlatch's own with its tool groups off."""

from __future__ import annotations

import html
import json
import shutil
import tempfile
import time
from pathlib import Path

from ...core.config import Config
from ...core.process import internal_env
from .base import LIMIT_MARKERS, LLMError, LLMResult, Runner, UsageLimitError
from .process import run_sleep_aware

# Bob Shell's tool groups (those its built-in modes use); every one is off for analysis.
BOB_TOOL_GROUPS = ("artifact", "browser", "edit", "execute", "mcp", "mode", "plan", "read", "skill", "subagent",
                   "subtask", "todo")
BOB_MODE = "interlatch-analyst"
# Bob reads tool calls out of the reply's text, so a model can write one with every tool group off; saying so up front
# makes that rare, and a call that does it anyway is retried once (TOOL_TRIES).
NO_TOOLS = ("You have no tools here: don't call or write one (no list_files, read_file or the like). Everything you "
            "need is in the message; answer it directly.")
TOOL_TRIES = 2


class _ToolUsed(LLMError):
    """The model called a tool, so its reply was discarded; `cost_usd` is what the call cost."""

    def __init__(self, msg: str, cost_usd: float):
        super().__init__(msg)
        self.cost_usd = cost_usd


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
        from .settings import key_source

        return key_source(self.cfg, "bob")

    def available(self) -> bool:
        return bool(self.bin) and bool(self._key()[0])

    def unavailable_reason(self) -> str:
        from ...core.i18n import tr

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
        spent = 0.0
        for attempt in range(1, TOOL_TRIES + 1):
            try:
                res, text = self._run(prompt, system=f"{system}\n\n{NO_TOOLS}", timeout=timeout,
                                      max_budget_usd=max_budget_usd)
            except _ToolUsed as exc:
                spent += exc.cost_usd
                if attempt == TOOL_TRIES:
                    raise
                continue
            res.cost_usd += spent
            return res, text
        raise AssertionError("unreachable")

    def _run(self, prompt: str, *, system: str, timeout: int | None,
             max_budget_usd: float | None) -> tuple[LLMResult, str]:
        """One `bob run`."""
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
            env = {**internal_env(), "BOB_API_KEY": key}
            t0 = time.monotonic()
            stdout, stderr, returncode = run_sleep_aware(cmd, prompt, cwd=ws, env=env,
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
            raise _ToolUsed(f"bob used a tool ({names}); the reply was discarded",
                            float(stats.get("session_costs") or 0.0))
        text = "".join(parts)
        if returncode != 0 or not result or result.get("status") != "success" or not text:
            msg = "; ".join(errors) or str((result or {}).get("error") or "") or html.unescape(stderr).strip()
            msg = (msg or f"exit {returncode}, no reply")[:800]
            if any(m in msg.lower() for m in (*LIMIT_MARKERS, "license", "api key")):
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
