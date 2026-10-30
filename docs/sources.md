# Sources: Claude Code, Codex, GitHub Copilot, IBM Bob, Google Antigravity, OpenCode

[← Interlatch](../README.md) · [Docs index](README.md)

`interlatch sources` (or the dashboard's **Sources** tab) shows each coding agent: detected or not, version,
sessions on disk vs. recorded and analyzed, how it is recorded, and whether its hook and MCP server are in
place. Connect or disconnect from the dashboard or with `interlatch connect <agent>` / `interlatch disconnect <agent>`
(`claude`, `codex`, `codex-cloud`, `copilot`, `bob`, `antigravity`, `opencode`; recorded sessions are always kept). Every source maps onto the same session
model, so sessions from all agents share the dashboard, analysis, knowledge bases, glossary and MCP tools.

**Codex** (`~/.codex`) is opt-in. Connecting it:

- records `sources.codex_dirs` in the config, archives `~/.codex/sessions` (rollouts), `session_index.jsonl`
  and Codex's own memory notes, and parses rollouts into the same session model: prompts (the IDE-context
  envelope is unwrapped), replies, tool calls (`exec_command`, `apply_patch`, `write_stdin`, web, MCP, subagents;
  a code-mode `exec` script is named after the tool it calls), file edits (`FileChange` items or patches), real
  exit codes (result chunks, `CommandExecution` items, and long-running cells finished by `wait`), token usage
  per response, compactions and interrupts;
- registers the MCP server in Codex (`codex mcp add interlatch`), so Codex can search every session, Claude's too;
- **recovers Claude Code sessions** that Codex Desktop imported: when Claude Code has already deleted the
  original transcript, Codex's copy becomes a full session (`source = codex-import`); duplicates are skipped.

Codex has no session-end hook (and its single `notify` slot may be taken by another app), so Codex sessions
are picked up by the 15-minute background sync once idle. GPT token costs use OpenAI list prices; newer GPT
models without a published price are estimated at GPT-5 rates.

**Codex Cloud** (tasks at chatgpt.com/codex) is a separate opt-in (`interlatch connect codex-cloud`), because it
goes online: every sync runs `codex cloud list` with your Codex login (`codex login`), then `codex cloud diff` for
each new or changed task. Each task becomes a Codex session (`source = codex-cloud`) with its title, repository,
status, changed files with line counts, the diff and a link to the task; it joins your local project when the
repository name matches one. The Codex CLI does not give a cloud task's conversation, so these sessions have no
prompts and are not analyzed. The task and its diff are archived in `~/.interlatch/archive/codex-cloud/` and
stay after the task expires in the cloud. If listing fails (not logged in, offline), the Sources card says why and
the next sync tries again.

**GitHub Copilot** is opt-in (`interlatch connect copilot`). It records two stores:

- Copilot agent sessions in `~/.copilot/session-state/<id>/events.jsonl` (Copilot CLI and VS Code's Copilot
  agent host): prompts, replies, reasoning, tool calls with results and durations, and line totals. Token usage
  per call (with cache splits) comes from `~/.copilot/session-store.db`, archived as a SQLite snapshot.
- Copilot Chat in VS Code: `User/workspaceStorage/<hash>/chatSessions/*.jsonl`. Each file is a patch log that is
  replayed into the final chat. The agent loop gives tool calls, results and edits; empty chat panels are skipped.
  These logs do not split cached input from uncached input, so they carry token counts but no cost estimate.

Connecting also registers the MCP server in VS Code (`User/mcp.json`) and the Copilot CLI (`~/.copilot/mcp-config.json`).
Both files are backed up to `~/.interlatch/backups/` first, and other servers in them are kept.

**IBM Bob** is opt-in (`interlatch connect bob`). It reads tasks and messages from `~/.bob/db/bob.db`, read-only,
and archives a SQLite snapshot of that database; nothing else in `~/.bob` (e.g. login state) is read. The Bob IDE and
Bob Shell (the `bob` command) both keep their tasks in that database, so tasks from either are recorded; the IDE keeps
no other conversation files locally. Connecting registers the MCP server in `~/.bob/settings/mcp.json`, which Bob 2.x
and Bob Shell read, and in `~/.bob/settings/mcp_settings.json` for older Bob IDE versions. If `mcp.json` doesn't exist
yet, it is first copied from `mcp_settings.json`, as Bob itself does, so your other MCP servers carry over.

