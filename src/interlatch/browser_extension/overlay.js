/* Interlatch: save web chats. The small message at the bottom right of a claude.ai or chatgpt.com page; capture.js says
when. It lives in a closed shadow root, so the site's styles don't reach it and its styles don't reach the site. It never
takes focus. A saved-chats message passes clicks through and fades; a failure stays until it's dismissed or fixed. A
message for a tab you aren't looking at waits until you come back to it. */
"use strict";

globalThis.interlatchOverlay = (() => {
  const SHOW = 3000;
  const FADE = 400;
  const CSS = `
    .pill { --panel: #0d2a4d; --rule: #5d86b6; --ink: #e8f2ff; --dim: #8eb1d8; --mark: #6fe3c1;
      display: flex; align-items: center; gap: 8px; max-width: 360px; padding: 8px 12px; pointer-events: none;
      color: var(--ink); background: var(--panel); border: 1px solid var(--rule); border-left: 3px solid var(--mark);
      box-shadow: 0 6px 20px rgba(0, 0, 0, .28); font: 13px/1.4 system-ui, -apple-system, "Hiragino Sans", "Yu Gothic UI", sans-serif;
      animation: in .18s ease-out; transition: opacity ${FADE}ms; }
    .pill.light { --panel: #ffffff; --rule: #8ba6c8; --ink: #0b1f38; --dim: #3d5878; --mark: #0c7c60; }
    .pill.wait { --mark: #ffb45e; }
    .pill.wait.light { --mark: #975708; }
    .pill.bad { --mark: #ff9b9b; pointer-events: auto; }
    .pill.bad.light { --mark: #b42323; }
    .pill.out { opacity: 0; }
    b { font-weight: 600; }
    span { overflow-wrap: anywhere; }
    a { flex: none; color: inherit; text-underline-offset: 3px; }
    button { flex: none; padding: 0 2px; font: inherit; color: var(--dim); background: none; border: 0; cursor: pointer; }
    button:hover { color: var(--ink); }
    @keyframes in { from { opacity: 0; transform: translateY(6px); } }
    @media (prefers-reduced-motion: reduce) { .pill { animation: none; transition: none; } }`;
  let host = null, timer = null, pending = null;

  function clear() {
    clearTimeout(timer);
    pending = null;
    host?.remove();
    host = null;
  }

  function el(tag, attrs, text) {
    const node = Object.assign(document.createElement(tag), attrs);
    if (text) node.textContent = text;
    return node;
  }

  async function show({ text, kind = "", link = null, dismiss = "" }) {
    clear();
    const { theme } = await chrome.storage.local.get("theme");
    const light = theme ? theme === "light" : matchMedia("(prefers-color-scheme: light)").matches;
    host = document.createElement("div");
    // set one at a time, not as a style attribute, which a page's security policy can block; `all` first, or it undoes the rest
    for (const [k, v] of [["all", "initial"], ["position", "fixed"], ["right", "16px"], ["bottom", "16px"], ["z-index", "2147483647"]]) {
      host.style.setProperty(k, v, "important");
    }
    const root = host.attachShadow({ mode: "closed" });
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(CSS);
    root.adoptedStyleSheets = [sheet];
    const pill = el("div", { className: `pill ${kind} ${light ? "light" : ""}`, role: kind === "bad" ? "alert" : "status" });
    pill.append(el("b", {}, "Interlatch"), el("span", {}, text));
    if (link) pill.append(el("a", { href: link.href, target: "_blank", rel: "noopener" }, link.text));
    if (kind === "bad") {
      const close = el("button", { type: "button", title: dismiss, ariaLabel: dismiss }, "×");
      close.addEventListener("click", clear);
      pill.append(close);
    } else {
      const shown = host;
      timer = setTimeout(() => {
        pill.classList.add("out");
        timer = setTimeout(() => { if (host === shown) clear(); }, FADE);
      }, SHOW);
    }
    root.append(pill);
    document.documentElement.append(host);
  }

  function say(message) {
    if (document.visibilityState === "visible") return show(message);
    pending = message;
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && pending) show(pending);
  });

  return { say, clear };
})();
