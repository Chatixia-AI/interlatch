# よくある質問

[← Interlatch](../../README.ja.md) · [ドキュメント一覧](README.md)

よく聞かれる質問への短い答えです。詳しくはそれぞれのリンク先を参照してください。うまく動かないときは
[トラブルシューティング](troubleshooting.md)を参照してください。

## はじめに

**エージェント自身のメモリーとは何が違う？** エージェントのメモリー（`CLAUDE.md`、`AGENTS.md`、メモリーのメモ）は、あなたか
そのエージェントが書き留めたもので、そのエージェントのためのものです。Interlatch は使っているすべてのエージェントのすべての
セッションを記録し、Claude Code がトランスクリプトを削除したあと（30 日後）も残し、学んだことを元のセッションへのリンク付きで
取り出して、1 つの [MCP サーバー](mcp.md)ですべてのエージェントに渡します。指示ファイルに加える行も提案しますが、書き込むのは
あなたが承認したときだけです（[提案](suggestions.md)）。

**どのエージェントを記録できる？** Claude Code、Codex、Codex Cloud、GitHub Copilot（CLI と VS Code の Copilot Chat）、IBM Bob、
Google Antigravity と、claude.ai や ChatGPT から取り込んだチャットです（[ソース](sources.md)）。Claude Desktop、Cursor、
Windsurf、Gemini CLI は記録しませんが、Interlatch のツールは使えます（[MCP サーバー](mcp.md#クライアントを接続する)）。

**Claude Code は必須？** いいえ。分析は Claude Code、Codex、IBM Bob のほか、あなた自身のキーでモデルプロバイダーの API でも行えます：
Anthropic、Amazon Bedrock、OpenAI、Azure OpenAI、OpenRouter、OpenAI 互換の任意のサーバー、またはあなたのコンピューター上の
Ollama。**Status › Analysis** または `interlatch config set analysis.backend <name>` で選びます
（[モデルプロバイダー](analysis.md#モデルプロバイダー)）。記録と閲覧はどれでも使えます。何も設定していない場合、
セッションは保管され、分析待ちの列で待ちます。

**デスクトップアプリとコマンドライン、どちら？** アプリ（Apple シリコン、macOS 13 以降）はメニューバーに常駐し、
バックグラウンドの同期も自分で行います。コマンドラインでのインストール（`uv tool install interlatch` のあと `interlatch install`）は
同期とダッシュボードをバックグラウンドのエージェントとして動かし、Intel Mac、Windows、Linux ではこちらを使います。どちらも
`~/.interlatch` の同じデータを使い、併用できます（[インストール](install.md)）。

**Windows や Linux は？** デスクトップアプリは macOS 専用です。Windows では `uv tool install interlatch` と `interlatch install` が
Mac と同じように使え、同期とダッシュボードはタスク スケジューラが動かします（[Windows](install.md#windows)）。
Windows のコンピューターからのチームのハブへの参加はまだ未検証です。Linux では `interlatch install` が同期とダッシュボードを systemd の
ユーザーユニットとして動かすので、Linux マシンをほかのコンピューターの[ハブ](devices.md#linux-のハブ)にできます。
チームのハブは [Docker](docker.md) でも動かせます。

**エージェントが遅くならない？** なりません。セッション終了フックは切り離したプロセスに処理を渡し、数ミリ秒で戻ります。
分析はあとでバックグラウンドで行います。

**インストール前のセッションは？** `interlatch install` はディスクに残っている過去のセッションを取り込み、分析するかを尋ねます：
すべて、新しい 20 件、またはあとでバックグラウンドで。トランスクリプトが消えた 30 日より前の Claude Code のセッションは、
`~/.claude/history.jsonl` からプロンプトを復元します。インストール後のセッションだけを分析するには
`analysis.backfill = false` にします（[設定](configuration.md#analysis)）。

## プライバシーと費用

**マシンの外に送られるものは？** 各セッションをまとめた要約（機密情報は伏せ字）だけで、分析用に選んだものに、あなた自身の
ログインやキーで送られます（Ollama ならそれもありません）。テレメトリはなく、リーダーボードに参加しない限り（後述）Interlatch の作者には何も送られません。
アップデート確認が pypi.org に最新のバージョン番号を問い合わせるのは、ボタンを押したときか、毎日の確認をオンにしたときだけです
（[データとプライバシー](privacy.md)）。

**分析の費用は？** Claude Code や Codex 経由なら、エージェントのほかの利用と同じく Claude や ChatGPT のプランから使われます。
Bob とモデルプロバイダーはあなた自身のアカウントに請求され、Ollama は無料です。Claude の場合、API 料金に換算すると Sonnet で
1 セッションあたり平均約 0.38 ドルです。`interlatch analyze --pending --dry-run` で費用をかけずに未分析分の量を確かめられ、
`analysis.auto = false` で自動の分析を止められます（[分析の仕組み](analysis.md#分析の仕組み)）。

**`CLAUDE.md` や `AGENTS.md` を書き換える？** 提案をあなたが承認したときだけです。先にファイルをバックアップし、自分の
ブロックの中だけに書き、どの変更も元に戻せます（[提案](suggestions.md#interlatch-が決してしないこと)）。

**特定のプロジェクトやセッションを除外できる？** [設定](configuration.md)の `sources.exclude_projects` にプロジェクトを
追加すると、記録も分析もされず、ハブにも送られません。1 つのセッションは `interlatch forget <id>` で完全に削除します。

**リーダーボードとは？利用状況が送られる？** **リーダーボード**のページで、あなたの時間、セッション数、トークン数をほかの人と
比べられます。そこで Google でサインインするまで何も送られません。サインイン後は、同期のたびに直近 7・30・90 日の合計を、
表示する名前と画像とともに送ります。メールアドレス、プロジェクト、プロンプト、タイトルは送りません。
`interlatch leaderboard leave` であなたの行を削除し、送信をやめます。

**パーティーとは？** コーディングエージェントが、セッションから得た教訓を技術キャリアのロードマップに照らして
レベルアップするピクセルアートのキャラクターになります（[パーティー](party.md)）。Google でのサインインが必要ですが何も送らず、
レベルはエージェントのもので、あなたのレベルではありません。

**オープンソース？** はい、MIT ライセンスです（ハブも含みます）。会社のチームに展開するための機能（シングルサインオン、
ポリシー、監査ログのエクスポート）を収めた `ee/` だけが [Interlatch Enterprise License](../../ee/README.md) です。

## エージェントから使う

**学んだことをエージェントはどう使う？** MCP サーバー `interlatch mcp` を通じてです。`interlatch install` が接続した各エージェントに
登録します。ローカルのプロセスとして stdio で動き、ツールはアーカイブを読むだけです。エージェントに「このエラー、前にも
出た？」「billing-api はどうデプロイしている？」と聞いてみてください（[MCP サーバー](mcp.md)）。

**日本語で書ける？** `[analysis] language = "ja"`（または **Status › Analysis**）にすると、要約、ナレッジ、用語集、週次レビューが
以後日本語で書かれます。ダッシュボード自体の言語はブラウザーごとに **Settings › Appearance** で選びます
（[言語](analysis.md#言語)）。

**Slack を読む？** いいえ。Interlatch が読むのは、あなたのコンピューターでコーディングエージェントが書いたものと、取り込んだ
チャットだけです。セッションが Slack のキャンバスを公開した場合は、そのセッションの[成果物](dashboard.md#成果物)にリンクが残ります。

## 複数のコンピューターとチーム

**スマートフォンや複数のコンピューターで使える？** はい。1 台をハブにします。ハブが記録、分析、ダッシュボードの表示を行い、
ほかのコンピューターはセッションをハブに送ります。スマートフォンはハブのダッシュボードを開きます
（[スマートフォンとほかのコンピューター](devices.md)）。

**Tailscale は必要？** いいえ。自分のデバイスどうしなら Tailscale がいちばん手軽ですが、ハブは社内ネットワークや VPN 上でも、
HTTPS のプロキシの後ろで同じように動きます（[Tailscale を使わずにハブにつなぐ](devices.md#tailscale-を使わずにハブにつなぐ)）。

**チームメイトには自分のセッションのどこまで見える？** `--share knowledge` なら、ハブが受け取るのは分析済みセッションの要約と
プロジェクトのナレッジだけで、プロンプト、トランスクリプト、ファイルは送られません。これを付けずに参加したコンピューターは
トランスクリプトを送るので、管理者はハブをナレッジだけを受け付けるようにできます（[共有されるもの](join-a-hub.md#共有されるもの)、
[ナレッジだけを受け付けるハブ](devices.md#ナレッジだけを受け付けるハブ)）。

**`~/.interlatch` を Dropbox や iCloud で同期できる？** いいえ。使用中に SQLite のデータベースをコピーすると壊れることがあります。
ハブを使うか、エージェントのセッションのフォルダーを Syncthing で同期してください
（[ハブを使わない方法](devices.md#ハブを使わない方法syncthing)）。

**データを Postgres に置ける？** Interlatch 自身のデータベースは SQLite ですが、そのコピーを選んだ Postgres のデータベースに置いて、
SQL や BI ツールで使えます（[Postgres へのコピー](postgres.md)）。

## アップデートと削除

**アップデートするには？** **Settings › Status › Updates** が、インストールしたのと同じ方法で Interlatch をアップデートし、
ダッシュボードを再起動します。ターミナルからは `uv tool upgrade interlatch`。アプリは新しいリリースをダウンロードします
（[アップデート](install.md#アップデート)）。

**Chronicle を使っていた。どうなる？** Interlatch の以前の名前は Chronicle です。アップデートするとインストールとデータは自動的に
移行され、`chronicle` コマンドもそのまま使えます（[Chronicle からの移行](moving-from-chronicle.md)）。

**ダッシュボードは 11524 番？8765 番？** 新しいインストールは <http://127.0.0.1:11524/> です。11524 が既定になる前のインストールは
8765 番のままです（`config.toml` の `[server] port`）。アプリは 11524 が使用中なら空いているポートを使います
（[トラブルシューティング](troubleshooting.md#インストールとアプリ)）。

**削除するには？** `interlatch uninstall` でフック、バックグラウンドのエージェント、MCP の登録を削除します（データは残ります）。
データも消すには `--purge` を付けます。アプリの場合は、**Open at Login** もオフにしてアプリを削除します。
