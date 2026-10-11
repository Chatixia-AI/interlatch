# Install

[← Interlatch](../README.md) · [Docs index](README.md)

Interlatch runs on macOS and [Windows](#windows) and needs something to analyze sessions: a logged-in [Claude Code](https://claude.com/claude-code) (`claude`) or [Codex](https://github.com/openai/codex) (`codex`), IBM Bob with a Bob API key, or a model provider's API with your own key ([Model providers](analysis.md#model-providers)). Setup uses Claude Code when it is installed and offers Codex when it is not; switch any time in **Status › Analysis** or with `interlatch config set analysis.backend codex`. There are two ways to run it; both use the same data in `~/.interlatch` and can coexist.

Interlatch was called Chronicle. To update from Chronicle, see [Moving from Chronicle](moving-from-chronicle.md).

## Desktop app

1. Download `Interlatch-<version>-arm64.dmg` from the
   [latest release](https://github.com/Chatixia-AI/interlatch/releases/latest) (Apple silicon, macOS 13+).
2. Open it and drag **Interlatch** into **Applications**, then open it from there.
3. On first launch, choose **Connect** to record Claude Code sessions. This adds the `SessionEnd` hook and the MCP
   server (as `interlatch connect claude` does) and makes Interlatch open at login.

If macOS says Interlatch "cannot be opened" or "cannot verify the developer", that release was not notarized:
open **System Settings → Privacy & Security**, click **Open Anyway** next to the Interlatch message, and confirm.
There is no Intel build yet; on an Intel Mac use the command line install.

Interlatch then lives in the menu bar. It serves the dashboard in its own window and does the 15-minute
background sync itself, so it needs no launchd agents; closing the window keeps it running. The window has no
title bar: the sidebar is native macOS glass (it blurs whatever is behind the window) with the traffic lights on
top of it, and the window moves by its toolbar or the strip above the sidebar. The menu-bar icon is described under
[The menu-bar icon](#the-menu-bar-icon); in the app, its pages open in the app window, and the menu adds:

| Menu item | |
| --- | --- |
| Open in Browser | The dashboard in your browser instead of the app window |
| Connect Claude Code… | Shown until Claude Code is connected (if you chose *Not Now* at first launch) |
| Open at Login | On after connecting; turn it off to run Interlatch only when you open it |
| Install Command-Line Tool | Links the app's own `interlatch` command into `~/.local/bin` (skipped if one exists) |
| Open Data Folder | `~/.interlatch` |

Codex, Copilot, Bob and Antigravity are connected from the dashboard's **Sources** page. Hooks and MCP registrations point
at `~/.interlatch/bin/interlatch`, a small script the app rewrites on every launch, so moving or updating the
app does not break them. Quitting stops the sync until the next launch; an analysis cut off by quitting runs
again at the next sync.

To remove Interlatch, run `~/.interlatch/bin/interlatch uninstall` (hooks and MCP
servers; data is kept), turn off **Open at Login**, and delete the app.

## Command line

```bash
uv tool install --python 3.13 interlatch         # puts `interlatch` on PATH (~/.local/bin)
interlatch install                               # pick the agents to record, import, start the dashboard
```

Needs [uv](https://docs.astral.sh/uv/) (or `pipx install interlatch`). To install from a checkout instead,
run `uv tool install --python 3.13 .` in it.

`interlatch install` (also `interlatch setup`) walks through setup:

1. Checks the agent that analyzes sessions (Claude Code by default). If it is missing and Codex is installed, offers
   to analyze with Codex instead; with neither, sessions are recorded but not analyzed until one is installed.
2. Lists the coding agents on this Mac (Claude Code, Codex, GitHub Copilot, IBM Bob, Google Antigravity) and the MCP-only clients it
   finds (Claude Desktop, Cursor, Windsurf, Gemini CLI), with how many sessions each has on disk.
3. Asks, for each one found and not yet connected, whether to record it (default yes), and whether to give each
   MCP client Interlatch's tools. Codex Cloud is offered after Codex and defaults to no, since it goes online.
   Declining Claude Code stops Interlatch scanning `~/.claude`.
4. Connects the chosen ones, exactly as `interlatch connect <name>` does ([Sources](sources.md)), and imports their
   past sessions (skip with `--no-sync`; the background sync does it then).
5. Asks whether to run Interlatch in the background, starting at login (default yes): the 15-minute sync and the
   always-on dashboard below. If you say no, Claude Code sessions are still recorded and analyzed as they end;
   run `interlatch sync --work` for the rest and `interlatch ui --open` for the dashboard. Not asked again once the
   agents run; `--no-launchd --no-ui` turns them off. With the background sync on, it also asks once whether to
   show a desktop notification when a new version of Interlatch is out (`--notify-updates` or
   `--no-notify-updates` answers without asking).
6. Explains analysis: each past session is read through your Claude Code (or Codex) login, which counts toward
   your plan's usage (or through IBM Bob or the model provider you chose), and the knowledge it yields builds each
   project's knowledge base, then the **Glossary**, then the **Map**. Until sessions are analyzed, those two stay empty. It says how many sessions are waiting and how
   long the background takes over them (6 every 15 minutes), then asks whether to analyze them now with a
   progress bar per stage (sessions, knowledge bases, glossary, map themes): all of them, or the newest 20 for a
   first Glossary and Map in minutes. Ctrl-C stops, keeping what is done; the rest is analyzed in the background.
   `--analyze all`, `--analyze N` (the newest N) or `--analyze later` answers without asking.
7. Prints the dashboard address; on a first install it offers to open it.

Without a terminal, or with `--yes`, it takes the defaults without asking (analysis: later). Re-running it is safe: connected
agents are refreshed without a question, so it asks only about agents installed since. `--dry-run` shows what it
would do and changes nothing.

For Claude Code, and for the Mac itself, it sets up four things (each can be skipped with `--no-hooks`,
`--no-launchd`, `--no-ui`, `--no-mcp`). `--no-launchd` and `--no-ui` also remove their agent if it is installed, so
`interlatch install --no-launchd --no-ui` turns background running off and keeps everything else:

| Piece | What it does |
| --- | --- |
| `SessionEnd` hook in `~/.claude/settings.json` | Hands the ended transcript to a detached process that archives, ingests and analyzes it. Returns in milliseconds; a backup of `settings.json` is kept in `~/.interlatch/backups/`. |
| launchd `com.interlatch.sync` | `interlatch sync --work` every 15 minutes: catches anything the hook missed, processes the analysis queue, synthesizes knowledge bases, exports notes. |
| launchd `com.interlatch.ui` | Keeps the dashboard at <http://127.0.0.1:11524/>, with [Interlatch's icon in the menu bar](#the-menu-bar-icon) if you turned it on and the `app` extra is installed. |
| MCP server `interlatch` (user scope) | Lets Claude Code search your past sessions and knowledge. |

Optional: `interlatch install --inject-context` also adds a `SessionStart` hook that gives each new
session a short digest of the project's knowledge base (off by default; preview it with `interlatch context`).

Optional: `interlatch install --statusline` records each Claude Code session's real context use and, on Pro and Max
plans, how far the 5-hour and 7-day limits moved while it ran (Claude Code shares these only with the status line).
It sets `statusLine` in `~/.claude/settings.json` to `interlatch statusline`, which saves the snapshot under
`~/.interlatch/statusline/` and then runs the status line you already had, with the same input, so it looks
exactly as before; your original is kept in `~/.interlatch/statusline/wrapped.json`. If you had none, it
shows model, context and limits, and Claude Code then hides most of its footer key hints. The sync copies each
session's numbers into the database (the session page and **Status**). `interlatch uninstall` puts your own status
line back.

Optional (macOS): `interlatch install --menu-bar` puts Interlatch's icon in the menu bar while the dashboard runs, with
its status, a search and your recent sessions a click away. It needs the `app` extra
([The menu-bar icon](#the-menu-bar-icon)).

Remove everything with `interlatch uninstall` (data is kept; `--purge` deletes it too). It also restores your own
status line if `--statusline` wrapped it.

To use the desktop app from a command-line install, add the `app` extra and run `interlatch app`:
`uv tool install --force --python 3.13 'interlatch[app]'` (list any other extras you have in the brackets
too). If you switch to the app for good, `interlatch uninstall` first and let the app connect Claude Code, so the
launchd agents do not run alongside it (harmless, but redundant).

## Windows

On Windows 10 and 11 Interlatch is the command-line install; there is no desktop app or menu-bar icon. In PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # uv, if you don't have it yet
uv tool install --python 3.13 interlatch
interlatch install
```

`interlatch install` asks what it asks on a Mac. In place of launchd it registers two tasks in Task Scheduler, in an
**Interlatch** folder, for your account while you are signed in:

| Task | What it does |
| --- | --- |
| `\Interlatch\Sync` | `interlatch sync --work` every 15 minutes, and when you sign in |
| `\Interlatch\Dashboard` | Keeps the dashboard at <http://127.0.0.1:11524/>: started when you sign in, and again within 5 minutes if it stops |

Neither opens a window: a task starts `pythonw.exe`, which runs the command in a console without one, and its output
goes to `sync.out.log`, `ui.err.log` and the like in `%USERPROFILE%\.interlatch\logs`. They run on battery too.
`interlatch uninstall` ends and removes them, the dashboard with them.

The hooks it adds to Claude Code run `C:/Users/<you>/.local/bin/interlatch.exe hook session-end`. Claude Code runs a
hook in Git Bash, or in PowerShell without Git for Windows, and forward slashes read the same in both. A folder with
a space in its name is written in its short form (`C:/Users/ADALOV~1/…`).

Interlatch reads and writes text in UTF-8, as the coding agents write it, whatever language Windows is set to.

Not on Windows yet:

- **Joining a team hub.** Parts of the hub expect project folders as `/…` paths, so it is untested from Windows.
- **The VS Code extension's file history,** which looks files up by `/…` paths.
- **Desktop notifications** of a new version, and the menu-bar icon.

## The menu-bar icon

On macOS, Interlatch's mark (the stack of pages from its app icon) can sit in the menu bar. The desktop app always
shows it. A command-line install shows it only if you turn it on, and it needs two things there: the dashboard
running at login (`interlatch install` sets that up unless you pass `--no-ui`) and the `app` extra (PyObjC).

The quickest way is the **Menu-bar icon** switch under **Settings › Status › Recording**. Turning it on adds the `app`
extra first if it is missing (for a uv install from PyPI, keeping your other extras and your version; any other
install shows the command to run), then restarts the dashboard with the icon. Turning it off restarts it without.
The switch works only in a browser on the computer itself, since it installs software there.

To turn it on from the command line:

1. Add the `app` extra. `uv tool install --force` installs exactly the extras you list, so name the ones you already
   have too (`uv tool list --show-extras` shows them), e.g. `'interlatch[app,team]'`:

    ```sh
    uv tool install --force --python 3.13 'interlatch[app]'
    ```

2. Turn it on, which restarts the dashboard with the icon:

    ```sh
    interlatch install --menu-bar
    ```

    That runs the usual setup again (agents already connected are updated without asking). To skip it, set the
    option and restart the dashboard yourself:

    ```sh
    interlatch config set server.menu_bar true
    launchctl kickstart -k gui/$(id -u)/com.interlatch.ui
    ```

On a first install, `interlatch install` asks *Show Interlatch's icon in the menu bar?* once instead, and the answer is
No unless you type `y`. Turned on without the `app` extra, it says so and prints the command for step 1.

The icon stays plain while there is nothing to report:

| Icon | When |
| --- | --- |
| The mark | Nothing needs a look |
| With a dot | Syncing, analyzing, or sending to the hub |
| With a "!" | The last sync failed, the background sync agent exited with an error, or the hub refused the last push |
| Dimmed | Analysis is paused (a usage limit was reached; it resumes by itself) |

Clicking it opens a panel over the menu-bar glass, in the dashboard's blueprint style (light or dark, as the menu
bar is):

| Part | |
| --- | --- |
| The header | What is happening, with a picture for it: *All caught up · Synced 5 min ago*, *Syncing · 2 of 5* with a progress bar, *Sync failed* with the reason, *Analysis paused*. ⟳ syncs now (archive, ingest and analyze instead of waiting for the 15-minute run); the pulse opens the Activity page |
| The numbers | Sessions today, sessions waiting for analysis (not on a computer whose hub analyzes them), and lessons learned in the last seven days. Each opens its page |
| Notes | When knowledge last went to your hub, and what stops the analysis queue if something does |
| Search | Type anywhere in the panel to search every session, as the dashboard's search does; ↵ opens all the results in the dashboard |
| Recent sessions | The six most recent coding-agent sessions (imported chats left out) by day, each with its agent, project, outcome and time. ↑ ↓ move through them, ↵ or a click opens one |
| Open Dashboard | The dashboard (in the app: the app window). **Update to …** sits beside it when a new version is out and opens **Status**, where the update runs |
| ⋯ | The quick menu below |

Esc clears the search, then closes the panel. A right-click (or Control-click) on the icon opens the quick menu
instead: the status line, Search Sessions…, Open Dashboard, Sync Now, Update, the app's own items (**Open in
Browser**, **Connect Claude Code…**, **Open at Login**, **Install Command-Line Tool**, **Open Data Folder**) and **Quit
Interlatch**. For the login item, Quit stops the dashboard (and the icon) until you next log in or run `interlatch ui`;
sessions are still recorded.

The command-line install shows the icon only for the dashboard that runs at login, so a second `interlatch ui` in a
terminal adds no second icon (`--menu-bar` shows one anyway, `--no-menu-bar` hides it). To turn it off again, use the
switch, run `interlatch install --no-menu-bar`, or `interlatch config set server.menu_bar false` and the same `launchctl kickstart`
line.

## Updating

**Settings › Status › Updates** shows how Interlatch was installed and updates it the same way:

| Installed with | Update button runs |
| --- | --- |
| `uv tool install interlatch` | `uv tool upgrade interlatch`, after **Check for updates** finds a newer release |
| `uv tool install .` (a checkout) | `uv tool upgrade --reinstall interlatch`, offered when the checkout's files changed after the install; no network check |
| `uv sync` / `uv run` in a checkout (editable) | Nothing: the card says **git pull to update** |
| `pipx` or `pip` | `pipx upgrade interlatch` or `pip install --upgrade interlatch` |
| `uv tool install agents-chronicle` or `pipx install agents-chronicle` (Chronicle's package) | Moves it to `interlatch` with the same extras, even at the same version: uv installs `interlatch` over it, uninstalls `agents-chronicle`, then installs `interlatch` again; pipx uninstalls, then installs ([Moving from Chronicle](moving-from-chronicle.md)) |
| The desktop app | Nothing: **Download** opens the latest release to drag into Applications |

Only **Check for updates** goes online (to pypi.org), or, if you turn on **Check for updates daily** on the same
card, a check once a day while the dashboard is open. **Notify me about new versions** (also offered by `interlatch
install`) has the background sync check once a day instead, dashboard open or not, and show a desktop notification
once per release saying how to update: macOS Notification Center, or `notify-send` on Linux. On macOS clicking the
notification opens Script Editor, which posts it for Interlatch; the notification text says where to update. The
last answer is kept across restarts. Once an update is known, a notification says so (once per release, or per new commit in a checkout; **Later**
dismisses it), Settings gets a dot and the status bar shows **Update to …**. A checkout's Updates card lists the
commits and changed files a reinstall would bring in.
A dashboard run by `interlatch ui` (or its launchd agent) restarts itself afterwards and open tabs reload; an
`interlatch app` from the command line needs quitting and reopening. The button waits while a sync or analysis runs.
From a terminal, run the same command yourself.

On Windows, update with the button or `uv tool upgrade interlatch`, which keep the tool's folder and work while
Interlatch runs. Don't run `uv tool install --force interlatch` while it runs: that deletes the folder first, Windows
refuses to delete a running `python.exe`, and the install is left half removed. If that happened, end the tasks
(`schtasks /End /TN \Interlatch\Dashboard`, the same for `\Interlatch\Sync`), close Claude Code, run the same
command again, then `interlatch install`.

Install without a version pin: `uv tool install 'interlatch==<version>'` keeps `==<version>` in uv's record of the
install, and `uv tool upgrade` (the button included) then never goes past it. `uv tool install --force
interlatch` drops the pin.

A source checkout is versioned by its git tags: at the `v0.7.0` tag it is `0.7.0`, and three commits later
`0.7.1.dev3+g1a2b3c4`, so the version on the Status page says how far it is past the last release.

### From a checkout to a PyPI install

To get the Update button on a Mac that runs Interlatch from a checkout, install it from PyPI and let `interlatch
install` move the hooks, MCP servers and background agents over to the new `interlatch`. Your data in
`~/.interlatch` stays where it is; reinstalling never touches it.

```bash
uv tool install --force --python 3.13 'interlatch[app]'           # --force replaces a ~/.local/bin/interlatch that points into the checkout
interlatch --version                                              # the PyPI version
interlatch install                                                # point everything at it
interlatch status                                                 # hooks, MCP server and dashboard agent all ✓
```

The dashboard then shows **uv tool from PyPI** on its Updates card. The checkout keeps working with `uv run
interlatch …` on another port (`uv run interlatch ui --port 8799`) for trying changes before they are released.

Something not working? See [Troubleshooting](troubleshooting.md).
