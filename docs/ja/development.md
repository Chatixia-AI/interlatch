# 開発

[← Interlatch](../../README.ja.md) · [ドキュメント一覧](README.md)

## テストとローカルへのインストール

```bash
uv sync && uv run pytest -q        # ~300 tests, ~40 s: a fake `claude` binary and synthetic Codex, Copilot, Bob and Antigravity stores
# redeploy: --reinstall picks up uncommitted edits too (uv rebuilds on its own only when pyproject.toml, the commit or a tag changes)
uv tool install --force --reinstall --python 3.13 . && interlatch install   # install restarts the agents
```

## macOS アプリ

`uv run --extra app interlatch app` でチェックアウトから起動できます（Open at Login など、バンドルでしか意味のない
メニュー項目は非表示）。`./packaging/macos/build.sh` は `dist/Interlatch.app` と
`dist/Interlatch-<version>-<arch>.dmg` をビルドします（PyInstaller、約 30 秒。`packaging/macos/Interlatch.spec`）。1 つのバイナリが、
引数なしではアプリとして、引数ありでは CLI として動き、フックと MCP サーバーはこれを CLI として実行します。署名なしのビルドはアドホック
署名となり、ビルドした Mac でのみ動きます。配布するには `INTERLATCH_CODESIGN_IDENTITY`（Developer ID
Application 証明書）と `NOTARY_KEYCHAIN_PROFILE`（`xcrun notarytool store-credentials` で作成）を設定すると、スクリプトが
DMG の署名・公証・ステープルまで行います。`uv run --group build python packaging/macos/make_icon.py` で、
コミット済みの `packaging/macos/icon-3d.webp`（`packaging/icons3d/render.py` で描画）から macOS の `.icns`、README のアイコン、ダッシュボードのロゴ、
favicon、スマートフォンのホーム画面用アイコンを再生成できます。PNG はどの OS でも生成でき、`.icns` には macOS の
`iconutil` が必要です。`desktop.py` は
pywebview の Cocoa アプリデリゲートを拡張しているため、pywebview を `<7` に固定しています。

## 継続的インテグレーション

`.github/workflows/ci.yml` は、すべてのプルリクエストと `main` へのプッシュで動きます。テストスイート（`test`。`main` の
必須チェック）、pre-commit のフック（`lint`）、ロックした依存関係への `pip-audit`（`audit`）です。Dependabot が `uv.lock` とワークフローのアクションの更新を
毎週提案します。アクションはコミットハッシュで固定しています（バージョンは横のコメントにあります）。リポジトリの設定で、
GitHub のシークレットスキャン、プッシュ保護、CodeQL のコードスキャンを有効にしています。

### pre-commit のフック

一度 `uvx pre-commit install` を実行すると、`.pre-commit-config.yaml` がコミットごとに確認します。マージの印、YAML と TOML、
600 KB を超えるファイル、秘密鍵などのシークレット（gitleaks）、行末の空白と最後の改行、`ruff check`、`uv.lock` と
`pyproject.toml` の一致、ワークフロー（zizmor）、ダッシュボードの JavaScript が読めること（`node --check`）です。Ruff は
バグになりそうな箇所だけを見ます（pyflakes と bugbear）。コードは独自のレイアウトを保つので、整形はしません。
`uvx pre-commit run --all-files` ですべてを手動で実行できます。偽のキーが必要なテストは、その行に `# gitleaks:allow` を付けます。

## リリース

バージョンはどこにも書きません。git のタグから決まります（hatch-vcs）。タグの付いたコミットはそのバージョン
（`v0.7.0` → `0.7.0`）、その後のコミットは次のパッチの開発版（`0.7.1.dev3+g1a2b3c4`）としてビルドされ、ソースの
チェックアウトでは Status ページにこれが表示されます。

