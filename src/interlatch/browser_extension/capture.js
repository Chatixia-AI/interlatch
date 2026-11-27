/* Interlatch: save web chats. Runs in claude.ai, chatgpt.com and hark.com tabs.

While a tab of the site is open (on load, then every few minutes), it asks the site's own conversation list which
chats changed since the last sync, reads each one the way the site's page does (same origin, your own sign-in), and
hands it to background.js for the dashboard. A chat is read in the JSON a data export holds, so the dashboard stores
it as an import would, as the same session. Hark has no data export: its chats go one day at a time (hark, below). The first sync goes back `days` (30 by default); older chats come in from a
data export. Temporary chats are left out. A sync reads at most MAX chats and the next one follows a few minutes
later, so a first sync with many chats doesn't run into the site's rate limit; when the site asks to slow down anyway
(429), the sync waits longer.

These are the sites' internal endpoints, not a published API: if a site changes them, the popup shows the error. */
"use strict";

(() => {
  const SITE = { "claude.ai": "claude-ai", "chatgpt.com": "chatgpt", "hark.com": "hark" }[location.hostname];
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
  const NOTICE = `notice:${SITE}`; // when this site's notice was last shown
  const NOTICE_GAP = 60 * 60e3;

  const t = (key, ...subs) => chrome.i18n.getMessage(key, subs.map(String));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const time = (v) => { // an ISO date, or seconds (ChatGPT) or milliseconds since 1970
    const n = typeof v === "number" ? v : v !== "" && v != null && !isNaN(Number(v)) ? Number(v) : NaN;
    return isNaN(n) ? Date.parse(v) || 0 : n < 1e12 ? n * 1000 : n;
  };

  async function getJSON(path, headers) {
    const res = await fetch(path, { credentials: "include", headers });
    if (!res.ok) {
      throw Object.assign(new Error(t("errSite", `${location.hostname}${path.split("?")[0]}`, res.status)), { status: res.status });
    }
    return res.json();
  }

  // Each site: the chats changed since `since` (ms), newest first, and one chat as its data export holds it.
  const claude = {
    signedIn: () => getJSON("/api/organizations"),
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
    signedIn() { return this.headers(); },
    async headers() {
      if (!this.token) {
        const session = await getJSON("/api/auth/session");
        if (!session.accessToken) throw Object.assign(new Error(t("errNotSignedIn")), { status: 401 });
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

  /* hark.com: one chat that never ends (`main`) and one per Hark project. A chat is sent one day at a time (local time)
  so the main chat doesn't become one endless session: <chat id>_<YYYY-MM-DD>, the day's messages and the work Hark
  dispatched that day (sources/hark.py). A changed chat is read back, newest page first, to the start of the first day
  that can have changed, so each day sent is whole. Only the fields Interlatch reads are kept: never the sign-in
  screens Hark asks you to fill in (its `asks`), screenshots or live-view links. */
  const HARK_PAGES = 100; // pages of one chat in one sync: a first sync of a busy main chat
  const pad = (n) => String(n).padStart(2, "0");
  const dayOf = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  const dayStart = (ms) => { const d = new Date(ms); d.setHours(0, 0, 0, 0); return d.getTime(); };
  const num = (v) => (typeof v === "number" && v > 0 ? v : 0);
  const slimCall = (c) => ({ toolName: c.toolName, status: c.status, input: c.input, dispatchId: c.dispatchId,
    dispatchedAt: c.dispatchedAt, updatedAt: c.updatedAt });
  const slimMessage = (m) => ({ id: m.id, role: m.role, content: typeof m.content === "string" ? m.content : "",
    timestamp: m.timestamp, source: m.source, thought: typeof m.thought === "string" ? m.thought : null,
    task_name: m.task_name ?? null, brief: m.brief ?? null, jobStatus: m.jobStatus ?? null, threadId: m.threadId ?? null,
    toolCalls: Array.isArray(m.toolCalls) ? m.toolCalls.filter((c) => c && typeof c === "object").map(slimCall) : [] });
  const slimPanel = (p, at) => ({ dispatchId: p.dispatchId, kind: p.kind, status: p.status, narrative: p.narrative,
    latestNarration: p.latestNarration, startedAt: num(p.startedAt) || at,
    phases: Array.isArray(p.phases) ? p.phases.map((x) => ({ title: x?.title, status: x?.status })) : [] });

  const hark = {
    signedIn: () => getJSON("/api/conversations"),
    async *changed(since) {
      const { conversations } = await getJSON("/api/conversations");
      for (const c of Array.isArray(conversations) ? conversations : []) {
        if (!c?.id || time(c.activityAt || c.updatedAt) < since) continue;
        yield* await this.days(c, dayStart(since));
      }
    },
    async days(c, from) {
      const messages = new Map(), panels = new Map();
      let url = `/api/messages/history?conversationId=${encodeURIComponent(c.id)}&v=2`;
      for (let n = 0; url && n < HARK_PAGES; n++) {
        if (n) await sleep(PAUSE);
        const page = await getJSON(url);
        let older = false;
        for (const m of Object.values(page.messages || {})) {
          if (!m?.id || (m.role !== "user" && m.role !== "assistant")) continue;
          if (num(m.timestamp) < from) { older = true; continue; }
          messages.set(m.id, slimMessage(m));
        }
        for (const w of Object.values(page.windows || {})) {
          for (const p of w?.panels || []) {
            const at = num(p?.startedAt) || num(w.at);
            if (p?.dispatchId && at >= from) panels.set(p.dispatchId, slimPanel(p, at));
          }
        }
        const back = page.older;
        url = !older && back?.hasMore && back.cursor ? `/api/messages/history?conversationId=${encodeURIComponent(c.id)}&v=2`
          + `&before=${encodeURIComponent(back.cursor.id)}&beforeCreatedAt=${encodeURIComponent(back.cursor.createdAt)}` : null;
      }
      const byDay = new Map();
      const day = (key) => byDay.get(key) || byDay.set(key, { messages: [], panels: [], updated: 0 }).get(key);
      for (const m of messages.values()) {
        const d = day(dayOf(m.timestamp));
        d.messages.push(m);
        d.updated = Math.max(d.updated, m.timestamp, ...m.toolCalls.map((x) => num(x.updatedAt)));
      }
      for (const p of panels.values()) {
        const d = day(dayOf(p.startedAt));
        d.panels.push(p);
        d.updated = Math.max(d.updated, p.startedAt);
      }
      const chat = { id: c.id, kind: c.kind, title: c.title ?? null, createdAt: c.createdAt };
      return [...byDay].filter(([, d]) => d.messages.length).sort(([a], [b]) => (a < b ? 1 : -1)).map(([key, d]) => {
        d.messages.sort((a, b) => a.timestamp - b.timestamp);
        const id = `${c.id}_${key}`;
        return { id, updated: String(d.updated),
          conv: { hark: 1, id, day: key, updated: d.updated, conversation: chat, messages: d.messages, panels: d.panels } };
      }); // newest day first, as the other sites list their chats
    },
    read: async (it) => it.conv, // read already, with the chat's pages
  };

  const site = { "claude-ai": claude, chatgpt, hark }[SITE];
  let running = false;
  let wanted = false; // a sync you'd expect to hear about is due: not the timer's, which would interrupt a chat you're in

  /* What this sync has to say on the page. Chats saved by the timer wait for the next sync you'd expect (load, leaving a
  chat, Sync now), so a long chat doesn't announce itself every 5 minutes. A long first sync says so once at the start
  and once at the end. A failure is told once until it changes; being signed out of the site isn't one. */
  function tell(state, { announce, quiet, down, seen, overlay }) {
    const say = overlay ? interlatchOverlay.say : () => {};
    if (state.error && !quiet) {
      if (state.shownError === state.error) return;
      state.shownError = state.error;
      const link = down && !seen ? { href: t("installUrl"), text: t("stInstall") } : { href: t("helpUrl"), text: t("ovFix") };
      say({ text: state.error[0].toUpperCase() + state.error.slice(1), kind: "bad", link, dismiss: t("ovDismiss") });
      return;
    }
    if (state.shownError) {
      state.shownError = null;
      interlatchOverlay.clear(); // fixed: the failure's message goes with it
    }
    if (state.error) return;
    if (state.more) {
      if (!state.catchingUp) say({ text: t("ovCatching"), kind: "wait" });
      state.catchingUp = true;
      return;
    }
    const n = state.unannounced?.length || 0;
    if (n && (announce || state.catchingUp)) {
      say({ text: n === 1 ? t("ovSavedOne") : t("ovSavedMany", n) });
      state.unannounced = [];
    }
    state.catchingUp = false;
  }

  /* What the extension is doing here and where chats go, with Pause: once an hour per site, when you are looking at the
  tab. It says so only when it's true: not before you chose Start saving, while Paused, signed out of the site or when
  the dashboard doesn't answer. A failure to send has its own message (tell). */
  const noticeFor = (paused) => ({ kind: "notice", text: t(paused ? "ovNoticePaused" : "ovNoticeSaving"), dismiss: t("ovDismiss"),
    action: { text: t(paused ? "ovResume" : "ovPause"), run: async () => {
      await chrome.storage.local.set({ paused: !paused });
      interlatchOverlay.show(noticeFor(!paused));
    } } });

  async function due() {
    const store = await chrome.storage.local.get([NOTICE, "consented", "paused", "noOverlay"]);
    return store.consented && !store.paused && !store.noOverlay && Date.now() - (store[NOTICE] || 0) >= NOTICE_GAP;
  }

  async function notice() {
    if (!await due()) return;
    if (document.visibilityState !== "visible") {
      await new Promise((resolve) => {
        const look = () => {
          if (document.visibilityState !== "visible") return;
          document.removeEventListener("visibilitychange", look);
          resolve();
        };
        document.addEventListener("visibilitychange", look);
      });
    }
    await site.signedIn(); // throws when signed out
    if (!await chrome.runtime.sendMessage({ type: "reachable" }) || !await due()) return; // another tab may have shown it meanwhile
    await chrome.storage.local.set({ [NOTICE]: Date.now() });
    interlatchOverlay.say(noticeFor(false));
  }

  async function sync(announce = false) {
    wanted ||= announce === true;
    if (running) return;
    running = true;
    try {
      const { [KEY]: saved, [LOCK]: lock, days, consented, paused, noOverlay, seenDashboard } = await chrome.storage.local.get(
        [KEY, LOCK, "days", "consented", "paused", "noOverlay", "seenDashboard"]);
      const started = Date.now();
      if (!consented || paused || (lock && started - lock < STALE_LOCK)) return;
      await chrome.storage.local.set({ [LOCK]: started });
      const state = { seen: {}, captured: 0, ...saved, running: started, progress: 0 }; // the popup shows the progress
      await chrome.storage.local.set({ [KEY]: state });
      const since = state.since ? state.since - MARGIN : started - (Number(days) || DEFAULT_DAYS) * DAY;
      let sent = 0, read = 0, error = null, more = false, slowed = false, quiet = false, down = false, busy = false, halted = false;
      const fresh = [];
      const done = new Set();
      try {
        for await (const it of site.changed(since)) {
          if (done.has(it.id)) continue;
          done.add(it.id);
          if (state.seen[it.id] === it.updated) continue; // sent already, and not changed since
          // Paused during a sync: the rest waits for a sync after you resume
          if ((await chrome.storage.local.get("paused")).paused) { more = halted = true; break; }
          if (read++ >= MAX) { more = true; break; }
          const conv = await site.read(it);
          let r;
          try {
            r = await chrome.runtime.sendMessage({ type: "capture", format: SITE, conversation: conv });
          } catch {
            r = { retry: true, error: t("errReloaded") };
          }
          if (r?.busy) { more = busy = true; break; } // sent again by the next sync, a little later
          if (!r?.ok && r?.retry !== false) { error = r?.error || t("errNoReach"); down = !!r?.down; break; }
          state.seen[it.id] = it.updated; // sent, or refused for good (not sent again until it changes)
          if (r.ok && (r.result === "new" || r.result === "updated")) { state.progress = ++sent; fresh.push(it.id); }
          if (!r.ok) state.refused = { at: Date.now(), error: r.error };
          await chrome.storage.local.set({ [KEY]: state });
          await sleep(PAUSE);
        }
      } catch (e) {
        if (e?.status === 429) more = slowed = true;
        else { error = String(e?.message || e); quiet = e?.status === 401 || e?.status === 403; } // signed out of the site
      }
      if (!error && !more) { // a later sync looks only from here; chats seen before that can't come up again unchanged
        state.since = started;
        for (const [id, updated] of Object.entries(state.seen)) if (time(updated) < started - MARGIN) delete state.seen[id];
      }
      Object.assign(state, { lastRun: Date.now(), error, quiet, more, slowed, running: null, lastSaved: sent,
        captured: (state.captured || 0) + sent, unannounced: [...new Set([...(state.unannounced || []), ...fresh])] });
      if (sent) state.lastSent = Date.now();
      const told = wanted;
      wanted = false;
      if (busy || halted) wanted = told; // it says nothing now; the retry does
      else tell(state, { announce: told, quiet, down, seen: seenDashboard || saved?.captured, overlay: !noOverlay });
      await chrome.storage.local.set({ [KEY]: state });
      await chrome.storage.local.remove(LOCK);
      if (more) setTimeout(sync, slowed ? SLOWED : AGAIN); // the rest, a little later
    } finally {
      running = false;
    }
  }

  const here = () => chrome.storage.local.set({ [`alive:${SITE}`]: Date.now() }); // the popup knows a tab answers
  here();
  // the toolbar icon and badge on this tab. A page the browser prerendered (the address bar guessing where you're going)
  // isn't on show yet, the tab still has another page, and showing it clears them
  const lit = () => { if (!document.prerendering) chrome.runtime.sendMessage({ type: "site" }).catch(() => {}); };
  lit();
  document.addEventListener("prerenderingchange", lit);
  window.addEventListener("pageshow", (e) => { if (e.persisted) lit(); }); // back from the browser's page cache: this script doesn't run again
  notice().catch(() => {}); // no message is the right answer when any of it can't tell
  setTimeout(() => sync(true), 3000);
  setInterval(sync, EVERY);
  // the chat you were in, without waiting for the next 5 minutes: when you leave it for another chat (the sites
  // change the address without loading a page) or for another tab or app; at most once a minute
  let lastSoon = 0, path = location.pathname;
  const soon = () => {
    if (Date.now() - lastSoon < SOON_GAP) return;
    lastSoon = Date.now();
    setTimeout(() => sync(true), SOON_DELAY);
  };
  setInterval(() => { if (location.pathname !== path) { path = location.pathname; soon(); } }, 2000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") soon(); });
  chrome.storage.onChanged.addListener((changes) => { // Sync now, from the popup
    if (changes.syncNow?.newValue) { here(); sync(true); }
    if (changes.consented?.newValue) { // Start saving, on the welcome page
      notice().catch(() => {});
      sync(true);
    }
    if (changes.paused?.oldValue && !changes.paused.newValue) sync(); // resumed: what a Pause stopped goes on
    if (Object.keys(changes).some((k) => k.startsWith("sync:") || ["consented", "paused", "port", "checkedAt"].includes(k))) lit();
  });
})();
