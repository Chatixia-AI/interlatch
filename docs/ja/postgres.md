# Postgres へのコピー

[← Interlatch](../../README.ja.md) · [ドキュメント一覧](README.md)

Interlatch はアーカイブを、コンピューター上の自前の SQLite データベース（`~/.interlatch/chronicle.db`）に保存します。
そのコピーを、選んだ Postgres データベース（このコンピューター、Docker、クラウドのいずれか）に置くこともできます。コピーは SQL や
BI ツール（Metabase、Grafana、Power BI、ノートブックなど）で問い合わせたり、別の場所のコピーとして持っておいたりするのに使えます。

コピーは一方向です。Interlatch はバックグラウンド実行のたびに書き込み、読み戻すことはありません。ダッシュボード、検索、MCP サーバー、
セッション開始時のメモは引き続きコンピューター上のデータベースを使うので、Postgres が止まっていても動きは変わりません。

## 設定する

Postgres のデータベースと、そこにテーブルを作れるユーザーが必要です。Postgres のドライバーは `postgres` エクストラに含まれています：

```bash
uv tool install --reinstall 'interlatch[postgres]'
```

インストールしたらダッシュボードを再起動してください（アプリを終了して開き直すか、`interlatch ui` をもう一度実行）。

### ダッシュボードで

