- **`interlatch status` checks every agent:** it has a line for each coding agent found on this computer (Claude Code,
  Codex, Copilot, Bob, Antigravity, OpenCode, Cursor and the other MCP clients) saying whether Interlatch's MCP server
  is in it, and flags one whose command is gone or runs another install, such as an old `chronicle` or a checkout's
  `.venv`; `interlatch connect <name>` points it back ([Troubleshooting](docs/troubleshooting.md)).
