# Phone and other computers

[← Interlatch](../README.md) · [Docs index](README.md)

Interlatch keeps its archive on one computer. You can also open its dashboard on your phone, and keep the sessions
of your other computers in the same archive. Both go through [Tailscale](https://tailscale.com), a private network
between your own devices: nothing is opened to the internet.

One computer is the **hub**: the only one that records sessions, analyzes them and serves the dashboard. Pick the
one that is switched on most of the time, such as a desktop Mac, a Mac mini or a Linux box. Your other computers
**send** it their sessions, and your phone opens its dashboard. Because only the hub writes, each session is
analyzed (and paid for) once, and there is never anything to merge.

A hub can also serve a team. Each person joins with an invite and a role ([People and roles](#people-and-roles)),
and sees every project or only some ([Projects and who sees them](#projects-and-who-sees-them)). The company network
can take the place of Tailscale ([Reaching the hub without Tailscale](#reaching-the-hub-without-tailscale)).

## Your phone

1. Install Tailscale on the hub and on your phone, and sign both in to the same account.
2. On the hub, run:

   ```bash
   interlatch tailnet on
   ```

   The first time, Tailscale may print a link to turn on Serve and HTTPS certificates for your tailnet: open it,
   and run the command again if it stopped.
3. On your phone (with the Tailscale app on), open the address it prints, `https://<computer>.<tailnet>.ts.net/`.
   On an iPhone, **Share › Add to Home Screen** gives it an icon and opens it like an app; on Android, **⋮ › Add to
   Home screen**.

On a phone the dashboard puts its sections in a tab bar at the bottom and uses the whole width; Sessions opens as
cards. Everything works as on the computer: search, knowledge, reviews, pinning and dismissing.

How it works: Tailscale Serve forwards that HTTPS address to the dashboard, which still listens on 127.0.0.1 only.
The dashboard answers to that name (`[server] allowed_hosts`) and lets in only your Tailscale login
(`[server] allowed_users`), which Serve passes along. `interlatch tailnet on --anyone` lets in everyone in your
tailnet who has a Tailscale login. Serve names no login for a tagged device or a Tailscale Funnel visitor, so
those get in only once the hub has people and they sign in. `interlatch tailnet status` shows the setup; `interlatch tailnet off` takes the dashboard off the tailnet.
The dashboard has to be running on the hub: `interlatch install` keeps it running in the background.

## Your other computers

On the hub, after `interlatch tailnet on`:

```bash
interlatch hub enable
```

It prints a command with the hub's address and a token. [Install Interlatch](install.md) on each other computer,
then run that command there:

```bash
interlatch hub join https://pc.tail1234.ts.net --token …
```

That token is the hub's shared token, which suits your own computers. For other people's computers, invite each
person instead ([People and roles](#people-and-roles)).

It sends every Claude Code and Codex session on that computer to the hub, which records and analyzes them. From
then on:

- **New sessions go to the hub** as each one ends (the SessionEnd hook) and every 15 minutes (the background sync),
  once `interlatch install` has set those up on that computer. `interlatch push` sends them right away.
- **The hub knows where each session ran.** A session's page says which computer it came from, and **Team ›
  Computers** lists the computers, whose each is, their sessions and when each last sent something (`interlatch hub
  status` in a terminal).
- **Analysis runs only on the hub.** If the computer already analyzed sessions with its own Interlatch, it hands
  those analyses over when it joins, so the hub does not pay to analyze them again.
- **That computer's own dashboard and MCP tools stop updating.** They keep what they had; open the hub's dashboard
  instead (**Settings › Devices** has a link).

### Same project, different folders

The same repository often lives in different folders on different computers. The hub files each session under the
matching project of its own:

1. **By git remote.** The other computer reports each project folder's git remote (Codex sessions record theirs
   too); a session goes to the hub's folder with the same remote.
2. **By a folder added on that computer**, for a folder whose sessions belong to one of the hub's projects: a notes
   folder, a scratch folder, or a repository the hub doesn't know. On that computer, run:

   ```bash
   interlatch hub add-folder ~/work/client-notes --project demo-app
   ```

   `--project` takes a project's name or its path on the hub; `interlatch hub folders --list` lists them. A project
   nobody has sent to yet can be set up on the hub first ([Projects and who sees
   them](#projects-and-who-sees-them)). The folder and everything below it go to that project, including sessions
   already on the hub: they move with their knowledge, and both projects' knowledge bases are rebuilt. The more
   specific match wins, so a repository inside the folder whose git remote the hub knows still follows its remote. A
   folder inside a repository the hub files under another project is refused, because a repository belongs to one
   project. `interlatch hub folders` shows what goes where. **Settings › Devices › Projects on the hub** does the same
   from the dashboard: **Join a project** picks one of the hub's projects and a folder here. To take back a folder
   added by mistake, see [Leaving](#leaving). The hub's **Team › Computers** lists the folders each computer added.
   On this computer's **Projects** page and sidebar, the hub's projects that folders here go to come first, under
   the hub's name, each above its projects here and with the team's sessions there (as of the last push). A hub
   project opens on the hub's dashboard, signed in, and a project here that goes to one has a folder icon with a
   teal cloud. Elsewhere, such as in a group or on its own page, it has an **On the hub** badge, with the hub's name
   for it when that differs.
3. **By `[hub] path_map`**, for projects without a remote, in the hub's [configuration](configuration.md#hub):

   ```toml
   [hub]
   path_map = { "/home/me/code" = "/Users/me/Projects" }
   ```

Otherwise the session keeps the folder it ran in. A session page shows the original folder when you hover over the
computer's name.

### A group as one project on the hub

A [group of your projects](dashboard.md#project-groups) can go to the hub as one project. Edit the group on the
**Projects** page, pick the hub's project under **On the hub**, and save; this computer then sends the group's
projects there from its next push, as if you had added each of their folders to it. It stays linked:

- A project that joins the group later, by its folder or by hand, goes too.
- A project you keep out of the group stays here, even inside a folder of one that goes. Sharing knowledge only, it
  isn't sent at all; a computer that sends transcripts sends everything, and the group only decides where it's filed.
- A project that leaves the group, or the whole group when you pick **Not shared**, stops going. What it already
  shared stays in that project on the hub, as when you [leave](#leaving) one.
- A folder you added by hand, or a repository whose git remote the hub files elsewhere, wins over the group, as
  above. A project recorded on another computer (such as `server:/root/app`) can't go from here.

The project must already be on the hub (a member's computer can't make one), and the choice is made on this computer
itself, not from another device. **Settings › Devices › Projects on the hub** shows the group under that project, and
the Projects page and sidebar list the group's projects under that project as well as in the group.

### What is sent

- From each `claude_dirs` folder: `projects/` (transcripts, subagent threads, memory notes) and `history.jsonl`.
  From each `codex_dirs` folder: `sessions/`, `memories/` and Codex's session index.
- Projects in `sources.exclude_projects` are never sent.
- GitHub Copilot, IBM Bob, Google Antigravity, Codex Cloud and chat exports are not sent: connect or import those on the hub.
- Files travel over HTTPS (inside your tailnet, or through the HTTPS proxy in front of the hub), compressed, and are
  checked against a SHA-256 on arrival. Only files the hub lacks, or has an older copy of, are sent. Each computer
  proves itself with a token kept in `~/.interlatch/hub-token`, readable by your user only: the hub's shared
  token, or a token of its own when it joined with an invite. `interlatch hub enable --rotate` replaces the shared
  token; the computers that use it then have to join again.
- The hub keeps what it receives under `~/.interlatch/machines/<computer>/` and archives it like its own.

### Sharing knowledge only

A computer can keep its transcripts and still add to the hub's knowledge. Join with `--share knowledge`:

```bash
interlatch hub join https://pc.tail1234.ts.net --token … --share knowledge
```

(or, on a computer that already joined, `interlatch config set hub.share knowledge`, or **Settings › Devices › What this
computer sends** in its dashboard). That computer then works like a
standalone Interlatch: it records its own sessions, analyzes them itself (with its own Claude Code or Codex login,
Bob API key or model provider), and keeps its dashboard and MCP tools up to date. After each analysis it sends the hub only:

- each analyzed session's details: when it ran, agent, model, branch, tokens and cost, which tools it used;
- its analysis: title, summary, outcome, highlights and what went wrong;
- the lessons it produced about the project.

It does that only for sessions the hub files under one of its projects: those in a folder added with
`interlatch hub add-folder`, and those in a repository whose git remote the hub already files under a project
([Same project, different folders](#same-project-different-folders)). Sessions in any other folder stay on the
computer, and its push counts them as "kept here". A hub that has never run a session in a repository itself doesn't
know its remote, as with a hub in Docker or on a server, so add your folder for each project there. To share
sessions from every folder, join with `--all-folders`, run `interlatch config set hub.all_folders true`, or choose
**Every folder** under **Settings › Devices › What this computer sends**.

Prompts, shell commands, file paths, transcripts and lessons about you (global ones and preferences) never leave
the computer. Sessions from GitHub Copilot, IBM Bob and Google Antigravity are shared too; imported chats are not.

On the hub these sessions are filed like any other from that computer (by git remote, an added folder or
`path_map`) and their lessons join the project's knowledge base. A session page shows **transcript on
<computer>** instead of the transcript, and the hub never analyzes these sessions again, so it needs no login or key
to analyze them. It still builds each project's knowledge base and glossary with its own. **Team › Computers** marks
the computer **knowledge only**. A session the hub already has the transcript of keeps the hub's record.

### A hub that takes knowledge only

Each computer chooses what it sends, so one that joined without `--share knowledge` sends its transcripts. To keep
every transcript off the hub, whoever's computer it is, make the hub take knowledge only. At the hub:

```bash
interlatch config set hub.accept knowledge
```

or, as an admin, switch on **Knowledge only** in **Team › Hub settings**. The hub then turns away any computer that sends
transcripts, with the shared token or a person's own token, and tells it what to run: `interlatch config set hub.share
knowledge`, then `interlatch push`. **Team › Computers** marks such a computer **sends transcripts: turned away**, and
`interlatch hub` lists it the same way. A computer that joins with an invite is set to share knowledge on its own.
Transcripts the hub already has stay; `interlatch forget` removes a session for good. Any value other than
`"everything"` or `"knowledge"` counts as `"knowledge"`, so a typo never lets transcripts in. The audit log records who
changed it.

### Teammates' lessons, and a team store in Postgres

A hub that several people share can keep the team's record in Postgres, and send each computer that shares
knowledge what its teammates learned. On the hub:

```bash
uv tool install 'interlatch[team]'    # the Postgres driver, needed on the hub only
# PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD (and PGSSLMODE, require by default), one per line:
$EDITOR ~/.interlatch/team-store.env && chmod 600 ~/.interlatch/team-store.env
interlatch config set hub.store postgres
interlatch hub store                          # connects, sets up its tables, and shows what it holds
```

Then restart the dashboard, which is what receives the sessions. Or, once the driver is installed, open **Team › Team
store** on the hub itself: fill in the connection, **Test connection**, then **Save**. Nothing is saved
unless the connection works, the password is never shown again, and only an admin can change these settings
([People and roles](#people-and-roles)).

- **What it keeps.** Every session a computer shares, its details, summary and project lessons, is written to
  Postgres first and then to the hub's own database, which the dashboard still reads. If Postgres can't be reached,
  neither keeps it, and the computer sends it again at its next push.
- **Lessons belong to a repository.** A lesson learned in a git repository belongs to that repository (its remote),
  wherever each person cloned it; one learned in a folder without a remote belongs to that folder's project on the
  hub. The same lesson from two people (same kind and title) becomes one item that remembers whose sessions stated
  it.
- **Teammates' lessons come back.** Each time it shares, a computer that shares knowledge gets its teammates' lessons
  for the repositories it has clones of, the projects it shared sessions in, and those it added folders to. It shares
  after each analysis (when a session ends, and every 15 minutes with the background agent from `interlatch install`),
  and at once with `interlatch push` or **Share now** in **Settings › Devices**. They are
  kept read-only in its own database, under its own folder for that repository, where its MCP tools answer with them
  (marked as teammates') and the start-of-session notes list them under **From teammates' sessions**. Its dashboard
  shows them on project pages and in **All knowledge**, marked **from** the computers whose sessions stated them;
  the source **From teammates** there lists only them, and **Read them** in **Settings › Devices** opens that list.
  Lessons it stated itself are not sent back. One it dismisses stays dismissed; one the team no longer has disappears.
  `interlatch hub status` and its **Settings › Devices** show how many it holds; **Share now** there
  pushes at once.
- **Only the hub connects to the database.** Computers never get its address or password, so it can sit on a
  network only the hub reaches. Its tables live in the schema `team`, with an audit log of every push and pull
  (counts only), and each upgrade step runs once, recorded by name.

Not yet: of the sessions the hub analyzes itself, only its own sessions in a project set up on it go to Postgres
([Projects and who sees them](#projects-and-who-sees-them)); those other computers send as transcripts
(`share = "everything"`) don't. When the same lesson arrives in two languages, the latest wording wins.

### Leaving

A computer that shares knowledge can leave one of the hub's projects and stay in the others. In **Settings ›
Devices › Projects on the hub**, each project has these buttons:

- **Leave**: this computer stops sharing its sessions there and stops getting the project's teammates' lessons.
  What it already shared stays on the hub, in the project, for the team. **Rejoin** undoes it. On the command line:
  `interlatch hub leave --project <name>` and `interlatch hub rejoin --project <name>`. They are kept as `[hub] left`
  in the computer's [configuration](configuration.md#hub). The hub's **Team › Projects** marks a computer that left.
- **Remove** next to a folder: for a folder added to the wrong project. Its sessions stop going there, and the hub
  deletes what this computer shared from that folder, with its lessons, from its database and its team store.
  Unlike `interlatch hub purge`, the hub takes them again if the folder is later added to the right project. On the
  command line: `interlatch hub remove-folder <folder>`. A computer that sends transcripts keeps them on the hub: the
  hub files them by their git remote or their own folder again.

`interlatch hub leave`, or **Leave the hub…** in **Settings › Devices**, makes the computer record and analyze its own
sessions again; the hub keeps what it was sent. Joining again takes a new invite. `interlatch hub disable` on the hub
stops it accepting sessions.

## People and roles

A hub that several people share knows who each of them is. An admin invites each person with a role, each
person's computers get a token of their own, and the hub's dashboard opens without a password. None of it needs
Tailscale.

| Role | What they can do |
| --- | --- |
| `admin` | everything a member can, and on the hub's dashboard: invite people, change roles, remove people, and change the hub's settings (the team store, people, the shared token) |
| `member` | their computers send to the hub and get teammates' lessons back; they see the hub's dashboard |
| `readonly` | they see the hub's dashboard; they send nothing and change nothing |

Members and read-only people see the projects they were given, every project or only some ([Projects and who sees
them](#projects-and-who-sees-them)); admins always see every project. Only admins change anything on the hub's
dashboard: settings, Sync, analyses, pinning and dismissing. A member's own Interlatch works as before.

Until you add the first person, computers send with the shared token, and the hub's dashboard has no sign-in, so it
opens only at the hub itself and for the Tailscale logins `interlatch tailnet on` lets in ([Your phone](#your-phone)).
Anyone else, from another device or through a reverse proxy, is turned away with how to add the first admin. Once
the hub has people, everyone who opens its dashboard from another device signs in, through Tailscale too.

### Whoever is at the hub is an admin

Someone at the hub computer itself is always an admin: the `interlatch hub` commands run there, and so does the
dashboard opened on that computer. That is how the first people are added. It is also how you recover a hub whose
admins are gone: at the hub, run `interlatch hub role ana@example.com admin`. A person with the admin role can't
demote or remove the hub's last admin; at the hub itself you can.

### Inviting someone

On the hub, run:

```bash
interlatch hub invite "Ana Lima" --email ana@example.com --role member --all-projects
```

or use **Team › People › Invite someone** in the hub's dashboard. `--role` is `member` when you leave
it out. The email is optional, but company sign-in finds people by it. A member or read-only person sees nothing
until you say which projects: `--all-projects` for every project, or `--project <name>` for one (repeat it for
more; [Projects and who sees them](#projects-and-who-sees-them)). Inviting someone new without either is refused. An
admin always sees every project. You get:

- a code, such as `K7PQ-M2XD-9HNA`;
- the command for Ana's computer: `interlatch hub join https://interlatch.example.internal --code K7PQ-M2XD-9HNA --share knowledge`;
- a link for a browser: `https://interlatch.example.internal/signin?code=K7PQ-M2XD-9HNA`;
- when the code expires.

A code works once, for 7 days. The hub sends no email: pass the code on by chat. It is shown once, because the hub
keeps only a hash of it. If it is lost or has expired, make a new one: run `interlatch hub invite` again with the same
email, or use **New invite** next to the person. One code joins one computer or signs in one browser, so someone
with a laptop and a phone needs two. Case and dashes don't matter when a code is typed.

The command and the link use the hub's address, `[hub] address`, which `interlatch hub enable --url <address>` sets.
`interlatch hub people` lists everyone with their role, the projects they see and their computers.

### Joining a computer

On the person's computer, after [installing Interlatch](install.md). [Joining your team's hub](join-a-hub.md) is a
step-by-step guide to send them:

```bash
interlatch hub join https://interlatch.example.internal --code K7PQ-M2XD-9HNA --share knowledge
```

The hub trades the code for a token for that person and that computer, kept in `~/.interlatch/hub-token`. The
token only works from that computer. From then on the computer sends like any other
([Your other computers](#your-other-computers)). `--share knowledge` keeps its transcripts on the computer
([Sharing knowledge only](#sharing-knowledge-only)); leave it out to send everything. Joining again with a new code
for the same person replaces the computer's old token. A code for someone else is refused while the computer is
still joined as another person, so an invite can't take a computer over: an admin first uses **Revoke** next to it
([Removing someone](#removing-someone)), then it joins with the new code.

A computer the hub already knows (one that sent to it with the shared token) joins only from itself. Interlatch keeps
a key in `~/.interlatch/machine-key`, the hub records it the first time the computer says hello, and `hub join
--code` shows it, saying hello with the shared token first if the computer still has it. Someone else's invite used
with that computer's id is refused, so it can't claim the computer and its sessions. For a computer that can't show
its key (it last sent with an older Interlatch, and the shared token is off now), an admin makes an invite for that
one computer: `interlatch hub invite <name> --computer <name or id>`, with its name as **Team › Computers** shows it
or the id the refused join printed. That code joins that computer only.

### Opening the hub's dashboard

- **From your own Interlatch.** On a computer that joined, **Settings › Devices › Open the hub's dashboard** asks the
  hub for a sign-in link, valid for 5 minutes, and opens it in your browser. There is no password.
- **With the invite link.** Opening `…/signin?code=…` signs that browser in. You can also paste the code on the
  hub's sign-in screen. Use this for a phone, or for someone with no computer joined to the hub.
- **With company sign-in**, when the hub sits behind it ([below](#company-sign-in)).

A browser stays signed in until 30 days after it was last used. **Sign out** in the header ends it.

### The hub's dashboard

Once the hub has people, its **Home** is the team's. It shows the team projects (the ones set up on the hub, and the
ones other computers send to) with the sessions and new lessons of the last 7, 30 or 90 days in each, who worked on
them, and the newest lessons and sessions, each with the person whose computer it came from. **Who worked on what**
lists each person with the projects they worked on in the period; it counts no one's sessions or lessons, since a
tally per person reads as a score and a lesson count mostly counts what went wrong. A session counts by the
day it ran, a lesson by the day it was written, so a computer that shares older sessions it analyzed this week adds
lessons to the week without adding sessions to it. A project's own page counts all its sessions. Admins also see what
needs attention: people who haven't joined yet or have no way in, and computers the hub hasn't heard from for a week.
A project only the hub computer works on stays off the team's Home; **Activity** (top right of Home) keeps the charts
of every session on the hub. Someone limited to projects sees their projects only.

**What's new.** When another computer shares sessions or lessons the hub didn't have, or a computer joins, the
**Team overview** icon shows a count, so does the browser tab (`(3) Team overview — Interlatch`), and an open dashboard
says what came in: *New from Yuma in AI-BPO-Resona: 2 sessions, 4 lessons*. Opening Team overview lists what is new
since your last visit, adds **+2** to that project's sessions and lessons, and clears the count. A session a computer
sends again because it went on is not new; a lesson its new analysis found is. What you saw is kept on the hub, per
person, so another browser knows it too; the first time, the last 7 days are new. Someone limited to projects hears of
those projects only, and only admins hear of computers joining. The hub keeps this for 90 days.

Admins run the hub from **Team** in the sidebar: **People**, **Shared projects**, **Computers**, **Team store** and
**Hub settings** (the hub's name and address, **Knowledge only**, the model that writes knowledge bases, updates and
backups).
Every page of a hub's dashboard says it is one, so it never passes for your own: a green band across the top with the
hub's name, its address and what kind of hub it is, a **Hub** line in the status bar, and a green tint. The name is
the hub computer's unless you set one: `interlatch config set hub.name "Resona team"` on the hub.

A **dedicated hub** (`[hub] dedicated = true`, which [the Docker image](docker.md) sets) is a server for the team with
no sessions of its own. Its dashboard opens on the team's Home and leaves out what only a person's own computer needs:
**Sync**, **Activity**, **Artifacts**, **Suggestions**, and **Settings › Sources**, **MCP** and **Devices**. Under
**Team**, **Projects** takes the place of Shared projects: an admin makes a project there by name, from any browser.

**Sessions** and **All knowledge** list whose each session and lesson is, under its project, and take a person in the
filters: everyone's, or one person's (or one computer's that no one joined). A name on the team's Home, a session row
or a lesson opens that person's sessions or lessons.

### Read-only people

A read-only person only looks. Send them the browser link rather than the join command: the hub refuses what a
computer joined with their code sends ("read-only people can't send to the hub"). Make them a member to let their
computer send.

### Projects and who sees them

A member or read-only person sees every project on the hub, or only some. Someone limited to projects sees only
those on the hub's dashboard, and their computers share knowledge only, for those projects only. Admins always see
every project. People added before projects could be chosen see every project, as they did.

A project can be set up on the hub before anyone sends to it. The hub's **Projects** page and sidebar list it from
then on, with a stacked-folders icon and "no sessions yet", and open **Team › Projects** for it until sessions arrive. Say the
hub owner keeps Resona in
`~/Projects/Work/Resona`, and Aki and Ben should see Resona and nothing else. On the hub:

```bash
interlatch hub project add ~/Projects/Work/Resona
interlatch hub invite Aki --email aki@example.com --project Resona
interlatch hub invite Ben --email ben@example.com --project Resona
```

On Aki's computer (and the same on Ben's, with Ben's code):

```bash
interlatch hub join https://interlatch.example.internal --code XXXX-XXXX-XXXX
interlatch hub add-folder ~/work/Resona --project Resona
```

- **A project set up on the hub** is a folder on the hub computer and everything below it, named after the folder.
  The hub's own sessions there are filed under it at once, with their knowledge, and so are new ones. Other computers
  can add a folder to it right away, before anything was sent to it. The folder must exist on the hub or have
  sessions that ran in it. It can't be `/`, your home folder or a folder above it, and it can't sit inside or around
  another project set up this way. `interlatch hub project list` lists the hub's projects and marks the ones set up here. On the dashboard, **Team › Shared projects**
  shows the same, with who sees each project and which computers send to it, and a **Shared** badge marks them in
  **Projects**. At the hub computer itself, that card also shares another project or stops sharing one.
  `interlatch hub project remove <folder>` undoes one: the hub's own sessions go back to their own folders, and what
  other computers sent stays where it was filed until they send it again.
- **Which projects a person sees.** `--project` takes the name or the path of any project on the hub, set up here or
  not, and can be repeated; `--all-projects` gives every project.
  `interlatch hub access <email|id> --project <name>` (or `--all-projects`) changes it later, from their next request;
  what their computers already sent stays. It replaces the list, so name every project they keep:
  `--project Resona --project Website` gives Ben Website and keeps Resona. `interlatch hub people` shows what each
  person sees. On the hub's dashboard, **Team › People** lets an admin choose the projects when inviting someone and
  change them later; on a hub with more than six projects, a search box finds them by name or folder.
- **A new project isn't added to anyone's list.** Someone limited to projects doesn't see a project created
  after they were given theirs: it isn't on the hub's dashboard for them, **Join a project** answers "Nothing
  matches.", and `add-folder` says the hub has no such project. Give it to each person who should see it, as
  above. Admins, and people who see every project, see it at once.
- **Joining.** A computer that joins as someone limited to projects shares knowledge only, with or without
  `--share knowledge` ([Sharing knowledge only](#sharing-knowledge-only)), and `interlatch hub join` lists the projects
  it shares. `add-folder` then files the sessions in `~/work/Resona` under Resona. A repository whose git remote the
  hub already files under Resona goes there without a folder being added.
- **What their computers send.** Only the sessions the hub files under their projects: those in a folder added to
  one of them, or in a repository whose git remote the hub files there. Other sessions stay on the computer. The hub
  refuses transcripts from them, drops anything else they send, and tells their computers only about their projects
  (names and git remotes). The teammates' lessons they get back come only from sessions filed under those projects.
- **What they see on the hub's dashboard.** **Home**, **Sessions**, **Knowledge** and **Projects**, for their
  projects only, with each project's knowledge base. Only analyzed sessions are listed, each as its summary and
  project lessons: no prompts, transcript, files, shell commands or tool calls, and no export. Searching sessions
  matches titles and summaries only. Lessons about a person (preferences and global ones) never show. Everything
  else, such as the Glossary, the Map, Artifacts, Reviews, Suggestions, What goes wrong and the hub's settings,
  answers "you see only some projects on this hub". The hub enforces this itself: it answers them on those pages
  only, and every request they make sees only their projects' rows in its database.
- **The hub owner's lessons.** With a team store ([Teammates' lessons, and a team store in
  Postgres](#teammates-lessons-and-a-team-store-in-postgres)), the hub's own analyzed sessions in a project set up on
  it go to Postgres like a member's: details (times, counts, models), the project's folder, and the summary and
  project lessons as the analysis wrote them; no prompts, transcripts or lessons about you. The summary and lessons are
  not redacted, so they can name files or commands the session dealt with. So Aki's and Ben's agents get the hub owner's Resona lessons too. The hub sends them whenever
  a computer asks it for teammates' lessons.
- **Notes inside the project.** On the hub computer, the start-of-session notes and the MCP tool `project_knowledge`
  use the project's knowledge in any folder inside a project set up there.
- **The shared token is not limited.** A computer that sends with the hub's shared token is nobody in particular, so
  no project limit applies to it. Once everyone has joined with an invite, turn it off with
  `interlatch hub shared-token off` ([below](#the-shared-token)); `interlatch hub access` reminds you while it is on.

### The shared token

Computers that joined with `--token` keep sending with the hub's shared token after people are added, so nobody is
cut off. The shared token names no computer, so whoever holds it can send as any computer that has not joined as a
person; a computer that joined with an invite is its person's, and only its own token sends as it. Once everyone has
joined with an invite, turn it off on the hub:

```bash
interlatch hub shared-token off
```

or with the switch in **Team › People**. From then on the hub refuses the shared token, and a computer
that still uses it has to join again with a code. `interlatch hub shared-token on` turns it back on. The shared token
never opens the dashboard. It belongs to nobody in particular, so no [project
limit](#projects-and-who-sees-them) applies to a computer that sends with it.

### Removing someone

`interlatch hub remove ana@example.com`, or **Remove** next to the person in **Team › People**, takes a
person off the hub. Their unused codes, their computers' tokens and their browser sessions stop working at the next
request. What their computers sent stays on the hub.

To cut off one computer or one browser and keep the person, a lost laptop say, use **Revoke** next to it in the
same list. That computer can join again with a new code, as the same person or as someone else. `interlatch hub role ana@example.com readonly` changes a
role, and `interlatch hub access ana@example.com --project demo-app` the projects someone sees. These commands take an
email or the id that `interlatch hub people` shows.

### Taking back what a computer sent

Removing someone, or limiting the projects they see, keeps what their computers already sent. To remove it from the
hub, run on the hub:

```bash
interlatch hub purge ana@example.com --outside-access      # what Ana's computers sent outside the projects she sees
interlatch hub purge ana@example.com --project demo-app    # what they sent to demo-app
interlatch hub purge "Ana's laptop" --project demo-app     # one computer: its name, or its id from `interlatch hub status`
```

It lists the sessions by project and asks before removing anything (`--yes` doesn't ask). They go for good: their
summaries, lessons and notes, any transcripts that computer sent, their copy in the team store with the lessons only
they stated, and the knowledge bases built from them, which the next sync builds again from what is left. The
computer keeps its own copy, and the hub refuses those sessions if it sends them again. Weekly reviews already written
don't change, and a teammate's computer that already fetched lessons from them keeps its copy until it next fetches.
The audit log records each purge. The hub's own sessions can't be purged; `interlatch forget <id>` removes one.

### The audit log

The hub records who added, invited, changed the role or the projects of, removed or revoked whom, and each time a
computer joins or a browser signs in, with the time. What is done at the hub itself is recorded as **this
computer**. **Team › People** shows the latest entries. Codes, tokens and browser sessions are stored
only as hashes.

## Reaching the hub without Tailscale

Tailscale is one way to reach a hub. Many companies don't allow it, and a hub works just as well on the company
network or over the company VPN. Say the hub should be reached at `https://interlatch.example.internal`:

1. **Set the address and add people first.** At the hub, `interlatch hub enable --url
   https://interlatch.example.internal` sets `[hub] address`, which the join commands and sign-in links use. Then
   invite yourself and your team ([above](#inviting-someone)). Until a hub has people, its dashboard has no sign-in
   and answers only the hub itself: anyone else, through the proxy or from another device, is turned away.
2. **Put HTTPS in front.** The dashboard speaks plain HTTP, and codes, tokens and sign-in cookies must not cross the
   network in clear text. Put a reverse proxy in front of it with a certificate your computers trust: Caddy, nginx,
   or the company's load balancer. The proxy passes on the original `Host` and sets `X-Forwarded-For` and
   `X-Forwarded-Proto`. Caddy does this by default; with nginx:

   ```nginx
   location / {
       proxy_pass http://127.0.0.1:11524;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
       client_max_body_size 0;    # transcripts can be large
   }
   ```

3. **Let the dashboard answer to that name**, in the hub's [configuration](configuration.md#synthesis-export-server-inject-updates):

   ```toml
   [server]
   host = "0.0.0.0"                                # only when the proxy runs on another computer
   allowed_hosts = ["interlatch.example.internal"]
   trusted_proxies = ["10.0.4.12"]                 # that computer, so its X-Forwarded-Proto is believed
   ```

   When the proxy runs on the hub itself, keep `host = "127.0.0.1"` and the default `trusted_proxies`, and add
   `behind_proxy = true`: a proxied request can look exactly like one made at the hub, which is always an admin, so
   with it set no request through the dashboard counts as made at the hub. Admins then sign in like everyone, or use
   the `interlatch hub` commands at the hub. With `0.0.0.0`, let only the proxy reach the dashboard's port (11524) through the firewall.
   Restart the dashboard afterwards.

With Tailscale instead, `interlatch tailnet on --anyone` lets everyone in your tailnet reach the hub over HTTPS
([Your phone](#your-phone)). Once the hub has people, they still sign in.

### Company sign-in

If your company signs people in through an auth proxy (oauth2-proxy, Azure App Service authentication and the like)
that passes the signed-in person's email in a header, the hub can use it:

```toml
[server]
auth_header = "X-Forwarded-Email"
trusted_proxies = ["127.0.0.1", "::1"]     # where the auth proxy connects from
```

The hub believes the header only on requests from `trusted_proxies`, and only for people added on the hub with that
email; anyone else sees "you're not on this hub; ask an admin to add you". Roles still come from the hub. The proxy
must set the header itself and drop any copy a browser sends: anything else in `trusted_proxies` that passes a
visitor's headers through unchanged (Tailscale Serve on the hub, for one) would let a visitor name any email. Use
`auth_header` only when the auth proxy is the only way in. Computers don't use company sign-in: they keep joining
with a code, so let requests to `/api/hub/` through the auth proxy without sign-in (oauth2-proxy:
`--skip-auth-route`). The hub checks their tokens itself.

## A Linux hub

On Linux, `interlatch install` sets up systemd user units instead of launchd agents: `interlatch-sync.timer` (the
15-minute sync) and `interlatch-ui.service` (the dashboard). Two one-time commands help a hub that runs unattended:

```bash
loginctl enable-linger $USER             # keep them running while nobody is logged in
sudo tailscale set --operator=$USER      # let `interlatch tailnet on` configure Tailscale Serve
```

The desktop app is macOS only; the command line and the dashboard work the same.

A team's hub on a server can also run in Docker, with HTTPS and the team store set up: [A hub in Docker](docker.md).

## Without a hub: Syncthing

If you would rather not run a hub, [Syncthing](https://syncthing.net) (or rsync) can copy another computer's
sessions to the main one: sync its `~/.claude/projects` into a folder such as `~/sessions/laptop/claude/projects`
and its `~/.codex/sessions` into `~/sessions/laptop/codex/sessions`, then add `~/sessions/laptop/claude` to
`claude_dirs` and `~/sessions/laptop/codex` to `codex_dirs`. Interlatch records them as its own: without the
computer's name, project matching or the handover of earlier analyses.

Never sync `~/.interlatch` itself between computers: a SQLite database copied while it is in use can be
corrupted.

## Not covered yet

- **The Claude app on your phone.** Claude's custom connectors reach MCP servers from Anthropic's cloud, not from
  your phone, so the hub's tailnet address is out of their reach. Claude Code's Remote Control, from the phone into
  a session on the hub, does have Interlatch's tools.
- **A copy to read offline** on the other computers.
- **A Windows computer in a hub.** Interlatch runs on Windows on its own ([Windows](install.md#windows)), but parts of
  the hub expect project folders as `/…` paths.
