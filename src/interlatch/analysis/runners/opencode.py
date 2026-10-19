"""Analysis through OpenCode: `opencode run` with an agent of Interlatch's own whose every permission is denied."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from ...core.config import Config
from ...core.process import internal_env
from .base import LIMIT_MARKERS, NO_TOOLS, LLMError, LLMResult, Runner, UsageLimitError
from .process import run_sleep_aware

log = logging.getLogger("interlatch.llm")

OPENCODE_AGENT = "interlatch-analyst"
OPENCODE_TITLE = "Interlatch analysis"  # a title of its own: OpenCode doesn't spend a model call naming the session
# event parts that are not a tool call; anything else (tool, subtask, patch, ...) means the model acted
OPENCODE_ANSWER_PARTS = {"text", "reasoning", "step-start", "step-finish"}
AUTH_ERRORS = ("provider.auth", "auth")


class OpenCodeRunner(Runner):
    """OpenCode's CLI, headless (`opencode run --standalone`, a private server of its own for each call, so the
    background service's config doesn't apply). The call's agent comes in OPENCODE_CONFIG_CONTENT: Interlatch's
    instructions (read from a file, as Windows caps one environment variable at 32K characters) and `"*": "deny"`,
    so the model is offered no tool. It runs in one work folder, workdir/opencode: OpenCode keeps a project per
    folder, and the importer skips sessions there. OpenCode still saves each run as a session; once its usage is
    read, the session is deleted. The model is `provider/model` from your OpenCode providers; empty uses OpenCode's
    default."""

    name, label, cli = "opencode", "OpenCode", "opencode run"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.bin = cfg.opencode_bin()

    def _model(self, model: str | None) -> str | None:
        """A model given for this call when it names a provider (callers pass the Claude defaults, like "sonnet")."""
        if model and "/" in model:
            return model
        return self.cfg.analysis.opencode_model or None

    def model_label(self, model: str | None = None) -> str:
        return self._model(model) or "OpenCode's default model"

    def agent_settings(self) -> dict:
        return {"model": self.cfg.analysis.opencode_model}

    def _env(self, workdir: Path, prompt_file: str | None = None) -> dict[str, str]:
        env = internal_env()
        env["PWD"] = str(workdir)  # OpenCode files the session under $PWD, not the process's own folder
        if prompt_file:
            agent = {"mode": "primary", "prompt": "{file:%s}" % Path(prompt_file).as_posix(), "permission": {"*": "deny"}}
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"agent": {OPENCODE_AGENT: agent}, "autoupdate": False,
                                                         "share": "disabled"})
        return env

    def _call(self, prompt: str, *, system: str, model: str | None, effort: str | None, timeout: int | None,
              max_budget_usd: float | None) -> tuple[LLMResult, str]:
        if not self.bin:
            raise LLMError("opencode executable not found (set analysis.opencode_bin in config.toml)")
        a = self.cfg.analysis
        workdir = self.cfg.home / "workdir" / "opencode"  # empty: no repository, no AGENTS.md
        workdir.mkdir(parents=True, exist_ok=True)
        model = self._model(model)
        with tempfile.NamedTemporaryFile("w", dir=self.cfg.home / "workdir", prefix="opencode-system-", suffix=".md",
                                         delete=False, encoding="utf-8") as fh:
            fh.write(f"{system}\n\n{NO_TOOLS}")
        cmd = [self.bin, "run", "--standalone", "--format", "json", "--agent", OPENCODE_AGENT, "--title", OPENCODE_TITLE,
               "--log-level", "error"]
        if model:
            cmd += ["--model", model]
        t0 = time.monotonic()
        try:
            stdout, stderr, returncode = run_sleep_aware(cmd, prompt, cwd=workdir, env=self._env(workdir, fh.name),
                                                         timeout=timeout or a.timeout_seconds, name=self.cli)
        finally:
            os.unlink(fh.name)
        elapsed = int((time.monotonic() - t0) * 1000)
        texts: dict[str, str] = {}  # part id -> text, in order: a part can be sent more than once as it grows
        tools, errors, session, auth = [], [], None, False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            session = session or event.get("sessionID")
            part = event.get("part") if isinstance(event.get("part"), dict) else {}
            if part and part.get("type") not in OPENCODE_ANSWER_PARTS:
                tools.append(str(part.get("tool") or part.get("type")))
            elif event.get("type") == "text" and part:
                texts[str(part.get("id") or len(texts))] = str(part.get("text") or "")
            elif event.get("type") == "error":
                err = event.get("error") if isinstance(event.get("error"), dict) else {}
                auth = auth or err.get("type") in AUTH_ERRORS or err.get("status") in (401, 403, 429)
                errors.append(str(err.get("message") or event.get("error") or "error"))
        usage = self._usage(session) if session else {}
        if session:
            self._forget(session, workdir)
        if tools:
            raise LLMError(f"opencode used a tool ({', '.join(sorted(set(tools)))}); the reply was discarded")
        text = "".join(texts.values()).strip()
        if errors or returncode != 0 or not text:
            msg = ("; ".join(errors) or stderr.strip() or f"exit {returncode}, no reply")[:800]
            if auth or any(m in msg.lower() for m in LIMIT_MARKERS):
                raise UsageLimitError(msg)
            raise LLMError(f"opencode run failed: {msg}")
        used = usage.get("model") or model
        return LLMResult(
            data={},
            cost_usd=float(usage.get("cost") or 0.0),  # OpenCode's own price; 0 for a subscription or a local model
            duration_ms=elapsed,
            model=f"opencode:{used}" if used else "opencode",
            input_tokens=int(usage.get("input") or 0),
            output_tokens=int(usage.get("output") or 0),
        ), text

    def _usage(self, session: str) -> dict:
        """The run's tokens, cost and model, from its row in OpenCode's database (2.x); {} when it can't be read."""
        from ...sources.discovery import opencode_home
        from ...sources.opencode import opencode_dbs

        for db in opencode_dbs(opencode_home()):
            try:
                conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
                try:
                    row = conn.execute(
                        "SELECT cost, tokens_input + tokens_cache_read + tokens_cache_write, "
                        "tokens_output + tokens_reasoning, model FROM session_v2 WHERE id = ?", (session,)).fetchone()
                finally:
                    conn.close()
            except sqlite3.Error:
                continue
            if row:
                try:
                    m = json.loads(row[3] or "{}")
                except ValueError:
                    m = {}
                name = "/".join(x for x in (m.get("providerID"), m.get("id")) if x) if isinstance(m, dict) else ""
                return {"cost": row[0], "input": row[1], "output": row[2], "model": name or None}
        return {}

    def _forget(self, session: str, workdir: Path) -> None:
        """Delete the run's session from OpenCode, so analyses don't pile up in its history. The importer skips the
        work folder anyway, so a session left behind (an older OpenCode, a failed delete) is never recorded."""
        try:
            done = subprocess.run([self.bin, "session", "delete", "--standalone", session], cwd=workdir,
                                  env=self._env(workdir), capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("opencode: could not delete analysis session %s: %s", session, exc)
            return
        if done.returncode != 0:
            log.warning("opencode: could not delete analysis session %s (exit %s)", session, done.returncode)
