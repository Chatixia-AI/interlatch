"""Every analysis backend Interlatch knows, and the Runner for the one [analysis] backend names."""

from __future__ import annotations

from ...core.config import Config
from .base import Runner
from .bob import BobRunner
from .claude import ClaudeRunner
from .codex import CodexRunner

AGENTS = {"claude": "Claude Code", "codex": "Codex", "bob": "IBM Bob"}  # backends that run a coding agent's CLI
# every analysis.backend: the agents, then the model providers' APIs (providers.PROVIDERS)
BACKENDS = {**AGENTS, "anthropic": "Anthropic API", "bedrock": "Amazon Bedrock", "openai": "OpenAI API",
            "azure": "Azure OpenAI", "openrouter": "OpenRouter", "ollama": "Ollama", "openai-compatible": "OpenAI-compatible"}


def make_runner(cfg: Config) -> Runner:
    backend = cfg.analysis.backend
    if backend in BACKENDS and backend not in AGENTS:
        from .api import ApiRunner

        return ApiRunner(cfg, backend)
    if backend == "bob":
        return BobRunner(cfg)
    return CodexRunner(cfg) if backend == "codex" else ClaudeRunner(cfg)
