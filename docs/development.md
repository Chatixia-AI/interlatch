# Development

[← Interlatch](../README.md) · [Docs index](README.md)

## Tests and a local install

```bash
uv sync && uv run pytest -q        # ~300 tests, ~40 s: a fake `claude` binary and synthetic Codex, Copilot, Bob and Antigravity stores
# redeploy: --reinstall picks up uncommitted edits too (uv rebuilds on its own only when pyproject.toml, the commit or a tag changes)
uv tool install --force --reinstall --python 3.13 . && interlatch install   # install restarts the agents
```

## macOS app

`uv run --extra app interlatch app` runs it from the checkout (menu-bar actions that only make sense
in the bundle, such as Open at Login, are hidden). `./packaging/macos/build.sh` builds `dist/Interlatch.app` and
`dist/Interlatch-<version>-<arch>.dmg` (PyInstaller, ~30 s; `packaging/macos/Interlatch.spec`). One binary is both the
app (no arguments) and the CLI (any arguments), which is how hooks and MCP servers run it. Unsigned builds are ad-hoc
signed and run on the Mac that built them; to distribute, set `INTERLATCH_CODESIGN_IDENTITY` (a Developer ID
Application certificate) and `NOTARY_KEYCHAIN_PROFILE` (from `xcrun notarytool store-credentials`), and the script
signs, notarizes and staples the DMG. `uv run --group build python packaging/macos/make_icon.py` exports the committed
`packaging/macos/icon-3d.webp` artwork (rendered by `packaging/icons3d/render.py`) to the macOS `.icns`, README icon, dashboard logo, favicon and phone
home-screen icons. PNGs can be regenerated on any platform; the `.icns` export needs macOS's `iconutil`. `desktop.py` extends
pywebview's Cocoa app delegate, hence the `<7` pin on pywebview.

## Continuous integration

`.github/workflows/ci.yml` runs on every pull request and every push to `main`: the test suite (`test`, a required
check on `main`), the pre-commit hooks (`lint`) and `pip-audit` over the locked dependencies (`audit`). Dependabot proposes weekly updates for
`uv.lock` and the workflows' actions, which are pinned to commit hashes (the version is in a comment beside each).
GitHub's secret scanning, push protection and CodeQL code scanning are on in the repository settings.

### Pre-commit hooks

`uvx pre-commit install` once, and `.pre-commit-config.yaml` checks each commit: merge markers, YAML and TOML,
files over 600 KB, private keys and other secrets (gitleaks), trailing whitespace and final newlines, `ruff check`,
`uv.lock` matching `pyproject.toml`, the workflows (zizmor) and that the dashboard's JavaScript parses
(`node --check`). Ruff looks for likely bugs only (pyflakes and bugbear); the code keeps its own layout, so nothing
reformats it. `uvx pre-commit run --all-files` runs everything by hand. A test that needs a fake key marks its line
`# gitleaks:allow`.

## Releasing

The version is not written anywhere: it comes from the git tags (hatch-vcs). A tagged commit builds as that version
(`v0.7.0` → `0.7.0`), and commits after it as the next patch's dev release (`0.7.1.dev3+g1a2b3c4`), which is what a
source checkout shows on the Status page.

