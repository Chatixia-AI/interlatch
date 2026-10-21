- **Lessons say when their code has changed:** a lesson now records the files it was learned from (those the session
  read or changed), and when one of them changes, the SessionStart digest and the MCP search mark it *may be out of
  date* and name the file, so an agent checks it before relying on it
  ([When a lesson may be out of date](docs/analysis.md#when-a-lesson-may-be-out-of-date)).
