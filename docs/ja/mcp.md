# MCP サーバー

[← Interlatch](../../README.ja.md) · [ドキュメント一覧](README.md)

Interlatch には [Model Context Protocol](https://modelcontextprotocol.io) のサーバーが付いていて、MCP クライアントなら何でも
過去のセッションとナレッジを検索できます。Claude Code、Codex、Copilot、Bob、Antigravity、OpenCode、Claude Desktop、Cursor、Windsurf、Gemini CLI、
そのほか MCP に対応したものすべてです。たとえば *「このエラー、前にも出た？」* *「なぜ冪等性を Postgres でやることにしたんだっけ？」*
*「billing-api はどうデプロイする？」* のように尋ねられます。

サーバーは `interlatch mcp` です。stdio で通信し、クライアントがローカルのプロセスとして起動するので、ネットワークで待ち受けるものは
ありません。どのツールもローカルの保管庫を読むだけです。

## ツール

| ツール | 返すもの | 引数 |
| --- | --- | --- |
| `resume` | 作業を始めるエージェントのための、プロジェクトの現状：直前のセッションの目的と結果、残した未解決の事項、あなたの判断を待っているもの、そして尋ねたエージェントの前回のセッション以降にほかのセッションがしたことと信頼されるようになった教訓。`max_chars` に収まるよう項目を丸ごと省き、省いた数を示します。 | `project`, `agent`, `max_chars` |
| `search_knowledge` | 語句に一致するナレッジ（修正、落とし穴、決定、事実、コマンド、好み）を信頼度の高い順に返し、各項目に[段階](analysis.md#ナレッジの信頼度)（`established ×3`）を付けます。[事件簿](analysis.md#事件簿)に載った項目には、問題が最初にどう見えたかと外れた手がかりも付きます。まず最初に使うツールです。 | `query`, `project`, `kind`, `limit` |
| `search_sessions` | トランスクリプトか要約が一致するセッションと、その抜粋 | `query`, `project`, `limit` |
| `get_session` | 1 つのセッション：要約、結果、ナレッジ、変更したファイル、プロンプト | `session_id`（または一意な先頭部分） |
| `get_transcript` | セッションの会話の一部。ツール呼び出しも含められます | `session_id`, `offset`, `limit`, `include_tools` |
| `project_knowledge` | プロジェクトのナレッジベース：構成、実行・テスト・デプロイの方法、落とし穴、決定、未解決の事項。`project="global"` で全プロジェクト共通のプレイブック。 | `project` |
| `glossary` | 用語の定義、別名、使われ方と出どころ、またはプロジェクトの用語集全体 | `term`, `project` |
| `find_artifacts` | セッションが作ったもの：ドキュメント、ページ、図、スライド、公開したリンク、プルリクエスト、コミット。それぞれの場所、ファイルが書いたときのままディスクにあるか、作ったセッション | `query`, `kind`, `project`, `limit` |
| `recent_sessions` | 最近のセッション（新しい順） | `project`, `days`, `limit` |

`project` にはパスか名前（`billing-api`）を渡します。省略すると、`resume`、`project_knowledge` と `glossary` はクライアントがサーバーを
起動したディレクトリのプロジェクトを使います。コーディングエージェントはプロジェクトの中で起動しますが、Claude Desktop のような
チャットアプリはそうではないので、質問の中でプロジェクト名を挙げてください。`resume` は、接続時にクライアントが名乗る名前から
どのエージェントが尋ねているかを知ります（Claude Code、Codex、Copilot、OpenCode、Antigravity、Bob）。ほかのクライアントは `agent` を渡せます。

すべてのツールは読み取り専用（`readOnlyHint`）と宣言しているので、このヒントに対応したクライアントは確認なしで実行できます。

## クライアントを接続する

**Interlatch が記録するエージェント**は、接続したときにサーバーが登録されます（[ソース](sources.md)を参照）：

| エージェント | 登録先 |
| --- | --- |
| Claude Code | ユーザースコープ、`claude mcp add` で（`interlatch install` または `interlatch connect claude`） |
| Codex | `~/.codex/config.toml`、`codex mcp add` で（`interlatch connect codex`） |
| GitHub Copilot | VS Code の `User/mcp.json` と `~/.copilot/mcp-config.json`（`interlatch connect copilot`） |
| IBM Bob（IDE と Bob Shell） | `~/.bob/settings/mcp.json` と `mcp_settings.json`（`interlatch connect bob`） |
| Google Antigravity | `~/.gemini/config/mcp_config.json`（`interlatch connect antigravity`） |
| OpenCode | `~/.config/opencode/opencode.json`（`interlatch connect opencode`） |

**ほかのクライアント**にはサーバーだけを追加します。Interlatch はそのセッションを記録しません。**Settings › MCP ›
Other MCP clients** から、またはコマンドラインで追加します：

| クライアント | コマンド | 編集する設定ファイル |
| --- | --- | --- |
| Claude Desktop | `interlatch connect claude-desktop` | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Cursor | `interlatch connect cursor` | `~/.cursor/mcp.json` |
| Windsurf | `interlatch connect windsurf` | `~/.codeium/windsurf/mcp_config.json` |
| Gemini CLI | `interlatch connect gemini` | `~/.gemini/settings.json` |

Interlatch は `interlatch` の項目を追加するだけで、ファイルのほかの部分には触れません。事前に `~/.interlatch/backups/` に
バックアップし、プレーンな JSON でないファイル（コメント入りなど）は編集しません。サーバーを読み込むにはクライアントを再起動して
ください。`interlatch disconnect <client>` で項目を削除でき、`interlatch uninstall` でも削除されます。どのクライアントに登録済みかは
`interlatch sources` で確認できます。

## そのほかのクライアント

ダッシュボードの **Settings › MCP** では、サーバーを使えるエージェント、提供するツール、そしてほとんどのクライアント（`mcpServers` の項目）・
VS Code・Codex・Claude Code 向けのコピーできる設定を、インストールに合ったパスで表示します。ターミナルでは次のコマンドで項目を出力します：

```bash
interlatch mcp --print-config
```

```json
{
  "mcpServers": {
    "interlatch": {
      "command": "/Users/you/.local/bin/interlatch",
      "args": ["mcp"]
    }
  }
}
```

`interlatch` の項目をクライアントの MCP 設定に貼り付けます。多くのクライアントはこのような `mcpServers` を使いますが、VS Code は
`servers` を使い、ほかの名前のクライアントもあるので、キーはクライアントのドキュメントで確認してください。次の点に注意してください：

- **フルパスを使う。** デスクトップアプリはシェルの `PATH` を引き継ぎません。コマンドラインでインストールした場合は
  `~/.local/bin/interlatch`、デスクトップアプリだけの場合は `~/.interlatch/bin/interlatch`（アプリに同梱された Interlatch を
  実行する小さなスクリプト）です。
- **ホームを変えている場合。** `INTERLATCH_HOME` を設定しているなら、それも渡します：`"env": {"INTERLATCH_HOME": "/path/to/home"}`。
- **トランスポート。** 対応しているのは stdio だけです。URL でしか接続できないクライアントは、まだ使えません。

## 手で試す

[MCP Inspector](https://github.com/modelcontextprotocol/inspector) でツールの一覧を見て、呼び出せます：

```bash
npx @modelcontextprotocol/inspector ~/.local/bin/interlatch mcp
```

または JSON-RPC をそのまま送ります：

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_knowledge","arguments":{"query":"webhook"}}}' \
  | interlatch mcp
```

## プライバシー

サーバーはあなたの Mac で動き、Interlatch 自身のデータベースだけを読みます。ただし、ツールが返した内容はクライアントの会話の一部になり、
その会話はクライアントのモデルに送られます。Claude Desktop なら Claude、Cursor、Windsurf、Gemini CLI なら選んだモデルです。
ツールの結果には分析用の要約と同じ伏せ字処理がかかります（API キー、トークン、URL 内のパスワードなど）。それ以外のトランスクリプトの
内容はクライアントのモデルに届きうるので、セッションを見せてもよいと思えるモデル提供元のクライアントにだけサーバーを追加してください。
[データとプライバシー](privacy.md)も参照してください。

## トラブルシューティング

- **ツールが表示されない。** 接続したあとクライアントを再起動してください。設定に書かれたパスが存在するかも確認します：
  `ls -l ~/.local/bin/interlatch`。
- **どこでも「No knowledge found」になる。** セッションはバックグラウンドで分析されます。`interlatch status` で待ち行列を確認できます。
- **別のプロジェクトの答えが返ってくる。** `project` を明示するか、プロジェクト名を挙げて尋ねてください。

そのほかは[トラブルシューティング](troubleshooting.md)を参照してください。
