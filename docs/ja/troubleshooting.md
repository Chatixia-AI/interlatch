# トラブルシューティング

[← Interlatch](../../README.ja.md) · [ドキュメント一覧](README.md)

まずは `interlatch status`（またはダッシュボードの **Settings › Status**）を実行してください。フック、バックグラウンドエージェント、
MCP サーバー、`claude` CLI を確認し、最近の分析の失敗を一覧表示します。ログは `~/.interlatch/logs/` にあります
（すべてのログは `chronicle.log`、セッション終了フックのログは `hooks.log`）。

## インストールとアプリ

**macOS に「“Interlatch”は開けません」や「開発元を検証できません」と表示される。** そのリリースは公証されていません。
**システム設定 → プライバシーとセキュリティ** を開き、Interlatch のメッセージの横にある **このまま開く** をクリックして確認してください。

**アプリを使っているのに、Status と Sources で *Background sync* が停止中と表示される。** これらはコマンドライン版のインストールが
設定する launchd エージェントしか確認しません。アプリは自身で 15 分ごとの同期を行っており、最終同期時刻はメニューバーのメニューで確認できます。

**アプリで `analysis.backfill = false` が効かない。** アプリの Connect は、`interlatch install` が記録するインストール日を記録しないため、
接続前のセッションも分析されます。`~/.interlatch/bin/interlatch install --no-launchd --no-ui` を一度実行してください。
インストール日が記録され、フックと MCP サーバーが再登録されます。

**アプリのウインドウをドラッグできない。** ツールバーの何もない部分か、信号機ボタンの横の帯をドラッグしてください。ツールバー内の
ボタンやリンクはクリックにしか反応しません。ソースから起動している場合は、最新のチェックアウトで `uv run --extra app interlatch app` を実行してください。

**メニューバーに Interlatch ではなく「python3」と表示される。** 古いチェックアウトからアプリを起動した場合にだけ起こります。
DMG 版では常に Interlatch と表示されます。

**スイッチはオンなのに、メニューバーアイコンが表示されない。** スイッチ（**Settings › Status › Recording**）はアイコンを
置くだけで、どこに表示されるかは macOS が決めます：

- 0.21.0 以前のダッシュボードでオンにした場合、アイコンが隠れたままになることがあります。そのバージョンはダッシュボードを、macOS が
  アイコンを隠したままにする方法で再起動していました。ダッシュボードを再起動（最後の項目）すると表示されます。
- **システム設定 › メニューバー › メニューバーへの表示を許可** では、コマンドライン版のダッシュボードは Interlatch ではなく、
  動いている Python の名前（たとえば `python3.14`）で表示されます。その項目をオンにしてください。
- ノッチのある MacBook では、ノッチの横に収まらないアイコンは隠れます。不要なアイコンのアプリを終了するか、⌘ キーを押しながら
  アイコンを右へドラッグして場所を空けてください。
- ダッシュボードを再起動します：`launchctl kickstart -k gui/$(id -u)/com.interlatch.ui`。

**ダッシュボードが :11524 にない。** 11524 が使用中の場合（たとえばコマンドライン版のダッシュボードエージェントが使っている場合）、
アプリは空いているポートを使います。メニューバーのメニューの **Open in Browser** で正しいポートが開きます。11524 が既定になる前の
インストールは :8765 のままです（`config.toml` の `[server] port`）。

