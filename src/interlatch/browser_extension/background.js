/* Interlatch: save web chats. The service worker: the one part that talks to the dashboard on this computer.

capture.js runs in claude.ai and chatgpt.com tabs and reads the chats there; a page on https can't call
http://127.0.0.1, so it hands each chat here and this sends it to POST /api/capture (the dashboard's
chat_import.capture). The dashboard takes it only from this computer, with the X-Interlatch header no web page can
send it. */
"use strict";

const DEFAULT_PORT = 11524;
const WAIT = 60e3; // a dashboard that hasn't answered by then is stuck: the chat goes again on a later sync
const t = (key, ...subs) => chrome.i18n.getMessage(key, subs.map(String));

async function dashboard() {
  const { port } = await chrome.storage.local.get("port");
  return `http://127.0.0.1:${Number(port) || DEFAULT_PORT}`;
}

async function send(format, conversation) {
  const base = await dashboard();
  let res;
  try {
    res = await fetch(`${base}/api/capture`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Interlatch": "1" },
      body: JSON.stringify({ format, conversation }),
      signal: AbortSignal.timeout(WAIT),
    });
  } catch (e) {
    return { ok: false, retry: true, down: e?.name !== "TimeoutError", error: e?.name === "TimeoutError"
      ? t("errTimeout", base) : t("errNotRunning", base) };
  }
  const body = await res.json().catch(() => ({}));
  if (res.ok) {
    await chrome.storage.local.set({ seenDashboard: true });
    return { ok: true, ...body };
  }
  // only 400 is about the chat itself (not one the dashboard can read), so it isn't sent again until it changes; the
  // rest is the dashboard's: 409 a sync holds the archive, 404 a version without /api/capture, 403 not this computer's
  if (res.status === 404) return { ok: false, retry: true, error: t("errUpdate") };
  if (res.status === 409) return { ok: false, retry: true, busy: true }; // nothing's wrong: an Interlatch sync has the archive for now
  return { ok: false, retry: res.status !== 400, error: body.error || t("errAnswered", res.status) };
}

async function status() {
  const base = await dashboard();
  try {
    const res = await fetch(`${base}/api/me`, { headers: { "X-Interlatch": "1" }, signal: AbortSignal.timeout(5e3) });
    const me = await res.json();
    if (!res.ok) return { base, ok: false, error: me.error || t("errAnswered", res.status) };
    if (me.viewer?.here !== false) await chrome.storage.local.set({ seenDashboard: true });
    return { base, ok: me.viewer?.here !== false, error: me.viewer?.here === false ? t("errNotHere") : null };
  } catch {
    return { base, ok: false, down: true }; // nothing answers there: not installed, stopped, or on another port
  }
}

// The toolbar icon, for when no tab of a site is in front of you: a red ! while a site's chats aren't being saved, a green
// check while they are and the dashboard answers, nothing when paused or before the first sync.
async function badge() {
  const { paused, "sync:claude-ai": claude, "sync:chatgpt": chatgpt } = await chrome.storage.local.get(["paused", "sync:claude-ai", "sync:chatgpt"]);
  const sites = [claude, chatgpt];
  const bad = sites.some((st) => st?.error && !st.quiet);
  const good = !bad && !paused && sites.some((st) => st?.lastRun && !st.error) && (await status()).ok;
  await chrome.action.setBadgeBackgroundColor({ color: bad ? "#c92a2a" : "#0c7c60" });
  await chrome.action.setBadgeTextColor({ color: "#ffffff" });
  await chrome.action.setBadgeText({ text: bad ? "!" : good ? "✓" : "" });
}
chrome.storage.onChanged.addListener((changes) => {
  if (Object.keys(changes).some((k) => k.startsWith("sync:") || k === "paused" || k === "port")) badge();
});
chrome.runtime.onStartup.addListener(badge);
chrome.runtime.onInstalled.addListener(badge);

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg?.type === "capture" && sender.tab) {
    send(msg.format, msg.conversation).then(reply);
    return true;
  }
  if (msg?.type === "status" && !sender.tab) {
    badge(); // opening the popup is a fresh look at the dashboard
    status().then(reply);
    return true;
  }
  return false;
});
