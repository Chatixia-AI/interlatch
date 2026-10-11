# インストール

[← Interlatch](../../README.ja.md) · [ドキュメント一覧](README.md)

Interlatch は macOS で動作し、セッションを分析するものが必要です。ログイン済みの [Claude Code](https://claude.com/claude-code)（`claude`）か [Codex](https://github.com/openai/codex)（`codex`）、Bob API キーを設定した IBM Bob、またはあなた自身のキーで使うモデルプロバイダーの API（[モデルプロバイダー](analysis.md#モデルプロバイダー)）のどれかです。セットアップは Claude Code がインストールされていればそれを使い、なければ Codex を提案します。**Status › Analysis** または `interlatch config set analysis.backend codex` でいつでも切り替えられます。使い方は 2 通りあり、どちらも `~/.interlatch` の同じデータを使うため、併用できます。

Interlatch は以前 Chronicle という名前でした。Chronicle からのアップデートは[Chronicle からの移行](moving-from-chronicle.md)を参照してください。

## デスクトップアプリ

1. [最新リリース](https://github.com/Chatixia-AI/interlatch/releases/latest)から
   `Interlatch-<version>-arm64.dmg` をダウンロードします（Apple シリコン、macOS 13 以降）。
2. 開いて **Interlatch** を「アプリケーション」フォルダにドラッグし、そこから起動します。
3. 初回起動時に **Connect** を選ぶと、Claude Code のセッションの記録が始まります。`SessionEnd` フックと MCP サーバーが
   追加され（`interlatch connect claude` と同じ）、Interlatch がログイン時に起動するようになります。

macOS に「“Interlatch”は開けません」や「開発元を検証できません」と表示された場合、そのリリースは公証されていません：
**システム設定 → プライバシーとセキュリティ** を開き、Interlatch のメッセージの横にある **このまま開く** をクリックして確認してください。
Intel 版はまだありません。Intel Mac ではコマンドラインでインストールしてください。

以降、Interlatch はメニューバーに常駐します。ダッシュボードを専用ウインドウで表示し、15 分ごとのバックグラウンド同期も
アプリ自身が行うため、launchd エージェントは不要です。ウインドウを閉じても動き続けます。ウインドウにはタイトルバーがなく、
サイドバーは macOS ネイティブのガラス（ウインドウの背後をぼかします）で、その上に信号機ボタンが載ります。ウインドウはツールバーか
サイドバー上端の帯をドラッグして動かします。メニューバーアイコンについては[メニューバーアイコン](#メニューバーアイコン)を
参照してください。アプリではそのページがアプリのウインドウで開き、メニューには次の項目が加わります：

| メニュー項目 | |
| --- | --- |
| Open in Browser | ダッシュボードをアプリのウインドウではなくブラウザで開く |
| Connect Claude Code… | Claude Code が未接続のときに表示（初回起動時に *Not Now* を選んだ場合） |
| Open at Login | 接続後はオン。オフにすると、自分で開いたときだけ Interlatch が動きます |
| Install Command-Line Tool | アプリ内蔵の `interlatch` コマンドを `~/.local/bin` にリンク（既に存在する場合は何もしません） |
| Open Data Folder | `~/.interlatch` |

Codex、Copilot、Bob、Antigravity はダッシュボードの **Sources** ページから接続します。フックと MCP の登録は
`~/.interlatch/bin/interlatch` を指しています。これはアプリが起動のたびに書き直す小さなスクリプトなので、
アプリを移動・更新しても壊れません。アプリを終了すると、次に起動するまで同期は止まります。終了で中断された分析は、
次の同期で改めて実行されます。

Interlatch を削除するには、`~/.interlatch/bin/interlatch uninstall` を実行し（フックと MCP
サーバーを削除。データは残ります）、**Open at Login** をオフにしてから、アプリを削除します。

## コマンドライン

```bash
uv tool install --python 3.13 interlatch         # puts `interlatch` on PATH (~/.local/bin)
interlatch install                               # pick the agents to record, import, start the dashboard
```

[uv](https://docs.astral.sh/uv/) が必要です（`pipx install interlatch` でも可）。リポジトリのチェックアウトから
インストールする場合は、その中で `uv tool install --python 3.13 .` を実行します。

`interlatch install`（`interlatch setup` でも可）は次の順にセットアップします：

1. セッションを分析するエージェント（既定は Claude Code）を確認します。見つからず Codex がインストールされていれば、Codex で分析するか尋ねます。どちらもなければ、セッションは記録されますが、どちらかをインストールするまで分析されません。
2. この Mac にあるコーディングエージェント（Claude Code、Codex、GitHub Copilot、IBM Bob、Google Antigravity）と、MCP のみのクライアント
   （Claude Desktop、Cursor、Windsurf、Gemini CLI）を、ディスク上のセッション数とともに一覧にします。
3. 見つかってまだ接続していないものごとに、記録するか（既定は「はい」）、MCP クライアントには Interlatch のツールを
   渡すかを尋ねます。Codex Cloud はオンラインにアクセスするため、Codex の後に尋ね、既定は「いいえ」です。
   Claude Code を断ると `~/.claude` もスキャンしません。
4. 選んだものを `interlatch connect <name>` と同じ方法で接続し（[ソース](sources.md)）、過去のセッションを取り込みます
   （`--no-sync` で省略可。その場合はバックグラウンド同期が取り込みます）。
5. Interlatch をバックグラウンドで、ログイン時から動かすかを尋ねます（既定は「はい」）：下の 15 分ごとの同期と常時稼働の
   ダッシュボードです。「いいえ」の場合も Claude Code のセッションは終了時に記録・分析されます。それ以外は
   `interlatch sync --work`、ダッシュボードは `interlatch ui --open` で。エージェントが動いていれば以後は尋ねません（`--no-launchd --no-ui` でオフ）。
   バックグラウンド同期がオンなら、Interlatch の新しいバージョンが出たときにデスクトップ通知を表示するかも一度だけ
   尋ねます（`--notify-updates` または `--no-notify-updates` を付けると尋ねません）。
6. 分析について説明します：過去のセッションは Claude Code（または Codex）のログインで読み込まれ（プランの使用量に
   含まれます。IBM Bob やモデルプロバイダーを選んでいればそちらで読み込みます）、そこから得た知識で各プロジェクトの
   ナレッジベース、次に**用語集**、次に**マップ**が作られます。
   セッションが分析されるまで、この 2 つは空のままです。待っているセッション数と、バックグラウンドで（15 分ごとに
   6 件）すべて終わるまでの目安を示し、今すぐ分析するかを尋ねます。段階ごと（セッション、ナレッジベース、用語集、
   マップのテーマ）に進捗バーを表示します。すべてか、最初の用語集とマップを数分で作れる新しい 20 件を選べます。
   Ctrl-C で止めても終わった分は残り、残りはバックグラウンドで分析されます。`--analyze all`、`--analyze N`
   （新しい N 件）、`--analyze later` を付けると尋ねません。
7. ダッシュボードのアドレスを表示します。初回は開くかどうか尋ねます。

端末がない場合や `--yes` を付けた場合は、尋ねずに既定値を使います（分析は後で）。何度実行しても安全です：接続済みのエージェントは
質問なしで更新されるので、尋ねるのはその後にインストールされたエージェントだけです。`--dry-run` は何をするかを表示し、何も変更しません。

Claude Code と Mac 本体には、次の 4 つを設定します（それぞれ `--no-hooks`、`--no-launchd`、`--no-ui`、`--no-mcp` で省略できます）。
`--no-launchd` と `--no-ui` は、そのエージェントがインストール済みなら削除もするので、`interlatch install --no-launchd --no-ui`
でバックグラウンド実行だけをオフにできます：

| 構成要素 | 役割 |
| --- | --- |
| `~/.claude/settings.json` の `SessionEnd` フック | 終了したトランスクリプトを、アーカイブ・取り込み・分析を行う切り離されたプロセスに渡します。フック自体は数ミリ秒で戻ります。`settings.json` のバックアップは `~/.interlatch/backups/` に保存されます。 |
| launchd `com.interlatch.sync` | 15 分ごとに `interlatch sync --work` を実行します。フックが取りこぼしたものの回収、分析キューの処理、ナレッジベースの統合、ノートの書き出しを行います。 |
| launchd `com.interlatch.ui` | ダッシュボードを <http://127.0.0.1:11524/> で常時提供します。オンにしていて `app` エクストラがあれば、[メニューバーに Interlatch のアイコン](#メニューバーアイコン)も表示します。 |
| MCP サーバー `interlatch`（ユーザースコープ） | Claude Code が過去のセッションとナレッジを検索できるようにします。 |

任意：`interlatch install --inject-context` を使うと、新しいセッションにそのプロジェクトのナレッジベースの短い要約を渡す
`SessionStart` フックも追加されます（既定ではオフ。内容は `interlatch context` で確認できます）。

任意：`interlatch install --statusline` を使うと、Claude Code の各セッションが実際に使ったコンテキストと、Pro と Max プランでは
そのセッションの間に 5 時間枠と 7 日枠の上限がどれだけ進んだかを記録します（Claude Code はこれらをステータスラインにしか渡しません）。
`~/.claude/settings.json` の `statusLine` を `interlatch statusline` に設定し、これがスナップショットを `~/.interlatch/statusline/`
に保存してから、もともと使っていたステータスラインを同じ入力で実行するため、表示は以前と変わりません。元の設定は
`~/.interlatch/statusline/wrapped.json` に保存されます。ステータスラインを使っていなかった場合はモデル、コンテキスト、
上限を表示し、Claude Code はフッターのキー操作のヒントの大半を表示しなくなります。同期のたびに各セッションの数値がデータベースに
取り込まれます（セッションのページと **Status**）。`interlatch uninstall` で元のステータスラインに戻ります。

任意（macOS）：`interlatch install --menu-bar` を使うと、ダッシュボードが動いている間、メニューバーに Interlatch のアイコンが
表示され、状態、検索、最近のセッションにワンクリックで届きます。`app` エクストラが必要です
（[メニューバーアイコン](#メニューバーアイコン)）。

すべてを削除するには `interlatch uninstall` を実行します（データは残ります。`--purge` でデータも削除）。`--statusline` で
ステータスラインを包んでいた場合は、元のステータスラインも復元します。

コマンドライン版からデスクトップアプリを使うには、`app` エクストラを追加して `interlatch app` を実行します：
`uv tool install --force --python 3.13 'interlatch[app]'`（ほかのエクストラを使っている場合は、それも角かっこ内に並べます）。今後アプリだけを使う場合は、先に `interlatch uninstall` を実行してから
アプリで Claude Code を接続してください。launchd エージェントとアプリが並行して動くのを避けるためです（害はありませんが無駄です）。

## Windows

Windows 10 と 11 では、Interlatch はコマンドライン版を使います。デスクトップアプリとメニューバーアイコンはありません。PowerShell で：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # uv, if you don't have it yet
uv tool install --python 3.13 interlatch
interlatch install
```

`interlatch install` は Mac と同じことを尋ねます。launchd の代わりに、タスク スケジューラの **Interlatch** フォルダーに、
あなたのアカウントでサインイン中に動く 2 つのタスクを登録します：

| タスク | 内容 |
| --- | --- |
| `\Interlatch\Sync` | `interlatch sync --work` を 15 分ごと、およびサインイン時に実行 |
| `\Interlatch\Dashboard` | ダッシュボードを <http://127.0.0.1:11524/> で動かし続けます。サインイン時に起動し、止まっても 5 分以内に再起動 |

どちらもウィンドウを開きません。タスクは `pythonw.exe` を起動し、それがウィンドウのないコンソールでコマンドを実行します。
出力は `%USERPROFILE%\.interlatch\logs` の `sync.out.log` や `ui.err.log` などに残ります。バッテリー駆動中も動きます。
`interlatch uninstall` はタスクを終了して削除し、ダッシュボードも止めます。

Claude Code に追加するフックは `C:/Users/<you>/.local/bin/interlatch.exe hook session-end` を実行します。Claude Code は
フックを Git Bash で（Git for Windows がなければ PowerShell で）実行し、スラッシュ区切りのパスはどちらでも同じに読まれます。
名前に空白を含むフォルダーは短い形式（`C:/Users/ADALOV~1/…`）で書きます。

Interlatch は、Windows の表示言語にかかわらず、コーディングエージェントが書くのと同じ UTF-8 でテキストを読み書きします。

Windows ではまだできないこと：

- **チームのハブへの参加。** ハブの一部はプロジェクトのフォルダーを `/…` 形式のパスとして扱うため、Windows からは未検証です。
- **VS Code 拡張のファイル履歴。** ファイルを `/…` 形式のパスで探します。
- 新しいバージョンの**デスクトップ通知**と、メニューバーアイコン。

## メニューバーアイコン

macOS では、Interlatch のマーク（アプリアイコンの重なったページ）をメニューバーに置けます。デスクトップアプリでは常に表示されます。
コマンドライン版では、オンにしたときだけ表示され、次の 2 つが必要です：ログイン時に起動するダッシュボード（`--no-ui` を付けない限り
`interlatch install` が設定します）と、`app` エクストラ（PyObjC）。

いちばん手軽なのは、**Settings › Status › Recording** の **Menu-bar icon** スイッチです。オンにすると、`app` エクストラが
なければ先に追加し（PyPI からの uv のインストールの場合。ほかのエクストラとバージョンはそのまま。それ以外のインストールでは
実行するコマンドを表示）、アイコン付きでダッシュボードを再起動します。オフにするとアイコンなしで再起動します。ソフトウェアを
インストールするため、このスイッチはそのコンピューター上のブラウザーからだけ使えます。

コマンドラインでオンにするには：

1. `app` エクストラを追加します。`uv tool install --force` は指定したエクストラだけをインストールするため、すでに使っている
   エクストラも並べてください（`uv tool list --show-extras` で確認できます）。例：`'interlatch[app,team]'`

    ```sh
    uv tool install --force --python 3.13 'interlatch[app]'
    ```

2. オンにします。ダッシュボードが再起動し、アイコンが表示されます：

    ```sh
    interlatch install --menu-bar
    ```

    これは通常のセットアップをもう一度実行します（接続済みのエージェントは質問なしで更新されます）。セットアップを省くには、
    設定を変えてダッシュボードを自分で再起動します：

    ```sh
    interlatch config set server.menu_bar true
    launchctl kickstart -k gui/$(id -u)/com.interlatch.ui
    ```

初回のインストールでは、`interlatch install` が代わりに *Show Interlatch's icon in the menu bar?* と一度だけ尋ねます。`y` と
入力しない限り答えは No です。`app` エクストラなしでオンにすると、その旨と手順 1 のコマンドを表示します。

何も伝えることがない間、アイコンは無地のままです：

| アイコン | 状態 |
| --- | --- |
| マークのみ | 確認が必要なものはない |
| 点付き | 同期中、分析中、またはハブへ送信中 |
| 「!」付き | 前回の同期が失敗した、バックグラウンド同期エージェントがエラーで終了した、またはハブが前回の送信を拒否した |
| 薄い表示 | 分析が一時停止中（使用量の上限に達した。自動で再開します） |

クリックすると、メニューバーのガラスの上に、ダッシュボードのブループリント調のパネルが開きます（メニューバーに合わせてライトかダーク）：

| 部分 | |
| --- | --- |
| ヘッダー | いま何が起きているかを絵とともに表示：*All caught up · Synced 5 min ago*、進捗バー付きの *Syncing · 2 of 5*、理由付きの *Sync failed*、*Analysis paused*。⟳ で今すぐ同期（15 分ごとの実行を待たずにアーカイブ・取り込み・分析）、パルスで Activity ページを開く |
| 数値 | 今日のセッション、分析待ちのセッション（ハブが分析するコンピューターでは表示しない）、過去 7 日間に学んだ教訓。それぞれのページを開く |
| ノート | ナレッジが最後にハブへ送られた時刻と、分析キューを止めているものがあればその理由 |
| 検索 | パネルのどこででも入力すると、ダッシュボードの検索と同じようにすべてのセッションを検索。↵ ですべての結果をダッシュボードで開く |
| 最近のセッション | 最新 6 件のコーディングエージェントのセッション（取り込んだチャットは除く）を日ごとに、エージェント、プロジェクト、結果、時刻とともに表示。↑ ↓ で移動、↵ かクリックで開く |
| Open Dashboard | ダッシュボード（アプリではアプリのウインドウ）。新しいバージョンがあると隣に **Update to …** が表示され、アップデートを行う **Status** を開く |
| ⋯ | 下のクイックメニュー |

Esc で検索を消し、もう一度押すとパネルを閉じます。アイコンを右クリック（または Control キーを押しながらクリック）すると、
代わりにクイックメニューが開きます：状態の行、Search Sessions…、Open Dashboard、Sync Now、Update、アプリ独自の項目（**Open in
Browser**、**Connect Claude Code…**、**Open at Login**、**Install Command-Line Tool**、**Open Data Folder**）、**Quit Interlatch**。
ログイン項目の場合、Quit は次にログインするか `interlatch ui` を実行するまでダッシュボード（とアイコン）を止めます。セッションは
引き続き記録されます。

コマンドライン版は、ログイン時に起動するダッシュボードにだけアイコンを表示するため、ターミナルで 2 つ目の `interlatch ui` を
起動してもアイコンは増えません（`--menu-bar` で表示、`--no-menu-bar` で非表示にできます）。オフに戻すには
スイッチを使うか、`interlatch install --no-menu-bar` を実行するか、`interlatch config set server.menu_bar false` を実行して同じ `launchctl kickstart`
の行を実行します。

## アップデート

**設定 › Status › Updates** に Interlatch のインストール方法が表示され、同じ方法でアップデートできます。

| インストール方法 | アップデートボタンが実行するもの |
| --- | --- |
| `uv tool install interlatch` | **Check for updates** で新しいリリースが見つかったあと、`uv tool upgrade interlatch` |
| `uv tool install .`（チェックアウトから） | インストール後にチェックアウトのファイルが変わったとき、`uv tool upgrade --reinstall interlatch`（ネットワーク確認なし） |
| チェックアウトで `uv sync` / `uv run`（editable） | 実行しません。カードに **git pull to update** と表示されます |
| `pipx` または `pip` | `pipx upgrade interlatch` または `pip install --upgrade interlatch` |
| `uv tool install agents-chronicle` または `pipx install agents-chronicle`（Chronicle のパッケージ） | バージョンが同じでも、同じエクストラのまま `interlatch` に移します。uv では `interlatch` を上書きでインストールし、`agents-chronicle` をアンインストールしてから、`interlatch` をもう一度インストールします。pipx ではアンインストールしてからインストールします（[Chronicle からの移行](moving-from-chronicle.md)） |
| デスクトップアプリ | 実行しません。**Download** で最新リリースを開き、アプリケーションフォルダにドラッグします |

ネットワークに接続するのは **Check for updates**（pypi.org）だけです。同じカードの **Check for updates daily** をオンにすると、ダッシュボードを開いている間 1 日 1 回確認します。**Notify me about new versions**（`interlatch install` でも尋ねます）をオンにすると、ダッシュボードを開いていなくてもバックグラウンド同期が 1 日 1 回確認し、リリースごとに 1 回、アップデート方法を書いたデスクトップ通知を表示します（macOS は通知センター、Linux は `notify-send`）。macOS では通知を Interlatch の代わりに Script Editor が出すため、クリックすると Script Editor が開きます。アップデート先は通知の本文に書かれています。最後の結果は再起動後も残ります。アップデートが見つかると通知が表示され（リリースごと、チェックアウトなら新しいコミットごとに 1 回。**Later** で閉じられます）、Settings にドットが付き、ステータスバーに **Update to …** が出ます。チェックアウトの Updates カードには、再インストールで入るコミットと変更ファイルが並びます。
`interlatch ui`（またはその launchd エージェント）で動くダッシュボードはアップデート後に自動で再起動し、開いているタブも再読み込みされます。
コマンドラインの `interlatch app` は終了して開き直してください。同期や分析の実行中は、終わるまでボタンは待ちます。
ターミナルからは同じコマンドを直接実行できます。

Windows では、ボタンか `uv tool upgrade interlatch` でアップデートしてください。どちらもツールのフォルダーを残すので、Interlatch
の実行中でも動きます。実行中に `uv tool install --force interlatch` は使わないでください。先にフォルダーを削除しようとし、
Windows は実行中の `python.exe` を削除させないため、インストールが途中まで消えた状態になります。そうなった場合は、タスクを
終了し（`schtasks /End /TN \Interlatch\Dashboard`、`\Interlatch\Sync` も同様）、Claude Code を閉じてから同じコマンドを
もう一度実行し、`interlatch install` を実行します。

バージョンを固定せずにインストールしてください。`uv tool install 'interlatch==<version>'` とすると uv がインストールの記録に
`==<version>` を残し、`uv tool upgrade`（ボタンも同じ）はそれより先に進みません。`uv tool install --force interlatch`
で固定が外れます。

ソースのチェックアウトのバージョンは git のタグで決まります。`v0.7.0` タグの位置では `0.7.0`、その 3 コミット後は
`0.7.1.dev3+g1a2b3c4` になるので、Status ページのバージョンで最後のリリースからどれだけ進んでいるかが分かります。

### チェックアウトから PyPI のインストールへ

チェックアウトから Interlatch を動かしている Mac でアップデートボタンを使うには、PyPI からインストールし、`interlatch install`
でフック、MCP サーバー、バックグラウンドエージェントを新しい `interlatch` に切り替えます。`~/.interlatch` のデータは
そのまま残ります。インストールし直してもデータには触れません。

```bash
uv tool install --force --python 3.13 'interlatch[app]'           # --force: チェックアウトを指す ~/.local/bin/interlatch を置き換える
interlatch --version                                              # PyPI のバージョン
interlatch install                                                # すべてをこちらに向ける
interlatch status                                                 # フック、MCP サーバー、ダッシュボードのエージェントがすべて ✓
```

以後、ダッシュボードの Updates カードには **uv tool from PyPI** と表示されます。チェックアウトは別のポートで
`uv run interlatch …`（`uv run interlatch ui --port 8799`）として使い続けられ、リリース前の変更を試せます。

うまく動かない場合は、[トラブルシューティング](troubleshooting.md)を参照してください。