**`interlatch ui` が「Address already in use」で失敗する、または再インストール後もダッシュボードが古いまま。** コマンドライン版の
launchd エージェントがすでに :11524（以前のインストールでは :8765）でダッシュボードを提供しており、起動時のコードのまま動き続けています。
`launchctl kickstart -k gui/$(id -u)/com.interlatch.ui` で再起動するか、**設定 › Status › Updates** からアップデートしてください
（自動で再起動します。[アップデート](install.md#アップデート)）。別のダッシュボードを動かすには `interlatch ui --port <n>` を使います。

## 記録

**新しいセッションが表示されない。** Claude Code のセッションは `SessionEnd` フックを通じて数秒で取り込まれ、それ以外は
15 分ごとの同期で取り込まれます。`interlatch status` でフックを確認し、`interlatch sync` を実行すると今すぐ取り込めます。
Codex にはセッション終了フックがないため、Codex のセッションはしばらくアイドルになってから表示されます。

**古い Claude Code のセッションがない。** Claude Code は 30 日でトランスクリプトを削除します。Interlatch は一度見たものはすべて保存し、
それより古いセッションについては `~/.claude/history.jsonl` からプロンプト（のみ）を復元して、*history* として表示します。

**記録したくないプロジェクトがある。** 設定の `sources.exclude_projects` に glob を追加するか、`interlatch forget <id>` で
セッションを完全に削除してください。

## 分析

**何も分析されない。** セッションを開くと、要約の下に待っている理由が表示されます。**Status › Analysis**（または
`interlatch status`）では理由ごとの件数がわかります。分析には、選んだものが使える状態になっている必要があります：
ログイン済みの `claude`（または `codex`）CLI、IBM Bob なら Bob API キー、モデルプロバイダーならそのキー（**Status › Analysis** の
**Test connection** で確かめられます）。`interlatch status` で `claude` がどこで見つかったかを確認できます。アプリは
ログインシェルの PATH を読み込むため、npm や Homebrew でインストールした `claude` も見つかります。
セッションは、終了するか `analysis.idle_minutes` の間アイドルになると分析されます。

**「usage limit」で分析が止まった。** Claude が使用量の上限や認証のエラーを返すと、Interlatch は分析を 1 時間停止し、その後自動で
再開します。その他の失敗は間隔を空けて再試行します（30 分、2 時間、8 時間）。セッションページの **Analyze now** で、すぐに再試行できます。

**未分析分にかかる費用を先に確認したい。** `interlatch analyze --pending --dry-run` はトークンを使わずにキューの規模を確認します。
`analysis.max_budget_usd` で 1 回の呼び出しの上限を設定でき、`analysis.auto = false` で自動分析を止められます。

## ハブ

**ハブのダッシュボードに「このダッシュボードにはまだ人が登録されていない」と表示される。** ハブに利用者がいないため、ダッシュボードには
サインインがなく、開けるのはハブ自身（と Tailscale ログイン。[スマートフォン](devices.md#スマートフォン)）だけです。ハブで
`interlatch hub invite <名前> --email <メール> --role admin` を実行して自分を追加し、表示される招待リンクを開いてください
（[利用者とロール](devices.md#利用者とロール)）。

**`interlatch hub join` が「このコンピューターは別の人としてこのハブに参加済みです」と言う。** そのコンピューターは、ハブでまだ別の人の
トークンを持っています。管理者が **チーム › 利用者** でそのコンピューターの横の **Revoke** を押してから、新しいコードで参加してください
（[コンピューターを参加させる](devices.md#コンピューターを参加させる)）。

**`interlatch hub join` が「このハブは同じ ID のコンピューター（…）をすでに知っていて、これがそのコンピューターだと確かめられません」と言う。**
そのコンピューターは以前ハブに送ったことがあり、ハブにはその鍵の記録がないか、別の鍵が記録されています。管理者が、メッセージに出ている
ID でそのコンピューター専用の招待を作り（`interlatch hub invite <名前> --computer <ID>`）、そのコードで参加してください
（[コンピューターを参加させる](devices.md#コンピューターを参加させる)）。

**あるコンピューターのセッションがハブに表示されない。** ハブでは、セッションは最初に送ったコンピューターのものです。同じセッションを
別のコンピューターが送っても受け付けず、ハブのログに「refused: it came from …」（共有セッション）または
「not stored: it belongs to …」（トランスクリプト）と残ります。Claude Code のフォルダーを別のコンピューターに
コピーしたときに起こります。ハブには最初のコンピューターのものが残ります。

**送信が「too large once unpacked」で失敗する。** ハブは、コンピューターから届いたものを上限まで（共有セッション 1 回分は 256 MB、
分析は 1 GB）しか展開しません。上限に届くのは、壊れたアップロードか意図的に作られたものだけです。

**招待リンクやサインインリンクが「このアドレスから間違ったコードが続きました」と言う。** 1 つのアドレスから 15 分以内に、ハブが
発行していないコードが 5 回届くと、そのアドレスからの次の試行は少しずつ長く待つことになります（1 秒、2 秒、4 秒と続き、最長 1 分）。
その時間だけ待ってから、送られたとおりのコードをもう一度貼り付けてください。使用済みや期限切れのコードは数えません。自分のプロキシを
前に置いている場合は、そのアドレスを `[server] trusted_proxies`（Docker では `INTERLATCH_TRUSTED_PROXIES`）に加えてください。
そうしないと、ハブは訪問者を見分けられず、全員をそのプロキシとして数えます。

## Chronicle からの移行

移行で何が変わるかは[Chronicle からの移行](moving-from-chronicle.md)にあります。行った変更はすべて
`~/.interlatch/logs/migrate.log` に記録され、`interlatch migrate --dry-run` でまだ残っている作業を確認できます。

**Update のあとダッシュボードが戻らず、`interlatch` が「command not found」になる。** 0.22.0 の Update は
`agents-chronicle` をアンインストールしたときに `interlatch` と `chronicle` のコマンドも消し、元に戻しませんでした。
バックグラウンドのエージェント、セッションのフック、MCP サーバーはどれもこのコマンドを実行するので、これらも止まっています。
使っていた extras を付けて（`uv tool list --show-extras` で確認できます）コマンドを戻し、ダッシュボードを再起動します。

```bash
uv tool install --force --python 3.13 'interlatch[app]'   # デスクトップアプリなしなら interlatch だけ
launchctl kickstart -k gui/$(id -u)/com.interlatch.ui     # Linux: systemctl --user restart interlatch-ui.service
```

**データフォルダーが `~/.claude-chronicle` のまま。** 移行は、ダッシュボード、バックグラウンドの同期、`interlatch install`、
アプリが始まるときに行われます。今すぐ行うには `interlatch migrate` を実行します。次の場合はフォルダーを移動しません。
`CHRONICLE_HOME` か `INTERLATCH_HOME` が別のフォルダーを指しているとき（そう表示します）、`~/.interlatch` もすでにあるとき
（Interlatch は `~/.interlatch` を使います。必要なものは自分で移してください）、マウントポイントである、別のディスクにある
など、フォルダーの名前を変えるだけでは移せないとき。

**エージェントに `mcp__chronicle__…` のツールが出る、または同じツールが 2 つずつ出る。** 1 つのプロジェクトだけに追加した
サーバー（その `.mcp.json`、または `~/.claude.json` のプロジェクトごとのサーバー）は名前が変わりません。`chronicle` の項目の
名前を `interlatch` に変えてください。エージェントは起動時に MCP サーバーを読み込むので、そのあと再起動します。プロジェクトで
共有している `.claude/settings.json` の `mcp__chronicle__…` のルールも、書き換えるまでそのままです。移行は知らせるだけです。

**`uv tool install interlatch` が「Executable already exists: chronicle」と言う。** `agents-chronicle` がまだ
インストールされていて、コマンドの名前がぶつかっています。先に `uv tool uninstall agents-chronicle` を実行するか、
両方を行う **設定 › Status › Updates** の **Update** を使ってください。

**Update が `interlatch` への切り替えを提案しない。** チェックアウトや git の URL からのインストールは自動では切り替わりません。
アンインストールしてから `interlatch` をインストールしてください（[Chronicle からの移行](moving-from-chronicle.md#アップデートする)）。

## 最初からやり直す

`interlatch uninstall` はフック、バックグラウンドエージェント、MCP の登録を削除し、データは残します。
`interlatch uninstall --purge` はデータも削除します。アプリを使っている場合は、**Open at Login** もオフにしてからアプリを削除してください。
