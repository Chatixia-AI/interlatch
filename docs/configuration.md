# Configuration

[← Interlatch](../README.md) · [Docs index](README.md)

Settings live in `~/.interlatch/config.toml`; `interlatch config` shows them and `interlatch config edit` opens
the file. Changes apply on the next sync or worker run. `INTERLATCH_HOME` relocates everything.

## `[sources]`

| Key | Default | |
| --- | --- | --- |
| `claude_dirs` | `["~/.claude"]` | Claude Code config directories to scan; several are supported |
| `codex_dirs` | `[]` | `["~/.codex"]` once Codex is connected |
| `codex_cloud` | `false` | `true` once Codex Cloud is connected: list its tasks through the codex CLI every sync |
| `copilot_dirs` | `[]` | `~/.copilot` and VS Code `User` directories once Copilot is connected |
| `bob_dirs` | `[]` | `["~/.bob"]` once Bob is connected |
| `antigravity_dirs` | `[]` | `["~/.gemini/antigravity"]` once Antigravity is connected |
| `opencode_dirs` | `[]` | `["~/.local/share/opencode"]` once OpenCode is connected |
| `import_history` | `true` | recover prompts of sessions Claude Code already deleted, from `history.jsonl` |
| `import_memory` | `true` | import Claude's auto-memory notes (`projects/*/memory/*.md`) as knowledge |
| `exclude_projects` | `[]` | glob patterns of project paths to ignore entirely, e.g. `["/Users/me/secret/*"]` |

## `[analysis]`

| Key | Default | |
| --- | --- | --- |
| `auto` | `true` | analyze sessions automatically once they go idle |
| `backend` | `claude` | what analyzes sessions: `claude` (Claude Code) or `codex` (Codex) through your own login, `bob` (IBM Bob Shell, with a Bob API key: `interlatch config set-key bob`), or a model provider's API: `anthropic`, `bedrock`, `openai`, `azure`, `openrouter`, `ollama`, `openai-compatible` (see [`[providers.<name>]`](#providers)). Also in **Status › Analysis** |
| `model` / `effort` | `sonnet` / `medium` | Claude: any `claude --model` alias or full model id. `effort` also sets Codex's reasoning effort (`max` becomes `xhigh`). Both are in Claude Code's tab in **Status › Analysis** |
| `codex_model` | `""` | Codex model, e.g. `gpt-5.5`; empty uses Codex's default |
| `screen_model` | `haiku` | model that screens imported chats (`interlatch screen`); it reads only each chat's opening, 60 chats a call |
| `language` | `en` | the language Interlatch writes in: summaries, knowledge, knowledge bases, the playbook, glossary definitions, weekly reviews, screening reasons and the lines it proposes for `CLAUDE.md` / `AGENTS.md`. `en` or `ja`; applies to what is analyzed from then on. Also in **Status › Analysis**. The dashboard's own language is chosen in the dashboard, per browser |
| `max_budget_usd` | `3.0` | spend cap per `claude -p` call, API-equivalent USD (Codex reports tokens only) |
| `idle_minutes` | `20` | a session must have ended or been idle this long before it is analyzed |
| `min_prompts` | `1` | sessions with fewer human prompts are skipped |
| `max_per_run` / `concurrency` | `6` / `2` | analyses per 15-minute run, and parallel analysis processes |
| `backfill` | `true` | also analyze sessions recorded before install (newest first) |
| `chunk_chars` | `150000` | characters of condensed transcript per call; longer sessions are map-reduced |
| `timeout_seconds` | `900` | wall-clock limit per call |
| `claude_bin` / `codex_bin` / `bob_bin` | `""` | path to `claude` / `codex` / Bob Shell's `bob` (found automatically when empty) |

## `[providers.*]`

