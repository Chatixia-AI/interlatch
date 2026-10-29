/* Interlatch: save web chats. Runs in claude.ai and chatgpt.com tabs.

While a tab of the site is open (on load, then every few minutes), it asks the site's own conversation list which
chats changed since the last sync, reads each one the way the site's page does (same origin, your own sign-in), and
hands it to background.js for the dashboard. A chat is read in the JSON a data export holds, so the dashboard stores
it as an import would, as the same session. The first sync goes back `days` (30 by default); older chats come in from a
data export. Temporary chats are left out. A sync reads at most MAX chats and the next one follows a few minutes
later, so a first sync with many chats doesn't run into the site's rate limit; when the site asks to slow down anyway
(429), the sync waits longer.

These are the sites' internal endpoints, not a published API: if a site changes them, the popup shows the error. */
"use strict";

(() => {
  const SITE = { "claude.ai": "claude-ai", "chatgpt.com": "chatgpt" }[location.hostname];
  if (!SITE) return;
  const KEY = `sync:${SITE}`;
  const LOCK = `lock:${SITE}`; // one tab syncs a site at a time
  const EVERY = 5 * 60e3;
  const PAUSE = 700; // between two chats read: the pace of someone clicking through them
  const MAX = 40; // chats read in one sync: ChatGPT answered 429 after about 200 in a row
  const AGAIN = 2 * 60e3; // the next sync, when this one stopped at MAX
  const SLOWED = 10 * 60e3; // the next sync, after a 429
  const SOON_DELAY = 5e3; // after you leave a chat: the site has saved its last reply by then
  const SOON_GAP = 60e3;
  const MARGIN = 10 * 60e3; // the next sync looks this far before the last one began
  const STALE_LOCK = 15 * 60e3;
  const DEFAULT_DAYS = 30;
  const DAY = 86400e3;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const time = (v) => { // an ISO date, or seconds (ChatGPT) or milliseconds since 1970
    const n = typeof v === "number" ? v : v !== "" && v != null && !isNaN(Number(v)) ? Number(v) : NaN;
    return isNaN(n) ? Date.parse(v) || 0 : n < 1e12 ? n * 1000 : n;
  };

  async function getJSON(path, headers) {
    const res = await fetch(path, { credentials: "include", headers });
    if (!res.ok) {
      throw Object.assign(new Error(`${location.hostname}${path.split("?")[0]} answered ${res.status}`), { status: res.status });
    }
    return res.json();
  }

  // Each site: the chats changed since `since` (ms), newest first, and one chat as its data export holds it.
  const claude = {
    async *changed(since) {
      const orgs = await getJSON("/api/organizations");
      for (const org of Array.isArray(orgs) ? orgs : []) {
        if (Array.isArray(org.capabilities) && !org.capabilities.includes("chat")) continue; // an API-only organization
        for (let offset = 0; ; offset += 50) {
          const page = await getJSON(`/api/organizations/${org.uuid}/chat_conversations?limit=50&offset=${offset}`);
          if (!Array.isArray(page) || !page.length) break;
          let older = false;
          for (const c of page) {
            if (time(c.updated_at) < since) { older = true; continue; }
            if (!c.is_temporary) yield { id: c.uuid, updated: c.updated_at, org: org.uuid, item: c };
          }
          if (older || page.length < 50) break;
        }
      }
    },
    async read(it) {
      const conv = await getJSON(
        `/api/organizations/${it.org}/chat_conversations/${it.id}?tree=True&rendering_mode=messages&render_all_tools=true`);
      // the chat itself doesn't name its project; the list does
      if (it.item.project && !conv.project) conv.project = it.item.project;
      if (it.item.project_uuid && !conv.project_uuid) conv.project_uuid = it.item.project_uuid;
      return conv;
    },
  };

  const chatgpt = {
    token: null,
    async headers() {
      if (!this.token) {
        const session = await getJSON("/api/auth/session");
        if (!session.accessToken) throw new Error("not signed in to ChatGPT");
        this.token = session.accessToken;
      }
      const device = document.cookie.match(/(?:^|; )oai-did=([^;]+)/)?.[1];
      return { Authorization: `Bearer ${this.token}`, ...(device ? { "oai-device-id": decodeURIComponent(device) } : {}) };
    },
    async *listed(path, since, project) {
      const h = await this.headers();
      for (let offset = 0; ; offset += 28) {
        const page = await getJSON(`${path}offset=${offset}&limit=28&order=updated`, h);
        const items = page.items || [];
        let older = false;
        for (const c of items) {
          if (time(c.update_time) < since) { older = true; continue; }
          if (!c.is_temporary_chat) yield { id: c.id, updated: String(c.update_time), project };
        }
        if (older || items.length < 28) break;
      }
    },
    async *changed(since) {
      this.token = null; // a fresh one each sync: it expires
      yield* this.listed("/backend-api/conversations?", since, null);
      // chats in a ChatGPT project are not in that list: each project lists its own, newest first
      const h = await this.headers();
      let cursor = null;
      do {
        const side = await getJSON(`/backend-api/gizmos/snorlax/sidebar?conversations_per_gizmo=0${
          cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, h);
        const items = side.items || [];
        for (const g of items) {
          const giz = g.gizmo?.gizmo || g.gizmo || {};
          if (!giz.id) continue;
          const project = { id: giz.id, name: giz.display?.name || "" };
          for (let c = "0"; c != null;) {
            const page = await getJSON(`/backend-api/gizmos/${giz.id}/conversations?cursor=${encodeURIComponent(c)}`, h);
            let older = false;
            for (const conv of page.items || []) {
              if (time(conv.update_time) < since) { older = true; continue; }
              yield { id: conv.id, updated: String(conv.update_time), project };
            }
            c = older || !(page.items || []).length ? null : page.cursor ?? null;
          }
        }
        cursor = items.length ? side.cursor : null;
      } while (cursor);
    },
    async read(it) {
      const conv = await getJSON(`/backend-api/conversation/${it.id}`, await this.headers());
      if (it.project) conv.project = it.project;
      return conv;
    },
  };

  const site = SITE === "claude-ai" ? claude : chatgpt;
  let running = false;

  async function sync() {
    if (running) return;
    running = true;
    try {
      const { [KEY]: saved, [LOCK]: lock, days, paused } = await chrome.storage.local.get([KEY, LOCK, "days", "paused"]);
      const started = Date.now();
      if (paused || (lock && started - lock < STALE_LOCK)) return;
      await chrome.storage.local.set({ [LOCK]: started });
      const state = { seen: {}, captured: 0, ...saved, running: started, progress: 0 }; // the popup shows the progress
      await chrome.storage.local.set({ [KEY]: state });
      const since = state.since ? state.since - MARGIN : started - (Number(days) || DEFAULT_DAYS) * DAY;
      let sent = 0, read = 0, error = null, more = false, slowed = false;
      const done = new Set();
      try {
        for await (const it of site.changed(since)) {
          if (done.has(it.id)) continue;
          done.add(it.id);
          if (state.seen[it.id] === it.updated) continue; // sent already, and not changed since
          if (read++ >= MAX) { more = true; break; }
          const conv = await site.read(it);
          let r;
          try {
            r = await chrome.runtime.sendMessage({ type: "capture", format: SITE, conversation: conv });
          } catch {
            r = { retry: true, error: "the extension was reloaded: reload this tab" };
          }
          if (!r?.ok && r?.retry !== false) { error = r?.error || "could not reach the dashboard"; break; }
          state.seen[it.id] = it.updated; // sent, or refused for good (not sent again until it changes)
          if (r.ok && (r.result === "new" || r.result === "updated")) state.progress = ++sent;
          if (!r.ok) state.refused = { at: Date.now(), error: r.error };
          await chrome.storage.local.set({ [KEY]: state });
          await sleep(PAUSE);
        }
      } catch (e) {
        if (e?.status === 429) more = slowed = true;
        else error = String(e?.message || e);
      }
      if (!error && !more) { // a later sync looks only from here; chats seen before that can't come up again unchanged
        state.since = started;
        for (const [id, updated] of Object.entries(state.seen)) if (time(updated) < started - MARGIN) delete state.seen[id];
      }
      Object.assign(state, { lastRun: Date.now(), error, more, slowed, running: null, captured: (state.captured || 0) + sent });
      if (sent) state.lastSent = Date.now();
      await chrome.storage.local.set({ [KEY]: state });
      await chrome.storage.local.remove(LOCK);
      if (more) setTimeout(sync, slowed ? SLOWED : AGAIN); // the rest, a little later
    } finally {
      running = false;
    }
  }

  const here = () => chrome.storage.local.set({ [`alive:${SITE}`]: Date.now() }); // the popup knows a tab answers
  here();
  setTimeout(sync, 3000);
  setInterval(sync, EVERY);
  // the chat you were in, without waiting for the next 5 minutes: when you leave it for another chat (the sites
  // change the address without loading a page) or for another tab or app; at most once a minute
  let lastSoon = 0, path = location.pathname;
  const soon = () => {
    if (Date.now() - lastSoon < SOON_GAP) return;
    lastSoon = Date.now();
    setTimeout(sync, SOON_DELAY);
  };
  setInterval(() => { if (location.pathname !== path) { path = location.pathname; soon(); } }, 2000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") soon(); });
  chrome.storage.onChanged.addListener((changes) => { // Sync now, from the popup
    if (changes.syncNow?.newValue) { here(); sync(); }
  });
})();