**Google Antigravity** is opt-in (`interlatch connect antigravity`). Antigravity keeps each conversation in its own
store under `~/.gemini/antigravity/conversations/` (encrypted in older versions, a SQLite database in newer ones), and
also writes a plain step log beside the agent's artifacts: `brain/<id>/.system_generated/logs/transcript_full.jsonl`
(`transcript.jsonl` in older versions). Interlatch reads that log: prompts (artifact approvals included), replies,
thinking, tool calls with their results and durations, background-task notices and tokens per model call. From a
newer version's conversation database, opened read-only, it adds the workspace folder, git branch and remote, and the
model; the title comes from `annotations/<id>.pbtxt`. The log and the task, plan and walkthrough Markdown files are
archived, generated images are not. A conversation kept only in the encrypted store cannot be read; the Sources card
counts those. Antigravity records no prices, so the cost of a session on a Gemini model is estimated at Google's
Gemini API list prices (a Gemini model without a published price at those of its kind: Flash, Flash-Lite or Pro),
as are GitHub Copilot sessions on Gemini models. Connecting
registers the MCP server in `~/.gemini/config/mcp_config.json`, Antigravity's global MCP config.

**OpenCode** is opt-in (`interlatch connect opencode`). OpenCode 1.2 and later keep every session in one SQLite
database, `~/.local/share/opencode/opencode.db` (under `$XDG_DATA_HOME` when set, on Windows too; preview builds use
`opencode-<channel>.db`, which is read as well); the desktop app writes the same file. Interlatch opens it read-only and
reads only the session and message tables (`session`, `message` and `part` in 1.x; `session_v2` and `session_message`
in 2.x): prompts (attached images are counted; the file contents OpenCode adds to a prompt are not your
words and are left out), replies, reasoning, tool calls with their input, output or error and duration, file edits
with line counts (`edit`, `write`, `apply_patch` or `patch`), commands you ran with `!`, tokens and cost per model call, retried API errors, interrupts and
compactions. A subagent (the `task` tool in 1.x, `subagent` in 2.x) runs in a session of its own; it is recorded inside the session that started
it. A call OpenCode priced at 0 (a subscription or a free model) is estimated at list price. Each session is archived as
one JSON document in `~/.interlatch/archive/opencode/`; the database itself is not copied, since it also holds
OpenCode's credentials. Sessions from before 1.2 (JSON files under `storage/`) are read once OpenCode has moved them
into the database, which it does on its first start. OpenCode has no session-end hook here, so sessions are picked up
by the 15-minute sync. Connecting registers the MCP server in `~/.config/opencode/opencode.json` (OpenCode merges it
with `opencode.jsonc`, which is never rewritten).

