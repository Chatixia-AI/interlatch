# 設定

[← Interlatch](../../README.ja.md) · [ドキュメント一覧](README.md)

設定は `~/.interlatch/config.toml` にあります。`interlatch config` で表示し、`interlatch config edit` でファイルを開きます。
変更は次の同期またはワーカーの実行時に反映されます。`INTERLATCH_HOME` で保存先全体を移動できます。

## `[sources]`

| キー | 既定値 | |
| --- | --- | --- |
| `claude_dirs` | `["~/.claude"]` | 読み込む Claude Code の設定ディレクトリ。複数指定できます |
| `codex_dirs` | `[]` | Codex を接続すると `["~/.codex"]` |
| `codex_cloud` | `false` | Codex Cloud を接続すると `true`。同期のたびに codex CLI でタスクを一覧します |
| `copilot_dirs` | `[]` | Copilot を接続すると `~/.copilot` と VS Code の `User` ディレクトリ |
| `bob_dirs` | `[]` | Bob を接続すると `["~/.bob"]` |
| `antigravity_dirs` | `[]` | Antigravity を接続すると `["~/.gemini/antigravity"]` |
| `opencode_dirs` | `[]` | OpenCode を接続すると `["~/.local/share/opencode"]` |
| `import_history` | `true` | Claude Code がすでに削除したセッションのプロンプトを `history.jsonl` から復元する |
| `import_memory` | `true` | Claude の自動メモリーノート（`projects/*/memory/*.md`）をナレッジとして取り込む |
| `exclude_projects` | `[]` | 完全に無視するプロジェクトパスの glob パターン。例：`["/Users/me/secret/*"]` |

## `[analysis]`

