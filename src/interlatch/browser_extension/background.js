/* Interlatch: save web chats. The service worker: the one part that talks to the dashboard on this computer.

capture.js runs in claude.ai and chatgpt.com tabs and reads the chats there; a page on https can't call
http://127.0.0.1, so it hands each chat here and this sends it to POST /api/capture (the dashboard's
chat_import.capture). The dashboard takes it only from this computer, with the X-Interlatch header no web page can
send it. */
"use strict";

const DEFAULT_PORT = 11524;

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
    });
  } catch {
    return { ok: false, retry: true, error: `the Interlatch dashboard is not running at ${base}` };
  }
  const body = await res.json().catch(() => ({}));
  if (res.ok) return { ok: true, ...body };
  // only 400 is about the chat itself (not one the dashboard can read), so it isn't sent again until it changes; the
  // rest is the dashboard's: 409 a sync holds the archive, 404 a version without /api/capture, 403 not this computer's
  if (res.status === 404) return { ok: false, retry: true, error: "update Interlatch: this dashboard can't take chats yet" };
  return { ok: false, retry: res.status !== 400, error: body.error || `the dashboard answered ${res.status}` };
}

async function status() {
  const base = await dashboard();
  try {
    const res = await fetch(`${base}/api/me`, { headers: { "X-Interlatch": "1" } });
    const me = await res.json();
    if (!res.ok) return { base, ok: false, error: me.error || `answered ${res.status}` };
    return { base, ok: me.viewer?.here !== false, error: me.viewer?.here === false ? "not this computer's dashboard" : null };
  } catch {
    return { base, ok: false, error: "not running" };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg?.type === "capture" && sender.tab) {
    send(msg.format, msg.conversation).then(reply);
    return true;
  }
  if (msg?.type === "status" && !sender.tab) {
    status().then(reply);
    return true;
  }
  return false;
});
