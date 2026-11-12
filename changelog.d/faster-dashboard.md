- **A faster dashboard on macOS:** the dashboard's launchd agent ran it as a background process, which macOS keeps on
  its slower cores, so pages that do real work took several times as long. It now runs as a standard process. The
  first time the new version starts, it loads its agent again by itself, which restarts the dashboard once. The
  **Systems map** opens at once: it is built when the dashboard starts, and a map that is out of date is shown while a
  new one is built in the background (which takes about a second, down from three or more). It also keeps the same
  order from one build to the next ([Systems map](docs/dashboard.md#systems-map)).
