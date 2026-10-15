# FAQ

[← Interlatch](../README.md) · [Docs index](README.md)

Short answers to the questions people ask most, each linking to the page with the details. Something not working?
See [Troubleshooting](troubleshooting.md).

## Getting started

**What does Interlatch do that my agent's own memory doesn't?** An agent's memory (`CLAUDE.md`, `AGENTS.md`, its
memory notes) is what you or that agent wrote down, for that agent. Interlatch records every session from every agent
you use, keeps the transcripts after Claude Code deletes them (at 30 days), pulls out what was learned with a link to
the session it came from, and gives it to all your agents through one [MCP server](mcp.md). It can also propose
lines for your instruction files, which it writes only when you approve them ([Suggestions](suggestions.md)).

**Which agents does it record?** Claude Code, Codex, Codex Cloud, GitHub Copilot (the CLI and Copilot Chat in VS
Code), IBM Bob, Google Antigravity and OpenCode, plus chats you import from claude.ai and ChatGPT ([Sources](sources.md)).
Claude Desktop, Cursor, Windsurf and Gemini CLI aren't recorded, but they can use Interlatch's tools
([MCP server](mcp.md#connecting-a-client)).

**Do I need Claude Code?** No. Analysis runs through Claude Code, Codex or IBM Bob, or a model provider's API with
your own key: Anthropic, Amazon Bedrock, OpenAI, Azure OpenAI, OpenRouter, any OpenAI-compatible server, or Ollama on
your own computer. Pick one in **Status › Analysis** or with `interlatch config set analysis.backend <name>`
([Model providers](analysis.md#model-providers)). Recording and browsing work either way; with none set up, sessions
are archived and wait in the analysis queue.

**The desktop app or the command line?** The app (Apple silicon, macOS 13+) lives in the menu bar and runs its own
background sync. The command-line install (`uv tool install interlatch`, then `interlatch install`) runs the sync
and the dashboard as background agents, and is the one for an Intel Mac, Windows or Linux. Both use the same data in
`~/.interlatch` and can run side by side ([Install](install.md)).

**Windows or Linux?** The desktop app is macOS only. On Windows, `uv tool install interlatch` and `interlatch install`
work as on a Mac, with Task Scheduler running the sync and the dashboard ([Windows](install.md#windows)); a Windows
computer joining a team hub is untested yet. On Linux, `interlatch install` runs the sync and the dashboard as systemd user
units, so a Linux box can be the [hub](devices.md#a-linux-hub) for your other computers. A team's hub also runs
[in Docker](docker.md).

**Will it slow down my agent?** No. The session-end hook hands off to a detached process and returns in
milliseconds; analysis runs later in the background.

**What about the sessions I had before installing?** `interlatch install` imports the past sessions still on disk and
offers to analyze them: all of them, the newest 20, or later in the background. For Claude Code sessions older than
30 days, whose transcripts are gone, it recovers the prompts from `~/.claude/history.jsonl`. Set
`analysis.backfill = false` to analyze only sessions from after the install ([Configuration](configuration.md#analysis)).

## Privacy and cost

**What leaves my machine?** Only a condensed digest of each session, with secrets redacted, sent to whatever you
chose for analysis, through your own login or key (with Ollama, not even that). No telemetry, and nothing goes to
Interlatch's authors unless you join the leaderboard (below). The update check asks pypi.org for the latest version number only when you click it, unless
you turn on a daily check ([Data and privacy](privacy.md)).

**What does analysis cost?** Through Claude Code or Codex, analysis draws from your Claude or ChatGPT plan like any
other use of the agent; Bob and model providers bill your own account, and Ollama costs nothing. With Claude, in API
terms it averages about $0.38 per session with Sonnet. `interlatch analyze --pending --dry-run` sizes a backlog
before you spend anything, and `analysis.auto = false` stops automatic analysis
([How analysis works](analysis.md#how-analysis-works)).

**Does it change my `CLAUDE.md` or `AGENTS.md`?** Only when you approve a suggestion. It backs the file up first,
writes only inside its own block, and every change can be undone ([Suggestions](suggestions.md#what-interlatch-never-does)).

**Can I keep a project or a session out?** Add the project to `sources.exclude_projects` in the
[configuration](configuration.md); it is then never recorded, analyzed or sent to a hub. Remove a single session for
good with `interlatch forget <id>`.

**What is the leaderboard, and does it send my usage?** The **Leaderboard** compares your hours, sessions and
tokens with other people's. It is in Innerlatch, behind the door at the bottom of the dashboard's rail. Nothing is sent until you sign in there with Google. From then on, each sync sends your
totals for the last 7, 30 and 90 days, with the name and picture you show: no email, project, prompt or title.
`interlatch leaderboard leave` deletes your row and stops sending.

**What is the Party?** Your coding agents as pixel-art characters who level up from the lessons their sessions
taught, matched to a technical career roadmap ([Party](party.md)). It is in Innerlatch, with the Leaderboard and the
Arena. It needs a Google sign-in but sends nothing, and the levels are your agents', not yours.

**Is it open source?** Yes, under the MIT license, the hub included. Only `ee/`, the features a company needs to roll
it out across teams (single sign-on, policies, audit export), is under the
[Interlatch Enterprise License](../ee/README.md).

## Your agents

**How do my agents use what it learned?** Through the MCP server, `interlatch mcp`, which `interlatch install`
registers with each agent you connect. It runs as a local process over stdio, and its tools only read your archive.
Ask an agent *"have we hit this error before?"* or *"how is billing-api deployed?"* ([MCP server](mcp.md)).

**Does it write in Japanese?** Set `[analysis] language = "ja"` (or pick it in **Status › Analysis**) and summaries,
knowledge, glossary and weekly reviews are written in Japanese from then on. The dashboard's own language is chosen
per browser in **Settings › Appearance** ([Language](analysis.md#language)).

**Does it read Slack?** No. Interlatch reads only what your coding agents write on your computer, and the chats you
import. When a session published a Slack canvas, the session's [Artifacts](dashboard.md#artifacts) keep its link.

## Several computers and teams

**Can I use it on my phone or across several computers?** Yes. One computer is the hub: it records, analyzes and
serves the dashboard, and your other computers send it their sessions. Your phone opens the hub's dashboard
([Phone and other computers](devices.md)).

**Do I need Tailscale?** No. Tailscale is the quickest way between your own devices, but a hub works just as well on
a company network or VPN behind an HTTPS proxy
([Reaching the hub without Tailscale](devices.md#reaching-the-hub-without-tailscale)).

**What do my teammates see of my sessions?** With `--share knowledge`, the hub gets each analyzed session's summary
and project lessons, never your prompts, transcripts or files. A computer that joins without it sends its
transcripts, so an admin can make the hub take knowledge only ([Joining your team's hub](join-a-hub.md#what-is-shared),
[A hub that takes knowledge only](devices.md#a-hub-that-takes-knowledge-only)).

**Can I sync `~/.interlatch` with Dropbox or iCloud?** No: a SQLite database copied while it is in use can be
corrupted. Use a hub, or sync the agents' session folders with Syncthing instead
([Without a hub](devices.md#without-a-hub-syncthing)).

**Can I keep my data in Postgres?** Interlatch keeps its own database in SQLite and can keep a copy of it in a
Postgres database you choose, for SQL and BI tools ([A copy in Postgres](postgres.md)).

## Updating and removing

**How do I update?** **Settings › Status › Updates** updates Interlatch the way it was installed, and restarts the
dashboard. From a terminal, `uv tool upgrade interlatch`. For the app, download the new release
([Updating](install.md#updating)).

**I used Chronicle. What happens?** Interlatch was called Chronicle. Updating moves your install and data over by
itself, and the `chronicle` command keeps working ([Moving from Chronicle](moving-from-chronicle.md)).

**Is the dashboard at 11524 or 8765?** New installs use <http://127.0.0.1:11524/>. Installs made before 11524 became
the default stay on 8765 (`[server] port` in `config.toml`), and the app picks a free port when 11524 is taken
([Troubleshooting](troubleshooting.md#install-and-the-app)).

**How do I remove it?** `interlatch uninstall` removes the hooks, background agents and MCP registrations and keeps
your data; add `--purge` to delete the data too. With the app, also turn off **Open at Login** and delete it.
