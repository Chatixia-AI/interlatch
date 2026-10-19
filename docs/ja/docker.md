# Docker でハブを動かす

[← Interlatch](../../README.ja.md) · [ドキュメント一覧](README.md)

このガイドでは、Docker が動くサーバーにチームの[ハブ](devices.md#ほかのコンピューター)を用意します。空のサーバーから、
チームメイトがプロジェクトで学んだことを共有できるようになるまでです。ハブを運用する人向けです。チームメイトは
代わりに[チームのハブに参加する](join-a-hub.md)を読んでください。

`docker/compose.yaml` は次の 3 つのコンテナーを起動します。

- **ハブ**：イメージ `ghcr.io/chatixia-ai/interlatch-hub`
- **Caddy**：ハブの前に立ち、HTTPS の証明書を取得・更新します
- **Postgres**：[チームストア](devices.md#チームメイトのナレッジとpostgres-のチームストア)。各コンピューターが学んだことを
  まとめ、チームメイトのナレッジを送り返します

ハブが受け取るのは**ナレッジだけ**です。各コンピューターは自分のセッションを自分の Claude Code、Codex、またはモデル
プロバイダーで記録・分析し、各セッションの要約とプロジェクトのナレッジだけをハブに送ります。トランスクリプト、
プロンプト、ファイルパスはコンピューターに残ります。ハブは何も分析しないので、モデルも API キーも要りません。

## 必要なもの

- **Docker が動く Linux サーバー**（SSH で接続できるもの）。CPU 2 つとメモリー 4 GB で十分です。
- **ポート 80 と 443**：ハブを使うコンピューターから届くように開けておきます。Caddy が Let's Encrypt から証明書を
  取得するのにも使います。
- **ハブの名前**：`interlatch.example.com` のように、DNS がサーバーを指す名前です。まだない場合は、サーバーの IP
  アドレスをハイフンでつなぎ `.sslip.io` を付けた名前で試せます。`172-207-25-249.sslip.io` は `172.207.25.249` を
  指す無料の名前です。社内ネットワークからしか届かない名前の場合は[自分の証明書を使う](#自分の証明書を使う)を
  参照してください。

    ハブごとに別の名前が必要です。ブラウザーは 1 つの名前の Cookie をそのすべてのポートに送るので、ポートが違っても
    同じ名前の 2 つのハブは、互いの利用者としてもう一方にサインインできてしまいます。同じサーバーで 2 つ目のハブを
    動かすときは、前に語を付けます。`team-b.172-207-25-249.sslip.io` も同じアドレスを指します
    （[ハブごとに別の名前](devices.md#ハブごとに別の名前)）。

サーバーに Docker を入れるには：

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
```

### Azure の場合

- **仮想マシンを使います**：Ubuntu、サイズは B2s 程度。Azure Container Apps や App Service は使えません。ファイルを
  ネットワーク共有の Azure Files に置くため、ハブの SQLite データベース（WAL モード）が安定して動かないからです。
- **自分の鍵を使います。** **管理者アカウント**で **既存の公開キーを使用** を選び、ハブ用に作った鍵の公開鍵を
  貼り付けます。

    ```bash
    ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_interlatch_hub -C interlatch-hub
    pbcopy < ~/.ssh/id_ed25519_interlatch_hub.pub
    ```

    代わりに **新しいキーの組の生成** を選ぶと、VM の作成の最後に **秘密キーのダウンロード** ダイアログが
    出ます。エラーではありません。そこで鍵をダウンロードしたときに初めて VM が作られます。
- **ポートを開けます**：VM の **ネットワーク** › **受信ポートの規則を追加** で、80 と 443 をそれぞれ追加します。
- **名前を付けます**：VM のパブリック IP アドレス › **構成** › **DNS 名ラベル** で
  `<ラベル>.<リージョン>.cloudapp.azure.com` になります。上の sslip.io の名前でもかまいません。

接続は `ssh -i ~/.ssh/id_ed25519_interlatch_hub azureuser@<VM の IP アドレス>` です。

## ハブを用意する

手順 1〜3 はサーバーで実行します。

### 1. ファイルをダウンロードする

```bash
mkdir ~/interlatch-hub && cd ~/interlatch-hub
base=https://raw.githubusercontent.com/Chatixia-AI/interlatch/main/docker
curl -fsSL "$base/compose.yaml" -o compose.yaml
curl -fsSL "$base/Caddyfile" -o Caddyfile
curl -fsSL "$base/.env.example" -o .env
```

### 2. 設定を埋める

チームストアのデータベース用のパスワードを作ってコピーし、`.env` を開きます。

```bash
openssl rand -hex 24
nano .env
```

次の 4 行を設定します。nano では Ctrl+O のあと Enter で保存、Ctrl+X で終了します。

```bash
INTERLATCH_DOMAIN=interlatch.example.com  # ハブの名前
INTERLATCH_ADMIN_EMAIL=you@example.com    # 最初の管理者（あなた）
INTERLATCH_ADMIN_NAME=You
POSTGRES_PASSWORD=...                     # コピーしたパスワード
```

### 3. 起動する

```bash
docker compose up -d
docker compose logs hub
```

ログに、ハブの最初の管理者としてのあなたの招待が出ます。

```text
Added You (you@example.com) as this hub's admin. The invite works once, for 7 days:

  In a browser, to open the hub's dashboard:  https://interlatch.example.com/signin?code=ABCD-EFGH-JKLM
  Or on their computer, to join it:           interlatch hub join https://interlatch.example.com --code ABCD-EFGH-JKLM --share knowledge
```

これが表示されるのは**ハブの最初の起動のときだけ**です。再起動しても利用者はそのままで、招待は表示されません。
見逃したときや期限が切れたときは、新しく作ります。

```bash
docker compose exec hub interlatch hub invite you@example.com
```

ハブに管理者ができるまで、コンテナーは何も提供しません。利用者のいないハブにはサインインがなく、そのダッシュボードは
プロキシ経由の人には誰にも応答しないため、`INTERLATCH_ADMIN_EMAIL` なしで起動するとエラーで止まり、何も提供しません。

### 4. サインインする

招待の**リンク**をブラウザーで開きます。これでハブの管理者です。サイドバーの **Team** に、利用者、プロジェクト、
コンピューター、チームストアがあります。どのページにも、ハブであることが表示されます。上端の緑の帯（名前とアドレス）と、
ステータスバーの行です。専用のハブなので、**Sync** や **Settings › Sources** のように個人のコンピューターにだけ必要な
ものはダッシュボードにありません。

## プロジェクトを追加する

ダッシュボードで **Team › Projects** を開き、**New project** にプロジェクト名（例：`Website`）を入力して
**Create project** を選びます。

古いイメージにはこのボタンがありません。その場合、プロジェクトはプロジェクト名を付けたハブのコンピューター上のフォルダーで、
サーバーの `~/interlatch-hub` で作ります。

```bash
docker compose exec hub mkdir -p /data/projects/Website
docker compose exec hub interlatch hub project add /data/projects/Website
docker compose exec hub interlatch hub project list
```

あとは各コンピューターがそれぞれのフォルダーをプロジェクトに加えます（次の節）。あるコンピューターが git リポジトリのセッションを送ると、同じリポジトリのほかの
クローンは自動でそのプロジェクトに入ります。

一部のプロジェクトに限られたメンバーには、新しいプロジェクトを加えるまでそれが見えません。その人の
**Join a project** では「一致するものはありません。」となります。**Team › People** で、またはサーバーで、その人のプロジェクトを
変えてください。一覧はまるごと置き換わるので、残すプロジェクトもすべて指定します。`Mobile` が見えている人なら：

```bash
docker compose exec hub interlatch hub people
docker compose exec hub interlatch hub access <メールアドレスか ID> --project Mobile --project Website
```

## 自分のコンピューターをつなぐ

自分のコンピューターも、チームメイトと同じように参加します。専用の招待と、その招待が表示する `interlatch hub join`
コマンドを使います。まず、何を共有するかを決めます。

- **そのプロジェクトだけ。** そのプロジェクトだけが見える人としてコンピューターを招待します。ブラウザーは管理者の
  サインインのままです。

    ```bash
    docker compose exec hub interlatch hub invite "Your Mac" --project Website
    ```

- **分析したすべて。** `docker compose exec hub interlatch hub invite you@example.com` で自分の新しいコードを作り、
  それで参加します。分析したコーディングエージェントのセッションすべての要約とプロジェクトのナレッジが送られ、
  すべてのプロジェクトがハブに現れます。

どちらの場合も、ChatGPT や claude.ai から取り込んだチャットはコンピューターから出ません。

招待はそれぞれ 2 つのものを表示し、そのコードは**どちらか一方に一度だけ**使えます。

- **`interlatch hub join …` コマンド**はコンピューターをつなぎます。ターミナルで実行します。
- **`https://…/signin?code=…` リンク**はダッシュボードを開きます。ブラウザーで開きます。

リンクを開くとコードは使い切られ、コンピューターには新しいコードが必要になります。

自分のコンピューターで：

```bash
interlatch hub disable    # このコンピューター自体がハブの場合だけ
interlatch hub leave      # ほかのハブに参加している場合だけ
interlatch hub join https://interlatch.example.com --code XXXX-XXXX-XXXX --share knowledge --no-push
interlatch hub add-folder ~/Projects/Website --project Website
```

`add-folder` はすぐに送ります。`3 sessions shared … 300 excluded` のような結果は、そのプロジェクトの分析済みの
セッション 3 件がハブに送られ、ほかの 300 件はコンピューターに残ったことを意味します。ダッシュボードの **Projects** を
再読み込みすると表示されます。

## チームを招待する

サーバーで、1 人ずつ招待します。

```bash
docker compose exec hub interlatch hub invite "Yuma" --email yuma@example.com --project Website
```

リンクではなく `interlatch hub join …` の行を、[チームのハブに参加する](join-a-hub.md)と一緒に送ってください。
残りの手順はそのページが案内します。コードは一度しか使えないので、両方は送らないでください。コンピューターが参加したあとは、
本人の Interlatch からダッシュボードを開けます：**Settings › Devices › Open the hub's dashboard**、または
`interlatch hub signin`。スマートフォン用や、ダッシュボードを見るだけの人には、
`docker compose exec hub interlatch hub invite yuma@example.com`（または **Team › People** のその人の行の **New invite**）で
新しいコードを作り、その**リンク**を送ります。

ダッシュボードの **Team › People** でも、ロールと見えるプロジェクトを選んで招待できます。

## よく使うコマンド

サーバーの `~/interlatch-hub` で：

| すること | コマンド |
|---|---|
| 利用者と、それぞれに見えるものを一覧する | `docker compose exec hub interlatch hub people` |
| 送ってくるコンピューターと、最後に送った時刻を見る | `docker compose exec hub interlatch hub status` |
| プロジェクトを一覧する | `docker compose exec hub interlatch hub project list` |
| すでにいる人の新しいコードを作る | `docker compose exec hub interlatch hub invite <メールアドレスか ID>` |
| 見えるプロジェクトを変える | `docker compose exec hub interlatch hub access <メールアドレスか ID> --project <名前>…`（残すプロジェクトもすべて指定。一覧は置き換わります） |
| 利用者を外す | `docker compose exec hub interlatch hub remove <メールアドレスか ID>` |
| ハブのログを読む | `docker compose logs hub` |

新しいコードを作るときは、メールアドレスか、`hub people` が表示する ID を使ってください。名前をもう一度入力すると、
2 人目の利用者が加わります。

コンテナーのハブはあなたのリポジトリでセッションを実行しないので、その git リモートを知りません。参加したら、メンバーは
プロジェクトごとに自分のフォルダーを追加します。追加しないと、そのコンピューターは何も共有しません：
`interlatch hub add-folder ~/work/demo-app --project demo-app`。ほかのフォルダーのセッションはそのコンピューターに残ります
（[ナレッジだけを共有する](devices.md#ナレッジだけを共有する)）。コンピューターが送ったものを取り消すには：
`docker compose exec hub interlatch hub purge bob@example.com --project demo-app`
（[コンピューターが送ったものを取り消す](devices.md#コンピューターが送ったものを取り消す)）。

## ハブが受け取るもの

コンテナーは最初の起動時に次の 2 つを設定します。どちらも管理者があとで変更できます。

| 設定 | 値 | 意味 |
|---|---|---|
| `[hub] accept` | `"knowledge"` | トランスクリプトを送ってくるコンピューターを受け付けません。 |
| `[hub] shared_token` | `false` | コンピューターはハブの共有トークンではなく、それぞれの招待で参加します。 |

ハブにトランスクリプトを受け取らせて自分で分析させるには、モデルプロバイダーの API が必要です。イメージには
Claude Code も Codex も入っていないので、ここでは使えません。

```bash
docker compose exec hub interlatch config set hub.accept everything
docker compose exec hub interlatch config set analysis.backend anthropic
docker compose exec hub interlatch config set-key anthropic   # キーを尋ねられます
docker compose restart hub
```

ほかのプロバイダーは[モデルプロバイダー](analysis.md#モデルプロバイダー)を参照してください。

## 設定

コンテナーは起動のたびに、次の変数から `config.toml` を設定します。値のある変数は `config.toml` より優先され、
未設定または空の変数は `config.toml` をそのままにします。それ以外の設定は `config.toml` に残ります。
`docker compose exec hub interlatch config set ...` で変更し、ハブを再起動してください。どの変数も、名前が変わる前の
名前（`INTERLATCH_HUB_URL` なら `CHRONICLE_HUB_URL`）でも読み込むので、以前の `.env` もそのまま使えます。

| 変数 | 既定値 | |
|---|---|---|
| `INTERLATCH_HUB_URL` | （必須） | コンピューターとブラウザーがハブに届くアドレス。`[hub] address` になり、そのホスト名が `[server] allowed_hosts` に加わります。`compose.yaml` は `INTERLATCH_DOMAIN` から設定します。 |
| `INTERLATCH_ADMIN_EMAIL` | （最初は必須） | 最初の管理者。ハブに利用者がいないときに追加されます。 |
| `INTERLATCH_ADMIN_NAME` | メールアドレスの @ より前 | その人の名前。 |
| `INTERLATCH_HUB_NAME` | `Interlatch hub` | ダッシュボードに表示される名前（`[hub] name`）。 |
| `INTERLATCH_HOST` | `0.0.0.0` | ダッシュボードが待ち受けるアドレス。`compose.yaml` は `127.0.0.1` にします。ハブは Caddy のネットワークを共有するので、届くのは Caddy だけです。 |
| `INTERLATCH_PORT` | `11524` | ダッシュボードのポート。 |
| `INTERLATCH_ALLOWED_HOSTS` | | ダッシュボードが応答するほかの名前（カンマ区切り）。 |
| `INTERLATCH_TRUSTED_PROXIES` | `127.0.0.1, ::1` | ハブが `X-Forwarded-Proto` を信じるプロキシのアドレス（カンマ区切り）。自分のプロキシからコンテナーに転送するときに設定します（[下記](#自分のプロキシを使う)）。 |
| `INTERLATCH_TEAM_STORE` | | `postgres` にすると、チームストアを Postgres に置き、`PGHOST`、`PGDATABASE`、`PGUSER`、`PGPASSWORD`、`PGSSLMODE` で接続します。`compose.yaml` はすべて設定済みです。 |
| `INTERLATCH_WORK_MINUTES` | `15` | ハブが送られてきたものを読み込み、バックグラウンドの処理を実行する間隔（分）。`0` で止めます。 |

ハブが保持するものはすべて `hub-data` ボリューム（コンテナー内の `/data`）にあります。チームストアのデータは
`postgres-data` ボリュームにあります。

管理者は、ハブのダッシュボードの **Team › Hub settings** でもハブの設定を確認できます：名前、アドレス、
**Knowledge only**、各プロジェクトのナレッジベースを書くモデル、更新、下記のバックアップのコマンドです。
名前とアドレスは、`INTERLATCH_HUB_NAME` や `INTERLATCH_HUB_URL` が決めている間はそこで変更できません（起動のたびに
書き直されるため）。`.env` の変数を変えて `docker compose up -d` を実行してください。

## 自分の証明書を使う

Caddy は Let's Encrypt から証明書を取得します。そのためには Let's Encrypt がサーバーのポート 80 か 443 に届く必要が
あります。社内ネットワークからしか届かない名前の場合は、会社の証明書を Caddy に渡します。`compose.yaml` の
隣に置いた `compose.override.yaml` でマウントします。Compose はこれを `compose.yaml` と一緒に読み込みます（[更新](#更新する)で
`compose.yaml` を取り直しても、こちらはそのまま残ります）。

```yaml
services:
  caddy:
    volumes:
      - ./certs:/certs:ro
```

そして `Caddyfile` に `tls` の行を加えます。

```text
{$INTERLATCH_DOMAIN} {
	tls /certs/interlatch.crt /certs/interlatch.key
	reverse_proxy 127.0.0.1:11524
}
```

`tls internal` にすると、Caddy が自分で証明書を作ります。その場合、ハブを使うすべてのコンピューターとブラウザーが
Caddy のルート証明書を信頼する必要があります。

## 自分のプロキシを使う

`compose.yaml` を使わずにイメージだけを動かし、自分の HTTPS プロキシ（nginx、ロードバランサー）を前に置くこともできます。
[Tailscale を使わずにハブにつなぐ](devices.md#tailscale-を使わずにハブにつなぐ)と同じ構成です。

```bash
docker run -d --name interlatch-hub --restart unless-stopped -p 11524:11524 -v interlatch-hub:/data \
  -e INTERLATCH_HUB_URL=https://interlatch.example.internal -e INTERLATCH_ADMIN_EMAIL=you@example.com \
  -e INTERLATCH_TRUSTED_PROXIES=10.0.4.12 ghcr.io/chatixia-ai/interlatch-hub
```

`INTERLATCH_TRUSTED_PROXIES` には、コンテナーから見たプロキシのアドレスを設定します。こうするとハブはプロキシの
`X-Forwarded-Proto` を信じ、サインインの Cookie に `Secure` を付けます。同じサーバー上のプロキシが公開ポートに
つなぐ場合、たいていは Docker のブリッジのゲートウェイ `172.17.0.1` です。ポート 11524 にはプロキシだけが
届くようにしてください。チームストアがないと、共有したコンピューターにチームメイトのナレッジは返りません。
Postgres を加えるには `INTERLATCH_TEAM_STORE=postgres` と `PG*` 変数を設定します。

## 更新する

```bash
curl -fsSL https://raw.githubusercontent.com/Chatixia-AI/interlatch/main/docker/compose.yaml -o compose.yaml
docker compose pull
docker compose up -d
```

`compose.yaml` は Caddy と Postgres のバージョンとダイジェストを固定しているので、イメージが知らないうちに変わることは
ありません。新しいものはリリースされるたびにこのリポジトリの `compose.yaml` に入るので、取得の前に取り直してください。
自分で加える変更は `compose.yaml` ではなく `compose.override.yaml` に書きます（[自分の証明書を使う](#自分の証明書を使う)と同じです）。
Postgres は 17 のままです。新しいメジャーバージョンは、移行しないと古いバージョンのデータを読めません。

バージョンを固定するには、`.env` の `INTERLATCH_VERSION` にリリースのバージョン番号を設定します。ダッシュボードからは
コンテナーをアップデートできません。**Status** には、代わりに新しいイメージを取得するよう表示されます。

**Chronicle として用意したハブ**も同じ手順で移行できます。新しい `compose.yaml`（`Caddyfile` も、変更していなければ）を
ダウンロードし、`.env` はそのままにして、上と同じく取得と起動をします。`docker compose logs hub` に
`Interlatch hub is up` と表示されます。データは移動しません。ボリューム、Postgres のデータベースとユーザー（`chronicle`）、
`/data` のデータはそのままです。`.env` で名前が変わる前のバージョンを固定している（`CHRONICLE_VERSION=0.13.0`）場合は、
その行を削除してください。`interlatch-hub` のイメージにはそれほど古いタグがありません。古い `compose.yaml` のまま取得する
だけのハブも動き続けます。`ghcr.io/chatixia-ai/chronicle-hub` にも各リリースが届きます（[Chronicle からの移行](moving-from-chronicle.md)）。

## バックアップする

ハブの動作中にデータベースをコピーし、チームストアをダンプします。

```bash
docker compose exec hub python -c "import sqlite3; sqlite3.connect('/data/chronicle.db').backup(sqlite3.connect('/data/backup.db'))"
docker compose cp hub:/data/backup.db ./interlatch-backup.db
docker compose exec -T postgres pg_dump -U chronicle chronicle > team-store.sql
```

ハブの動作中に `chronicle.db` そのものをコピーしないでください。コピーが壊れることがあります。

## トラブルシューティング

**ログに招待が出ない。** 最初の管理者の招待は、ハブの最初の起動のときだけ表示されます。
`docker compose exec hub interlatch hub invite you@example.com` で新しく作ってください。

**起動の直後にページがエラーになる。** Caddy はハブより先に起動し、ハブが起動するまでの数秒間はエラーを返します。
ページを再読み込みしてください。

**コンピューター用のコードをブラウザーで開いてしまった、または期限が切れた。** その人のメールアドレスか ID で新しい
コードを作ります（例：`docker compose exec hub interlatch hub invite 3`）。

**`interlatch hub join` が、このコンピューターはハブだと言う。** 先にそのコンピューターで `interlatch hub disable` を
実行します。保持しているものはそのまま残りますが、ほかのコンピューターはそこへ送れなくなります。

**自分のダッシュボード `http://127.0.0.1:11524/` が応答しなくなった。** VS Code の Remote-SSH でサーバーにつなぎ、
ハブのログを読んだあとに起こります。0.13.0 のイメージは `Chronicle dashboard: http://127.0.0.1:11524/` と表示し、
VS Code がそのポートを自分のコンピューターへ転送して、自分のダッシュボードの前に立ってしまいます。サーバーにつないだ
VS Code のウィンドウ（隅に **SSH: …** と表示されるもの）で **Ports** を開き、11524 を右クリックして **Stop
Forwarding Port** を選びます。VS Code の設定に `"remote.portsAttributes": { "11524": { "onAutoForward": "ignore" } }` を
加えると、再発しません。以降のイメージは代わりに `Interlatch hub is up: <ハブのアドレス>`（名前が変わる前は `Chronicle hub is up`）と表示します。

**HTTPS がつながらない。** 名前がサーバーの IP アドレスを指しているか、ポート 80 と 443 が開いているか、
`docker compose logs caddy` の内容を確認してください。社内ネットワークからしか届かないサーバーには Let's Encrypt が
届きません。[自分の証明書](#自分の証明書を使う)を使ってください。sslip.io の名前をブロックする社内ネットワークも
あります。その場合は自分の名前を使ってください。
