# Troubleshooting

[← Interlatch](../README.md) · [Docs index](README.md)

Start with `interlatch status` (or **Settings › Status** in the dashboard). It checks the hook, the background
agents, the MCP server and the `claude` CLI, and lists recent analysis failures. Logs are in
`~/.interlatch/logs/` (`chronicle.log` for everything, `hooks.log` for session-end hooks).

## Install and the app

**macOS says Interlatch "cannot be opened" or "cannot verify the developer".** That release was not notarized. Open
**System Settings → Privacy & Security**, click **Open Anyway** next to the Interlatch message, and confirm.

**Status and Sources say *Background sync* is not running, but I use the app.** They only check the launchd agent
that the command-line install sets up. The app runs its own 15-minute sync; the menu-bar menu's first line shows when it last ran.

**`analysis.backfill = false` does nothing with the app.** The app's Connect step does not record the install date
that `interlatch install` records, so sessions from before connecting are analyzed too. Run
`~/.interlatch/bin/interlatch install --no-launchd --no-ui` once: it records the date and reinstalls the hook
and MCP server.

**The app window can't be dragged.** Drag by the toolbar's empty space or the strip beside the traffic lights;
buttons and links in the toolbar only click. From source, run `uv run --extra app interlatch app` with a current
checkout.

**The menu bar says "python3" instead of Interlatch.** Only when running the app from source with an old checkout;
the DMG build always shows Interlatch.

**The menu-bar icon doesn't show, though its switch is on.** The switch (**Settings › Status › Recording**) only
puts the icon there; macOS decides where it appears:

- Turned on from the dashboard of 0.21.0 or earlier, it can stay hidden: that version restarted the dashboard in a
  way that left macOS hiding the icon. Restart the dashboard (last item) and it shows.
- **System Settings › Menu Bar › Allow in the Menu Bar** lists a command-line install's dashboard by the Python it
  runs on (`python3.14`, say), not as Interlatch. Turn that entry on.
- On a MacBook with a notch, icons that don't fit beside the notch are hidden. Quit an app with an icon you don't
  need, or hold ⌘ and drag icons to the right to make room.
- Restart the dashboard: `launchctl kickstart -k gui/$(id -u)/com.interlatch.ui`.

**The dashboard isn't at :11524.** The app uses a free port when 11524 is taken (for example by the command-line
install's dashboard agent). **Open in Browser** in the menu-bar menu opens the right one. Installs made before
11524 became the default stay on :8765 (`[server] port` in `config.toml`).

