# Roadmap

[← Interlatch](README.md) · [Changelog](CHANGELOG.md) · 日本語: [ROADMAP.ja.md](ROADMAP.ja.md)

What is coming to Interlatch, roughly in the order it will be worked on. Nothing here has a date. Items move between
sections as plans change, and finished work moves to the [changelog](CHANGELOG.md). Last updated 2026-10-08, at
version 0.15.0.

Three rules shape everything below. Interlatch stays local: no server of ours, no telemetry, no account. Analysis
runs on your own account: your Claude Code or Codex login, or your own API key for IBM Bob or a model provider,
never a key we hold. And Interlatch should say what it doesn't know rather than guess.

## Now

### Answers that say what they don't know

These come from reading [OpenRig](https://github.com/mvschwarz/openrig), which handles trust and missing data
carefully.

- **Trust that expires.** An established or canonical item that no session has confirmed for 90 days is shown as
  *seen once* until a new session confirms it. The stored stage doesn't change, so nothing is lost; the window
  will be a setting. Today a lesson about a tool you replaced months ago keeps being offered first.
- **Start-of-session notes that drop whole items.** The notes Interlatch gives Claude Code at the start of a
  session are cut at 3,000 characters, which can end mid-sentence and lose the pointer to the MCP tools. They will
  be filled item by item, most trusted first, and end with how many more there are and how to search for them.
- **Empty search results that say what was searched.** "No sessions matched" can mean it never happened, or that
  Interlatch never saw it: a Claude Code on the web session, a chat newer than your last export, or a session not
  analyzed yet. An empty result will say which sources were searched and which weren't.

### Fixes

- **Desktop app without the command-line install.** When the app is set up on its own, the Status and Sources
  pages report the background sync as off, and `analysis.backfill = false` is ignored on the first sync. Until
  this is fixed, run `~/.interlatch/bin/interlatch install --no-launchd --no-ui` once after connecting your
  agents in the app ([Troubleshooting](docs/troubleshooting.md)).
- **Clicking a new-version notification on macOS** opens Script Editor instead of the dashboard.

### Safer upgrades

- **Named upgrade steps.** The database records one schema version number. When two changes are developed at the
  same time, both can claim the same number and one upgrade step gets skipped (this was caught before 0.6.0). Each
  step will be recorded by name instead, so every step runs exactly once whatever order changes land in.

## Next

- **Tailscale and the phone, on real hardware.** A team's hub now runs on a real Linux server in Docker, with
  members' Macs joining over HTTPS. Tailscale access and the phone layout have still only been tried with automated
  tests and mobile emulation in a desktop browser, and need a run on a real tailnet and a real iPhone.
- **Knowing when a session has really ended.** Interlatch waits for a session to go quiet before analyzing it.
  Claude Code keeps a small status file for each running session, and reading it would let Interlatch analyze a
  session as soon as it closes and say exactly where it is still running. The file format is undocumented, so the
  quiet-period timer stays as the fallback.
- **Lessons with their reason attached.** Gotchas extracted in a fixed shape: the moment it bites, the reflex that
  goes wrong, what to do instead, and why. Agents get something they can act on, not just a fact. (The symptom and
  the leads ruled out are kept already, in [case files](docs/analysis.md#case-files).) Existing items keep their
  current shape unless you re-analyze them.
- **Lessons people learn from.** Gotchas, fixes and decisions already read as
  [case files](docs/analysis.md#case-files) that ask before they tell. Still to come: related cases side by side,
  and the weekly review bringing five back in that form, with no overdue count and no backlog. The agent keeps
  getting every lesson. Whether it works is measured by recall and by whether the same gotcha comes back in your
  sessions, not by asking, and every per-person measure stays on your computer
  ([design](docs/design/learning.md)).
- **Teammates' lessons, by person and side by side.** They show on project pages and in **All knowledge**, marked
  with the computers they came from. Still to come: the person's name instead of the computer's, teammates' lessons
  in the weekly review, and a lesson two people stated shown as both their cases
  ([design](docs/design/learning.md#4-the-team)).
- **Refreshing knowledge in long sessions** (optional). The start-of-session notes already come back after
  compaction. After many hours of work in one session, Interlatch could offer the relevant knowledge again, saying
  why it is doing so. It will be off by default, since it runs on every prompt.

## Later

- **Linked discussions.** When a session's agent reads a team-chat thread through an MCP tool, keep the thread's
  link (not its text) with the session, show it on the session page, and let knowledge cite it. That gives you
  "this discussion, this session, this code" without Interlatch ever connecting to Slack. An option will drop such
  tools' message text from the archive and keep only the links. It waits until real sessions use such a tool.
- **Remote access without Tailscale or a network of your own**, over a peer-to-peer connection with QR pairing. A
  hub on a company network or VPN already works without Tailscale
  ([Reaching the hub without Tailscale](docs/devices.md#reaching-the-hub-without-tailscale)); this is for when there
  is no such network. A design exists. It needs a browser-side peer, and its WebRTC dependency would be an optional
  install so the core package stays small.
- **More agents recorded.** Claude Desktop, Cursor, Windsurf and Gemini CLI can use Interlatch's MCP server today,
  but their sessions aren't recorded.
- **Windows.** Not supported yet. The command line and dashboard are mostly portable; background running and the
  desktop app are not.
- **Notifications on your phone** for new versions and finished analyses, without adding a server.
- **Growing as an architect.** Each case names the design principle it shows, from a fixed list based on
  architecture's quality attributes and tactics, with an open question and one to three readings. An Architect's
  track groups the cases by principle, each with a kata about your own project. Security lessons get a MITRE CWE
  ID and lessons about the agents themselves a NIST AI 600-1 risk name. Principles and readings come from lists,
  never from the model ([design](docs/design/learning.md#2-the-architects-lens-and-track)).
- **Your skills.** A private page of what you've worked on with your agents and, kept apart, what you've shown you
  know: cases answered weeks later, causes named before the agent named them, decisions you recorded. It follows
  the SFIA 9, NIST NICE and SWEBOK skill lists, never shows levels, and can be exported as a draft brag document.
  Nothing from it reaches the hub ([design](docs/design/learning.md#5-your-skills)).
- **More for the team on the hub.** Members react to a lesson (useful, outdated, I knew this), a case of the week
  on the team's Home, and a casebook for whoever is new to a project.

## Not planned

- **A hosted service, cloud sync or telemetry.** Interlatch's promise is that nothing leaves your machines except
  the analysis call through your own login or key. Sharing across computers goes through your own hub.
- **Computers connecting to a shared database.** One computer, the hub, writes; the others send it their sessions
  and never get a database's address or password. A hub may keep the team's record in your own Postgres as well
  ([team store](docs/devices.md#teammates-lessons-and-a-team-store-in-postgres)), but it stays the only writer.
- **Analysis with an API key we hold.** Analysis always runs on your own account: the Claude Code or Codex login
  already on your computer, or your own API key for IBM Bob or a model provider. With Ollama it never leaves your
  computer.

## Suggesting something

Open an [issue](https://github.com/Chatixia-AI/interlatch/issues) describing what you were trying to do and
where Interlatch got in the way. That helps more than a feature name, and it is how items get onto this page.
