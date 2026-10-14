"""What goes wrong: recurring failure causes across recorded sessions, found without an LLM.

Two signals feed it. Failed tool results (events with is_error=1) carry a distinctive literal for most wasteful
causes ('outside allowed roots', 'Browser is already in use', 'Blocked: sleep', 'no matches found:'), and the friction
notes the session analysis wrote (sessions.friction_json) describe the same causes in prose, in English or Japanese.
A curated CATALOG maps both to a cause and its concrete fixes. A note can name several causes, so notes are
multi-label; a failed result that matches a wasteful cause is never counted as noise.

Noise causes (a failing test inside a dev loop, a read-only chain whose last command exited non-zero, provider
outages) never produce suggestions. They are reported as context, so the rest of the report is about waste.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from .i18n import tr
from .util import loads, one_line, parse_ts, utcnow

STILL_HAPPENING_DAYS = 14
WEEKS = 12  # sparkline length
OVERLAP_MINUTES = 10  # another session active this close to a contention error means the tree was shared
OTHER_MIN_SESSIONS = 3
CLAUDE_ONLY = ["claude"]
SHELL_AGENTS = ["claude", "codex"]

# ---------------------------------------------------------------- catalog
# error_patterns: regexes on the failed tool result's text (case-insensitive). An entry is either a string or a dict
#   {text, tool, target} where `tool` restricts the tool name and `target` must match the call's command/file path.
# tools: a tool-name regex that restricts every string pattern of the entry.
# friction_patterns: regexes on friction notes. needs_repeat: a failed result counts only when the next result in the
# same session fails the same way (the waste is the retry, not the first refusal).
CATALOG: list[dict] = [
    {
        "id": "playwright-output-roots",
        "name": "Playwright MCP rejects screenshot paths outside its allowed roots and blocks file:// URLs",
        "category": "mcp-config",
        "noise": False,
        "tools": r"playwright|browser_",
        "error_patterns": [r"outside allowed roots", r"file: protocol is blocked", r"access to file: ?urls? is (blocked|denied)",
                           {"text": r"file does not exist", "tool": r"^Read$", "target": r"\.playwright-mcp/"}],
        "friction_patterns": [r"outside (the )?allowed roots", r"allowed[- ]roots?",
                              r"file:// ?(navigation|url|urls|preview)?.{0,30}(blocked|denied)",
                              r"(blocked|denied).{0,30}file://", r"\.playwright-mcp",
                              r"playwright.{0,60}screenshot.{0,60}(path|root|denied|blocked|saved)",
                              r"screenshot.{0,40}(outside|wrong (path|folder|directory)|repo root)", r"許可.{0,10}ルート",
                              r"スクリーンショット.{0,40}(保存|パス|出力).{0,30}(拒否|許可|できな|失敗|範囲外)",
                              r"file://.{0,30}(ブロック|拒否|開けな|使えな)", r"(ブロック|拒否).{0,20}file://"],
        "fixes": [
            {"kind": "config", "scope": "user", "agents": CLAUDE_ONLY,
             "title": "Give Playwright MCP its own output directory and an isolated browser profile",
             "text": 'mcpServers.playwright.args += ["--isolated", "--output-dir", "{playwright_output_dir}"]'},
            {"kind": "instruction", "scope": "user", "agents": CLAUDE_ONLY,
             "title": "Save Playwright screenshots by bare filename; preview over http, not file://",
             "text": "Pass browser_take_screenshot a bare filename (it is saved in the Playwright MCP output directory); never "
                     "an absolute scratchpad path. Preview local HTML over http://localhost (`python3 -m http.server`), not file://.",
             "text_ja": "browser_take_screenshot にはファイル名だけを渡す（Playwright MCP "
                        "の出力ディレクトリに保存される）。スクラッチパッドの絶対パスは渡さない。ローカルの HTML は "
                        "file:// ではなく http://localhost（`python3 -m http.server`）でプレビューする。"},
        ],
    },
    {
        "id": "playwright-browser-in-use",
        "name": "Playwright MCP 'Browser is already in use': another session holds the shared Chrome profile",
        "category": "mcp-config",
        "noise": False,
        "tools": r"playwright|browser_",
        "error_patterns": [r"browser is already in use"],
        "friction_patterns": [r"browser is already in use", r"(browser|chrome|profile).{0,40}(already in use|locked|held by)",
                              r"(mcp )?browser.{0,30}in use by (the )?(other|another)", r"singletonlock",
                              r"ブラウザ.{0,20}使用中", r"(プロファイル|ブラウザ).{0,20}(ロック|占有)"],
        "fixes": [
            {"kind": "config", "scope": "user", "agents": CLAUDE_ONLY,
             "title": "Run Playwright MCP with --isolated so parallel sessions get their own browser",
             "text": 'mcpServers.playwright.args += ["--isolated"]'},
            {"kind": "instruction", "scope": "user", "agents": CLAUDE_ONLY,
             "title": "Stop on 'Browser is already in use' instead of retrying",
             "text": "If Playwright MCP says the browser is already in use, do not retry or delete lock files: another "
                     "session holds it. Tell me, or use a headless Playwright script for this check.",
             "text_ja": "Playwright MCP がブラウザーは使用中（browser is already in use）だと言ったら、"
                        "再試行もロックファイルの削除もしない。別のセッションが使っている。私に伝えるか、"
                        "この確認にはヘッドレスの Playwright スクリプトを使う。"},
        ],
    },
    {
        "id": "playwright-stale-refs",
        "name": "Playwright actions on refs from an outdated snapshot or on ambiguous selectors",
        "category": "tool-misuse",
        "noise": False,
        "error_patterns": [r"ref \S+ not found in the current page snapshot", r"strict mode violation",
                           r"execution context was destroyed",
                           {"text": r"(locator\.\w+|waiting for).{0,40}timeout \d+ms exceeded|timeout \d+ms exceeded",
                            "tool": r"playwright|browser_"}],
        "friction_patterns": [r"ref\b.{0,30}not found", r"strict[- ]mode", r"stale (element )?refs?", r"outdated snapshot",
                              r"execution context was destroyed", r"ambiguous (selector|locator)s?",
                              r"(古い|最新でない|以前の)スナップショット", r"スナップショット.{0,30}(取り直|再取得|古く)",
                              r"(?<![a-z])ref(?![a-z]).{0,20}(見つから|無効)",
                              r"(セレクター?|ロケーター?).{0,20}(曖昧|あいまい|複数.{0,10}一致)"],
        "fixes": [
            {"kind": "instruction", "scope": "user", "agents": CLAUDE_ONLY,
             "title": "Take a fresh Playwright snapshot before using refs",
             "text": "Playwright: take a fresh browser_snapshot after any navigation, reload or re-rendering click and use "
                     "only refs from it; after navigating, browser_wait_for a known text before browser_evaluate. In "
                     "scripts use getByRole(role, {name, exact: true}) or a scoped locator, never bare text= selectors.",
             "text_ja": "Playwright：ページ遷移、再読み込み、再描画を起こすクリックのあとは browser_snapshot "
                        "を取り直し、その ref だけを使う。遷移したら browser_evaluate の前に browser_wait_for "
                        "で既知のテキストを待つ。スクリプトでは getByRole(role, {name, exact: true}) "
                        "か範囲を絞ったロケーターを使い、text= だけのセレクターは使わない。"},
        ],
    },
    {
        "id": "edit-stale-context",
        "name": "Edits fail on stale context: the file changed since it was read, or the expected lines no longer match",
        "category": "tool-misuse",
        "noise": False,
        "error_patterns": [r"failed to find expected lines", r"string to replace not found", r"modified since read",
                           r"file has not been read yet"],
        "friction_patterns": [r"failed to find expected lines", r"string to replace not found", r"modified since (it was )?read",
                              r"stale (file )?context", r"apply_patch.{0,40}(fail|mismatch|verification)",
                              r"(patch|hunk|edit)es?.{0,30}(did not|didn't|failed to) (match|apply)",
                              r"パッチ.{0,20}(失敗|適用でき)", r"(編集|置換|Edit).{0,20}(失敗|できなかった|できず)",
                              r"ファイルが.{0,20}(変更|更新)されていた", r"(読み込|読ん)だ(後|あと)に.{0,20}(変更|更新|変わ)",
                              r"(一致する|該当する|想定した|対象の)(行|文字列|箇所)が.{0,10}(見つから|な[いかく])"],
        "fixes": [
            {"kind": "instruction", "scope": "user", "agents": SHELL_AGENTS,
             "title": "Re-read the exact lines right before every edit",
             "text": "Re-read the exact lines (Read, or `sed -n 'a,bp' file`) in the same turn before every edit and copy "
                     "context from that output, never from memory. Keep patches small with unique anchors and one "
                     "operation per file. After a failed edit, re-read the file before retrying.",
             "text_ja": "編集のたびに、同じターンの中で直前に該当行を読み直し（Read か `sed -n 'a,bp' file`）、"
                        "文脈は記憶ではなくその出力からコピーする。パッチは小さく一意なアンカーで、1 ファイルにつき "
                        "1 操作にする。編集が失敗したら、再試行の前にファイルを読み直す。"},
        ],
    },
    {
        "id": "zsh-nomatch",
        "name": "zsh aborts a whole command on an unquoted glob that matches nothing ('no matches found')",
        "category": "environment",
        "noise": False,
        "error_patterns": [r"no matches found:"],
        "friction_patterns": [r"no matches found", r"zsh.{0,40}glob", r"glob.{0,40}zsh", r"unquoted glob", r"--include=\*",
                              r"グロブ", r"(glob|ワイルドカード).{0,20}(一致|マッチ|展開).{0,10}(せず|しな|できな|失敗)"],
        "fixes": [
            {"kind": "environment", "scope": "user", "agents": ["all"],
             "title": "Let unmatched globs pass through in zsh, as bash does",
             "text": "echo 'setopt NO_NOMATCH' >> ~/.zshrc"},
            {"kind": "instruction", "scope": "user", "agents": SHELL_AGENTS,
             "title": "Quote every glob: the shell is zsh",
             "text": "The shell is zsh: quote every glob argument (`grep -rn --include='*.py'`, `ls 'out*.png'`) or use "
                     "`rg -g '*.py'`.",
             "text_ja": "シェルは zsh：glob の引数はすべて引用符で囲む（`grep -rn --include='*.py'`、`ls 'out*.png'`）"
                        "か、`rg -g '*.py'` を使う。"},
        ],
    },
    {
        "id": "zsh-equals",
        "name": "zsh expands a word starting with '=' ('echo ====' fails with '=== not found')",
        "category": "environment",
        "noise": False,
        "error_patterns": [r"(^|\n|:\s?)=+\S* not found"],
        "friction_patterns": [r"=== ?not found", r"echo ={3,}", r"equals expansion", r"=\s?で始まる.{0,20}(展開|語|単語)",
                              r"区切り(線|行).{0,30}(展開|エラー|失敗)"],
        "fixes": [
            {"kind": "environment", "scope": "user", "agents": ["all"],
             "title": "Turn off zsh's '=command' expansion",
             "text": "echo 'unsetopt EQUALS' >> ~/.zshrc"},
            {"kind": "instruction", "scope": "user", "agents": SHELL_AGENTS,
             "title": "Quote separator lines in zsh",
             "text": "In zsh, print separators quoted (`echo '=== name ==='`) or as `echo ---`; never a bare `====`.",
             "text_ja": "zsh では区切り線を引用符で囲んで出力する（`echo '=== name ==='`）か `echo ---` にする。裸の "
                        "`====` は使わない。"},
        ],
    },
    {
        "id": "zsh-dialect",
        "name": "Bash idioms that zsh treats differently (read-only $status, no word splitting, history modifiers)",
        "category": "environment",
        "noise": False,
        "error_patterns": [r"read-only variable: status", r"bad (math expression|substitution|pattern)"],
        "friction_patterns": [r"read-only variable", r"zsh.{0,60}(word[- ]split|history[- ]modifier|read-only)",
                              r"(word[- ]split|history[- ]modifier).{0,40}zsh", r"zsh (semantics|dialect|quirk)",
                              r"読み取り専用の?変数", r"zsh.{0,40}(単語分割|ワード分割|履歴修飾)", r"(単語分割|ワード分割).{0,40}zsh",
                              r"bash.{0,30}(書き方|構文|イディオム).{0,30}zsh", r"zsh.{0,30}bash.{0,10}と(は|の)?.{0,6}(違|異な)"],
        "fixes": [
            {"kind": "instruction", "scope": "user", "agents": SHELL_AGENTS,
             "title": "Write zsh, not bash, in shell commands",
             "text": "The shell is zsh: never name a variable `status`, write `${var}` before a `:`, keep multi-word "
                     "options in an array rather than a string variable, and wrap bash-only snippets in `bash -c '...'`.",
             "text_ja": "シェルは zsh：変数名に `status` を使わない、`:` の前は `${var}` と書く、"
                        "複数語のオプションは文字列の変数ではなく配列に入れる、bash 専用のスニペットは `bash -c "
                        "'...'` で包む。"},
        ],
    },
    {
        "id": "sleep-polling-blocked",
        "name": "Chained 'sleep N; cmd' polling is blocked by Claude Code",
        "category": "tool-misuse",
        "noise": False,
        "error_patterns": [r"blocked: sleep"],
        "friction_patterns": [r"blocked: sleep", r"sleep \d+\s*(;|&&)", r"chained .?sleep", r"sleep.{0,40}(blocked|rejected)",
                              r"sleep.{0,40}(ブロック|拒否|禁止|使えな)", r"(ブロック|拒否)された.{0,10}sleep"],
        "fixes": [
            {"kind": "instruction", "scope": "user", "agents": CLAUDE_ONLY,
             "title": "Wait with run_in_background or Monitor, never 'sleep N; cmd'",
             "text": "Never chain `sleep N` before a command to wait. Start long commands with run_in_background and wait "
                     "for the completion notice, or use Monitor with `until <check>; do sleep 2; done`.",
             "text_ja": "待つためにコマンドの前に `sleep N` をつなげない。長いコマンドは run_in_background "
                        "で始めて完了の通知を待つか、Monitor で `until <check>; do sleep 2; done` を使う。"},
        ],
    },
    {
        "id": "timeout-missing",
        "name": "GNU 'timeout' is not installed (macOS), so wrapped commands exit 127",
        "category": "environment",
        "noise": False,
        "error_patterns": [r"command not found: timeout", r"timeout: command not found",
                           {"text": r"exit code 127|exited with code 127", "target": r"(^|[;&|(]\s*)timeout\s+\d"}],
        "friction_patterns": [r"`?timeout`?.{0,30}(command )?(not found|missing|unavailable|isn't available|absent|exit 127)",
                              r"command not found: timeout", r"\bgtimeout\b",
                              r"timeout`?\s?(コマンド)?(が|は)?(見つから|存在しな|インストールされていな|使えな|な[いく])"],
        "fixes": [
            {"kind": "environment", "scope": "user", "agents": ["all"],
             "title": "Install GNU coreutils so 'timeout' exists",
             "text": "brew install coreutils && echo 'export PATH=\"$(brew --prefix)/opt/coreutils/libexec/gnubin:$PATH\"' "
                     ">> ~/.zshenv"},
            {"kind": "instruction", "scope": "user", "agents": CLAUDE_ONLY,
             "title": "Bound commands with the Bash timeout parameter, not 'timeout'",
             "text": "This Mac has no `timeout` command: bound a command with the Bash tool's timeout parameter or "
                     "run_in_background, or the tool's own --timeout flag.",
             "text_ja": "この Mac には `timeout` コマンドがない。コマンドの実行時間は Bash ツールの timeout "
                        "パラメーターか run_in_background、またはそのツール自身の --timeout フラグで区切る。"},
        ],
    },
    {
        "id": "bare-python-modules",
        "name": "Ad-hoc Python on the bare system interpreter: missing modules, no 'python'",
        "category": "environment",
        "noise": False,
        "error_patterns": [{"text": r"modulenotfounderror: no module named",  # not inside the project's own env
                            "target": r"^(?![\s\S]*\b(pytest|uv run|poetry run|\.venv/))"},
                           r"command not found: python\b", r"python: command not found"],
        "friction_patterns": [r"modulenotfounderror", r"no module named", r"(system|bare|global) python", r"command not found: python\b",
                              r"(python-pptx|openpyxl|pillow|\bpil\b|pptx|requests).{0,40}not (installed|available)",
                              r"not installed in (the )?system python", r"(システム|素|グローバル)の\s?python",
                              r"python.{0,40}(モジュール|パッケージ).{0,15}(見つから|ない|なく|入っていな|インストールされていな)",
                              r"python3?\s?(コマンド)?が(見つから|ない|存在しな)",
                              r"(python-pptx|openpyxl|pillow|pptx|requests).{0,30}(インストールされていな|入っていな|入っておらず)"],
        "fixes": [
            {"kind": "instruction", "scope": "user", "agents": SHELL_AGENTS,
             "title": "Run throwaway Python through uv with its packages",
             "text": "For throwaway Python that needs packages, run `uv run --with <pkg> python3 - <<'PY'`; inside a "
                     "project use `uv run python` or the venv's absolute path. Always `python3`, never `python` or `pip`.",
             "text_ja": "パッケージが必要な使い捨ての Python は `uv run --with <pkg> python3 - <<'PY'` で実行する。"
                        "プロジェクトの中では `uv run python` か venv の絶対パスを使う。常に `python3` を使い、"
                        "`python` や `pip` は使わない。"},
        ],
    },
    {
        "id": "interactive-aliases",
        "name": "Interactive aliases (cp -i, rm -i) hang agent commands or silently skip overwrites",
        "category": "environment",
        "noise": False,
        "error_patterns": [r"overwrite .{1,300}\? \(y/n", r"not overwritten", r"remove .{1,300}\? $"],
        "friction_patterns": [r"\bcp -i\b", r"\brm -i\b", r"\bmv -i\b", r"alias(ed)? .{0,20}\b(cp|rm|mv)\b",
                              r"\b(cp|rm|mv)`? .{0,10}alias",
                              r"interactive (prompt|confirmation|overwrite)", r"not overwritten", r"エイリアス",
                              r"上書き.{0,20}(確認|されなかった|されず|スキップ)",
                              r"対話(的な|式の)?(確認|プロンプト).{0,30}(cp|rm|mv|上書き|削除|止ま)"],
        "fixes": [
            {"kind": "environment", "scope": "user", "agents": ["all"],
             "title": "Keep interactive cp/rm/mv aliases out of agent shells",
             "text": "Wrap the aliases in your zsh startup file: if [[ -z \"$CLAUDECODE$CODEX_SANDBOX\" ]]; then alias "
                     "cp='cp -i'; alias rm='rm -i'; alias mv='mv -i'; fi"},
            {"kind": "instruction", "scope": "user", "agents": SHELL_AGENTS,
             "title": "Bypass interactive aliases with 'command'",
             "text": "cp, rm and mv are aliased to interactive versions: use `command cp -f`, `command rm -f` and "
                     "`command mv -f` in shell commands.",
             "text_ja": "cp、rm、mv は対話式の版にエイリアスされている。シェルのコマンドでは `command cp -f`、"
                        "`command rm -f`、`command mv -f` を使う。"},
        ],
    },
    {
        "id": "stale-dev-server",
        "name": "Stale or orphaned dev servers: ports already taken, old code served during live checks",
        "category": "environment",
        "noise": False,
        "error_patterns": [r"address already in use", r"eaddrinuse", r"port \d+ is (already )?in use", r"port \d+ already in use"],
        "friction_patterns": [r"address already in use", r"eaddrinuse", r"port \d*\s*(is |was )?(already )?(in use|taken|occupied)",
                              r"stale (\w+[ /-]){0,2}(server|process|backend|instance|sidecar)s?\b",
                              r"already[- ]running (\w+ )?(backend|server|process)", r"orphan(ed)? .{0,30}(server|process)",
                              r"(served|serving) (a )?(stale|old|cached) ", r"(old|stale) (code|build|bundle|assets?) .{0,20}served",
                              r"port :?\d{4,5}.{0,60}(unrelated|another|other|different) (app|project|session)",
                              r"port :?\d{4,5} (collision|conflict)", r"(dev )?server was actually serving",
                              r"ポート.{0,20}(使用中|競合)", r"ポート.{0,20}(既に|すでに).{0,10}(使われ|使用され|占有)",
                              r"アドレスは?(既に|すでに)使用", r"(古い|以前の|残っていた|起動したままの)(開発)?サーバ",
                              r"サーバー?が.{0,20}(古い|以前の)(コード|ビルド)", r"(孤立|ゾンビ).{0,10}(プロセス|サーバ)"],
        "fixes": [
            {"kind": "instruction", "scope": "user", "agents": SHELL_AGENTS,
             "title": "Check who owns a port before a live check",
             "text": "Before a live check, run `lsof -nP -iTCP:<port> -sTCP:LISTEN` and confirm the process is a server you "
                     "started in this session. Start servers on a free port, restart them after backend or .env edits, "
                     "and stop the ones you started before you finish.",
             "text_ja": "動作確認の前に `lsof -nP -iTCP:<port> -sTCP:LISTEN` を実行し、"
                        "そのプロセスがこのセッションで起動したサーバーか確かめる。"
                        "サーバーは空いているポートで起動し、バックエンドや .env を編集したら再起動し、"
                        "終える前に自分で起動したものを止める。"},
        ],
    },
    {
        "id": "cwd-drift",
        "name": "Relative 'cd sub && ...' run from the wrong directory (the shell's cwd persists)",
        "category": "tool-misuse",
        "noise": False,
        "error_patterns": [r"cd:\d*:? ?no such file or directory", r"\bcd: no such file or directory",
                           r"cd: .{1,200}: no such file or directory"],
        "friction_patterns": [r"\bcwd\b", r"wrong (working )?directory", r"working directory (did not|didn't) persist",
                              r"cd (backend|frontend|src)\b.{0,40}(fail|already)", r"no such file or directory: (backend|frontend)",
                              r"relative `?cd\b", r"relative[- ]path (issue|confusion|problem)s?", r"カレントディレクトリ",
                              r"作業ディレクトリ.{0,30}(違|ずれ|間違|戻|リセット|保持|残)",
                              r"(別の|違う|誤った|間違った)ディレクトリ(から|で)", r"相対(パス|的な ?cd).{0,30}(失敗|間違|ずれ)"],
        "fixes": [
            {"kind": "instruction", "scope": "user", "agents": SHELL_AGENTS,
             "title": "Use absolute paths instead of a relative cd",
             "text": "The shell's working directory persists between commands: use absolute paths or directory flags "
                     "(`cd /abs/path && ...`, `git -C`, `uv --directory`, `pnpm -C`), never a relative `cd sub &&`.",
             "text_ja": "シェルの作業ディレクトリはコマンドの間で保たれる。"
                        "絶対パスかディレクトリを指定するフラグ（`cd /abs/path && ...`、`git -C`、`uv --directory`、"
                        "`pnpm -C`）を使い、相対パスの `cd sub &&` は使わない。"},
        ],
    },
    {
        "id": "auto-mode-retry",
        "name": "An action the auto-mode classifier denied is retried or rephrased",
        "category": "permissions",
        "noise": False,
        "needs_repeat": True,
        "error_patterns": [r"denied by the claude code auto mode classifier", r"auto mode classifier"],
        "friction_patterns": [r"classifier.{0,80}(repeated|again|twice|retr|rephras|multiple)",
                              r"(repeated|again|twice|multiple).{0,60}classifier",
                              r"auto[- ]mode.{0,60}(blocked|denied).{0,60}(again|twice|repeated)",
                              r"(分類器|クラシファイア|classifier).{0,60}(再試行|繰り返|何度も|もう一度|言い換え|再度)",
                              r"(再試行|繰り返|何度も|再度).{0,60}(分類器|クラシファイア)",
                              r"オートモード.{0,60}(拒否|ブロック).{0,60}(再|繰り返|何度)"],
        "fixes": [
            {"kind": "instruction", "scope": "user", "agents": CLAUDE_ONLY,
             "title": "Stop and ask when the auto-mode classifier denies an action",
             "text": "When the auto-mode classifier denies an action, do not retry it, rephrase it or wrap it in a script: "
                     "stop and ask me to run or approve the exact command.",
             "text_ja": "オートモードの分類器が操作を拒否したら、再試行も言い換えもスクリプトで包むこともしない。"
                        "止まって、そのコマンドをそのまま私に実行か承認してもらう。"},
        ],
    },
    {
        "id": "bash-timeout",
        "name": "Long or unbounded commands hit the shell tool's timeout",
        "category": "tool-misuse",
        "noise": False,
        "error_patterns": [r"command timed out after"],
        "friction_patterns": [r"timed out (at|after) \d+ ?(s|sec|seconds|m|min|minutes)\b", r"(hit|exceeded) (the|its) .{0,25}timeout",
                              r"\b\d+ ?(s|sec|seconds|-?minutes?|min) (background )?(command )?timeout",
                              r"(bash|background|shell) (commands?|tasks?).{0,30}timed out", r"\bfind\b.{0,40}timed out",
                              r"(bash|シェル|バックグラウンド|コマンド|find).{0,30}タイムアウト", r"\d+ ?(秒|分).{0,15}タイムアウト",
                              r"タイムアウト(の)?(上限|制限)に(達|かか)"],
        "fixes": [
            {"kind": "instruction", "scope": "user", "agents": CLAUDE_ONLY,
             "title": "Background anything that may run longer than a minute",
             "text": "Run anything that may exceed 60 seconds with run_in_background and have it write results to a file "
                     "as it goes. Never `find /` or `find ~`; scope searches to the project or use `mdfind -name`.",
             "text_ja": "60 秒を超えそうなものは run_in_background で実行し、結果を逐次ファイルに書かせる。`find /` "
                        "や `find ~` は使わず、検索はプロジェクトの中に絞るか `mdfind -name` を使う。"},
        ],
    },
    {
        "id": "concurrent-sessions",
        "name": "Several agent sessions working in the same tree at once",
        "category": "agent-behaviour",
        "noise": False,
        "error_patterns": [],  # detected from overlapping sessions plus contention errors (see _concurrency_hits)
        "friction_patterns": [r"(concurrent|parallel|another|other|second) (claude |codex |agent )?(code )?sessions?",
                              r"sessions? (running|editing|working) (in parallel|concurrently|at the same time)",
                              r"git add (-a|\.).{0,60}(swept|picked up|included).{0,40}(other|another|unrelated)",
                              r"別の?セッション", r"並行(して|の)?セッション", r"(他|ほか)の(エージェントの?)?セッション",
                              r"(並行|並列|同時)(に|して)?(動|実行|作業)(いて|して|中).{0,10}(セッション|エージェント)",
                              r"git add (-a|\.).{0,60}(他|ほか|別|無関係)"],
        "fixes": [
            {"kind": "instruction", "scope": "project", "agents": SHELL_AGENTS,
             "title": "Assume other agent sessions may share this tree",
             "text": "Other agent sessions may be editing this working tree: stage explicit paths (never `git add -A` or "
                     "`git add .`), re-read files right before editing them, and use a separate git worktree for "
                     "parallel tasks.",
             "text_ja": "ほかのエージェントのセッションがこの作業ツリーを編集していることがある。"
                        "ステージするパスは明示し（`git add -A` や `git add .` は使わない）、"
                        "編集の直前にファイルを読み直し、並行する作業には別の git worktree を使う。"},
        ],
    },
    # ---- noise: shown as context, never turned into suggestions
    {
        "id": "expected-test-failures",
        "name": "Tests, linters and type checks failing inside a dev loop",
        "category": "noise",
        "noise": True,
        "error_patterns": [
            {"text": r"[\s\S]", "target": r"(^|[\s;&|/(])(pytest|vitest|jest|tsc|ruff|mypy|pyright|eslint|biome|prettier|"
                                          r"cargo (test|check|clippy)|go (test|vet)|"
                                          r"(npm|pnpm|yarn|bun)( run)? (test|lint|build|typecheck|check|e2e)[\w:-]*|"
                                          r"make (check|test|lint|typecheck))\b"},
            r"\b\d+ failed\b", r"error TS\d{4}", r"short test summary info", r"Found \d+ errors?", r"Tests:\s+\d+ failed",
            r"Test Files\s+\d+ failed", r"✖ \d+ problems?",
        ],
        "friction_patterns": [],
        "fixes": [],
    },
    {
        "id": "read-chain-nonzero",
        "name": "Read-only command chains whose last command exited non-zero",
        "category": "noise",
        "noise": True,
        "error_patterns": [],  # decided from the command shape (is_read_chain)
        "friction_patterns": [],
        "fixes": [],
    },
    {
        "id": "provider-platform",
        "name": "Provider and platform failures (overloaded, connection lost, usage limits, expired login)",
        "category": "noise",
        "noise": True,
        "error_patterns": [r"\b529\b", r"overloaded_error", r"\boverloaded\b", r"econnreset", r"usage limit",
                           r"oauth (session |token )?(has )?expired", r"providererror", r"api error: connection"],
        "friction_patterns": [r"\b529\b", r"overloaded", r"econnreset", r"connection (lost|reset|dropped)",
                              r"(usage|session|credit) limits?", r"oauth.{0,30}expired", r"providererror", r"api error",
                              r"(computer|machine|laptop) (went to )?(sleep|asleep)",
                              r"\basleep\b", r"使用(量)?制限", r"過負荷", r"接続が(切れ|リセットされ|失われ)",
                              r"(OAuth|ログイン|認証).{0,20}(期限切れ|切れ)", r"(マシン|Mac|PC|パソコン)が?スリープ"],
        "fixes": [],
    },
    {
        "id": "user-rejected",
        "name": "Tool calls you rejected at the permission prompt (steering, not a failure)",
        "category": "noise",
        "noise": True,
        "error_patterns": [r"the user doesn't want to proceed with this tool use", r"user rejected", r"tool use was rejected"],
        "friction_patterns": [],
        "fixes": [],
    },
]

BY_ID = {c["id"]: c for c in CATALOG}
CONTENTION = ("edit-stale-context", "stale-dev-server", "playwright-browser-in-use")
CONCURRENT_NOTE = "while session {sid} was active here: {text}"


def _compile(entry: dict) -> dict:
    tools = re.compile(entry["tools"], re.I) if entry.get("tools") else None
    errs = []
    for p in entry.get("error_patterns") or []:
        if isinstance(p, str):
            errs.append((re.compile(p, re.I), tools, None))
        else:
            errs.append((re.compile(p["text"], re.I), re.compile(p["tool"], re.I) if p.get("tool") else tools,
                         re.compile(p["target"], re.I) if p.get("target") else None))
    notes = [re.compile(p, re.I) for p in entry.get("friction_patterns") or []]
    return {"errors": errs, "notes": notes}


_COMPILED = {c["id"]: _compile(c) for c in CATALOG}


# ---------------------------------------------------------------- normalizing and matching
_CODEX_WRAPPER = re.compile(
    r"^\s*(Script (completed|failed)|Script error:|Wall time:? [\d.]+ ?seconds?|Output:|Exit code:? \d+|Chunk ID: \w+|"
    r"Process exited with code \d+|Original token count: \d+|Warning: truncated output.*|Total output lines: \d+)\s*$",
    re.I | re.M)
_SCRATCHPAD = re.compile(r"/(private/)?tmp/claude-\d+/[^/\s]+/[0-9a-f-]{36}/scratchpad")
_TMP = re.compile(r"/(private/)?(tmp|var/folders)/[^\s'\"`:]*")
_HOME = re.compile(r"/(Users|home)/[^/\s]+")


def normalize(text: str | None, project_path: str | None = None) -> str:
    """Error or note text with the variable parts removed, so the same failure groups as one signature."""
    if not text:
        return ""
    out = _CODEX_WRAPPER.sub("", text)
    if project_path:
        out = out.replace(project_path.rstrip("/"), "<project>")
    out = _SCRATCHPAD.sub("<scratchpad>", out)
    out = _TMP.sub("<tmp>", out)
    out = _HOME.sub("~", out)
    out = re.sub(r"\d+", "N", out)
    return re.sub(r"\s+", " ", out).strip().lower()


_READ_ONLY = {"cat", "sed", "grep", "rg", "ls", "head", "tail", "wc", "find", "echo", "printf", "nl", "awk", "sort", "uniq",
              "cut", "tr", "file", "stat", "du", "tree", "diff", "jq", "pwd", "which", "true", "test", "[", "basename",
              "dirname", "realpath", "readlink", "column", "less", "fd", "eza", "date", "git"}
_READ_ONLY_GIT = {"status", "diff", "log", "show", "branch", "ls-files", "rev-parse", "remote", "blame", "grep", "describe",
                  "tag", "stash list", "config"}


def is_read_chain(command: str | None) -> bool:
    """A compound read-only command (cat/sed/grep/ls joined by ;, &&, ||, |) whose exit status says little."""
    if not command or "<<" in command or ">" in command.replace("2>&1", "").replace("2>/dev/null", ""):
        return False
    segments = [s.strip() for s in re.split(r"\s*(?:&&|\|\||;|\||\n)\s*", command) if s.strip()]
    if len(segments) < 2:
        return False
    for seg in segments:
        words = seg.split()
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):  # env prefix / assignment
            words = words[1:]
        if not words:
            continue
        prog = words[0].rsplit("/", 1)[-1]
        if prog == "cd":
            continue
        if prog == "sed" and any(w == "-i" or w.startswith("-i") for w in words[1:]):
            return False
        if prog == "git":
            sub = words[1] if len(words) > 1 and not words[1].startswith("-") else (words[3] if len(words) > 3 else "")
            if sub not in _READ_ONLY_GIT:
                return False
            continue
        if prog not in _READ_ONLY:
            return False
    return True


def match_error(text: str | None, tool: str | None = None, target: str | None = None, *,
                include_noise: bool = True) -> list[str]:
    """Catalog cause ids a failed tool result matches. Wasteful causes win: noise only when nothing else matched."""
    text, tool, target = text or "", tool or "", target or ""
    hits = []
    for entry in CATALOG:
        if entry["noise"]:
            continue
        if _error_matches(entry["id"], text, tool, target):
            hits.append(entry["id"])
    if hits or not include_noise:
        return hits
    for cid in ("user-rejected", "provider-platform"):
        if _error_matches(cid, text, tool, target):
            return [cid]
    if is_read_chain(target):
        return ["read-chain-nonzero"]
    if _error_matches("expected-test-failures", text, tool, target):
        return ["expected-test-failures"]
    return []


def _error_matches(cid: str, text: str, tool: str, target: str) -> bool:
    for rx, tool_rx, target_rx in _COMPILED[cid]["errors"]:
        if tool_rx is not None and not tool_rx.search(tool):
            continue
        if target_rx is not None and not target_rx.search(target):
            continue
        if rx.search(text):
            return True
    return False


def match_note(note: str | None) -> list[str]:
    """Every catalog cause a friction note describes (a note often bundles several)."""
    if not note:
        return []
    return [c["id"] for c in CATALOG if any(rx.search(note) for rx in _COMPILED[c["id"]]["notes"])]


# ---------------------------------------------------------------- scanning
def _cutoff(days: int | None, now: datetime) -> str | None:
    if not days:
        return None
    return (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def _sessions(conn: sqlite3.Connection, cutoff: str | None, project: str | None) -> dict[str, dict]:
    sql = ("SELECT id, agent, source, project_path, project_name, started_at, ended_at, "
           "COALESCE(llm_title, title, ai_title) AS title, friction_json FROM sessions WHERE source != 'history'")
    params: list = []
    if cutoff:
        sql += " AND COALESCE(ended_at, started_at) >= ?"
        params.append(cutoff)
    if project:
        sql += " AND project_path = ?"
        params.append(project)
    return {r["id"]: dict(r) for r in conn.execute(sql, params)}


def _failed_results(conn: sqlite3.Connection, sessions: dict, cutoff: str | None) -> list[dict]:
    """Failed tool results, deduped (resumed sessions repeat rows), each with the next result of its session."""
    sql = """
        WITH r AS (
            SELECT session_id, agent_id, MIN(seq) AS seq, ts, tool_use_id, tool_name, is_error, text
            FROM events WHERE kind = 'tool_result' {where}
            GROUP BY session_id, ts, tool_use_id, text
        ), w AS (
            SELECT r.*, LEAD(is_error) OVER win AS next_error, LEAD(text) OVER win AS next_text,
                   LEAD(tool_name) OVER win AS next_tool
            FROM r WINDOW win AS (PARTITION BY session_id, agent_id ORDER BY seq)
        )
        SELECT w.session_id, w.ts, w.tool_use_id, w.tool_name, w.text, w.next_error,
               CASE WHEN w.next_error = 1 THEN w.next_text END AS next_text, w.next_tool
        FROM w WHERE w.is_error = 1
    """
    params: list = []
    where = ""
    if cutoff:
        where = "AND ts >= ?"
        params.append(cutoff)
    out = [dict(r) for r in conn.execute(sql.format(where=where), params) if r["session_id"] in sessions]
    targets: dict[tuple, str] = {}
    for r in conn.execute("SELECT session_id, tool_use_id, name, command, file_path FROM tool_calls "
                          "WHERE is_error = 1 AND tool_use_id IS NOT NULL"):
        targets[(r["session_id"], r["tool_use_id"])] = r["command"] or r["file_path"] or ""
    for r in out:
        r["target"] = targets.get((r["session_id"], r["tool_use_id"]), "")
    return out


def _snippet(text: str, cid: str, tool: str, target: str) -> str:
    for rx, _t, _g in _COMPILED[cid]["errors"]:
        m = rx.search(text or "")
        if m and m.group(0).strip():
            start = max(0, m.start() - 60)
            return one_line(normalize(text[start:m.end() + 100]), 160)
    return one_line(normalize(text), 160)


def _week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _scan(conn: sqlite3.Connection, *, days: int | None = None, project: str | None = None,
          now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    cutoff = _cutoff(days, now)
    sessions = _sessions(conn, cutoff, project)
    # hits[cause] -> list of {session_id, ts, kind: error|note, text, repeat}
    hits: dict[str, list[dict]] = defaultdict(list)

    for r in _failed_results(conn, sessions, cutoff):
        causes = match_error(r["text"], r["tool_name"], r["target"])
        for cid in causes:
            entry = BY_ID[cid]
            repeat = bool(r["next_error"]) and cid in match_error(r["next_text"], r["next_tool"], "", include_noise=False)
            if entry.get("needs_repeat") and not repeat:
                continue
            hits[cid].append({"session_id": r["session_id"], "ts": r["ts"], "kind": "error", "repeat": repeat,
                              "text": _snippet(r["text"], cid, r["tool_name"] or "", r["target"])})

    unmatched: dict[str, list[dict]] = defaultdict(list)
    for sid, s in sessions.items():
        notes = loads(s.get("friction_json"), []) or []
        when = s.get("ended_at") or s.get("started_at")
        for f in notes:
            note = (f.get("note") if isinstance(f, dict) else str(f)) or ""
            causes = match_note(note)
            for cid in causes:
                hits[cid].append({"session_id": sid, "ts": when, "kind": "note", "repeat": False, "text": one_line(note, 240)})
            if not causes and note.strip():
                key = " ".join(normalize(note).split()[:6])
                unmatched[key].append({"session_id": sid, "ts": when, "kind": "note", "repeat": False, "text": one_line(note, 240)})

    hits["concurrent-sessions"].extend(_concurrency_hits(conn, sessions, hits))

    causes = [_summarize(BY_ID[cid], h, sessions, now) for cid, h in hits.items() if h]
    for key, h in unmatched.items():
        if len({x["session_id"] for x in h}) >= OTHER_MIN_SESSIONS and key:
            entry = {"id": "other:" + re.sub(r"[^a-z0-9]+", "-", key).strip("-")[:60], "name": h[0]["text"],
                     "category": "other", "noise": False, "fixes": []}
            causes.append(_summarize(entry, h, sessions, now))
    causes.sort(key=lambda c: (c["noise"], c["category"] == "other", -c["sessions"], -c["occurrences"]))
    return causes


def _concurrency_hits(conn: sqlite3.Connection, sessions: dict, hits: dict) -> list[dict]:
    """Contention errors raised while another session was active in the same project: the tree was shared."""
    out = []
    seen: set[tuple] = set()
    for cid in CONTENTION:
        for h in hits.get(cid, []):
            if h["kind"] != "error":
                continue
            s = sessions.get(h["session_id"]) or {}
            dt = parse_ts(h["ts"])
            if not dt or not s.get("project_path") or s.get("source") != "transcript":
                continue
            lo = (dt - timedelta(minutes=OVERLAP_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
            hi = (dt + timedelta(minutes=OVERLAP_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
            other = conn.execute(
                "SELECT e.session_id FROM events e JOIN sessions o ON o.id = e.session_id "
                "WHERE e.kind = 'tool_use' AND e.ts BETWEEN ? AND ? AND e.session_id != ? AND o.project_path = ? "
                "AND o.source = 'transcript' LIMIT 1", (lo, hi, h["session_id"], s["project_path"])).fetchone()
            if other and (h["session_id"], h["ts"]) not in seen:
                seen.add((h["session_id"], h["ts"]))
                out.append({**h, "repeat": False, "text": CONCURRENT_NOTE.format(sid=other[0][:8], text=h["text"])})
    return out


def _summarize(entry: dict, hits: list[dict], sessions: dict, now: datetime) -> dict:
    sids = {h["session_id"] for h in hits}
    last_by_session: dict[str, str] = {}
    for h in hits:
        if h["ts"] and h["ts"] > last_by_session.get(h["session_id"], ""):
            last_by_session[h["session_id"]] = h["ts"]
    by_project: Counter = Counter()
    names: dict[str, str] = {}
    agents: Counter = Counter()
    for sid in sids:
        s = sessions.get(sid) or {}
        path = s.get("project_path") or ""
        by_project[path] += 1
        names[path] = s.get("project_name") or (path.rstrip("/").rsplit("/", 1)[-1] if path else "")
        agents[s.get("agent") or "claude"] += 1
    dates = sorted(d for d in (parse_ts(h["ts"]) for h in hits) if d)
    last = dates[-1] if dates else None
    weekly_sessions: dict[str, set] = defaultdict(set)
    for h in hits:
        d = parse_ts(h["ts"])
        if d:
            weekly_sessions[_week(d)].add(h["session_id"])
    weeks = [_week(now - timedelta(weeks=i)) for i in range(WEEKS - 1, -1, -1)]
    in_projects = sum(1 for s in sessions.values() if (s.get("project_path") or "") in by_project)
    # examples: prefer readable notes, newest first, one per session
    examples, used = [], set()
    for h in sorted(hits, key=lambda x: (x["kind"] != "note", -(parse_ts(x["ts"]) or now).timestamp())):
        if h["session_id"] in used:
            continue
        used.add(h["session_id"])
        s = sessions.get(h["session_id"]) or {}
        examples.append({"session_id": h["session_id"], "title": s.get("title") or "", "note": h["text"]})
        if len(examples) == 3:
            break
    return {
        "id": entry["id"],
        "name": entry["name"],
        "category": entry["category"],
        "noise": bool(entry["noise"]),
        "sessions": len(sids),
        "occurrences": len(hits),
        "errors": sum(1 for h in hits if h["kind"] == "error"),
        "notes": sum(1 for h in hits if h["kind"] == "note"),
        "immediate_repeats": sum(1 for h in hits if h["repeat"]),
        "projects": [names[p] for p, _n in by_project.most_common() if p],
        "project_paths": [{"path": p, "name": names[p], "sessions": n} for p, n in by_project.most_common() if p],
        "agents": [a for a, _n in agents.most_common()],
        "first_seen": dates[0].strftime("%Y-%m-%d") if dates else None,
        "last_seen": last.strftime("%Y-%m-%d") if last else None,
        "still_happening": bool(last and last >= now - timedelta(days=STILL_HAPPENING_DAYS)),
        "weekly": [{"week": w, "sessions": len(weekly_sessions.get(w, ()))} for w in weeks],
        "examples": examples,
        "rate": round(len(sids) / in_projects, 3) if in_projects else 0.0,
        "fixes": entry.get("fixes") or [],
        "_sessions": sids,
        "_last_by_session": last_by_session,
    }


def scan(conn: sqlite3.Connection, *, days: int | None = None, project: str | None = None,
         now: datetime | None = None) -> list[dict]:
    """Recurring failure causes, wasteful first by sessions affected, then 'other' groups, then noise."""
    out = _scan(conn, days=days, project=project, now=now)
    for c in out:
        _strip(c)
    return out


def _strip(cause: dict) -> dict:
    cause.pop("_sessions", None)
    cause.pop("_last_by_session", None)
    return cause


def noise_summary(conn: sqlite3.Connection, *, days: int | None = None, project: str | None = None,
                  causes: list[dict] | None = None) -> dict:
    """How much noise the report filtered: failed results and notes, and the distinct sessions they came from."""
    raw = _scan(conn, days=days, project=project) if causes is None else causes
    noise = [c for c in raw if c["noise"]]
    sids: set = set()
    for c in noise:
        sids |= c.get("_sessions") or set()
    return {"occurrences": sum(c["occurrences"] for c in noise),
            "sessions": len(sids) if sids else max((c["sessions"] for c in noise), default=0)}


def report(conn: sqlite3.Connection, *, days: int | None = None, project: str | None = None, noise: bool = False) -> dict:
    """Everything the 'What goes wrong' views show: causes (noise only on request), tool error rates, noise counts."""
    raw = _scan(conn, days=days, project=project)
    summary = noise_summary(conn, causes=raw)
    causes = [c for c in raw if noise or not c["noise"]]
    for c in raw:
        _strip(c)
    return {"causes": causes, "tool_errors": tool_error_rates(conn, days=days, project=project), "noise_summary": summary}


def display(cause: dict) -> dict:
    """A cause as the dashboard shows it, in the viewer's language (i18n): catalog names and fix titles, and the note
    Interlatch wrote about overlapping sessions. Notes from sessions are shown as they were written."""
    out = {**cause, "fixes": [{**f, "title": tr(f["title"])} for f in cause.get("fixes") or []],
           "examples": [{**e, "note": display_note(e.get("note"))} for e in cause.get("examples") or []]}
    if cause.get("id") in BY_ID:
        out["name"] = tr(cause["name"])
    return out


def display_note(note: str | None) -> str | None:
    """An example's note in the viewer's language, when it is Interlatch's own (CONCURRENT_NOTE)."""
    m = re.match(r"while session (\S+) was active here: (.*)", note or "", re.S)
    return tr(CONCURRENT_NOTE, sid=m.group(1), text=m.group(2)) if m else note


