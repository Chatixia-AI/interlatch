// The menu-bar panel (menubar.py shows panel.html in a popover). The app sends what to show with interlatch.render();
// the page sends back what was clicked through window.webkit.messageHandlers.interlatch. Search asks the dashboard's
// own /api/search. Text from sessions only ever goes in as text (textContent), never as HTML.
"use strict";

(() => {
  const post = (msg) => {
    try { window.webkit.messageHandlers.interlatch.postMessage(msg); } catch (e) { /* opened outside the app */ }
  };
  const $ = (sel, el = document) => el.querySelector(sel);

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "class") el.className = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
    return el;
  }

  // 16px line icons (fixed strings, the only markup set as HTML)
  const SVG = (d) => `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const ICONS = {
    sync: SVG('<path d="M13.5 8a5.5 5.5 0 0 1-9.6 3.7M2.5 8a5.5 5.5 0 0 1 9.6-3.7"/><path d="M12.4 1.8v2.6H9.8M3.6 14.2v-2.6h2.6"/>'),
    activity: SVG('<path d="M1.5 8.5h3l2-5 3 9 2-4h3"/>'),
    search: SVG('<circle cx="7" cy="7" r="4.5"/><path d="m10.4 10.4 3.6 3.6"/>'),
    open: SVG('<path d="M9.5 2.5h4v4M13.5 2.5 7.5 8.5"/><path d="M12 9.5v3a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3"/>'),
    cloud: SVG('<path d="M4.5 12.5a3 3 0 0 1-.4-6 4 4 0 0 1 7.7-.9 3.4 3.4 0 0 1 .2 6.9z"/>'),
    pause: SVG('<circle cx="8" cy="8" r="6"/><path d="M6.5 6v4M9.5 6v4"/>'),
    more: SVG('<circle cx="3.5" cy="8" r=".6" fill="currentColor"/><circle cx="8" cy="8" r=".6" fill="currentColor"/><circle cx="12.5" cy="8" r=".6" fill="currentColor"/>'),
  };
  const icon = (name) => { const s = h("span", { class: "ic" }); s.innerHTML = ICONS[name]; return s; };

  const AGENTS = { claude: ["C", "Claude Code"], codex: ["X", "Codex"], copilot: ["G", "GitHub Copilot"], bob: ["B", "IBM Bob"],
                   antigravity: ["A", "Google Antigravity"] };
  const OUTCOMES = { completed: "Completed", partial: "Partial", blocked: "Blocked", abandoned: "Abandoned",
                     exploratory: "Exploratory", unclear: "Unclear" };

  let snap = null;
  let query = "", found = null, seq = 0, timer = 0;

  // ------------------------------------------------------------------ pieces
  function sessionRow(s) {
    const [letter, name] = AGENTS[s.agent] || [((s.agent || "?")[0] || "?").toUpperCase(), s.agent || ""];
    const status = OUTCOMES[s.outcome]
      || { running: "Analyzing…", error: "Analysis failed", pending: "Not analyzed", stale: "Not analyzed" }[s.analysis_status]
      || (s.analysis_status === "done" ? "Analyzed" : "Not analyzed");
    return h("button", { class: "row", type: "button", title: s.title || "", onclick: () => post({ type: "open", page: `#/session/${s.id}` }) },
      h("span", { class: `agent a-${s.agent || "other"}`, title: name, "aria-label": name }, letter),
      h("span", { class: "row-main" },
        h("span", { class: "row-title" }, s.title || "(untitled session)"),
        h("span", { class: "row-meta" },
          s.project_name ? [h("span", { class: "proj" }, s.project_name), h("span", { class: "sep" }, "·")] : null,
          h("span", { class: "outcome" }, h("span", { class: `dot o-${OUTCOMES[s.outcome] ? s.outcome : "none"}` }), status))),
      h("span", { class: "row-time" }, s.time || ""));
  }

  function recentList(rows) {
    if (!rows.length) {
      return h("div", { class: "empty" }, h("img", { src: "art-pip.webp", alt: "" }),
        "No sessions yet. They show up here as your coding agents finish them.");
    }
    const out = [];
    let day = null;
    for (const s of rows) {
      if (s.day !== day) { day = s.day; out.push(h("div", { class: "day" }, day)); }
      out.push(sessionRow(s));
    }
    return out;
  }

  function shortDate(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }

  function searchList() {
    if (query.trim().length < 3) return h("div", { class: "empty" }, "Type at least 3 characters to search every session.");
    if (!found) return h("div", { class: "empty" }, "Searching…");
    const rows = (found.sessions || []).slice(0, 5).map((s) => ({ id: s.session_id, title: s.title, project_name: s.project_name,
      agent: s.agent, outcome: s.outcome, analysis_status: s.outcome ? "done" : "", time: shortDate(s.started_at) }));
    if (!rows.length) {
      return h("div", { class: "empty" }, h("img", { src: "art-magnifier.webp", alt: "" }), `No session mentions “${query.trim()}”.`);
    }
    const total = found.total || rows.length;
    return [h("div", { class: "day" }, `${total} session${total === 1 ? "" : "s"}`), rows.map(sessionRow),
      h("button", { class: "all", type: "button", onclick: openSearch }, "Show every mention in the dashboard  ↵")];
  }

  function openSearch() {
    post({ type: "open", page: `#/search?q=${encodeURIComponent(query.trim())}` });
  }

  // ------------------------------------------------------------------ the page
  function skeleton() {
    const input = h("input", { id: "q", type: "search", placeholder: "Search your sessions", "aria-label": "Search your sessions",
      autocomplete: "off", spellcheck: "false" });
    input.addEventListener("input", () => {
      query = input.value;
      found = null;
      clearTimeout(timer);
      if (query.trim().length >= 3) timer = setTimeout(search, 180);
      drawList();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && query.trim().length >= 3) { e.preventDefault(); openSearch(); }
      if (e.key === "ArrowDown") { e.preventDefault(); $(".row")?.focus(); }
    });
    $("#panel").replaceChildren(
      h("header", { class: "hero", id: "hero" }),
      h("section", { class: "tiles", id: "tiles" }),
      h("div", { id: "infos" }),
      h("label", { class: "search" }, icon("search"), input, h("kbd", null, "↵")),
      h("section", { class: "list", id: "list", "aria-label": "Sessions" }),
      h("footer", { class: "foot", id: "foot" }));
  }

  function drawHero(s) {
    const busy = !!s.syncing;
    const kids = [
      h("img", { class: "art", src: s.art, alt: "" }),
      h("div", { class: "hero-text" }, h("div", { class: "title" }, s.title), s.sub ? h("div", { class: "sub" }, s.sub) : null),
      h("div", { class: "tools" },
        h("button", { class: `icon-btn${busy ? " spinning" : ""}`, type: "button", disabled: busy, title: busy ? "Syncing…" : "Sync now",
          "aria-label": "Sync now", onclick: () => post({ type: "sync" }) }, icon("sync")),
        h("button", { class: "icon-btn", type: "button", title: "Activity", "aria-label": "Open Activity",
          onclick: () => post({ type: "open", page: "#/" }) }, icon("activity"))),
    ];
    if (s.state === "working") {
      const bar = h("span");
      const p = h("div", { class: `progress${s.progress ? "" : " indeterminate"}` }, bar);
      if (s.progress) bar.style.width = `${Math.min(100, (100 * s.progress[0]) / Math.max(1, s.progress[1]))}%`;
      kids.push(p);
    }
    $("#hero").replaceChildren(...kids);
  }

  function drawTiles(s) {
    const st = s.stats || {};
    const tiles = [
      ["Today", st.today, "#/sessions", false],
      st.waiting == null ? null : ["To analyze", st.waiting, "#/status", st.waiting > 0],
      ["Lessons, 7 days", st.lessons, "#/knowledge/all", false],
    ].filter(Boolean);
    const el = $("#tiles");
    el.style.setProperty("--n", tiles.length);
    el.replaceChildren(...tiles.map(([label, n, page, hot]) =>
      h("button", { class: `tile${hot ? " hot" : ""}`, type: "button", onclick: () => post({ type: "open", page }) },
        h("div", { class: "num" }, n == null ? "–" : Number(n).toLocaleString("en-US")), h("span", { class: "lbl" }, label))));
  }

  function drawInfos(s) {
    $("#infos").replaceChildren(...[
      s.hub ? h("div", { class: "info" }, icon("cloud"), s.hub) : null,
      s.note ? h("div", { class: "info warn" }, icon("pause"), s.note) : null,
    ].filter(Boolean));
    $("#infos").hidden = !s.hub && !s.note;
  }

  function drawFoot(s) {
    $("#foot").replaceChildren(...[
      h("button", { class: "cta", type: "button", onclick: () => post({ type: "open", page: "#/" }) }, "Open Dashboard", icon("open")),
      s.update ? h("button", { class: "chip", type: "button", onclick: () => post({ type: "update" }) }, h("span", { class: "dot" }),
        s.update === "build" ? "Update available" : `Update to ${s.update}`) : null,
      h("span", { class: "spacer" }),
      h("button", { class: "icon-btn", type: "button", title: "More", "aria-label": "More", onclick: () => post({ type: "menu" }) }, icon("more")),
    ].filter(Boolean));
  }

  function drawList() {
    const list = $("#list");
    const kids = query.trim() ? searchList() : recentList(snap ? snap.recent || [] : []);
    list.replaceChildren(...[kids].flat(Infinity));
  }

  function render() {
    if (!snap) return;
    $("#panel").className = `panel state-${snap.state}`;
    drawHero(snap);
    drawTiles(snap);
    drawInfos(snap);
    drawFoot(snap);
    if (!query.trim()) drawList();
  }

  async function search() {
    const mine = ++seq, q = query.trim();
    try {
      const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`, { headers: { "X-Interlatch-Lang": "en" } });
      const data = await r.json();
      if (mine === seq) { found = data; drawList(); }
    } catch (e) {
      if (mine === seq) { found = { sessions: [], total: 0 }; drawList(); }
    }
  }

  // ------------------------------------------------------------------ keys and size
  document.addEventListener("click", (e) => { if (e.target.closest("a")) e.preventDefault(); }); // the panel never navigates
  document.addEventListener("keydown", (e) => {
    const input = $("#q");
    if (e.key === "Escape") {
      e.preventDefault();
      if (query) { input.value = ""; query = ""; found = null; drawList(); input.focus(); } else post({ type: "close" });
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const rows = [...document.querySelectorAll(".row")];
      const i = rows.indexOf(document.activeElement);
      if (i < 0) return;
      e.preventDefault();
      if (e.key === "ArrowUp" && i === 0) input.focus();
      else rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))].focus();
      return;
    }
    // typing anywhere searches
    if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey && document.activeElement !== input) input.focus();
  });

  new ResizeObserver(() => { if (snap) post({ type: "size", height: Math.ceil($("#panel").getBoundingClientRect().height) }); })
    .observe($("#panel"));

  window.interlatch = {
    render(data) {
      const first = !snap;
      snap = data;
      render();
      if (first) post({ type: "size", height: Math.ceil($("#panel").getBoundingClientRect().height) });
    },
    opened() {  // the popover was shown again: start from the recent sessions, ready to type
      const input = $("#q");
      if (input.value) { input.value = ""; query = ""; found = null; drawList(); }
      input.focus();
    },
  };

  skeleton();
  post({ type: "ready" });
})();
