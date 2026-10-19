# Suggestions and What goes wrong

[← Interlatch](../README.md) · [Docs index](README.md)

Interlatch looks through your recorded sessions for failures that keep coming back: a screenshot path Playwright
refuses, an edit that fails because the file changed, a zsh glob that matched nothing. **What goes wrong** lists
those causes, with noise kept apart. **Suggestions** is one queue of proposed fixes: lines for your `CLAUDE.md` or
`AGENTS.md`, a change to Claude Code's MCP config, and setup steps for you to run. Knowledge that sessions confirmed
often enough is proposed as an instruction line too.

Nothing is written until you approve a suggestion, and the file is backed up first. No model is called: both are
built from your archive by fixed rules, in a few seconds.

## What goes wrong

**What it reads:** the tool calls that failed in your sessions (a row stored twice, as happens when a session is
resumed, counts once), and the friction notes that session analysis wrote about each session, in English or
Japanese. One note can name several causes. Sessions recovered from `history.jsonl` have neither, and are left out.

Each failure is matched against a catalog of known causes, each with its fixes:

| Cause | What happens | Fixes proposed |
| --- | --- | --- |
| `edit-stale-context` | An edit fails because the file changed since it was read, or the expected lines are not there | Instruction: re-read the exact lines right before every edit |
| `playwright-output-roots` | Playwright MCP refuses a screenshot path outside its allowed roots, or blocks a `file://` page | Config: `--isolated` and an output directory; instruction: save screenshots by bare filename, preview over `http://localhost` |
| `playwright-browser-in-use` | "Browser is already in use": another session holds Playwright's shared browser | Config: `--isolated`; instruction: stop instead of retrying |
| `playwright-stale-refs` | Playwright actions on refs from an old snapshot, or on ambiguous selectors | Instruction: take a fresh snapshot before using refs |
| `zsh-nomatch` | zsh stops a whole command on an unquoted glob that matches nothing | Setup step: `setopt NO_NOMATCH`; instruction: quote every glob |
| `zsh-equals` | zsh expands a word starting with `=` (`echo ====` fails) | Setup step: `unsetopt EQUALS`; instruction: quote separator lines |
| `zsh-dialect` | Bash habits that zsh treats differently (`$status` is read-only, no word splitting) | Instruction: write zsh, not bash |
| `sleep-polling-blocked` | Claude Code blocks `sleep N; cmd` used to wait | Instruction: wait with `run_in_background` or Monitor |
| `timeout-missing` | `timeout` is not installed (macOS), so wrapped commands exit 127 | Setup step: `brew install coreutils`; instruction: use the Bash tool's timeout instead |
| `bare-python-modules` | Throwaway Python on the system interpreter: missing modules, no `python` | Instruction: run it through `uv run --with` |
| `interactive-aliases` | `cp -i`, `rm -i` aliases hang commands or skip overwrites | Setup step: keep the aliases out of agent shells; instruction: use `command cp -f` |
| `stale-dev-server` | A port is already taken, or an old server answers a live check | Instruction: check who owns the port first |
| `cwd-drift` | A relative `cd sub && …` runs from the wrong directory | Instruction: use absolute paths |
| `auto-mode-retry` | An action the auto-mode classifier denied is tried again | Instruction: stop and ask |
| `bash-timeout` | A long command hits the shell tool's time limit | Instruction: run it in the background |
| `concurrent-sessions` | An edit, port or browser conflict while another session was active in the same project (within 10 minutes) | Instruction, in that project's file: assume other sessions share this tree |

**Noise** is kept apart and never gets a fix: tests, linters and type checks failing inside a dev loop; read-only
command chains (`cat`, `grep`, `ls`…) whose last command exited non-zero; provider and platform failures
(overloaded, connection lost, usage limits, expired login); and tool calls you turned down at the permission
prompt. A failure that matches a real cause is never counted as noise.

Friction notes that match no known cause but recur in 3 or more sessions are listed as *other*, as context, with no
fix.

For each cause you see how many sessions and projects it hit, how many times it happened, how often the same
failure was retried right away, the share of sessions in those projects it affected, first and last seen, whether
it is **still happening** (seen in the last 14 days), its sessions per week over the last 12 weeks, and example
sessions. Below the causes, the 15 tools with the most failed calls, with their failure rate.

**In the dashboard:** the **What goes wrong** page (from the Suggestions sidebar, or ⌘K). Pick 30 days, 90 days
(the default), 180 days or all, and a project. Click a cause for its examples, agents, projects and fixes; **N to
review** opens its suggestions. The **Noise** card says how many failures were expected; **Show** lists them.

**On the command line:** `interlatch friction [-p project] [--days N] [--json] [--noise]`. It covers all time unless
you give `--days`, and prints one line on how much noise was left out; `--noise` lists it.

