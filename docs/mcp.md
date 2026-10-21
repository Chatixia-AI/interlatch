# MCP server

[← Interlatch](../README.md) · [Docs index](README.md)

Interlatch includes a [Model Context Protocol](https://modelcontextprotocol.io) server, so any MCP client can search
your past sessions and knowledge: Claude Code, Codex, Copilot, Bob, Antigravity, OpenCode, Claude Desktop, Cursor, Windsurf, Gemini CLI, or
anything else that speaks MCP. Ask things like *"have we hit this error before?"*, *"why did we put idempotency in
Postgres?"* or *"how is billing-api deployed?"*.

The server is `interlatch mcp`. It talks over stdio: the client starts it as a local process, and nothing listens on
the network. Every tool only reads the local vault.

## Tools

| Tool | What it returns | Arguments |
| --- | --- | --- |
| `resume` | Where a project stands for an agent starting work in it: the last session's goal, outcome and what it left open, what waits on you, and what other sessions did and which lessons became trusted since the asking agent's previous session there. Fits `max_chars` by leaving out whole items, and says how many. | `project`, `agent`, `max_chars` |
| `search_knowledge` | Knowledge items (fixes, gotchas, decisions, facts, commands, preferences) matching the words, the most trusted first, each labelled with its [stage](analysis.md#how-knowledge-earns-trust) (`established ×3`); a [case file](analysis.md#case-files) adds how the problem first showed and the leads ruled out. The best first stop. | `query`, `project`, `kind`, `limit` |
| `search_sessions` | Sessions whose transcript or summary matches, with snippets | `query`, `project`, `limit` |
| `get_session` | One session: summary, outcome, knowledge, files changed, prompts | `session_id` (or a unique prefix) |
| `get_transcript` | Part of a session's conversation, optionally with tool calls | `session_id`, `offset`, `limit`, `include_tools` |
| `project_knowledge` | A project's knowledge base: architecture, how to run, test and deploy it, gotchas, decisions, open threads. `project="global"` gives the cross-project playbook. | `project` |
| `glossary` | A term's definition, aliases, uses and sources, or a project's whole glossary | `term`, `project` |
| `find_artifacts` | What sessions made: documents, pages, diagrams, decks, published links, pull requests and commits, each with where it is, whether the file is still on disk as written, and the session that made it | `query`, `kind`, `project`, `limit` |
| `recent_sessions` | Recent sessions, newest first | `project`, `days`, `limit` |

`project` takes a path or a name (`billing-api`). When it is left out, `resume`, `project_knowledge` and `glossary` use the
project of the directory the client started the server in. Coding agents start it in your project; desktop chat apps
such as Claude Desktop don't, so name the project in your question there. `resume` knows which agent is asking from
the name its client gives when it connects (Claude Code, Codex, Copilot, OpenCode, Antigravity, Bob); another client can
pass `agent`.

All tools are marked read-only (`readOnlyHint`), so clients that honour the hint can run them without asking.

## Connecting a client

**Agents Interlatch records** get the server when you connect them (see [Sources](sources.md)):

| Agent | Registered in |
| --- | --- |
| Claude Code | user scope, with `claude mcp add` (`interlatch install` or `interlatch connect claude`) |
| Codex | `~/.codex/config.toml`, with `codex mcp add` (`interlatch connect codex`) |
| GitHub Copilot | VS Code `User/mcp.json` and `~/.copilot/mcp-config.json` (`interlatch connect copilot`) |
| IBM Bob (IDE and Bob Shell) | `~/.bob/settings/mcp.json` and `mcp_settings.json` (`interlatch connect bob`) |
| Google Antigravity | `~/.gemini/config/mcp_config.json` (`interlatch connect antigravity`) |
| OpenCode | `~/.config/opencode/opencode.json` (`interlatch connect opencode`) |

**Other clients** only get the server. Interlatch doesn't record their sessions. Add one from **Settings › MCP › Other MCP
clients**, or from the command line:

| Client | Command | Config file it edits |
| --- | --- | --- |
| Claude Desktop | `interlatch connect claude-desktop` | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Cursor | `interlatch connect cursor` | `~/.cursor/mcp.json` |
| Windsurf | `interlatch connect windsurf` | `~/.codeium/windsurf/mcp_config.json` |
| Gemini CLI | `interlatch connect gemini` | `~/.gemini/settings.json` |

Interlatch adds an `interlatch` entry and leaves the rest of the file alone. It backs the file up to
`~/.interlatch/backups/` first, and won't touch a file that isn't plain JSON (for example, one with comments).
Restart the client to load the server. `interlatch disconnect <client>` removes the entry again, and so does
`interlatch uninstall`. `interlatch sources` lists which clients have it.

## Any other client

**Settings › MCP** in the dashboard shows which agents have the server, the tools it offers, and ready-to-copy
config for most clients (an `mcpServers` entry), VS Code, Codex and Claude Code, with the right path for your
install. From a terminal, print an entry:

```bash
interlatch mcp --print-config
```

```json
{
  "mcpServers": {
    "interlatch": {
      "command": "/Users/you/.local/bin/interlatch",
      "args": ["mcp"]
    }
  }
}
```

Paste the `interlatch` entry into the client's MCP settings. Most clients use an `mcpServers` map like this one;
VS Code uses `servers`, and some clients call it something else, so check the client's documentation for the key.
Keep these in mind:

- **Use the full path.** Desktop apps don't see your shell's `PATH`. With the command-line install the path is
  `~/.local/bin/interlatch`. With only the desktop app, it's `~/.interlatch/bin/interlatch`, a small script that
  runs the app's bundled Interlatch.
- **Custom home.** If you set `INTERLATCH_HOME`, pass it too: `"env": {"INTERLATCH_HOME": "/path/to/home"}`.
- **Transport.** The server supports stdio only. A client that can only connect to a URL can't use it yet.

## Trying it by hand

The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) lists the tools and lets you call them:

```bash
npx @modelcontextprotocol/inspector ~/.local/bin/interlatch mcp
```

Or send raw JSON-RPC:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_knowledge","arguments":{"query":"webhook"}}}' \
  | interlatch mcp
```

## Privacy

The server runs on your Mac and reads only Interlatch's own database. But whatever a tool returns becomes part of the
client's conversation, and that conversation is sent to the client's model: Claude for Claude Desktop, and the model
you picked in Cursor, Windsurf or Gemini CLI. Tool results get the same secret redaction as the analysis digests
(API keys, tokens, passwords in URLs and similar). Everything else in a transcript can still reach the client's model,
so give the server only to clients whose model provider you are happy to show your sessions to. See
[Data and privacy](privacy.md).

## Troubleshooting

- **The tools don't appear.** Restart the client after connecting it. Check that the path in its config exists:
  `ls -l ~/.local/bin/interlatch`.
- **"No knowledge found" everywhere.** Sessions are analyzed in the background. `interlatch status` shows the queue.
- **Answers are about the wrong project.** Pass `project` explicitly, or ask about the project by name.

For more, see [Troubleshooting](troubleshooting.md).
