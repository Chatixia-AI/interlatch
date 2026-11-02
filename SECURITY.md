# Security

Interlatch keeps every coding-agent session you run, and a hub keeps a team's. A hole in it can expose transcripts, so
reports are welcome and taken seriously.

## Reporting a vulnerability

Please don't open a public issue for something that could be used against a running hub or dashboard.

- Report it privately through GitHub: [Report a vulnerability](https://github.com/Chatixia-AI/interlatch/security/advisories/new)
  (the **Security** tab of this repository).
- If that page says private reporting is not enabled, email [chatixia.dev@gmail.com](mailto:chatixia.dev@gmail.com).

Say what you found, where in the code, and how it could be reached, in enough detail to reproduce. You should hear
back within a week. Once a fix is released, the change is described in [CHANGELOG.md](CHANGELOG.md) and the report
is credited there if you want it to be.

Only the latest release on [PyPI](https://pypi.org/project/interlatch/) and the hub image that matches it get
fixes.

## What Interlatch promises

- Everything stays on your computer unless you set up a hub or an API provider yourself
  ([Data and privacy](docs/privacy.md)).
- The dashboard listens on `127.0.0.1` by default. Reached from other devices, it opens only for people an admin
  added, or for a Tailscale login the hub's owner allowed ([Phone and other computers](docs/devices.md)).
- On a hub, each computer's sessions stay its own: another computer can neither replace nor take them back.
- The dashboard page runs only the dashboard's own script files (a Content-Security-Policy). Files agents made open
  under a sandboxing content policy; links a transcript recorded open only as `http(s)`.
- The analyses Interlatch runs with `claude` or `codex` disable every tool, so a transcript can't make the analyzer
  act on your computer.

## Reviews

Interlatch is reviewed with the same coding agents it records.

- **October 2026**: a review of the hub and the dashboard with Claude Code (Claude Mythos 5.1), covering
  authentication and authorization between a hub's computers and people, uploads, file serving, the analyzers'
  subprocesses, the MCP server and the updater. What it found was fixed in
  [#84](https://github.com/Chatixia-AI/interlatch/pull/84) and
  [#91](https://github.com/Chatixia-AI/interlatch/pull/91); the smaller hardening steps it suggested are
  tracked in [#92](https://github.com/Chatixia-AI/interlatch/issues/92).

A review is a point in time, not a guarantee about later changes: [CodeQL](https://github.com/Chatixia-AI/interlatch/actions/workflows/github-code-scanning/codeql)
and the pre-commit secret scan run on every change, and the next review goes on this list.