def tool_error_rates(conn: sqlite3.Connection, days: int | None = None, *, project: str | None = None,
                     limit: int = 15) -> list[dict]:
    """Calls and failures per tool (duplicated rows counted once), the tools with the most failures first."""
    cutoff = _cutoff(days, utcnow())
    sql = ("SELECT name AS tool, COUNT(*) AS calls, SUM(is_error) AS errors FROM ("
           "  SELECT DISTINCT t.session_id, t.ts, t.tool_use_id, t.name, t.is_error FROM tool_calls t "
           "  JOIN sessions s ON s.id = t.session_id WHERE s.source != 'history' {where}"
           ") GROUP BY name HAVING errors > 0 ORDER BY errors DESC, calls DESC LIMIT ?")
    where, params = "", []
    if cutoff:
        where += " AND t.ts >= ?"
        params.append(cutoff)
    if project:
        where += " AND s.project_path = ?"
        params.append(project)
    out = []
    for r in conn.execute(sql.format(where=where), [*params, limit]):
        out.append({"tool": r["tool"], "calls": r["calls"], "errors": r["errors"],
                    "rate": round(r["errors"] / r["calls"], 4) if r["calls"] else 0.0})
    return out


def sessions_since(cause: dict, when: str | None) -> int:
    """Sessions that hit this cause after `when` (did an applied fix help?). Needs a cause from _scan."""
    start = parse_ts(when)
    if not start:
        return 0
    return sum(1 for ts in (cause.get("_last_by_session") or {}).values() if (parse_ts(ts) or start) > start)
