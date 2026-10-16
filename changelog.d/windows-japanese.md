- **Windows in Japanese (and other languages):** Task Scheduler's and PowerShell's own messages now read in Windows'
  language instead of coming out garbled, and a `claude.cmd` (npm) or another tool that reports an error in the console's
  code page no longer ends `interlatch install` in a traceback. The tasks name your account by its SID, so a PC joined to
  Microsoft Entra ID registers them too; a folder name with a full-width space works in hooks; an npm-installed Claude
  Code gets the whole analysis prompt; the shared log no longer prints "Logging error" when another process holds it.
  The old console window prints `+` and `>` for ✓ and ›, the dashboard says Ctrl for ⌘ off a Mac, and the install steps
  set PATH in the same window, where uv's installer left "uv is not recognized" ([Windows](docs/install.md#windows)).
