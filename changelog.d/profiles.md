- **Profiles, first stage:** `interlatch profile create Acme --owns ~/work/acme` makes a named profile that owns a
  folder (or repositories by git remote, `--remotes`). Its sessions stay off your Personal hub, a folder two profiles
  could claim waits for you (`profile waiting`, `profile choose`), and `interlatch --profile Acme <command>` runs any
  command for it. Sharing with each profile's own hub comes next ([Profiles](docs/profiles.md)).
