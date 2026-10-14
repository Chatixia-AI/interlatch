"""Analysis through Claude Code: `claude -p` with its tools off, from a work folder of Interlatch's own."""

from __future__ import annotations

import json
import subprocess
import time

from ...core.config import Config
from ...core.process import internal_env
from .base import LIMIT_MARKERS, BudgetExceededError, LLMError, LLMResult, Runner, UsageLimitError
from .process import run_sleep_aware


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
        env = internal_env()  # the sessions of the agents this runs are not recorded
        workdir = self.cfg.home / "workdir"
        workdir.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        stdout, stderr, returncode = run_sleep_aware(cmd, prompt, cwd=workdir, env=env,
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
            if any(m in msg.lower() for m in LIMIT_MARKERS):
                raise UsageLimitError(msg)
            raise LLMError(f"claude -p failed: {msg}")
        subtype = payload.get("subtype")
        text = str(payload.get("result") or "")
        if payload.get("is_error") or (subtype and subtype != "success"):
            detail = f"{subtype}: {text or payload.get('api_error_status') or ''}".strip()[:800]
            if subtype and "budget" in subtype:
                raise BudgetExceededError(detail)
            if payload.get("api_error_status") in (429, 529) or any(m in detail.lower() for m in LIMIT_MARKERS):
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
