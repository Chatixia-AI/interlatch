# Chronicle からの移行

[← Interlatch](../../README.ja.md) · [ドキュメント一覧](README.md)

Interlatch は以前 Chronicle という名前でした。名前が変わっただけの同じプログラムで、セッション、ナレッジ、設定、
ハブはそのまま使えます。移行はインストール 1 回で済みます。

## アップデートする

**コマンドラインの場合：** PyPI のパッケージ名は `interlatch` になりました（以前は `agents-chronicle`）。
**設定 › Status › Updates** の **Update** が、バージョンが同じでも切り替えを行い、エクストラもそのまま引き継ぎます。
手動で切り替える場合は、2 つのパッケージのコマンドがぶつかるので先にアンインストールし、使っていたエクストラ
（`uv tool list --show-extras` で確認できます）を付けてインストールします。

```bash
uv tool uninstall agents-chronicle
uv tool install --python 3.13 'interlatch[app]'   # デスクトップアプリが不要なら interlatch だけ
```

pipx では `pipx uninstall agents-chronicle` のあとに `pipx install interlatch` です。切り替えるまでは
`uv tool upgrade agents-chronicle` もそのまま使えます。最後の `agents-chronicle` のリリースが Interlatch をインストールし、
両方のコマンドを残します。チェックアウトや git の URL からのインストールは、ボタンでは切り替わりません。
アンインストールしてから、同じ方法で `interlatch` をインストールしてください。

