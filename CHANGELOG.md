# Changelog

<!-- Don't add lines here: a pull request puts its line in a new file in changelog.d/ (see changelog.d/README.md),
     and each release moves the files it took into a dated section below. -->

## 0.24.0 (2026-10-15)

- **Party:** your coding agents become pixel-art characters who level up from the lessons their sessions taught,
  matched to a 65-track technical career roadmap: skills, six attributes, a class, quests you mark done and a journey
  of level-ups week by week. Sign in with Google to meet them; it sends nothing and doesn't join the leaderboard
  ([Party](docs/party.md)).
- **Party on Home:** once you're signed in to the Party, the top of Home shows your coding agents as Chatixia-style
  pixel characters battling through the week: each of this week's lessons is a monster beaten by the agent that
  taught it, main-quest milestones wait as bosses until you mark them done, and level-ups flash as they happen
  ([Party](docs/party.md#the-journey-on-home)).
- **Arena:** people on the leaderboard can enter their Party in a weekly arena: each player's champion, the agent they
  pick, duels every other player's champion once, the new Arena board ranks them by wins, and any duel replays round by
  round with the pixel characters. Entering publishes only each agent's level, tier, class and attribute levels and
  which one is your champion, with your board name and picture ([The arena](docs/party.md#the-arena)).
- **Who's hiring, in the Party:** each class shows how many open technical roles match it on public company job
  boards (worldwide; Japan's own count shows once its sample is big enough), the goal picker can sort tracks by demand, and a track lists
  the skills employers ask for. The numbers are read from Interlatch's Firebase project, which sends nothing about
  you; a copy ships with Interlatch for when it can't be reached ([Party](docs/party.md)).
- **Party on Home before signing in:** the Journey strip on Home now shows this week's lessons while your party sleeps;
  point at one to read it, click it to open its session, and sign in with Google to send your party after them
  ([Party](docs/party.md#the-journey-on-home)).

## 0.23.4 (2026-10-14)

- **What goes wrong:** the projects and agents a cause hit equally often are listed by name, in the same order every
  time, instead of in a different order after each restart of the dashboard.
- **Codex Cloud:** listing Codex Cloud tasks no longer leaves an `error.log` from the Codex CLI in the folder Interlatch
  was started from (a sync run in a project left one there).
- **A project's page tells you where you left off:** it opens on the last session's goal and what it left open, the
  sessions before it, the project folder's git state (branch, uncommitted changes, last commit, other worktrees) and
  the work week by week with the pull requests and commits it made. The knowledge base, lessons, sessions, artifacts
  and usage numbers move into tabs. On a hub with people it shows whose each session is
  ([A project's page](docs/dashboard.md#a-projects-page)).
- **What needs you, and where the project is going:** a project's page now starts with **Needs you**: changes not
  committed and commits on no remote, each with how long it has waited, and the decisions and open questions the last
  sessions left to you (amber after two days). Beside it, the project's own `ROADMAP.md`, `BACKLOG.md` or `TODO.md`
  by section, or else a backlog of its to-do items and open threads ([A project's page](docs/dashboard.md#a-projects-page)).

## 0.23.3 (2026-10-14)

- **`python -m interlatch`:** Interlatch's Python package is now `interlatch` too, so it runs as `python -m interlatch`.
  Hooks, background tasks and MCP servers set up with `python -m chronicle` keep working, as does the `chronicle`
  command.
- **Agents on Home:** a table with one row per coding agent: whether it is connected (or imported, or has issues),
  when it was last used, and its sessions, active time, prompts, tool calls, lines changed, tokens, est. cost and
  knowledge for the period, each with an inline bar. A dash marks what an agent doesn't record (a ChatGPT export has
  no tokens); click a row to see only that agent, or its status to open it in **Sources**.
- **Gemini costs:** Google Antigravity sessions, and GitHub Copilot sessions on Gemini models, now show an estimated
  cost at Google's Gemini API list prices instead of $0 ([Sources](docs/sources.md)). The next sync re-prices the
  sessions already recorded. Analysis through an OpenAI-compatible provider on a Gemini model is priced the same way.

## 0.23.2 (2026-10-14)

- **Hub computers stay with admins:** on a hub with people, a member or read-only person no longer receives the hub's
  computers, its team store's database, host and status, or its allowed sign-ins; only admins (and the hub computer
  itself) do.
- **Agent logos:** the Sessions list and its agent filter, Sources, MCP and the menu-bar panel mark each session and
  source with its agent's own logo (Claude Code, Codex, GitHub Copilot, IBM Bob, Google Antigravity, claude.ai,
  ChatGPT) instead of a colour dot.
- **Windows polish:** The dashboard can show an Interlatch icon in the notification area (turn on the menu-bar icon
  under **Status › Recording**, or run `interlatch ui --menu-bar`): click it to open the dashboard, right-click for Sync
  Now and Quit, which stops the dashboard until you next sign in. Release notifications show as Windows toasts. The VS
  Code extension's file history and folder list, hub folders and projects, and a person's projects now match Windows
  paths however they're spelled (`C:\work\app`, `c:/work/app`).

## 0.23.1 (2026-10-13)

- **`agents-chronicle` is retired:** Interlatch is published only as `interlatch` now, and `agents-chronicle` is no
  longer on PyPI. An install under the old name gets no more updates: **Update** in the dashboard still moves it to
  `interlatch`, or see [Moving from Chronicle](docs/moving-from-chronicle.md) for the commands.

## 0.23.0 (2026-10-13)

- **Leaderboard:** sign in with Google and compare your hours of agent time, sessions and tokens over 7, 30 or 90
  days with everyone else who joined, under **Leaderboard** in the dashboard (`interlatch leaderboard` shows where
  you stand). Only the totals from this computer's coding-agent sessions are sent, with your first name (rename it
  any time) and your Google picture. No email, projects, prompts or titles. **Leave** deletes your row.

## 0.22.1 (2026-10-11)

This release never reached PyPI (its upload failed after the repository's rename); these changes first shipped
there in 0.23.0.

- **Docs:** the quick links atop the docs home (Docs, 日本語, English) open their pages instead of a 404, Install
  lists every way to analyze sessions (Claude Code, Codex, IBM Bob with a Bob API key, or a model provider's API with
  your own key), and links, **What's new** and **Download** under **Status › Updates** among them, point at the
  repository's new name, [Chatixia-AI/interlatch](https://github.com/Chatixia-AI/interlatch).
- **Moving from Chronicle keeps the commands:** **Update** on an `agents-chronicle` install no longer leaves it
  without the `interlatch` and `chronicle` commands, which kept the dashboard from coming back and stopped the
  background sync, the session hooks and the MCP server. Already hit by it? [Troubleshooting](docs/troubleshooting.md#moving-from-chronicle)
  has the two commands that put it right.
- **Restarting, animated:** while the dashboard restarts after an update, its dialog turns the arrows inside a ring
  that swells and fades, with a sweep along its foot (with animations off they turn slowly and the rest holds still).
  The last places that still said Chronicle say Interlatch: the diagrams in the docs, the log file's lines, the
  Postgres quick start, and the names the dashboard and menu-bar panel use for their requests and what the browser
  remembers. Settings a browser kept under the old names carry over, and a sign-in to a hub from before stays valid.
- **Windows:** `uv tool install interlatch` and `interlatch install` now work on Windows 10 and 11. Task Scheduler runs
  the 15-minute sync and keeps the dashboard up, without a window, and the hooks run in Git Bash and in PowerShell
  ([Windows](docs/install.md#windows)). A Windows computer can't join a team hub yet.
- **The VS Code extension is now Interlatch:** a new extension, `Chatixia-AI.interlatch` 0.3.0, with the sections
  **Interlatch: This File** and **Interlatch: Files in Workspace**. Your `chronicle.*` settings still apply until you
  set their `interlatch.*` counterparts. Uninstall the Chronicle extension after installing it; Interlatch offers to,
  once ([VS Code extension](docs/vscode.md)).
- **The VS Code extension on the Marketplace:** install **Interlatch** from VS Code's Extensions view
  ([VS Code extension](docs/vscode.md#install)). Releases publish it whenever its own version goes up.

## 0.22.0 (2026-10-10)

- **Your Chronicle setup moves over by itself:** the first time the dashboard or the background sync starts,
  Interlatch moves `~/.claude-chronicle` to `~/.interlatch` (a link stays at the old path, so nothing that still names
  it breaks) and updates everything that ran Chronicle: the Claude Code hooks and status line, the MCP server in each
  agent (now named `interlatch`, with permission rules for `mcp__chronicle__` tools renamed), the block in your
  `CLAUDE.md` / `AGENTS.md` files and the login items. `chronicle` keeps working as another name for the
  `interlatch` command, and `interlatch migrate --dry-run` shows what the move changes. An install of the
  `agents-chronicle` package offers the move to `interlatch` under **Status › Updates**.
- **Chronicle is now Interlatch:** the same program under a new name. Install it with `uv tool install interlatch`
  (the app is `Interlatch-<version>-arm64.dmg`) and run it as `interlatch`; `chronicle` still works. An existing
  install moves over by itself: the data folder becomes `~/.interlatch`, the MCP server `interlatch` in every agent,
  and hooks, login items and permissions follow; `CHRONICLE_*` variables are still read. The docs are at
  [interlatch.com/docs](https://interlatch.com/docs/) ([Moving from Chronicle](docs/moving-from-chronicle.md)).
- **The hub's image is now `ghcr.io/chatixia-ai/interlatch-hub`:** download `compose.yaml` again to move to it
  ([Update](docs/docker.md#update)); a `.env` with the `CHRONICLE_*` names, the data and the volumes stay as they are,
  and `chronicle-hub` gets the same image for now, so a hub that only runs `docker compose pull` keeps updating.

## 0.21.3 (2026-10-10)

- **Hide the sidebar from the rail:** a click on the rail icon of the page you're on hides the sidebar, and another
  click brings it back. While it's hidden, pointing at the rail still brings it out.

## 0.21.2 (2026-10-10)

- **Analyze the queue now:** **Status › Analysis** has an **Analyze N now** button under the queue. It analyzes the
  sessions ready now at once instead of waiting for the background run, even with automatic analysis off or during a
  usage-limit pause, which it lifts when it gets through. When nothing is ready but sessions are still active, it
  offers to analyze those. It's in the command palette too, and the stale "Paused until" line that stayed after a
  pause ran out is gone ([How analysis works](docs/analysis.md)).

## 0.21.1 (2026-10-10)

- **The menu-bar switch shows the icon after it restarts the dashboard:** turning the icon on from **Settings ›
  Status › Recording** (or updating from **Status › Updates**) restarted the dashboard in place, and macOS then kept
  the new icon hidden. On a Mac the dashboard that runs at login is now restarted by launchd, and the icon shows.
  With the icon on, the Status note no longer says it is in the menu bar: it says to check **System Settings › Menu
  Bar**, where the dashboard is listed by its Python (`python3.14`, say), and on a MacBook whether the notch hides
  it ([Troubleshooting](docs/troubleshooting.md#install-and-the-app)).
- **Animations on or off:** **Settings › Appearance › Animations** turns the dashboard's motion off (loading shimmers,
  slides, fades and smooth scrolling; spinners turn slowly) or on, or follows macOS Reduce motion, the default. The
  loading placeholder's shimmer is now a clear glint across each bar, and it keeps moving while the page starts up
  ([The dashboard](docs/dashboard.md)).
- **Teammates' initials on Knowledge:** a teammate's lesson now shows the initials of the computer it came from next to
  its name ("from YP YumaのMacBook Pro (2)"), the same as your own lessons, in the list and on the card.

## 0.21.0 (2026-10-10)

- **Turn the menu-bar icon on or off from the dashboard:** **Settings › Status › Recording** has a **Menu-bar icon**
  switch. Turning it on adds the `app` extra first if it is missing (for a uv install from PyPI, keeping your other
  extras and your version), then restarts the dashboard with the icon; turning it off restarts it without. It works
  only in a browser on the computer itself ([The menu-bar icon](docs/install.md#the-menu-bar-icon)).

## 0.20.0 (2026-10-10)

- **Chronicle in the menu bar:** on macOS the dashboard that runs at login can now put Chronicle's mark in the menu
  bar (with the `app` extra installed). It's optional: `chronicle install` asks once, off unless you say yes, and
  `chronicle install --menu-bar` turns it on. The icon stays plain while all is well, gets a dot while it syncs or
  analyzes, a "!" when a sync or a push to the hub failed, and dims while analysis is paused. A click opens a panel in
  the dashboard's blueprint style: what is happening (with a progress bar while it works), sessions today, the
  analysis queue and lessons this week, a search across every session as you type, and your recent sessions with their
  agent, project and outcome, a click or ↵ away. A right-click opens a quick menu with Sync Now and Quit. The desktop
  app has the same panel, opening pages in its window. `chronicle install --no-menu-bar` turns it off.
  [The menu-bar icon](docs/install.md#the-menu-bar-icon)
- **Warnings before what leaves your computer or can't be undone:**
  - **Settings › Storage:** picking **Everything** for the copy in Postgres now says what whole transcripts carry (your
    code, file contents, command output, anything pasted, and any secret redaction misses). **Save** asks you to
    confirm before transcripts start to go.
  - **Sharing a group with the hub:** the group dialog now says what goes. On a computer that sends transcripts, a
    warning says every session goes unredacted and the hub's admins can read them. Saving asks you to confirm before
    the group starts going there.
  - **Your own role on a hub:** making yourself a member or read-only asks first, since only another admin can make you
    an admin again.
  - **Analysis with an API provider:** the provider panel says each call is billed to your API key, and **Analyze N
    sessions** says so too.

## 0.19.0 (2026-10-10)

- **A copy in Postgres:** Chronicle can keep a copy of your archive in a Postgres database you choose (on this
  computer, in Docker, or in the cloud) for SQL and BI tools. Set it up under **Settings › Storage** or with
  `[mirror] to = "postgres"`. After every background run it writes only what changed. By default the copy holds
  sessions' details, summaries, lessons, knowledge bases, artifacts and token usage, but no prompts or transcripts;
  `include = "everything"` adds them, with secrets redacted. Chronicle still works from its own database and never
  reads the copy back. `chronicle mirror` shows what the copy holds, and `chronicle mirror sync` writes it now. The
  driver comes with `uv tool install 'agents-chronicle[postgres]'` ([A copy in Postgres](docs/postgres.md)).

## 0.18.0 (2026-10-09)

- **3D icons from the Chatixia Studio cast:** the app icon, each page's title and the Home tiles now carry 3D art
  rendered with the same characters and objects as the studio's explainer films (a log scroll for Sessions, a book
  for Knowledge, a folder for Projects), and an empty Recent sessions or Latest knowledge card shows Pip or Lumi. The
  sidebar and buttons keep their line icons. `packaging/icons3d` re-renders the art.

## 0.17.1 (2026-10-09)

- **Make a group from the Projects sidebar:** a folder-plus button beside the project count opens **New group**, the
  same dialog as on the Projects page, so a new group no longer means going to All projects first.
- **Learn from your work on the Knowledge page:** the page now opens with a band from the library instead of a plain
  link: the latest lesson with its principle, a case file you haven't answered (answer it right there, with the same
  choices as All knowledge), the topic you ask about most or that has the most lessons this month, with its two
  newest lessons, and how many lessons are ready to revisit. Each part shows only when it has something. Lesson
  topics are worked out once per lesson text, so the library and this band load faster after the first time.
- **VS Code extension 0.2.0:** rebuild and reinstall it to pick up the dashboard's new port (11524, then 8765) and
  Antigravity sessions; the version bump lets VS Code tell the new build from the old one.
- **A session's Files table keeps its numbers readable:** a long path wraps at its slashes instead of squeezing the
  Reads, Edits and Lines columns, and a file outside the project (a temp scratchpad, say) shows its last two folders,
  with the full path on hover. The context chart's "API calls" note moves to a second line when the chart is too
  narrow for it to sit between the start and end times.

## 0.17.0 (2026-10-09)

- **Your hub's projects in a section of their own:** on a computer that sends to a hub, the Projects sidebar and page
  now list first the hub's projects that folders here go to, under the hub's name. Each shows the team's sessions there
  and the projects here that go to it, which no longer sit among Other projects (one in a group stays there too). A
  hub project opens on the hub's dashboard, signed in, at that project (an older hub opens on Home). A project here
  that goes to a hub project now has a folder icon with a teal cloud, instead of a plain cloud, in the sidebar, search
  and **Settings › Devices**, and the hub's projects have a stacked-folders icon.
  [Same project, different folders](docs/devices.md#same-project-different-folders)
- **Learn from your work:** a library under Knowledge with every fix, gotcha, decision, learning and pattern, grouped
  by topic (system design, frontend, APIs, data modeling and more) with counts and a search. A lesson reads top to
  bottom, each part once: a case file's scene, question and answer, then the explanation, the leads ruled out, and the
  principle and checks for next time where the analysis found them. A diagram appears only where the analysis drew one
  from the session. **Save work note** keeps a copy; **Mark as read** and **Revisit sooner** add badges. All of it
  stays in that browser. A lesson analyzed before shows its title and explanation until its session is analyzed
  again. See [Pages](docs/dashboard.md#pages).
- **A long session's analysis keeps its lessons:** a session too long for one pass is analyzed in parts, and the
  final pass used to write every part's lessons out again. With case files that reply could come back with none,
  and the session's lessons were replaced by nothing. The final pass now picks the parts' lessons to keep, and
  keeps all of them if its reply names none.
- **Learning follows your curiosity:** the analysis notes the questions you ask about concepts and tradeoffs, and
  Learn from your work puts those topics first, with the questions behind them under **Why these topics?**. Only your
  own sessions count, and the quotes stay on your computer, out of what a hub receives.

## 0.16.0 (2026-10-08)

- **Case files: lessons that ask before they tell:** the analysis now keeps, for each fix, gotcha and decision, how it
  first showed up, the question it raised, the answer, and the leads the session tried and ruled out. In **All
  knowledge** a case you haven't answered opens with its scene and question; the choices are the real answer and the
  real dead ends, and a wrong pick says what ruled it out. **Skip, just show me** opens it at once, five skips in a row
  turn the questions off, and **Ask first** turns them back on. **Case files** lists only them. Your answers stay in
  that browser. The MCP tools show agents the scene and the ruled-out leads too. Lessons analyzed before get a case
  file when their session is analyzed again. See [Case files](docs/analysis.md#case-files).
- **Wrong invite and sign-in codes make an address wait:** after five codes the hub never issued, from one address
  within 15 minutes, each further try from there waits 1 second, then 2, 4 and so on up to a minute, and its code
  isn't looked at meanwhile. Behind a proxy in `[server] trusted_proxies` the hub counts each visitor by
  `X-Forwarded-For`. A used or expired code never counts. See [Troubleshooting](docs/troubleshooting.md#a-hub).
- **Security fixes, continued (the hub):**
  - An analysis a computer sends applies only to that computer's own sessions. A computer could send an analysis
    under the id of a session another computer (or the hub itself) has, before the hub analyzed it, and replace its
    title, summary and lessons. Now the hub applies an analysis only to the sending computer's session.
  - The hub's shared token can no longer act as a computer that joined as a person. Named with that computer's id,
    it could send, refile or take back that computer's sessions. Such a computer sends with its own token only.
  - A response header with a line break in it is sent with a space instead, rather than failing midway.
- **The dashboard page runs only its own scripts:** it now sends a Content-Security-Policy that allows scripts and
  styles from the dashboard's own files only, so a mistake that lets a transcript's text into the page as HTML could
  run nothing. The macOS app's window also allows `eval`, which its bridge to the window needs.
- **The team's Home shows who worked on what, without counts per person:** **Who's active** listed each person's
  sessions and lessons. It is now **Who worked on what**: each person with the projects they worked on in the period.
  A tally per person reads as a score, and a lesson count mostly counts what went wrong in someone's sessions.
- **An invite can't claim a computer the hub already knows:** a computer that sent with the shared token now keeps a
  key the hub records, and an invite that names its id must show it. Someone else's invite could take over that
  computer and its sessions. `chronicle hub join --code` shows the key on its own; for a computer that can't, an
  admin makes an invite for it with `chronicle hub invite <name> --computer <name or id>`.
- **Teammates' lessons on your own dashboard:** a computer that shares with a team hub showed its teammates' lessons
  only as a count in **Settings › Devices**. They now show on project pages and in **All knowledge**, marked **from**
  the computers whose sessions stated them; **From teammates** in the source filter lists only them, and **Read them**
  in **Settings › Devices** opens that list.
- **All knowledge's source filter finds every match:** it used to filter the newest 400 items, so with a large archive
  it missed older ones (157 of 227 teammates' lessons, on one archive of 3,581 items).
- **Redaction catches more:** the credential in an `Authorization:` header (`Basic`, `Bearer`, `token`), the password
  in `curl -u user:password`, cookies (`Cookie:` and `Set-Cookie:` lines, `curl --cookie`), and a secret under a
  prefixed name such as `DB_PASSWORD`, `PGPASSWORD` or `AWS_SECRET_ACCESS_KEY`, which went through as they were.
  [Data and privacy](docs/privacy.md#what-redaction-catches) now lists what redaction catches and what it doesn't.
- **The hub's Docker setup pins Caddy and Postgres** to an exact version and digest, so their images can't change
  under you; Dependabot moves them forward. To [update](docs/docker.md#update), download `compose.yaml` again before
  `docker compose pull`, and keep your own changes (such as [your own
  certificate](docs/docker.md#your-own-certificate)) in `compose.override.yaml`.
- **No more tooltips on the rail:** pointing at a rail icon no longer shows a card with its name and what the section
  holds. The sidebar already shows that, and with the sidebar hidden, pointing at the rail brings it out.
- **The Sync button shows when a sync is running:** its icon turns into a spinner from the click until the sync ends,
  whether it was started from the button, the command palette or Settings › Status.
- **A folder you add to a hub's project stays there:** a share that started before the change (an analysis run takes
  minutes) told the hub the folders as they were when it started. The hub took the new folder back out and listed
  its sessions as a project of their own, until the next share put them back. A folder removed, or a project left,
  during such a run was shared again the same way. Each share now reads the config when it starts talking to the
  hub, and a share or folder change waits for the one before it.
- **Each folder of a hub project counts its own sessions:** in **Settings › Devices**, two folders of one project each
  showed the project's total.
- **A hub says what's new:** when another computer shares sessions or lessons the hub didn't have, or a computer
  joins, the **Team overview** icon and the browser tab show a count, and an open dashboard says what came in (*New
  from Yuma in AI-BPO-Resona: 2 sessions, 4 lessons*). Team overview lists what is new since your last visit and adds
  **+n** to each project's sessions and lessons. Each person's count is their own and follows them to another browser;
  someone limited to projects hears of theirs only, and only admins hear of computers joining.
  [The hub's dashboard](docs/devices.md#the-hubs-dashboard)

## 0.15.0 (2026-10-08)

- **Security fixes from a review of the hub and the dashboard:**
  - On a hub, each computer's sessions stay its own. A computer could send a session, its lessons or its transcript
    under another computer's session id and replace it, then take it back. Now the hub keeps the first computer's
    copy and refuses the other one, and a computer can take back only what it sent itself.
  - An invite can no longer take over a computer that already joined as someone else. An admin removes that
    computer from its person first.
  - A hub without people no longer lets other devices in as admins. Reached directly with `server.host 0.0.0.0`,
    through a reverse proxy or through Tailscale Funnel, its dashboard now answers only the hub itself and says how
    to add the first admin (`chronicle hub invite <your name> --email <email> --role admin`). A Tailscale login that
    `chronicle tailnet on` lets in still gets in.
  - Shared sessions and analyses are unpacked only up to a size limit. Before, a small upload could unpack to
    gigabytes and fill the hub's memory.
  - A file name taken from a transcript can't add response headers when the file is opened, and a link a transcript
    recorded opens only as `http(s)`, never as `javascript:`.
  - Smaller hardening: listing git-ignored files runs no repository hooks or fsmonitor; the app's launcher finds
    Chronicle by bundle id only in the Applications folders; Chronicle's folder is made private to you (mode 700).
    [Data and privacy](docs/privacy.md) now says plainly that `share = "everything"` sends unredacted transcripts.
    [Troubleshooting](docs/troubleshooting.md#a-hub) explains the new hub messages.

## 0.14.4 (2026-10-07)

- **Share a group as one project on the hub:** on a computer that sends to a hub, edit a group and pick one of the
  hub's projects under **On the hub**. Every project in the group goes there from the next push, including ones that
  join the group later; one you keep out of the group stays on your computer. A project that leaves the group stops
  going, and what it shared stays on the hub. See [A group as one project on the
  hub](docs/devices.md#a-group-as-one-project-on-the-hub).
- **Check for updates finds a new release on the first click:** PyPI's cache servers can keep serving the previous
  version for a few minutes after a release, so one check sometimes said you were up to date. Chronicle now asks up
  to three times and keeps the newest answer.

## 0.14.3 (2026-10-07)

- **See which projects are on the hub:** on a computer that sends to a hub, a project that is in one of the hub's
  projects has a cloud icon in the Projects sidebar and an **On the hub** badge, with the hub's name for it when it
  differs. On the hub, a project set up ahead of time is listed on the Projects page and in the sidebar right away,
  marked "no sessions yet", instead of only under Team › Projects.
- **Group your projects:** put related projects under one heading, such as **Aktio**, on the Projects page and in its
  sidebar. Tick a few projects and Chronicle offers the folder they share as a rule, so new projects there join on
  their own; move any project in or out by hand from its card. Sessions can be filtered by a whole group. Groups only
  change how projects are listed. See [Project groups](docs/dashboard.md#project-groups).
- **Clicking a rail icon opens a hidden sidebar:** with the sidebar hidden, a click on a section's icon now shows the
  sidebar and keeps it shown, as the sidebar button does. Hovering still only peeks. Search, which has no sidebar,
  and narrow windows leave it hidden.

## 0.14.2 (2026-10-07)

- **The Map's search finds knowledge and sessions too:** besides terms and groups, it now matches the knowledge items
  (title and text) and session titles drawn under each term, opens them on the map, highlights them and lists them in
  the side panel. Before, text you could see on a knowledge node found nothing.
- **A hidden sidebar peeks out on hover:** with the sidebar hidden, resting the pointer on the rail brings it out
  over the page, and it goes again once the pointer moves away. Pointing at another icon shows that section's
  sidebar. Whether the sidebar is shown stays as you set it.

## 0.14.1 (2026-10-07)

- **You can see which agent analyzes your sessions:** in Status › Analysis, a green dot marks the agent (or API
  provider) in use, and an agent that isn't installed on this computer says **Not installed** on its tab instead of
  just fading out.
- **Leave one of the hub's projects:** a computer that shares knowledge can now leave a project and stay in the
  others. **Leave** in **Settings › Devices** (or `chronicle hub leave --project <name>`) stops it sharing its
  sessions there and getting the project's teammates' lessons. What it already shared stays on the hub for the team.
  **Rejoin** (`chronicle hub rejoin --project <name>`) undoes it, and the hub's **Team › Projects** marks a computer
  that left. [Leaving](docs/devices.md#leaving)
- **Remove a folder added by mistake:** **Remove** next to a folder, or `chronicle hub remove-folder <folder>`, now has
  the hub delete what the computer shared from that folder, with its lessons, here and in the team store. Before,
  those sessions stayed on the hub, filed under a project named after the computer's own folder. Added later to the
  right project, they are shared again.
- **Settings › Devices on a computer that joined a hub, redesigned:** a **Hub** card with its name, whether the last
  send worked, how many sessions it shared and how many teammates' lessons it holds, **Share now**, **Open the hub's
  dashboard** and **Leave the hub…**. A **Projects on the hub** card lists the projects the computer is in, by folder
  or by git remote, and **Join a project** adds one from the dashboard: pick one of the hub's projects and a folder,
  with search. The sharing choices have a card of their own.
- **Team › Hub settings:** one page for a hub's admins with the hub's name and address, **Knowledge only** (moved here
  from Team › Computers), the model that writes each project's knowledge base, updates, and the commands to back it
  up. In the Docker image, the name and address that `.env` sets are shown but not editable, since each start writes
  them again. A hub now gives computers its `[hub] name`, not its computer's name.
- **Fixed:** the band at the top of a hub's dashboard showed "null" when the hub had no address set.

## 0.14.0 (2026-10-07)

- **The sidebar stays as you left it:** clicking a section in the rail no longer brings back a sidebar you hid. Only
  the button at the top left (or ⌘B) shows or hides it.
- **The join command works with every hub token:** about one token in 64 starts with `-`, and the
  `chronicle hub join … --token …` command that `chronicle hub enable` prints then failed with "expected one
  argument". It now prints `--token=…`.
- **A hub says it's a hub on every page:** a green band across the top of its dashboard gives the hub's name, its
  address and what kind of hub it is, and the status bar has a **Hub** line. They replace the small Hub marker in the
  toolbar. On a phone, which has no status bar, the band stays.
- **A dashboard for a dedicated hub:** a hub that is a server for the team (`[hub] dedicated = true`, which the Docker
  image now sets) opens on the team's Home and leaves out what only a person's own computer needs: Sync, Activity,
  Artifacts, Suggestions, and Settings › Sources, MCP and Devices. Under **Team**, **Projects** replaces Shared
  projects: an admin makes a project by name from any browser (**New project**), or removes one, with no command line
  on the server. A name never reaches outside the hub's own `projects` folder.

## 0.13.1 (2026-10-07)

- **A hub that takes knowledge only:** `chronicle config set hub.accept knowledge` on the hub, or **Team › Computers ›
  Knowledge only** for an admin, keeps every transcript off the hub. It turns away any computer that sends
  transcripts, whatever token it uses, and tells it to run `chronicle config set hub.share knowledge`. Computers that
  share knowledge go on as before, and one that joins with an invite is set to share knowledge on its own.
  **Team › Computers** and `chronicle hub` mark a computer that is turned away. A typo in the setting counts as
  knowledge only, and the audit log records who changed it.
  [A hub that takes knowledge only](docs/devices.md#a-hub-that-takes-knowledge-only)
- **Sharing knowledge keeps to the hub's projects:** a computer that shares knowledge with a hub now shares only the
  sessions the hub files under one of its projects (a folder added with `chronicle hub add-folder`, or a repository
  whose git remote the hub knows). Sessions in other folders stay on the computer, and its push counts them as "kept
  here". Before, someone given every project shared every folder they had analyzed. To share every folder, join with
  `--all-folders`, run `chronicle config set hub.all_folders true`, or choose **Every folder** in **Settings ›
  Devices**.
- **`chronicle hub purge`:** removes for good what a person's computers, or one computer, sent to a hub: in some
  projects (`--project`), or outside the projects the person sees (`--outside-access`). It takes their sessions,
  lessons, notes and received transcripts, their copy in the team store, and the knowledge bases built from them, and
  the hub refuses those sessions if they are sent again.
- **More vivid charts:** the eight series colours (charts, the Map, the Systems map, agent dots) keep their hues at
  the most saturation a screen can show, within the lightness range that keeps them readable and distinct, checked
  with the palette validator in both themes. Bars, lines and the tiles' sparklines draw in that bright blue rather
  than grey or the link colour, with stronger fills, and the heatmaps run from pale sky to deep blue.
- **Search for projects when inviting someone:** on a hub with more than six projects, **Team › People** has a search
  box above the projects a person sees, both when inviting someone and under **Change**. It filters by name or
  folder, and ticked projects stay ticked while hidden, with a count of how many are chosen. Enter ticks the only
  match left and never sends the invite; Escape clears the search.
- **The hub's container names its own address:** its log said `Chronicle dashboard: http://127.0.0.1:11524/`, and
  VS Code connected to the server over Remote-SSH forwarded that port to your own computer, where it hid your own
  dashboard at `127.0.0.1:11524`. It now says `Chronicle hub is up: https://<the hub's address>`.
- **Step-by-step guides for a team's hub:** [A hub in Docker](docs/docker.md) now walks whoever runs the hub from an
  empty server to a shared project: Azure (a VM, your own SSH key, the ports, a name), the first admin, adding a
  project from the command line, connecting your own computer, inviting the team, everyday commands and
  troubleshooting. [Joining your team's hub](docs/join-a-hub.md) is the page to send teammates.

## 0.13.0 (2026-10-07)

- **The dashboard in the Chronicle blueprint style:** the app now looks like
  [chronicle.chatixia.net](https://chronicle.chatixia.net/). The dark theme is navy blueprint paper with a faint grid,
  the light theme the same drawing as a whiteprint; type is IBM Plex Sans and Plex Mono, with small mono capitals for
  labels. Gold marks what to press (Sync, Approve) and the current section, teal what is done, blue the links and the
  data. Panels are flat with thin rules instead of glass, in the browser and in the macOS app, so *Reduce transparency*
  is gone from **Settings › Appearance**. The fonts ship with Chronicle (Latin subsets, SIL Open Font License), so the
  dashboard never calls a font service; Japanese text uses the system's Hiragino.
- **A hub in Docker:** `docker/compose.yaml` runs a team's hub on any server with Docker: the hub, Caddy for HTTPS,
  and Postgres for the team store. The image (`ghcr.io/chatixia-ai/chronicle-hub`, published with each release)
  sets the hub up from `CHRONICLE_*` variables. It takes knowledge only, so it needs no model. Computers join with
  invites, and the first admin's invite is printed in the container's log. It won't serve a hub without people,
  since anyone who reaches such a hub counts as its admin. Requests through the dashboard never count as made at the
  hub, so everyone signs in. See [A hub in Docker](docs/docker.md).

## 0.12.1 (2026-10-07)

- **IBM Bob analyzes sessions too:** pick **IBM Bob** in **Status › Analysis** (or `analysis.backend = "bob"`) and add a
  Bob API key (`chronicle config set-key bob`, or `BOB_API_KEY`). Chronicle runs Bob Shell headless (`bob run`) with
  every tool group, MCP and subagents off, its instructions in a throwaway custom mode with no tools, and discards
  a reply that follows any tool call. Bob keeps these runs in its own task list; Chronicle doesn't import them.
- **Choose the agent's model:** Claude Code's tab sets the models for sessions, knowledge bases and screening (an
  alias or a full id such as `claude-opus-5-5`) and the effort; Codex's sets its model and effort.
- **Forgetting a session keeps files other sessions share:** `chronicle forget` (and the dashboard's Forget) deleted the
  session's archive even when it was shared: the snapshot of Bob's database every Bob task points to, or the archived
  chat export behind every imported ChatGPT or Claude.ai chat. With `--delete-transcript`, forgetting one Bob task
  deleted Bob's own `~/.bob/db/bob.db`. A file is now removed only once no remaining session uses it.
- **A tidier Status › Analysis:** the card spans the page, with what analyzes on the left and the queue (ready,
  queued, held, spent), why it waits and the knowledge language on the right. A provider's settings sit in two
  columns with a Ready / Not set up badge, endpoint and tuning under **Advanced**, and the buttons on one row.
- **Analysis through your own model provider stays MIT:** [ee/README.md](ee/README.md) no longer lists a company's
  own model endpoint as an enterprise feature. Since 0.12.0 every provider (Bedrock, Azure OpenAI and the rest) is in
  the MIT core, and it stays there.

## 0.12.0 (2026-10-06)

- **Analyze with a model provider's API:** besides Claude Code and Codex, sessions can be analyzed through the
  Anthropic API, Claude in Amazon Bedrock (a Bedrock API key, or your AWS sign-in with SigV4), the OpenAI API, Azure
  OpenAI (an API key, or Microsoft Entra ID through `az login`), OpenRouter, any OpenAI-compatible server (LM Studio,
  vLLM, ...), or Ollama, where nothing leaves your computer. **Status › Analysis › API provider** sets the endpoint,
  models and key, tests the connection and switches to it; in the terminal, `chronicle config set
  providers.<name>.<key>`, `chronicle config set-key <name>` and `analysis.backend = "<name>"`. Keys are kept in
  `provider-keys.json` (mode 600), never in `config.toml`, and never sent back to the browser. Calls carry no tools, so
  the model can only answer, and need nothing beyond the standard library. [Model providers](docs/analysis.md#model-providers)
- **Bob Shell gets the MCP server:** `chronicle connect bob` now also registers the MCP server in
  `~/.bob/settings/mcp.json`, the file Bob 2.x and Bob Shell (`bob`) read. It used to write only
  `mcp_settings.json`, which Bob copies to `mcp.json` once, so if Bob Shell had run before you connected, it never
  saw the server. Disconnecting removes it from both files, and the Sources card checks `mcp.json`. Bob Shell's tasks
  were already recorded: they're in the same `~/.bob/db/bob.db` as the IDE's.
- **The dashboard's default port is 11524**, out of the way of other local servers that like 8765. New installs use
  it; an existing install keeps the `port = 8765` its `config.toml` already has (`chronicle config set server.port
  11524` moves it). The VS Code extension, unless `chronicle.url` is set, finds the dashboard on either port.
- **Bob's tool calls are recorded:** Bob now writes tool calls as `toolCalls` rather than the OpenAI-style
  `tool_calls`, so Chronicle recorded none of them: Bob sessions showed 0 tool calls, no files, and the files Bob
  wrote never appeared as artifacts. Both shapes are read now. Bob's relative paths (`docs/x.html`) resolve against
  the task's folder, lines added and removed come from the diff Bob keeps with each change, a call Bob marks failed
  counts as an error, and a subagent's calls and files are the subagent's. Cost comes from what Bob reports for each
  message, which used to read $0.00 when the task total had no token counts.

## 0.11.0 (2026-10-06)

- **A hub opens on Activity:** on a hub with people, the dashboard now opens on **Activity** (the charts of every
  session), with its own rail icon. The team's page (what needs attention, team projects) moves to its own
  **Team overview** rail icon at `#/overview`, for every member of the hub. A computer that isn't a hub keeps Home.
- **Updating reloads the page:** after **Update to …** finishes, a "Restarting Chronicle…" cover stays over the page
  while the dashboard restarts, and the page reloads itself as soon as the new version answers (it used to wait for
  the next 20-second status check). If the restart takes over a minute, the cover says so and offers a Reload button.

## 0.10.0 (2026-10-06)

- **Chronicle Enterprise lives in `ee/`:** features a company needs to run Chronicle across its teams (single
  sign-on, policies, audit export, admin reports) will be built in `ee/` under the Chronicle Enterprise License and
  ship as a separate package. Everything else, the hub and its team features included, stays MIT, and the
  `agents-chronicle` package stays MIT only. [ee/README.md](ee/README.md)
- **A map of your systems:** **Projects › Systems map** draws every folder your agents worked in as a system, grouped
  by the folders they live in, with lines where one project edited or read another's files or its glossary says how
  it uses the other. Open a system for its parts in five rows (ways in, code, data, delivery, and what it runs on or
  uses): its frontend calling the backend's `/api`, the Postgres it stores in, the Terraform that provisions its App
  Service, the VM it is deployed to over ssh. Nothing is drawn by a model: every part and line comes from the project's
  manifests (package manifests, compose files, Dockerfiles, Terraform, CI workflows, vite proxies, deploy configs, read
  only) and what sessions did (servers they started, hosts and clouds they reached, machines they ssh'd into, files
  they touched), and clicking it shows that evidence, each command linked to its session. `chronicle systems [NAME]`
  prints the same in the terminal; `[systems] read_manifests = false` draws from sessions only.
  [Systems map](docs/dashboard.md#systems-map)
- **Whose work, on a hub:** **Sessions** and **All knowledge** show whose each session and lesson is (the person whose
  computer it came from, else that computer) and filter by person. Names on the team's Home, session rows and lessons
  open that person's sessions or lessons. `GET /api/team/who` lists who to filter by, within what the viewer may see.
  [The hub's dashboard](docs/devices.md#the-hubs-dashboard)
- **The hub's dashboard is the team's:** once a hub has people, its **Home** shows the team projects (set up on the
  hub, or sent to by other computers) with the sessions and new lessons of the last 7, 30 or 90 days, who worked on
  each, and the newest lessons and sessions with the person they came from. Admins also see what needs attention:
  people who haven't joined or have no way in, and computers not heard from for a week. Projects only the hub
  computer works on stay off it; **Home › Activity** keeps the charts of every session. People, shared projects,
  computers and the team store move from **Settings › Devices** to a **Team** section for admins, and **Team ›
  Computers** says whose each computer is. A **Hub** marker in the header and a green tint tell the hub's dashboard
  from your own; `[hub] name` names it. [The hub's dashboard](docs/devices.md#the-hubs-dashboard)
- **Search when sharing a project:** **Team › Shared projects** now has one search box instead of a
  list and a folder field. Typing filters the hub's projects by name or folder (arrow keys and Enter pick one); a
  folder starting with `/` or `~` can be shared as it is.
- **Pages that say they're loading:** a page that takes more than a moment shows a thin bar along the top of the
  content area, and the dashboard's first open shows grey blocks in the shape of Home instead of a blank area. After
  4 seconds a note under the toolbar says the page is still loading and counts the seconds, adding what the dashboard
  is busy with when a sync or import is running. With reduced motion there is no shimmer and the bar stays still.

## 0.9.0 (2026-10-05)

- **Shared projects on the dashboard:** on a hub, **Settings › Devices › Shared projects** shows which projects leave
  the computer: each one's folder, who sees it, which computers send to it and how many sessions it holds. At the hub
  computer itself you can share another project, picked from the hub's projects or typed as a folder, or stop sharing
  one; admins elsewhere see the card but can't change it, since it decides what leaves the hub. Shared projects carry
  a **Shared** badge in **Projects** and on their page, and sharing or stopping goes into the audit log.
  [Projects and who sees them](docs/devices.md#projects-and-who-sees-them)

## 0.8.0 (2026-10-05)

- **Projects on the hub, and who sees them:** `chronicle hub project add <folder>` sets up a project on the hub
  before anyone sent to it: a folder on the hub computer and everything below it, named after the folder. The hub's
  own sessions there are filed under it at once, with their knowledge, and other computers can
  `chronicle hub add-folder` to it right away. An admin now says which projects each member or read-only person
  sees: `chronicle hub invite <name> --project <name>` (repeat it for more) or `--all-projects`, changed later with
  `chronicle hub access`, or in **Settings › Devices › People**. A new member or read-only invite without either is refused. Someone
  limited to projects sees only theirs on the hub's dashboard (Home, Sessions, Knowledge and Projects), each session
  as its summary and project lessons: no prompts, transcripts, files or commands, and the hub enforces this on its
  side. Their computers share knowledge only, and only the sessions the hub files under their projects; the hub
  drops anything else they send, and the teammates' lessons they get back come from those projects only. With a
  team store, the hub's own sessions in a project set up there go to Postgres too, so teammates get the hub owner's
  lessons. On the hub, the start-of-session notes and `project_knowledge` in any folder inside such a project use
  that project's knowledge. Admins always see everything, and people added before this release still see every
  project. [Projects and who sees them](docs/devices.md#projects-and-who-sees-them)

## 0.7.1 (2026-10-05)

- **Moving a checkout install to PyPI, documented:** [Updating](docs/install.md#updating) now says that a checkout run
  with `uv sync` or `uv run` has no Update button, and **From a checkout to a PyPI install** moves the hooks, MCP
  servers and background agents to `uv tool install 'agents-chronicle[app]'` without touching your data. Installing
  with a `==version` pin stops `uv tool upgrade`, and the Update button, from going past that version; the page
  says how to drop it. A checkout's version now reads like `0.7.1.dev3+g1a2b3c4`: the last release plus the commits
  since.

## 0.7.0 (2026-10-05)

- **People and roles on a hub, without Tailscale:** an admin invites each person as an admin, a member or read-only,
  with `chronicle hub invite <name> --email … --role …` or **Settings › Devices › People** on the hub. The invite is a
  one-time code, valid for 7 days and shown once, passed on by chat. A member's computer joins with
  `chronicle hub join <address> --code …` and gets a token of its own; a browser opens the invite link. Members open
  the hub's dashboard from their own Chronicle with a short sign-in link, no password. Read-only people see the
  dashboard and send nothing, and only admins change anything there. Removing a person, or revoking one computer or
  browser, takes effect at once, and every invite, role change, removal, join and sign-in goes into an audit log.
  Computers that joined with the hub's shared token keep working until `chronicle hub shared-token off`. Whoever is
  at the hub computer itself is always an admin. A hub can now sit on the company network or VPN behind an HTTPS
  proxy (`[hub] address`), with company sign-in through an auth proxy's header (`[server] auth_header`,
  `trusted_proxies`) for people added on the hub. [People and roles](docs/devices.md#people-and-roles)
- **Teammates' lessons, and a team store in Postgres:** a hub with `[hub] store = "postgres"` (the connection in
  `team-store.env`, the driver from `agents-chronicle[team]`) also keeps what computers share in Postgres: each
  session's details and summary, its project lessons, and an audit log. Lessons belong to their git repository
  wherever each person cloned it, so the same lesson from two people becomes one item that remembers whose sessions
  stated it. After each push, a computer that shares knowledge gets its teammates' lessons for its own repositories
  back, read-only: its MCP tools answer with them, marked as teammates', and the start-of-session notes list them
  under **From teammates' sessions**. Only the hub connects to the database. `chronicle hub store` sets it up and
  shows what it holds, and so does **Settings › Devices › Team store** on the hub itself (tested before it is saved; the
  password never comes back to the browser). On a member, **Settings › Devices** switches what it sends and shows its
  teammates' lessons. [Teammates' lessons, and a team store in Postgres](docs/devices.md#teammates-lessons-and-a-team-store-in-postgres)
- **Share knowledge, keep transcripts:** `chronicle hub join … --share knowledge` (or `[hub] share = "knowledge"`)
  lets a computer add to the hub's knowledge without sending its transcripts. It keeps recording and analyzing its
  own sessions with its own Claude Code or Codex login, and after each analysis sends the hub only the session's
  details, its summary and its lessons about the project. Prompts, shell commands, file paths, transcripts and
  lessons about the person stay on the computer. The hub files these sessions like any other, adds their lessons to
  the project's knowledge base, never analyzes them again, and shows **transcript on <computer>** on their pages.
  [Sharing knowledge only](docs/devices.md#sharing-knowledge-only)
- **Add a folder to a project on the hub:** on a computer that sends its sessions to a hub,
  `chronicle hub add-folder <folder> --project <name>` files the sessions in that folder, and every folder below it,
  under one of the hub's projects: a notes folder, a scratch folder, or a repository the hub doesn't know. Sessions
  already on the hub move too, with their knowledge, and both projects' knowledge bases are rebuilt. The more
  specific match wins: a repository inside the folder whose git remote the hub knows still follows its remote, and a
  folder inside a repository the hub files under another project is refused. `chronicle hub folders` shows what
  goes where, `chronicle hub remove-folder` undoes one, and the hub's **Settings › Devices** lists the folders each
  computer added. [Same project, different folders](docs/devices.md#same-project-different-folders)
- **The dashboard in Japanese:** every page, menu, chart, toast and the ⌘K palette can be shown in Japanese, with
  dates, times and durations written the Japanese way. **日本語** in the status bar (next to the theme toggle) switches
  over, and **Settings › Appearance › Language** picks System, English or 日本語; System follows the browser. Text the
  server writes, such as why a session is waiting, What goes wrong and suggestion evidence, follows the same choice.
  Each browser keeps its own language, so the dashboard on your phone can differ from the one on your Mac.
- **Knowledge in Japanese:** `[analysis] language = "ja"` (or **Status › Analysis › Knowledge language**) has
  Chronicle write summaries, knowledge, knowledge bases, the playbook, glossary definitions, weekly reviews and
  screening reasons in Japanese, and propose Japanese lines for `CLAUDE.md` and `AGENTS.md`. Identifiers, commands,
  file paths and error messages stay as they were, and so do tags, so sessions still group by topic. It applies to
  sessions analyzed from then on; what is already written stays in its language. A line whose marker is already in
  the file is not proposed again in the other language, and What goes wrong recognizes friction notes written in
  Japanese. [Language](docs/analysis.md#language)
- **Session pages open on Details:** the summary, knowledge, context chart, tools and files come first, and
  **Transcript** is one click away. A search result still opens the transcript at the match, and `?tab=transcript`
  or `?tab=details` in a link picks one.
- **Google Antigravity is a source:** `chronicle connect antigravity` (or the Sources page) records Antigravity
  conversations from the step log Antigravity writes beside each conversation's artifacts
  (`~/.gemini/antigravity/brain/<id>/.system_generated/logs/`): prompts, replies, thinking, tool calls with results
  and durations, and tokens per call, plus the workspace, git branch and model from its conversation database.
  Connecting also gives Antigravity Chronicle's MCP server (`~/.gemini/config/mcp_config.json`).
- **Artifacts: what your agents made, in one place.** A new **Artifacts** section in the rail lists the documents, HTML
  pages, diagrams, decks, spreadsheets, generated images, published links, pull requests and commits from every
  session. Each comes from an explicit signal in the transcript (a file written whole, a page published, `gh pr
  create`, a commit), each links to the tool call that made it, and each says whether the file is still on disk as
  written, changed since, or gone. The same file across sessions is one item with its versions. Project pages list
  their latest artifacts, and a session's Details lists what it made. Images and SVG diagrams still on disk show as
  thumbnails, as a grid under **Images** and **Diagrams**, and open full size; a generated image is named after what
  its prompt asked for. Decks, documents, spreadsheets and PDFs a script saved are found too (the session names the
  file, and it was created during the session), with their first page drawn by Quick Look on a Mac; a file made in a
  claude.ai chat links to the chat, where it lives. **Open** shows any file in a new tab, even one deleted since (rebuilt from the archived
  transcript), with an HTML page's scripts sandboxed; on the Mac itself, **Open on this Mac** and **Show in Finder**
  hand a file to its own app. claude.ai chats contribute their artifacts and
  the files they handed over. Agents can search them with the new `find_artifacts` MCP tool. The next sync re-reads
  every session once to find them, imported claude.ai chats included, from the copy Chronicle archived; no analysis
  runs again. [Artifacts](docs/dashboard.md#artifacts)
- **Fixed:** an unanalyzed session without a screening result showed a stray "null" under "Not analyzed yet".
- **Fixed:** claude.ai's artifacts tool was counted as shell commands (its `command` is create/update), images that
  tools return (screenshots, image files read) are now counted, and PRs opened with `gh pr create` in Codex, Copilot
  or on a GitHub Enterprise host now reach the session's PR list.
- **An architecture sketch for each project:** synthesizing a project's knowledge base now also draws its main parts
  (code it owns, ways in, data it keeps, external services) and how they connect, shown hand-drawn at the top of the
  project page. Every part and connection cites the knowledge items behind it, and anything without a valid source is
  dropped. Click a part to see where it comes from; **Excalidraw** downloads it as an editable `.excalidraw` file
  (`/api/diagram?path=`), laid out the same way. The Markdown knowledge base carries it as a Mermaid flowchart, so the
  notes export and the `project_knowledge` MCP tool include it. Existing knowledge bases show it after their next
  synthesis. [Architecture sketch](docs/dashboard.md#architecture-sketch)
- **The sessions behind your files, in VS Code:** a new extension (`vscode-extension/`) adds two
  sections to the Explorer. **Chronicle: This File** lists the sessions that read or changed the open file,
  newest first, with what each did to it; every window follows its own editor. **Chronicle: Files in Workspace** shows
  the files sessions touched in your open folders as a folder tree, leaving out what git ignores, and each file
  expands to its sessions. Click a session to open it in the dashboard. They read the new `/api/file?path=` and
  `/api/files?root=` endpoints, which also match the relative paths Codex sometimes records. Not on the Marketplace
  yet: build it with `npx @vscode/vsce package`. [VS Code extension](docs/vscode.md)

## 0.6.1 (2026-10-02)

- **Suggestions go to the right file:** a preference about how you work (one the analysis marked *global*) is now
  proposed once for your user-level `CLAUDE.md` or `AGENTS.md`, not once for each project it came up in. Other global
  knowledge, like a gotcha about a tool, stays in its project's file, since the user-level file is read in every
  session. Each waiting card can move: **Move to every project**, or from the user level back to the projects it came
  from (`chronicle suggest move ID --to user|project`). Chronicle remembers the choice for that lesson or cause, its
  other waiting cards move along, and your edited wording comes too. Lines you dismissed or applied no longer use up
  one of a file's 8 places, so the next ones come up. A card whose lesson moved to another file, or that is waiting
  for a place, now leaves the queue quietly instead of showing as stale.
  [Moving a line](docs/suggestions.md#moving-a-line)
- **Easier to read at a glance:** on Suggestions, the status filter is one joined switch and **What goes wrong** is a
  link, so **Check again** is the only button in the header. The rail icons show their name and what the section
  holds as soon as you point at them or tab to them, in the app window too.

## 0.6.0 (2026-10-02)

- **Screen imported chats before analyzing them:** years of claude.ai and ChatGPT chats are mostly lookups, rewrites
  and everyday questions, and analyzing all of them would take your plan's limits for days. **Screen N chats** on the
  export's card (Sources › Chat exports) or `chronicle screen` sorts them into *worth analyzing*, *maybe* and *not
  worth it*, each with a topic and a one-line reason, reading only each chat's opening. Rules settle the certain cases
  without a model call (no reply, too short, translating or summarizing pasted text); Haiku (`analysis.screen_model`)
  reads the rest, 60 chats a call, knowing which projects you work on, and keeps anything about your own work at least
  *maybe*. Nothing is analyzed until you choose **Queue N worth analyzing** (`chronicle screen --queue`, `--maybe` for
  the maybes too). The Sessions list gets a *Screening* filter and shows each chat's verdict and reason until it is
  analyzed; `chronicle screen --list analyze` does the same on the command line, `--sample 200` tries it first, and
  `chronicle import --screen` screens right after importing. A chat is screened again when a newer export changes it,
  and a chat queued for analysis now stays queued when a newer export updates it.
  [Screening imported chats](docs/sources.md#screening-imported-chats)
- **Knowledge earns its trust:** every knowledge item now has a stage, *tentative*, *seen once*, *established* or
  *canonical*, computed from how many sessions confirmed it and over how long, never guessed by the model. When
  synthesis finds the same lesson in another session it reports the pair as a duplicate, and the surviving item
  takes over that session: two sessions make it established, three over at least two weeks (or a pin) make it
  canonical, and the reason is kept ("confirmed in 3 sessions over 19 days"). Search, the MCP tools and the
  SessionStart digest list the most trusted knowledge first and label it (`[gotcha · established ×3]`);
  knowledge-base bullets, knowledge cards and the knowledge table show the stage. A superseded item now names the
  item that replaced it and why (duplicate, outdated, contradicted), and established or canonical knowledge that a
  newer session overturns is listed under **Overturned** in that week's review. Existing items get their stage on
  upgrade. [How knowledge earns trust](docs/analysis.md#how-knowledge-earns-trust)
- **Every waiting session says why:** a session that is not analyzed yet explains it on its page (still active and
  analyzed around 14:20, retrying at 16:05 after a timeout, project excluded, too few prompts, from before install
  with backfill off, failed four times…), and **Status** and `chronicle status` count the queue per reason. Sessions
  that will never be analyzed without a change are now counted as *held* instead of *queued*, and the status bar no
  longer counts ready sessions twice. When the whole queue is stopped (paused, automatic analysis off, analyzer not
  found) that is said too.
- **Real context and plan usage from the status line (optional):** `chronicle install --statusline` records, per
  Claude Code session, the peak context window use and, on Pro and Max plans, how far the 5-hour and 7-day limits
  moved while it ran. Claude Code shares these only with its status line, so Chronicle wraps yours: it records the
  numbers, then runs your own status line with the same input, so it looks exactly as before (with none, it shows
  model, context and limits). Session pages and **Status** show the numbers; sessions without a record stay unknown
  rather than zero. `chronicle uninstall` restores your status line.
- **What goes wrong, and fixes to approve:** Chronicle now finds the failures that keep coming back across your
  sessions, from failed tool calls and the friction notes analysis writes, with no model call: Playwright refusing a
  screenshot path or a busy browser, edits on stale context, zsh globs that match nothing, `sleep` polling blocked,
  a missing `timeout`, ports already taken, relative `cd`s, several sessions in one tree, and more. Expected failures
  (tests failing in a dev loop, read-only checks, provider outages, calls you turned down) are kept apart as noise.
  The **What goes wrong** page and `chronicle friction` show each cause's sessions, projects, 12-week trend and
  whether it is still happening, plus the tools that fail most.
- **Suggestions:** one queue of proposed fixes for those causes, and for knowledge confirmed often enough to tell your
  agents: a line for `CLAUDE.md` or `AGENTS.md` (user level, or the project's), the Playwright MCP arguments for
  `~/.claude.json`, or a setup step such as `setopt NO_NOMATCH`, which Chronicle shows and never runs. Nothing is
  written until you apply one: **Preview** shows the diff, the file is backed up first, the line goes inside
  Chronicle's own `<!-- BEGIN chronicle -->` block at the end of the file, and **Undo** takes it back out. You can
  edit the line before applying. Warnings flag a public repository (checked with `gh`), text that looks sensitive,
  and files git doesn't track. A dismissed suggestion never comes back, and one whose cause stopped happening goes
  stale. The queue refreshes after each background sync; the rail shows a badge for new ones, Home shows the top 3,
  and `[suggestions] notify = true` adds a desktop notification. On the command line: `chronicle suggest` (`show`,
  `apply`, `dismiss`, `done`, `undo`, `refresh`). [Suggestions and What goes wrong](docs/suggestions.md)
- **Fixed: a partly downloaded export was "not a chat export".** A large ChatGPT export (gigabytes of
  attachments) whose download stopped early lacks the end of the zip, so it was rejected outright. ChatGPT puts the
  chats first, so Chronicle now reads them from such a zip and says the download was incomplete; if the cut falls
  inside the chats, it says to download the export again.

## 0.5.1 (2026-10-01)

- **Hear about new versions:** turn on **Notify me about new versions** (Status › Updates, or say yes when
  `chronicle install` asks) and the background sync asks pypi.org once a day, dashboard open or not, and shows a
  desktop notification once per release saying how to update. macOS shows it in Notification Center, Linux through
  `notify-send`. Off by default; it sends nothing about you. `chronicle install --notify-updates` (or
  `--no-notify-updates`) answers without asking.

## 0.5.0 (2026-10-01)

- **On your phone:** `chronicle tailnet on` puts the dashboard on your Tailscale network with Tailscale Serve, at
  `https://<computer>.<tailnet>.ts.net/`, for your Tailscale login only (`[server] allowed_hosts` and
  `allowed_users`); nothing is opened to the internet. On a phone the dashboard puts its sections in a tab bar at
  the bottom, uses the whole width, keeps clear of the notch and opens Sessions as cards; **Add to Home Screen**
  gives it an icon and opens it like an app.
- **One archive for several computers:** `chronicle hub enable` makes a computer the hub, and prints the `chronicle
  hub join` command to run on the others. They send their Claude Code and Codex sessions to the hub as each one ends
  and every 15 minutes (`chronicle push` sends now); the hub records and analyzes them, once, and a computer that
  already analyzed sessions hands those analyses over when it joins. Sessions from each computer are filed under the
  hub's projects by git remote, or by `[hub] path_map`. A session's page names the computer it ran on, and the new
  **Settings › Devices** page lists the computers. See [Phone and other computers](docs/devices.md).
- **Install builds the Glossary and the Map:** `chronicle install` now explains that the Glossary and the Map are
  built from analyzed sessions, says how many are waiting and how long the background would take, and offers to
  analyze them now (all, or the newest 20) with a progress bar for each stage: sessions, knowledge bases, glossary,
  map themes. `--analyze all|N|later` answers without asking. Before, a fresh install left both pages empty for
  hours while the background worked through the backlog 6 sessions at a time.
- Ctrl-C during `chronicle analyze` (or install) now stops after the sessions already in progress instead of
  running the rest of the queue.
- Where nothing can run in the background, `chronicle install` no longer says past sessions are analyzed there.
- **Linux:** `chronicle install` runs the background sync and the dashboard as systemd user units, so a Linux box
  can be the hub.

## 0.4.0 (2026-09-30)

- **Search all sessions:** the Search page (now also in the rail) lists every session that mentions a word or
  phrase, with how many times, ordered by most mentions, newest or oldest. Each session shows its mentions in
  transcript order with the terms highlighted and who said them; **Show all** lists every mention. Clicking one
  opens the transcript at that message with the terms still highlighted, including inside folded tool calls.

## 0.3.0 (2026-09-30)

- **Analyze with Codex:** sessions, knowledge bases, the glossary and weekly reviews can now be written by OpenAI
  Codex instead of Claude Code, through your own Codex login. Pick it in **Status › Analysis**, with `chronicle
  config set analysis.backend codex`, or for one run with `chronicle analyze --backend codex`; `codex_model` picks
  the model. `chronicle install` offers Codex when Claude Code is not installed, so Chronicle no longer needs
  Claude Code at all. Codex runs sandboxed like Claude does: no session is saved for the analysis, none of your
  hooks, plugins, MCP servers, `AGENTS.md` or skills load, every tool feature is off, and Chronicle discards any
  reply that follows a tool call. Codex reports tokens but no price, so Codex analyses show no cost.
- `chronicle config set <section.key> <value>` changes one setting from the command line.
- A configured `claude_bin` or `codex_bin` that does not exist now fails the analysis cleanly instead of stopping
  the background run.

## 0.2.1 (2026-09-30)

- Chronicle moved to the [Chatixia-AI](https://github.com/Chatixia-AI) organization, and the documentation to
  <https://chronicle.chatixia.net/>. Old GitHub links redirect.
- `chronicle --version` works; 0.1.2 announced it but never shipped the flag.
- `chronicle search` shows snippet labels dimmed instead of printing `[dim]` markup literally.

## 0.2.0 (2026-09-30)

- **Knowledge overview:** the Knowledge section opens on a page with a card each for the Map, All knowledge, the
  Glossary and Weekly reviews, each with a glance at what is inside; the full list moved to **All knowledge**
  (`#/knowledge/all`; older `#/knowledge?kind=…` links still work).
- **Weekly reviews you can skim:** one week at a time, picked from a strip of weeks: a three-line TL;DR, the week's
  numbers against the week before, active time per day, where the time went, outcomes and the knowledge captured,
  then themes and short lists (shipped, learned, still open, slowed you down, try next) whose items open to their
  full text. The long write-up is folded away. New reviews are written to word limits and include the TL;DR.
- **Knowledge bases and the global playbook you can skim:** a TL;DR, a few figures, chips that jump to each
  section, a filter, and each section as a card of short bullets that open to their detail and the sessions they
  came from; the overview is folded away. Synthesis now writes a TL;DR, a short overview and a title per bullet,
  within word limits; existing knowledge bases pick this up the next time they are synthesized.
- **Guided setup:** `chronicle install` (or `chronicle setup`) lists the coding agents and MCP clients it finds,
  asks which to record, imports their past sessions, asks whether to run in the background from login (the
  15-minute sync and the always-on dashboard; previously always installed) and ends with the dashboard address,
  offering to open it. `--no-launchd` / `--no-ui` now also remove those agents if installed, so
  `chronicle install --no-launchd --no-ui` turns background running off.
  `--yes` (or no terminal) takes the defaults; re-running asks only about newly installed agents. Running
  `chronicle` on its own now shows help and, before setup, points to `chronicle install`.
- **Claude.ai and ChatGPT chats:** import a claude.ai or ChatGPT data export with **Settings › Sources › Chat
  exports › Import export…** or `chronicle import <zip>`; the format is recognized. Chats become sessions you can
  search, browse and analyze on demand; re-importing a newer export adds only new and changed chats. The account
  files in the export are never read.
- **Codex Cloud tasks:** `chronicle connect codex-cloud` (or **Settings › Sources › Codex Cloud**) records the tasks
  you ran at chatgpt.com/codex, through the `codex cloud` CLI: title, repository, changed files, the diff and a link,
  archived for good. Opt-in, since it goes online. The CLI has no task conversations, so these are not analyzed.
- **Update from the dashboard:** Settings › Status › Updates upgrades Chronicle with whatever installed it (uv
  tool, pipx, pip; a checkout install is reinstalled when its files changed) and restarts the dashboard; the
  desktop app links to the latest release. Checking PyPI happens only when you click **Check for updates**. An
  update on offer shows a notification (once per release, or per new commit for a checkout install), a dot on
  Settings and a chip in the status bar; a checkout's Updates card lists the commits and files it would bring in.
  **Check for updates daily** (off by default, `[updates] check_daily`) asks PyPI once a day while the dashboard
  is open; the last answer survives a restart. See [Updating](docs/install.md#updating).
- **Map search finds everything:** **Find terms** opens every match on the map at once (terms by name, alias or
  definition, plus themes, categories, projects and agents by name), highlights them, trims the branches on the way
  to just the path, and lists the matches in the side panel. The search is kept in the link.
- The Sessions list and a project's session table have an **Agent** column (sortable), with the same colour per agent as
  the Sources page; Codex Cloud tasks show as Codex · Cloud.
- The Sessions sidebar sorts and groups by last activity, so a long-running session stays under Today.
- **MCP page:** Settings › MCP shows which agents and clients have Chronicle's MCP server (adding or removing
  the other clients moved here from Sources), ready-to-copy config for most clients, VS Code, Codex and Claude
  Code, the tools and what to ask.
- **Export sessions:** **Export** on a session page, or on a selection in the Sessions list, downloads Markdown
  (overview and conversation), JSON (every event) or the original transcript; several sessions come as a .zip with
  an index. `chronicle export <id>… --format md|json|raw` does the same from a terminal. Secrets are redacted except
  in the original transcript.
- **Analyze a selection:** tick sessions in the Sessions list (shift-click for a range, or **Select all matching**)
  and choose **Analyze** to run them in one background job, skipped ones such as imported chats included.
- **Activity:** clicking the status bar opens what is running, with a progress bar, time so far and an estimate of
  time left, plus the analysis queue and recent results. The dashboard serves the page it started with, so an
  upgrade (or an edit to a checkout) never pairs a new page with old code before the restart.
- **Sources** is a list: a row per coding agent, chat export and MCP client that opens to its checks and actions.
  A connected agent with a failing check opens on its own.
- **New dashboard design:** a simpler VS Code layout in Apple's Liquid Glass style. An icon rail and per-section
  sidebar replace the top navigation, with a glass toolbar and breadcrumbs, a status bar, and a ⌘K palette that
  searches sessions, knowledge, projects and glossary terms and runs commands. New **Appearance** settings (theme,
  Reduce transparency).
- **Session pages** lead with headline figures, the summary and its knowledge, then switch between **Transcript**
  (one-line tool calls; **Load earlier** for links into the middle of a session) and **Details**, with an outline of
  prompts and changed files that follows the scroll.
- **macOS app:** no title bar. The sidebar is native glass with the traffic lights on it; the toolbar drags and
  zooms the window; the window follows the page's theme and the macOS Reduce transparency setting.
- New app icon, favicon and per-page titles.
- **MCP for more clients:** `chronicle connect claude-desktop`, `cursor`, `windsurf` or `gemini` (or **Settings ›
  MCP › Other MCP clients**) gives them Chronicle's MCP server; `chronicle mcp --print-config` prints an entry
  for any other client. Tool results are now redacted like analysis digests, and the tools are marked read-only.
  New [MCP server](docs/mcp.md) docs page.
- MIT license. Documentation split into a short README and pages under `docs/` (English and Japanese), with
  screenshots made from demo data (`docs/demo/make_demo.py`).
- The source is public, and the documentation is online at <https://chronicle.chatixia.net/> (English and Japanese), built from
  `docs/` and the READMEs with MkDocs.

## 0.1.2 (2026-09-28)

- **Map:** stack any of four levels (Category, Theme, Project, Agent); terms open into the knowledge items and
  sessions behind them.
- **Themes:** Claude groups large glossary categories into named themes (`chronicle glossary --themes`).
- `chronicle --version` reports the installed version.
- Japanese README.

## 0.1.1 (2026-09-28)

- User-facing text describes Chronicle as agent-neutral (Claude Code, Codex, GitHub Copilot, IBM Bob).

## 0.1.0 (2026-09-28)

- First release on PyPI as `agents-chronicle`, and the macOS desktop app (menu bar, background sync, DMG).
- Records Claude Code, Codex, GitHub Copilot and IBM Bob sessions; analyzes them with `claude -p` into summaries and
  knowledge; project knowledge bases, glossary and Map, weekly reviews, Markdown vault, CLI and MCP server.
