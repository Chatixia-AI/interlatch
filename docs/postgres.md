# A copy in Postgres

[← Interlatch](../README.md) · [Docs index](README.md)

Interlatch keeps your archive in its own SQLite database on your computer (`~/.interlatch/chronicle.db`). It can
also keep a copy in a Postgres database you choose: one on this computer, in Docker, or in the cloud. You can query
that copy with SQL or a BI tool (Metabase, Grafana, Power BI, a notebook), or keep it as a second copy somewhere else.

The copy is one-way. Interlatch writes to it after every background run and never reads it back: the dashboard, search,
the MCP server and the start-of-session notes keep using the database on your computer, and they work the same if
Postgres is down.

## Setting it up

You need a Postgres database and a user that may create tables in it. You also need the Postgres driver, which comes
with the `postgres` extra:

```bash
uv tool install --reinstall 'interlatch[postgres]'
```

Restart the dashboard afterwards (quit and reopen the app, or `interlatch ui` again).

### In the dashboard

Open **Settings › Storage**, switch **Copy in Postgres** to **Postgres**, and fill in the server address, port,
database, user, password and SSL mode. **What it holds** picks how much goes ([below](#what-it-holds)). Picking **Everything**
shows a warning about what transcripts carry, and **Save** asks you to confirm before transcripts start to go. **Test
connection** checks the settings, and **Save** checks them again, saves them, and writes the copy straight away.
Nothing is saved unless the connection works. The password goes in `mirror.env` in Interlatch's folder, readable by
your user only, and the dashboard never shows it again.

The Storage page shows what the copy holds, when it was last written, and any error. **Write now** brings it up to date
without waiting for the next background run. On a hub with people, only an admin can open the page.

### From the command line

```bash
# PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD (and PGSSLMODE, require by default), one per line:
$EDITOR ~/.interlatch/mirror.env && chmod 600 ~/.interlatch/mirror.env
interlatch config set mirror.to postgres
interlatch mirror sync        # writes the copy now and says what it wrote
interlatch mirror             # where it is, how many rows each table holds, and the last write
```

### Postgres in Docker on this computer

```bash
docker run -d --name interlatch-pg --restart unless-stopped \
  -e POSTGRES_USER=interlatch -e POSTGRES_DB=interlatch -e POSTGRES_PASSWORD=<a password> \
  -p 127.0.0.1:5432:5432 -v interlatch-pg:/var/lib/postgresql/data postgres:17
```

Then use server address `127.0.0.1`, port `5432`, database and user `interlatch`, your password, and SSL mode
`disable`. Docker's Postgres has no TLS certificate, and the port is open to this computer only. The data lives in the
`interlatch-pg` volume, so it survives the container being recreated.

### A database in the cloud

Any managed Postgres works: Azure Database for PostgreSQL, Amazon RDS, Google Cloud SQL, Supabase, Neon, and others.
Keep SSL mode `require` (the default), or `verify-full` if the provider gives you its CA certificate. Allow your
computer's address through the database's firewall. Give Interlatch a user of its own that owns the database, or that
may create a schema in it.

## What it holds

`[mirror] include` sets how much goes. It is `knowledge` unless you choose otherwise:

| | `knowledge` (default) | `everything` |
| --- | --- | --- |
| Sessions: agent, project, times, models, token counts, cost, outcome, summary and analysis | ✓ | ✓ |
| Your lessons (`knowledge`), knowledge bases (`project_kb`), weekly reviews, glossary | ✓ | ✓ |
| Artifacts, files each session touched, token usage per API call, subagents, the analysis log | ✓ | ✓ |
| Each session's first and last prompt, and subagents' task descriptions | | ✓ |
| Transcripts: every message (`events`) and tool call (`tool_calls`) | | ✓ |

- **Secrets.** With `everything`, prompts and transcripts go with secrets redacted, the same way they are before
  analysis ([What redaction catches](privacy.md#what-redaction-catches)). Redaction works by patterns, so it can miss
  a secret that looks like nothing in particular. Pick `everything` only for a database you trust with your
  transcripts.
- **Titles.** With `knowledge`, a session whose only title is the opening of its first prompt goes without one.
- **Excluded projects.** Sessions in projects you excluded (`[sources] exclude_projects`) never go, nor do their
  lessons. A project you exclude later is removed from the copy at the next write.
- **Imported chats.** Imported claude.ai and ChatGPT chats are part of your archive, so they go too. Their full text
  goes only with `everything`.
- **Never.** Interlatch's own bookkeeping never goes: where transcripts sit on disk, retry counters, a hub's keys and
  people.

Going back from `everything` to `knowledge` removes the transcript tables and the prompt columns from the copy at the
next write. Postgres frees their space when it next vacuums the tables.

The tables keep the names they have in Interlatch's database, in the schema `chronicle` (`[mirror] schema`). Times are
`timestamptz` columns, `*_json` columns are `jsonb`, and each table has its usual key. Each keyed table also has a
`_hash` column, which the next write compares, and a `_synced_at` column. The table `_mirror` says which computer
writes the schema, with which `include`, and when it last did.

## How it stays up to date

- **After every background run**, every 15 minutes, Interlatch compares the copy with your database and writes only
  what changed: a session that was analyzed or continued is written again along with its own rows (usage, files,
  artifacts, transcript). Rows deleted here are removed there. With nothing new, a write takes about a second.
- **The first write** copies everything. For an archive of about 4,000 sessions it takes a few seconds with
  `knowledge` and about a minute with `everything` to a database on the same computer. Over the internet it takes longer.
- **If Postgres can't be reached**, nothing on your computer is affected. The Storage page and `interlatch mirror` show
  the error, and the next run catches up.
- **`interlatch mirror sync --full`** writes every row again, whatever the copy holds.
- **Turning it off** (**Off** on the Storage page, or `interlatch config set mirror.to ""`) stops the writes. The copy
  and the settings stay, so turning it on again needs no password.

## One computer per schema

Each computer writes a schema of its own. Interlatch refuses a schema that another computer already writes, so two
computers never overwrite each other's copy. To copy several computers into one database, give each a different
`[mirror] schema` (for example `chronicle_laptop` and `chronicle_desktop`). To bring several computers' sessions
together in one archive instead, use a hub ([Phone and other computers](devices.md#your-other-computers)), and point the
hub's mirror at Postgres.

Interlatch owns the schema it writes. It removes columns it didn't create from its tables, so put your own views and
tables in another schema.

## Querying it

```sql
-- What each agent cost per month
SELECT agent, date_trunc('month', started_at)::date AS month, count(*) AS sessions,
       round(sum(est_cost_usd)::numeric, 2) AS usd
FROM chronicle.sessions GROUP BY 1, 2 ORDER BY 2 DESC, 4 DESC;

-- Your lessons about one project, newest first
SELECT kind, title, body FROM chronicle.knowledge
WHERE project_name = 'my-app' AND status = 'active' ORDER BY updated_at DESC;

-- Tokens per model per day
SELECT ts::date AS day, model, sum(input_tokens + output_tokens) AS tokens
FROM chronicle.api_calls GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC;

-- Lessons tagged postgres, with the project they came from
SELECT s.project_name, k.title FROM chronicle.knowledge k JOIN chronicle.sessions s ON s.id = k.session_id
WHERE k.tags_json ? 'postgres';
```

To give a BI tool access, use a read-only role. Run this as Interlatch's user, so the tables it creates later
(the transcript tables, when you switch to `everything`) are readable too:

```sql
CREATE ROLE bi LOGIN PASSWORD '…';
GRANT USAGE ON SCHEMA chronicle TO bi;
GRANT SELECT ON ALL TABLES IN SCHEMA chronicle TO bi;
ALTER DEFAULT PRIVILEGES IN SCHEMA chronicle GRANT SELECT ON TABLES TO bi;
```
