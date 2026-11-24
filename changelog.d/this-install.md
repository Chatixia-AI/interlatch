- **`interlatch status` and `connect` no longer mix up two installs:** in a terminal where another copy of Interlatch
  came first on `PATH` (a checkout's `.venv` switched on in an editor), `interlatch status` said every agent ran "not
  this install", and `interlatch connect` wrote that other copy's command into the agent's settings. Both now go by
  the install that is running.
