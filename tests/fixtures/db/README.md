# Archives written by old releases

Each folder holds a `chronicle.db` that an old release wrote with its own code, and the facts
`tests/test_db_compat.py` checks once the current code has opened a copy of it (`expected.json`). The test fails when a
change to the schema or its migrations (`src/interlatch/storage/`) stops an existing archive from opening, loses or
changes what is in it, or changes it again the second time it opens.

| Folder   | Written by                                     | Schema (`user_version`) | Size   |
|----------|------------------------------------------------|-------------------------|--------|
| `0.5.0`  | agents-chronicle 0.5.0 (git tag `v0.5.0`)      | 7                       | 79 KB  |
| `0.21.3` | agents-chronicle 0.21.3 (git tag `v0.21.3`)    | 18                      | 119 KB |
| `0.27.8` | interlatch 0.27.8 (PyPI)                       | 19                      | 119 KB |

- **0.5.0**: before knowledge had a maturity stage (schema 8 derives it), and before hubs, people, groups,
  suggestions and artifacts.
- **0.21.3**: the last release named Chronicle (`chronicle.db`'s code lived in `src/chronicle/db.py`); knowledge has
  no `basis` yet.
- **0.27.8**: the last release before schema 20, which clears what a hub kept of other computers' analyses and
  redacts titles again.

agents-chronicle, the package's name before the rename to Interlatch (0.22.0), is no longer on PyPI, so those
releases install from their git tag.

## What's in them

Every row is made up (no data from anyone's archive). The same rows go into each, minus the columns and tables that
release didn't have yet:

- 3 sessions in two projects (`/home/fixture/projects/alpha`, `.../beta`), one of them with a fake secret in its
  title (`password=fixture-not-a-secret`, redacted by schema 20); from 0.21.3 on, a 4th that another computer shared
  with this one as a hub (`source = 'remote'`, with the raw analysis schema 20 clears)
- 3 `session_files`, 5 `events` (4 of them in the full-text index), 3 `tool_calls`
- 6 knowledge items (7 with the remote session's): fix, gotcha, decision, command, preference (and fact), one at each
  stage (`wip`, `provisional`, `established`, `canonical`), one pinned, one superseded, one from the agent's memory
- a `kv` key, a `machines` row, an alpha `project_kb`, a glossary term; from 0.21.3 on a `hub_projects` row, a
  project group and an artifact

`expected.json` lists, per archive, the row counts, every session's title (as the current code shows it),
every knowledge item's kind, title, status, pin, project and stage (for 0.5.0, the stage the current code derives),
and what the knowledge and session searches must find.

## Making them again

```sh
python tests/fixtures/db/make_fixture.py all        # or one version: 0.5.0 | 0.21.3 | 0.27.8
```

For each version it runs, with a clean environment and a throwaway `HOME` (nothing touches `~/.interlatch`):

```sh
env -i HOME=<scratch>/home PATH=/usr/bin:/bin:<uv's folder> \
  uvx --python 3.12 --from <spec> python -I tests/fixtures/db/make_fixture.py --child <scratch>/chronicle.db <scratch>/expected.json
```

with `<spec>` `git+https://github.com/Chatixia-AI/interlatch@v0.5.0`, `...@v0.21.3` or `interlatch==0.27.8`. In that
child the release's own `connect()` creates the archive (its schema, its WAL mode), plain SQL inserts the rows, and the
archive is VACUUMed with 1 KiB pages to keep it small. Then it is copied here with its `expected.json`.

To cover another release, add it to `RELEASES` in `make_fixture.py`, run it for that version, and commit the new
folder. Never regenerate an existing one to make a failing test pass: a failure means the current code no longer reads
what that release wrote.
