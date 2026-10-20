# VS Code 拡張機能

[← Interlatch](../../README.ja.md) · [ドキュメント一覧](README.md)

Interlatch 拡張機能は、VS Code のエクスプローラーに 2 つのセクションを追加し、ファイルの背後にあるコーディング
エージェントのセッションを表示します。「このコードはなぜこうなっているのか」がワンクリックで
わかります。書いたセッション、その要約と結果、ダッシュボードでの全文の記録です。

- **Interlatch: This File** は、開いているファイルを読んだり変更したりしたセッションを新しい順に表示します。アクティブな
  エディターに追従し、VS Code のウィンドウごとにそれぞれのファイルを表示します。
- **Interlatch: Files in Workspace** は、開いているフォルダーの中でセッションが読んだり変更したりしたファイルを、
  エクスプローラーと同じようなフォルダーツリーで表示します。フォルダーとファイルごとに、触れたセッションの数と
  時期が表示されます。ファイルを展開するとそのセッションが表示され、横の **Open File** ボタンでファイルが開きます。
  もう存在しないファイルと、git が無視するファイルは表示しません。

新しいセクションはエクスプローラーの一番下に、折りたたまれた状態で追加されます。セクションの見出しをドラッグすると
移動でき、見出しを右クリックするとセクションの表示・非表示を切り替えられます。配置は VS Code が記憶します。

各セッションの行には、タイトルと、実行してからの時間が表示されます。展開すると詳細が 1 行ずつ表示されるので、
狭いサイドバーでも読めます。日時、エージェントとプロジェクト、ブランチ、ファイルに対して行ったこと（`+12 −3 lines`、
`edited 2×`、`read 4×`）、結果、要約です。詳細の最後の **Open in Interlatch**、または行のボタンで、ダッシュボードで
そのセッションが開きます。

データは、すでに動いているダッシュボード（`interlatch ui`、または `interlatch install` が設定するバックグラウンド
サービス）から `http://127.0.0.1:11524`（以前のインストールでは `:8765`）経由で読み込みます。ほかの場所には何も送らず、アカウントも不要です。

コマンドパレットの **Interlatch: Send Feedback** で、不具合を報告（GitHub の新しい Issue）したり、アイデアを提案
（Ideas の新しいディスカッション）したりできます。ダッシュボードにこのインストールの情報（`GET /api/feedback`）を
尋ね、エディターの名前とバージョン、拡張機能のバージョンを加えて表示し、了承したときだけそれを記入済みの GitHub の
ページを開きます。そこで投稿するまで何も送られません。

## インストール

VS Code の拡張機能ビューで Interlatch を検索するか、
[Marketplace のページ](https://marketplace.visualstudio.com/items?itemName=Chatixia-AI.interlatch)からインストールします。
Marketplace を使わないエディター（Cursor、Windsurf、Antigravity、VSCodium は Open VSX からインストールします）では、
リポジトリからビルドしたファイルを、そのエディターのコマンド（たとえば `cursor --install-extension …`）でインストールします。

```bash
cd vscode-extension
npx @vscode/vsce package          # interlatch-0.4.0.vsix を作成
code --install-extension interlatch-0.4.0.vsix
```

一覧が読み込む `/api/file` と `/api/files` エンドポイントは、Interlatch と 0.6.1 より新しい Chronicle にあります。
古いバージョンでは、一覧にアップデートするよう表示されます。

**Chronicle 拡張機能から移る場合。** 0.3.0 より前は Chronicle 拡張機能（`chatixia.chronicle-sessions`、セクションは
**Chronicle: …**）でした。この拡張機能をインストールしてから Chronicle をアンインストールしてください。両方が
インストールされていると、Interlatch が一度だけアンインストールを提案します（**Uninstall Chronicle**）。Chronicle で
変えた設定はそのまま使えます。設定していない `interlatch.*` は、対応する `chronicle.*` の値を使います。

## 設定

| 設定 | 既定値 | 内容 |
| --- | --- | --- |
| `interlatch.url` | 空：`chronicle.url` を設定していればその値、なければ `http://127.0.0.1:11524`、それもなければ `:8765` | ダッシュボードのアドレス。Interlatch の[設定](configuration.md)で `[server] port` を変えた場合は合わせて変更します。ユーザー設定の値だけが使われます。ワークスペースの `.vscode/settings.json` では設定できないので、リポジトリが拡張機能を別のサーバーに向けることはできません。 |
| `interlatch.includeReads` | `true` | 読んだだけのセッションとファイルも表示します。オフにすると、セッションが変更したものだけになります。 |
| `interlatch.showIgnoredFiles` | `false` | git が無視するファイル（スクリーンショット、ビルド出力、キャッシュ）も **Interlatch: Files in Workspace** に表示します。 |

## 見つかるもの、見つからないもの

ファイルは絶対パスで照合し、パスをセッションのフォルダーからの相対パスで記録するエージェント（Codex がそうすることが
あります）についてはその相対パスでも照合します。そのため、次のものは見つかりません。

- **別の git worktree や古いクローンにある同じファイル。** `../myapp-feature` で動いたセッションが記録したのは
  `../myapp-feature/src/app.py` で、`myapp/src/app.py` とは別のパスです。
- **リモートウィンドウのファイル**（SSH、コンテナー、WSL）。一覧には、Interlatch はこのコンピューター上のファイルしか
  わからないと表示されます。
- **まだ同期されていないセッション。** Claude Code のセッションは終わったときに、Codex、Copilot、Bob、Antigravity、OpenCode のセッションは
  15 分ごとに取り込まれます（[ソース](sources.md)）。

## エンドポイント

`GET /api/file?path=<絶対パス>&limit=50` は `{"path", "total", "sessions"}` を返します。各セッションには
`/api/sessions` と同じ項目に加えて、このファイルについての `file: {reads, changes, added, removed}` が含まれます。
ダッシュボードに届くものなら、スクリプトでもほかのエディターでも使えます。

`GET /api/files?root=<絶対パスのフォルダー>&limit=200&existing=1&ignored=0` は `{"root", "total", "files"}` を返します。
そのフォルダーの中でセッションが読んだり変更したりしたファイルを、最近触れた順に並べたものです。各ファイルには
`path`、`rel`（`root` からの相対パス）、`sessions`、`changed`（そのうち変更したセッションの数）、`last`、
そして数の元になる ID（`session_ids`、`changed_ids`）が含まれます。`existing=1` を付けるともう存在しない
ファイルを、`ignored=0` を付けると git が無視するファイルを除きます。