**デスクトップアプリの場合：** [最新リリース](https://github.com/Chatixia-AI/interlatch/releases/latest)から
`Interlatch-<version>-arm64.dmg` をダウンロードし、**Interlatch** を **アプリケーション** にドラッグして開きます。
macOS からは別のアプリに見えるため、Chronicle.app はそのまま残ります。Interlatch が起動したら削除してください
（ログイン時に開く設定も一緒に消えます）。

## 移行が行われるとき

移行は、`interlatch ui`、`interlatch sync`、`interlatch install` が始まるときと、アプリを開いたときに自動で行われます。
アップデートのあと、数分以内にダッシュボードかバックグラウンドの同期が行います。セッションのフックと MCP サーバーは
移行を始めませんが、その必要もありません。どちらの名前のデータフォルダーも見つけられます。

`interlatch migrate` は移行を手動で行い、何を変えたかを表示します。`interlatch migrate --dry-run` は何が変わるかを表示する
だけで、何も変えません。もう一度実行しても何も変わりません。移行の記録は `~/.interlatch/logs/migrate.log` に残ります。
途中で止まった移行（コンピューターがスリープした、プロセスが止められたなど）は、次にこれらのコマンドが始まるときに続きから
行います。フォルダー内の `migration.json` が進み具合を覚えています。

**自分で決めたデータフォルダー。** `CHRONICLE_HOME` や `INTERLATCH_HOME` で指定したフォルダーは移動しません。名前が
変わるのは連携の設定だけです。ただし、変数が `~/.claude-chronicle` そのものを指している場合は通常のフォルダーとみなして
移動します。Chronicle のバックグラウンドエージェントがまさにそう設定しているためです。

## 変わるもの

| 対象 | 以前 | 以後 |
| --- | --- | --- |
| データフォルダー | `~/.claude-chronicle` | `~/.interlatch`。古いパスにはリンクを残すので、まだそこを指しているものも動きます。データベースが保存しているパスも書き換えます |
| Claude Code | フック、ステータスライン、MCP サーバー `chronicle` | 同じものが `interlatch` を実行します。MCP サーバーは `interlatch` として登録し直します |
| Codex | `config.toml` の `[mcp_servers.chronicle]` | `[mcp_servers.interlatch]`。設定を残したままその場で名前を変えます |
| VS Code、Copilot CLI、IBM Bob、Antigravity、Claude Desktop、Cursor、Windsurf、Gemini CLI の MCP サーバー | `chronicle` | `interlatch`。設定を残したままその場で名前を変えます |
| Claude Code の許可ルール（自分の `settings.json` と `settings.local.json`、各プロジェクトの `.claude/settings.local.json`） | `mcp__chronicle__…` | `mcp__interlatch__…`。エージェントがもう一度確認を求めることはありません |
| `CLAUDE.md` と `AGENTS.md` で Interlatch が管理するブロック | `<!-- BEGIN chronicle -->`、`<!-- chronicle:friction:… -->` | `<!-- BEGIN interlatch -->`、`<!-- interlatch:friction:… -->` |
| バックグラウンドエージェント（macOS の launchd） | `com.claude-chronicle.sync`、`com.claude-chronicle.ui` | `com.interlatch.sync`、`com.interlatch.ui` |
| バックグラウンドエージェント（Linux の systemd） | `chronicle-sync.timer`、`chronicle-ui.service` | `interlatch-sync.timer`、`interlatch-ui.service` |

編集するファイルは、エージェントを接続するときと同じく、事前に `~/.interlatch/backups/` にバックアップします。
データベースの名前（`chronicle.db`）とフォルダー内のほかのファイルの名前は変わりません。

## 手で行うこと

次のものは、移行が知らせるだけで、変更はあなたに任せます。

- **プロジェクトで共有している `.claude/settings.json`** が `mcp__chronicle__…` のツールを許可している場合。プロジェクトに
  コミットされていて、ほかの人がまだ Chronicle を使っているかもしれません。全員が移行したら、ルールを
  `mcp__interlatch__…` に書き換えてください。
- **1 つのプロジェクトだけに追加した MCP サーバー**（その `.mcp.json`、または `~/.claude.json` のプロジェクトごとの
  サーバー）。`chronicle` の項目の名前を `interlatch` に変えてください。そのままだと、エージェントが同じツールを 2 つの名前で
  重ねて持つことになります。
- **Chronicle.app**。削除してよいと知らせるだけで、勝手に削除はしません。

データフォルダーの古い `bin/chronicle` スクリプトは、まだそれを呼ぶもののために、Interlatch を動かすコピーとして残します。

## そのまま使えるもの

- **`chronicle` コマンド。** `interlatch` と同じプログラムなので、`chronicle …` を呼ぶスクリプトや手癖もそのまま
  動きます。ドキュメントでは `interlatch` を使います。
- **環境変数。** `CHRONICLE_*` の変数は引き続き読み込みます。`INTERLATCH_*` の名前（`INTERLATCH_HOME`、
  `INTERLATCH_HUB_URL` など）と両方ある場合は `INTERLATCH_*` が優先されます。
- **ハブ。** ハブと、そこへ送るコンピューターは、どの順番で移行してもかまいません。古いバージョンと新しいバージョンは
  そのままやり取りでき、招待、サインイン、トークンも有効なままです。[Docker のハブ](docker.md#更新する)は、イメージ
  `ghcr.io/chatixia-ai/interlatch-hub` を使う最新の `compose.yaml` で移行します。`.env`、ボリューム、データはそのままで、
  `/data` フォルダーが移動することもありません。古い `compose.yaml` のままのハブにも、古いイメージ名で各リリースが届きます。
- **Postgres へのコピー**とハブのチームストアは、スキーマもテーブルも変わりません。
- **リンク。** chronicle.chatixia.net は [interlatch.com](https://interlatch.com) に転送されます。

## VS Code 拡張機能

Chronicle 拡張機能は Interlatch でもそのまま使えます。後継は Interlatch 拡張機能で、セクションは **Interlatch: …**、
設定は `interlatch.*` です。[インストール](vscode.md#インストール)すると、古い拡張機能が残っている間に一度だけ
そのアンインストールを提案します。Chronicle で変えた `chronicle.*` の設定は、`interlatch.*` を設定するまでそのまま使われます。

うまくいかないときは[トラブルシューティング](troubleshooting.md#chronicle-からの移行)を参照してください。
