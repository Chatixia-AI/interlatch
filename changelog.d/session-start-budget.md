- **New sessions get whole notes, never a cut-off one:** the knowledge digest a new session receives
  (`interlatch install --inject-context`) now fits its length limit by leaving out the least useful notes and the
  oldest sessions whole, and its last line says how many it left out so the agent knows to search for them. It is also
  checked for secrets once more on its way out ([Configuration](docs/configuration.md#synthesis-export-server-inject-updates)).
