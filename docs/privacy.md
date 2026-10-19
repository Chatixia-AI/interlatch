# Data and privacy

[← Interlatch](../README.md) · [Docs index](README.md)

**What leaves your machine:** one thing. When a session is analyzed, a condensed digest of it (secrets redacted
first) goes to whatever you chose for analysis: Anthropic with Claude Code (`claude -p`, the default) or OpenAI with
Codex (`codex exec`), through your own login, IBM with Bob Shell (`bob run`) and your Bob API key, or the [model provider](analysis.md#model-providers) you set up, with
your own key (with Ollama on your computer, nothing leaves it). Nothing is sent to Interlatch's authors or any other service unless you join the leaderboard (below), and there is no telemetry. One other
connection is the update check: it asks pypi.org for the latest version number and sends nothing about you. It
runs when you click **Check for updates** on the Status page, and once a day only if you turn on **Check for
updates daily** or **Notify me about new versions** there (both off by default; `interlatch install` asks about
the second). If you connect Codex Cloud, each sync also runs the
`codex cloud` CLI, which fetches your own tasks from OpenAI with your Codex login; nothing is sent the other way.
If you sign in to the **Leaderboard** with Google (in Innerlatch, behind the door at the bottom of the dashboard's rail), each sync sends Interlatch's Firebase project your totals for the
last 7, 30 and 90 days (active hours, sessions, tokens) with the name and picture you show: no email, project,
prompt or title. Nothing is sent before you sign in or while game mode is off, and `interlatch leaderboard leave` deletes your row.
Signing in with Google for the [Party](party.md) page alone sends nothing: your characters are worked out on your
computer, and only **Join the board** on the Leaderboard starts sending totals. Once signed in, the Party reads
public job-market numbers (open roles per roadmap track) from Interlatch's Firebase project, at most every six hours.
That request carries the project's public web key and nothing about you, not even your sign-in.
If you also **Enter the arena** on the Arena page, each sync publishes your party's character sheet to the same
Firebase project, readable by anyone: for each of your coding agents its level, tier, class and six attribute
levels, and the team you field (which agents, in what order), with your board name and picture and a hash that tells your computers apart. No lessons, projects, sessions,
titles or prompts. Battles are worked out on your computer from the published sheets; **Leave the arena** (or leaving
the board) deletes your sheet ([The arena](party.md#the-arena)).
Importing a claude.ai or ChatGPT export reads only the chats, never the account files (`users.json`, `user.json`).
To warn before a suggested line lands in a public repository, Interlatch runs `gh repo view` with your own `gh` login
for projects with a GitHub remote; that sends GitHub the repository's name and nothing else, and the answer is
remembered for a week. Without `gh` nothing is sent ([Suggestions](suggestions.md#privacy)).

**With your phone and other computers** ([Phone and other computers](devices.md)), nothing leaves your own
devices either: `interlatch tailnet on` makes the dashboard reachable inside your Tailscale network, to your
Tailscale login only. A computer that joined a hub sends it one of two things. With `share = "everything"` (the
default) it sends its **raw session files, unredacted** like the archive: secrets that appeared in a session (a key
pasted in a prompt, a `.env` an agent read) reach the hub and its admins as they are. With `share = "knowledge"` it
sends only titles, summaries and project lessons, made from the redacted digest and redacted again as they go;
never a session's mood or friction notes, or lessons about you. Pick `knowledge` for a hub you don't run
yourself. Inside a tailnet, or behind a proxy with HTTPS, the files travel encrypted; over plain `http://` they don't.

**With a copy in Postgres** ([A copy in Postgres](postgres.md)), which is off unless you set it up, Interlatch writes
your archive to the database you chose after every background run. With `include = "knowledge"` (the default) that is
sessions' details, summaries and analyses, lessons, knowledge bases and usage, with no prompts or transcripts. With
`"everything"`, prompts and transcripts go too, with secrets redacted. Sessions in excluded projects never go. Over
the internet the connection uses TLS (SSL mode `require`) unless you turn it off.

| Stored locally | Where |
| --- | --- |
| Database: sessions, events, knowledge, glossary, reviews | `~/.interlatch/chronicle.db` (SQLite) |
| Raw transcripts, kept forever (gzip) | `~/.interlatch/archive/` |
| Markdown vault | `~/.interlatch/notes/` |
| Logs | `~/.interlatch/logs/` |
| Backups of agent config and instruction files Interlatch edits | `~/.interlatch/backups/` |
| Status-line usage per session (only with `--statusline`) | `~/.interlatch/statusline/` |
| The app's launcher script and window storage | `~/.interlatch/bin/interlatch`, `~/.interlatch/webview/` |
| This computer's id and name, its key for a hub; a hub's token (the key and token readable by you only) | `~/.interlatch/machine.json`, `~/.interlatch/machine-key`, `~/.interlatch/hub-token` |
| On a hub: the session files other computers sent | `~/.interlatch/machines/` |
| The mirror's connection, password included (readable by you only) | `~/.interlatch/mirror.env` |

## Details

- **The Systems map reads manifests.** To draw what each project is made of, Interlatch reads a short, fixed list of
  files in your project folders, read-only: package manifests, compose files, Dockerfiles, Terraform, GitHub workflows,
  vite configs, `.env.example` and deploy configs, two folder levels deep. Never `.env`, never source code, and
  nothing is stored or sent: the map is rebuilt from them in memory. A git remote is shown without its credentials.
  `[systems] read_manifests = false` turns this off ([Systems map](dashboard.md#systems-map)).
- **Redaction.** API keys, tokens and similar secrets are replaced in the digest before any call. The raw archive
  keeps the original transcripts unchanged, on your disk only. Redaction goes by patterns, so it misses a secret
  that looks like nothing in particular: see [What redaction catches](#what-redaction-catches).
- **Analysis runs sandboxed.** No session is written for the analysis itself, none of your hooks, plugins, MCP
  servers or instruction files load, and the model can only answer. `claude -p` runs with
  `--no-session-persistence --safe-mode --tools "" --strict-mcp-config`; `codex exec` runs `--ephemeral` and
  `--ignore-user-config` in a read-only sandbox with every tool feature off, and Interlatch discards any reply that
  follows a tool call ([details](analysis.md#how-analysis-works)). A model provider gets a plain API request with no
  tools in it. `analysis.auto = false` turns automatic analysis off.
- **Provider API keys** live in `provider-keys.json` in Interlatch's folder (mode 600), not in `config.toml`, and the
  dashboard never sends one back to the browser.
- **The dashboard** binds to 127.0.0.1, rejects foreign `Host` headers (DNS rebinding) and requires a custom header
  on state-changing requests (CSRF). Its page runs only the dashboard's own script files (a Content-Security-Policy),
  so text from a transcript that slipped into the page as HTML could run nothing. In the app window, the page can call only three window actions (theme, drag,
  zoom). Reached through Tailscale Serve, it answers only to the names in `[server] allowed_hosts` and lets in only
  the Tailscale logins in `[server] allowed_users`, a header it trusts only from Serve on the same computer. A
  hub takes other computers' files only with its token (`interlatch hub enable --rotate` replaces it). Reached from
  another device directly (`server.host 0.0.0.0`) or through a reverse proxy, the dashboard opens only for people
  signed in: until a hub has people (`interlatch hub invite`), it answers only this computer itself.
- **On a hub, each computer's sessions stay its own.** A session another computer sent can't be replaced, changed
  or taken back by a different computer, and an invite can't take over a computer that already joined as someone
  else.
- **Exports** (Export on a session, or on a selection in the Sessions list) are redacted, in Markdown and JSON
  ([what that catches](#what-redaction-catches)). **Original transcript** is the agent's own file as archived, unredacted:
  check it before sharing.
- **MCP tools** read the database and answer on stdio; nothing listens on the network. Their results join the
  client's conversation and so reach that client's model, with secrets redacted as in the digests. Give the server
  only to clients whose model provider you trust with your sessions. See [MCP server](mcp.md#privacy).
- **Other agents' stores are only read.** SQLite databases are opened read-only and archived as snapshots; Bob's
  login state is never read, and from Antigravity only conversation logs and Markdown artifacts are archived. From OpenCode's database only its session, message and part tables are read, and each session is archived as JSON, never the database, which also holds OpenCode's credentials. Connecting an agent edits its MCP config, backed up to `~/.interlatch/backups/`
  first.
- **Instruction files and `~/.claude.json` are written only when you apply a suggestion.** Interlatch may add a line
  to a `CLAUDE.md` or `AGENTS.md`, inside its own `<!-- BEGIN interlatch -->` block and nowhere else in the file, or
  add the missing Playwright MCP arguments to `~/.claude.json`. Each file is backed up to
  `~/.interlatch/backups/` first, and **Undo** takes the change back out. Setup steps are only shown; Interlatch
  never runs them or edits your shell startup files. See [Suggestions](suggestions.md).
- **Deleting.** `interlatch forget <id> [--delete-transcript]` removes a session for good; `interlatch uninstall
  --purge` deletes everything.

## What redaction catches

Redaction replaces what matches a list of patterns (`src/interlatch/core/redact.py`) with `[REDACTED]`. It runs on the
digest sent for analysis and on what analysis writes back (titles, summaries, lessons), and on exports, the Markdown
vault and what the MCP tools answer. It never changes the raw archive, which the dashboard's transcript view shows as
it is on your computer, an **Original transcript** download, or the session files a computer sends a hub with
`share = "everything"`.

**Caught:**

- Private keys (`-----BEGIN … PRIVATE KEY-----` blocks) and JWTs (`eyJ….….…`).
- Keys and tokens with a known prefix: Anthropic (`sk-ant-`), OpenAI (`sk-`), GitHub (`ghp_`, `gho_`, `github_pat_`
  and the like), GitLab (`glpat-`), Slack (`xoxb-` and the like), AWS access key ids (`AKIA`, `ASIA`), Google
  (`AIza`), Databricks (`dapi`), Stripe (`sk_live_`, `rk_test_` and the like), Hugging Face (`hf_`) and npm (`npm_`).
- A value after a secret's name and `=` or `:`: `password`, `passwd`, `pwd`, `secret`, `client_secret`, `api_key`,
  `access_key`, `secret_key`, `auth_token`, `access_token`, `refresh_token`, `private_key` or `token`, also with a
  prefix as in an environment variable (`DB_PASSWORD=`, `AWS_SECRET_ACCESS_KEY=`).
- HTTP credentials: `Bearer` tokens, the credential in an `Authorization:` or `Proxy-Authorization:` header
  (`Basic`, `Bearer`, `token`), a password in a URL (`postgres://user:password@host`) or in `curl -u user:password`,
  and cookies (`Cookie:` and `Set-Cookie:` lines, `curl --cookie`).
- Azure keys: `AccountKey=`, `SharedAccessKey=` and a SAS token's `sig=`.

**Not caught:**

- A secret with no known prefix and no name beside it: a password typed on its own, a key pasted without its
  variable, a value under a name not listed above (`DB_PASS=`, `PGUSER_PW=`).
- A secret split across lines, encoded (base64 other than a `Basic` header, URL-encoded), or inside a file an agent
  attached as an image or a PDF.
- Personal data: names, email addresses, phone numbers and the like are left as they are.

When a secret has reached a session, rotate it: redaction keeps it out of what Interlatch shows and sends, not out of
the transcript the agent wrote.