リリースするには、利用者が気づく変更を含むプルリクエストごとに、変更履歴の 1 行を書いたファイルを `changelog.d/` に
追加しておき（[changelog.d/README.md](https://github.com/Chatixia-AI/interlatch/blob/main/changelog.d/README.md)。
**Changelog** チェック `.github/workflows/changelog.yml` は、出荷物を変えるのにこのファイルがないプルリクエストを、
`no-changelog` ラベルがない限り失敗させます）、`main` で **Actions → Release →
Run workflow** を実行して `patch`、`minor`、`major` のどれかを選びます。`.github/workflows/release.yml` が最新のタグから
次のバージョンを決め、テストを実行し、タグと GitHub リリースを作成し（前のタグ以降に追加された `changelog.d/` の
ファイルがリリースノートになります）、
`interlatch` を PyPI に公開し（Trusted Publishing、環境 `pypi`）、ハブのイメージ
`ghcr.io/chatixia-ai/interlatch-hub` を公開し（バージョンと `latest` のタグ、amd64 と arm64、その PyPI のリリースから
ビルド）、DMG をリリースに添付します（`MACOS_*` /
`APPLE_*` シークレットが設定されていれば署名・公証済み。詳細はワークフローの先頭を参照）。最後に、それらのファイルの
内容を `CHANGELOG.md` の `## <version> (<date>)` に移してファイルを削除するプルリクエストを開きます。マージはいつでも
構いません。タグの後にマージされたプルリクエストのファイルはこのリリースに含まれず次のリリースに入り、次のリリースも
このプルリクエストを待ちません。このリポジトリで
GitHub Actions がプルリクエストを作れない設定の場合は、公開を終えたあとの最後のステップが失敗し、そのプルリクエストを
手で開くためのリンクを示します。
既定の `dry run` はテストを実行して DMG をワークフローの成果物として保存するだけで、何も公開しません。
リリース前にハブのイメージを試すには、`uv build --wheel -o docker/wheels && docker build -t interlatch-hub docker`
で PyPI の代わりにこのチェックアウトからビルドします（[Docker でハブを動かす](docker.md)）。CI の `docker` ジョブも
同じようにビルドし、起動することを確かめます。
GitHub で `v<version>` タグのリリースを手動で公開する方法も引き続き使えます。

### 実機の Mac でメニューバーアイコンを確認する

テストではメニューバーを確認できません。アイコンを表示するかどうかは macOS がディスプレイごとに決め、あとから隠すこともあります。
`menubar.py`、メニューバーのスイッチ、ダッシュボードの再起動方法に関わるリリースの前には、ノッチのある MacBook に外部ディスプレイを
つなぎ、PyPI と同じ形のインストール（`uv tool install`）で、手で確認します。各手順のあとに、すべてのディスプレイのメニューバーに
アイコンがあることを確かめてください：

1. **Settings › Status › Recording** のスイッチをオンにする（一度は `app` エクストラなしで、インストールされることも確かめる）。
2. **Status › Updates** でアップデート（または **Reinstall**）を実行する。ダッシュボードが再起動します。
3. Mac をスリープさせて復帰させる。外部ディスプレイをつないだまま蓋を閉じ、開く。
4. 外部ディスプレイを外し、つなぎ直す。
5. アイコンがノッチの裏に隠れるまでメニューバーを埋め、Status の説明文を確認する。
6. スイッチをオフにする。すべてのディスプレイからアイコンが消えます。

### 最初のリリースの前に一度だけ必要な設定

1. PyPI で *pending publisher* を追加します（Account → Publishing）：プロジェクト `interlatch`、オーナー
   `Chatixia-AI`、リポジトリ `interlatch`、ワークフロー `release.yml`、環境 `pypi`。PyPI はリポジトリを名前で
   照合するので、リポジトリの名前を変えたら、PyPI 側でパブリッシャーを更新してください。
2. GitHub リポジトリで `pypi` という名前の環境を作成します（Settings → Environments）。
3. 署名・公証済みの DMG を配布するには（Apple Developer Program への加入が必要）：*Developer ID Application*
   証明書を鍵ごと `.p12` として書き出し、シークレット `MACOS_CERT_P12`（ファイルの base64）、
   `MACOS_CERT_PASSWORD`、`MACOS_CODESIGN_IDENTITY`、`APPLE_ID`、`APPLE_TEAM_ID`、`APPLE_APP_PASSWORD`
   （account.apple.com で発行するアプリ用パスワード）を追加します。これらがない場合、DMG はアドホック署名となり、
   利用者は「プライバシーとセキュリティ」で許可する必要があります。
4. Settings → Actions → General → **Allow GitHub Actions to create and approve pull requests** をオンにして、
   リリースが変更履歴のプルリクエストを自分で開けるようにします。オフのままだと、リリースのたびに最後のステップが
   失敗し、開くためのリンクを示します。

## ドキュメントサイト

<https://interlatch.com/docs/> は `docs/` と README から MkDocs Material でそのまま生成されます。`README.md` と
`README.ja.md` がホームページになり、`docs/` の外を指すリンクは `docs/_site/hooks.py` が GitHub へのリンクに書き換えます。
ランディングページを含むサイト全体は [Chatixia-AI/chronicle-site](https://github.com/Chatixia-AI/chronicle-site) にあり、
このドキュメントをそのままビルドしてすべてを公開します。`.github/workflows/docs.yml` はプルリクエストごとにビルドを確認し、
`main` でドキュメントが変わると chronicle-site に再ビルドを依頼します（毎日の再ビルドもあります）。

```bash
uv run --only-group docs mkdocs serve            # http://127.0.0.1:8000/ でプレビュー（保存で再読み込み）
uv run --only-group docs mkdocs build --strict     # CI と同じ：リンクやアンカーが壊れていると失敗
```

## 図

`docs/diagrams/` の図は `.excalidraw.svg` ファイルです。画像として表示され、Excalidraw の VS Code 拡張機能
（`pomdtr.excalidraw-editor`）または excalidraw.com で編集できます。保存すると同じファイルに書き戻されます。

## コードの構成

コードは `src/interlatch/` の `interlatch` パッケージです（`src/chronicle/` は、改名前のインストールがまだ実行する
`python -m chronicle` を転送するだけです）。

- `core/`：設定、セッションの記録、パス、翻訳、エージェント名、料金、伏せ字処理、このコンピューターの識別子、
  セッションが作ったものの見分け方
- `storage/`：SQLite のアーカイブ（スキーマ、マイグレーション、接続）と、任意の Postgres ストア
- `sources/`：エージェントごとの読み取り（`claude/`、`codex/`、`copilot.py`、`bob.py`、`antigravity.py`）と、
  チャットのエクスポートごとの読み取り（`claude/export.py`、`chatgpt.py`）
- `artifacts/`：セッションが作ったもの、その今の状態、開くこと
- `ingestion/`：すべてのソースの同期（`service.py`）。生ファイルのアーカイブ、解析したセッションの保存、チャットの
  取り込み、エージェントのメモリーと履歴、忘れること
- `connectors.py`（ソース）、`digest.py` / `analyze.py` / `llm.py`（分析）、`synthesize.py`（ナレッジベース）、
  `diagram.py`（アーキテクチャ図）、`glossary.py`、`reviews.py`、`worker.py`（キュー）
- `server.py` ＋ `web/`（ダッシュボード）、`mcp_server.py`、`export_md.py`、`hooks.py` / `install.py`、`desktop.py`
  （macOS アプリ）、`cli.py`。`packaging/macos/` がアプリをビルドします

ダッシュボードのページは Content-Security-Policy（`server.py` の `PAGE_CSP`）の下で動き、スクリプトとスタイルは自分のファイルの
ものしか使えません。`index.html` にインラインの `<script>` や `on…=` 属性を加えないでください。また `h()` に渡すスタイルは
文字列ではなくオブジェクト（`style: { "--h": "40px" }`）にします。文字列は style 属性になり、ポリシーが拒否します。
どちらも `tests/test_security.py` が確かめます。

## 変更を試す

`./dev.sh` は、チェックアウトのコードでダッシュボードを起動します。データは `~/.interlatch-sandbox/dev-sh` にコピーした
アーカイブを使うため、自分の `~/.interlatch` やインストール済みの Interlatch には触れません。設定は、記録・分析・共有を
一切しないものです。`--app` で macOS アプリのウィンドウを開き、`--menu-bar` でブラウザのダッシュボードに[メニューバーアイコン](install.md#メニューバーアイコン)を
加え（ログイン項目と同じ表示）、`--demo` で[デモデータ](#デモデータ)を使い、`--fresh` で
アーカイブをコピーし直し、`--tree ../agents-chronicle-<topic>` でほかのワークツリーのコードを動かします。サーバーは起動時に
一度だけ web のファイルを読むため、`app.css` や `app.js` を編集したら再起動してください。

```bash
./dev.sh                                  # http://127.0.0.1:8797/（使用中なら次の空きポート）
./dev.sh --tree ../agents-chronicle-<topic> --app
```

## デモデータ

`docs/demo/make_demo.py` は、架空のセッションから Interlatch のホームを作ります：5 つのプロジェクトと約 6 週間分の作業を持つ
架空の開発者です。合成した Claude Code のトランスクリプトを書き出し、それに対して実際の処理（同期、分析、ナレッジベース、用語集、
週次の振り返り）を実行します。代役の `claude` が、手書きの要約とナレッジで各分析に答えるため、費用はかからず、ログインも不要です。
`docs/images/` のスクリーンショットはこのデータから作られています。

```bash
uv run python docs/demo/make_demo.py /tmp/interlatch-demo
INTERLATCH_HOME=/tmp/interlatch-demo/home uv run python -m interlatch ui --port 8898 --open
```

README の GIF とツアー動画もこのデータから作ります。そのダッシュボードを動かしたまま `docs/demo/record_demo.py` を実行すると、
ヘッドレスの Chromium が決まった順にダッシュボードを巡ります（`ffmpeg` が PATH に必要です）。ツアー全体は git が無視する
`docs/images/demo.mp4` に、その数秒は大きなファイルを止めるフックが許す 600 KB 以内で `docs/images/demo.gif` に書き出されます。
README は動画をリリースのアセットとしてリンクするので、そこにアップロードします：

```bash
uv run --with playwright python docs/demo/record_demo.py --port 8898
gh release upload v<latest> docs/images/demo.mp4 --clobber
```

そのあと README の 2 つのリンクをそのリリースに向けます。
