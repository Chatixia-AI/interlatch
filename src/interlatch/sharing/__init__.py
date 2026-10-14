"""Several computers, one archive.

One computer, the hub, is the only one that writes the database: it records, analyzes and serves the dashboard.
The others (spokes) send it their coding agents' raw session files over HTTP, usually inside a Tailscale network,
and the hub ingests them as if they were its own, tagged with the computer they came from. A push is stateless:
the spoke asks the hub which files it already has (by size and modification time) and sends only the rest, so a
lost push is simply redone by the next one.

A spoke that already analyzed sessions with its own Interlatch sends those results once, when it first joins, so
the hub does not pay to analyze the same sessions again.

With `[hub] store = "postgres"` the hub also keeps the team's record in Postgres (storage/team.py), and each computer
that shares knowledge gets its teammates' lessons for its own projects back after every push, kept read-only in its
own database (knowledge.source 'team') where its MCP tools and start-of-session notes find them.

Projects can be set up on the hub ahead of time (`interlatch hub project add <folder>`): a folder on the hub computer
whose sessions, and everything below it, are one project that other computers can add folders to before anything was
sent. Its own analyzed sessions go to the team store like a member's, so teammates get the hub owner's lessons too.
A person limited to projects (people.py) hears only of those projects, and the hub keeps only what belongs to them.
"""