| キー | 既定値 | |
| --- | --- | --- |
| `auto` | `true` | アイドルになったセッションを自動で分析する |
| `backend` | `claude` | セッションを分析するもの：あなた自身のログインで `claude`（Claude Code）、`codex`（Codex）、`opencode`（OpenCode）、Bob の API キーで `bob`（IBM Bob Shell。`interlatch config set-key bob`）、またはモデルプロバイダーの API：`anthropic`、`bedrock`、`openai`、`azure`、`openrouter`、`ollama`、`openai-compatible`（[`[providers.<name>]`](#providers) を参照）。または `none`：セッションを記録するだけで分析しません（`interlatch install --no-analysis`）。**Status › Analysis** でも変更できます |
| `model` / `effort` | `sonnet` / `medium` | Claude：`claude --model` の任意のエイリアスか完全なモデル ID。`effort` は Codex の推論の強さにも使われます（`max` は `xhigh` になります）。どちらも **Status › Analysis** の Claude Code タブで変更できます |
| `codex_model` | `""` | Codex のモデル。例：`gpt-5.5`。空の場合は Codex の既定のモデルを使います |
| `opencode_model` | `""` | OpenCode のモデル（`provider/model`）。例：`ollama/qwen3.8`。空の場合は OpenCode の既定のモデルを使います |
| `screen_model` | `haiku` | 取り込んだチャットを選別するモデル（`interlatch screen`）。各チャットの冒頭だけを、1 回の呼び出しで 60 件ずつ読みます |
| `language` | `en` | Interlatch が書く言語：要約、ナレッジ、ナレッジベース、プレイブック、用語集の定義、週次の振り返り、選別の理由、`CLAUDE.md` / `AGENTS.md` に提案する行。`en` または `ja`。変更したあとに分析するものから反映されます。**Status › Analysis** でも変更できます。ダッシュボード自体の言語は、ダッシュボードでブラウザーごとに選びます |
| `max_budget_usd` | `3.0` | `claude -p` の呼び出し 1 回あたりの費用上限（API 換算の USD。Codex はトークン数だけを報告します） |
| `idle_minutes` | `20` | 分析されるには、セッションが終了しているか、この時間アイドルである必要があります |
| `min_prompts` | `1` | 人間のプロンプトがこれより少ないセッションはスキップされます |
| `max_per_run` / `concurrency` | `6` / `2` | 15 分ごとの実行 1 回あたりの分析数と、並列に動かす分析プロセスの数 |
| `backfill` | `true` | インストール前に記録されたセッションも分析する（新しい順） |
| `chunk_chars` | `150000` | 1 回の呼び出しに渡す、まとめたトランスクリプトの文字数。これより長いセッションは map-reduce で処理します |
| `timeout_seconds` | `900` | 呼び出し 1 回あたりの実時間の上限 |
| `claude_bin` / `codex_bin` / `bob_bin` / `opencode_bin` | `""` | `claude` / `codex` / Bob Shell の `bob` / `opencode` のパス（空の場合は自動で検出。OpenCode はデスクトップアプリに同梱の CLI も探します） |

## `[providers.*]`

`analysis.backend = "<name>"` のときの設定です。一覧は[モデルプロバイダー](analysis.md#モデルプロバイダー)にあります。
**Status › Analysis** か `interlatch config set providers.<name>.<key> <value>` で設定します。API キーはここには置きません：
`interlatch config set-key <name>`（またはダッシュボード）が、あなたのユーザーだけが読める `provider-keys.json` に保存します。

| キー | 既定 | |
| --- | --- | --- |
| `base_url` | プロバイダーのもの | API のアドレス。`openai-compatible` では必須、`azure` では `resource` がなければ必須 |
| `model` / `small_model` | `anthropic` と `bedrock` は Claude Sonnet 5.5 / Claude Haiku 4.5、それ以外はなし | 分析とナレッジベース用のモデル / 取り込んだチャットの選別用（空なら `model`） |
| `region` / `profile` | `AWS_REGION`、なければ `us-east-1` / `AWS_PROFILE` | `bedrock`：エンドポイントのリージョンと、Bedrock の API キーがないときにサインインする AWS プロファイル |
| `resource` | | `azure`：`https://<resource>.openai.azure.com/openai/v1` のリソース名 |
| `num_ctx` | `32768` | `ollama`：コンテキスト長（トークン） |
| `chunk_chars` | `analysis.chunk_chars`（`ollama` は `60000`） | 1 回の呼び出しで送る要約済みトランスクリプトの文字数 |
| `max_output_tokens` | `anthropic` / `bedrock` は `32000`、それ以外はモデル自身の上限 | 1 回あたりの出力上限 |
| `json_mode` | `true`（`openai-compatible` は `false`） | OpenAI 形式のプロバイダー：JSON オブジェクトを求める |
| `key_env` | プロバイダーの通常の変数 | キーを読む別の環境変数 |

## `[synthesis]`、`[export]`、`[server]`、`[inject]`、`[updates]`

| キー | 既定値 | |
| --- | --- | --- |
| `synthesis.auto` / `model` / `min_new_items` | `true` / `sonnet` / `3` | 新しい項目がこの件数たまると、プロジェクトのナレッジベースを再構築する |
| `synthesis.weekly_day` | `"mon"` | [毎週の再分析](dashboard.md#プロジェクトのグループ)を実行する曜日（`mon` … `sun`、このコンピューターのタイムゾーン）。**Settings › Re-analysis** で設定する |
| `export.markdown` | `true` | すべてを Markdown 保管庫にミラーする |
| `export.notes_dir` | `""` | 保管庫の場所（空の場合：`~/.interlatch/notes`） |
| `server.host` / `port` | `127.0.0.1` / `11524` | ダッシュボード。このポートが使用中の場合、アプリは空いているポートを使います。11524 が既定になる前のインストールは `config.toml` に `port = 8765` を持ったままです。移すには `interlatch config set server.port 11524` を実行し、ダッシュボードを再起動して（`launchctl kickstart -k gui/$(id -u)/com.interlatch.ui`）。ハブとして送ってくるコンピューターでは、それぞれ `interlatch hub set-url http://<hub>:11524 --yes` で新しいアドレスを設定します |
| `server.allowed_hosts` | `[]` | 127.0.0.1 と localhost のほかにダッシュボードが応答する名前。Tailscale の名前など。`interlatch tailnet on` が設定します（[スマートフォンとほかのコンピューター](devices.md#スマートフォン)） |
| `server.allowed_users` | `[]` | それらの名前で Tailscale Serve 経由でアクセスしたとき、通す Tailscale ログイン（空の場合は tailnet の全員）。`interlatch tailnet on` があなたのログインを設定します |
| `server.auth_header` | `""` | 認証プロキシ経由の会社のサインイン：サインインした人のメールアドレスを運ぶリクエストヘッダー。例：`"X-Forwarded-Email"`。`trusted_proxies` からのリクエストで、ハブに追加された人の場合だけ信頼します。空の場合は招待かサインインリンクでサインインします（[会社のサインイン](devices.md#会社のサインイン)） |
| `server.trusted_proxies` | `["127.0.0.1", "::1"]` | ダッシュボードが `auth_header`、`X-Forwarded-Proto`、`X-Forwarded-For` を信頼するプロキシのアドレス。コードを試す訪問者は、`X-Forwarded-For` の最後の項目で見分けます（[間違ったコードが続いたとき](troubleshooting.md#ハブ)）（[Tailscale を使わずにハブにつなぐ](devices.md#tailscale-を使わずにハブにつなぐ)） |
| `server.behind_proxy` | `false` | このコンピューター上のリバースプロキシがダッシュボードに転送する：それを通るリクエストはハブ自身から（常に管理者）とは見なされず、管理者はサインインするか `interlatch hub` コマンドを使う（[Tailscale を使わずにハブにつなぐ](devices.md#tailscale-を使わずにハブにつなぐ)） |
| `server.menu_bar` | `false` | macOS：ログイン時に起動するダッシュボードが、メニューバーに Interlatch のアイコンも表示する（`app` エクストラが必要）。`interlatch install --menu-bar` でオンになります（[メニューバーアイコン](install.md#メニューバーアイコン)） |
| `inject.session_start` / `max_chars` | `false` / `3000` | 新しいセッションにプロジェクトのナレッジベースの要約を渡す（SessionStart フック）。長さは `max_chars` 文字以内で、収まらないときは役立ちにくい項目から丸ごと省き、最後の行に省いた数を示す |
| `updates.check_daily` | `false` | ダッシュボードを開いている間、1 日 1 回 pypi.org に最新バージョンを問い合わせる（Status › Updates） |
| `updates.notify` | `false` | バックグラウンド同期が 1 日 1 回 pypi.org に問い合わせ、新しいリリースごとに 1 回デスクトップ通知を表示する（Status › Updates、または `interlatch install --notify-updates`） |

## `[suggestions]`

繰り返し起きる失敗への修正案（[提案と「うまくいかないこと」](suggestions.md)）。

| キー | 既定値 | |
| --- | --- | --- |
| `enabled` | `true` | バックグラウンド同期のたびに提案を更新する（モデルは呼び出しません）。適用するまで何も書き込みません。**Check again** と `interlatch suggest refresh` はどちらの場合も使えます |
| `notify` | `false` | バックグラウンド同期で新しい提案が見つかったら、デスクトップ通知を表示する |

## `[game]`

ゲームモード：[パーティー](party.md)、リーダーボード、アリーナのある Innerlatch（[開き方](party.md#開き方)）。

| キー | 既定値 | |
| --- | --- | --- |
| `enabled` | `false` | Innerlatch への扉を表示し、そのページを開けるようにする。オフのあいだは Innerlatch のルートが断り、リーダーボードには何も送られません。`interlatch install` が一度だけ尋ね、**設定 › 外観 › ゲームモード**で切り替えられます。選べるようになる前の config.toml にはこのキーがなく、その場合はパーティーかリーダーボードの Google サインインが残っているときだけオンです。ハブでは、サインインしている全員に適用されます |

## `[systems]`

[システムマップ](dashboard.md#システムマップ)。

| キー | 既定値 | |
| --- | --- | --- |
| `read_manifests` | `true` | 各プロジェクトフォルダーのマニフェストを少しだけ読み取り専用で読む（パッケージのマニフェスト、compose ファイル、Dockerfile、Terraform、CI ワークフロー、vite の設定、`.env.example`、デプロイ設定）。`false`：パーツはセッションが行ったことだけから描きます |

## `[mirror]`

アーカイブのコピーを、選んだ Postgres データベースに置きます（[Postgres へのコピー](postgres.md)）。

| キー | 既定値 | |
| --- | --- | --- |
| `to` | `""` | `"postgres"` でバックグラウンド実行のたびにコピーを書き込みます。接続（PGHOST、PGPORT、PGDATABASE、PGUSER、PGPASSWORD、PGSSLMODE）は Interlatch のフォルダーの `mirror.env` から読みます。**Settings › Storage** で両方を書けます。ドライバーが必要：`uv tool install 'interlatch[postgres]'` |
| `include` | `"knowledge"` | `"knowledge"`：セッションの詳細・要約・分析、教訓、ナレッジベース、振り返り、用語集、成果物、トークン使用量。プロンプトとトランスクリプトは含みません。`"everything"`：プロンプトとトランスクリプトも（秘密情報は伏せ字）。ほかの値は `"knowledge"` として扱います（[入れる内容](postgres.md#入れる内容)） |
| `schema` | `"chronicle"` | 書き込む Postgres のスキーマ。1 台に 1 つ（[スキーマは 1 台に 1 つ](postgres.md#スキーマは-1-台に-1-つ)） |

## `[hub]`

複数のコンピューターで 1 つのアーカイブ（[スマートフォンとほかのコンピューター](devices.md#ほかのコンピューター)）。

| キー | 既定値 | |
| --- | --- | --- |
| `url` | `""` | セッションをハブに送るコンピューターで：ハブのアドレス。`interlatch hub join` が設定し、`interlatch hub leave` が消します。設定されている間、このコンピューターは記録・分析をせずにハブへ送ります |
| `path_map` | `{}` | ハブで：ほかのコンピューターのフォルダーのうち、こちらのフォルダーと同じプロジェクトを持つもの。例：`{ "/home/me/code" = "/Users/me/Projects" }`。git リモートのあるプロジェクトは先にリモートで対応付けます |
| `folders` | `{}` | ハブに送るコンピューターで：セッションをハブのプロジェクトに入れるこちらのフォルダー（その下も含む）。例：`{ "/Users/me/work/notes" = "/Users/hub/Projects/demo-app" }`。`interlatch hub add-folder` が設定します（[同じプロジェクト、別のフォルダー](devices.md#同じプロジェクト別のフォルダー)） |
| `share` | `"everything"` | ハブに送るコンピューターで：`"everything"` はトランスクリプトを送り、ハブが記録・分析します。`"knowledge"` はこちらで記録・分析を続け、各セッションの情報、要約、プロジェクトのナレッジだけを送ります（[ナレッジだけを共有する](devices.md#ナレッジだけを共有する)）。一部のプロジェクトに限られた人として参加したコンピューターは `"knowledge"` でなければなりません。`interlatch hub join --code` がそう設定し、ハブはそのコンピューターからのトランスクリプトを受け付けません |
| `accept` | `"everything"` | ハブで：`"knowledge"` にすると、どのコンピューターからも要約とプロジェクトのナレッジだけを受け取り、トランスクリプトを送るコンピューターは受け付けません。`"everything"` 以外の値は `"knowledge"` として扱います（[ナレッジだけを受け付けるハブ](devices.md#ナレッジだけを受け付けるハブ)） |
| `all_folders` | `false` | ハブとナレッジを共有するコンピューターで：`false` はハブがいずれかのプロジェクトに入れるセッション（`interlatch hub add-folder` で追加したフォルダー、またはハブがプロジェクトに入れている git リモートのリポジトリ）だけを共有し、それ以外はここに残します。`true` はすべてのフォルダーのセッションを共有します。`interlatch hub join --all-folders` で設定されます（[ナレッジだけを共有する](devices.md#ナレッジだけを共有する)） |
| `store` | `""` | ハブで：`"postgres"` にすると、チームの記録を Postgres にも残し（接続は Interlatch のフォルダーの `team-store.env`）、ナレッジを共有するコンピューターにチームメイトのナレッジを返します（[チームメイトのナレッジと、Postgres のチームストア](devices.md#チームメイトのナレッジとpostgres-のチームストア)） |
| `shared_token` | `true` | 利用者のいるハブで：コンピューターが専用のトークンの代わりにハブの共有トークンで送ることを許す。共有トークンで送るコンピューターは特定の誰でもなく、プロジェクトの制限はかかりません。全員が招待で参加し終えたらオフにします（`interlatch hub shared-token off`）（[共有トークン](devices.md#共有トークン)） |
| `address` | `""` | ハブで：ほかのコンピューターやブラウザーからつなぐときのハブのアドレス。例：`"https://interlatch.example.internal"`。ハブが渡す参加コマンドとサインインリンクに使います。`interlatch hub enable --url` が設定します（[利用者とロール](devices.md#利用者とロール)） |
| `left` | `[]` | ハブとナレッジを共有するコンピューターで：抜けたハブのプロジェクト（ハブでのパス）。そこに入るセッションは共有せず、チームメイトのナレッジも受け取りません。すでに共有したものは残ります。`interlatch hub leave --project` と `rejoin --project` で設定されます（[やめるとき](devices.md#やめるとき)） |
| `dedicated` | `false` | ハブで：自分のセッションを持たない、チームのためのサーバーであること。[Docker のイメージ](docker.md)が設定します。ダッシュボードはチームの Home から始まり、個人のコンピューターにだけ必要なものを省き、管理者は **Team › Projects** で名前を付けてプロジェクトを作れます（[ハブのダッシュボード](devices.md#ハブのダッシュボード)） |
| `name` | `""` | ハブで：ダッシュボードに表示する名前。例：`"Resona team"`。空ならハブのコンピューターの名前です（[ハブのダッシュボード](devices.md#ハブのダッシュボード)） |

ハブで用意したプロジェクト（`interlatch hub project add`）と、各自に見えるプロジェクト（`interlatch hub invite`、
`interlatch hub access`）は、このファイルではなくハブのデータベースに保存されます
（[見えるプロジェクトを人ごとに決める](devices.md#見えるプロジェクトを人ごとに決める)）。