**`interlatch ui` fails with "Address already in use", or the dashboard still shows the old version after
reinstalling.** The command-line install's launchd agent already serves the dashboard at :11524 (:8765 on older installs) and keeps running
the code it started with. Restart it with `launchctl kickstart -k gui/$(id -u)/com.interlatch.ui`, or update
from **Settings › Status › Updates**, which restarts it for you ([Updating](install.md#updating)). Use
`interlatch ui --port <n>` for a second dashboard.

## Recording

**New sessions don't show up.** Claude Code sessions arrive through the `SessionEnd` hook within seconds, and
everything else with the 15-minute sync. Check `interlatch status` for the hook, then run `interlatch sync` to pick
things up now. Codex has no session-end hook, so its sessions appear once they have been idle for a while.

**Old Claude Code sessions are missing.** Claude Code deletes transcripts after 30 days. Interlatch keeps everything
it has seen, and recovers prompts (only) of older sessions from `~/.claude/history.jsonl`, marked *history*.

**A project I don't want recorded.** Add a glob to `sources.exclude_projects` in the config, or remove a session for
good with `interlatch forget <id>`.

## Analysis

**Nothing gets analyzed.** Open the session: under its summary it says why it is waiting, and **Status ›
Analysis** (or `interlatch status`) counts sessions per reason. Analysis needs what you chose for it to be ready:
the `claude` (or `codex`) CLI logged in, a Bob API key for IBM Bob, or a model provider's key (**Test connection**
in **Status › Analysis** checks it).
`interlatch status` shows where it found `claude`; the app reads your login shell's PATH, so a `claude` installed
with npm or Homebrew is found too. A session is analyzed once it ends or has been idle for `analysis.idle_minutes`.

**Analysis stopped with "usage limit".** Interlatch pauses analysis for an hour when Claude reports a usage limit or
an auth error, and resumes by itself. Other failures back off (30 min, 2 h, 8 h). **Analyze now** on a session
page retries that session at once, and **Analyze N now** in **Status › Analysis** retries the whole ready queue;
either one lifts the pause when it gets through.

**I want to see what a backlog would cost first.** `interlatch analyze --pending --dry-run` sizes the queue without
spending tokens; `analysis.max_budget_usd` caps each call, and `analysis.auto = false` stops automatic analysis.

## A hub

**The hub's dashboard says "this dashboard has no people yet".** The hub has no people, so its dashboard has no
sign-in and opens only at the hub itself (and for Tailscale logins, [Your phone](devices.md#your-phone)). At the
hub, add yourself: `interlatch hub invite <your name> --email <email> --role admin`, then open the invite link it
prints ([People and roles](devices.md#people-and-roles)).

**`interlatch hub join` says "this computer already joined this hub as someone else".** The computer still holds
another person's token on the hub. An admin uses **Revoke** next to that computer in **Team › People**, then the
computer joins with the new code ([Joining a computer](devices.md#joining-a-computer)).

**`interlatch hub join` says "this hub already knows a computer with this id … and can't tell this is it".** The
computer sent to the hub before, and the hub has no key on record from it, or another one. An admin makes an invite
for that computer, with the id the message shows: `interlatch hub invite <name> --computer <id>`; join with that code
([Joining a computer](devices.md#joining-a-computer)).

**A computer's sessions don't show up on the hub.** On a hub, a session belongs to the computer that sent it first:
the same session from another computer is refused, and the hub's log says "refused: it came from …" (shared
sessions) or "not stored: it belongs to …" (transcripts). This happens
when a Claude Code folder was copied from one computer to another. The first computer's copy stays on the hub.

**A push fails with "too large once unpacked".** The hub unpacks what a computer sends only up to a limit (256 MB
for one batch of shared sessions, 1 GB for analyses). Only a damaged or crafted upload reaches it.

**An invite or sign-in link says "too many wrong codes from this address".** After five codes the hub never issued
from one address within 15 minutes, each further try from there waits a little longer: 1 second, then 2, 4 and so on,
up to a minute. Wait that long, then paste the code again, exactly as it was sent. A code that was already used or has
expired doesn't count. Behind your own proxy, add its address to `[server] trusted_proxies` (`INTERLATCH_TRUSTED_PROXIES`
in Docker) so the hub tells visitors apart rather than counting them all as the proxy.

## Moving from Chronicle

[Moving from Chronicle](moving-from-chronicle.md) says what the move changes. Each one is recorded in
`~/.interlatch/logs/migrate.log`, and `interlatch migrate --dry-run` shows what is still to do.

**After Update, the dashboard never came back, and `interlatch` is "command not found".** Update in 0.22.0 removed
`agents-chronicle`, which took the `interlatch` and `chronicle` commands with it, and didn't put them back. The
background agents, the session hooks and the MCP server all run that command, so they stopped too. Put the commands
back with the extras you had (`uv tool list --show-extras` shows them), then restart the dashboard:

```bash
uv tool install --force --python 3.13 'interlatch[app]'   # or plain interlatch, without the desktop app
launchctl kickstart -k gui/$(id -u)/com.interlatch.ui     # Linux: systemctl --user restart interlatch-ui.service
```

**The data folder is still `~/.claude-chronicle`.** The move runs when the dashboard, the background sync,
`interlatch install` or the app starts; run `interlatch migrate` to do it now. It leaves the folder where it is when
`CHRONICLE_HOME` or `INTERLATCH_HOME` names another folder (it says so), when `~/.interlatch` already exists as well
(Interlatch then uses `~/.interlatch`; move what you need yourself), and when the folder can't simply be renamed, for
example because it is a mount point or on another disk.

**An agent shows `mcp__chronicle__…` tools, or the same tools twice.** A server added to one project, in its
`.mcp.json` or as a project's server in `~/.claude.json`, isn't renamed: rename its `chronicle` entry `interlatch`.
Restart the agent afterwards, since it reads its MCP servers when it starts. A project's shared
`.claude/settings.json` keeps its `mcp__chronicle__…` rules until you rename them: the move only reports it.

**`uv tool install interlatch` says "Executable already exists: chronicle".** `agents-chronicle` is still
installed, and its commands have the same names: `uv tool uninstall agents-chronicle` first, or use **Update** in
**Settings › Status › Updates**, which does both.

**Update didn't offer to switch to `interlatch`.** An install from a checkout or a git URL isn't switched for you:
uninstall it, then install `interlatch` ([Moving from Chronicle](moving-from-chronicle.md#update)).

## Starting over

`interlatch uninstall` removes the hooks, background agents and MCP registrations and keeps your data;
`interlatch uninstall --purge` deletes the data too. With the app, also turn off **Open at Login** and delete it.
