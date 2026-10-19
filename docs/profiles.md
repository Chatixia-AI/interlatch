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
    So far, Interlatch decides who owns each session, keeps a named profile's sessions off Personal's hub, and shows
    agents only what their folder's profile owns. Named profiles don't send to their own hub yet
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
| Run any command for a profile | `interlatch --profile <profile> <command>` |

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

1. **Each profile shares with its own hub.** A named profile copies its sessions from Personal and shares them with
   its hub (`interlatch --profile Acme hub join …`). Moving a folder takes its sessions back from the hub that had
   them.
2. **Windows and the dashboard.** A labelled window per profile, the menu bar, and Profiles and Waiting cards in the
   dashboard.
