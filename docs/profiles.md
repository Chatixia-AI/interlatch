# Profiles: one computer, several hubs

[← Interlatch](../README.md) · [Docs index](README.md)

A computer joins one hub. If you work for more than one team, such as your own projects and a client's, each with
its own hub, **profiles** keep their sessions apart: each folder belongs to one profile, and each session reaches at
most one hub.

- **Personal** is the profile you already have (`~/.interlatch`). It records and analyzes every session from every
  agent, as it always has. It's the master copy of everything on this computer.
- A **named profile**, such as Acme, owns some folders, and if you like the repositories whose git remote matches a
  pattern. Its sessions stay off Personal's hub, and later go to Acme's hub only.

!!! note "Being built in stages"
    Interlatch decides who owns each session, sends each profile's sessions to its own hub only, and shows agents only
    what their folder's profile owns. Profiles are set up from the command line for now
    ([What comes next](#what-comes-next)).

## Make a profile

```bash
interlatch profile create Acme --owns ~/work/acme
interlatch profile create Globex --owns ~/clients/globex --remotes 'github.com/globex-inc/*'
```

`--owns` gives the profile a folder and everything below it. `--remotes` gives it every repository whose git remote
matches the pattern, wherever it is cloned. Repeat either option for more. A repository first seen inside a
profile's folder belongs to that profile in its other clones too.

Sessions already recorded in those folders move to the profile. Personal's hub keeps its copies of any that it already
has. So if it has some, nothing changes, and the command says how many. Take them back first
(`interlatch hub remove-folder <folder>` for a folder you added there, or ask the hub's admin to purge them), or pass
`--leave-on-hub` to move them anyway. `--dry-run` shows what would move without changing anything.

Each profile gets its own folder, `~/.interlatch/profiles/<id>`, with its own settings, computer identity and port
for its dashboard (11525, 11526, …).

## Who owns a session

One rule decides, the first that matches:

1. **A session keeps its owner.** Once decided, only moving a folder (`profile own`, `choose`) changes it, never a
   new rule.
2. **The folder.** The longest owned folder that holds it wins, or a choice you made (below). If another profile's
   `--remotes` pattern also claims the repository, the session waits for you.
3. **The repository.** Its git remote matches a profile's pattern, or the repository was seen in a profile's folder.
   If two profiles claim it, it waits.
4. **Otherwise, Personal.** That includes chats imported from claude.ai and ChatGPT.

To see how a folder is decided:

```bash
interlatch profile where ~/work/acme/app
```

## Waiting folders

A session that two profiles could claim **waits**. Personal still records and analyzes it, but no hub gets it and no
profile owns it until you choose:

```bash
interlatch profile waiting
interlatch profile choose ~/work/shared-lib Acme       # or: personal
```

A choice is your answer: it settles the folder and everything below it, unless a profile owns a narrower folder
inside it.

## Move folders in and out

| To | Run |
| --- | --- |
| List the profiles, their folders and sessions | `interlatch profile list` |
| Give a profile another folder, with its sessions so far | `interlatch profile own <folder> <profile>` |
| Take a folder away from its profile | `interlatch profile disown <folder>` |
| Remove a profile | `interlatch profile remove <profile> [--delete-data]` |
| Run a hub command for a profile | `interlatch --profile <profile> hub …` / `push` / `config …` |

`disown` and `remove` never send a folder back to Personal on their own. Its sessions wait for you, so a client's
folder isn't shared with your Personal hub by mistake. `remove` keeps the profile's folder unless you pass
`--delete-data`.

## What agents see

Once you have named profiles, what Interlatch gives an agent depends on the folder it runs in: the
[MCP server](mcp.md)'s tools and the notes at the start of each session.

- **In a Personal folder:** Personal's sessions, lessons and knowledge bases only.
- **In a profile's folder:** that profile's only.
- **In a waiting folder:** nothing. The MCP tools say the folder waits for you to choose.

What is made from every project at once (the global playbook, glossary terms seen in another profile's projects,
themes and weekly reviews) isn't shown to agents while you have named profiles. The dashboard still shows everything.

Lines Interlatch proposes for your user-level instruction files (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`), which
agents read in every folder, come only from Personal's lessons. A lesson a profile's sessions learned is proposed for
that project's own files instead ([Suggestions](suggestions.md)).

## Connect a profile to its hub

A profile joins its hub the way a computer does, with an invite from that hub's admin
([Joining your team's hub](join-a-hub.md)), and `--profile` in front:

```bash
interlatch --profile Acme hub join https://hub.acme.example --code XXXX-XXXX-XXXX
interlatch --profile Acme hub add-folder ~/work/acme/app --project App
```

From then on, the background job sends each hub what is new: Personal's hub first, then each profile's.
`interlatch push` does the same now, and `interlatch --profile Acme push` sends Acme's only.

- **Only its own sessions.** A profile's hub gets the sessions that profile owns, read from Personal's archive: there
  is no second copy, and nothing is analyzed twice. As on any computer, only folders added to a project on its hub go,
  unless it joined with `--all-folders`.
- **Its own identity.** Each profile has its own token and computer ID, so two hubs can't match this computer by its
  ID. Each hub still shows the computer's name: change the name a profile's hub sees in
  `~/.interlatch/profiles/<id>/machine.json`.
- **Its own teammates.** Teammates' lessons a profile's hub sends are kept for that profile, and agents see them only
  in its folders. Leaving one hub (`interlatch --profile Acme hub leave`) removes only that hub's.
- **Personal does the rest.** Recording, analysis and the dashboard are Personal's: with `--profile`, only `hub`,
  `push` and `config` run.

## Sharing with profiles

- **Knowledge only.** Every profile shares summaries and project lessons, never transcripts. A transcript upload
  would carry every profile's sessions, so while you have named profiles, Personal won't send transcripts and
  `interlatch profile create` asks you to switch first: `interlatch config set hub.share knowledge`.
- **One profile per hub.** Joining a hub that another profile already sends to is refused before anything is sent.
- **Personal's hub** gets only Personal's sessions. The repositories and folders its hello names leave out what other
  profiles own.

## Where it's kept

`~/.interlatch/profiles.toml` lists the profiles, their folders and patterns, and your choices. Only you can read
it. If it can't be read, Personal shares nothing with its hub until it's fixed.

Personal's archive also gets a `session_owner` table, the owner of each session, once the first profile is made.
An archive without profiles is never changed.

## What comes next

1. **Groups instead of profiles.** The groups you already make in the dashboard (a name and its folders) will connect
   to a hub, in place of `interlatch profile`.
2. **Moving a folder takes its sessions back** from the hub that had them, session by session.
3. **The dashboard:** each group shows its hub, Settings › Devices lists every hub, and waiting folders appear under
   Needs you.
