"""The chat exports Interlatch imports (claude.ai, ChatGPT): each one's name, the agent and source its sessions
are recorded under, and the module that decodes it."""

from __future__ import annotations

from dataclasses import dataclass

from . import chatgpt as chatgpt_export
from .claude import export as claude_export


@dataclass(frozen=True)
class Format:
    key: str  # kv and archive name
    label: str
    module: object

    @property
    def agent(self) -> str:
        return self.module.AGENT

    @property
    def source(self) -> str:
        return self.module.SOURCE

    @property
    def last_import_key(self) -> str:
        return f"{self.key}:last-import"

    @property
    def last_capture_key(self) -> str:
        return f"{self.key}:last-capture"  # the browser extension's last chat sent


CLAUDE_AI = Format("claude-ai", "claude.ai", claude_export)
CHATGPT = Format("chatgpt", "ChatGPT", chatgpt_export)
FORMATS = (CLAUDE_AI, CHATGPT)
