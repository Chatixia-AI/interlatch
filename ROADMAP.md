# Roadmap

[← Interlatch](README.md) · [Changelog](CHANGELOG.md) · 日本語: [ROADMAP.ja.md](ROADMAP.ja.md)

What is coming to Interlatch is tracked as [open issues](https://github.com/Chatixia-AI/interlatch/issues), one per
change, labelled by type (Bug, Feature, Improvement, Spike) and by area (Dashboard, Hub & sharing, Sources and so on).
Nothing has a date. Finished work moves to the [changelog](CHANGELOG.md).

Three rules shape all of it. Interlatch stays local: no server of ours, no telemetry, no account. Analysis runs on
your own account: your Claude Code or Codex login, or your own API key for IBM Bob or a model provider, never a key
we hold. And Interlatch should say what it doesn't know rather than guess.

## Not planned

- **A hosted service, cloud sync or telemetry.** Interlatch's promise is that nothing leaves your machines except
  the analysis call through your own login or key. Sharing across computers goes through your own hub.
- **Computers connecting to a shared database.** One computer, the hub, writes; the others send it their sessions
  and never get a database's address or password. A hub may keep the team's record in your own Postgres as well
  ([team store](docs/devices.md#teammates-lessons-and-a-team-store-in-postgres)), but it stays the only writer.
- **Analysis with an API key we hold.** Analysis always runs on your own account: the Claude Code or Codex login
  already on your computer, or your own API key for IBM Bob or a model provider. With Ollama it never leaves your
  computer.

## Suggesting something

Open an [issue](https://github.com/Chatixia-AI/interlatch/issues/new) describing what you were trying to do and
where Interlatch got in the way. That helps more than a feature name.
