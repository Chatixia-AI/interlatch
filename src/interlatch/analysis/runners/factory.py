"""Every analysis backend Interlatch knows, and the Runner for the one [analysis] backend names."""

from __future__ import annotations

from ...core.config import Config
from .base import Runner
from .bob import BobRunner
from .claude import ClaudeRunner
from .codex import CodexRunner
from .off import OffRunner
from .opencode import OpenCodeRunner

# backends that run a coding agent's CLI
AGENTS = {"claude": "Claude Code", "codex": "Codex", "bob": "IBM Bob", "opencode": "OpenCode"}
# every analysis.backend: the agents, the model providers' APIs (settings.PROVIDERS), and "none": record only
BACKENDS = {**AGENTS, "anthropic": "Anthropic API", "bedrock": "Amazon Bedrock", "openai": "OpenAI API",
            "azure": "Azure OpenAI", "openrouter": "OpenRouter", "ollama": "Ollama", "openai-compatible": "OpenAI-compatible",
            "none": "Record only"}
OFF = "none"


def make_runner(cfg: Config) -> Runner:
    backend = cfg.analysis.backend
    if backend == OFF:
        return OffRunner(cfg)
    if backend in BACKENDS and backend not in AGENTS:
        from .api import ApiRunner

        return ApiRunner(cfg, backend)
    if backend == "bob":
        return BobRunner(cfg)
    if backend == "opencode":
        return OpenCodeRunner(cfg)
    return CodexRunner(cfg) if backend == "codex" else ClaudeRunner(cfg)