**Settings › Storage**（保存場所）を開き、**Copy in Postgres**（Postgres へのコピー）を **Postgres** に切り替えて、サーバーのアドレス、
ポート、データベース、ユーザー、パスワード、SSL モードを入力します。**What it holds**（入れる内容）でどこまで入れるかを選びます
（[入れる内容](#入れる内容)）。**Everything**（すべて）を選ぶとトランスクリプトに含まれるものについての警告が出て、トランスクリプトを送り始める前に**保存**で確認を求めます。**接続をテスト**で設定を確かめ、**保存**するともう一度確かめてから保存し、すぐにコピーを書き込みます。
接続できない設定は保存しません。パスワードは Interlatch のフォルダーの `mirror.env`（自分のユーザーだけが読めるファイル）に入り、
ダッシュボードに再び表示されることはありません。

このページには、コピーの中身、最後に書き込んだ日時、エラーがあればそれが表示されます。**Write now**（今すぐ書き込む）を押すと、次の
バックグラウンド実行を待たずに最新にします。利用者がいるハブでは、このページを開けるのは管理者だけです。

### コマンドラインで

```bash
# PGHOST、PGPORT、PGDATABASE、PGUSER、PGPASSWORD（と PGSSLMODE、既定は require）を 1 行に 1 つ：
$EDITOR ~/.interlatch/mirror.env && chmod 600 ~/.interlatch/mirror.env
interlatch config set mirror.to postgres
interlatch mirror sync        # 今すぐ書き込み、何を書いたかを表示
interlatch mirror             # 書き込み先、テーブルごとの行数、最後の書き込み
```

### このコンピューターの Docker で Postgres を動かす

```bash
docker run -d --name interlatch-pg --restart unless-stopped \
  -e POSTGRES_USER=interlatch -e POSTGRES_DB=interlatch -e POSTGRES_PASSWORD=<パスワード> \
  -p 127.0.0.1:5432:5432 -v interlatch-pg:/var/lib/postgresql/data postgres:17
```

設定には、サーバーのアドレス `127.0.0.1`、ポート `5432`、データベースとユーザーに `interlatch`、決めたパスワード、SSL モード
`disable` を使います。Docker の Postgres には TLS 証明書がなく、ポートはこのコンピューターからしか開いていません。データは
`interlatch-pg` ボリュームにあるので、コンテナーを作り直しても残ります。

### クラウドのデータベース

マネージドの Postgres ならどれでも使えます（Azure Database for PostgreSQL、Amazon RDS、Google Cloud SQL、Supabase、Neon など）。
SSL モードは `require`（既定）のままにするか、プロバイダーの CA 証明書があれば `verify-full` にします。データベースのファイアウォールで
このコンピューターのアドレスを許可してください。Interlatch には、データベースを所有するか、そこにスキーマを作れる専用のユーザーを用意します。

## 入れる内容

`[mirror] include` でどこまで入れるかを決めます。別のものを選ばない限り `knowledge` です：

| | `knowledge`（既定） | `everything` |
| --- | --- | --- |
| セッション：エージェント、プロジェクト、時刻、モデル、トークン数、費用、結果、要約と分析 | ✓ | ✓ |
| 教訓（`knowledge`）、ナレッジベース（`project_kb`）、週次の振り返り、用語集 | ✓ | ✓ |
| 成果物、各セッションが触ったファイル、API 呼び出しごとのトークン使用量、サブエージェント、分析の記録 | ✓ | ✓ |
| 各セッションの最初と最後のプロンプト、サブエージェントのタスクの説明 | | ✓ |
| トランスクリプト：すべてのメッセージ（`events`）とツール呼び出し（`tool_calls`） | | ✓ |

- **秘密情報。** `everything` では、プロンプトとトランスクリプトの秘密情報を、分析の前と同じように伏せ字にしてから送ります
  （[伏せ字にされるもの](privacy.md#伏せ字にされるもの)）。伏せ字はパターンで判定するため、特徴のない秘密情報は見逃すことがあります。
  `everything` は、トランスクリプトを預けてよいデータベースにだけ選んでください。
- **タイトル。** `knowledge` では、最初のプロンプトの書き出ししかタイトルがないセッションは、タイトルなしで送ります。
- **除外したプロジェクト。** 除外したプロジェクト（`[sources] exclude_projects`）のセッションとその教訓は送りません。あとから除外した
  プロジェクトは、次の書き込みでコピーから消えます。
- **取り込んだチャット。** 取り込んだ claude.ai と ChatGPT のチャットもアーカイブの一部なので送ります。本文を送るのは `everything` のときだけです。
- **送らないもの。** Interlatch 自身の管理情報（トランスクリプトのディスク上の場所、再試行の回数、ハブの鍵や利用者）は送りません。

`everything` から `knowledge` に戻すと、次の書き込みでトランスクリプトのテーブルとプロンプトの列をコピーから消します。その領域は、
Postgres が次にテーブルをバキュームしたときに解放されます。

テーブル名は Interlatch のデータベースと同じで、スキーマ `chronicle`（`[mirror] schema`）に入ります。時刻は `timestamptz`、`*_json` の列は
`jsonb` で、各テーブルにはいつものキーがあります。キーのあるテーブルには、次の書き込みで比べる `_hash` 列と `_synced_at` 列もあります。
`_mirror` テーブルには、どのコンピューターがどの `include` で書き込み、最後にいつ書いたかが入ります。

## 最新に保つ仕組み

- **バックグラウンド実行のたびに**（15 分ごと）、コピーとデータベースを比べて、変わったものだけを書きます。分析されたり続きが
  あったりしたセッションは、そのセッションの行（使用量、ファイル、成果物、トランスクリプト）ごと書き直します。こちらで消えた行は
  向こうでも消えます。新しいものがなければ、書き込みは 1 秒ほどで終わります。
- **最初の書き込み**ではすべてをコピーします。約 4,000 セッションのアーカイブを同じコンピューターのデータベースに書く場合、`knowledge`
  で数秒、`everything` で 1 分ほどです。インターネット越しではもっとかかります。
- **Postgres につながらないとき**も、コンピューター上のものには影響しません。エラーは Storage ページと `interlatch mirror` に出て、
  次の実行で追いつきます。
- **`interlatch mirror sync --full`** は、コピーの中身にかかわらず、すべての行を書き直します。
- **オフにする**（Storage ページで **Off**、または `interlatch config set mirror.to ""`）と書き込みが止まります。コピーと設定は残るので、
  もう一度オンにするときにパスワードは要りません。

## スキーマは 1 台に 1 つ

コンピューターはそれぞれ自分のスキーマに書きます。Interlatch は、ほかのコンピューターが書いているスキーマを使いません。2 台が互いの
コピーを上書きすることはありません。複数のコンピューターを 1 つのデータベースにコピーするときは、それぞれ別の `[mirror] schema`
（例：`chronicle_laptop` と `chronicle_desktop`）を指定します。複数のコンピューターのセッションを 1 つのアーカイブにまとめたいときは、
ハブを使い（[スマートフォンとほかのコンピューター](devices.md#ほかのコンピューター)）、ハブのミラーを Postgres に向けます。

書き込むスキーマは Interlatch が管理します。Interlatch が作っていない列はテーブルから消すので、自分のビューやテーブルは別のスキーマに置いてください。

## 問い合わせる

```sql
-- エージェントごと・月ごとの費用
SELECT agent, date_trunc('month', started_at)::date AS month, count(*) AS sessions,
       round(sum(est_cost_usd)::numeric, 2) AS usd
FROM chronicle.sessions GROUP BY 1, 2 ORDER BY 2 DESC, 4 DESC;

-- あるプロジェクトの教訓、新しい順
SELECT kind, title, body FROM chronicle.knowledge
WHERE project_name = 'my-app' AND status = 'active' ORDER BY updated_at DESC;

-- モデルごと・日ごとのトークン数
SELECT ts::date AS day, model, sum(input_tokens + output_tokens) AS tokens
FROM chronicle.api_calls GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC;

-- postgres タグの付いた教訓と、そのプロジェクト
SELECT s.project_name, k.title FROM chronicle.knowledge k JOIN chronicle.sessions s ON s.id = k.session_id
WHERE k.tags_json ? 'postgres';
```

BI ツールには読み取り専用のロールを使います。あとから Interlatch が作るテーブル（`everything` に切り替えたときのトランスクリプトの
テーブル）も読めるように、Interlatch のユーザーで実行してください：

```sql
CREATE ROLE bi LOGIN PASSWORD '…';
GRANT USAGE ON SCHEMA chronicle TO bi;
GRANT SELECT ON ALL TABLES IN SCHEMA chronicle TO bi;
ALTER DEFAULT PRIVILEGES IN SCHEMA chronicle GRANT SELECT ON TABLES TO bi;
```
