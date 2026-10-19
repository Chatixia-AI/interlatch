- **Leaving a hub leaves nothing behind for the next one:** `interlatch hub leave` (and **Leave the hub…** in
  **Settings › Devices**) now also clears the folders, left projects and shared groups the computer had for that hub,
  and its teammates' lessons, so a different hub joined later no longer gets the first one's folders and groups. They
  are put aside, and joining the same hub again asks whether to bring them back (`--restore` / `--no-restore`).
  `interlatch hub join` refuses a second hub while the computer is in one, before it sends anything there; joining the
  same hub again still just renews the token. A hub that moved: `interlatch hub set-url <address>`
  ([Joining your team's hub](docs/join-a-hub.md)).