**Chats on claude.ai and ChatGPT** are not stored on your Mac. The browser extension saves them as you use the sites
([below](#saving-chats-as-you-use-the-sites)); older chats come in from a data export: on claude.ai,
**Settings › Privacy › Export data**; on ChatGPT, **Settings › Data controls › Export data**. The email's link
downloads a `.zip`. Import it with **Import export…** on the Sources page (card *Web chats*), or
`interlatch import <zip>` (the unpacked folder or `conversations.json` work too); which service it came from is
recognized from its contents. Each chat becomes a session (agent `claude-ai` or `chatgpt`, `source =
claude-ai-export` or `chatgpt-export`) with its prompts, replies, thinking, tool calls (claude.ai artifacts,
ChatGPT's Python, browsing and image steps, with their output) and notes of attached files and images. claude.ai
chats in a project are grouped under `claude.ai/<project>`, the rest under `claude.ai`; ChatGPT chats under
`chatgpt.com` (or `chatgpt.com/<project>`, when the browser extension saved it). A ChatGPT chat is read along the branch ChatGPT shows, so edited prompts and regenerated answers you
moved away from are left out. Import a newer export any time: new and changed chats are added, unchanged ones
skipped. Only the chats are read and archived (`conversations.json`, and claude.ai's `projects.json` for project
names); the account files (`users.json`, `user.json`) and ChatGPT's `chat.html` are never opened, and a zip uploaded
from the dashboard is deleted once imported. Imported chats are **not analyzed automatically**, since years of chats
would use up your plan's limits at once: screen them (below) and queue the ones worth it, tick the chats you want
in the Sessions list and choose **Analyze** (**Select all matching** takes every chat the filters show), open one and
choose **Analyze now**, or import with `--analyze` to queue them all. The exports have no token counts, so chats show
no cost. Claude Code on the web sessions are not in the claude.ai export; `claude --teleport <id>` brings one onto your
Mac as an ordinary Claude Code transcript. Codex Cloud tasks are a separate source (above).

### Saving chats as you use the sites

The browser extension saves claude.ai and ChatGPT chats as you use the sites, so new chats need no new export. It
works in Chrome, Edge, Brave and other Chromium browsers. It isn't in the Chrome Web Store: you load it from a folder
the dashboard keeps for it.

**Settings › Browser extension** in the dashboard walks you through it, for Chrome, Edge or Brave, and shows what the
extension has sent so far. In short:

1. Open `chrome://extensions` (Edge: `edge://extensions`) and turn on **Developer mode**.
2. Choose **Load unpacked** and pick `~/.interlatch/browser-extension` (the page has the path to copy).
3. Open claude.ai or chatgpt.com.

While a tab of either site is open, the extension asks the site which chats changed since it last looked: when the
tab loads, every 5 minutes, and a few seconds after you leave a chat (for another chat, tab or app; at most once a
minute). It reads each changed chat the way the site's own page does, with your sign-in, and
sends it to the dashboard on this computer. The first sync goes back 30 days (change it in the extension's popup);
older chats come in from an export. A sync reads at most 40 chats and the next follows two minutes later, so a first
sync of a few hundred chats takes a while; when a site asks it to slow down, it waits ten minutes. Temporary chats
are left out. Chats in a ChatGPT project are grouped under `chatgpt.com/<project>`.

A saved chat is the same session an export updates, stored and archived the same way (its latest copy in
`archive/<claude-ai|chatgpt>/captured/<id>/`). Like imported chats, saved chats are **not analyzed automatically**:
screen them (below) and queue the ones worth it, or choose **Analyze now** on one.

The extension's toolbar button shows whether it reaches the dashboard, each site's last sync and any error, with
**Sync now**, **Paused**, the dashboard's port (11524 unless you changed `[server] port`) and how far back the first
sync goes. It reads only claude.ai and chatgpt.com and sends only to `http://127.0.0.1`. The dashboard takes chats
only from this computer, with a header no web page can send. The extension uses the sites' internal endpoints, not a
published API: when a site changes them, the toolbar button shows the error until an Interlatch update catches up.
After an Interlatch update the dashboard refreshes the folder, and Chrome loads the new version when it restarts (or
with the reload button on `chrome://extensions`).

### Screening imported chats

Most of a chat history is lookups, rewrites and everyday questions that analysis would turn into nothing. Screening
sorts the chats into **worth analyzing**, **maybe** and **not worth it**, with a topic and a one-line reason each,
so the analysis goes where it pays. Choose **Screen N chats** on the export's card (Sources › Web chats), or run
`interlatch screen` (`--sample 200` tries it on a random 200 first, `--dry-run` only counts).

- It reads only each chat's opening: title, date, the first and last prompt and the start of the first reply,
  redacted like everything sent for analysis.
- Rules settle what is certain without a model call: no reply in the export (usually an image request), too little
  to analyze, or a one- or two-prompt request to translate, summarize or proofread pasted text.
- The rest goes to `analysis.screen_model` (Haiku by default), 60 chats a call. It is told what analysis keeps
  (fixes, decisions, facts about your own projects and work, preferences) and which projects you work on, taken from
  your recorded coding sessions, so chats about your own systems, employer or clients rank above generic questions.
  A chat wrongly skipped would never be analyzed, so the model is told to keep anything about your work at least
  *maybe*.
- About 3,400 ChatGPT chats take some 60 calls; with Claude the reported cost is a few dollars at API list price, drawn
  from your plan.

Nothing is analyzed by screening. **Queue N worth analyzing** (or `interlatch screen --queue`, `--maybe` for the maybes
too) puts them in the background queue, which analyzes a few every 15 minutes, newest first. The verdicts link to the
Sessions list (filter *Screening*), where each chat shows its verdict and reason until it is analyzed; review the
maybes there and **Analyze** the ones you want. `interlatch screen --list analyze|maybe|skip` shows the same on the
command line. A chat is screened once, and again when a newer export changes it; a queued chat stays queued.

### Which project a chat is about

A web chat stays under `claude.ai` or `chatgpt.com`, but Interlatch suggests which of your coding projects it most
likely belongs to. The chat's session page shows it under the title, for example *Project: likely billing-api (medium
· by rule · mentions billing-api)*, with **Confirm** and **Change…** (another project, or *No project*).

- **Rules**, with no model call, when a chat is imported or saved and on every sync: its claude.ai or ChatGPT project
  has a coding project's name (or its folder's or git repository's), or the chat names one of them, its folder, or
  terms only that project's glossary uses. Two projects named alike give no suggestion. A chat changed since is
  checked again.
- **Screening** picks one of your 25 most recent projects, or none, for the chats the rules left open.
- **A chat's full analysis** picks too, and corrects what the rules or screening said.
- **You**: Confirm or Change. Nothing overwrites your answer.

A project's page lists the chats you confirmed under **Sessions › Web chats about this project**, and their knowledge
with the project's. The knowledge itself stays the chat's, so where it is shared (hubs, profiles) doesn't change.

Claude Desktop, Cursor, Windsurf and Gemini CLI aren't recorded, but they can use the MCP server too. See
[MCP server](mcp.md).
