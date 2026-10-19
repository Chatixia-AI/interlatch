# Command line

[← Interlatch](../README.md) · [Docs index](README.md)

| Command | |
| --- | --- |
| `interlatch install [--yes] [--backend NAME] [--analyze all\|N\|later] [--[no-]notify-updates] [--[no-]menu-bar] [--[no-]game] [--dry-run]` | Setup: asks what analyzes sessions (`--backend` answers it: a coding agent or a [model provider](analysis.md#model-providers), set up on the spot), lists the agents found, asks which to record, imports them, offers to analyze them now with progress (which builds the Glossary and the Map), starts the dashboard, and on macOS asks once whether to show [the menu-bar icon](install.md#the-menu-bar-icon) (off unless you say yes), and asks once whether to turn on [game mode](party.md#getting-there) (also off unless you say yes) ([Install](install.md#command-line)) |
| `interlatch app` | The desktop app (window + menu bar); needs the `app` extra |
| `interlatch ui [--open] [--menu-bar]` | Dashboard (also always running at :11524 after install; if it is, `interlatch ui` says so and `--open` opens it). On macOS the one that runs at login also shows [the menu-bar icon](install.md#the-menu-bar-icon) if you turned it on at install; `--menu-bar`/`--no-menu-bar` overrides that. Sessions, Knowledge, Projects and Glossary switch between Cards and List (a sortable table; click a row for details), remembered per page |
| `interlatch sessions [-p project] [--since 7d]` | List sessions |
| `interlatch show <id-prefix> [--transcript\|--markdown\|--json]` | Session overview or full conversation |
| `interlatch search <words>` | Full-text search over transcripts + knowledge (any language, 3+ chars) |
| `interlatch knowledge [query] [-k gotcha] [-p project]` | Browse extracted knowledge |
| `interlatch projects` / `interlatch stats [--since 30d]` | Per-project and overall statistics |
| `interlatch analyze <id> \| --pending [--limit N] [--dry-run] [--backend codex]` | Analyze now (`--dry-run` shows digest sizes, no tokens spent; `--backend` picks the agent or [model provider](analysis.md#model-providers) for this run only) |
| `interlatch synthesize [--project P] [--global] [--all]` | Rebuild knowledge bases |
| `interlatch export [--full]` | Rewrite the Markdown vault |
| `interlatch export <id>… [--format md\|json\|raw] [--out PATH]` | Export sessions: one file, or a .zip of several (`raw`: the original transcript) |
| `interlatch glossary [term] [-p project] [--rebuild --all] [--themes]` | Your vocabulary: internal names, acronyms, domain terms with definitions and usage; `--themes` groups big categories into themes for the Map |
| `interlatch systems [NAME] [--links] [--evidence] [--json]` | The [Systems map](dashboard.md#systems-map): every project as a system, grouped by folder, with what it runs on; NAME for one system's parts, connections and evidence (`--evidence`: with example commands) |
| `interlatch review [2026-W39\|current]` | Weekly engineering review written by the analysis model (automatic for each completed week) |
| `interlatch import <zip> [--analyze] [--screen]` | Import chats from a claude.ai or ChatGPT data export (the .zip, its folder, or `conversations.json`); repeatable. `--screen` screens them right after. See [Sources](sources.md) |
| `interlatch screen [--source chatgpt\|claude-ai] [--sample N] [--dry-run] [--redo]` | Sort imported chats into worth analyzing, maybe and not worth it, reading only each chat's opening ([Screening imported chats](sources.md#screening-imported-chats)) |
| `interlatch screen --list analyze\|maybe\|skip [--json]` / `--queue [--maybe]` | Show the chats screened as one verdict, with why; queue the ones worth analyzing (and the maybes) for the background analysis |
| `interlatch friction [-p project] [--days N] [--json] [--noise]` | What goes wrong: recurring failure causes across your sessions (sessions, projects, last seen, still happening, trend), then the tools that fail most; `--noise` also lists expected failures ([Suggestions](suggestions.md#what-goes-wrong)) |
| `interlatch suggest [-p project] [--all] [--json]` | Proposed fixes waiting for you: instruction lines, config changes, setup steps. Nothing is written until you apply one ([Suggestions](suggestions.md)) |
| `interlatch suggest show\|apply\|dismiss\|done\|undo ID… [--yes] [--reason R]` | Show the diff; apply it (asks first unless `--yes`, backs the file up); never propose it again; mark a setup step you ran as done; take an applied one back out |
| `interlatch suggest move ID --to user\|project` | Put a waiting line in your user-level file, or back in the files of the projects it came from; remembered for that lesson or cause ([Moving a line](suggestions.md#moving-a-line)) |
| `interlatch suggest refresh` | Look at the latest sessions and knowledge for suggestions now (the background sync does this after each run) |
| `interlatch forget <id> [--delete-transcript]` | Remove a session from the vault for good (it is never re-ingested) |
| `interlatch sources` | Which agents are connected, and how |
| `interlatch connect <agent>` / `disconnect <agent>` | Start/stop recording `claude`, `codex`, `codex-cloud`, `copilot`, `bob`, `antigravity` or `opencode` (data is kept) |
| `interlatch connect <client>` / `disconnect <client>` | Add or remove the MCP server in `claude-desktop`, `cursor`, `windsurf` or `gemini` |
| `interlatch mcp [--print-config]` | Run the MCP server (clients start it), or print a config entry for any other MCP client |
| `interlatch status` | Health: hooks, agents, MCP, queue, failures |
| `interlatch migrate [--dry-run]` | Move a Chronicle setup over: `~/.claude-chronicle` to `~/.interlatch`, and hooks, MCP servers, permission rules and background agents to the new names. Runs by itself when the dashboard, the sync, `install` or the app starts; `--dry-run` shows what it would change ([Moving from Chronicle](moving-from-chronicle.md)) |
| `interlatch tailnet on\|off\|status [--anyone]` | Open the dashboard on your phone and other devices through Tailscale Serve, to your Tailscale login only (`--anyone`: everyone in your tailnet) ([Phone and other computers](devices.md#your-phone)) |
| `interlatch mirror [status]` / `mirror sync [--full]` | The copy of this archive in Postgres: where it is, what each table holds and the last write; or write it now (`--full`: every row again) ([A copy in Postgres](postgres.md)) |
| `interlatch hub enable [--rotate]` | Make this computer the hub for your others; prints the `interlatch hub join` command to run on them |
| `interlatch hub join <address> --token <token> \| --code <code> [--share knowledge] [--all-folders] [--restore\|--no-restore]` / `hub leave` | Send this computer's sessions to a hub instead of recording them here, or stop ([Your other computers](devices.md#your-other-computers)). A computer in one hub leaves it before it joins another; `hub leave` puts aside what it had for the hub (folders, left projects, shared groups) and removes the hub's lessons, and joining that same hub again offers to bring them back (`--restore`/`--no-restore` answer without asking) ([Leaving](devices.md#leaving)). `hub set-url <address> [--yes]`: the same hub at a new address, keeping everything. `--code`: the invite code an admin gave you ([Joining a computer](devices.md#joining-a-computer)). `--share knowledge`: keep recording and analyzing here and send only summaries and project lessons ([Sharing knowledge only](devices.md#sharing-knowledge-only)); always the case for someone limited to projects. Sharing knowledge, it shares only sessions in the hub's projects unless `--all-folders` |
| `interlatch hub status` / `hub disable` | The computers sending to this hub; stop accepting them |
| `interlatch hub project add <folder>` / `hub project remove <folder>` / `hub project list` | On the hub: set up a project before anyone sent to it (that folder on the hub computer and everything below it, named after the folder), undo one, or list the hub's projects with the ones set up here marked ([Projects and who sees them](devices.md#projects-and-who-sees-them)) |
| `interlatch hub invite <name> [--email <email>] [--role admin\|member\|readonly] --project <name>… \| --all-projects` | On the hub: add a person and make a one-time invite code ([Inviting someone](devices.md#inviting-someone)). `--project` (repeat it for more) or `--all-projects`: the projects a member or read-only person sees; a new one needs one of the two, an admin sees every project. Run again for someone already on the hub: a new code. `--computer <name or id>`: a code for that one computer the hub already knows, when it can't show its key ([Joining a computer](devices.md#joining-a-computer)) |
| `interlatch hub access <email\|id> --project <name>… \| --all-projects` | On the hub: change which projects someone sees, from their next request. It replaces the list: name every project they keep ([Projects and who sees them](devices.md#projects-and-who-sees-them)) |
| `interlatch hub purge <email\|id\|computer> --project <name>… \| --outside-access [--yes]` | On the hub: remove for good what a person's computers (or one computer) sent, in those projects or outside the ones the person sees ([Taking back what a computer sent](devices.md#taking-back-what-a-computer-sent)) |
| `interlatch hub people` / `hub role <email\|id> <role>` / `hub remove <email\|id>` / `hub shared-token on\|off` | On the hub: everyone with their role, the projects they see and their computers; change a role; remove someone; allow or refuse the shared token ([People and roles](devices.md#people-and-roles)) |
| `interlatch hub add-folder <folder> --project <name>` / `hub remove-folder <folder>` / `hub folders [--list]` | On a computer that joined a hub: file a folder's sessions under a project on the hub, take a folder added by mistake back out, or show what goes where (`--list`: the hub's projects) ([Same project, different folders](devices.md#same-project-different-folders)). Sharing knowledge, `remove-folder` also has the hub delete what the computer shared from that folder ([Leaving](devices.md#leaving)) |
| `interlatch hub leave --project <name>` / `hub rejoin --project <name>` | On a computer that shares knowledge: stop sharing to one of the hub's projects and getting its teammates' lessons, keeping what it already shared there; or start again ([Leaving](devices.md#leaving)) |
| `interlatch push` | On a computer that joined a hub: send its new sessions now (the hook and the background sync do this) |
| `interlatch container` | What the hub's Docker image runs: set the hub up from `INTERLATCH_*` variables, then serve the dashboard and sync every 15 minutes ([A hub in Docker](docker.md)) |
| `interlatch config [edit]` | Show or edit `~/.interlatch/config.toml` |
| `interlatch config set <section.key> <value>` | Change one setting, e.g. `interlatch config set analysis.backend codex` or `providers.ollama.model qwen3:30b` ([Configuration](configuration.md)) |
| `interlatch config set-key <provider> [KEY]` / `forget-key <provider>` | Store a model provider's API key, or IBM Bob's (`bob`), in `provider-keys.json` (asked for when left out), or remove it |

## Other ways in

**Markdown vault:** `~/.interlatch/notes` (open it as an Obsidian vault): `Home.md`,
`Sessions/YYYY/MM/*.md` with YAML frontmatter, `Projects/*.md` (knowledge base + session list),
`Knowledge/<Kind>.md`, `Reviews/YYYY-Www.md`, `Glossary.md`, `Global Playbook.md`.

**Inside your agents** (MCP tools, registered in Claude Code and in each connected agent): `search_knowledge`, `search_sessions`, `get_session`,
`get_transcript`, `project_knowledge`, `glossary`, `find_artifacts`, `recent_sessions`. Ask e.g. *"have we hit this error before?"*
or *"what is the deployer_ip rule?"*.

The MCP server is registered when you connect an agent (see [Sources](sources.md)). [MCP server](mcp.md) covers
each tool and how to connect other clients.