To release, give each pull request that users will notice a file in `changelog.d/` with its changelog line
([changelog.d/README.md](https://github.com/Chatixia-AI/interlatch/blob/main/changelog.d/README.md); the
**Changelog** check, `.github/workflows/changelog.yml`, fails a pull request that changes what ships without one,
unless it has the `no-changelog` label), then run **Actions → Release → Run
workflow** on `main` and pick `patch`, `minor` or `major`. `.github/workflows/release.yml` works out the next version
from the latest tag, runs the tests, creates the tag and the GitHub release (its notes are the `changelog.d/` files
added since the previous tag),
publishes `interlatch` to PyPI (trusted publishing, environment `pypi`), publishes the hub's image
`ghcr.io/chatixia-ai/interlatch-hub` (tagged with the version and `latest`, for amd64 and arm64, built from that
PyPI release) and attaches the DMG to the release
(signed and notarized when the `MACOS_*` / `APPLE_*` secrets are set; see the workflow header). Last, it opens a
pull request that moves those files' lines into `CHANGELOG.md` under `## <version> (<date>)` and deletes the files.
Merge it whenever: a pull request merged after the tag adds a file the release didn't take, so it goes out in the
next one, and the next release doesn't wait for this pull request. If GitHub
Actions may not create pull requests in this repository, the run fails at its last step, after publishing, with a
link to open that pull request by hand.
`dry run`, the default, runs the tests and keeps the DMG as a workflow artifact, publishing nothing.
To try the hub's image before a release: `uv build --wheel -o docker/wheels && docker build -t interlatch-hub
docker` builds it from this checkout instead of PyPI ([A hub in Docker](docker.md)); CI's `docker` job does the
same and checks that it starts.
Publishing a release tagged `v<version>` on GitHub by hand still works too.

### Checking the menu-bar icon on a real Mac

Tests can't see the menu bar: macOS decides whether an icon shows, per display, and can hide it later. Before a
release that touches `menubar.py`, the menu-bar switch or how the dashboard restarts, check by hand on a MacBook
(with a notch), with an external display attached, from a PyPI-style install (`uv tool install`), and after each step
confirm the icon is in the menu bar of every display:

1. Turn the switch on in **Settings › Status › Recording** (once without the `app` extra, so it installs it).
2. Run an update from **Status › Updates** (or **Reinstall**), which restarts the dashboard.
3. Sleep the Mac and wake it; close the lid with the external display attached, then open it.
4. Unplug the external display and plug it back in.
5. Fill the menu bar until icons go behind the notch, and check what the Status note says.
6. Turn the switch off: the icon goes, on every display.

### One-time setup before the first release

1. On PyPI, add a *pending publisher* (Account → Publishing): project `interlatch`, owner
   `Chatixia-AI`, repository `interlatch`, workflow `release.yml`, environment `pypi`. PyPI matches the
   repository by its name, so after renaming the repository, update the publisher there.
2. In the GitHub repository, create an environment named `pypi` (Settings → Environments).
3. To ship a signed, notarized DMG (Apple Developer Program membership): export the *Developer ID Application*
   certificate with its key as a `.p12`, and add the secrets `MACOS_CERT_P12` (base64 of the file),
   `MACOS_CERT_PASSWORD`, `MACOS_CODESIGN_IDENTITY`, `APPLE_ID`, `APPLE_TEAM_ID` and `APPLE_APP_PASSWORD`
   (an app-specific password from account.apple.com). Without them the DMG is ad-hoc signed and users have to
   approve it in Privacy & Security.
4. Settings → Actions → General → **Allow GitHub Actions to create and approve pull requests**, so the release opens
   the changelog pull request itself. Without it every release ends in a failed step that links to one to open.

## Documentation site

<https://interlatch.com/docs/> is built with MkDocs Material from `docs/` and the READMEs, unchanged: `README.md`
and `README.ja.md` become the home pages, and `docs/_site/hooks.py` points links that leave `docs/` at GitHub.
The website around it, landing page included, lives in
[Chatixia-AI/chronicle-site](https://github.com/Chatixia-AI/chronicle-site), which builds these docs as they are and
publishes everything. `.github/workflows/docs.yml` checks the build on every pull request and, when the docs change on
`main`, asks chronicle-site to rebuild (it also rebuilds daily).

```bash
uv run --only-group docs mkdocs serve            # preview at http://127.0.0.1:8000/, reloads on save
uv run --only-group docs mkdocs build --strict     # what CI runs: fails on broken links and anchors
```

## Diagrams

The diagrams in `docs/diagrams/` are `.excalidraw.svg` files: they render as images and open for editing in the
Excalidraw VS Code extension (`pomdtr.excalidraw-editor`) or on excalidraw.com; saving writes back to the same file.

## Code layout

The code is the `interlatch` package in `src/interlatch/` (`src/chronicle/` only forwards `python -m chronicle`,
which installs from before the rename still run):

- `core/`: configuration, session records, paths, translations, agent names, pricing, redaction, this computer's
  identity, and what counts as an artifact a session made
- `storage/`: the SQLite archive (schema, migrations, connections) and the optional Postgres stores
- `sources/`: where each agent keeps its files (`discovery.py`), a reader per agent (`claude/`, `codex/`, `copilot.py`, `bob.py`, `antigravity.py`) and per chat export
  (`claude/export.py`, `chatgpt.py`)
- `artifacts/`: what sessions made, where each stands, and opening it
- `ingestion/`: syncing every source (`service.py`): the raw-file archive, storing parsed sessions, chat imports,
  agent memory and history, forgetting
- `analysis/`: condensing a transcript (`digest.py`), asking a model for its lessons (`session.py`), screening
  imported chats, the queue and the worker; `analysis/runners/` asks the model, through an agent's CLI or a
  provider's API
- `knowledge/`: per-project knowledge bases (`synthesize.py`), the maturity ladder, glossaries, case files, learning
  topics, friction, suggestions, instruction files, the weekly review
- `projects/`: folders that belong together, project names, which project another computer's folder belongs to
  (`routing.py`), the systems map
- `sharing/`: a hub and the computers that join it: the protocol, tokens, the client and what it pushes and pulls,
  the hub's receiver, projects on the hub, people and what each may see, the Team page's news, the leaderboard
- `application/`: what a worker run does from start to finish (`workflows.py`), session records and search, which
  span the features
- `interfaces/`: the MCP server (`mcp.py`) and the files Interlatch exports (`exports/`: the Markdown vault, session
  exports, Excalidraw diagrams)
- `runtime/`: Interlatch on this computer: installing it (hooks, MCP servers, background jobs), connecting agents
  (`connectors.py`), the status line, moving from Chronicle, updates, notifications, the hub in a container;
  `runtime/platforms/`: Windows (Task Scheduler, the notification area), the macOS app and its menu-bar icon
- `interfaces/cli/`: the command line: `parser.py` assembles the commands, and each module in `commands/` holds a
  group of them (recording, archive, knowledge, setup, sharing, operations), each command's options beside its code;
  `cli.py` at the top is the entry point the `interlatch` command runs
- `interfaces/web/`: the dashboard: `server.py` starts it, `handler.py` checks who asks and routes each request to
  `api/`, where each page's reads and actions are functions of a `Context` (the config, this request's connection, the
  jobs), one module per feature; `app.py` holds what lives as long as the dashboard; `assets.py` serves the page from
  `web/`; `packaging/macos/` builds the app

The dashboard page runs under a Content-Security-Policy (`PAGE_CSP` in `interfaces/web/assets.py`) that allows scripts and styles
from its own files only. Add no inline `<script>` or `on…=` attribute to `index.html`, and give `h()` a style as an
object (`style: { "--h": "40px" }`), never a string: a string becomes a style attribute the policy refuses.
`tests/test_security.py` checks both.

`ee/` is Interlatch Enterprise: its own package (`interlatch_ee`) under the [Interlatch Enterprise License](../ee/LICENSE),
left out of the MIT wheel and sdist. The core never imports it; [ee/README.md](../ee/README.md) says what belongs there.

## Trying a change

`./dev.sh` starts the dashboard from the checkout on a copy of your archive in `~/.interlatch-sandbox/dev-sh`, so
your own `~/.interlatch` and an installed Interlatch are never touched. Its config records, analyzes and
shares nothing. `--app` opens the macOS app window instead, `--menu-bar` adds the [menu-bar icon](install.md#the-menu-bar-icon) to the
browser dashboard (as the login item shows it), `--demo` uses the [demo data](#demo-data), `--fresh`
re-copies the archive, and `--tree ../agents-chronicle-<topic>` runs another worktree's code. The server reads the
web files once at startup, so restart it after editing `app.css` or `app.js`.

```bash
./dev.sh                                  # http://127.0.0.1:8797/ (or the next free port)
./dev.sh --tree ../agents-chronicle-<topic> --app
```

## Demo data

`docs/demo/make_demo.py` builds an Interlatch home from made-up sessions: a fictional developer with five projects
and about six weeks of work. It writes synthetic Claude Code transcripts and runs the real pipeline over them
(sync, analysis, knowledge bases, glossary, weekly reviews). A stand-in `claude` answers each analysis with
hand-written summaries and knowledge, so it costs nothing and needs no login. The screenshots in `docs/images/`
come from it.

```bash
uv run python docs/demo/make_demo.py /tmp/interlatch-demo
INTERLATCH_HOME=/tmp/interlatch-demo/home uv run python -m interlatch ui --port 8898 --open
```

The README's GIF and tour video come from it too. With that dashboard running, `docs/demo/record_demo.py` drives
headless Chromium through a scripted tour (it needs `ffmpeg` on PATH). It writes the whole tour to
`docs/images/demo.mp4`, which git ignores, and a few seconds of it, under the 600 KB the large-file hook allows, to
`docs/images/demo.gif`. The README links the video as an asset of a release, so upload it there:

```bash
uv run --with playwright python docs/demo/record_demo.py --port 8898
gh release upload v<latest> docs/images/demo.mp4 --clobber
```

then point the README's two links at that release.
