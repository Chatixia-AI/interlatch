# Submitting the browser extension to the Chrome Web Store

Everything the dashboard asks for, ready to paste, checked against its forms on 2026-11-02. Sign in as
`chatixia.dev@gmail.com` at <https://chrome.google.com/webstore/devconsole>. Publish **Unlisted** first; go **Public**
once a real install from the store has worked.

**Status:** 1.1.0 submitted for review as Unlisted on 2026-11-02. Item ID `dfoaabcfgmpgkfjaemipocpjhmenlheb`.

## 1. Build the ZIP

```bash
python3 packaging/chrome-web-store/make_zip.py
```

It writes `dist/interlatch-browser-extension-<version>.zip`. **New item › Choose file** takes it; for a later version,
use the **Package** page. Each published version needs a higher `version` in `manifest.json`. Build it from an up-to-date
`main` right before uploading: other changes to the extension may have landed since the last build.

## 2. Store listing

**Title and summary** come from `_locales`. **Language:** English; add Japanese as a second one, with the text below.

**Category:** Developer Tools (Productivity if that's not offered).

**Description (English):**

```text
Keep your claude.ai and ChatGPT chats in Interlatch, on your own computer, so you can search them and find them again.

It saves them as you use the sites: when a tab loads, every 5 minutes, and soon after you leave a chat.

What it reads: your chats on claude.ai and chatgpt.com, with their titles, messages and project names, using your own sign-in. Temporary chats are left out. It reads nothing on other sites.

Where it goes: only to the Interlatch dashboard on this computer (127.0.0.1). The extension has no server, no analytics and no ads, and Interlatch's authors never receive your chats.

You stay in charge: it reads nothing until you choose Start saving, says on the page when it's saving, and has a Pause button there and on the toolbar.

It needs Interlatch, a free, open-source app on your computer: https://interlatch.com/docs/install/

Interlatch is an independent project, not affiliated with Anthropic or OpenAI.
```

**Description (Japanese):**

```text
claude.ai と ChatGPT のチャットを、あなたのコンピューター上の Interlatch に保存し、あとで検索したり見つけ直したりできるようにします。

サイトを使うたびに保存します。タブを読み込んだとき、5 分ごと、チャットを離れた少しあとに動きます。

読み取るもの: claude.ai と chatgpt.com のチャット（タイトル、メッセージ、プロジェクト名）を、あなた自身のログインで読み取ります。一時チャットは対象外です。ほかのサイトは読み取りません。

送り先: このコンピューターの Interlatch ダッシュボード（127.0.0.1）だけです。拡張機能にはサーバーも分析ツールも広告もなく、Interlatch の作者にチャットが届くことはありません。

操作はあなた次第です: 「保存を開始」を選ぶまで何も読み取らず、保存中はページ上でお知らせし、ページとツールバーに一時停止ボタンがあります。

コンピューター上の無料のオープンソースアプリ Interlatch が必要です: https://interlatch.com/docs/ja/install/

Interlatch は独立したプロジェクトで、Anthropic や OpenAI とは関係ありません。
```

**Screenshots:** `screenshots/1-asks-first.png`, `2-says-where-chats-go.png`, `3-toolbar-button.png` (1280×800, 24-bit with
no alpha, from `screenshots.js`; retake them when the popup or page message changes). The form has **Localized** and
**Global** fields, both marked required: these go in both. The site in the second is a plain stand-in, so no
real chats or other companies' pages appear.

**Store icon:** `src/interlatch/browser_extension/icon-128.png`. Promo tiles are optional; listings without the small one
(440×280) are shown after those with it, so make one before going Public.

**Homepage URL:** `https://interlatch.com` (its privacy link is one click away, which the Limited Use policy asks for).
**Support URL:** `https://github.com/Chatixia-AI/interlatch/issues`

## 3. Privacy

**Single purpose:**

```text
Saves the user's own claude.ai and ChatGPT chats into the Interlatch app on the user's computer, so they can search them and find them again.
```

**Justification for `storage`:**

```text
Keeps the extension's settings (dashboard port, Paused, first-sync range, whether the user chose Start saving) and its progress (when each site last synced and which chats were already sent), so it sends only chats that changed.
```

**Host permission justification** (one box, for the sites and the dashboard together):

```text
https://claude.ai/* and https://chatgpt.com/*: reading the user's chats on these two sites is the extension's purpose. It asks each site for the chats that changed, from a tab where the user is already signed in, as the site's own page does, and shows a small message on the page saying that it's saving, with a Pause button. It runs on no other sites.

http://127.0.0.1/*: the only place the extension sends anything, the Interlatch dashboard, an app on the user's own computer. A page on https can't call it, so the service worker does. The port is the user's setting (11524 by default), so no single port can be named. Nothing leaves the computer on this connection, which is why it is plain HTTP.
```

The form warns that host permissions may mean an in-depth review, which delays publishing.

**Remote code:** **No, I am not using remote code**. The form can start with **Yes** selected, so check it. Every script is
in the package, with no `eval` or outside scripts.

**Data usage:** this list is shown publicly on the listing. It must match
[the privacy policy](../../docs/privacy.md#browser-extension) and what the extension does, or the extension can be removed.
Tick:

- **Personally identifiable information:** a chat holds no account fields such as an email or phone number, but anything
  typed into a chat is saved, and the policy says chats can include personal information.
- **Authentication information:** on ChatGPT it reads the session token to make the site's own requests, and sends it only
  to chatgpt.com.
- **Personal communications:** the chats.
- **Website content:** the same chats, as content of those sites.

Leave **Health**, **Financial and payment**, **Location**, **Web history** and **User activity** unticked.

**Certifications:** tick all three (no selling or transfer to third parties outside the approved use cases; no use unrelated
to the single purpose; no use for creditworthiness or lending). They are required to publish.

**Privacy policy URL:** `https://interlatch.com/docs/privacy/#browser-extension`. Check it loads before submitting: it's
published when the docs site next builds after this is merged.

## 4. Distribution

**Payment:** Free. **Visibility:** Unlisted. **Regions:** all.

## 5. Test instructions

Leave **Username** and **Password** empty: the extension has no login of its own, and real claude.ai or ChatGPT accounts
hold real chats. If Google asks for a test account, make a new free one with a single harmless chat, and change its
password after the review.

**Additional instructions** has a 500-character limit:

```text
Needs the free Interlatch app on the same computer:
1. Install uv, then run: uv tool install --python 3.13 interlatch && interlatch ui --open
Docs: https://interlatch.com/docs/install/
2. Install the extension. Choose Start saving on the page that opens.
3. Open claude.ai or chatgpt.com, signed in with any free account that has a chat. A message at the bottom right says it's saving.
4. The chat appears in the dashboard under Sessions.
No extension login. Plain HTTP goes only to 127.0.0.1.
```

## What a reviewer may question

- **Plain HTTP to 127.0.0.1.** The policy asks for secure transmission. Its FAQ exempts traffic to a native program on the
  same computer but doesn't mention a local HTTP server. Test instructions above explain it; if it's refused, the
  extension would have to move to native messaging.
- **The sites' internal endpoints.** The extension uses the same requests as the sites' own pages, with the user's
  sign-in, on their own chats. The policy forbids facilitating "unauthorized access"; it doesn't discuss internal
  endpoints. It paces itself (700 ms between chats, at most 40 a sync).
- **The welcome page is the consent.** Google's FAQ says disclosure and consent must be in the extension's own interface,
  not only the listing or a privacy policy, even for data that stays on the device. That's why nothing is read before
  **Start saving**. Don't remove it.
