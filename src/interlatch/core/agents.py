"""The coding agents Interlatch records, and how each is named."""

AGENTS = {  # id -> (full name, short name, speaker label in digests)
    "claude": ("Claude Code", "Claude", "CLAUDE"),
    "codex": ("OpenAI Codex", "Codex", "CODEX"),
    "copilot": ("GitHub Copilot", "Copilot", "COPILOT"),
    "bob": ("IBM Bob", "Bob", "BOB"),
    "antigravity": ("Google Antigravity", "Antigravity", "ANTIGRAVITY"),
    "opencode": ("OpenCode", "OpenCode", "OPENCODE"),
    "claude-ai": ("Claude.ai", "Claude.ai", "CLAUDE"),  # chats imported from a claude.ai data export
    "chatgpt": ("ChatGPT", "ChatGPT", "CHATGPT"),  # chats imported from a ChatGPT data export
}
# the agents that code (their sessions run on a computer): never the imported chats (claude-ai, chatgpt). They are also
# the Party's characters and the arena's; leaderboard/firestore.rules lists them for arena sheets (a new one there is
# allowed, not required, so sheets from older versions still pass).
CODING_AGENTS = ("claude", "codex", "copilot", "bob", "antigravity", "opencode")


def full_name(agent: str | None) -> str:
    return AGENTS.get(agent or "claude", (agent or "agent",) * 3)[0]


def short_name(agent: str | None) -> str:
    return AGENTS.get(agent or "claude", (agent or "agent",) * 3)[1]


def speaker(agent: str | None) -> str:
    return AGENTS.get(agent or "claude", ("", "", (agent or "agent").upper()))[2]
