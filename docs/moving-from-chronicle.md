# Moving from Chronicle

[← Interlatch](../README.md) · [Docs index](README.md)

Interlatch was called Chronicle. It is the same program under a new name: your sessions, knowledge, settings and
hub keep working, and moving takes one install.

## Update

**From the command line:** the package on PyPI is now `interlatch` (it was `agents-chronicle`). **Update** in
**Settings › Status › Updates** makes the switch for you, even when the version is the same, and keeps your extras.
By hand, uninstall first, since the two packages' commands clash, and add the extras you had
(`uv tool list --show-extras` shows them):

```bash
uv tool uninstall agents-chronicle
uv tool install --python 3.13 'interlatch[app]'   # or plain interlatch, without the desktop app
```

With pipx it is `pipx uninstall agents-chronicle`, then `pipx install interlatch`. Until you switch,
`uv tool upgrade agents-chronicle` keeps working: the last `agents-chronicle` release installs Interlatch and keeps
both commands. An install from a checkout or a git URL is not switched by the button: uninstall it and install
`interlatch` the same way.

**The desktop app:** download `Interlatch-<version>-arm64.dmg` from the
[latest release](https://github.com/Chatixia-AI/interlatch/releases/latest), drag **Interlatch** into
**Applications** and open it. It is a new app to macOS, so Chronicle.app stays where it was: delete it once
Interlatch has started (its Open at Login item goes with it).

## When it moves

The move runs by itself when `interlatch ui`, `interlatch sync` or `interlatch install` starts, and when the app
opens: after an update, the dashboard or the background sync does it within minutes. Session hooks and the MCP server
don't start it, and don't need to: they find the data folder under either name.

`interlatch migrate` runs it by hand and prints what it changed; `interlatch migrate --dry-run` shows what it would
change and changes nothing. Running it again changes nothing. Each move is recorded in `~/.interlatch/logs/migrate.log`.
A move that was cut off (the computer slept, the process was stopped) carries on the next time one of those commands
starts; `migration.json` in the folder keeps track of it.

**Your own data folder.** A folder you chose with `CHRONICLE_HOME` or `INTERLATCH_HOME` is never moved; only its
integrations are renamed. A variable that names `~/.claude-chronicle` itself counts as the usual folder and is moved:
Chronicle's background agents set exactly that.

## What it changes

| What | Before | After |
| --- | --- | --- |
| Data folder | `~/.claude-chronicle` | `~/.interlatch`, with a link left at the old path so anything that still names it keeps working; the paths the database keeps are updated too |
| Claude Code | hooks, status line and MCP server `chronicle` | the same, running `interlatch`; the MCP server is registered again as `interlatch` |
| Codex | `[mcp_servers.chronicle]` in `config.toml` | `[mcp_servers.interlatch]`, renamed in place with its settings |
| MCP server in VS Code, Copilot CLI, IBM Bob, Antigravity, Claude Desktop, Cursor, Windsurf and Gemini CLI | `chronicle` | `interlatch`, renamed in place with its settings |
| Claude Code permission rules, in your `settings.json` and `settings.local.json` and in each project's `.claude/settings.local.json` | `mcp__chronicle__…` | `mcp__interlatch__…`, so agents aren't asked again |
| The block Interlatch manages in `CLAUDE.md` and `AGENTS.md` | `<!-- BEGIN chronicle -->`, `<!-- chronicle:friction:… -->` | `<!-- BEGIN interlatch -->`, `<!-- interlatch:friction:… -->` |
| Background agents (macOS launchd) | `com.claude-chronicle.sync`, `com.claude-chronicle.ui` | `com.interlatch.sync`, `com.interlatch.ui` |
| Background agents (Linux systemd) | `chronicle-sync.timer`, `chronicle-ui.service` | `interlatch-sync.timer`, `interlatch-ui.service` |

Every file it edits is backed up to `~/.interlatch/backups/` first, as when you connect an agent. The database keeps
its name (`chronicle.db`), and so do the other files in the folder.

## What to do by hand

The move reports these and leaves them to you:

- **A project's shared `.claude/settings.json`** that allows `mcp__chronicle__…` tools. It is committed to the
  project, so others may still run Chronicle: rename the rules to `mcp__interlatch__…` once everyone has moved.
- **MCP servers you added to one project**, in its `.mcp.json` or as a project's server in `~/.claude.json`: rename
  the `chronicle` entry `interlatch`. Left as it is, the agent gets the same tools twice, under both names.
- **Chronicle.app**, reported as one you can delete; it is never deleted for you.

The old `bin/chronicle` script in the data folder stays, as a copy that runs Interlatch, for anything that still
calls it.

## What keeps working

- **The `chronicle` command.** It is the same program as `interlatch`, so scripts and habits that call `chronicle …`
  still work. The docs use `interlatch`.
- **Environment variables.** Each `CHRONICLE_*` variable is still read; its `INTERLATCH_*` name (`INTERLATCH_HOME`,
  `INTERLATCH_HUB_URL`, …) wins when both are set.
- **Hubs.** A hub and the computers that send to it can move in any order: old and new versions keep talking to each
  other, and invites, sign-ins and tokens stay valid. A [hub in Docker](docker.md#update) moves with the current
  `compose.yaml`, which runs the image `ghcr.io/chatixia-ai/interlatch-hub`; its `.env`, volumes and data stay as
  they are, and its `/data` folder is never moved. One that keeps its old `compose.yaml` keeps getting releases too,
  under the old image name.
- **A copy in Postgres** and a hub's team store keep their schemas and tables.
- **Links.** chronicle.chatixia.net now forwards to [interlatch.com](https://interlatch.com).

## The VS Code extension

The Chronicle extension keeps working with Interlatch. Its successor is the Interlatch extension, with **Interlatch: …**
sections and `interlatch.*` settings: [install it](vscode.md#install), and while the old one is still there it offers
once to uninstall it. The `chronicle.*` settings you made keep applying until you set their `interlatch.*` ones.

Something not working? See [Troubleshooting](troubleshooting.md#moving-from-chronicle).