## Where suggestions come from

**From What goes wrong.** A cause gets suggestions when it is still happening and has hit 3 or more sessions or 2
or more projects. Its instruction lines go to your user-level files when it hit 3 or more projects, otherwise to the
project where it happened most. `concurrent-sessions` always goes to the project files, for up to 5 projects where
it hit 2 or more sessions. Lines are proposed only for the agents that ran into the cause: Claude Code's file, Codex's
file, or both. A cause you [moved](#moving-a-line) goes where you put it.

**From knowledge.** An item becomes a suggestion when it is:

- active, extracted by analysis or added by you (never from Claude Code or Codex memory notes),
- a gotcha, fix, command, preference or decision,
- high confidence, or [established or canonical](analysis.md#how-knowledge-earns-trust),
- worded as a rule ("never…", "use X, not Y", "requires…"), not a changelog entry ("Fixed…", "Added…", "X now
  does Y"). An item seen in one session only also has to be a command, a preference, or a firm rule.

Items with near-identical titles are treated as one lesson, and the most trusted one is proposed. A lesson becomes one
line in your user-level file instead of one per project when it was found in 3 or more projects, or when it is a
preference the analysis marked *global*: how you like to work, not how the project works. Other global items (a
gotcha about a tool, a command) stay in the file of the project they came from, since the user-level file is read
in every session. A lesson you [moved](#moving-a-line) goes where you put it. A lesson the catalog above already
covers is left to it. The line is the item's title in bold and the first sentence of its body, up to 240
characters. At most 8 knowledge lines are proposed per file, the best first: a higher stage, more sessions behind it and more
recent use rank higher. Lines you dismissed or applied don't take one of the 8 places, and neither do lessons you
moved. The rest wait, and come up as places free; they are not shown as stale.

A suggestion is not made when a line outside Interlatch's block in that file already says much the same thing.

## The three kinds

| Kind | Shown as | What it changes | Who does it |
| --- | --- | --- | --- |
| `instruction` | Instruction | One line in Interlatch's block of a `CLAUDE.md` or `AGENTS.md` | Interlatch, when you apply it |
| `config` | Config change | Arguments added to the Playwright MCP server in `~/.claude.json` | Interlatch, when you apply it |
| `environment` | Setup step | Your shell setup: a `setopt` line, `brew install coreutils`, aliases | You. Interlatch shows the command and never runs it; you mark it done |

### Where instruction lines go

| | User level | Project level |
| --- | --- | --- |
| Claude Code | `~/.claude/CLAUDE.md` (in `$CLAUDE_CONFIG_DIR` when set) | `<project>/CLAUDE.md` |
| Codex | `~/.codex/AGENTS.md` (in `$CODEX_HOME` when set) | `<project>/AGENTS.md` |
| GitHub Copilot, IBM Bob, Google Antigravity, OpenCode | none (skipped) | `<project>/AGENTS.md` |

User-level files follow the first folder in `[sources] claude_dirs` and `codex_dirs` (see
[Configuration](configuration.md)), so the background job and your shell write to the same file. A project line is
only proposed for a folder that still exists, and never for your home folder or a folder above it: a `CLAUDE.md`
there would reach every project.

When a project's `AGENTS.md` refers to `CLAUDE.md`, the line goes to `CLAUDE.md` only; when its `CLAUDE.md`
imports `@AGENTS.md`, it goes to `AGENTS.md` only. A project file is proposed only for agents that work in that
project (3 or more sessions, or a fifth of them), and never for a project folder that no longer exists. A missing
file is created when you apply.

### Interlatch's block

Interlatch writes only inside one block at the end of the file:

```markdown
<!-- BEGIN interlatch -->
- Re-read the exact lines (Read, or `sed -n 'a,bp' file`) in the same turn before every edit … <!-- interlatch:friction:edit-stale-context -->
<!-- END interlatch -->
```

- The block goes after everything else in the file, including other tools' managed blocks (such as
  `agent-ninja-START` … `agent-ninja-END`), never inside one.
- Text outside the block is never touched. A line you type inside the block without an `interlatch:` marker is kept.
- Each line ends with a comment naming the suggestion, so undo removes exactly that line. Agents ignore the comment.
- Nothing else is added: no header, no "generated by" text.
- Removing the last line removes the block. If Interlatch created the file for that line, undoing it deletes the
  file again; a file you already had is kept.
- A file with Windows line endings (CRLF) keeps them. A symlinked file is written where the link points, and stays a
  link.
- A damaged block (a `BEGIN` without its `END`, or two blocks) is never guessed at: applying stops and names the file
  to fix by hand.

### Language

Lines from what goes wrong are written in the language of `[analysis] language` ([Configuration](configuration.md#analysis)):
English, or Japanese with `ja`. Commands and config changes read the same in both. The marker at the end of a line
(`<!-- interlatch:friction:… -->`) does not change with the language, so a line already in the file is not proposed
again in the other one, and a line outside the block that says the same thing counts in either language. A waiting
line you have not edited switches language at the next refresh; an applied or edited one stays as it is. Lines from
knowledge are in the language the knowledge was written in.

### The config change

For `playwright-output-roots` and `playwright-browser-in-use`, Interlatch proposes:

```
mcpServers.playwright.args += ["--isolated", "--output-dir", "/Users/you/.cache/playwright-mcp"]
```

It is proposed only when `~/.claude.json` (or `$CLAUDE_CONFIG_DIR/.claude.json`, when that exists) has a server
named `playwright` that lacks these arguments. Applying adds only the missing ones; a flag that is already set
keeps its value. Every other key is kept, and the file is written back with two-space indentation. Once the
arguments are there, the suggestion goes stale. New Claude Code sessions pick up the change.

This is the only config change Interlatch makes. Edited text is checked too: it must name a Playwright server and
may only add `--isolated` and `--output-dir <absolute path>`. Undo removes only the arguments Interlatch added.

## Reviewing and applying

**In the dashboard:** **Suggestions** in the rail, with a badge counting suggestions you haven't seen. Opening the
page clears it. The sidebar and the switch at the top move between **To review**, **Applied**, **Done**,
**Stale** and **Dismissed**; a menu narrows to user-level suggestions or one project. Suggestions are grouped by the
file they change, with setup steps in their own group. Each card shows:

- the kind, where it came from (*from what goes wrong* or *from knowledge*), and the agent,
- the evidence: "seen in 31 sessions across 17 projects · still happening · last 2026-10-01", or for knowledge
  "established · confirmed in 2 sessions · agents-chronicle", with two example sessions to open,
- any warnings (below),
- the line itself, which you can edit. Your wording is saved when you leave the box and kept on later refreshes;
  **Reset to the proposed text** goes back.

**Preview** shows the exact change to the file as a diff, without writing it. **Apply** writes it. **Dismiss** takes
it out of the queue. A setup step shows its command with **Copy**, and **Mark done** once you have run it.
**Check again** looks at the latest sessions and knowledge now.

### Moving a line

A waiting instruction line can go somewhere else than Interlatch chose. On a project's card, **Move to every
project** puts it in your user-level file instead. On a user-level card, **Move to *project* only** (or **Move to
its projects**, when it came from several) puts it back in the files of the projects it came from; for a recurring
failure, up to 5 projects where it hit 2 or more sessions. Interlatch remembers the choice for that lesson or cause:
every waiting card of it moves along (two project cards become one user-level card), your edited wording comes with
it, and later refreshes keep it there. Lines already applied stay where they were written.

The **Home** page shows a **Suggestions** card with the top 3 waiting, when there are any, with **Approve** and
**Dismiss** (setup steps get **Copy command**). Its title opens the full card.

**On the command line:**

| Command | |
| --- | --- |
| `interlatch suggest [-p project] [--all] [--json]` | List what is waiting (`--all`: also applied, done, stale and dismissed), with the target file, evidence and warnings |
| `interlatch suggest show ID` | The diff applying would make, or a setup step's command |
| `interlatch suggest apply ID… [--yes]` | Show each diff and ask before writing (`--yes`: don't ask). A setup step is refused: run it yourself |
| `interlatch suggest dismiss ID [--reason R]` | Never propose it again (an applied one must be undone first) |
| `interlatch suggest done ID` | You ran a setup step |
| `interlatch suggest undo ID` | Take an applied line or config change back out, or put a dismissed or done one back in the queue |
| `interlatch suggest retire ID` | Take an applied line that [didn't help](#did-it-help) back out, and never propose it again |
| `interlatch suggest move ID --to user\|project` | [Move](#moving-a-line) a waiting line to your user-level file, or back to the files of its projects |
| `interlatch suggest refresh` | Look at the latest sessions and knowledge now |

**Backups:** before any write, the current file is copied to `~/.interlatch/backups/<name>.<time>.bak`, for
example `CLAUDE.md.2026-10-02T091500.123Z.bak`, or `.claude.json.<time>.bak` (with its leading dot, so `ls -a` shows it).
A file Interlatch creates has nothing to back up. The new file is written to a temporary file and moved into place,
so it is never half-written.

## Warnings

A suggestion that should be read twice says so. Warnings don't block applying.

- **Public repository:** the project's `origin` is a public GitHub repository, so anyone can read the line once you
  commit it. Interlatch asks `git remote get-url origin`, then `gh repo view` for GitHub remotes, and remembers the
  answer for a week. Without `gh`, or for other hosts, the visibility is unknown and no warning is shown.
- **Looks sensitive:** the line contains an email address, a GUID, a private IP address, a home folder path
  (`/Users/<name>/`, `/home/<name>/`), a host name that isn't a well-known public one, or something that looks like a
  token or key. In the dashboard these follow your edits when you press Preview.
- **File not tracked by git:** the file exists in a git repository but isn't committed, so the line stays on this
  computer.

## Later refreshes

The queue is refreshed after every background sync (no model is called; it takes a few seconds), by **Check
again**, and by `interlatch suggest refresh`.

| Status | Meaning |
| --- | --- |
| `new` (To review) | Waiting for you. A refresh updates its evidence, but keeps text you edited |
| `applied` | Written. For a friction fix, the evidence counts the sessions that hit the cause since ("seen in 2 sessions since applied", or "not seen since applied"), and says whether it [helped](#did-it-help) |
| `done` | A setup step you marked as run |
| `stale` | Its cause stopped happening, or the file already has it. It comes back as new if the cause returns |
| `dismissed` | Never proposed again, whatever later sessions show. **Restore** (or `interlatch suggest undo ID`) puts it back |

### Did it help?

An applied friction fix says whether the cause got rarer. Interlatch compares the share of sessions that hit the cause
in the 4 weeks before the fix with the share since, among the sessions the fix reaches: its agent, and its project
for a project file. A session counts on the side it started on, because one that began earlier never read the line.
Once any of those sessions is analyzed, only analyzed ones count, since friction notes arrive with the analysis.

| Verdict | When |
| --- | --- |
| **Helped** | The share fell to half or less, by more than chance would explain (one-sided Fisher's exact test, p < 0.1) |
| **Not helping** | The share stayed at 80% or more of what it was, with at least 2 sessions hitting it since |
| **Can't tell** | Another fix for the same cause was applied within a week and reaches the same sessions (for example the Playwright config change and its CLAUDE.md line), so neither gets the credit |
| **Too early to tell** | Fewer than 5 sessions on either side, fewer than 2 hits before, or a change in between |

The counts behind the verdict are shown next to it ("4 of 13 sessions hit this in the 4 weeks before, 4 of 12
since"). The comparison can't see everything else that changed, such as a new agent version or a different kind of
work, so read it as a hint, not a measurement.

Every line in an instruction file is read at the start of every session, and a file holds at most 8 of Interlatch's
lines. When a line has not helped for 3 weeks and at least 10 sessions, its card offers **Remove the line**
(`interlatch suggest retire ID`): the line leaves the file, and the suggestion is dismissed so it isn't proposed again.

The SessionStart digest leaves tentative (`wip`) lessons out for the same reason. The interlatch MCP tools still
find them.

## Undoing

- **Undo** on an applied card, or `interlatch suggest undo ID`, takes the line back out of the block (or the
  arguments back out of `~/.claude.json`) and returns the suggestion to the queue.
- To remove everything at once, delete the lines from `<!-- BEGIN interlatch -->` to `<!-- END interlatch -->` from the
  file. A refresh never writes a line back, but those suggestions still show as applied until you undo or dismiss
  them.
- Every earlier version of a changed file is in `~/.interlatch/backups/`.
- `interlatch uninstall` does not touch instruction files or `~/.claude.json` for suggestions; undo them first if you
  want them gone.

## What Interlatch never does

- Write anything you haven't applied. A refresh only updates the queue.
- Run a setup step, or edit your shell startup files (`~/.zshrc`, `~/.zshenv`): it shows the command, and you run it.
- Write outside its block in an instruction file, or change any part of `~/.claude.json` except the Playwright
  server's `args`.
- Add attribution or "generated by" text.
- Send your sessions anywhere for this. The only outside call is `gh repo view`, described below.

## Settings

| Key | Default | |
| --- | --- | --- |
| `suggestions.enabled` | `true` | refresh the queue after each background sync. Off, the queue stays as it is; **Check again** and `interlatch suggest refresh` still work |
| `suggestions.notify` | `false` | show a desktop notification ("3 new suggestions. Open the dashboard > Suggestions") when a background sync finds new ones |

Set them with `interlatch config set suggestions.notify true`, or under `[suggestions]` in `config.toml`
([Configuration](configuration.md)).

## Privacy

What goes wrong and Suggestions read only your local database and the instruction files they would change. To warn
about public repositories, Interlatch runs `git remote get-url origin` in the projects it proposes lines for and, for
GitHub remotes, `gh repo view <owner>/<repo> --json visibility` with your own `gh` login. That sends the repository's
name to GitHub and nothing else; the answer is kept for a week. Without `gh` nothing is sent. See
[Data and privacy](privacy.md).
