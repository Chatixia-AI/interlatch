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

// The toolbar icon says what's happening on this tab: a pause sign where nothing is read (the default, and on a site while
// Paused), in colour on a site. A red ! while a site's chats aren't being saved shows on every tab; a green check, while
// they are and the dashboard answers, only on a tab of a site, because on the others it would sit on the pause sign.
const COLOUR = { 16: "icon-16.png", 32: "icon-32.png" };
const IDLE = { 16: "icon-idle-16.png", 32: "icon-idle-32.png" };
const RED = "#c92a2a";
const GREEN = "#0c7c60";

async function failing() {
  const sites = await chrome.storage.local.get(["sync:claude-ai", "sync:chatgpt"]);
  return { sites: Object.values(sites), bad: Object.values(sites).some((st) => st?.error && !st.quiet) };
}

async function badge() {
  const { bad } = await failing();
  await chrome.action.setBadgeBackgroundColor({ color: RED });
  await chrome.action.setBadgeTextColor({ color: "#ffffff" });
  await chrome.action.setBadgeText({ text: bad ? "!" : "" });
}

// A tab of a site; these settings go when it navigates away, and capture.js asks again whenever something changes.
async function siteTab(tabId) {
  const { consented, paused: stopped } = await chrome.storage.local.get(["consented", "paused"]);
  const paused = stopped || !consented;
  await chrome.action.setIcon({ tabId, path: paused ? IDLE : COLOUR }); // before asking the dashboard, which can take seconds
  const { sites, bad } = await failing();
  const good = !bad && !paused && sites.some((st) => st?.lastRun && !st.error) && (await status()).ok;
  await chrome.action.setBadgeBackgroundColor({ tabId, color: bad ? RED : GREEN });
  await chrome.action.setBadgeText({ tabId, text: bad ? "!" : good ? "✓" : "" });
}

chrome.storage.onChanged.addListener((changes) => {
  if (Object.keys(changes).some((k) => k.startsWith("sync:"))) badge();
});
chrome.runtime.onStartup.addListener(badge);
chrome.runtime.onInstalled.addListener(badge);
/* Nothing is read until you choose Start saving on the welcome page, which opens when the extension is installed. An
update of one that has been saving keeps going: that person chose it when they loaded it by hand. */
chrome.runtime.onInstalled.addListener(async ({ reason }) => {
  if (reason === "install") {
    await chrome.tabs.create({ url: "welcome.html" });
    return;
  }
  const { sites } = await failing();
  if (sites.length) await chrome.storage.local.set({ consented: true });
});

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg?.type === "site" && sender.tab) {
    siteTab(sender.tab.id).catch(() => {}); // the tab has gone
    return false;
  }
  if (msg?.type === "capture" && sender.tab) {
    send(msg.format, msg.conversation).then(reply);
    return true;
  }
  if (msg?.type === "reachable" && sender.tab) { // capture.js, before it says chats are being saved
    status().then((r) => reply(!!r.ok));
    return true;
  }
  if (msg?.type === "status" && !sender.tab) {
    badge();
    chrome.storage.local.set({ checkedAt: Date.now() }); // opening the popup is a fresh look at the dashboard: site tabs ask again
    status().then(reply);
    return true;
  }
  return false;
});
