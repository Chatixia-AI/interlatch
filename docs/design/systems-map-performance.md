# Design: a faster Systems map (and dashboard)

[← Roadmap](../../ROADMAP.md) · [Systems map](../dashboard.md#systems-map)

Status: built, 2026-11-12. Measured on version 0.29.9; all four steps under [Order of work](#order-of-work) are done,
with what was built and measured after them in [What was built](#what-was-built).

**Projects › Systems map** took 15–20 seconds to open. Profiling found that the map's own code needs only 2.8 seconds
of CPU. Most of the wait comes from macOS running the whole dashboard at background priority. A one-line change to
the dashboard's launchd job brings the map back to about 3 seconds, and it should make every other dashboard request
faster too. Two more changes would bring it close to instant.

## What was measured

The archive used: 4,527 sessions, 116,390 tool calls (72,985 of them shell commands), 13,529 session files, 2,502
glossary usages, and 77 systems in 17 groups with 100 links. The Mac was mostly idle (load average about 1.7).

| Where the map was built | Time |
| --- | --- |
| The live dashboard, `GET /api/systems`, first load | 15.6 s and 20.4 s (`built_s` in the response agrees) |
| The live dashboard, once cached | 0.012 s |
| The same code and Python (3.14.3, the installed 0.29.9) in its own process | 2.8 s wall, 2.8 s CPU |
| That process under `taskpolicy -b` (background priority) | 17.6 s wall, 16.7 s CPU |
| That process under `taskpolicy -c utility` | 2.8 s |

On 2026-10-06 a build used about 1.5 s of CPU. The archive has grown since then.

The map built inside the server and the one built alone are identical: the same systems, groups and links. The
server isn't doing extra work. It does the same work about six times slower.

## Finding 1: the dashboard runs at background priority

`install_ui_agent` in [runtime/install.py](../../src/interlatch/runtime/install.py) writes the dashboard's launchd
job with `"ProcessType": "Background"`. It has done so since the first import of Chronicle. On macOS, a background
process gets background quality of service. On Apple silicon that means the efficiency cores at a low clock, with
CPU and I/O throttled.

The evidence:

- `sample` on the dashboard during a rebuild showed the request thread busy in regex matching for the whole
  18 seconds. It was not waiting for the GIL (11 samples in `take_gil` out of 12,464), and no other thread was busy.
- The same build in its own process under `taskpolicy -b` takes 17.6 s, the same as in the server. Under
  `taskpolicy -c utility` it still takes 2.8 s, so the slowdown comes from the background clamp, not from any lower
  priority.

The dashboard isn't background work. It answers the user's clicks, and the menu-bar panel and the desktop window run
in the same process. Every CPU-bound request pays this cost, not only the map. Knowledge (0.5 s) and search for a
common word (1.4 s) were measured in the server and probably run several times faster at normal priority (not
measured).

**Proposal:**

- Set the dashboard job's `ProcessType` to `Standard`.
- Keep `Background`, `LowPriorityIO` and `Nice 10` on the sync job (`install_launchd`). That one really is
  background work.
- The dashboard does almost nothing when idle (0.2% CPU over 10 hours), so the change costs no battery.

Existing installs keep their old plist until the job is written again. Today only `interlatch install`
(`interfaces/cli/commands/setup.py`) and the Chronicle migration (`runtime/migrate.py`) write it. The fix also needs
one of these:

- the dashboard checks its own plist at start and rewrites it when `ProcessType` differs;
- `doctor` reports the old value and offers the fix;
- the release notes tell people to run `interlatch install` once.

Tests: `tests/test_setup.py` calls `install_ui_agent`, but no test checks `ProcessType` today, so the change needs an
assertion for the dashboard job (`Standard`) and the sync job (`Background`). Like the other install tests, it must
never run against the real launchd.

## Finding 2: the cache is rebuilt on most visits

`cached()` in [projects/systems.py](../../src/interlatch/projects/systems.py) rebuilds the map in two cases:

- **The signature changes.** It includes `MAX(id) FROM tool_calls`, so it changes every time a sync ingests a new
  tool call, which happens constantly while agents are running.
- **The map is older than `TTL = 300` seconds,** kept so that edits to manifests on disk get picked up.

So almost every first visit to the map pays the full build. The build also holds `_LOCK`, so other requests for the
map wait for it.

**Proposal: serve the last map and rebuild in the background.**

- When the cache is stale, `cached()` returns the previous map at once, starts one rebuild on a background thread
  (if none is running) and marks the response as refreshing. The page updates when the next request sees the new map.
- Only the very first build after the dashboard starts has to wait. That build can also be started when the
  dashboard starts, so in practice it is already done when someone opens the page.
- Optionally, keep the last map in the database's key-value store so a restart doesn't lose it.

## Finding 3: most of the build is parsing the same commands again

`cProfile` of one build (6.4 s with the profiler's overhead):

| Function | Cumulative time | Notes |
| --- | --- | --- |
| `read_commands` | 5.45 s (85%) | 62 systems |
| `_segments` | 2.12 s | 10,847 calls; a Python loop over each character, 9.6 million `list.append` calls |
| `_interesting` | 1.76 s | 213,869 calls; `any(x in text for x in LITERALS)`, 7.2 million generator steps |
| `redact` (through `_ev_command`) | 0.51 s | 12,912 calls |
| `read_manifests` + `_scan` | 0.95 s | reading manifests and walking the folders |

Only 16% of the commands (11,468 of 72,985) pass `_interesting` at all, and checking them takes about 1 s of CPU on
its own. Each build splits, filters and checks the same 73,000 commands from scratch, although a stored command
never changes.

**Proposal: parse each command once and keep the result.** The splitting (`_segments`) and the filter
(`_interesting`) depend only on the command's text. They can be remembered between builds. A prototype that kept
them in two dictionaries (157,216 entries):

| Build | CPU |
| --- | --- |
| Without the memo | 2.79 s |
| First build with the memo (fills it) | 2.75 s |
| Every build after that | **1.16 s** |

The map came out identical. In the real change the memo would be keyed by `tool_calls.id` rather than the text, so
the next build reads only the new rows. It could be held in memory, bounded by the number of tool calls, or stored as
a column filled at ingest (for example, a flag that says the command is worth reading). The column would also let the
SQL skip the other 84% of commands.

## Smaller changes that were tried

Every change below was checked against the baseline. The map stayed identical in each case.

| Change | Result | Verdict |
| --- | --- | --- |
| `_segments` with one regex instead of a loop over each character | 2.91 s → 2.57 s; same output for all 72,985 commands | Worth it, small |
| `_interesting` as one compiled regex of all the literals | 2.57 s → 4.20 s | Rejected: the `in` checks are faster |
| Redacting evidence only when it is kept as an example | no gain that could be measured | Rejected |

The regex for `_segments` handles quotes the way the loop does: single quotes have no escapes, a backslash in double
quotes keeps the next character, and an unterminated quote runs to the end.

```python
SEG_TOKEN = re.compile(r"""'[^']*'?|"(?:\\.|[^"\\])*"?|[;|&\n]|[^'";|&\n]+""", re.S)

def _segments(cmd: str) -> list[str]:
    out, cur = [], []
    for t in SEG_TOKEN.findall(cmd):
        if t in (";", "|", "&", "\n"):
            out.append("".join(cur))
            cur = []
        else:
            cur.append(t)
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]
```

## Order of work

1. **The dashboard's priority** (Finding 1): one line, plus a way to update existing plists. Expected: the map from
   15–20 s to about 3 s, and every CPU-bound dashboard request several times faster.
2. **Serve the last map, rebuild in the background, build at start** (Finding 2). Expected: the map opens at once
   except right after a restart.
3. **Parse each command once** (Finding 3). Expected: a rebuild from 2.8 s to about 1.2 s, and it grows with new
   sessions rather than with the whole archive.
4. **`_segments` as a regex**: optional, about 0.3 s.

The other slow request seen while measuring was search for a common word (1.4 s, which Finding 1 should already
improve). The data folder is 5.3 GB: archive 3.6 GB, database 1.2 GB, backups 506 MB, with almost no free pages in
the database. Neither is covered here.

## What was built

1. **Priority.** `install_ui_agent` writes `ProcessType` `Standard` (`UI_PROCESS_TYPE` in `runtime/install.py`); the
   sync job keeps `Background`, `LowPriorityIO` and `Nice 10`. For existing installs, `refresh_ui_agent()` runs when
   the dashboard starts: only on macOS, only in the launchd agent itself (`XPC_SERVICE_NAME` is `com.interlatch.ui`),
   only when its plist says `Background`. It rewrites the plist and runs `launchctl bootout` then `bootstrap` (retried
   for 15 s) from a shell in its own session, so launchd stopping the old dashboard doesn't stop the reload; the old
   process waits up to 30 s and serves anyway if it is still alive. launchctl's output goes to the agent's
   `ui.err.log`. Neither `setpriority` nor raising a thread's QoS lifts the clamp from inside the process: both were
   tried in a throwaway launchd job, which stayed about 7 times slower. A throwaway job that reloaded itself this way
   came back as a standard process (a loop went from 1.28 s to 0.20 s).
2. **Cache.** `cached()` serves a stale map at once and calls `refresh()`, which builds in a daemon thread with its own
   read-only connection, one build at a time (`_LOCK`). A request waits only when there is no map yet, when
   `read_manifests` or the excluded projects changed, or for an in-memory database. A failed background build is
   logged and keeps the last map until the next change. `serve()` calls `refresh()` 10 s after start, except on a
   dedicated hub.
3. **Parse once.** `parse_command()` returns the `cd`s and the simple commands worth reading. `build()` keeps its
   results in `_PARSED` by tool call id and a hash of the text, because `tool_calls.id` has no AUTOINCREMENT and a
   session ingested again can reuse an id. Each build keeps only the commands it read.
4. **`_segments`** uses one regex (`SHELL_TOKEN`).

Also: the hosts a command names were walked as a set, so the order of parts changed with Python's hash seed, from one
process to the next. They are sorted now. With rebuilds happening unseen in the background, the map would otherwise
reorder itself between visits.

Measured after, on a snapshot of the same archive:

| | Before | After |
| --- | --- | --- |
| Map from the new code vs 0.29.9, same hash seed (0 and 1) | | identical |
| Map from four different hash seeds | differed | identical |
| CPU per build, first / later | 2.78 s / 2.78 s | 2.55 s / 1.08 s |
| A second dashboard from the branch, first map visit right after start | | 2.6 s (before the start-up build ran) |
| The same, 16 s after start | | 0.007 s |

The live dashboard's 15–20 s comes down to the 2.5 s line only once it runs as a standard process, which happens the
first time the new version starts as the launchd agent.

Other platforms need nothing: the Windows dashboard task already asks for priority 6 (normal; the sync task's 7 is
below normal), and the Linux `interlatch-ui.service` has no `Nice` (only the sync service does).

## How to measure again

- **The live map:**
  `curl -s -o /tmp/s.json -w '%{time_total}\n' http://127.0.0.1:11524/api/systems`. Then read `built_s` in the
  response. The cache serves the old map for up to 5 minutes, so a rebuild shows only on the first request after
  that.
- **A build on its own:** open the archive read-only, `load_config(create=False)`, and time
  `interlatch.projects.systems.build(conn, cfg)` with `time.process_time()`. Run it again under `taskpolicy -b` to
  see the background penalty.
- **Where the server spends the time:** `/usr/bin/sample <pid of "interlatch ui"> 18 -file out.txt` while a rebuild
  runs. No root is needed.
- **Load:** check `uptime` first. A loaded Mac makes wall-clock time misleading, so compare CPU time.
