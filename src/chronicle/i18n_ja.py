"""Japanese for the dashboard text the server writes (i18n.tr), keyed by the English template.

Terms follow docs/ja: セッション, ナレッジ, 提案, うまくいかないこと, ノイズ, 適用, 却下, 元に戻す, 同期, キュー. A half-width
space separates Japanese from Latin letters and digits; quoted UI labels use 「」.
"""

JA: dict[str, str] = {
    # ---- what goes wrong: catalog causes (friction.CATALOG names)
    "Playwright MCP rejects screenshot paths outside its allowed roots and blocks file:// URLs":
        "Playwright MCP が許可されたルートの外のスクリーンショットのパスを拒否し、file:// の URL をブロックする",
    "Playwright MCP 'Browser is already in use': another session holds the shared Chrome profile":
        "Playwright MCP の「Browser is already in use」：別のセッションが共有の Chrome プロファイルを使っている",
    "Playwright actions on refs from an outdated snapshot or on ambiguous selectors":
        "古いスナップショットの ref や、あいまいなセレクターで Playwright を操作する",
    "Edits fail on stale context: the file changed since it was read, or the expected lines no longer match":
        "古い文脈で編集が失敗する：読んだあとにファイルが変わった、または想定した行がもう一致しない",
    "zsh aborts a whole command on an unquoted glob that matches nothing ('no matches found')":
        "引用符のない glob が何にも一致せず、zsh がコマンド全体を止める（「no matches found」）",
    "zsh expands a word starting with '=' ('echo ====' fails with '=== not found')":
        "zsh が「=」で始まる語を展開する（「echo ====」が「=== not found」で失敗する）",
    "Bash idioms that zsh treats differently (read-only $status, no word splitting, history modifiers)":
        "zsh では動きが違う bash の書き方（$status は読み取り専用、単語分割がない、履歴修飾子）",
    "Chained 'sleep N; cmd' polling is blocked by Claude Code": "待つための「sleep N; cmd」を Claude Code がブロックする",
    "GNU 'timeout' is not installed (macOS), so wrapped commands exit 127":
        "GNU の「timeout」がインストールされておらず（macOS）、包んだコマンドが 127 で終わる",
    "Ad-hoc Python on the bare system interpreter: missing modules, no 'python'":
        "使い捨ての Python をシステムのインタープリターで実行する：モジュールがない、「python」がない",
    "Interactive aliases (cp -i, rm -i) hang agent commands or silently skip overwrites":
        "対話式のエイリアス（cp -i、rm -i）でエージェントのコマンドが止まる、または上書きが黙って飛ばされる",
    "Stale or orphaned dev servers: ports already taken, old code served during live checks":
        "古いまま、または取り残された開発サーバー：ポートがすでに使われている、動作確認で古いコードが返る",
    "Relative 'cd sub && ...' run from the wrong directory (the shell's cwd persists)":
        "相対パスの「cd sub && ...」が違うディレクトリから実行される（シェルの作業ディレクトリは保たれる）",
    "An action the auto-mode classifier denied is retried or rephrased": "オートモードの分類器が拒否した操作を再試行する、または言い換える",
    "Long or unbounded commands hit the shell tool's timeout": "長い、または終わりのないコマンドがシェルツールの制限時間に達する",
    "Several agent sessions working in the same tree at once": "複数のエージェントのセッションが同時に同じツリーで作業している",
    "Tests, linters and type checks failing inside a dev loop": "開発ループの中で失敗するテスト、リンター、型チェック",
    "Read-only command chains whose last command exited non-zero": "最後のコマンドが 0 以外で終わった読み取り専用のコマンドの連鎖",
    "Provider and platform failures (overloaded, connection lost, usage limits, expired login)":
        "提供元やプラットフォームの障害（過負荷、接続切れ、使用量の上限、ログインの期限切れ）",
    "Tool calls you rejected at the permission prompt (steering, not a failure)":
        "許可の確認であなたが断ったツール呼び出し（失敗ではなく、あなたの判断）",
    "while session {sid} was active here: {text}": "セッション {sid} がここで動いていた間：{text}",

    # ---- catalog fixes (titles; the lines themselves are friction.CATALOG text_ja)
    "Give Playwright MCP its own output directory and an isolated browser profile":
        "Playwright MCP に専用の出力ディレクトリと分離したブラウザープロファイルを与える",
    "Save Playwright screenshots by bare filename; preview over http, not file://":
        "Playwright のスクリーンショットはファイル名だけで保存し、file:// ではなく http でプレビューする",
    "Run Playwright MCP with --isolated so parallel sessions get their own browser":
        "Playwright MCP を --isolated で動かし、並行するセッションにそれぞれのブラウザーを持たせる",
    "Stop on 'Browser is already in use' instead of retrying": "「Browser is already in use」では再試行せずに止まる",
    "Take a fresh Playwright snapshot before using refs": "ref を使う前に Playwright のスナップショットを取り直す",
    "Re-read the exact lines right before every edit": "編集の直前に該当行を読み直す",
    "Let unmatched globs pass through in zsh, as bash does": "zsh でも bash と同じく、一致しない glob をそのまま渡す",
    "Quote every glob: the shell is zsh": "glob はすべて引用符で囲む：シェルは zsh",
    "Turn off zsh's '=command' expansion": "zsh の「=command」展開をオフにする",
    "Quote separator lines in zsh": "zsh では区切り線を引用符で囲む",
    "Write zsh, not bash, in shell commands": "シェルのコマンドは bash ではなく zsh として書く",
    "Wait with run_in_background or Monitor, never 'sleep N; cmd'": "「sleep N; cmd」ではなく run_in_background か Monitor で待つ",
    "Install GNU coreutils so 'timeout' exists": "「timeout」を使えるように GNU coreutils をインストールする",
    "Bound commands with the Bash timeout parameter, not 'timeout'":
        "コマンドの時間は「timeout」ではなく Bash ツールの timeout パラメーターで区切る",
    "Run throwaway Python through uv with its packages": "使い捨ての Python は必要なパッケージと一緒に uv で実行する",
    "Keep interactive cp/rm/mv aliases out of agent shells": "対話式の cp/rm/mv エイリアスをエージェントのシェルで使わない",
    "Bypass interactive aliases with 'command'": "対話式のエイリアスは「command」で回避する",
    "Check who owns a port before a live check": "動作確認の前にポートの持ち主を確認する",
    "Use absolute paths instead of a relative cd": "相対パスの cd ではなく絶対パスを使う",
    "Stop and ask when the auto-mode classifier denies an action": "オートモードの分類器が操作を拒否したら、止まって尋ねる",
    "Background anything that may run longer than a minute": "1 分を超えそうなものはバックグラウンドで実行する",
    "Assume other agent sessions may share this tree": "ほかのエージェントのセッションも同じツリーを使っている前提で動く",

    # ---- suggestions: evidence lines and refusals
    "seen in {n} sessions": "{n} セッションで発生",
    "seen in {n} sessions across {p} projects": "{p} プロジェクトの {n} セッションで発生",
    "still happening": "まだ起きている",
    "not seen lately": "最近は起きていない",
    "last {date}": "最終 {date}",
    "{n} in this project": "このプロジェクトで {n}",
    "confirmed in {n} sessions across {p} projects": "{p} プロジェクトの {n} セッションで確認",
    "user level": "ユーザーレベル",
    "no such suggestion": "その提案はありません",
    "move to user or project": "移す先は user か project です",
    "only an instruction line waiting for review can move": "移せるのは確認待ちの指示の行だけです",
    "it is already there": "すでにそこにあります",
    'expected: mcpServers.<name>.args += ["--flag", ...]': '次の形で書いてください：mcpServers.<name>.args += ["--flag", ...]',
    "the arguments must be a JSON list of strings": "引数は文字列の JSON リストにしてください",
    "Interlatch only changes the Playwright MCP server's arguments": "Interlatch が変更するのは Playwright MCP サーバーの引数だけです",
    "Interlatch does not set {arg!r}; only {flags}": "Interlatch は {arg!r} を設定しません。設定できるのは {flags} だけです",
    "{flag} needs an absolute path": "{flag} には絶対パスが必要です",
    "{path} is not a JSON object": "{path} は JSON オブジェクトではありません",
    "no MCP server named {name!r} in {path}": "{path} に {name!r} という MCP サーバーはありません",
    "the project {path} no longer exists": "プロジェクト {path} はもうありません",
    "{kind} suggestions are not written by Interlatch": "{kind} の提案は Interlatch が書き込むものではありません",
    "Interlatch does not run setup steps: run the command yourself, then mark it done":
        "Interlatch はセットアップ手順を実行しません。コマンドを自分で実行してから、完了にしてください",
    "empty text": "テキストが空です",
    "this one is applied: undo it first, so its line leaves the file": "この提案は適用済みです。先に元に戻して、行をファイルから取り除いてください",
    "only environment steps are marked done": "完了にできるのはセットアップ手順だけです",
    "malformed interlatch block ({begins} BEGIN and {ends} END markers, expected one BEGIN followed by one END)":
        "Interlatch のブロックが壊れています（BEGIN が {begins} 個、END が {ends} 個。BEGIN 1 個のあとに END 1 個が必要です）",
    "{error} in {path}; fix it by hand": "{path}：{error}。手で直してください",
    "{path} no longer exists": "{path} はもうありません",

    # ---- knowledge trust (ladder): stages and the reasons stored with them
    "wip": "仮",
    "provisional": "1 回のみ",
    "established": "定着",
    "canonical": "確定",
    "confirmed in {n} sessions over {days} days ({when})": "{days} 日間の {n} セッションで確認（{when}）",
    "confirmed in {n} sessions ({when})": "{n} セッションで確認（{when}）",
    "confirmed in {n} sessions": "{n} セッションで確認",
    "pinned by you": "あなたがピン留め",
    "added by you": "あなたが追加",
    "written by the agent's own memory": "エージェント自身のメモリーが記録",
    "one session, low confidence": "1 セッションのみ、確信度は低い",
    "one session": "1 セッションのみ",

    # ---- the analysis queue (worker)
    "ready now": "準備完了",
    "still active": "まだ作業中",
    "retry later": "再試行待ち",
    "project excluded": "除外したプロジェクト",
    "too few prompts": "プロンプトが少なすぎる",
    "before install (backfill off)": "インストール前（backfill オフ）",
    "failed, not retried": "失敗、再試行なし",
    "no error recorded": "エラーの記録なし",
    "too little content": "内容が少なすぎる",
    "excluded project": "除外したプロジェクト",
    "session continued during analysis": "分析中にセッションが続いた",
    "session continued after analysis": "分析のあとにセッションが続いた",
    "history only (transcript deleted before Interlatch)": "履歴のみ（Interlatch を入れる前にトランスクリプトが削除された）",
    "imported {label} chat: not analyzed automatically (Analyze now, or import with --analyze)":
        "取り込んだ {label} のチャット：自動では分析しません（今すぐ分析するか、--analyze を付けて取り込みます）",
    "analysis failed {n}; it will not be retried automatically ({reason})": "分析に {n} 回失敗したため、自動では再試行しません（{reason}）",
    "analysis failed for good; it will not be retried automatically ({reason})":
        "再試行しても解決しない失敗のため、自動では再試行しません（{reason}）",
    "its project is excluded from analysis (sources.exclude_projects)": "プロジェクトが分析の対象から除外されています（sources.exclude_projects）",
    "fewer than {n} prompts (analysis.min_prompts)": "プロンプトが {n} 件未満です（analysis.min_prompts）",
    "it started before Interlatch was installed, and analysis.backfill is off":
        "Interlatch をインストールする前に始まったセッションで、analysis.backfill がオフです",
    "retrying at {when} after a failed attempt ({reason})": "失敗したため、{when} に再試行します（{reason}）",
    "the session may still be going; it is analyzed once idle for {minutes} minutes (around {at})":
        "セッションがまだ続いている可能性があります。{minutes} 分間アイドルになったら分析します（{at} ごろ）",
    "ready, but {block}": "準備完了ですが、{block}",
    "ready: the background agent analyzes it on its next run (every 15 minutes)":
        "準備完了：バックグラウンドの処理が次の実行（15 分ごと）で分析します",
    "ready to re-analyze: the session continued after it was analyzed": "再分析の準備完了：分析のあとにセッションが続きました",
    "analysis is paused until {when} (usage limit); it resumes by itself": "使用量の上限のため、分析は {when} まで一時停止しています。自動で再開します",
    "automatic analysis is off (analysis.auto); analyze it from its page or with `interlatch analyze`":
        "自動分析がオフです（analysis.auto）。セッションのページか `interlatch analyze` で分析してください",
    "not saved: {keys}": "保存されませんでした：{keys}",
    "{reason}, so nothing can be analyzed": "{reason}ため、何も分析できません",
    "{label} (`{cli}`) was not found": "{label}（`{cli}`）が見つかりません",
    "{label}: set its endpoint (providers.{name}.base_url)": "{label}：エンドポイントを設定してください（providers.{name}.base_url）",
    "{label}: choose a model (providers.{name}.model)": "{label}：モデルを選んでください（providers.{name}.model）",
    "{label}: add a Bob API key (BOB_API_KEY); headless runs don't use the app's sign-in": "{label}：Bob の API キー（BOB_API_KEY）を追加してください。ヘッドレス実行ではアプリのサインインは使われません",
    "{label}: add an API key": "{label}：API キーを追加してください",

    # ---- weekly reviews
    "exists": "作成済み",
    "fewer than 2 analyzed sessions": "分析済みのセッションが 2 件未満",
    "{n} sessions still waiting for analysis": "{n} セッションが分析待ち",
    "(no project)": "（プロジェクトなし）",

    # ---- dashboard API (server.py, session export, glossary)
    "Main thread": "メインスレッド",
    "Global playbook": "グローバルプレイブック",
    "Everywhere": "全プロジェクト共通",
    "all projects": "すべてのプロジェクト",
    "unknown analysis backend {backend!r}": "{backend!r} という分析のバックエンドはありません",
    "unknown language {lang!r}": "{lang!r} という言語は選べません",
    "Wait for {job} to finish first": "先に {job} が終わるのを待ってください",
    "The menu-bar icon is for a command-line install on macOS": "メニューバーアイコンは macOS のコマンドライン版で使えます",
    "The menu-bar icon needs the app extra: {command}": "メニューバーアイコンには app エクストラが必要です：{command}",
    "installing the app extra…": "app エクストラをインストール中…",
    "The menu-bar icon is on; the dashboard is restarting": "メニューバーアイコンをオンにしました。ダッシュボードを再起動しています",
    "The menu-bar icon is off; the dashboard is restarting": "メニューバーアイコンをオフにしました。ダッシュボードを再起動しています",
    "The menu-bar icon is off": "メニューバーアイコンをオフにしました",
    "The menu-bar icon is on": "メニューバーアイコンをオンにしました",
    "The menu-bar icon is on; it shows while the dashboard runs at login":
        "メニューバーアイコンをオンにしました。ログイン時に起動するダッシュボードが動いている間に表示されます",
    "Nothing in the selection can be analyzed": "選んだ中に分析できるものがありません",
    "A batch analysis is already running": "まとめての分析がすでに実行中です",
    "unknown queue": "そのキューはありません",
    "analysis runs on the hub ({hub})": "分析はハブ ({hub}) で行われます",
    "Nothing in the queue to analyze": "キューに分析するものがありません",
    "The queue is already being analyzed": "キューはすでに分析中です",
    "no such sessions": "そのセッションはありません",
    "unknown format {fmt!r}": "{fmt!r} という形式はありません",
    "no sessions to export": "エクスポートするセッションがありません",
    "No original transcript to export: claude.ai chats share one export file and prompt-history sessions have none. "
    "Export as Markdown or JSON instead.":
        "エクスポートする元のトランスクリプトがありません。claude.ai のチャットは 1 つのエクスポートファイルを共有し、"
        "プロンプト履歴のセッションにはトランスクリプトがありません。Markdown か JSON でエクスポートしてください。",
    "This session has no original transcript of its own (claude.ai chats and prompt-history sessions); export it as "
    "Markdown or JSON instead.":
        "このセッションには自身の元のトランスクリプトがありません（claude.ai のチャットとプロンプト履歴のセッション）。"
        "Markdown か JSON でエクスポートしてください。",
    "None of these sessions has an original transcript of its own; export them as Markdown or JSON.":
        "どのセッションにも自身の元のトランスクリプトがありません。Markdown か JSON でエクスポートしてください。",
    "forbidden host": "許可されていないホストです",
    "this Tailscale login is not allowed ([server] allowed_users)": "この Tailscale のログインは許可されていません（[server] allowed_users）",
    "forbidden": "許可されていません",
    "not found": "見つかりません",
    "bad json": "JSON が正しくありません",
    "empty upload": "アップロードが空です",
    "upload cut off": "アップロードが途中で切れました",
    "unknown connector": "そのソースはありません",
    "no architecture sketch for this project": "このプロジェクトにはアーキテクチャの図がありません",

    # ---- updates
    "Running from a source checkout: git pull to update.": "ソースのチェックアウトから実行しています。更新するには git pull してください。",
    "Matches the checkout.": "チェックアウトと一致しています。",
    "Installed from {source}; updating reinstalls from there.": "{source} からインストールされています。更新するとそこから再インストールします。",
    "Download the new version and drag it into Applications.": "新しいバージョンをダウンロードし、アプリケーションフォルダーにドラッグしてください。",
    "Pull the new image and recreate the container: docker compose pull && docker compose up -d":
        "新しいイメージを取得してコンテナーを作り直してください: docker compose pull && docker compose up -d",
    "Could not reach PyPI ({error})": "PyPI に接続できませんでした（{error}）",

    # ---- sources (connectors) and what connecting one did
    "not recording": "記録していません",
    "background sync every 15 min": "15 分ごとのバックグラウンド同期",
    "SessionEnd hook + background sync every 15 min": "SessionEnd フックと 15 分ごとのバックグラウンド同期",
    "task list and diffs every sync, through the codex CLI": "同期のたびに codex CLI でタスクの一覧と差分を取得",
    "Transcripts": "トランスクリプト",
    "Rollouts": "ロールアウト",
    "Recording": "記録",
    "SessionEnd hook": "SessionEnd フック",
    "Session-end hook": "セッション終了のフック",
    "Knowledge injection": "ナレッジの注入",
    "Background sync": "バックグラウンド同期",
    "MCP server in Claude Code": "Claude Code の MCP サーバー",
    "MCP server in Codex": "Codex の MCP サーバー",
    "MCP server in VS Code": "VS Code の MCP サーバー",
    "MCP server in Copilot CLI": "Copilot CLI の MCP サーバー",
    "MCP server in Bob": "Bob の MCP サーバー",
    "MCP server in Antigravity": "Antigravity の MCP サーバー",
    "Claude sessions imported by Codex": "Codex が取り込んだ Claude のセッション",
    "Task list": "タスクの一覧",
    "Conversation": "会話",
    "Copilot agent sessions": "Copilot のエージェントセッション",
    "Copilot Chat in VS Code": "VS Code の Copilot Chat",
    "Task database": "タスクのデータベース",
    "IDE chat history": "IDE のチャット履歴",
    "Conversation logs": "会話のログ",
    "Conversations without a log": "ログのない会話",
    "in the cloud": "クラウド上",
    "{where} · {n} on disk": "{where} · ディスク上に {n} 件",
    "scanned every sync": "同期のたびに読み込み",
    "not scanned (disconnected)": "読み込んでいません（接続を解除済み）",
    "not scanned (connect to start)": "読み込んでいません（接続すると始まります）",
    "checked every sync": "同期のたびに確認",
    "not checked (connect to start)": "確認していません（接続すると始まります）",
    "archives and analyzes each session as it ends": "セッションが終わるたびに保存して分析します",
    "Claude can search your sessions and knowledge": "Claude があなたのセッションとナレッジを検索できます",
    "Codex can search your sessions and knowledge (Claude's too)": "Codex があなたのセッションとナレッジ（Claude のものも）を検索できます",
    "Copilot Chat can search your sessions and knowledge": "Copilot Chat があなたのセッションとナレッジを検索できます",
    "SessionStart hook adds the project's knowledge base to new sessions":
        "SessionStart フックが新しいセッションにプロジェクトのナレッジベースを加えます",
    "{manager}, every 15 minutes": "{manager}、15 分ごと",
    "{n} older sessions recovered (prompt history / Codex imports)": "以前のセッションを {n} 件復元しました（プロンプト履歴、Codex の取り込み）",
    "not available: Codex allows one notify program and it is used by {owner}; sessions are picked up by the 15-minute sync":
        "使えません。Codex の notify プログラムは 1 つだけで、{owner} が使っています。セッションは 15 分ごとの同期で取り込みます",
    "Codex has no session-end hook; sessions are picked up by the 15-minute sync":
        "Codex にはセッション終了のフックがありません。セッションは 15 分ごとの同期で取り込みます",
    "{n} in Codex's import registry; {recovered} recovered into the vault": "Codex の取り込み記録に {n} 件、うち {recovered} 件をアーカイブに復元",
    " (the rest are duplicates of transcripts already recorded)": "（残りは記録済みのトランスクリプトと重複）",
    "{n} tasks at the last sync ({when})": "前回の同期で {n} 件のタスク（{when}）",
    "failed at the last sync ({when}): {error}": "前回の同期で失敗しました（{when}）：{error}",
    "not listed yet": "まだ一覧を取得していません",
    "lists tasks with your Codex login (`codex login`)": "あなたの Codex のログイン（`codex login`）でタスクを一覧にします",
    "not found: install Codex and run `codex login`": "見つかりません。Codex をインストールして `codex login` を実行してください",
    "not available from the CLI: each task is recorded with its title, repository, changed files and diff":
        "CLI からは取得できません。各タスクはタイトル、リポジトリ、変更したファイル、差分とともに記録されます",
    "{where} · {n} (Copilot CLI and VS Code agent host)": "{where} · {n} 件（Copilot CLI と VS Code のエージェントホスト）",
    "{n} chat logs in VS Code workspace storage (empty chat panels are skipped)":
        "VS Code のワークスペースストレージに {n} 件のチャットログ（空のチャットパネルは除外）",
    "not used: sessions are picked up by the 15-minute sync": "使いません。セッションは 15 分ごとの同期で取り込みます",
    "Copilot is billed per seat; costs shown are API list-price estimates where token splits are known":
        "Copilot はシート単位の課金です。表示する費用は、トークンの内訳が分かる場合の API 定価による見積もりです",
    "{where} · {n} tasks, {messages} with messages": "{where} · {n} 件のタスク、うちメッセージ付き {messages} 件",
    "Bob IDE keeps no conversation files on this Mac; only tasks in its task database are recorded":
        "Bob IDE はこの Mac に会話のファイルを残さないため、タスクのデータベースにあるタスクだけを記録します",
    "{where} · {n} conversations with a step log": "{where} · ステップのログがある会話 {n} 件",
    "{n} kept only in Antigravity's own encrypted store; these cannot be read":
        "{n} 件は Antigravity 独自の暗号化されたストアにしかなく、読めません",
    "none: every conversation has a readable step log": "なし：すべての会話に読めるステップのログがあります",
    "Antigravity records tokens but no prices; sessions on Gemini models show no cost":
        "Antigravity はトークン数を記録しますが料金は記録しないため、Gemini モデルのセッションには費用が表示されません",
    "recording {where}": "{where} を記録します",
    "recording Codex Cloud tasks": "Codex Cloud のタスクを記録します",
    "stopped recording {label} (recorded sessions are kept)": "{label} の記録を止めました（記録済みのセッションは残ります）",
    "stopped recording Codex Cloud (recorded tasks are kept)": "Codex Cloud の記録を止めました（記録済みのタスクは残ります）",
    "codex CLI not found: install Codex and run `codex login`": "codex CLI が見つかりません。Codex をインストールして `codex login` を実行してください",
    "MCP registration skipped: codex CLI not found": "codex CLI が見つからないため、MCP の登録を省きました",
    "registered MCP server 'interlatch' in Codex": "Codex に MCP サーバー「interlatch」を登録しました",
    "Codex MCP registration failed: {error}": "Codex への MCP の登録に失敗しました：{error}",
    "removed MCP server 'interlatch' from Codex": "Codex から MCP サーバー「interlatch」を削除しました",
    "Codex MCP removal failed: {error}": "Codex からの MCP の削除に失敗しました：{error}",
    "{label}: {path} is not plain JSON; add the 'interlatch' MCP server by hand":
        "{label}：{path} は素の JSON ではありません。MCP サーバー「interlatch」は手で追加してください",
    "{label}: unexpected {path} format; left unchanged": "{label}：{path} の形式が想定と違うため、変更していません",
    "{label}: no interlatch MCP server to remove": "{label}：削除する MCP サーバー「interlatch」がありません",
    "{label}: interlatch MCP server already registered": "{label}：MCP サーバー「interlatch」は登録済みです",
    "{label}: removed MCP server 'interlatch' ({path})": "{label}：MCP サーバー「interlatch」を削除しました（{path}）",
    "{label}: registered MCP server 'interlatch' ({path})": "{label}：MCP サーバー「interlatch」を登録しました（{path}）",
    "{label} not found on this computer; nothing changed": "このコンピューターに {label} が見つかりません。何も変更していません",
    "; restart {label} to load it": "。読み込むには {label} を再起動してください",
    "registered MCP server '{name}' (user scope)": "MCP サーバー「{name}」を登録しました（ユーザースコープ）",
    "removed MCP server '{name}'": "MCP サーバー「{name}」を削除しました",
    "MCP registration skipped: claude CLI not found": "claude CLI が見つからないため、MCP の登録を省きました",
    "MCP registration failed: {error}": "MCP の登録に失敗しました：{error}",
    "MCP removal failed: {error}": "MCP の削除に失敗しました：{error}",
    "removed {n} hook(s) from {path}": "{path} からフックを {n} 個削除しました",
    "backed up {path} -> {backup}": "{path} を {backup} にバックアップしました",
    # ---- artifacts: opening a file
    "this file can no longer be opened": "このファイルはもう開けません",
    "files open only on the computer Interlatch runs on": "ファイルは Interlatch が動いているコンピューターでだけ開けます",
    # ---- Devices: what this computer sends, the team store
    "change this on the computer itself, not from another device": "ほかのデバイスからではなく、そのコンピューター自身で変更してください",
    "this computer sends to a hub: the team store is set up on the hub": "このコンピューターはハブに送っています。チームストアはハブで設定します",
    "this computer has not joined a hub": "このコンピューターはハブに参加していません",
    "all_folders must be true or false": "all_folders は true か false で指定します",
    "unknown share mode {share!r}": "不明な共有方法 {share!r}",
    "the database server's address is missing": "データベースサーバーのアドレスがありません",
    "the database name is missing": "データベース名がありません",
    "the user name is missing": "ユーザー名がありません",
    "the port must be a number from 1 to 65535": "ポートは 1 から 65535 までの数字にしてください",
    "the password is missing": "パスワードがありません",
    # ---- Settings › Storage: the mirror in Postgres
    "the schema name can have only letters, digits and _, and can't start with a digit":
        "スキーマ名に使えるのは英字・数字・_ だけで、数字では始められません",
    "what the mirror holds must be \"knowledge\" or \"everything\"": "ミラーに入れる内容は \"knowledge\" か \"everything\" で指定します",
    "that file is not on this computer": "そのファイルはこのコンピューターにありません",
    "that kind of file is not opened from here": "その種類のファイルはここからは開きません",
    "opening files is not supported on this system": "このシステムではファイルを開けません",
    "could not open it: {error}": "開けませんでした：{error}",
    "not available": "利用できません",
    # ---- people on a hub: signing in, roles, invites
    "sign in to this hub": "このハブにサインインしてください",
    "this dashboard has no people yet, so only this computer may open it. At the hub, run `interlatch hub invite <your "
    "name> --email <email> --role admin` and open the invite link it prints":
        "このダッシュボードにはまだ人が登録されていないため、開けるのはこのコンピューター自身だけです。ハブで "
        "`interlatch hub invite <名前> --email <メール> --role admin` を実行し、表示される招待リンクを開いてください",
    "you're not on this hub; ask an admin to add you": "あなたはこのハブに登録されていません。管理者に追加を頼んでください",
    "only an admin of this hub can do this": "これができるのはこのハブの管理者だけです",
    "this computer is not a hub": "このコンピューターはハブではありません",
    "Could not sign in": "サインインできませんでした",
    "Ask an admin of this hub for a new invite, or open the hub's dashboard again from your own Interlatch "
    "(Settings › Devices).":
        "このハブの管理者に新しい招待を頼むか、自分の Interlatch（「設定」›「デバイス」）からハブのダッシュボードを開き直してください。",
    "Open the dashboard": "ダッシュボードを開く",
    "no such computer or browser session": "そのコンピューターまたはブラウザーのセッションはありません",
    "this hub has no address set ([hub] address), so these use the address this page was opened at; set it with "
    "`interlatch hub enable --url`":
        "このハブにはアドレスが設定されていない（[hub] address）ため、このページを開いたアドレスを使っています。"
        "`interlatch hub enable --url` で設定してください",
    "a name is needed": "名前が必要です",
    "role must be one of: {roles}": "役割は {roles} のいずれかにしてください",
    "{email} is not an email address": "{email} はメールアドレスではありません",
    "{email} is already on this hub": "{email} はすでにこのハブにいます",
    "no such person": "その人はいません",
    "this is the hub's last admin: make someone else an admin first":
        "このハブの最後の管理者です。先にほかの人を管理者にしてください",
    "you see only some projects on this hub": "このハブでは一部のプロジェクトだけが見えます",
    "projects must be a list of project paths": "プロジェクトはプロジェクトのパスの一覧で指定してください",
    "{project} is not a project on this hub": "{project} はこのハブのプロジェクトではありません",
    "say which projects they see, or every project": "見えるプロジェクトを選ぶか、すべてのプロジェクトを選んでください",
    "pick a project or type its folder": "プロジェクトを選ぶか、フォルダーを入力してください",
    "A project's name is up to 80 characters, without / or \\, and doesn't start with a dot.":
        "プロジェクト名は 80 文字以内で、/ と \\ を含まず、ドットで始まらない名前にしてください。",
    "that code isn't known on this hub": "このハブはそのコードを知りません",
    "too many wrong codes from this address; try again in {seconds} s":
        "このアドレスから間違ったコードが続きました。{seconds} 秒後にもう一度試してください",
    "that code was already used; ask an admin for a new one": "そのコードは使用済みです。管理者に新しいコードを頼んでください",
    "that code has expired; ask an admin for a new one": "そのコードは期限切れです。管理者に新しいコードを頼んでください",
    "that person is no longer on this hub": "その人はもうこのハブにいません",
    "this computer already joined this hub as someone else; an admin removes it from that person first (Team › People)":
        "このコンピューターは別の人としてこのハブに参加済みです。先に管理者がその人からこのコンピューターを外してください（チーム › 利用者）",
    "this hub already knows a computer with this id ({machine}) and can't tell this is it: an admin makes an invite for that computer (`interlatch hub invite <name> --computer {machine}`), and you join with that code":
        "このハブは同じ ID のコンピューター（{machine}）をすでに知っていて、これがそのコンピューターだと確かめられません。管理者がそのコンピューター用の招待を作り（`interlatch hub invite <名前> --computer {machine}`）、そのコードで参加してください",
    "this invite is for another computer ({machine}); join from that one":
        "この招待は別のコンピューター（{machine}）のものです。そのコンピューターから参加してください",
    "this hub doesn't know a computer {machine}": "このハブはコンピューター {machine} を知りません",
    "this computer has not joined a hub (`interlatch hub join`)": "このコンピューターはハブに参加していません（`interlatch hub join`）",
    "the hub sent no sign-in code": "ハブからサインインのコードが届きませんでした",
    "Can't reach the hub: {error}": "ハブにつながりません: {error}",
    "Give the folder's full path, such as /Users/you/code/app.": "/Users/you/code/app のように、フォルダーのフルパスを入力してください。",
    "The hub has no such project, or you don't see it.": "ハブにそのプロジェクトがないか、あなたには見えません。",
    "Which project?": "どのプロジェクトですか？",
    "INTERLATCH_HUB_NAME in the hub's .env sets its name: change it there.":
        "ハブの名前はハブの .env の INTERLATCH_HUB_NAME で決まります。そちらで変更してください。",
    "A hub's name is up to 80 characters.": "ハブの名前は 80 文字以内にしてください。",
    "Give the hub a name.": "ハブに名前を付けてください。",
    "INTERLATCH_HUB_URL in the hub's .env sets its address: change it there.":
        "ハブのアドレスはハブの .env の INTERLATCH_HUB_URL で決まります。そちらで変更してください。",
    "An address is like https://interlatch.example.com: http or https and a host name, with a port if needed.":
        "アドレスは https://interlatch.example.com のように、http か https とホスト名で指定します（必要ならポートも）。",
    "Nothing to change.": "変更するものがありません。",
    "{folder} is not a folder.": "{folder} はフォルダーではありません。",
    "{folder} is in the git repository {remote}, which the hub files under {owner}. A repository belongs to one project, "
    "so its sessions can't go to {name}.":
        "{folder} は git リポジトリ {remote} の中にあり、ハブはこれを {owner} に振り分けています。リポジトリは 1 つのプロジェクト"
        "に属するため、そのセッションを {name} に送ることはできません。",
    "{folder} was not added.": "{folder} は追加されていません。",
    "The hub runs an older version that can't take back what this computer shared. Update the hub first.":
        "ハブのバージョンが古いため、このコンピューターが共有したものを取り消せません。先にハブを更新してください。",
    "Only a computer that shares knowledge can leave a project: one that sends its transcripts sends them all.":
        "プロジェクトから抜けられるのは、ナレッジだけを共有するコンピューターです。トランスクリプトを送るコンピューターはすべてを送ります。",
    # ---- your own groups of projects (groups.py)
    "That group no longer exists.": "そのグループはもうありません。",
    "Give the group a name.": "グループに名前を付けてください。",
    "Keep the name to 60 characters or fewer.": "名前は 60 文字以内にしてください。",
    "There is already a group with that name.": "同じ名前のグループがすでにあります。",
    "Folder rules must be a list of folders.": "フォルダーのルールはフォルダーのリストで指定してください。",
    "A folder rule can't be empty or the whole disk.": "フォルダーのルールを空やディスク全体にすることはできません。",
    "Pick a project to move.": "移動するプロジェクトを選んでください。",
    "This computer doesn't send to a hub.": "このコンピューターはハブに送っていません。",
    "Chronicle is now Interlatch. Updating moves this install from the agents-chronicle package to interlatch; the chronicle command keeps working.": "Chronicle は Interlatch になりました。更新すると、このインストールは agents-chronicle パッケージから interlatch に移ります。chronicle コマンドはそのまま使えます。",
}
