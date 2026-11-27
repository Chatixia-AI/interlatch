# ロードマップ

[← Interlatch](README.ja.md) · [変更履歴](CHANGELOG.md) · English: [ROADMAP.md](ROADMAP.md)

Interlatch にこれから入るものは、[未完了の issue](https://github.com/Chatixia-AI/interlatch/issues) で管理しています。
変更 1 つにつき issue 1 つで、種類（Bug、Feature、Improvement、Spike）と分野（Dashboard、Hub & sharing、Sources など）の
ラベルが付いています。日付は約束していません。完成したものは[変更履歴](CHANGELOG.md)に移ります。

すべてに共通する原則が 3 つあります。Interlatch はローカルで完結します。私たちのサーバーもテレメトリーもアカウントも
ありません。分析はあなた自身のアカウントで行います。Claude Code か Codex のログイン、または IBM Bob やモデルプロバイダーの
あなた自身の API キーで、私たちが保有する API キーは使いません。そして Interlatch は、
わからないことを推測で埋めず、わからないと伝えます。

## 予定していないもの

- **ホスト型サービス、クラウド同期、テレメトリー。** Interlatch の約束は、あなた自身のログインかキーによる分析の呼び出し以外、
  何もあなたのマシンから出ないことです。コンピューター間の共有は、あなた自身のハブを通します。
- **各コンピューターが共有データベースにつなぐこと。** 書き込むのは 1 台（ハブ）だけで、ほかのコンピューターはセッションを
  ハブに送り、データベースのアドレスもパスワードも知りません。ハブはチームの記録をあなた自身の Postgres にも残せますが
  （[チームストア](docs/ja/devices.md#チームメイトのナレッジとpostgres-のチームストア)）、書き込むのはハブだけのままです。
- **私たちが保有する API キーでの分析。** 分析は常にあなた自身のアカウントで行います。コンピューターにある Claude Code か
  Codex のログイン、または IBM Bob やモデルプロバイダーのあなた自身の API キーです。Ollama なら、分析はあなたの
  コンピューターから出ません。

## 提案するには

[issue](https://github.com/Chatixia-AI/interlatch/issues/new) を開いて、何をしようとしていて、どこで Interlatch が
邪魔になったかを書いてください。機能の名前だけよりずっと役に立ちます。
