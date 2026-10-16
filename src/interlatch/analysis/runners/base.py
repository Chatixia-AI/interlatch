"""Run a headless coding agent with structured output: Claude Code (`claude -p`), Codex (`codex exec`) or IBM Bob;
or, through api.py, a model provider's API.

Agent runs use the developer's own install and login of the chosen agent (`analysis.backend`).
Each call is isolated: no session is persisted (so analyses never show up as sessions), user hooks,
plugins, MCP servers and instruction files are not loaded, and no tools are available, so the model
can only answer. Codex cannot turn every tool off by flag, so its event stream is also checked: a
reply produced after any tool call is discarded.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ...core.config import Config
from .replies import parse_json_object

log = logging.getLogger("interlatch.llm")


class LLMError(RuntimeError):
    pass


class UsageLimitError(LLMError):
    """Rate/usage limit or auth problem: pause all analysis for a while."""


class BudgetExceededError(LLMError):
    pass


class SleepInterruptedError(LLMError):
    """The machine slept during the call; the connection is dead. Retry without counting a failure."""


@dataclass
class LLMResult:
    data: dict
    cost_usd: float
    duration_ms: int
    model: str | None
    input_tokens: int
    output_tokens: int


LIMIT_MARKERS = (
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
        from ...core.i18n import tr

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
        """The settings the dashboard lets you change for this agent, with their values (its AGENT_SETTINGS)."""
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

    def check(self) -> LLMResult:
        """One small call with the main model: do the endpoint, the key and the model work? Raises LLMError."""
        return self.run('Reply with {"ok": true}.', {"type": "object", "properties": {"ok": {"type": "boolean"}},
                                                     "required": ["ok"]},
                        system="You check that a connection works.", effort="low", timeout=120)


CLAUDE_MODEL = re.compile(r"^(claude|sonnet|opus|haiku|fable)", re.I)