Settings for `analysis.backend = "<name>"`; [Model providers](analysis.md#model-providers) has the list. Set them in
**Status › Analysis** or with `interlatch config set providers.<name>.<key> <value>`. API keys are not kept here:
`interlatch config set-key <name>` (or the dashboard) stores them in `provider-keys.json`, readable by your user only.

| Key | Default | |
| --- | --- | --- |
| `base_url` | the provider's | the API's address; required for `openai-compatible`, and for `azure` unless `resource` is set |
| `model` / `small_model` | Claude Sonnet 5.5 / Claude Haiku 4.5 on `anthropic` and `bedrock`, else none | the model for analysis and knowledge bases / for screening imported chats (empty: `model`) |
| `region` / `profile` | `AWS_REGION`, else `us-east-1` / `AWS_PROFILE` | `bedrock`: the region of its endpoint, and the AWS profile to sign in with when there is no Bedrock API key |
| `resource` | | `azure`: the resource name, for `https://<resource>.openai.azure.com/openai/v1` |
| `num_ctx` | `32768` | `ollama`: the context window, in tokens |
| `chunk_chars` | `analysis.chunk_chars` (`ollama`: `60000`) | characters of condensed transcript per call |
| `max_output_tokens` | `32000` on `anthropic` / `bedrock`, else the model's own | output limit per call |
| `json_mode` | `true` (`openai-compatible`: `false`) | OpenAI-style providers: ask for a JSON object |
| `key_env` | the provider's usual variable | another environment variable to read the key from |

## `[synthesis]`, `[export]`, `[server]`, `[inject]`, `[updates]`

| Key | Default | |
| --- | --- | --- |
| `synthesis.auto` / `model` / `min_new_items` | `true` / `sonnet` / `3` | rebuild a project's knowledge base once it gains this many new items |
| `export.markdown` | `true` | mirror everything into the Markdown vault |
| `export.notes_dir` | `""` | where the vault lives (empty: `~/.interlatch/notes`) |
| `server.host` / `port` | `127.0.0.1` / `11524` | the dashboard; the app uses a free port when this one is taken. Installs made before 11524 became the default keep `port = 8765` in their `config.toml`; to move, run `interlatch config set server.port 11524`, then restart the dashboard (`launchctl kickstart -k gui/$(id -u)/com.interlatch.ui`). Computers that send to it as a hub then need its new address: `interlatch config set hub.url http://<hub>:11524` on each |
| `server.allowed_hosts` | `[]` | other names the dashboard answers to besides 127.0.0.1 and localhost, e.g. its Tailscale name; `interlatch tailnet on` sets it ([Phone and other computers](devices.md#your-phone)) |
| `server.allowed_users` | `[]` | reached by one of those names through Tailscale Serve, only these Tailscale logins get in (empty: everyone in your tailnet); `interlatch tailnet on` sets it to yours |
| `server.auth_header` | `""` | company sign-in through an auth proxy: the request header that carries the signed-in person's email, e.g. `"X-Forwarded-Email"`. Believed only from `trusted_proxies`, and only for people added on the hub. Empty: people sign in with an invite or a sign-in link ([Company sign-in](devices.md#company-sign-in)) |
| `server.trusted_proxies` | `["127.0.0.1", "::1"]` | addresses of the proxies whose `auth_header`, `X-Forwarded-Proto` and `X-Forwarded-For` the dashboard believes; the last `X-Forwarded-For` entry tells visitors apart when they try codes ([too many wrong codes](troubleshooting.md#a-hub)) ([Reaching the hub without Tailscale](devices.md#reaching-the-hub-without-tailscale)) |
| `server.behind_proxy` | `false` | a reverse proxy on this computer forwards to the dashboard: no request through it counts as made at the hub itself (which is always an admin), so admins sign in or use the `interlatch hub` commands ([Reaching the hub without Tailscale](devices.md#reaching-the-hub-without-tailscale)) |
| `server.menu_bar` | `false` | macOS: the dashboard that runs at login also shows Interlatch's icon in the menu bar (needs the `app` extra); `interlatch install --menu-bar` turns it on ([The menu-bar icon](install.md#the-menu-bar-icon)) |
| `inject.session_start` / `max_chars` | `false` / `3000` | give new sessions a digest of the project's knowledge base (SessionStart hook) |
| `updates.check_daily` | `false` | ask pypi.org for the latest version once a day while the dashboard is open (Status › Updates) |
| `updates.notify` | `false` | the background sync asks pypi.org once a day and shows a desktop notification once per new release (Status › Updates, or `interlatch install --notify-updates`) |

## `[suggestions]`

Proposed fixes for what keeps going wrong ([Suggestions and What goes wrong](suggestions.md)).

| Key | Default | |
| --- | --- | --- |
| `enabled` | `true` | refresh the suggestions after each background sync (no model is called). Nothing is written until you apply one; **Check again** and `interlatch suggest refresh` work either way |
| `notify` | `false` | show a desktop notification when a background sync finds new suggestions |

## `[systems]`

The [Systems map](dashboard.md#systems-map).

| Key | Default | |
| --- | --- | --- |
| `read_manifests` | `true` | read a few manifest files in each project folder (package manifests, compose files, Dockerfiles, Terraform, CI workflows, vite configs, `.env.example`, deploy configs), read-only. `false`: parts come from what sessions did only |

## `[mirror]`

A copy of your archive in a Postgres database you choose ([A copy in Postgres](postgres.md)).

| Key | Default | |
| --- | --- | --- |
| `to` | `""` | `"postgres"` writes the copy after every background run. The connection (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD, PGSSLMODE) is read from `mirror.env` in Interlatch's folder; **Settings › Storage** writes both. Needs the driver: `uv tool install 'interlatch[postgres]'` |
| `include` | `"knowledge"` | `"knowledge"`: sessions' details, summaries and analyses, lessons, knowledge bases, reviews, glossary, artifacts and token usage, no prompts or transcripts. `"everything"`: prompts and transcripts too, with secrets redacted. Any other value counts as `"knowledge"` ([What it holds](postgres.md#what-it-holds)) |
| `schema` | `"chronicle"` | the Postgres schema it writes. One computer per schema ([One computer per schema](postgres.md#one-computer-per-schema)) |

## `[hub]`

One archive for several computers ([Phone and other computers](devices.md#your-other-computers)).

| Key | Default | |
| --- | --- | --- |
| `url` | `""` | on a computer that sends its sessions to a hub: the hub's address. Set by `interlatch hub join`, cleared by `interlatch hub leave`; while it is set, this computer sends instead of recording and analyzing |
| `path_map` | `{}` | on the hub: folders on the other computers that hold the same projects as a folder here, e.g. `{ "/home/me/code" = "/Users/me/Projects" }`. Projects with a git remote are matched by it first |
| `folders` | `{}` | on a computer that sends to a hub: folders here whose sessions belong to a project on the hub, the folder and everything below it, e.g. `{ "/Users/me/work/notes" = "/Users/hub/Projects/demo-app" }`. Set by `interlatch hub add-folder` ([Same project, different folders](devices.md#same-project-different-folders)) |
| `share` | `"everything"` | on a computer that sends to a hub: `"everything"` sends its transcripts and the hub records and analyzes them; `"knowledge"` keeps recording and analyzing here and sends only each session's details, summary and project lessons ([Sharing knowledge only](devices.md#sharing-knowledge-only)). A computer that joined as someone limited to projects must use `"knowledge"`: `interlatch hub join --code` sets it, and the hub refuses transcripts from it |
| `accept` | `"everything"` | on the hub: `"knowledge"` takes only summaries and project lessons from every computer and turns away any that sends transcripts; any value other than `"everything"` counts as `"knowledge"` ([A hub that takes knowledge only](devices.md#a-hub-that-takes-knowledge-only)) |
| `all_folders` | `false` | on a computer that shares knowledge with a hub: `false` shares only sessions the hub files under one of its projects (a folder added with `interlatch hub add-folder`, or a repository whose git remote the hub files there); the rest stay here. `true` shares sessions from every folder. `interlatch hub join --all-folders` sets it ([Sharing knowledge only](devices.md#sharing-knowledge-only)) |
| `store` | `""` | on the hub: `"postgres"` also keeps the team's record in Postgres (connection in `team-store.env` in Interlatch's folder) and sends computers that share knowledge their teammates' lessons ([Teammates' lessons, and a team store in Postgres](devices.md#teammates-lessons-and-a-team-store-in-postgres)) |
| `shared_token` | `true` | on the hub, once it has people: computers may still send with the hub's shared token instead of a token of their own. A computer sending with it is nobody in particular and is not limited to any project. Turn off when everyone has joined with an invite (`interlatch hub shared-token off`) ([The shared token](devices.md#the-shared-token)) |
| `address` | `""` | on the hub: its address as other computers and browsers reach it, e.g. `"https://interlatch.example.internal"`, used in the join commands and sign-in links it hands out. Set by `interlatch hub enable --url` ([People and roles](devices.md#people-and-roles)) |
| `left` | `[]` | on a computer that shares knowledge with a hub: the hub's projects (their paths there) it left. It no longer shares sessions filed under them or gets their teammates' lessons; what it already shared stays. `interlatch hub leave --project` and `rejoin --project` set it ([Leaving](devices.md#leaving)) |
| `dedicated` | `false` | on the hub: it is a server for the team and records no sessions of its own; [the Docker image](docker.md) sets it. Its dashboard then opens on the team's Home, leaves out what only a person's own computer needs, and lets its admins make projects by name under **Team › Projects** ([The hub's dashboard](devices.md#the-hubs-dashboard)) |
| `name` | `""` | on the hub: the name its dashboard shows, e.g. `"Resona team"`; the hub computer's name when empty ([The hub's dashboard](devices.md#the-hubs-dashboard)) |

The projects set up on a hub (`interlatch hub project add`) and the projects each person sees (`interlatch hub invite`,
`interlatch hub access`) live in the hub's database, not in this file ([Projects and who sees
them](devices.md#projects-and-who-sees-them)).
