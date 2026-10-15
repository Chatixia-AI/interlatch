#!/usr/bin/env bash
# Start Interlatch from a checkout for testing, on a sandbox copy of your archive: your live ~/.interlatch and
# the Interlatch installed from PyPI are never touched. Nothing is recorded, analyzed or sent to a hub.
#
#   ./dev.sh                     dashboard in the browser, from this checkout, on a copy of your archive
#   ./dev.sh --app               the macOS app window instead
#   ./dev.sh --menu-bar          the browser dashboard with Interlatch's menu-bar icon, as the login item shows it
#   ./dev.sh --demo              made-up demo data instead of your archive (docs/demo/make_demo.py)
#   ./dev.sh --fresh             re-copy the archive (or rebuild the demo) first
#   ./dev.sh --tree ../agents-chronicle-<topic>   run another worktree's code
#   ./dev.sh --port 8800         a fixed port (default: the first free one from 8797)
#   ./dev.sh --no-open           don't open a browser tab
#
# The sandbox lives in ~/.interlatch-sandbox/dev-sh (demo: ~/.interlatch-sandbox/dev-sh-demo). The server reads the web
# files once at startup: after editing app.css or app.js, stop it (Ctrl-C), run it again and hard-reload the page.
set -euo pipefail

TREE="$(cd "$(dirname "$0")" && pwd)"
LIVE="$HOME/.interlatch"
if [ ! -f "$LIVE/chronicle.db" ] && [ -f "$HOME/.claude-chronicle/chronicle.db" ]; then
  LIVE="$HOME/.claude-chronicle"  # its name before the rename, until Interlatch moves it
fi
APP=0 DEMO=0 FRESH=0 OPEN=--open PORT="" MENU=0

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --app) APP=1 ;;
    --menu-bar) MENU=1 ;;
    --demo) DEMO=1 ;;
    --fresh) FRESH=1 ;;
    --no-open) OPEN="" ;;
    --tree) TREE="$(cd "${2:?--tree needs a path}" && pwd)"; shift ;;
    --port) PORT="${2:?--port needs a number}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "dev.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[ -f "$TREE/pyproject.toml" ] && { [ -d "$TREE/src/interlatch" ] || [ -d "$TREE/src/chronicle" ]; } || { echo "dev.sh: $TREE is not an Interlatch checkout" >&2; exit 1; }

listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

if [ "$DEMO" = 1 ]; then
  SANDBOX="$HOME/.interlatch-sandbox/dev-sh-demo"
  if [ "$FRESH" = 1 ] || [ ! -f "$SANDBOX/home/chronicle.db" ]; then
    echo "Building the demo data in $SANDBOX ..."
    /bin/rm -rf "$SANDBOX"
    uv run --project "$TREE" python "$TREE/docs/demo/make_demo.py" "$SANDBOX" >/dev/null
  fi
  HOME_DIR="$SANDBOX/home"
else
  HOME_DIR="$HOME/.interlatch-sandbox/dev-sh"
  mkdir -p "$HOME_DIR/empty"
  if [ "$FRESH" = 1 ] || [ ! -f "$HOME_DIR/chronicle.db" ]; then
    [ -f "$LIVE/chronicle.db" ] || { echo "dev.sh: no archive at $LIVE/chronicle.db (try --demo)" >&2; exit 1; }
    echo "Copying your archive to $HOME_DIR ..."
    /bin/rm -f "$HOME_DIR/chronicle.db" "$HOME_DIR/chronicle.db-wal" "$HOME_DIR/chronicle.db-shm"
    sqlite3 "$LIVE/chronicle.db" ".backup '$HOME_DIR/chronicle.db'"  # a consistent copy, even while Interlatch writes
  fi
  # Written once: edit it to try other settings. Sources point at an empty folder, so Sync records nothing.
  if [ ! -f "$HOME_DIR/config.toml" ]; then
    E="$HOME_DIR/empty"
    cat > "$HOME_DIR/config.toml" <<EOF
# dev.sh sandbox: a copy of your archive; nothing is recorded, analyzed or shared
[sources]
claude_dirs = ["$E"]
codex_dirs = ["$E"]
copilot_dirs = ["$E"]
bob_dirs = ["$E"]
antigravity_dirs = ["$E"]
opencode_dirs = ["$E"]
codex_cloud = false
import_history = false
import_memory = false

[analysis]
auto = false

[synthesis]
auto = false

[export]
markdown = false

[server]
host = "127.0.0.1"

[updates]
check_daily = false
EOF
  fi
fi

# Both names: a --tree from before the rename reads only CHRONICLE_HOME, and would otherwise use the live archive.
# Every checkout has the `chronicle` command; `interlatch` only those from the rename on.
export INTERLATCH_HOME="$HOME_DIR" CHRONICLE_HOME="$HOME_DIR"
cd "$TREE"
echo "Code:    $TREE ($(git -C "$TREE" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?'))"
echo "Data:    $INTERLATCH_HOME"

if [ "$APP" = 1 ]; then
  echo "Opening the app window (quit it from the menu bar icon, or Ctrl-C here)"
  exec uv run --project "$TREE" --extra app chronicle app
fi

if [ -z "$PORT" ]; then
  PORT=8797
  while listening "$PORT"; do PORT=$((PORT + 1)); done
elif listening "$PORT"; then
  echo "dev.sh: port $PORT is in use" >&2; exit 1
fi
echo "Open:    http://127.0.0.1:$PORT/   (Ctrl-C to stop)"
if [ "$MENU" = 1 ]; then
  echo "Menu bar: Interlatch's icon (quit it from its menu, or Ctrl-C here)"
  exec uv run --project "$TREE" --extra app chronicle ui --host 127.0.0.1 --port "$PORT" $OPEN --menu-bar
fi
exec uv run --project "$TREE" chronicle ui --host 127.0.0.1 --port "$PORT" $OPEN
