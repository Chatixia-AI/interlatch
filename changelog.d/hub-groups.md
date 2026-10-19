- **Several hubs: connect your groups.** `interlatch profile` became `interlatch hubs` and `interlatch group`: add a
  hub (`interlatch hubs add Acme <address> --code <code>`), then connect a group of projects to it
  (`interlatch group connect "Acme work" Acme`). What the group holds goes to that hub, and changing the group on the
  dashboard's Projects page moves its sessions with it. A project taken out of a connected group waits for you
  instead of going back to Personal. `--profile` is now `--hub`; the old name still works
  ([Several hubs on one computer](docs/profiles.md)).
