# Interlatch for VS Code

Adds two sections to the Explorer that list coding-agent sessions (Claude Code, Codex, Copilot, IBM Bob, Antigravity): the ones
that read or changed the open file, and the workspace's files that sessions touched, each expanding to its sessions.
Expand a session for its details, and open it in your local [Interlatch](https://interlatch.com) dashboard.

Needs Interlatch running on this computer: install it with `uv tool install interlatch`, then `interlatch ui`. Install,
settings and limits: [VS Code extension](https://interlatch.com/docs/vscode/).

Formerly the Chronicle extension; uninstall it after installing this one. Settings you made for Chronicle
(`chronicle.url` and the rest) still apply until you set their `interlatch.*` counterparts.
