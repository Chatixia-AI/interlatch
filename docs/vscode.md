# VS Code extension

[← Interlatch](../README.md) · [Docs index](README.md)

The Interlatch extension adds two sections to VS Code's Explorer that list the coding-agent sessions behind your
files. *"Why is this code like this?"* is one click away: the session that wrote it, its summary and outcome, and its
full transcript in the dashboard.

- **Interlatch: This File** shows the sessions that read or changed the file you have open, newest first. It follows
  the active editor, and every VS Code window shows its own file.
- **Interlatch: Files in Workspace** shows the files in your open folders that sessions read or changed, as a folder
  tree like the Explorer's. Each folder and file shows how many sessions touched it and when. Expand a file to see its
  sessions; the **Open File** button next to it opens the file. Files that no longer exist, and files git ignores,
  are left out.

New sections are added at the bottom of the Explorer, collapsed. Drag a section's header to move it, and right-click
any header to show or hide sections; VS Code remembers where you put them.

Each session row shows its title and how long ago it ran. Expand it for the details, one per line so they fit a
narrow sidebar: the date and time, the agent and project, the branch, what it did to the file (`+12 −3 lines`,
`edited 2×` or `read 4×`), the outcome and the summary. **Open in Interlatch**, at the end of the details or as the
button on the row, opens the session in the dashboard.

It reads from the dashboard you already run (`interlatch ui`, or the background service `interlatch install` sets up),
over `http://127.0.0.1:11524` (or `:8765`, where older installs run it). It sends nothing anywhere else and needs no account.

## Install

Install **Interlatch** from VS Code's Extensions view (search for Interlatch), or from its
[Marketplace page](https://marketplace.visualstudio.com/items?itemName=Chatixia-AI.interlatch). Editors that don't use
the Marketplace (Cursor, Windsurf, Antigravity and VSCodium install from Open VSX) take the file built from the
repository, installed with their own command (`cursor --install-extension …`, for example):

```bash
cd vscode-extension
npx @vscode/vsce package          # writes interlatch-0.3.0.vsix
code --install-extension interlatch-0.3.0.vsix
```

It needs Interlatch, or a Chronicle newer than 0.6.1, which adds the `/api/file` and `/api/files` endpoints the lists read. With an older one,
the lists say to update.

**From the Chronicle extension.** Until 0.3.0 this was the Chronicle extension (`chatixia.chronicle-sessions`, with
**Chronicle: …** sections). Install this one, then uninstall Chronicle; while both are installed, Interlatch offers once
to do it (**Uninstall Chronicle**). Settings you made for Chronicle keep working: an `interlatch.*` setting you haven't
set takes its `chronicle.*` value.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| `interlatch.url` | empty: your `chronicle.url` if you set one, else `http://127.0.0.1:11524`, else `:8765` | Where the dashboard runs. Change it if you set `[server] port` in Interlatch's [configuration](configuration.md). |
| `interlatch.includeReads` | `true` | Also list sessions, and files, that were only read. Turn it off to see only what sessions changed. |
| `interlatch.showIgnoredFiles` | `false` | Also show files git ignores (screenshots, build output, caches) in **Interlatch: Files in Workspace**. |

## What it finds, and what it misses

A file is matched by its absolute path, and by the path relative to a session's folder for agents that record it
that way (Codex sometimes does). So it misses:

- **The same file in another git worktree or an old clone.** A session that ran in `../myapp-feature` recorded
  `../myapp-feature/src/app.py`, which is a different path from `myapp/src/app.py`.
- **Files in remote windows** (SSH, containers, WSL). The list says Interlatch only knows files on this computer.
- **Sessions not synced yet.** Claude Code sessions arrive as they end; Codex, Copilot, Bob and Antigravity sessions every 15
  minutes ([Sources](sources.md)).

## The endpoints

`GET /api/file?path=<absolute path>&limit=50` returns `{"path", "total", "sessions"}`. Each session has the same fields
as `/api/sessions`, plus `file: {reads, changes, added, removed}` for this file. Anything that can reach the
dashboard can use it, a script or another editor.

`GET /api/files?root=<absolute folder>&limit=200&existing=1&ignored=0` returns `{"root", "total", "files"}`: the files under that
folder that sessions read or changed, the most recently touched first. Each has `path`, `rel` (relative to `root`),
`sessions`, `changed` (how many of those sessions changed it), `last`, and the ids behind the counts (`session_ids`,
`changed_ids`). `existing=1` leaves out files that no longer exist, and `ignored=0` the ones git ignores.
