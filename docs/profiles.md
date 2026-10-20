# Several hubs on one computer

[← Interlatch](../README.md) · [Docs index](README.md)

A computer joins one hub. If you work for more than one team, such as your own projects and a client's, each with
its own hub, connect your **groups of projects** to the hub each one belongs to. Each folder's sessions then reach one
hub only, and agents working there see only what belongs to it.

- **Personal** (`~/.interlatch`) records and analyzes every session from every agent, as always. It's the master copy
  of everything on this computer, and it may send to a hub of its own.
- **Another hub** has a name here, such as Acme. It gets the sessions in the groups you connect to it, read from
  Personal's archive: no second copy, and nothing analyzed twice.

<a id="connect-a-profile-to-its-hub"></a>

## Add a hub and connect a group

1. Add the hub, with the invite code its admin sent ([Joining your team's hub](join-a-hub.md)):

    ```bash
    interlatch hubs add Acme https://hub.acme.example --code XXXX-XXXX-XXXX
    ```

2. Make a group of the folders that belong to it, or use one you already have on the dashboard's **Projects** page:

    ```bash
    interlatch group create "Acme work" --folder ~/work/acme --folder ~/clients/acme-site
    ```

3. Connect the group to the hub:

    ```bash
    interlatch group connect "Acme work" Acme
    ```

The sessions already recorded in the group's folders move to Acme, and go to Acme's hub from then on. Any that
Personal's hub already has, it takes back first (see [Moving takes sessions back](#moving-takes-sessions-back)).
`--dry-run` shows what would move, and how many of them a hub has, without changing anything.

**On the dashboard:** **Settings › Devices › Hubs** adds a hub (its name, address and invite code), and a group's page
has **Goes to**, where you pick the hub its sessions go to.

As on any computer, a hub gets only the folders added to one of its projects
(`interlatch --hub Acme hub add-folder ~/work/acme/app --project App`), unless you joined with `--all-folders`.

## Groups decide

What a connected group holds goes to its hub: its folders and everything below them, and projects you put in it by
hand on the **Projects** page. Change the group and its hub follows, sessions so far included:

| You | The sessions there |
| --- | --- |
| Add a folder to a connected group, or put a project in it | Move to its hub |
| Take a project out of a connected group | Wait for you (below), never back to Personal on their own |
| Make a group inside a connected group's folder, connected to no hub | Wait for you |
| Connect a group to another hub inside a connected group's folder | Go to that hub: the narrower group wins |
| Disconnect a group (`interlatch group disconnect "Acme work"`) | Wait for you |

<a id="moving-takes-sessions-back"></a>

### Moving takes sessions back

A session is on one hub at a time. When a change moves sessions away from a hub (to another hub, or to wait for
you), that hub takes back the ones this computer sent it: their summaries and lessons go, there and in its team
store. This happens before anything goes to the new hub. A session chosen for that hub again is simply sent again.

If the hub can't be reached, or this computer is still sending to it, nothing changes, on the dashboard too, and
you're told why. Try again later, or move them anyway (`--leave-on-hub`, or **OK** on the dashboard's question): the
hub takes them back the next time this computer sends to it. A hub older than this version can't take sessions back.
Update it, or move them anyway and its copies stay there.

Removing a hub (`interlatch hubs remove`) takes nothing back: leave it first.

A folder can belong to one hub: connecting two hubs' groups to the same folder is refused.

## Waiting folders

A folder that could belong to more than one hub, or that a group let go, **waits**. Personal still records and
analyzes its sessions, but no hub gets them and no agent sees them until you choose:

```bash
interlatch group waiting
interlatch group choose ~/work/acme/notes Acme       # or: personal
interlatch group where ~/work/acme/app               # who gets a folder, and why
```

**Settings › Devices › Hubs** lists them too, with a **Choose** for each, and **Home** shows them under **Needs you**. Projects a folder holds waiting (a group
connected to no hub inside a connected one) are listed once, under that folder.

## What agents see

What Interlatch gives an agent depends on the folder it runs in: the [MCP server](mcp.md)'s tools and the notes at
the start of each session.

- **In a folder of a group connected to a hub:** that hub's sessions, lessons, knowledge bases and teammates' lessons
  only.
- **Anywhere else:** Personal's only.
- **In a waiting folder:** nothing. The MCP tools say the folder waits for you to choose.

What is made from every project at once (the global playbook, glossary terms seen in another hub's projects, themes
and weekly reviews) isn't shown to agents while you have another hub. The dashboard still shows everything.

Lines Interlatch proposes for your user-level instruction files (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`), which
agents read in every folder, come only from Personal's lessons. A lesson another hub's sessions taught is proposed for
that project's own files instead ([Suggestions](suggestions.md)).

## Commands

| To | Run |
| --- | --- |
| List the hubs and their groups | `interlatch hubs` |
| Add a hub, and join it | `interlatch hubs add <name> [<address> --code <code>] [--all-folders]` |
| Remove a hub | `interlatch hubs remove <name> [--delete-data]` (leave it first: `interlatch --hub <name> hub leave`) |
| List your groups and where each goes | `interlatch group list` |
| Make a group, change its folders | `interlatch group create <name> --folder <folder>…`, `group add-folder` / `remove-folder <group> <folder>` |
| Connect a group, or disconnect it | `interlatch group connect <group> <hub>`, `group disconnect <group>` |
| Run a hub command for another hub | `interlatch --hub <name> hub …` / `push` / `config …` |

`interlatch push` and the background job send to Personal's hub, then each other hub. Recording, analysis and the
dashboard are Personal's: with `--hub`, only `hub`, `push` and `config` run.

## Sharing with several hubs

- **Knowledge only.** Every hub gets summaries and project lessons, never transcripts. A transcript upload would
  carry every hub's sessions, so while you have another hub, Personal won't send transcripts:
  `interlatch config set hub.share knowledge`.
- **Each hub once.** Joining a hub that this computer already sends to is refused before anything is sent.
- **Its own identity.** Each hub gets its own token and computer ID, so two hubs can't match this computer by its ID.
  Each hub still shows the computer's name: change the name a hub sees in `~/.interlatch/profiles/<id>/machine.json`.
- **Its own teammates.** Teammates' lessons a hub sends are kept for that hub, and agents see them only in its
  folders. Leaving one hub (`interlatch --hub Acme hub leave`) removes only that hub's.
- **Personal's hub** gets only Personal's sessions. The repositories and folders its hello names leave out what the
  other hubs own.

## Where it's kept

`~/.interlatch/profiles.toml` lists the other hubs, the groups connected to each, the folders that follows from, and
your choices. Only you can read it. If it can't be read, nothing is shared with any hub until it's fixed. Each hub
also has a folder of its own, `~/.interlatch/profiles/<id>`, with its token, computer ID and sharing state.

Personal's archive gets a `session_owner` table, the hub each session belongs to, once the first other hub is added.
An archive without one is never changed.

**From 0.28.1:** `interlatch profile` became `interlatch hubs` and `interlatch group`, and `--profile` is now `--hub`
(the old name still works). Folders a profile was given with `--owns` stay that hub's.
