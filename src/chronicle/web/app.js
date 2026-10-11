/* Interlatch dashboard: vanilla JS, no build step. */
"use strict";

// =====================================================================================
// DOM helpers (all untrusted text goes through textContent)
// =====================================================================================
const $ = (sel, root = document) => root.querySelector(sel);
const SVGNS = "http://www.w3.org/2000/svg";

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  setAttrs(e, attrs);
  append(e, kids);
  return e;
}
function s(tag, attrs, ...kids) {
  const e = document.createElementNS(SVGNS, tag);
  setAttrs(e, attrs);
  append(e, kids);
  return e;
}
function setAttrs(e, attrs) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") e.setAttribute("class", v);
    else if (k === "style" && typeof v === "object") setStyle(e, v);
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (k === "html") e.innerHTML = v; // only for output of our own escaping renderers
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
}
// styles go through the CSSOM, custom properties (--x) included, never a style attribute: the page's
// Content-Security-Policy allows no inline styles (server.py, PAGE_CSP)
function setStyle(e, styles) {
  for (const [p, v] of Object.entries(styles)) {
    if (p.startsWith("--")) e.style.setProperty(p, v);
    else e.style[p] = v;
  }
}
function append(e, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false || kid === "") continue;
    e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}
function safeUrl(u) { return /^https?:\/\//i.test(String(u || "")) ? String(u) : null; }
function extLink(url, label) {
  const u = safeUrl(url);
  return u ? h("a", { href: u, target: "_blank", rel: "noopener" }, label) : h("span", null, label);
}
function escapeHtml(t) {
  return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// =====================================================================================
// Icons: 24px stroke glyphs drawn in currentColor (strings are path data; [tag, attrs] for other shapes)
// =====================================================================================
const C10 = ["circle", { cx: 12, cy: 12, r: 10 }];
const ICONS = {
  overview: [["rect", { x: 3, y: 3, width: 7, height: 9, rx: 1.5 }], ["rect", { x: 14, y: 3, width: 7, height: 5, rx: 1.5 }],
    ["rect", { x: 14, y: 12, width: 7, height: 9, rx: 1.5 }], ["rect", { x: 3, y: 16, width: 7, height: 5, rx: 1.5 }]],
  sessions: ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"],
  prompts: ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z", "M8 8h8", "M8 12h5"],
  knowledge: ["M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z", "M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"],
  projects: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  newGroup: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z", "M12 10.5v6", "M9 13.5h6"],
  systems: [["rect", { x: 3, y: 3, width: 7, height: 6, rx: 1.5 }], ["rect", { x: 14, y: 3, width: 7, height: 6, rx: 1.5 }],
    ["rect", { x: 8.5, y: 15, width: 7, height: 6, rx: 1.5 }], "M6.5 9v3h11V9", "M12 12v3"],
  cloud: ["M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9z"],
  // a project of a hub, which gathers folders from the team's computers; a folder here that goes to one (hub teal)
  hubproject: [["path", { d: "M20 17a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3.9a2 2 0 0 1-1.69-.9l-.81-1.2a2 2 0 0 0-1.67-.9H8a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2Z", class: "hub-line" }],
    ["path", { d: "M2 8v11a2 2 0 0 0 2 2h14", class: "hub-line" }]],
  linked: ["M11 20H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v2",
    ["path", { d: "M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9z", class: "hub-mark", transform: "translate(11.6 10.4) scale(.54)" }]],
  external: ["M15 3h6v6", "M10 14 21 3", "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"],
  terminal: ["m4 17 6-6-6-6", "M12 19h8"],
  glossary: ["M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20", "m8 13 4-7 4 7", "M9.1 11h5.8"],
  home: ["m3 10 9-7 9 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z", "M9 22V12h6v10"],
  settings: ["M4 21v-7", "M4 10V3", "M12 21v-9", "M12 8V3", "M20 21v-5", "M20 12V3", "M1 14h6", "M9 8h6", "M17 16h6"],
  sidebar: [["rect", { x: 3, y: 3, width: 18, height: 18, rx: 3 }], "M9 3v18"],
  left: ["m15 18-6-6 6-6"],
  right: ["m9 18 6-6-6-6"],
  outline: ["M8 6h13", "M8 12h13", "M8 18h13", "M3 6h.01", "M3 12h.01", "M3 18h.01"],
  appearance: [C10, ["path", { d: "M12 2a10 10 0 0 0 0 20z", class: "solid" }]],
  map: ["m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3z", "M9 3v15", "M15 6v15"],
  playbook: [C10, "m16.24 7.76-2.12 6.36-6.36 2.12 2.12-6.36z"],
  reviews: [["rect", { x: 3, y: 4, width: 18, height: 18, rx: 2 }], "M16 2v4", "M8 2v4", "M3 10h18", "m9 16 2 2 4-4"],
  sources: ["M12 22v-5", "M9 8V2", "M15 8V2", "M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z"],
  status: ["M22 12h-4l-3 9L9 3l-3 9H2"],
  search: [["circle", { cx: 11, cy: 11, r: 7 }], "m21 21-4.3-4.3"],
  sync: ["M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5", "M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16", "M16 16h5v5"],
  moon: ["M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"],
  sun: [["circle", { cx: 12, cy: 12, r: 4 }], "M12 2v2", "M12 20v2", "m4.93 4.93 1.41 1.41", "m17.66 17.66 1.41 1.41", "M2 12h2", "M20 12h2", "m6.34 17.66-1.41 1.41", "m19.07 4.93-1.41 1.41"],
  clock: [["circle", { cx: 12, cy: 12, r: 9 }], "M12 7v5l3 2"],
  zap: ["M13 2 3 14h9l-1 8 10-12h-9z"],
  tokens: ["M4 9h16", "M4 15h16", "M10 3 8 21", "M16 3l-2 18"],
  cost: ["M12 2v20", "M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"],
  diff: ["M12 3v12", "M6 9h12", "M6 21h12"],
  sparkles: ["M12 3l1.7 4.6L18.3 9.3l-4.6 1.7L12 15.6l-1.7-4.6L5.7 9.3l4.6-1.7z", "M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"],
  pin: ["M12 17v5", "M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"],
  x: ["M18 6 6 18", "m6 6 12 12"],
  download: ["M12 3v12", "m7 10 5 5 5-5", "M5 21h14"],
  mcp: ["M9 17H7A5 5 0 0 1 7 7h2", "M15 7h2a5 5 0 1 1 0 10h-2", "M8 12h8"],
  devices: [["rect", { x: 2, y: 4, width: 13, height: 10, rx: 1.5 }], "M1 18h15", ["rect", { x: 17, y: 8, width: 6, height: 12, rx: 1.5 }], "M20 17h.01"],
  arrow: ["M5 12h14", "m12 5 7 7-7 7"],
  branch: ["M6 3v12", ["circle", { cx: 18, cy: 6, r: 3 }], ["circle", { cx: 6, cy: 18, r: 3 }], "M18 9a9 9 0 0 1-9 9"],
  flame: ["M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z"],
  calendar: [["rect", { x: 3, y: 4, width: 18, height: 18, rx: 2 }], "M16 2v4", "M8 2v4", "M3 10h18"],
  dot: [["circle", { cx: 12, cy: 12, r: 3 }]],
  // knowledge kinds
  fix: ["M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"],
  gotcha: ["m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3", "M12 9v4", "M12 17h.01"],
  suggestions: ["M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5", "M9 18h6", "M10 22h4"],
  learning: ["M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5", "M9 18h6", "M10 22h4"],
  decision: ["M12 3v3", "M12 13v8", "M5 6h11l3 3.5-3 3.5H5z"],
  pattern: ["M8.3 10a.7.7 0 0 1-.63-1.02l3.7-6a.7.7 0 0 1 1.26 0l3.7 6A.7.7 0 0 1 15.7 10z", ["rect", { x: 3, y: 14, width: 7, height: 7, rx: 1 }], ["circle", { cx: 17.5, cy: 17.5, r: 3.5 }]],
  command: ["m4 17 6-6-6-6", "M12 19h8"],
  fact: [C10, "M12 16v-4", "M12 8h.01"],
  preference: [["circle", { cx: 12, cy: 8, r: 4 }], "M5 21a7 7 0 0 1 14 0"],
  reference: ["M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71", "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"],
  todo: [["rect", { x: 3, y: 3, width: 18, height: 18, rx: 2 }], "m9 12 2 2 4-4"],
  // outcomes and analysis states
  completed: [C10, "m9 12 2 2 4-4"],
  partial: [C10, ["path", { d: "M12 2a10 10 0 0 1 0 20z", class: "solid" }]],
  blocked: [C10, "m4.9 4.9 14.2 14.2"],
  abandoned: [C10, "m15 9-6 6", "m9 9 6 6"],
  exploratory: [C10, "m16.24 7.76-2.12 6.36-6.36 2.12 2.12-6.36z"],
  unclear: [C10, "M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3", "M12 17h.01"],
  history: ["M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5", "M12 7v5l4 2"],
  again: ["M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5"],
  queued: [C10, "M12 6v6l4 2"],
  pause: ["M9 5v14", "M15 5v14"],
  running: ["M21 12a9 9 0 1 1-6.22-8.56"],
  skipped: [C10, "M8 12h8"],
  // glossary categories
  file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6"],
  concept: ["M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5", "M9 18h6", "M10 22h4"],
  component: ["M8.3 10a.7.7 0 0 1-.63-1.02l3.7-6a.7.7 0 0 1 1.26 0l3.7 6A.7.7 0 0 1 15.7 10z", ["rect", { x: 3, y: 14, width: 7, height: 7, rx: 1 }], ["circle", { cx: 17.5, cy: 17.5, r: 3.5 }]],
  data: [["ellipse", { cx: 12, cy: 5, rx: 9, ry: 3 }], "M3 5v14a9 3 0 0 0 18 0V5", "M3 12a9 3 0 0 0 18 0"],
  domain: [C10, "M2 12h20", "M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"],
  tool: ["M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"],
  library: ["M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z", "M3.3 7 12 12l8.7-5", "M12 22V12"],
  system: [["rect", { x: 5, y: 5, width: 14, height: 14, rx: 2 }], ["rect", { x: 9, y: 9, width: 6, height: 6, rx: 1 }], "M9 2v3", "M15 2v3", "M9 19v3", "M15 19v3", "M2 9h3", "M2 15h3", "M19 9h3", "M19 15h3"],
  platform: ["m12 2 10 5-10 5L2 7z", "m2 17 10 5 10-5", "m2 12 10 5 10-5"],
  organization: [["rect", { x: 4, y: 2, width: 16, height: 20, rx: 2 }], "M9 22v-4h6v4", "M8 6h.01", "M12 6h.01", "M16 6h.01", "M8 10h.01", "M12 10h.01", "M16 10h.01", "M8 14h.01", "M12 14h.01", "M16 14h.01"],
  service: [["rect", { x: 2, y: 3, width: 20, height: 8, rx: 2 }], ["rect", { x: 2, y: 13, width: 20, height: 8, rx: 2 }], "M6 7h.01", "M6 17h.01"],
  acronym: ["M4 7V4h16v3", "M9 20h6", "M12 4v16"],
  // artifacts: what sessions made
  artifacts: ["M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z",
    "M12 22V12", "m3.3 7 7.7 4.7a2 2 0 0 0 2 0L20.7 7", "m7.5 4.27 9 5.15"],
  page: [["rect", { x: 3, y: 4, width: 18, height: 16, rx: 2 }], "M3 9h18", "M7 6.5h.01", "M10 6.5h.01"],
  diagram: [["rect", { x: 3, y: 3, width: 6, height: 6, rx: 1 }], ["rect", { x: 15, y: 15, width: 6, height: 6, rx: 1 }], "M6 9v3a3 3 0 0 0 3 3h6"],
  deck: ["M2 3h20", "M21 3v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V3", "m7 21 5-5 5 5"],
  sheet: [["rect", { x: 3, y: 3, width: 18, height: 18, rx: 2 }], "M3 9h18", "M3 15h18", "M9 9v12", "M15 9v12"],
  image: [["rect", { x: 3, y: 3, width: 18, height: 18, rx: 2 }], ["circle", { cx: 9, cy: 9, r: 2 }], "m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"],
  published: [C10, "M2 12h20", "M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"],
  pr: [["circle", { cx: 18, cy: 18, r: 3 }], ["circle", { cx: 6, cy: 6, r: 3 }], "M13 6h3a2 2 0 0 1 2 2v7", "M6 9v12"],
  commit: [["circle", { cx: 12, cy: 12, r: 3 }], "M3 12h6", "M15 12h6"],
  copy: [["rect", { x: 8, y: 8, width: 14, height: 14, rx: 2 }], "M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"],
};
ICONS.doc = ICONS.file;
ICONS.person = ICONS.preference;
ICONS.team = ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", ["circle", { cx: 9, cy: 7, r: 4 }], "M22 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"];
ICONS.other = ICONS.dot;
ICONS.shield = ["M12 3 3 6v6c0 5 9 9 9 9s9-4 9-9V6z", "m8 12 3 3 5-6"];
function icon(name, cls = "") {
  return s("svg", { class: `icon ${cls}`, viewBox: "0 0 24 24", "aria-hidden": "true" },
    (ICONS[name] || ICONS.dot).map((p) => (typeof p === "string" ? s("path", { d: p }) : s(p[0], p[1]))));
}
// 3D art: the Chatixia Studio cast, rendered by packaging/icons3d. It marks pages, Home tiles and empty states;
// the line icons above stay for everything small, since they follow the theme colour
function art(name, cls = "") {
  return h("img", { class: `art ${cls}`, src: `art-${name}.webp`, alt: "", "aria-hidden": "true", draggable: "false" });
}
function titled(name, ...children) { return h("div", { class: "head-title" }, art(name), h("div", null, ...children)); }
function emptyArt(name, ...children) { return h("div", { class: "empty empty-art" }, art(name), h("div", null, ...children)); }

// =====================================================================================
// Formatting
// =====================================================================================
const nf = new Intl.NumberFormat(LOCALE);
function fmtNum(n) { return n == null ? "–" : nf.format(Math.round(n)); }
function fmtCompact(n) {
  if (n == null || isNaN(n)) return "–";
  const a = Math.abs(n);
  const f = (v, u) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1).replace(/\.0$/, "") : v.toFixed(1).replace(/\.0$/, "")) + u;
  if (a >= 1e9) return f(n / 1e9, "B");
  if (a >= 1e6) return f(n / 1e6, "M");
  if (a >= 1e3) return f(n / 1e3, "k");
  return String(Math.round(n));
}
function fmtCost(v) {
  if (v == null) return "–";
  if (v >= 1000) return "$" + nf.format(Math.round(v));
  if (v >= 100) return "$" + v.toFixed(0);
  return "$" + v.toFixed(2);
}
function fmtDur(sec) {
  if (sec == null) return "–";
  sec = Math.round(sec);
  if (sec < 60) return t("{n}s", { n: sec });
  const m = Math.floor(sec / 60), hh = Math.floor(m / 60), d = Math.floor(hh / 24);
  if (d >= 1) return t("{d}d {h}h", { d, h: hh % 24 });
  if (hh >= 1) return t("{h}h {m}m", { h: hh, m: LANG === "ja" ? m % 60 : String(m % 60).padStart(2, "0") });
  return t("{m}m", { m });
}
function fmtHours(sec) { if (sec == null) return "–"; const hrs = sec / 3600; return t("{n}h", { n: hrs >= 10 ? Math.round(hrs) : Math.round(hrs * 10) / 10 }); }
const dateFmt = new Intl.DateTimeFormat(LOCALE, { month: "short", day: "numeric" });
const dateYFmt = new Intl.DateTimeFormat(LOCALE, { year: "numeric", month: "short", day: "numeric" });
const dtFmt = new Intl.DateTimeFormat(LOCALE, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const timeFmt = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit" });
const wdFmt = new Intl.DateTimeFormat(LOCALE, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
function d(ts) { return ts ? new Date(ts) : null; }
function fmtDate(ts) { return ts ? dateFmt.format(d(ts)) : "–"; }
function fmtDateY(ts) { return ts ? dateYFmt.format(d(ts)) : "–"; }
function fmtDT(ts) { return ts ? dtFmt.format(d(ts)) : "–"; }
function fmtTime(ts) { return ts ? timeFmt.format(d(ts)) : ""; }
function ago(ts) {
  if (!ts) return t("never");
  const sec = (Date.now() - new Date(ts).getTime()) / 1000;
  if (sec < 60) return t("just now");
  if (sec < 3600) return t("{n} min ago", { n: Math.round(sec / 60) });
  if (sec < 86400) return t("{n} h ago", { n: Math.round(sec / 3600) });
  const days = Math.round(sec / 86400);
  return days === 1 ? t("1 d ago") : t("{n} d ago", { n: days });
}
function localDate(ts) { // YYYY-MM-DD in local time
  const x = new Date(ts);
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
}
function shortPath(p, root) {
  if (!p) return "";
  if (root && p.startsWith(root.replace(/\/$/, "") + "/")) return p.slice(root.replace(/\/$/, "").length + 1);
  return p.replace(/^\/Users\/[^/]+/, "~");
}

const KIND = {
  fix: t("Fix"), gotcha: t("Gotcha"), learning: t("Learning"), decision: t("Decision"), pattern: t("Pattern"),
  command: t("Command"), fact: t("Fact"), preference: t("Preference"), reference: t("Reference"), todo: t("Todo"),
};
const KIND_PLURAL = {
  fix: t("Fixes"), gotcha: t("Gotchas"), learning: t("Learnings"), decision: t("Decisions"), pattern: t("Patterns"),
  command: t("Commands"), fact: t("Facts"), preference: t("Preferences"), reference: t("References"), todo: t("Todos"),
};
function kindPlural(k) { return KIND_PLURAL[k] || kindLabel(k); }
function kindLabel(k) { return KIND[k] || k || t("Note"); }
function kindChip(k) { return h("span", { class: "kind-chip" }, icon(KIND[k] ? k : "dot"), kindLabel(k)); }
// glossary categories (glossary.py): the codes stay, only their labels are translated
const GLOSS_CAT = {
  file: t("file"), concept: t("concept"), component: t("component"), data: t("data"), domain: t("domain"), tool: t("tool"),
  library: t("library"), system: t("system"), platform: t("platform"), organization: t("organization"), service: t("service"),
  acronym: t("acronym"), command: t("command"), person: t("person"), other: t("other"),
};
// session work types (analyze.py WORK_TYPES)
const WORK_TYPE = {
  feature: t("feature"), bugfix: t("bugfix"), debugging: t("debugging"), refactor: t("refactor"), research: t("research"),
  exploration: t("exploration"), ops: t("ops"), deploy: t("deploy"), config: t("config"), docs: t("docs"), testing: t("testing"),
  review: t("review"), planning: t("planning"), data: t("data"), design: t("design"), learning: t("learning"), other: t("other"),
};
function workTypeLabel(w) { return WORK_TYPE[w] || w; }
// session sentiment and friction kinds (analyze.py)
const SENTIMENT = { positive: t("positive"), neutral: t("neutral"), frustrated: t("frustrated"), mixed: t("mixed"), unclear: t("unclear") };
const FRICTION_KIND = {
  tool_error: t("tool error"), environment: t("environment"), misunderstanding: t("misunderstanding"), rework: t("rework"),
  permissions: t("permissions"), performance: t("performance"), external: t("external"), other: t("other"),
};
function catLabel(c) { return GLOSS_CAT[c || "other"] || c; }
function catBadge(c) { return h("span", { class: "badge cat" }, icon(ICONS[c] && c !== "x" ? c : "dot"), catLabel(c)); }
// outcome -> [status class, label]; status colors always travel with an icon and a label
const OUTCOME = {
  completed: ["good", t("Completed")], partial: ["warning", t("Partial")], blocked: ["critical", t("Blocked")],
  abandoned: ["serious", t("Abandoned")], exploratory: ["accent", t("Exploratory")], unclear: ["", t("Unclear")],
};
const STATUS_LABEL = {
  pending: t("queued"), stale: t("needs re-analysis"), running: t("analyzing…"), error: t("analysis failed"), skipped: t("skipped"), done: t("analyzed"),
};
const RUN_STATUS = { done: t("done"), error: t("Error"), running: t("analyzing…") }; // a row of a session's analysis runs
const STATUS_ICON = { pending: "queued", stale: "queued", running: "running", error: "gotcha", skipped: "skipped", done: "completed" };

const AGENTS = { claude: "Claude Code", codex: "Codex", copilot: "GitHub Copilot", bob: "IBM Bob", antigravity: "Google Antigravity", "claude-ai": "Claude.ai", chatgpt: "ChatGPT" };
const AGENT_SHORT = { claude: "Claude", codex: "Codex", copilot: "Copilot", bob: "Bob", antigravity: "Antigravity", "claude-ai": "Claude.ai", chatgpt: "ChatGPT" };
function agentShort(a) { return AGENT_SHORT[a] || a || "Claude"; }
function agentName(a) { return AGENTS[a] || "Claude Code"; }
// the agent that analyzes sessions (Status › Analysis): full name, and the short one for "… is writing"
function analyzer() { return lastStatus?.analysis?.label || "Claude Code"; }
function analyzerShort() { return analyzer() === "Claude Code" ? "Claude" : analyzer(); }
function agentCell(x) { // the Agent column: the Sources page's colour dot and the short name
  const cloud = x.source === "codex-cloud";
  return h("td", { class: "nowrap", title: cloud ? t("Codex Cloud task") : agentName(x.agent) }, h("span", { class: "agent-cell" },
    h("span", { class: `src-dot a-${cloud ? "codex-cloud" : x.agent || "claude"}`, "aria-hidden": "true" }), agentShort(x.agent)),
    cloud ? h("div", { class: "muted small agent-sub" }, "Cloud") : null);
}
function agentTag(a, title) { return a && a !== "claude" ? h("span", { class: `agent-tag a-${a}`, title: title || t("{agent} session", { agent: agentName(a) }) }, agentShort(a)) : null; }

function outcomeBadge(outcome, status, source) {
  if (source === "history") return h("span", { class: "badge", title: t("Recovered from prompt history; transcript was deleted before Interlatch") }, icon("history"), t("history"));
  if (outcome && OUTCOME[outcome]) {
    const [cls, label] = OUTCOME[outcome];
    return h("span", { class: `badge ${cls}` }, icon(outcome), label);
  }
  const cls = status === "error" ? "critical" : status === "running" ? "accent" : "";
  return h("span", { class: `badge ${cls}` }, icon(STATUS_ICON[status] || "dot", status === "running" ? "spin" : ""), STATUS_LABEL[status] || status || "–");
}
// Screening of imported chats (screen.py): is a full analysis worth it, read from the chat's opening
const SCREEN = { analyze: ["good", t("Worth analyzing")], maybe: ["warning", t("Maybe")], skip: ["", t("Not worth it")] };
function screenNote(x) { // a list row's verdict and why, while the chat is not analyzed
  if (!SCREEN[x.screen_verdict] || x.analysis_status === "done") return null;
  const [cls, label] = SCREEN[x.screen_verdict];
  return h("div", { class: "s" }, h("span", { class: `screen-v ${cls}` }, label), x.screen_reason ? `: ${x.screen_reason}` : null);
}
// Maturity: how well an item is established, earned by recurring across sessions (see ladder.py)
// Plan usage from Claude Code's status line (statusline.py). Absent means not recorded: never shown as zero.
const LIMIT_LABEL = { five_hour: t("5-hour"), seven_day: t("7-day"), spend_limit: t("spend") };
function planLine(plan) {
  const ws = Object.entries(plan?.limits || {});
  if (!ws.length) return null;
  return ws.map(([k, v]) => t("{limit} limit {pct}% (resets {when})", { limit: LIMIT_LABEL[k] || k, pct: Math.round(v.used_pct), when: fmtDT(v.resets_at) })).join(" · ")
    + " · " + t("as of {ago}", { ago: ago(plan.as_of) });
}
function planFact(usage, sfact) {
  const moved = Object.entries(usage?.limits_moved || {}).filter(([, v]) => v != null);
  if (!moved.length) return null;
  const [k, v] = moved.find(([k]) => k === "five_hour") || moved[0];
  const pct = (x) => `${x < 10 ? x.toFixed(1) : Math.round(x)}%`;
  const el = sfact(t("{limit} limit used", { limit: LIMIT_LABEL[k] || k }), pct(v),
    moved.filter(([x]) => x !== k).map(([x, y]) => `${LIMIT_LABEL[x] || x} ${pct(y)}`).join(" · ") || null);
  el.title = t("How far your plan limits moved while this session ran (includes anything else using the same account then)");
  return el;
}
// Why sessions wait for analysis (worker.waiting_reason): queued ones clear by themselves, held ones need a change
const QUEUE_REASON_LABEL = { ready: t("ready now"), active: t("still active"), retry: t("retry later"), excluded: t("project excluded"),
  too_short: t("too few prompts"), before_install: t("before install"), gave_up: t("failed, not retried") };
const QUEUE_REASON_HINT = { active: t("Analyzed once the session has been idle for analysis.idle_minutes"),
  retry: t("A failed attempt is retried with a growing delay"), excluded: "sources.exclude_projects",
  too_short: "analysis.min_prompts", before_install: t("Turn on analysis.backfill to analyze older sessions"),
  gave_up: t("Open the session to see the error; re-run it from its page") };
const STAGE_LABEL = { wip: t("tentative"), provisional: t("seen once"), established: t("established"), canonical: t("canonical") };
const STAGE_RANK = { canonical: 0, established: 1, provisional: 2, wip: 3 };
function confirmCount(k) {
  if (k.confirmations != null) return k.confirmations;
  let ids = [];
  try { ids = JSON.parse(k.confirmed_json || "[]") || []; } catch (e) { ids = []; }
  return new Set([k.session_id, ...ids].filter(Boolean)).size;
}
function stageTag(stage, n, reason, { quiet = false } = {}) {
  if (!stage || (quiet && stage === "provisional")) return null;
  const label = STAGE_LABEL[stage] || stage;
  return h("span", { class: `stage-tag s-${stage}`, title: reason || label }, label, n > 1 ? h("b", null, `×${n}`) : null);
}
function confidenceMeter(level) { // three steps; the fill is the accent, the track a lighter step of it
  const n = { high: 3, medium: 2, low: 1 }[level] || 0;
  const label = { high: t("high confidence"), medium: t("medium confidence"), low: t("low confidence") }[level];
  return n ? h("span", { class: "meter", title: label, "aria-label": label },
    [1, 2, 3].map((i) => h("i", { class: i <= n ? "on" : "" }))) : null;
}

// =====================================================================================
// Markdown (escape first, then a small safe subset)
// =====================================================================================
function mdInline(t) {
  const codes = [];
  t = t.replace(/`([^`\n]+)`/g, (m, c) => { codes.push(`<code>${c}</code>`); return `\u0001${codes.length - 1}\u0001`; });
  t = t.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
  t = t.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  t = t.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  t = t.replace(/&lt;sub&gt;(.*?)&lt;\/sub&gt;/g, '<span class="muted">$1</span>');
  return t.replace(/\u0001(\d+)\u0001/g, (m, i) => codes[+i]);
}
function md(src) {
  if (!src) return "";
  const blocks = [];
  let text = escapeHtml(src).replace(/```[\w+-]*[^\S\n]*\n?([\s\S]*?)```/g, (m, code) => {
    blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
    return `\n\u0000${blocks.length - 1}\u0000\n`;
  });
  const out = [];
  let list = null, para = [], table = null;
  const flushPara = () => { if (para.length) { out.push(`<p>${mdInline(para.join("<br>"))}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.type}>${list.items.map((i) => `<li>${mdInline(i)}</li>`).join("")}</${list.type}>`); list = null; } };
  const flushTable = () => {
    if (table) {
      const rows = table.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r));
      out.push("<table>" + rows.map((r, i) => "<tr>" + r.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|")
        .map((c) => (i === 0 ? `<th>${mdInline(c.trim())}</th>` : `<td>${mdInline(c.trim())}</td>`)).join("") + "</tr>").join("") + "</table>");
      table = null;
    }
  };
  for (const line of text.split("\n")) {
    let m;
    if (/^\u0000\d+\u0000$/.test(line.trim())) { flushPara(); flushList(); flushTable(); out.push(line.trim()); continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) { flushPara(); flushList(); (table ||= []).push(line); continue; }
    flushTable();
    if ((m = line.match(/^(#{1,4})\s+(.*)/))) { flushPara(); flushList(); out.push(`<h4>${mdInline(m[2])}</h4>`); continue; }
    if ((m = line.match(/^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.*)/))) {
      flushPara();
      const type = /^\s*\d/.test(line) ? "ol" : "ul";
      if (!list || list.type !== type) { flushList(); list = { type, items: [] }; }
      list.items.push(m[1]);
      continue;
    }
    if ((m = line.match(/^&gt;\s?(.*)/))) { flushPara(); flushList(); out.push(`<blockquote>${mdInline(m[1])}</blockquote>`); continue; }
    if (!line.trim()) { flushPara(); flushList(); continue; }
    if (list && /^\s{2,}\S/.test(line)) { list.items[list.items.length - 1] += " " + line.trim(); continue; }
    flushList();
    para.push(line);
  }
  flushPara(); flushList(); flushTable();
  return out.join("").replace(/\u0000(\d+)\u0000/g, (m, i) => blocks[+i]);
}
function mdEl(src, cls = "") { return h("div", { class: `md gloss ${cls}`, html: md(src) }); }
function snippetEl(text) { // server marks matches with «»
  return h("span", { html: escapeHtml(text).replace(/«(.*?)»/g, '<mark class="qhit">$1</mark>') });
}
function queryTerms(q) { // the words and "quoted phrases" a search matched on, as search.py splits them
  return (String(q || "").trim().match(/"[^"]+"|\S+/g) || []).map((t) => t.replace(/^"|"$/g, "")).filter(Boolean);
}
function markTerms(root, terms) { // wrap every occurrence of the terms under root in <mark class="qhit">
  if (!root || !terms?.length) return;
  const re = new RegExp([...terms].sort((a, b) => b.length - a.length).map(escRe).join("|"), "gi");
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement?.closest("mark, button, select, input, textarea") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.nodeValue;
    let m, last = 0, frag = null;
    re.lastIndex = 0;
    while ((m = re.exec(text))) {
      frag ||= document.createDocumentFragment();
      frag.append(text.slice(last, m.index), h("mark", { class: "qhit" }, m[0]));
      last = m.index + m[0].length;
    }
    if (frag) { frag.append(text.slice(last)); node.replaceWith(frag); }
  }
}

// =====================================================================================
// API
// =====================================================================================
async function api(path, params) {
  const url = new URL(path, location.origin);
  if (params) for (const [k, v] of Object.entries(params)) if (v != null && v !== "") url.searchParams.set(k, v);
  const res = await fetch(url, { headers: { "X-Interlatch-Lang": LANG } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    if (res.status === 401 && body.signin) { showSignin(); throw handledError(body.error); }
    if (res.status === 403 && body.nobody) { showSignin(body.error); throw handledError(body.error); }
    throw new Error(body.error || res.statusText);
  }
  return res.json();
}
// A hub with people answers 401 {signin: true} to someone not signed in (the sign-in screen takes over) and 403 to
// a viewer whose role doesn't allow the change (said in a toast). Both reject with an error already shown. A hub
// without people answers another device 403 {nobody: true}: the sign-in screen, saying how to add the first admin.
async function post(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Interlatch": "1", "X-Interlatch-Lang": LANG }, body: JSON.stringify(body || {}) });
  const data = await res.json().catch(() => ({ error: res.statusText }));
  if (res.status === 401 && data.signin) { showSignin(); throw handledError(data.error); }
  if (res.status === 403 && data.nobody) { showSignin(data.error); throw handledError(data.error); }
  if (res.status === 403) { toast(data.error || t("Only an admin of this hub can change this."), 6000); throw handledError(data.error); }
  return data;
}
function handledError(msg) { const e = new Error(msg || "forbidden"); e.handled = true; return e; }
window.addEventListener("unhandledrejection", (e) => { if (e.reason?.handled) e.preventDefault(); }); // already shown
let toastTimer;
function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

// =====================================================================================
// Tooltip (values lead, labels follow)
// =====================================================================================
const tip = () => $("#tooltip");
function showTip(evt, value, label, extra) {
  const t = tip();
  t.replaceChildren(...[ // replaceChildren would print a null as the text "null"
    h("div", { class: "tt-value" }, value),
    label ? h("div", { class: "tt-label" }, label) : null,
    extra ? h("div", { class: "tt-label" }, extra) : null,
  ].filter(Boolean));
  t.hidden = false;
  moveTip(evt);
}
function moveTip(evt) {
  const t = tip();
  const pad = 14, r = t.getBoundingClientRect();
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - pad;
  t.style.left = x + "px";
  t.style.top = y + "px";
}
function hideTip() { tip().hidden = true; }
function hoverable(node, getContent, { focusable = true } = {}) {
  node.addEventListener("pointerenter", (e) => { const [v, l, x] = getContent(); showTip(e, v, l, x); node.classList.add("hover"); });
  node.addEventListener("pointermove", moveTip);
  node.addEventListener("pointerleave", () => { hideTip(); node.classList.remove("hover"); });
  if (!focusable) return; // inside a link or row that is itself focusable
  node.setAttribute("tabindex", "0");
  node.addEventListener("focus", () => { const r = node.getBoundingClientRect(); const [v, l, x] = getContent(); showTip({ clientX: r.right, clientY: r.top }, v, l, x); });
  node.addEventListener("blur", hideTip);
}

// =====================================================================================
// Charts
// =====================================================================================
function niceTicks(max, count = 4) {
  if (!max || max <= 0) return [0, 1];
  const raw = max / count, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((st) => st >= raw) || raw;
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}
function barPath(x, y, w, hgt, r) {
  if (hgt <= 0) return "";
  r = Math.min(r, w / 2, hgt);
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
}
function responsive(container, draw) {
  let last = 0;
  const run = () => { const w = container.clientWidth; if (w && Math.abs(w - last) > 2) { last = w; container.replaceChildren(); draw(w); } };
  const ro = new ResizeObserver(() => requestAnimationFrame(run));
  ro.observe(container);
  requestAnimationFrame(run);
}

// A chart card: title, hint, optional tools, and a table-view twin.
function chartCard(title, hint, drawFn, table, tools = [], iconName = null) {
  const body = h("div", { class: "chart" });
  const tableBox = h("div", { class: "table-scroll", hidden: true });
  let showingTable = false;
  const toggle = h("button", { class: "link-btn", type: "button", onclick: () => {
    showingTable = !showingTable;
    toggle.textContent = showingTable ? t("Chart") : t("Table");
    body.hidden = showingTable;
    tableBox.hidden = !showingTable;
    if (showingTable && !tableBox.firstChild) tableBox.append(renderTable(typeof table === "function" ? table() : table));
  } }, t("Table"));
  const card = h("section", { class: "card" },
    h("div", { class: "card-head" }, h("div", null, h("div", { class: "card-title" }, iconName ? icon(iconName) : null, h("h2", null, title)), hint ? h("div", { class: "hint" }, hint) : null),
      h("div", { class: "tools" }, tools, table ? toggle : null)),
    body, tableBox);
  responsive(body, (w) => drawFn(body, w));
  return card;
}
function renderTable(spec) {
  return h("table", { class: "table-view" },
    h("thead", null, h("tr", null, spec.columns.map((c, i) => h("th", { class: spec.num?.[i] ? "num" : "" }, c)))),
    h("tbody", null, spec.rows.map((r) => h("tr", null, r.map((c, i) => h("td", { class: spec.num?.[i] ? "num" : "" }, c))))));
}

function columnChart(container, width, data, { value, fmt, label, height = 210, avg = 0, name = t("Daily"), unit = 1 }) {
  const m = { l: 44, r: 8, t: 10, b: 24 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const vals = data.map(value);
  const max = Math.max(0, ...vals);
  const ticks = niceTicks(max / unit).map((t) => t * unit);
  const top = ticks[ticks.length - 1] || 1;
  const band = iw / Math.max(data.length, 1);
  const bw = Math.max(2, Math.min(24, band - 2));
  const svg = s("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": t("column chart") });
  const g = s("g", { transform: `translate(${m.l},${m.t})` });
  svg.append(g);
  for (const t of ticks) {
    const y = ih - (t / top) * ih;
    g.append(s("line", { class: t === 0 ? "baseline" : "gridline", x1: 0, x2: iw, y1: Math.round(y) + 0.5, y2: Math.round(y) + 0.5 }));
    g.append(s("text", { class: "axis tick-num chart-label", x: -8, y: y + 3.5, "text-anchor": "end" }, fmt(t)));
  }
  // trailing moving average, same unit and axis as the bars
  const means = avg ? vals.map((_, i) => { const win = vals.slice(Math.max(0, i - avg + 1), i + 1); return win.reduce((a, b) => a + b, 0) / win.length; }) : null;
  const every = Math.max(1, Math.ceil(data.length / Math.max(2, Math.floor(iw / 70))));
  data.forEach((dd, i) => {
    const v = vals[i], x = i * band + (band - bw) / 2, bh = (v / top) * ih;
    if (bh > 0) g.append(s("path", { class: "bar", d: barPath(x, ih - bh, bw, Math.max(bh, 1), 4) }));
    if (i % every === 0) g.append(s("text", { class: "chart-label", x: i * band + band / 2, y: ih + 16, "text-anchor": "middle" }, fmtDate(dd.date + "T12:00:00")));
  });
  if (means && data.length > avg) {
    g.append(s("path", { class: "avg-line", d: "M" + means.map((v, i) => `${(i * band + band / 2).toFixed(1)},${(ih - (v / top) * ih).toFixed(1)}`).join("L") }));
    container.append(legendKey([["accent", name], ["line", t("{n}-day average", { n: avg })]]));
  }
  data.forEach((dd, i) => {
    const hit = s("rect", { class: "hit", x: i * band, y: 0, width: band, height: ih });
    hoverable(hit, () => [fmt(vals[i]), label(dd), means ? t("{n}-day average {value}", { n: avg, value: fmt(means[i]) }) : null]);
    g.append(hit);
  });
  container.append(svg);
}

function heatColor(level) { return `var(--seq-${level})`; }

function calendarHeatmap(container, width, rows) {
  const byDate = Object.fromEntries(rows.map((r) => [r.date, r]));
  const today = new Date(); today.setHours(12, 0, 0, 0);
  const first = rows.length ? new Date(rows[0].date + "T12:00:00") : today;
  const active = new Set(rows.filter((r) => r.sessions).map((r) => r.date));
  let longest = 0, run = 0;
  for (const d = new Date(first); d <= today; d.setDate(d.getDate() + 1)) {
    run = active.has(localDate(d)) ? run + 1 : 0;
    longest = Math.max(longest, run);
  }
  let streak = 0;
  const back = new Date(today);
  if (!active.has(localDate(back))) back.setDate(back.getDate() - 1); // today's first session may still be ahead
  while (active.has(localDate(back))) { streak++; back.setDate(back.getDate() - 1); }
  const busiest = rows.reduce((b, r) => ((r.active_s || 0) > (b?.active_s || 0) ? r : b), null);
  const side = width >= 560 ? 190 : 0;
  const left = 30, topPad = 16, gap = 3, gridW = width - side;
  const dataWeeks = Math.ceil((today - first) / (7 * 86400000)) + 1;
  const weeks = Math.max(1, Math.min(53, Math.floor((gridW - left) / (10 + gap)), Math.max(dataWeeks, 18)));
  const start = new Date(today); start.setDate(start.getDate() - start.getDay() - (weeks - 1) * 7);
  const cell = Math.max(9, Math.min(20, Math.floor((gridW - left) / weeks) - gap));
  const step = cell + gap;
  const height = topPad + 7 * step + 4, svgW = left + weeks * step;
  const svg = s("svg", { width: svgW, height, viewBox: `0 0 ${svgW} ${height}`, role: "img", "aria-label": t("activity calendar") });
  const days = (n) => tn(n, "{n} day", "{n} days");
  const stats = h("div", { class: "cal-stats" },
    fact(t("current streak"), days(streak)), fact(t("longest streak"), days(longest)), fact(t("active days this year"), fmtNum(active.size)),
    busiest?.active_s ? fact(t("busiest day"), `${fmtDate(busiest.date + "T12:00:00")} · ${fmtDur(busiest.active_s)}`) : null);
  const thresholds = [1, 15 * 60, 30 * 60, 3600, 2 * 3600, 4 * 3600];
  [t("Mon"), t("Wed"), t("Fri")].forEach((lbl, i) => svg.append(s("text", { class: "chart-label", x: 0, y: topPad + (1 + i * 2) * step + cell - 2 }, lbl)));
  let lastMonth = -1;
  for (let w = 0; w < weeks; w++) {
    for (let dow = 0; dow < 7; dow++) {
      const day = new Date(start); day.setDate(start.getDate() + w * 7 + dow);
      if (day > today) continue;
      const key = localDate(day), r = byDate[key];
      const activeS = r ? r.active_s || 0 : 0;
      let level = 0;
      thresholds.forEach((t, i) => { if (activeS >= t) level = i + 1; });
      if (r && r.sessions && level === 0) level = 1;
      const rect = s("rect", { class: "cell", x: left + w * step, y: topPad + dow * step, width: cell, height: cell, rx: cell > 12 ? 3 : 2, fill: heatColor(level) });
      hoverable(rect, () => [r ? t("{dur} active", { dur: fmtDur(activeS) }) : t("No sessions"), wdFmt.format(day),
        r ? `${tn(r.sessions, "{n} session", "{n} sessions")} · ${tn(r.prompts || 0, "{n} prompt", "{n} prompts")}` : null]);
      rect.addEventListener("click", () => r && go(`#/sessions?day=${key}`));
      svg.append(rect);
      if (dow === 0 && day.getMonth() !== lastMonth && day.getDate() <= 7) {
        lastMonth = day.getMonth();
        svg.append(s("text", { class: "chart-label", x: left + w * step, y: 10 }, day.toLocaleString(LOCALE, { month: "short" })));
      }
    }
  }
  container.append(h("div", { class: "cal-wrap" },
    h("div", null, svg, scaleLegend([t("none"), t("<15m"), t("15–30m"), t("30m–1h"), t("1–2h"), t("2–4h"), t("4h+")])), stats));
}
function scaleLegend(labels) {
  return h("div", { class: "scale-legend" }, t("Less"), labels.map((l, i) => h("i", { style: { background: heatColor(i) }, title: l })), t("More"));
}

function hourGrid(container, width, rows) {
  const counts = {}; let max = 0;
  rows.forEach((r) => { counts[`${r.dow}-${r.hour}`] = r.n; max = Math.max(max, r.n); });
  const left = 34, topPad = 4, bottom = 18, gap = 2;
  const cell = Math.max(8, Math.min(24, Math.floor((width - left) / 24) - gap));
  const step = cell + gap, height = topPad + 7 * step + bottom;
  const svg = s("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": t("activity by hour of week") });
  const order = [1, 2, 3, 4, 5, 6, 0], names = [t("Mon"), t("Tue"), t("Wed"), t("Thu"), t("Fri"), t("Sat"), t("Sun")];
  order.forEach((dow, row) => {
    svg.append(s("text", { class: "chart-label", x: 0, y: topPad + row * step + cell - 3 }, names[row]));
    for (let hr = 0; hr < 24; hr++) {
      const n = counts[`${dow}-${hr}`] || 0;
      const level = n === 0 ? 0 : Math.min(6, 1 + Math.floor((n / (max || 1)) * 5.999));
      const rect = s("rect", { class: "cell", x: left + hr * step, y: topPad + row * step, width: cell, height: cell, rx: 2, fill: heatColor(level) });
      hoverable(rect, () => [tn(n, "{n} prompt", "{n} prompts"), `${names[row]} ${String(hr).padStart(2, "0")}:00–${String((hr + 1) % 24).padStart(2, "0")}:00`]);
      svg.append(rect);
    }
  });
  for (let hr = 0; hr < 24; hr += 3) svg.append(s("text", { class: "chart-label", x: left + hr * step, y: height - 4 }, String(hr).padStart(2, "0")));
  const peak = rows.reduce((b, r) => (r.n > (b?.n || 0) ? r : b), null);
  const dayName = [t("Sunday"), t("Monday"), t("Tuesday"), t("Wednesday"), t("Thursday"), t("Friday"), t("Saturday")];
  container.append(svg, h("div", { class: "chart-foot" }, scaleLegend(["0", "", "", "", "", "", t("max")]),
    peak ? h("span", { class: "chart-note" }, tx("Busiest hour: {when} · {n} prompts",
      { when: h("b", null, `${dayName[peak.dow]} ${String(peak.hour).padStart(2, "0")}:00`), n: fmtNum(peak.n) })) : null));
}

// Horizontal bars; `part` splits each bar's end into a second (critical) segment, e.g. failed calls
function hbars(items, { label, value, fmt, href, sub, max, part, lead } = {}) {
  const top = max ?? Math.max(0, ...items.map(value));
  if (!items.length) return h("div", { class: "empty" }, t("No data"));
  return h("div", { class: "hbars" }, items.map((it) => {
    const v = value(it), p = part ? Math.min(part(it) || 0, v) : 0;
    const text = label(it);
    const name = href ? h("a", { class: "name", href: href(it), title: text }, lead ? lead(it) : null, text) : h("span", { class: "name", title: text }, lead ? lead(it) : null, text);
    const pct = (n) => (top ? (n / top) * 100 : 0);
    const row = h("div", { class: "hbar" }, name,
      h("div", { class: "track" }, h("div", { class: `fill ${p ? "has-part" : ""}`, style: { width: `${Math.max(0.5, pct(v - p))}%` } }),
        p ? h("div", { class: "fill part", style: { width: `${pct(p)}%` } }) : null),
      h("span", { class: "val" }, fmt(v), sub ? h("span", { class: "muted" }, " ", sub(it)) : null));
    return row;
  }));
}

// Trend sparkline: the de-emphasis hue for the line, the accent for the current period (last point)
function sparkEl(values, { height = 30 } = {}) {
  const box = h("div", { class: "spark", style: { height: `${height}px` } });
  if (!values.length || values.every((v) => !v)) return box;
  responsive(box, (w) => {
    const max = Math.max(...values), n = values.length, pad = 4;
    const x = (i) => (n === 1 ? w / 2 : pad + (i / (n - 1)) * (w - 2 * pad));
    const y = (v) => height - 4 - (max ? (v / max) * (height - 10) : 0);
    const pts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    box.append(s("svg", { width: w, height, viewBox: `0 0 ${w} ${height}`, "aria-hidden": "true" },
      s("path", { class: "spark-area", d: `M${x(0).toFixed(1)},${height}L${pts.join("L")}L${x(n - 1).toFixed(1)},${height}Z` }),
      s("path", { class: "spark-line", d: "M" + pts.join("L") }),
      s("circle", { class: "spark-dot", cx: x(n - 1), cy: y(values[n - 1]), r: 3.5 })));
  });
  return box;
}

function contextChart(container, width, calls, markers) {
  const main = calls.filter((c) => !c.agent_id);
  if (main.length < 2) { container.append(h("div", { class: "empty" }, t("Not enough API calls to chart"))); return; }
  const note = t("{n} API calls · ticks mark prompts", { n: main.length });
  const noteW = [...note].reduce((w, ch) => w + (ch.codePointAt(0) > 0x2e80 ? 11 : 6), 0);
  // too narrow to sit between the start and end times: drop it to a second row
  const noteRow = noteW + 130 > width - 58;
  const m = { l: 46, r: 12, t: 16, b: noteRow ? 38 : 24 }, height = 210;
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const ctx = main.map((c) => (c.input_tokens || 0) + (c.cache_read_tokens || 0) + (c.cache_write_tokens || 0));
  const ticks = niceTicks(Math.max(...ctx));
  const top = ticks[ticks.length - 1] || 1;
  const x = (i) => (i / (main.length - 1)) * iw, y = (v) => ih - (v / top) * ih;
  const svg = s("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": t("context window per API call") });
  const g = s("g", { transform: `translate(${m.l},${m.t})` });
  svg.append(g);
  for (const t of ticks) {
    g.append(s("line", { class: t === 0 ? "baseline" : "gridline", x1: 0, x2: iw, y1: Math.round(y(t)) + 0.5, y2: Math.round(y(t)) + 0.5 }));
    g.append(s("text", { class: "chart-label tick-num", x: -8, y: y(t) + 3.5, "text-anchor": "end" }, fmtCompact(t)));
  }
  const idxAt = (ts) => { const i = main.findIndex((c) => c.ts >= ts); return i === -1 ? main.length - 1 : i; };
  (markers || []).filter((mk) => mk.kind === "compact").forEach((mk) => {
    const xi = x(idxAt(mk.ts));
    g.append(s("line", { class: "marker-line", x1: xi, x2: xi, y1: -6, y2: ih }));
    g.append(s("text", { class: "marker-label", x: xi + 3, y: -4 }, t("compacted")));
  });
  (markers || []).filter((mk) => mk.kind === "prompt").forEach((mk) => {
    const xi = x(idxAt(mk.ts));
    g.append(s("line", { class: "baseline", x1: xi, x2: xi, y1: ih, y2: ih + 5 }));
  });
  const linePts = ctx.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  g.append(s("path", { class: "area", d: `M0,${ih}L${linePts.join("L")}L${iw},${ih}Z` }));
  g.append(s("path", { class: "line", d: "M" + linePts.join("L") }));
  const peak = ctx.indexOf(Math.max(...ctx));
  const compactXs = (markers || []).filter((mk) => mk.kind === "compact").map((mk) => x(idxAt(mk.ts)));
  const px = x(peak), crowded = compactXs.some((cx) => Math.abs(cx - px) < 90) || y(ctx[peak]) < 14;
  // beside the peak when a compaction label sits above it, otherwise just above
  g.append(s("text", { class: "chart-label peak-label", x: crowded ? Math.max(px - 8, 70) : Math.min(px, iw - 60), y: crowded ? y(ctx[peak]) + 14 : y(ctx[peak]) - 6,
    "text-anchor": crowded ? "end" : "start" }, t("peak {n}", { n: fmtCompact(ctx[peak]) })));
  g.append(s("text", { class: "chart-label", x: 0, y: ih + 17 }, fmtTime(main[0].ts)));
  g.append(s("text", { class: "chart-label", x: iw, y: ih + 17, "text-anchor": "end" }, fmtTime(main[main.length - 1].ts)));
  g.append(s("text", { class: "chart-label", x: iw / 2, y: ih + (noteRow ? 32 : 17), "text-anchor": "middle" }, note));
  const cross = s("line", { class: "crosshair", y1: 0, y2: ih, visibility: "hidden" });
  const dot = s("circle", { class: "dot", r: 4, visibility: "hidden" });
  g.append(cross, dot);
  const overlay = s("rect", { class: "hit", x: 0, y: 0, width: iw, height: ih });
  overlay.addEventListener("pointermove", (e) => {
    const r = overlay.getBoundingClientRect();
    const i = Math.max(0, Math.min(main.length - 1, Math.round(((e.clientX - r.left) / r.width) * (main.length - 1))));
    const c = main[i];
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(ctx[i])); dot.setAttribute("visibility", "visible");
    showTip(e, t("{n} context tokens", { n: fmtCompact(ctx[i]) }), t("Call {i} · {when} · {model}", { i: i + 1, when: fmtDT(c.ts), model: c.model || "" }),
      t("output {out} · cache read {read} · {cost}", { out: fmtCompact(c.output_tokens), read: fmtCompact(c.cache_read_tokens), cost: fmtCost(c.cost_usd) }));
  });
  overlay.addEventListener("pointerleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
  g.append(overlay);
  container.append(svg);
}

// =====================================================================================
// Router
// =====================================================================================
const routes = [];
function route(pattern, view) { routes.push([pattern, view]); }
function go(hash) { if (location.hash === hash) render(); else location.hash = hash; }
function parseHash() {
  if (/^#%2F/i.test(location.hash)) {  // a link that encoded the whole route (VS Code's openExternal does): decode it once
    history.replaceState(null, "", "#" + decodeURIComponent(location.hash.slice(1)));
  }
  const raw = location.hash.slice(1) || "/";
  const [path, qs] = raw.split("?");
  return { path, params: Object.fromEntries(new URLSearchParams(qs || "")) };
}
function setParams(params) {
  const { path } = parseHash();
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "")).toString();
  history.replaceState(null, "", `#${path}${qs ? "?" + qs : ""}`);
  lastHash = location.hash;
  const now = parseHash();
  markSidebar(now.path, now.params);
  if (!/^\/(session\/|project$)/.test(path) && !(path === "/systems" && now.params.system)) setCrumbs(defaultCrumbs(now.path, now.params)); // those pages name themselves
}
// a page that takes more than a moment gets a bar along the top; one that takes long, a note saying so
const LOAD_BAR_MS = 200, LOAD_NOTE_MS = 4000;
const pageLoad = { timers: [], tick: 0, started: 0 };
function loadStarted() {
  pageLoad.timers.forEach(clearTimeout);
  clearInterval(pageLoad.tick);
  pageLoad.started = Date.now();
  $("#app").classList.add("loading");
  $("#app").setAttribute("aria-busy", "true");
  $("#loadnote").hidden = true;
  pageLoad.timers = [setTimeout(showLoadBar, LOAD_BAR_MS), setTimeout(showLoadNote, LOAD_NOTE_MS)];
}
function loadFinished() {
  pageLoad.timers.forEach(clearTimeout);
  clearInterval(pageLoad.tick);
  $("#app").classList.remove("loading");
  $("#app").removeAttribute("aria-busy");
  $("#loadnote").hidden = true;
  const bar = $("#loadbar");
  bar.classList.remove("on");
  setTimeout(() => { if (!bar.classList.contains("on")) bar.hidden = true; }, 300); // after the fade, so the animation stops
}
function showLoadBar() {
  const bar = $("#loadbar");
  bar.hidden = false;
  void bar.offsetWidth; // shown first, so the fade-in runs
  bar.classList.add("on");
}
function showLoadNote() {
  const note = $("#loadnote"), secs = h("span", { class: "ln-secs", "aria-hidden": "true" });
  const tick = () => { secs.textContent = fmtSecs((Date.now() - pageLoad.started) / 1000); };
  // a job the dashboard started (a sync, an import) shares the database with this page
  const running = Object.keys(lastStatus?.jobs || {}).filter((name) => lastStatus.jobs[name].state === "running");
  note.replaceChildren(h("div", null, h("b", null, t("Still loading this page…")), secs),
    ...running.slice(0, 1).map((name) => h("div", { class: "ln-why" }, t("Background work: {job}", { job: jobLabel(name) }))));
  tick();
  pageLoad.tick = setInterval(tick, 1000);
  note.hidden = false;
}

let renderSeq = 0;
async function render() {
  const { path, params } = parseHash();
  const app = $("#app");
  const seq = ++renderSeq;
  if (path !== "/overview") newsVisit = null; // what was new when Team overview opened holds while it stays open
  updateShell(path, params);
  for (const [pattern, view] of routes) {
    const m = path.match(pattern);
    if (!m) continue;
    loadStarted();
    try {
      const [node] = await Promise.all([view(params, ...m.slice(1).map(decodeURIComponent)), loadGlossary()]);
      if (seq !== renderSeq) return;
      app.replaceChildren(node);
      app.querySelectorAll(".gloss").forEach((el) => glossify(el));
    } catch (err) {
      if (seq !== renderSeq) return;
      app.replaceChildren(h("div", { class: "card empty" }, t("Could not load: {error}", { error: err.message })));
    } finally {
      if (seq === renderSeq) loadFinished();
    }
    hideTip();
    return;
  }
  loadFinished(); // a page still loading when the user moved on would otherwise keep its bar
  app.replaceChildren(h("div", { class: "card empty" }, t("Not found")));
}
function navKey(path) {
  if (path.startsWith("/session")) return "sessions";
  if (path.startsWith("/project")) return "projects";
  if (path.startsWith("/knowledge")) return "knowledge";
  if (path === "/learn") return "learn";
  if (path.startsWith("/status")) return "status";
  if (path.startsWith("/reviews")) return "reviews";
  if (path.startsWith("/sources")) return "sources";
  if (path.startsWith("/mcp")) return "mcp";
  if (path.startsWith("/glossary")) return "glossary";
  if (path.startsWith("/map")) return "map";
  if (path.startsWith("/systems")) return "systems";
  if (path.startsWith("/devices")) return "devices";
  if (path.startsWith("/appearance")) return "appearance";
  if (path.startsWith("/storage")) return "storage";
  if (path.startsWith("/search")) return "search";
  if (path.startsWith("/suggestions")) return "suggestions";
  if (path.startsWith("/friction")) return "friction";
  if (path.startsWith("/artifacts")) return "artifacts";
  if (path.startsWith("/team")) return { "/team/projects": "teamprojects", "/team/computers": "teamcomputers", "/team/store": "teamstore", "/team/settings": "teamsettings" }[path] || "team";
  if (path === "/activity") return "activity";
  if (path === "/overview") return "teamhome";
  if (path === "/" || path === "") return dedicated() ? "teamhome" : "overview";
  return "";
}

// ------------------------------------------------------------------ projects and your groups of them (groups.py)
let projectsCache = null, groupsCache = [], homeDir = "", projectsLoading = null;
let groupsHub = null, groupsCanShare = false; // a computer that sends to a hub: its projects there, and whether this is it
// Each project carries the group it's listed under (p.group); groupsCache: A to Z. Callers use the list returned:
// another page may drop the cache (projectsCache = null) while they draw, and one request serves all who ask at once.
async function loadProjects(fresh = false) {
  if (fresh) projectsCache = null;
  if (projectsCache) return projectsCache;
  projectsLoading ||= Promise.all([api("/api/projects"), limited() ? null : api("/api/project-groups").catch(() => null)])
    .then(([list, g]) => {
      groupsCache = g?.groups || [];
      homeDir = g?.home || "";
      groupsHub = g?.hub || null;
      groupsCanShare = !!g?.can_share;
      return (projectsCache = list);
    }).finally(() => { projectsLoading = null; });
  return projectsLoading;
}
function refreshProjects() { projectsCache = null; shellSection = null; render(); } // the sidebar lists them too
// [[group, projects]]: each group A to Z, then the projects in none (group null). hub: the rest leaves out the projects
// listed under their hub project (byHubProject); one in a group stays in it as well.
function byGroup(list, { hub = false } = {}) {
  const sets = new Map(groupsCache.map((g) => [g.id, []]));
  const rest = [];
  for (const p of list) {
    if (sets.has(p.group)) sets.get(p.group).push(p);
    else if (!(hub && p.hub)) rest.push(p);
  }
  return [...groupsCache.map((g) => [g, sets.get(g.id)]), [null, rest]];
}
// On a computer that sends to a hub: [[hub project, the projects here that go to it]], A to Z. The hub project as the
// last push saw it (groupsHub: its name and the team's sessions there).
function byHubProject(list) {
  const out = new Map();
  for (const p of list) {
    if (!p.hub) continue;
    if (!out.has(p.hub.path)) out.set(p.hub.path, [{ ...groupsHub?.projects.find((x) => x.path === p.hub.path), ...p.hub }, []]);
    out.get(p.hub.path)[1].push(p);
  }
  return [...out.values()].sort(([a], [b]) => a.name.localeCompare(b.name));
}
// a project's icon: a folder here that goes to a hub project, a hub's own project (on the hub), or a folder
const projIcon = (p) => (p.hub ? "linked" : p.shared ? "hubproject" : "projects");
function canGroup() { return !limited() && canAdmin(); }
async function projectOptions(selected, { groups = false } = {}) { // groups: a choice for a whole group, valued group:<id>
  const projects = await loadProjects();
  const opt = (p) => h("option", { value: p.project_path, selected: p.project_path === selected }, p.label);
  if (!groupsCache.length) return [h("option", { value: "" }, t("All projects")), ...projects.map(opt)];
  return [h("option", { value: "" }, t("All projects")),
    ...byGroup(projects).filter(([, ps]) => ps.length).map(([g, ps]) => h("optgroup", { label: g ? g.name : t("Other projects") },
      g && groups ? h("option", { value: `group:${g.id}`, selected: selected === `group:${g.id}` }, t("All of {group}", { group: g.name })) : null,
      ps.map(opt)))];
}
// a folded group stays folded in this browser, on the Projects page and in the sidebar alike
let foldedGroups = null;
function groupFolded(key) {
  if (!foldedGroups) {
    try { foldedGroups = new Set(JSON.parse(localStorage.getItem("interlatch.groups.folded") || "[]")); } catch { foldedGroups = new Set(); }
  }
  return foldedGroups.has(key);
}
function foldGroup(key, folded) {
  groupFolded(key);
  folded ? foldedGroups.add(key) : foldedGroups.delete(key);
  try { localStorage.setItem("interlatch.groups.folded", JSON.stringify([...foldedGroups])); } catch { /* remembered for this tab only */ }
}
function under(path, folder) { return path === folder || path.startsWith(folder + "/") || path.startsWith(folder + "\\"); }
// The folder rule a group is offered from the projects picked for it: the folder they share, one per computer
// (aktio-vm:/root/… is another computer's); none for a chat, or one as wide as a home folder
function suggestFolders(paths) {
  const byHost = new Map();
  for (const p of paths) {
    const m = /^([^/\\:]+:)(\/.*)$/.exec(p);
    const [host, rest] = m ? [m[1], m[2]] : ["", p];
    if (!rest.startsWith("/")) continue;
    if (!byHost.has(host)) byHost.set(host, []);
    byHost.get(host).push(rest.split("/").filter(Boolean));
  }
  const out = [];
  for (const [host, lists] of byHost) {
    let common = lists.length === 1 ? lists[0].slice(0, -1) : lists[0];
    for (const l of lists.slice(1)) { let i = 0; while (i < common.length && i < l.length && common[i] === l[i]) i++; common = common.slice(0, i); }
    if (common.length < (["Users", "home"].includes(common[0]) ? 4 : 2)) continue; // ~/Projects at the widest
    out.push(host + "/" + common.join("/"));
  }
  return out;
}
async function moveToGroup(paths, group) {
  const r = await post("/api/project-groups/move", { paths, group });
  if (r.error) return toast(r.error, 6000);
  refreshProjects();
}
function groupMenu(p) {
  const item = (label, hint, on, run) => h("button", { type: "button", role: "menuitem", disabled: on, onclick: () => { menu.open = false; run(); } },
    h("b", null, label), hint ? h("span", null, hint) : null);
  const menu = h("details", { class: "menu pg-menu" },
    h("summary", { class: "btn small", title: t("Move to a group"), "aria-label": t("Move {name} to a group", { name: p.label }) }, icon("projects")),
    h("div", { class: "menu-list", role: "menu" },
      groupsCache.map((g) => item(g.name, p.group === g.id ? (p.group_by === "folder" ? t("In it by its folder") : t("In it now")) : null,
        p.group === g.id, () => moveToGroup([p.project_path], g.id))),
      p.group != null ? item(t("No group"), null, false, () => moveToGroup([p.project_path], null)) : null,
      item(t("New group…"), t("Starting with {name}", { name: p.label }), false, () => groupDialog(null, [p.project_path]))));
  return menu;
}
function closeGroupDialog() { $("#group-dialog")?.remove(); }
const hubProjectName = (path) => groupsHub?.projects.find((x) => x.path === path)?.name || String(path || "").split("/").pop();
// Make or change a group: its name, its folder rules, and the projects in it. Ticks show the group as it will be;
// a rule takes in whole folders (the longest rule of any group wins), and a tick or untick here wins over the rules.
// A new group is offered the folder its first picks share, and that folder's name, until either is changed by hand.
async function groupDialog(g, preset = []) {
  const projects = await loadProjects();
  closeGroupDialog();
  const others = groupsCache.filter((x) => !g || x.id !== g.id);
  let rules = g ? [...g.folders] : [], rulesByHand = !!g, nameByHand = !!g, sure = false;
  const ticks = new Map(preset.map((p) => [p, true])); // ticked or unticked here, over the rules
  const inGroup = (p) => {
    if (ticks.has(p.project_path)) return ticks.get(p.project_path);
    if (p.group_by === "hand") return !!g && p.group === g.id;
    let best = null, len = -1;
    for (const f of rules) if (under(p.project_path, f) && f.length > len) [best, len] = ["here", f.length];
    for (const x of others) for (const f of x.folders) if (under(p.project_path, f) && f.length > len) [best, len] = [x.id, f.length];
    return best === "here";
  };
  const byRule = (p) => !ticks.has(p.project_path) && p.group_by !== "hand" && rules.some((f) => under(p.project_path, f));
  const order = [...projects].sort((a, b) => Number(inGroup(b)) - Number(inGroup(a))); // the group's own first, once
  const nameIn = h("input", { class: "input", id: "group-name", value: g?.name || "", maxlength: 60, placeholder: t("e.g. Aktio"),
    oninput: () => { nameByHand = true; } });
  const folderIn = h("input", { class: "input", placeholder: t("Add a folder, e.g. ~/Projects/Work/Aktio"), "aria-label": t("Add a folder"),
    onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); addRule(); } } });
  const rulesBox = h("div", { class: "gd-rules" }), countEl = h("span", { class: "muted" }), list = h("div", { class: "gd-list" });
  const err = h("div", { class: "gd-err", role: "alert" });
  let q = "";
  // a computer that sends to a hub: the group shared as one project there, linked (hub.group_routes)
  const shareNote = h("div", { class: "muted gd-none" });
  const shareSel = groupsHub ? h("select", { "aria-label": t("Share on the hub"), disabled: !groupsCanShare, onchange: () => draw() },
    h("option", { value: "" }, t("Not shared")),
    [...groupsHub.projects, ...(g?.hub_project && !groupsHub.projects.some((x) => x.path === g.hub_project) ? [{ path: g.hub_project, name: hubProjectName(g.hub_project) }] : [])]
      .map((x) => h("option", { value: x.path, selected: g?.hub_project === x.path }, x.name))) : null;
  const drawShare = () => {
    if (!shareSel) return;
    const to = shareSel.value, members = projects.filter(inGroup);
    const away = members.filter((p) => !String(p.project_path || "").startsWith("/")); // recorded on another computer
    shareNote.replaceChildren(...[
      !groupsCanShare ? t("Change this on the computer itself, not from another device.")
        : !groupsHub.projects.length ? t("No projects on the hub yet, as of the last push. Make one there, or join one in Settings › Devices.")
        : to ? tn(members.length - away.length, "From the next push, its {n} project goes to {name} on the hub, and so does any project that joins the group later. One that leaves it stops going; what it shared stays.",
          "From the next push, its {n} projects go to {name} on the hub, and so does any project that joins the group later. One that leaves it stops going; what it shared stays.",
          { n: fmtNum(members.length - away.length), name: hubProjectName(to) })
        : g?.hub_project ? t("Stops sharing from the next push. What it shared stays on the hub.") : t("Its projects go to the hub only as they would without the group."),
      to && away.length ? h("div", null, t("Not from here, being recorded on another computer: {names}", { names: away.map((p) => p.label).join(", ") })) : null,
      // what goes: this computer's share mode, said where the choice is made (an older server sends none: transcripts)
      to && groupsCanShare ? (groupsHub.share === "knowledge"
        ? h("div", null, t("Each session's summary and project lessons go; transcripts stay on this computer."))
        : h("div", { class: "warn-line", role: "note" }, icon("gotcha"), " ", t("This computer sends whole transcripts, unredacted: every session in these projects goes to the hub as recorded, with any key or password that appeared in it, and the hub's admins can read them."))) : null,
    ].filter(Boolean));
  };
  const addRule = () => {
    let f = folderIn.value.trim().replace(/[/\\]+$/, "");
    if (!f) return;
    if ((f === "~" || f.startsWith("~/")) && homeDir) f = homeDir.replace(/\/$/, "") + f.slice(1);
    if (!rules.includes(f)) rules.push(f);
    rulesByHand = true;
    folderIn.value = "";
    draw();
  };
  const suggest = () => { // a new group, its rules not touched: the folder its picks share
    if (rulesByHand) return;
    rules = suggestFolders([...ticks].filter(([, on]) => on).map(([p]) => p));
    if (!nameByHand) nameIn.value = rules.length ? rules[0].split(/[/\\]/).pop() : (preset.length === 1 ? (projects.find((p) => p.project_path === preset[0])?.label || "") : "");
  };
  const draw = () => {
    rulesBox.replaceChildren(...rules.map((f) => h("div", { class: "gd-rule" }, icon("projects"), h("span", { class: "mono", title: f }, shortPath(f)),
      h("button", { type: "button", class: "icon-btn", "aria-label": t("Remove the rule {folder}", { folder: shortPath(f) }),
        onclick: () => { rules = rules.filter((x) => x !== f); rulesByHand = true; draw(); } }, icon("x")))),
      ...[rules.length ? null : h("div", { class: "muted gd-none" }, t("No folder rule: only the projects ticked below.")),
        !rulesByHand && rules.length ? h("div", { class: "muted gd-none" }, t("Suggested from the projects you picked.")) : null]
        .filter(Boolean)); // replaceChildren would print a null
    const shown = order.filter((p) => !q || p.label.toLowerCase().includes(q) || (p.project_path || "").toLowerCase().includes(q));
    list.replaceChildren(...shown.map((p) => {
      const on = inGroup(p);
      const elsewhere = !on && p.group != null && (!g || p.group !== g.id) ? groupsCache.find((x) => x.id === p.group) : null;
      return h("label", { class: `gd-item${on ? " on" : ""}` },
        h("input", { type: "checkbox", checked: on, onchange: (e) => { ticks.set(p.project_path, e.target.checked); suggest(); draw(); } }),
        h("span", { class: "gd-name" }, h("b", null, p.label), h("span", { class: "muted", title: p.project_path }, shortPath(p.project_path))),
        on && byRule(p) ? h("span", { class: "tag" }, t("by folder")) : elsewhere ? h("span", { class: "tag" }, t("in {group}", { group: elsewhere.name })) : null);
    }), ...(shown.length ? [] : [h("div", { class: "muted gd-none" }, t("No projects match"))]));
    countEl.textContent = tn(projects.filter(inGroup).length, "{n} project in this group", "{n} projects in this group");
    drawShare();
  };
  const save = async () => {
    if (folderIn.value.trim()) addRule(); // typed but not added: what was meant
    const sharing = shareSel && groupsCanShare && shareSel.value && shareSel.value !== (g?.hub_project || "") ? shareSel.value : null;
    if (sharing) { // starts going to a hub project, or to another one: asked before anything is saved
      const n = projects.filter(inGroup).filter((p) => String(p.project_path || "").startsWith("/")).length;
      const vars = { n: fmtNum(n), name: hubProjectName(sharing) };
      if (!confirm(groupsHub.share === "knowledge"
        ? tn(n, "Share the summaries and project lessons of {n} project with {name} on the hub, and of any project that joins the group later?",
          "Share the summaries and project lessons of {n} projects with {name} on the hub, and of any project that joins the group later?", vars)
        : tn(n, "Send the whole transcripts of {n} project to {name} on the hub, unredacted, and of any project that joins the group later?",
          "Send the whole transcripts of {n} projects to {name} on the hub, unredacted, and of any project that joins the group later?", vars))) return;
    }
    const members = projects.filter(inGroup).map((p) => p.project_path);
    const r = await post("/api/project-groups/save", { id: g?.id, name: nameIn.value, folders: rules, members });
    if (r.error) { err.textContent = r.error; return; }
    const to = shareSel && groupsCanShare ? shareSel.value : null, name = nameIn.value.trim();
    if (to != null && to !== (g?.hub_project || "")) {
      g = g || { id: r.id }; // made: a second save edits it, should sharing fail
      const s = await post("/api/project-groups/share", { id: r.id, hub_project: to || null });
      if (s.error) { err.textContent = s.error; refreshProjects(); return; }
      closeGroupDialog();
      toast(to ? t("Saved {name}. It goes to {project} on the hub from the next push.", { name, project: hubProjectName(to) })
        : t("Saved {name}. It stops sharing from the next push; what it shared stays on the hub.", { name }));
      return refreshProjects();
    }
    closeGroupDialog();
    toast(g ? t("Saved {name}.", { name }) : t("Made the group {name}.", { name }));
    refreshProjects();
  };
  const del = h("button", { type: "button", class: "btn danger", onclick: async () => {
    if (!sure) { sure = true; del.textContent = t("Delete {name}? Its projects stay", { name: g.name }); return; }
    const r = await post("/api/project-groups/delete", { id: g.id });
    if (r.error) { err.textContent = r.error; return; }
    closeGroupDialog();
    toast(t("Deleted the group {name}. Its projects are still here.", { name: g.name }));
    refreshProjects();
  } }, t("Delete group"));
  const back = h("div", { id: "group-dialog", class: "gd-back", onclick: (e) => { if (e.target === back) closeGroupDialog(); },
    onkeydown: (e) => { if (e.key === "Escape") closeGroupDialog(); } },
    h("form", { class: "gd card", role: "dialog", "aria-modal": "true", "aria-labelledby": "gd-title", onsubmit: (e) => { e.preventDefault(); save(); } },
      h("div", { class: "gd-head" }, h("h2", { id: "gd-title" }, g ? t("Edit group") : t("New group")),
        h("button", { type: "button", class: "icon-btn", "aria-label": t("Close"), onclick: closeGroupDialog }, icon("x"))),
      h("label", { class: "gd-label", for: "group-name" }, t("Name")), nameIn,
      h("div", { class: "gd-label" }, t("Folder rules"), h("span", { class: "muted" }, t("New projects under these folders join the group on their own."))),
      rulesBox,
      h("div", { class: "gd-add" }, folderIn, h("button", { type: "button", class: "btn small", onclick: addRule }, t("Add"))),
      h("div", { class: "gd-label" }, t("Projects"), countEl),
      h("input", { class: "input", type: "search", placeholder: t("Filter projects"), "aria-label": t("Filter projects"),
        oninput: (e) => { q = e.target.value.toLowerCase(); draw(); } }),
      list,
      shareSel ? [h("label", { class: "gd-label" }, t("On the hub"), h("span", { class: "muted" }, t("Share the whole group as one project there"))), shareSel, shareNote] : null,
      err,
      h("div", { class: "gd-foot" }, g ? del : h("span"),
        h("div", { class: "gd-actions" }, h("button", { type: "button", class: "btn", onclick: closeGroupDialog }, t("Cancel")),
          h("button", { type: "submit", class: "btn primary" }, g ? t("Save") : t("Make group"))))));
  suggest();
  draw();
  document.body.append(back);
  nameIn.focus();
}
// One group on the Projects page: its name and totals over its projects, folded away or open
function groupSection(g, list, body) {
  const key = g ? String(g.id) : "none";
  const sum = (k) => list.reduce((a, p) => a + (p[k] || 0), 0);
  return h("details", { class: "pgroup", open: !groupFolded(key), ontoggle: (e) => foldGroup(key, !e.target.open) },
    h("summary", null, icon("right", "pg-caret"), h("h2", null, g ? g.name : t("Other projects")),
      h("span", { class: "pg-meta" }, tn(list.length, "{n} project", "{n} projects"), " · ", tn(sum("sessions"), "{n} session", "{n} sessions", { n: fmtNum(sum("sessions")) }),
        " · ", t("{dur} active", { dur: fmtDur(sum("active_s")) })),
      g?.hub_project ? h("span", { class: "badge accent", title: t("Every project in this group goes to {name} on the hub, including ones that join it later", { name: hubProjectName(g.hub_project) }) },
        icon("linked"), t("On the hub as {name}", { name: hubProjectName(g.hub_project) })) : null,
      g && g.folders.length ? h("span", { class: "pg-rules mono", title: g.folders.join("\n") }, g.folders.map((f) => shortPath(f)).join(" · ")) : null,
      g && canGroup() ? h("button", { type: "button", class: "btn small", onclick: (e) => { e.preventDefault(); groupDialog(g); } }, t("Edit")) : null),
    list.length ? body(list) : h("div", { class: "card empty" }, t("No projects in this group yet.")));
}
// One of the hub's projects on the Projects page (a computer that sends to a hub): the projects here that go to it,
// totals over them, the team's sessions there as of the last push, and a way to open it on the hub, signed in
function hubProjectSection(hp, list, body) {
  const key = `hub:${hp.path}`, hubUrl = safeUrl(groupsHub?.url), hub = groupsHub?.name || t("Hub");
  const sum = (k) => list.reduce((a, p) => a + (p[k] || 0), 0);
  return h("details", { class: "pgroup", open: !groupFolded(key), ontoggle: (e) => foldGroup(key, !e.target.open) },
    h("summary", null, icon("right", "pg-caret"), icon("hubproject", "pg-hub-ic"), h("h2", null, hp.name),
      h("span", { class: "badge hub", title: t("The projects on {hub} that this computer's folders go to", { hub }) }, icon("cloud"), hub),
      h("span", { class: "pg-meta" }, tn(list.length, "{n} project", "{n} projects"), " · ", tn(sum("sessions"), "{n} session", "{n} sessions", { n: fmtNum(sum("sessions")) }),
        " · ", t("{dur} active", { dur: fmtDur(sum("active_s")) }),
        hp.sessions != null ? [" · ", tn(hp.sessions, "{n} session from the team", "{n} sessions from the team", { n: fmtNum(hp.sessions) })] : null),
      hubUrl ? h("a", { class: "btn small with-icon", href: `${hubUrl}/#${hubProjectPage(hp)}`, target: "_blank", rel: "noopener",
        onclick: (e) => { e.preventDefault(); openHub(hubUrl, hubProjectPage(hp)); } }, t("Open on the hub"), icon("external")) : null),
    body(list, { inHub: true }));
}
function segControl(options, value, onChange) {
  return h("div", { class: "seg", role: "group" }, options.map(([v, label]) =>
    h("button", { type: "button", class: v === value ? "on" : "", "aria-pressed": String(v === value), onclick: () => onChange(v) }, label)));
}

// Cards/List switch of the list pages; each page remembers its layout in this browser
const viewModes = {};
function viewMode(page, fallback) {
  if (!(page in viewModes)) {
    let saved = null;
    try { saved = localStorage.getItem(`interlatch.view.${page}`); } catch { /* storage blocked: use the default */ }
    viewModes[page] = saved === "cards" || saved === "list" ? saved : fallback;
  }
  return viewModes[page];
}
function viewToggle(page, mode) {
  const icon = (rects) => s("svg", { viewBox: "0 0 16 16", "aria-hidden": "true" },
    rects.map(([x, y, w, ht]) => s("rect", { x, y, width: w, height: ht, rx: 1.2 })));
  const seg = segControl([
    ["cards", [icon([[1.5, 1.5, 5.5, 5.5], [9, 1.5, 5.5, 5.5], [1.5, 9, 5.5, 5.5], [9, 9, 5.5, 5.5]]), t("Cards")]],
    ["list", [icon([[1.5, 2.2, 13, 1.8], [1.5, 7.1, 13, 1.8], [1.5, 12, 13, 1.8]]), t("List")]],
  ], mode, (v) => {
    if (v === mode) return;
    viewModes[page] = v;
    try { localStorage.setItem(`interlatch.view.${page}`, v); } catch { /* remembered for this tab only */ }
    render();
  });
  seg.classList.add("view-toggle");
  seg.setAttribute("aria-label", t("Layout"));
  return seg;
}

// A table sorted in the browser. cols: { key, label, num, desc, value(item) } (no value: not sortable; desc: sort
// descending on first click); row(item) returns the item's <tr> (or [] to skip it); group splits the default order.
function localTable(items, cols, row, { sort = null, order = "asc", group = null, empty = t("Nothing matches"), cls = "" } = {}) {
  const state = { sort, order };
  const thead = h("thead"), tbody = h("tbody");
  function draw() {
    const col = cols.find((c) => c.key === state.sort && c.value);
    const dir = state.order === "asc" ? 1 : -1;
    const list = !col ? items : [...items].sort((a, b) => {
      const va = col.value(a), vb = col.value(b);
      if (va == null || vb == null || va === "" || vb === "") return (va == null || va === "") - (vb == null || vb === ""); // blanks last
      return (typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb))) * dir;
    });
    thead.replaceChildren(h("tr", null, cols.map((c) => {
      const th = h("th", { class: `${c.value ? "sortable" : ""} ${c.num ? "num" : ""}`, "aria-sort": c.key === state.sort ? (state.order === "asc" ? "ascending" : "descending") : null },
        c.label, c.key === state.sort ? h("span", { class: "arrow" }, state.order === "asc" ? " ↑" : " ↓") : null);
      if (c.value) th.addEventListener("click", () => {
        state.order = state.sort === c.key ? (state.order === "asc" ? "desc" : "asc") : (c.desc ? "desc" : "asc");
        state.sort = c.key;
        draw();
      });
      return th;
    })));
    const rows = [];
    let current;
    for (const item of list) {
      const tr = row(item);
      if (Array.isArray(tr) && !tr.length) continue;
      if (group && (!state.sort || state.sort === group.key)) {
        const g = group.of(item);
        if (g !== current) rows.push(h("tr", { class: "group-row", id: group.id ? group.id(g) : null }, h("th", { colspan: cols.length }, (current = g))));
      }
      rows.push(tr);
    }
    tbody.replaceChildren(...(rows.length ? rows : [h("tr", null, h("td", { colspan: cols.length, class: "empty" }, empty))]));
  }
  draw();
  return h("div", { class: "table-wrap" }, h("table", { class: `data list-table ${cls}` }, thead, tbody));
}

// A table row that opens a detail row under it (click, Enter or Space)
function expandable(tr, colspan, detail, { indent = 0 } = {}) {
  let open = null;
  const toggle = () => {
    if (open) { open.remove(); open = null; }
    else {
      open = h("tr", { class: "detail-row" }, indent ? h("td", { colspan: indent }) : null, h("td", { colspan: colspan - indent }, detail()));
      tr.after(open);
      open.querySelectorAll(".gloss").forEach((el) => glossify(el));
    }
    tr.classList.toggle("open", !!open);
    tr.setAttribute("aria-expanded", String(!!open));
  };
  tr.classList.add("row-link");
  tr.tabIndex = 0;
  tr.setAttribute("aria-expanded", "false");
  tr.addEventListener("click", (e) => { if (!e.target.closest("a, button, .gterm")) toggle(); });
  tr.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === tr) { e.preventDefault(); toggle(); } });
  tr.closeDetail = () => { if (open) toggle(); };
  return tr;
}
function plainText(src) { // one-line excerpt of markdown
  return String(src || "").replace(/```[\s\S]*?```/g, " ").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(?:#{1,6}|>|[-*+]|\d+\.)\s+/gm, "").replace(/\*\*|__|`/g, "").replace(/\s+/g, " ").trim();
}
function tile(label, value, { delta, spark, title, iconName, artName } = {}) {
  return h("div", { class: artName ? "tile has-art" : "tile", title }, artName ? art(artName, "tile-art") : null,
    h("div", { class: "label" }, iconName && !artName ? icon(iconName) : null, label),
    h("div", { class: "value" }, value),
    h("div", { class: "foot" }, delta != null ? h("span", { class: "delta" }, delta) : h("span")),
    spark || null);
}
function fact(label, value) { return h("div", { class: "fact" }, h("b", null, value), h("span", null, label)); }
function fmtRate(v) { return v >= 100 ? fmtCompact(v) : v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v ? v.toFixed(2) : "0"; }
function pctText(part, whole) { return whole ? `${((part / whole) * 100).toFixed(part / whole < 0.1 ? 1 : 0)}%` : "–"; }
function cardHead(title, { iconName, hint, tools } = {}) {
  return h("div", { class: "card-head" },
    h("div", { class: "card-title" }, iconName ? icon(iconName) : null, h("h2", null, title)),
    hint || tools ? h("div", { class: "tools" }, hint ? (typeof hint === "string" ? h("span", { class: "hint" }, hint) : hint) : null, tools || null) : null);
}
function legendKey(items) { // [[swatch class, label]]; identity never rides on color alone
  return h("span", { class: "mini-legend" }, items.map(([cls, label]) => h("span", null, h("i", { class: `sw ${cls}` }), label)));
}
function deltaText(cur, prev, days) {
  if (prev == null || !prev) return cur ? t("new vs prior {n}d", { n: days }) : null;
  const pct = ((cur - prev) / prev) * 100;
  if (!isFinite(pct)) return null;
  return `${pct >= 0 ? "▲" : "▼"} ${t("{pct}% vs prior {n}d", { pct: Math.abs(pct).toFixed(0), n: days })}`;
}

// =====================================================================================
// Overview
// =====================================================================================
// Home: the activity charts. A hub with people calls it Activity and has the team's own page beside it (Team overview)
route(/^\/?$/, (params) => (dedicated() ? teamHome(params) : activityView(params, !!ME?.hub?.team)));
route(/^\/activity$/, (params) => activityView(params, true)); // an older link
route(/^\/overview$/, (params) => (ME?.hub?.team ? teamHome(params) : activityView(params)));
async function activityView(params, own = false) {
  const days = params.days || "90", project = params.project || "", agent = params.agent || "";
  const metric = params.metric || "active_s";
  const data = await api("/api/overview", { days, project, agent });
  const tot = data.totals, p = data.previous || {};
  const update = (patch) => { setParams({ days, project, agent, metric, ...patch }); render(); };
  // fill missing days so the time axis is honest
  const daily = fillDays(data.daily, days === "all" ? null : +days);
  // a delta only means something when recording covered the whole prior period; otherwise show a daily rate
  const DAY = 86400000, firstAt = tot.first_at ? new Date(tot.first_at).getTime() : Date.now();
  const spanDays = Math.max(1, Math.min(days === "all" ? Infinity : +days, Math.ceil((Date.now() - firstAt) / DAY)));
  const fullPrior = days !== "all" && firstAt <= Date.now() - 2 * +days * DAY;
  const second = (cur, prev, f) => (fullPrior ? deltaText(cur, prev, days) : t("{n} a day", { n: f(cur / spanDays) }));
  const sparkOf = (key) => sparkEl(bucket(daily.map((x) => x[key] || 0), 12));
  const activeDays = daily.filter((x) => (x.active_s || 0) > 0 || x.sessions).length;
  let longest = 0, run = 0;
  for (const x of daily) { run = (x.active_s || 0) > 0 || x.sessions ? run + 1 : 0; longest = Math.max(longest, run); }
  const periodName = days === "all" ? t("all time") : t("the last {n} days", { n: days });
  const hero = h("div", { class: "tile hero", title: t("Active time: the sum of gaps under 15 minutes between events") },
    art("clock", "tile-art"),
    h("div", { class: "label" }, icon("clock"), t("Active time")),
    h("div", { class: "value" }, fmtHours(tot.active_s)),
    h("div", { class: "hero-sub" }, t("across {sessions} sessions in {projects} projects, {period}", { sessions: fmtNum(tot.sessions), projects: fmtNum(tot.projects), period: periodName })),
    h("div", { class: "hero-facts" },
      fact(t("active days"), t("{a} of {b}", { a: fmtNum(activeDays), b: fmtNum(Math.min(daily.length, spanDays)) })),
      fact(t("per active day"), activeDays ? fmtDur(tot.active_s / activeDays) : "–"),
      fact(t("longest run"), tn(longest, "{n} day", "{n} days"))),
    sparkEl(bucket(daily.map((x) => x.active_s || 0), 24), { height: 46 }));
  const tiles = h("div", { class: "kpis" }, hero,
    tile(t("Sessions"), fmtNum(tot.sessions), { iconName: "sessions", artName: "log", delta: second(tot.sessions, p.sessions, fmtRate), spark: sparkOf("sessions") }),
    tile(t("Prompts"), fmtNum(tot.prompts), { iconName: "prompts", artName: "pip", delta: second(tot.prompts, p.prompts, fmtRate), spark: sparkOf("prompts") }),
    tile(t("Tool calls"), fmtCompact(tot.tool_calls), { iconName: "zap", artName: "gears", delta: t("{n} failed · {pct}", { n: fmtNum(tot.tool_errors), pct: pctText(tot.tool_errors, tot.tool_calls) }), spark: sparkOf("tool_calls") }),
    tile(t("Lines changed"), `+${fmtCompact(tot.lines_added)}`, { iconName: "diff", artName: "document", delta: t("−{n} removed", { n: fmtCompact(tot.lines_removed) }), spark: sparkOf("lines_added") }),
    tile(t("Tokens"), fmtCompact(tot.tokens), { iconName: "tokens", artName: "chip", delta: second(tot.tokens, p.tokens, fmtCompact), spark: sparkOf("tokens"), title: t("Input + output + cache read + cache write") }),
    tile(t("Est. API cost"), fmtCost(tot.cost), { iconName: "cost", artName: "coins", delta: second(tot.cost, p.cost, fmtCost), spark: sparkOf("cost"), title: t("API list-price equivalent; subscriptions are billed differently. Newer GPT models are estimated at GPT-5 rates.") }),
    tile(t("Knowledge"), fmtNum(tot.knowledge), { iconName: "sparkles", artName: "book", delta: t("{n} sessions analyzed", { n: fmtNum(tot.analyzed) }) }),
    tile(t("Projects"), fmtNum(tot.projects), { iconName: "projects", artName: "folder", delta: data.projects[0] ? t("most time: {name}", { name: data.projects[0].label }) : null }));
  const metrics = {
    sessions: [t("Sessions"), (x) => x.sessions, fmtNum],
    active_s: [t("Active time"), (x) => x.active_s, fmtHours, 3600],
    tokens: [t("Tokens"), (x) => x.tokens, fmtCompact],
    cost: [t("Est. cost"), (x) => x.cost, fmtCost],
  };
  const [mLabel, mValue, mFmt, mUnit] = metrics[metric] || metrics.active_s;
  const dailyName = t("Daily {metric}", { metric: mLabel.toLowerCase() });
  const dailyCard = chartCard(dailyName, days === "all" ? t("All time") : t("Last {n} days", { n: days }),
    (el, w) => columnChart(el, w, daily, { value: (x) => mValue(x) || 0, fmt: mFmt, label: (x) => wdFmt.format(new Date(x.date + "T12:00:00")), avg: daily.length >= 21 ? 7 : 0, name: dailyName, height: 320, unit: mUnit || 1 }),
    () => ({ columns: [t("Date"), mLabel], num: [false, true], rows: daily.filter((x) => mValue(x)).map((x) => [x.date, mFmt(mValue(x))]) }),
    [segControl(Object.entries(metrics).map(([k, v]) => [k, v[0]]), metric, (v) => update({ metric: v }))], "status");
  const outcomesCard = h("section", { class: "card" }, cardHead(t("Outcomes"), { iconName: "completed", hint: t("from session analysis") }),
    outcomeBreakdown(data.outcomes),
    data.work_types.length ? [h("div", { class: "subhead" }, t("Work types")),
      hbars(data.work_types.slice(0, 6).map(([w, n]) => ({ w, n })), { label: (x) => workTypeLabel(x.w), value: (x) => x.n, fmt: fmtNum })] : null);
  const cal = chartCard(t("Activity calendar"), t("Active time per day · click a day to see its sessions"),
    (el, w) => calendarHeatmap(el, w, data.calendar),
    () => ({ columns: [t("Date"), t("Sessions"), t("Prompts"), t("Active")], num: [false, true, true, true], rows: data.calendar.map((r) => [r.date, r.sessions, r.prompts || 0, fmtDur(r.active_s)]) }),
    [], "calendar");
  const hours = chartCard(t("When you work"), t("Prompts by weekday and hour, local time"),
    (el, w) => hourGrid(el, w, data.hours),
    () => ({ columns: [t("Weekday"), t("Hour"), t("Prompts")], num: [false, true, true], rows: data.hours.map((r) => [[t("Sun"), t("Mon"), t("Tue"), t("Wed"), t("Thu"), t("Fri"), t("Sat")][r.dow], r.hour, r.n]) }),
    [], "clock");
  const projCard = h("section", { class: "card" }, cardHead(t("Projects"), { iconName: "projects", hint: t("by active time · sessions") }),
    hbars(data.projects, { label: (x) => x.label, value: (x) => x.active_s || 0, fmt: fmtDur, href: (x) => `#/project?path=${encodeURIComponent(x.project_path)}`, sub: (x) => `· ${x.sessions}` }));
  const toolCard = h("section", { class: "card" }, cardHead(t("Tools"), { iconName: "zap", hint: legendKey([["accent", t("calls")], ["critical", t("failed")]]) }),
    hbars(groupMcp(data.tools).slice(0, 12), { label: (x) => x.name, value: (x) => x.n, fmt: fmtCompact, part: (x) => x.errors || 0, sub: (x) => (x.errors ? `(${fmtCompact(x.errors)})` : "") }));
  const modelCard = h("section", { class: "card" }, cardHead(t("Models"), { iconName: "system", hint: t("est. API cost · tokens") }),
    hbars(data.models, { label: (x) => x.model, value: (x) => x.cost || 0, fmt: fmtCost, sub: (x) => `· ${fmtCompact(x.tokens)}` }),
    data.agents && data.agents.length > 1 && !agent ? [h("div", { class: "subhead" }, t("Agents")),
      hbars(data.agents, { label: (x) => agentName(x.agent), value: (x) => x.active_s || 0, fmt: fmtDur, href: (x) => `#/?agent=${x.agent}`, sub: (x) => `· ${tn(x.sessions, "{n} session", "{n} sessions")}` })] : null);
  const recent = h("section", { class: "card" }, cardHead(t("Recent sessions"), { iconName: "sessions", tools: h("a", { href: "#/sessions", class: "hint link-arrow" }, t("All sessions"), icon("arrow")) }),
    sessionList(data.recent));
  const know = h("section", { class: "card" }, cardHead(t("Latest knowledge"), { iconName: "knowledge", tools: h("a", { href: "#/knowledge/all", class: "hint link-arrow" }, t("Browse"), icon("arrow")) }),
    knowledgeList(data.knowledge.slice(0, 8)));
  const counts = { sessions: fmtNum(tot.sessions), projects: fmtNum(tot.projects), date: tot.first_at ? fmtDateY(tot.first_at) : "" };
  const sgBox = h("div"); // filled in when the queue has something to review; Home does not wait for it
  suggestionsHomeCard(sgBox, project);
  return h("div", null,
    h("div", { class: "page-head" }, titled("dashboard", h("h1", null, own ? t("Activity") : t("Home")),
      h("div", { class: "sub" }, tot.first_at ? t("{sessions} sessions across {projects} projects since {date}", counts) : t("{sessions} sessions across {projects} projects", counts))),
      h("div", { class: "head-actions" },
        segControl([["7", t("7d")], ["30", t("30d")], ["90", t("90d")], ["180", t("180d")], ["all", t("All")]], days, (v) => update({ days: v })),
        segControl([["", t("All agents")], ...Object.keys(AGENTS).filter((a) => a === agent || (data.agents || []).some((x) => x.agent === a))
          .map((a) => [a, agentName(a)])], agent, (v) => update({ agent: v })),
        h("select", { "aria-label": t("Project"), onchange: (e) => update({ project: e.target.value }) }, await projectOptions(project)))),
    tiles,
    sgBox,
    h("div", { class: "grid cols-main section-gap" }, dailyCard, outcomesCard),
    h("div", { class: "grid cols-2 section-gap" }, cal, hours),
    h("div", { class: "grid cols-3 section-gap" }, projCard, toolCard, modelCard),
    h("div", { class: "grid cols-2 section-gap" }, recent, know));
}

function groupMcp(tools) {
  const out = {};
  for (const t of tools) {
    const m = t.name.match(/^mcp__(.+?)__/);
    const name = m ? `MCP: ${m[1].replace(/^claude_ai_/, "")}` : t.name;
    const g = (out[name] ||= { name, n: 0, errors: 0 });
    g.n += t.n;
    g.errors += t.errors || 0;
  }
  return Object.values(out).sort((a, b) => b.n - a.n);
}

function fillDays(daily, days) {
  if (!daily.length) return [];
  const map = Object.fromEntries(daily.map((x) => [x.date, x]));
  const end = new Date(); end.setHours(12, 0, 0, 0);
  const start = days ? new Date(end.getTime() - (days - 1) * 86400000) : new Date(daily[0].date + "T12:00:00");
  const out = [];
  for (let t = new Date(start); t <= end; t.setDate(t.getDate() + 1)) {
    const key = localDate(t);
    out.push(map[key] || { date: key, sessions: 0, prompts: 0, active_s: 0, tokens: 0, cost: 0, tool_calls: 0, lines_added: 0 });
  }
  return out;
}
function bucket(values, n) {
  if (values.length <= n) return values;
  const size = values.length / n, out = [];
  for (let i = 0; i < n; i++) out.push(values.slice(Math.floor(i * size), Math.floor((i + 1) * size)).reduce((a, b) => a + b, 0));
  return out;
}

function outcomeOf(x) { // -> [status class, icon, label] for a session row
  if (x.source === "history") return ["", "history", t("history")];
  if (x.outcome && OUTCOME[x.outcome]) return [OUTCOME[x.outcome][0], x.outcome, OUTCOME[x.outcome][1]];
  return [x.analysis_status === "error" ? "critical" : "", STATUS_ICON[x.analysis_status] || "dot", STATUS_LABEL[x.analysis_status] || "–"];
}
function sessionList(items) {
  if (!items.length) return emptyArt("pip", t("No sessions yet"));
  return h("div", { class: "session-list" }, items.map((x) => {
    const [cls, ic, label] = outcomeOf(x);
    return h("a", { class: "session-item", href: `#/session/${x.id}` },
      h("span", { class: `status-icon ${cls}`, title: label }, icon(ic), h("span", { class: "sr-only" }, label)),
      h("div", { style: { minWidth: 0 } }, h("div", { class: "t" }, x.title || t("(untitled)"), agentTag(x.agent)),
        h("div", { class: "m" }, `${x.project_name || "–"} · ${ago(x.started_at)} · ${t("{dur} active", { dur: fmtDur(x.active_s) })} · ${tn(x.n_prompts, "{n} prompt", "{n} prompts")}`)),
      h("div", { class: "r" }, fmtCost(x.est_cost_usd)));
  }));
}
function knowledgeList(items) {
  if (!items.length) return emptyArt("lumi", t("No analyzed knowledge yet. Sessions are analyzed automatically once idle."));
  return h("div", { class: "session-list" }, items.map((k) => h("a", { class: "session-item", href: `#/knowledge?q=${encodeURIComponent(k.title.slice(0, 60))}` },
    h("span", { class: "kind-icon", title: kindLabel(k.kind) }, icon(KIND[k.kind] ? k.kind : "dot")),
    h("div", { style: { minWidth: 0 } }, h("div", { class: "t" }, k.title),
      h("div", { class: "m" }, [kindLabel(k.kind), k.scope === "global" ? t("global") : k.project_name, ago(k.created_at)].filter(Boolean).join(" · "))),
    h("div", { class: "r" }, confidenceMeter(k.confidence)))));
}
const OUTCOME_ORDER = ["completed", "partial", "exploratory", "blocked", "abandoned", "unclear", "not analyzed"];
function outcomeBreakdown(outcomes) {
  const total = outcomes.reduce((a, o) => a + o.n, 0);
  if (!total) return h("div", { class: "empty" }, t("No sessions yet"));
  const rank = (o) => { const i = OUTCOME_ORDER.indexOf(o.outcome); return i < 0 ? 99 : i; };
  const items = [...outcomes].sort((a, b) => rank(a) - rank(b));
  const cls = (o) => (OUTCOME[o] ? OUTCOME[o][0] || "neutral" : "none");
  const label = (o) => (OUTCOME[o] ? OUTCOME[o][1] : o === "not analyzed" ? t("Not analyzed") : o);
  const href = (o) => `#/sessions?outcome=${o === "not analyzed" ? "none" : o}`;
  const pct = (n) => `${Math.round((n / total) * 100)}%`;
  return h("div", { class: "outcomes" },
    h("div", { class: "stackbar", role: "img", "aria-label": items.map((o) => `${label(o.outcome)}: ${o.n}`).join(", ") },
      items.map((o) => {
        const seg = h("a", { class: `seg s-${cls(o.outcome)}`, href: href(o.outcome), style: { flexGrow: String(o.n) }, "aria-label": `${label(o.outcome)}: ${o.n}` });
        hoverable(seg, () => [`${tn(o.n, "{n} session", "{n} sessions", { n: fmtNum(o.n) })} · ${pct(o.n)}`, label(o.outcome)]);
        return seg;
      })),
    h("div", { class: "legend-list" }, items.map((o) => h("a", { class: `legend-row s-${cls(o.outcome)}`, href: href(o.outcome) },
      icon(OUTCOME[o.outcome] ? o.outcome : o.outcome === "not analyzed" ? "queued" : "dot"),
      h("span", { class: "lname" }, label(o.outcome)), h("span", { class: "lval" }, fmtNum(o.n)), h("span", { class: "lpct" }, pct(o.n))))));
}

// =====================================================================================
// Team Home: a hub with people starts here (/api/team). Its team projects are the ones set up on the hub and the
// ones other computers send to; a project only the hub computer works on stays under Home › Activity.
// =====================================================================================
const QUIET_DAYS = 7; // a computer not heard from for longer is named under Needs attention
const TEAM_PERIODS = ["7", "30", "90"];
function olderThan(ts, days) { return !ts || Date.now() - new Date(ts).getTime() > days * 86400000; }
function initials(name) { return (name || "?").trim().split(/\s+/).slice(0, 2).map((w) => [...w][0] || "").join("").toUpperCase(); }
function avatar(name) { // a person's initials on a wash of one categorical colour, the same for the same name
  let n = 0;
  for (const ch of name || "") n = (n * 31 + ch.codePointAt(0)) >>> 0;
  return h("span", { class: "avatar", "aria-hidden": "true", style: { "--av": `var(--series-${(n % 8) + 1})` } }, initials(name));
}
// a person (or a computer no one joined) by name; with a key, a link to their sessions, or to `base` narrowed to them
function whoTag(name, key, base = "#/sessions") {
  if (!key) return h("span", { class: "who-tag", title: name }, avatar(name), h("span", null, name));
  return h("a", { class: "who-tag", href: `${base}?who=${encodeURIComponent(key)}`, title: t("Only {name}'s", { name }) }, avatar(name), h("span", null, name));
}
// On a hub with people: a picker that narrows a list to one person's sessions, or one computer's no one joined
async function whoFilter(value, onChange) {
  if (!ME?.hub?.team) return null;
  let items = [];
  try { items = (await api("/api/team/who")).items; } catch (e) { return null; }
  if (!items.length && !value) return null;
  return h("select", { "aria-label": t("Person"), onchange: (e) => onChange(e.target.value) },
    h("option", { value: "" }, t("Everyone")),
    items.map((x) => h("option", { value: x.key, selected: x.key === value }, `${x.name} · ${fmtNum(x.sessions)}`)),
    value && !items.some((x) => x.key === value) ? h("option", { value, selected: true }, t("Someone not listed")) : null);
}

// for admins: people who can't get in yet, and computers the hub hasn't heard from lately
function teamAttention(data) {
  if (!data.people) return null;
  const items = [];
  for (const p of data.people) {
    if (p.computers || p.browsers) continue;
    items.push(p.invites.length
      ? [avatar(p.name), t("{name} hasn't joined yet. The invite is open until {date}.", { name: p.name, date: fmtDate(p.invites[0]) }), "#/team"]
      : [avatar(p.name), t("{name} has no computer or browser signed in, and no open invite. Make them a new one.", { name: p.name }), "#/team"]);
  }
  for (const c of data.computers || []) {
    if (c.this || !olderThan(c.last_seen, QUIET_DAYS)) continue;
    const name = c.person ? `${c.name} (${c.person})` : c.name;
    items.push([icon("devices"), c.last_seen ? t("{computer} was last heard from {ago}.", { computer: name, ago: ago(c.last_seen) })
      : t("{computer} hasn't been heard from yet.", { computer: name }), "#/team/computers"]);
  }
  if (!items.length) return null;
  return h("section", { class: "card attention-card section-gap" }, cardHead(t("Needs attention"), { iconName: "gotcha", hint: tn(items.length, "{n} item", "{n} items") }),
    h("ul", { class: "attention" }, items.map(([lead, text, href]) => h("li", null, lead, h("span", null, text), h("a", { class: "hint link-arrow", href }, t("Open"), icon("arrow"))))));
}

// what other computers sent since this viewer last opened Team overview (news.py)
function newsCard(items) {
  if (!items.length) return null;
  return h("section", { class: "card news-card section-gap" }, cardHead(t("New since your last visit"), { iconName: "sparkles", hint: tn(items.length, "{n} update", "{n} updates") }),
    h("ul", { class: "attention" }, items.map((x) => h("li", null,
      x.kind === "joined" ? icon("devices") : avatar(x.person || x.computer || "?"),
      h("span", null, newsText(x)),
      h("span", { class: "hint nowrap", title: fmtDT(x.at) }, ago(x.at))))));
}

async function teamHome(params) {
  const days = TEAM_PERIODS.includes(params.days) ? params.days : TEAM_PERIODS[0];
  const data = await api("/api/team", { days });
  const tot = data.totals, period = t("the last {n} days", { n: days });
  newsVisit ||= { seen: data.news?.seen || "" }; // a period picked meanwhile still shows what was new on opening
  const fresh = (data.news?.items || []).filter((x) => x.at > newsVisit.seen);
  const gained = {}; // project -> what it gained since
  for (const x of fresh.filter((x) => x.kind === "shared")) {
    const g = (gained[x.project_path] ||= { sessions: 0, lessons: 0 });
    g.sessions += x.sessions;
    g.lessons += x.lessons;
  }
  const plus = (n) => n ? h("span", { class: "new-chip", title: t("New since your last visit") }, `+${fmtNum(n)}`) : null;
  if (data.news) post("/api/team/news/seen", { at: data.news.now }).then(() => drawNews(0)).catch(() => {});
  const news = newsCard(fresh);
  const head = h("div", { class: "page-head" },
    titled("cloud", h("h1", null, data.hub?.name || t("Team")),
      h("div", { class: "sub" }, limited() ? t("Your projects on this hub, {period}", { period }) : t("The team's projects on this hub, {period}", { period }))),
    h("div", { class: "head-actions" },
      segControl(TEAM_PERIODS.map((d) => [d, t("{n}d", { n: d })]), days, (v) => { setParams({ days: v }); render(); })));
  const attention = teamAttention(data);
  if (!data.projects.length) {
    return h("div", null, head, news, attention, h("section", { class: "card section-gap" }, cardHead(t("No team projects yet"), { iconName: "projects" }),
      limited() ? h("p", null, t("No project on this hub is shared with you yet. Ask an admin of this hub."))
        : dedicated() ? h("p", null, tx("A project shows up here once an admin makes it in {projects}, or a computer sends sessions to it.",
          { projects: h("a", { href: "#/team/projects" }, t("Team › Projects")) }))
        : h("p", null, tx("A project shows up here once it is set up on the hub with {command}, or another computer sends sessions to it. Projects only this computer works on stay under {activity}.",
          { command: h("span", { class: "codeline" }, "interlatch hub project add <folder>"), activity: h("a", { href: "#/" }, t("Activity")) }))));
  }
  const tiles = h("div", { class: "kpis team-kpis" },
    tile(t("Sessions"), fmtNum(tot.sessions), { iconName: "sessions", delta: period }),
    tile(t("New lessons"), fmtNum(tot.lessons), { iconName: "sparkles", delta: period }),
    tile(t("People active"), fmtNum(tot.people), { iconName: "team", delta: data.people ? t("of {n} on this hub", { n: fmtNum(data.people.length) }) : period }),
    tile(t("Projects active"), fmtNum(tot.projects), { iconName: "projects", delta: tn(tot.team_projects, "of {n} team project", "of {n} team projects") }));
  const projCard = h("section", { class: "card" }, cardHead(t("Team projects"), { iconName: "projects", hint: t("sessions and new lessons, {period}", { period }) }),
    h("div", { class: "table-wrap" }, h("table", { class: "data team-projects" },
      h("thead", null, h("tr", null, h("th", null, t("Project")), h("th", { class: "num" }, t("Sessions")), h("th", { class: "num" }, t("Lessons")),
        h("th", null, t("Who")), h("th", null, t("Latest")))),
      h("tbody", null, data.projects.map((p) => h("tr", null,
        h("td", null, h("a", { class: "proj", href: `#/project?path=${encodeURIComponent(p.path)}`, title: p.path }, p.label)),
        h("td", { class: "num" }, p.recent ? fmtNum(p.recent) : h("span", { class: "muted" }, "0"), plus(gained[p.path]?.sessions)),
        h("td", { class: "num" }, p.lessons ? fmtNum(p.lessons) : h("span", { class: "muted" }, "0"), plus(gained[p.path]?.lessons)),
        h("td", null, p.people.length ? h("div", { class: "who-list" }, p.people.slice(0, 3).map((x) => whoTag(x.who, x.key)),
          p.people.length > 3 ? h("span", { class: "muted" }, `+${p.people.length - 3}`) : null) : h("span", { class: "muted" }, "–")),
        h("td", { class: "nowrap", title: p.last ? fmtDT(p.last) : "" }, p.last ? ago(p.last) : h("span", { class: "muted" }, t("no sessions yet")))))))));
  // who worked on what, without counts per person (a tally reads as a score; see docs/design/learning.md)
  const whoCard = h("section", { class: "card" }, cardHead(t("Who worked on what"), { iconName: "team", hint: period }),
    data.who.length ? h("div", { class: "session-list" }, data.who.map((x) => h("a", { class: "session-item", href: `#/sessions?who=${encodeURIComponent(x.who_key)}`, title: t("Only {name}'s", { name: x.who }) },
      avatar(x.who),
      h("div", { style: { minWidth: 0 } }, h("div", { class: "t" }, x.who),
        h("div", { class: "m" }, x.projects.slice(0, 4).map((p) => p.label).join(" · ") + (x.projects.length > 4 ? ` +${x.projects.length - 4}` : ""))),
      h("div", { class: "r", title: fmtDT(x.last) }, ago(x.last)))))
    : h("div", { class: "empty" }, t("Nobody worked on a team project in {period}.", { period })));
  const lessonsCard = h("section", { class: "card" }, cardHead(t("New lessons"), { iconName: "knowledge", tools: h("a", { href: "#/knowledge/all", class: "hint link-arrow" }, t("Browse"), icon("arrow")) }),
    data.lessons.length ? h("div", { class: "session-list" }, data.lessons.map((k) => h("a", { class: "session-item", href: `#/session/${k.session_id}`, title: t("Open the session it came from") },
      h("span", { class: "kind-icon", title: kindLabel(k.kind) }, icon(KIND[k.kind] ? k.kind : "dot")),
      h("div", { style: { minWidth: 0 } }, h("div", { class: "t" }, k.title),
        h("div", { class: "m" }, [kindLabel(k.kind), k.project_name, k.who, ago(k.created_at)].filter(Boolean).join(" · "))),
      h("div", { class: "r" }, confidenceMeter(k.confidence)))))
    : h("div", { class: "empty" }, t("No lessons from team projects yet. Sessions are analyzed automatically once idle.")));
  const recentCard = h("section", { class: "card" }, cardHead(t("Recent sessions"), { iconName: "sessions", tools: h("a", { href: "#/sessions", class: "hint link-arrow" }, t("All sessions"), icon("arrow")) }),
    data.recent.length ? h("div", { class: "session-list" }, data.recent.map((x) => {
      const [cls, ic, label] = outcomeOf(x);
      return h("a", { class: "session-item", href: `#/session/${x.id}` },
        h("span", { class: `status-icon ${cls}`, title: label }, icon(ic), h("span", { class: "sr-only" }, label)),
        h("div", { style: { minWidth: 0 } }, h("div", { class: "t" }, x.title || t("(untitled)"), agentTag(x.agent)),
          h("div", { class: "m" }, [x.project_name, x.who, ago(x.started_at), x.active_s ? t("{dur} active", { dur: fmtDur(x.active_s) }) : null].filter(Boolean).join(" · "))));
    })) : h("div", { class: "empty" }, t("No sessions yet")));
  return h("div", null, head, tiles, news, attention,
    h("div", { class: "grid cols-main section-gap" }, projCard, whoCard),
    h("div", { class: "grid cols-2 section-gap" }, lessonsCard, recentCard));
}

// =====================================================================================
// Sessions list
// =====================================================================================
route(/^\/sessions$/, async (params) => {
  const state = { q: params.q || "", project: params.project || "", outcome: params.outcome || "", status: params.status || "",
    days: params.days || "", sort: params.sort || "started_at", order: params.order || "desc", day: params.day || "",
    agent: params.agent || "", screen: params.screen || "", who: params.who || "", group: params.group || "" };
  const mode = viewMode("sessions", matchMedia("(max-width: 600px)").matches ? "cards" : "list"); // a phone has no room for the table
  let offset = 0, scale = null, total = 0;
  // ---- selection (list view): pick sessions, then analyze them in one go
  const picked = new Set(), boxes = new Map(); // id -> checkbox of a loaded row
  let lastPick = null;
  const selBar = h("div", { class: "sel-bar", hidden: true });
  const headBox = h("input", { type: "checkbox", "aria-label": t("Select all loaded sessions"), onclick: (e) => {
    for (const [id, b] of boxes) if (!b.disabled) { b.checked = e.target.checked; b.closest("tr").classList.toggle("picked", b.checked); e.target.checked ? picked.add(id) : picked.delete(id); }
    drawSel();
  } });
  const setPick = (id, on) => {
    on ? picked.add(id) : picked.delete(id);
    const b = boxes.get(id);
    if (b) { b.checked = on; b.closest("tr")?.classList.toggle("picked", on); }
  };
  const pick = (x, tr) => {
    const b = h("input", { type: "checkbox", "aria-label": t("Select {name}", { name: x.title || t("session") }), checked: picked.has(x.id),
      onclick: (e) => {
        e.stopPropagation();
        if (e.shiftKey && lastPick) { // shift-click: the range from the last box clicked
          const ids = [...boxes.keys()], a = ids.indexOf(lastPick), z = ids.indexOf(x.id);
          if (a >= 0 && z >= 0) ids.slice(Math.min(a, z), Math.max(a, z) + 1).forEach((id) => { if (!boxes.get(id).disabled) setPick(id, e.target.checked); });
        }
        setPick(x.id, e.target.checked);
        lastPick = x.id;
        drawSel();
      } });
    tr.classList.toggle("picked", picked.has(x.id));
    boxes.set(x.id, b);
    return b;
  };
  async function pickAllMatching() {
    const r = await api("/api/sessions", { ...state, ids_only: 1 });
    r.ids.forEach((id) => setPick(id, true));
    drawSel();
  }
  function drawSel() {
    const n = picked.size;
    const loaded = [...boxes.values()].filter((b) => !b.disabled);
    headBox.checked = n > 0 && loaded.every((b) => b.checked);
    headBox.indeterminate = n > 0 && !headBox.checked;
    selBar.hidden = !n;
    if (!n) return;
    const analyze = h("button", { class: "btn primary small admin-only", type: "button", onclick: async () => {
      if (n > 25 && !confirm(lastStatus?.analysis?.billed
        ? t("Analyze {n} sessions now? Each one is a call to {agent}, billed to your API key.", { n, agent: analyzer() })
        : t("Analyze {n} sessions now? Each one is a call on your {agent} login.", { n, agent: analyzer() }))) return;
      analyze.disabled = true; analyze.textContent = t("Starting…");
      const r = await post("/api/sessions/analyze", { ids: [...picked] });
      if (!r.started) { toast(r.error || t("Could not start")); analyze.disabled = false; drawSel(); return; }
      toast(tn(r.count, "Analyzing {n} session; the status bar shows progress", "Analyzing {n} sessions; the status bar shows progress", { n: fmtNum(r.count) }) +
        (r.dropped ? t(" ({n} history-only left out: nothing to analyze)", { n: fmtNum(r.dropped) }) : ""), 7000);
      [...picked].forEach((id) => setPick(id, false));
      drawSel();
      watchJob("analyze:selection");
    } }, t("Analyze {n}", { n: fmtNum(n) }));
    selBar.replaceChildren(...[ // replaceChildren would print a null as the text "null"
      h("span", { class: "sel-count" }, t("{n} selected", { n: fmtNum(n) })),
      total > n ? h("button", { class: "text-link", type: "button", onclick: pickAllMatching }, t("Select all {n} matching", { n: fmtNum(total) })) : null,
      h("span", { class: "sel-spacer" }),
      h("button", { class: "btn small", type: "button", onclick: () => { [...picked].forEach((id) => setPick(id, false)); drawSel(); } }, t("Clear")),
      limited() ? null : exportMenu(() => [...picked], { small: true, up: true }),
      analyze].filter(Boolean));
  }
  const tbody = h("tbody");
  const cards = h("div", { class: "scard-grid" });
  const countEl = h("span", { class: "sub" });
  const moreBtn = h("button", { class: "btn", type: "button", onclick: () => load(true) }, t("Load more"));
  async function load(more) {
    if (!more) { offset = 0; tbody.replaceChildren(); cards.replaceChildren(); boxes.clear(); picked.clear(); lastPick = null; drawSel(); }
    const q = { ...state, limit: 60, offset };
    if (state.day) {
      const start = new Date(state.day + "T00:00:00");
      q.days = "";
      q.since = start.toISOString();
      q.until = new Date(start.getTime() + 86400000).toISOString();
    }
    const data = await api("/api/sessions", q);
    if (!more) scale = { active: Math.max(1, ...data.items.map((x) => x.active_s || 0)), tokens: Math.max(1, ...data.items.map((x) => x.tokens || 0)),
      cost: Math.max(0.01, ...data.items.map((x) => x.est_cost_usd || 0)) }; // bars compare rows within the first page
    data.items.forEach((x) => (mode === "list" ? tbody.append(sessionRow(x, scale, pick)) : cards.append(sessionCard(x))));
    offset += data.items.length;
    total = data.total;
    drawSel();
    const n = fmtNum(data.total);
    countEl.textContent = state.day ? tn(data.total, "{n} session on {date}", "{n} sessions on {date}", { n, date: fmtDateY(state.day + "T12:00:00") }) : tn(data.total, "{n} session", "{n} sessions", { n });
    moreBtn.hidden = offset >= data.total;
    if (!data.total) {
      if (mode === "list") tbody.append(h("tr", null, h("td", { colspan: 10, class: "empty" }, t("No sessions match"))));
      else cards.append(h("div", { class: "card empty" }, t("No sessions match")));
    }
  }
  const sorts = [["started_at:desc", t("Newest first")], ["started_at:asc", t("Oldest first")], ["active_s:desc", t("Most active time")],
    ["tokens:desc", t("Most tokens")], ["est_cost_usd:desc", t("Highest est. cost")], ["n_tool_calls:desc", t("Most tool calls")],
    ["n_prompts:desc", t("Most prompts")], ["title:asc", t("Title A–Z")]];
  const sortNow = `${state.sort}:${state.order}`;
  const sortSel = mode === "cards" ? h("select", { "aria-label": t("Sort sessions"), onchange: (e) => { [state.sort, state.order] = e.target.value.split(":"); refresh(); } },
    (sorts.some(([v]) => v === sortNow) ? sorts : [[sortNow, t("Custom order")], ...sorts]).map(([v, l]) => h("option", { value: v, selected: v === sortNow }, l))) : null;
  const refresh = () => { setParams(state); load(false); };
  let debounce;
  const search = h("input", { class: "input", type: "search", placeholder: t("Search titles, summaries and transcripts…"), value: state.q, style: { minWidth: "300px" },
    oninput: (e) => { clearTimeout(debounce); debounce = setTimeout(() => { state.q = e.target.value; refresh(); }, 300); } });
  const sel = (key, opts) => h("select", { onchange: (e) => { state[key] = e.target.value; refresh(); } },
    opts.map(([v, l]) => h("option", { value: v, selected: state[key] === v }, l)));
  const cols = [["started_at", t("Started")], ["title", t("Session")], ["project_name", t("Project")], ["agent", t("Agent")], ["n_prompts", t("Prompts"), 1], ["n_tool_calls", t("Tools"), 1],
    ["active_s", t("Active"), 1], ["tokens", t("Tokens"), 1], ["est_cost_usd", t("Est. cost"), 1], [null, t("Outcome")]];
  const thead = h("thead", null, h("tr", null, h("th", { class: "pick" }, headBox), cols.map(([key, label, num]) => {
    const th = h("th", { class: `${key ? "sortable" : ""} ${num ? "num" : ""}` }, label, key === state.sort ? h("span", { class: "arrow" }, state.order === "asc" ? " ↑" : " ↓") : null);
    if (key) th.addEventListener("click", () => { state.order = state.sort === key && state.order === "desc" ? "asc" : "desc"; state.sort = key; setParams(state); render(); });
    return th;
  })));
  await load(false);
  return h("div", null,
    h("div", { class: "page-head" }, titled("log", h("h1", null, t("Sessions")), countEl), viewToggle("sessions", mode)),
    h("div", { class: "filters" }, search,
      h("select", { "aria-label": t("Project"), onchange: (e) => { // a project, or every project in a group
        const v = e.target.value;
        [state.project, state.group] = v.startsWith("group:") ? ["", v.slice(6)] : [v, ""];
        refresh();
      } }, await projectOptions(state.group ? `group:${state.group}` : state.project, { groups: true })),
      sel("outcome", [["", t("Any outcome")], ...Object.entries(OUTCOME).map(([k, [, l]]) => [k, l]), ["none", t("Not analyzed")]]),
      sel("status", [["", t("Any status")], ["done", t("Analyzed")], ["pending", t("Queued")], ["stale", t("Needs re-analysis")], ["error", t("Failed")], ["skipped", t("Skipped")]]),
      sel("agent", [["", t("All agents")], ...Object.entries(AGENTS)]),
      await whoFilter(state.who, (v) => { state.who = v; refresh(); }),
      state.screen || ["chatgpt", "claude-ai"].includes(state.agent) // imported chats: as screening sorted them
        ? sel("screen", [["", t("Any screening")], ...Object.entries(SCREEN).map(([k, [, l]]) => [k, l]), ["none", t("Not screened")]]) : null,
      sel("days", [["", t("All time")], ["7", t("Last {n} days", { n: 7 })], ["30", t("Last {n} days", { n: 30 })], ["90", t("Last {n} days", { n: 90 })]]),
      sortSel,
      state.day ? h("button", { class: "chip on", type: "button", title: t("Clear the day filter"), onclick: () => { state.day = ""; refresh(); } }, icon("calendar"), state.day, icon("x")) : null),
    mode === "list" ? h("section", { class: "card flush" }, h("div", { class: "table-wrap" }, h("table", { class: "data sessions" }, thead, tbody))) : cards,
    h("div", { class: "load-more" }, moreBtn),
    mode === "list" ? selBar : null);
});

function sessionCard(x) {
  const [cls] = outcomeOf(x);
  const stat = (ic, value, label, title) => h("span", { title }, icon(ic), h("b", null, value), label ? ` ${label}` : null);
  return h("a", { class: `card scard o-${cls || "neutral"}`, href: `#/session/${x.id}` },
    h("div", { class: "scard-head" },
      h("div", { class: "t" }, x.title || t("(untitled)"), agentTag(x.agent),
        x.source === "codex-import" ? h("span", { class: "agent-tag", title: t("Claude Code deleted this transcript; recovered from Codex's copy") }, t("recovered")) : null),
      outcomeBadge(x.outcome, x.analysis_status, x.source)),
    h("div", { class: "m" }, [x.project_name || "–", x.who, fmtDT(x.started_at), t("{dur} active", { dur: fmtDur(x.active_s) })].filter(Boolean).join(" · ")),
    x.summary ? h("div", { class: "s" }, x.summary) : screenNote(x),
    x.tags?.length ? h("div", { class: "ktags" }, x.tags.slice(0, 5).map((t) => h("span", { class: "tag" }, t))) : null,
    h("div", { class: "scard-stats" },
      stat("prompts", fmtNum(x.n_prompts), tn(x.n_prompts, "prompt", "prompts")),
      stat("zap", fmtNum(x.n_tool_calls), x.n_tool_errors ? t("({n} failed)", { n: fmtNum(x.n_tool_errors) }) : t("calls"), t("Tool calls")),
      stat("tokens", fmtCompact(x.tokens), null, t("Tokens")),
      stat("cost", fmtCost(x.est_cost_usd).replace(/^\$/, ""), null, t("Estimated API cost"))));
}

function barCell(text, v, max) { // a number with a small magnitude bar under it
  return h("td", { class: "num" }, text, max ? h("div", { class: "cellbar", "aria-hidden": "true" }, h("i", { style: { width: `${Math.min(100, ((v || 0) / max) * 100)}%` } })) : null);
}
// ------------------------------------------------------------------ export: one session or a selection, as files
const RAW_SOURCES = ["transcript", "codex-import", "codex-cloud"]; // sessions with an original transcript of their own
const EXPORT_FORMATS = [["md", "Markdown", t("Summary, knowledge and the conversation")], ["json", "JSON", t("The full record, every event included")],
  ["raw", t("Original transcript"), t("The agent's own file, as archived (not redacted)")]];
async function downloadExport(ids, fmt) {
  const qs = new URLSearchParams({ ids: ids.join(","), format: fmt }).toString();
  let r;
  try { r = await api(`/api/export?${qs}&check=1`); } catch (e) { toast(e.message, 7000); return; }
  const a = h("a", { href: `/api/export?${qs}`, download: "" });
  document.body.append(a); a.click(); a.remove();
  toast(r.count === 1 ? t("Exporting the session…") : t("Exporting {n} sessions as a .zip…", { n: fmtNum(r.count) }) +
    (r.without_original ? " " + t("{n} without an original transcript are listed in its index.md.", { n: fmtNum(r.without_original) }) : ""), 6000);
}
function exportMenu(getIds, { raw = true, small = false, up = false } = {}) {
  const menu = h("details", { class: `menu${up ? " up" : ""}` },
    h("summary", { class: `btn${small ? " small" : ""}`, title: t("Download as files") }, icon("download"), t("Export")),
    h("div", { class: "menu-list", role: "menu" }, EXPORT_FORMATS.map(([fmt, label, hint]) => h("button", { type: "button", role: "menuitem",
      disabled: fmt === "raw" && !raw, title: fmt === "raw" && !raw ? t("claude.ai chats share one export file; prompt-history sessions have none") : "",
      onclick: () => { menu.open = false; downloadExport(getIds(), fmt); } }, h("b", null, label), h("span", null, hint)))));
  return menu;
}
document.addEventListener("mousedown", (e) => { document.querySelectorAll("details.menu[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; }); });

function sessionRow(x, scale = null, pick = null) {
  const tr = h("tr", { class: "row-link", onclick: (e) => { if (!e.target.closest("a, .pick")) go(`#/session/${x.id}`); } },
    pick ? h("td", { class: "pick" }) : null,
    h("td", { class: "nowrap" }, h("div", null, fmtDT(x.started_at)), h("div", { class: "muted small" }, ago(x.started_at))),
    h("td", { class: "title-cell" }, h("div", { class: "t" }, x.title || t("(untitled)"),
      x.source === "codex-import" ? h("span", { class: "agent-tag", title: t("Claude Code deleted this transcript; recovered from Codex's copy") }, t("recovered")) : null),
      x.summary ? h("div", { class: "s" }, x.summary) : screenNote(x),
      x.tags?.length ? h("div", null, x.tags.slice(0, 6).map((t) => h("span", { class: "tag" }, t))) : null),
    h("td", null, h("a", { class: "proj", href: `#/project?path=${encodeURIComponent(x.project_path || "")}` }, x.project_name || "–"),
      x.who ? h("div", { class: "who-line" }, whoTag(x.who, x.who_key)) : null),
    agentCell(x),
    h("td", { class: "num" }, fmtNum(x.n_prompts)),
    h("td", { class: "num" }, fmtNum(x.n_tool_calls), x.n_tool_errors ? h("div", { class: "muted" }, t("{n} failed", { n: x.n_tool_errors })) : null),
    barCell(fmtDur(x.active_s), x.active_s, scale?.active),
    barCell(fmtCompact(x.tokens), x.tokens, scale?.tokens),
    barCell(fmtCost(x.est_cost_usd), x.est_cost_usd, scale?.cost),
    h("td", null, outcomeBadge(x.outcome, x.analysis_status, x.source)));
  if (pick) tr.firstChild.append(pick(x, tr));
  return tr;
}

// =====================================================================================
// Session detail
// =====================================================================================
route(/^\/session\/([\w-]+)$/, async (params, id) => {
  const token = renderSeq;
  const sx = await api(`/api/sessions/${id}`);
  ART_LOCAL = sx.local;
  const analyzing = h("button", { class: "btn primary admin-only", type: "button", onclick: async () => {
    analyzing.disabled = true;
    analyzing.textContent = t("Analyzing…");
    const r = await post(`/api/sessions/${sx.id}/analyze`);
    toast(r.started ? t("Analysis started (via {agent}). This page refreshes when it finishes.", { agent: analyzer() }) : t("Analysis already running"));
    watchJob(`analyze:${sx.id}`);
  } }, sx.analysis_status === "done" ? t("Re-analyze") : t("Analyze now"));
  if (sx.source === "history" || sx.source === "remote" || sx.limited) analyzing.hidden = true; // remote: analyzed where its transcript is
  const head = h("div", { class: "session-head" },
    h("div", { style: { minWidth: 0, flex: "1 1 320px" } },
      h("h1", null, sx.title || t("(untitled session)")),
      h("div", { class: "meta" },
        h("span", null, tx("Project {name}", { name: h("a", { href: `#/project?path=${encodeURIComponent(sx.project_path || "")}` }, h("b", null, sx.project_name || "–")) })),
        sx.machine_name ? h("span", { title: sx.machine_path ? t("Ran in {path} there", { path: sx.machine_path }) : "" }, tx("on {machine}", { machine: h("b", null, sx.machine_name) })) : null,
        h("span", null, `${fmtDT(sx.started_at)} → ${fmtDT(sx.ended_at)}`),
        h("span", null, tx("{active} active · {wall} wall", { active: h("b", null, fmtDur(sx.active_s)), wall: fmtDur(sx.duration_s) })),
        sx.git_branch ? h("span", null, tx("branch {branch}", { branch: h("code", null, sx.git_branch) })) : null,
        sx.primary_model ? h("span", null, h("code", null, sx.primary_model)) : null,
        sx.cc_version ? h("span", { class: "muted" }, `${agentName(sx.agent)} ${sx.cc_version}`) : null,
        sx.source_present === 0 && sx.source !== "history" && !sx.limited ? h("span", { class: "badge", title: t("The agent deleted the original; Interlatch's archive keeps it") }, t("original deleted · archived")) : null,
        sx.source === "remote" ? h("span", { class: "badge", title: t("Analyzed on the computer it ran on, which keeps its transcript") }, t("transcript on {machine}", { machine: sx.machine_name || t("another machine") })) : null,
        sx.source === "codex-import" ? h("span", { class: "badge accent", title: t("Claude Code deleted this transcript; Interlatch recovered it from the copy Codex Desktop imported") }, h("span", { class: "sdot" }), t("recovered via Codex")) : null)),
    h("div", { style: { display: "flex", gap: "8px", alignItems: "center" } }, outcomeBadge(sx.outcome, sx.analysis_status, sx.source),
      sx.limited ? null : exportMenu(() => [sx.id], { raw: RAW_SOURCES.includes(sx.source) }), analyzing));
  setCrumbs([[t("Sessions"), "#/sessions"], [sx.project_name || "–", `#/project?path=${encodeURIComponent(sx.project_path || "")}`], [sx.title || t("(untitled session)")]], token);
  const sfact = (label, value, note, bad) => h("div", { class: "sfact" }, h("b", null, value), h("span", null, label, note ? h("small", { class: bad ? "bad" : "" }, ` · ${note}`) : null));
  const tiles = h("div", { class: "sfacts" },
    sfact(t("Active"), fmtDur(sx.active_s)),
    sfact(t("Prompts"), fmtNum(sx.n_prompts), sx.n_interrupts ? tn(sx.n_interrupts, "{n} interrupt", "{n} interrupts") : null),
    sfact(t("Tool calls"), fmtNum(sx.n_tool_calls), sx.n_tool_errors ? t("{n} failed", { n: fmtNum(sx.n_tool_errors) }) : null, true),
    sfact(t("Tokens"), fmtCompact(sx.total_tokens), sx.sub_tokens ? t("{n} subagents", { n: fmtCompact(sx.sub_tokens) }) : null),
    sfact(t("Est. API cost"), fmtCost(sx.est_cost_usd)),
    sfact(t("Lines"), `+${fmtCompact(sx.lines_added)} −${fmtCompact(sx.lines_removed)}`, tn(sx.n_files, "{n} file", "{n} files")),
    sfact(t("Peak context"), fmtCompact(sx.peak_context), [sx.usage?.context_peak_pct != null ? t("{pct}% of {size}", { pct: Math.round(sx.usage.context_peak_pct), size: fmtCompact(sx.usage.context_size) }) : null,
      sx.n_compactions ? tn(sx.n_compactions, "{n} compaction", "{n} compactions") : null].filter(Boolean).join(" · ") || null),
    planFact(sx.usage, sfact),
    sx.n_subagents ? sfact(t("Subagents"), fmtNum(sx.n_subagents)) : null);
  // summary
  const summary = h("section", { class: "card summary-card" }, h("div", { class: "card-head" }, h("h2", null, t("Summary")),
    sx.analyzed_at ? h("span", { class: "hint" }, `${t("analyzed {ago}", { ago: ago(sx.analyzed_at) })} · ${sx.analysis_model || ""}`) : null));
  if (sx.summary) {
    const kv = h("dl", { class: "kv" });
    if (sx.goal) kv.append(h("dt", null, t("Goal")), h("dd", null, sx.goal));
    if (sx.outcome_note) kv.append(h("dt", null, t("Outcome")), h("dd", null, sx.outcome_note));
    if (sx.work_types?.length) kv.append(h("dt", null, t("Work")), h("dd", null, sx.work_types.map(workTypeLabel).join(t(", "))));
    if (sx.sentiment && sx.sentiment !== "unclear") kv.append(h("dt", null, t("Mood")), h("dd", null, SENTIMENT[sx.sentiment] || sx.sentiment));
    if (sx.tags?.length) kv.append(h("dt", null, t("Tags")), h("dd", null, sx.tags.map((tag) => h("span", { class: "tag" }, tag))));
    summary.append(kv);
    if (sx.highlights?.length) summary.append(h("div", { class: "subhead" }, t("Highlights")), h("ul", { class: "bullets" }, sx.highlights.map((x) => h("li", null, x))));
    if (sx.open_threads?.length) summary.append(h("div", { class: "subhead" }, t("Open threads")), h("ul", { class: "checklist" }, sx.open_threads.map((x) => h("li", null, x))));
    if (sx.friction?.length) summary.append(h("div", { class: "subhead" }, t("Friction")), h("ul", { class: "bullets" }, sx.friction.map((f) => h("li", null, h("b", null, FRICTION_KIND[f.kind] || f.kind), t(": "), f.note))));
  } else {
    const reason = sx.source === "history" ? t("Only prompts survive for this session (recovered from Claude Code's prompt history).")
      : sx.waiting ? t("Not analyzed yet: {reason}.", { reason: sx.waiting.text })
      : sx.analysis_reason ? t("Not analyzed yet ({status}: {reason}).", { status: STATUS_LABEL[sx.analysis_status] || sx.analysis_status, reason: sx.analysis_reason })
      : t("Not analyzed yet ({status}).", { status: STATUS_LABEL[sx.analysis_status] || sx.analysis_status });
    const sv = sx.screen_sig === sx.files_sig && SCREEN[sx.screen_verdict];
    append(summary, [h("div", { class: "muted" }, reason), // the DOM's own append would print a missing part as "null"
      sv ? h("div", { class: "screen-line" }, t("Screening: "), h("span", { class: `screen-v ${sv[0]}` }, sv[1]),
        sx.screen_topic ? ` · ${sx.screen_topic}` : null, sx.screen_reason ? `: ${sx.screen_reason}` : null,
        h("span", { class: "muted" }, ` (${sx.screen_by === "rules" ? t("by rule") : sx.screen_by}, ${ago(sx.screened_at)})`)) : null,
      sx.first_prompt ? h("div", { class: "subhead" }, t("First prompt")) : null,
      sx.first_prompt ? h("div", { style: { whiteSpace: "pre-wrap" } }, sx.first_prompt.slice(0, 1200)) : null]);
  }
  const knowledge = sx.knowledge.length ? h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, t("Knowledge ({n})", { n: sx.knowledge.length }))),
    h("div", { class: "grid" }, sx.knowledge.map((k) => { const c = knowledgeCard(k, { hideSession: true }); c.id = `k-${k.id}`; return c; }))) : null;
  const ctxCard = chartCard(t("Context window"), t("Tokens in context per API call (main thread)"),
    (el, w) => contextChart(el, w, sx.api_calls, sx.markers),
    () => ({ columns: ["#", t("Time"), t("Model"), t("Context"), t("Output"), t("Cost")], num: [true, false, false, true, true, true],
      rows: sx.api_calls.filter((c) => !c.agent_id).map((c, i) => [i + 1, fmtDT(c.ts), c.model || "", fmtNum(c.input_tokens + c.cache_read_tokens + c.cache_write_tokens), fmtNum(c.output_tokens), fmtCost(c.cost_usd)]) }));
  const toolsCard = h("section", { class: "card" }, cardHead(t("Tools"), { iconName: "zap", hint: legendKey([["accent", t("calls")], ["critical", t("failed")]]) }),
    hbars(sx.tool_stats.slice(0, 12), { label: (x) => x.name.replace(/^mcp__/, "mcp:"), value: (x) => x.n, fmt: fmtNum, part: (x) => x.errors || 0, sub: (x) => (x.errors ? `(${x.errors})` : "") }));
  const changed = sx.files.filter((f) => f.edits || f.writes);
  // a file outside the project (a temp scratchpad, say) shows its last two parts; the tooltip keeps the full path
  const filePath = (p) => { const sp = shortPath(p, sx.project_path); const parts = sp.split("/");
    return sp.startsWith("/") || sp.startsWith("~/") ? (parts.length > 3 ? "…/" + parts.slice(-2).join("/") : sp) : sp; };
  const filesCard = h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, t("Files")), h("span", { class: "hint" }, t("{changed} changed · {touched} touched", { changed: changed.length, touched: sx.files.length }))),
    sx.files.length ? h("div", { class: "table-scroll" }, h("table", { class: "table-view" },
      h("thead", null, h("tr", null, h("th", null, t("File")), h("th", { class: "num" }, t("Reads")), h("th", { class: "num" }, t("Edits")), h("th", { class: "num" }, t("Lines")))),
      h("tbody", null, sx.files.slice(0, 60).map((f) => h("tr", null, h("td", { class: "mono", title: f.path, style: { fontSize: "12px" } },
        filePath(f.path).split("/").flatMap((part, i) => (i ? ["/", h("wbr"), part] : [part]))),
        h("td", { class: "num" }, f.reads || ""), h("td", { class: "num" }, (f.edits + f.writes) || ""),
        h("td", { class: "num" }, f.lines_added || f.lines_removed ? `+${f.lines_added}/−${f.lines_removed}` : "")))))) : h("div", { class: "empty" }, t("No files")));
  const extras = [];
  if (sx.subagents.length) extras.push(h("div", { class: "subhead" }, t("Subagents")), h("div", { class: "table-scroll" }, h("table", { class: "table-view" },
    h("tbody", null, sx.subagents.map((a) => h("tr", null, h("td", null, h("b", null, a.agent_type || "agent"), " ", a.description || a.agent_id),
      h("td", { class: "num" }, tn(a.n_tool_calls, "{n} tool", "{n} tools")), h("td", { class: "num" }, fmtCost(a.est_cost_usd))))))));
  const made = sx.outputs || [];
  const madeCard = made.length ? artifactsCard(made, { hint: tn(made.length, "{n} made in this session", "{n} made in this session", { n: fmtNum(made.length) }),
    project: false, session: false, compact: true }) : null;
  if (sx.workflows.length) extras.push(h("div", { class: "subhead" }, t("Workflows")), h("ul", { class: "bullets" }, sx.workflows.map((w) => h("li", null, h("b", null, w.name || w.id),
    ` · ${w.status || ""} · ${tn(w.agents || 0, "{n} agent", "{n} agents")} · ${t("{n} tokens", { n: fmtCompact(w.tokens) })}`, w.summary ? h("div", { class: "muted" }, w.summary) : null))));
  const chipsOf = (obj, label) => Object.keys(obj || {}).length ? [h("div", { class: "subhead" }, label), h("div", null, Object.entries(obj).map(([k, v]) => h("span", { class: "tag" }, `${k}${v > 1 ? " ×" + v : ""}`)))] : [];
  extras.push(...chipsOf(sx.skills, t("Skills")), ...chipsOf(sx.mcp, t("MCP servers")), ...chipsOf(sx.commands, t("Slash commands")));
  if (sx.analyses.length) extras.push(h("div", { class: "subhead" }, t("Analysis runs")), h("ul", { class: "bullets" }, sx.analyses.map((a) =>
    h("li", null, `${fmtDT(a.started_at)} · ${a.kind === "session" ? t("session") : a.kind} · ${RUN_STATUS[a.status] || a.status}${a.cost_usd != null ? " · " + fmtCost(a.cost_usd) : ""}${a.model ? " · " + a.model : ""}`, a.error ? h("div", { class: "muted" }, a.error.slice(0, 200)) : null))));
  const extrasCard = extras.length ? h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, t("Context"))), extras) : null;
  // outline: the prompts as they load, and the files that changed
  const promptList = h("ol", { class: "ol-prompts" });
  const outline = h("aside", { class: "s-outline", "aria-label": t("Session outline") },
    sx.n_prompts ? [h("h4", null, t("Prompts")), promptList] : null, // none: a Codex Cloud task, say; the list would say "Loading…" forever
    made.length ? [h("h4", null, t("Made")), h("div", { class: "ol-files ol-made" }, made.slice(0, 10).map((a) => {
      const out = a.seq == null ? safeUrl(a.url) : null; // a link the transcript recorded: http(s) only, never javascript:
      return h("a", { href: out || artifactSessionHref(a), title: a.title || a.path || a.url || "",
        target: out ? "_blank" : null, rel: out ? "noopener" : null },
        icon(a.kind), h("span", null, a.title || artifactWhere(a)));
    }),
      made.length > 10 ? h("div", { class: "muted" }, t("and {n} more", { n: made.length - 10 })) : null)] : null,
    changed.length ? [h("h4", null, t("Files changed")), h("div", { class: "ol-files" }, changed.slice(0, 12).map((f) =>
      h("div", { title: f.path }, h("span", null, f.path.split("/").pop()), f.lines_added || f.lines_removed ? h("em", null, `+${fmtCompact(f.lines_added)}`) : null)),
      changed.length > 12 ? h("div", { class: "muted" }, t("and {n} more", { n: changed.length - 12 })) : null)] : null);
  const tabs = { transcript: null, details: null };
  const defaultTab = params.seq || params.agent || params.q ? "transcript" : "details"; // a Search link points into the transcript
  const showTab = (name, record = true) => {
    for (const [k, el] of Object.entries(tabs)) el.hidden = k !== name;
    tabBar.querySelectorAll("button").forEach((b) => { const on = b.dataset.tab === name; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
    if (record) setParams({ ...params, tab: name === defaultTab ? "" : name });
  };
  const onPrompt = (ev, target) => {
    if (!ev) { promptList.replaceChildren(); return; }
    const li = h("li", { "data-seq": ev.seq, tabindex: 0, role: "link", onclick: () => { showTab("transcript"); target.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" }); },
      onkeydown: (e) => { if (e.key === "Enter") li.click(); } }, h("span", null, (ev.text || "").replace(/\s+/g, " ").slice(0, 120)), h("small", null, fmtTime(ev.ts)));
    li.target = target;
    const after = [...promptList.children].find((x) => +x.dataset.seq > ev.seq);
    promptList.insertBefore(li, after || null);
  };
  const transcript = sx.limited
    ? h("section", { class: "card" }, h("p", { class: "muted" }, t("On this hub you see each session's summary and project lessons. Its transcript stays with whoever ran it.")))
    : sx.source === "remote"
    ? h("section", { class: "card" }, h("p", { class: "muted" }, t("This session was analyzed on {machine}, which keeps its transcript. Only its summary and project lessons were shared.", { machine: sx.machine_name || t("another machine") })))
    : transcriptCard(sx, params.seq ? +params.seq : null, params.agent || "", onPrompt, queryTerms(params.q));
  tabs.transcript = transcript;
  tabs.details = h("div", { class: "grid cols-main" },
    h("div", { class: "grid", style: { alignContent: "start" } }, summary, knowledge),
    h("div", { class: "grid", style: { alignContent: "start" } }, ...(sx.limited ? [] : [madeCard, ctxCard, toolsCard, filesCard, extrasCard])));
  const tabBar = h("div", { class: "seg s-tabs", role: "group", "aria-label": t("Session view") },
    h("button", { type: "button", "data-tab": "transcript", onclick: () => showTab("transcript") }, t("Transcript")),
    h("button", { type: "button", "data-tab": "details", onclick: () => showTab("details") }, t("Details")));
  const outlineBtn = h("button", { type: "button", class: `chip ol-toggle ${sessionOutline ? "on" : ""}`, "aria-pressed": String(sessionOutline), title: t("Show the prompts and files beside the transcript"),
    onclick: () => {
      sessionOutline = !sessionOutline;
      try { localStorage.setItem("interlatch.outline", sessionOutline ? "1" : "0"); } catch (e) { /* this tab only */ }
      outlineBtn.classList.toggle("on", sessionOutline); outlineBtn.setAttribute("aria-pressed", String(sessionOutline));
      page.classList.toggle("with-outline", sessionOutline);
    } }, icon("outline"), t("Outline"));
  const kchips = sx.knowledge.length ? h("div", { class: "s-kchips" }, sx.knowledge.slice(0, 8).map((k) =>
    h("button", { type: "button", class: `s-kchip k-${k.kind}`, title: `${kindLabel(k.kind)}: ${k.title}`, onclick: () => {
      showTab("details");
      const card = document.getElementById(`k-${k.id}`);
      if (card) { card.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" }); card.classList.add("flash"); setTimeout(() => card.classList.remove("flash"), 1600); }
    } }, icon(KIND[k.kind] ? k.kind : "dot"), h("span", null, k.title))),
    sx.knowledge.length > 8 ? h("button", { type: "button", class: "s-kchip more", onclick: () => showTab("details") }, t("+{n} more", { n: sx.knowledge.length - 8 })) : null) : null;
  const summaryP = sx.summary ? h("p", { class: "s-summary gloss" }, sx.summary)
    : sx.waiting ? h("p", { class: "s-summary s-waiting" }, icon("queued"), " ", t("Not analyzed yet: {reason}.", { reason: sx.waiting.text })) : null;
  markTerms(summaryP, queryTerms(params.q)); // opened from Search: the terms stay marked here and in the transcript
  // someone limited to projects: the summary and lessons only, so no transcript tab and no outline of prompts
  const page = h("div", { class: `session-page ${sessionOutline && !sx.limited ? "with-outline" : ""}` },
    h("div", { class: "s-main" }, head, tiles,
      summaryP, kchips,
      sx.limited ? h("p", { class: "muted" }, t("On this hub you see each session's summary and project lessons. Its transcript stays with whoever ran it."))
        : h("div", { class: "s-tabbar" }, tabBar, outlineBtn),
      tabs.transcript, tabs.details),
    sx.limited ? null : outline);
  showTab(sx.limited ? "details" : Object.hasOwn(tabs, params.tab || "") ? params.tab : defaultTab, false);
  trackOutline(promptList);
  return page;
});
let sessionOutline = (() => { try { return localStorage.getItem("interlatch.outline") !== "0"; } catch (e) { return true; } })();
function trackOutline(list) { // mark the prompt currently at the top of the transcript
  const scroller = $("#app");
  let raf = 0;
  const onScroll = () => {
    if (!list.isConnected) { if (list.seen) scroller.removeEventListener("scroll", onScroll); return; } // gone, or not mounted yet
    list.seen = true;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      const top = scroller.getBoundingClientRect().top + 90;
      let current = null;
      for (const li of list.children) {
        const el = li.target;
        if (!el?.offsetParent) continue;
        if (el.getBoundingClientRect().top <= top) current = li; else break;
      }
      for (const li of list.children) li.classList.toggle("on", li === current);
    });
  };
  scroller.addEventListener("scroll", onScroll, { passive: true });
}

function transcriptCard(sx, focusSeq, agent, onPrompt = null, terms = []) {
  const assistant = agentShort(sx.agent);
  const state = { agent, kinds: new Set(["prompt", "text", "tool", "system", "command"]), offset: 0, start: 0, total: 0, terms };
  const bySeq = new Map(); // event seq -> the element showing it (a tool result lives in its call's row)
  const list = h("div", { class: "transcript" });
  let sink = list; // where renderEvent draws: the list, or a holder for earlier events that are then put in front
  const more = h("button", { class: "btn", type: "button", onclick: () => load(true) }, t("Load more"));
  const earlier = h("button", { class: "btn", type: "button", hidden: true, onclick: () => loadEarlier() }, t("Load earlier"));
  const info = h("span", { class: "hint" });
  const kindMap = { prompt: ["prompt", "attachment"], text: ["text"], tool: ["tool_use", "tool_result"], thinking: ["thinking"],
    system: ["compact", "interrupt", "api_error", "notice", "notification", "compact_summary", "meta", "command_output", "bash_output"], command: ["command", "bash_input"] };
  let toolBlock = null, toolRows = {};
  async function load(append) {
    if (!append) { state.offset = 0; list.replaceChildren(); bySeq.clear(); toolBlock = null; toolRows = {}; if (state.kinds.has("prompt")) onPrompt?.(null); }
    const kinds = [...state.kinds].flatMap((k) => kindMap[k]).join(",");
    const params = { agent: state.agent, kinds, limit: 400, offset: state.offset };
    if (focusSeq != null && !append) params.around = focusSeq;
    const data = await api(`/api/sessions/${sx.id}/events`, params);
    if (!append) state.start = data.offset; // a deep link (?seq=) starts part-way through
    state.offset = data.offset + data.items.length;
    state.total = data.total;
    for (const ev of data.items) renderEvent(ev);
    syncControls();
    if (focusSeq != null && !append) {
      const target = bySeq.get(focusSeq);
      if (target) focusEl(target);
      focusSeq = null;
    }
  }
  function focusEl(el) { // the linked event: outlined, opened if a tool row, and its first mention centred
    if (el.tagName === "DETAILS") el.open = true;
    el.classList.add("highlight");
    const hit = el.querySelector("mark.qhit");
    hit?.classList.add("on");
    setTimeout(() => (hit || el).scrollIntoView({ block: "center" }), 60);
  }
  function syncControls() {
    more.hidden = state.offset >= state.total;
    earlier.hidden = state.start <= 0;
    earlier.textContent = t("Load {n} earlier", { n: fmtNum(Math.min(400, state.start)) });
    info.textContent = t("{a} of {b} events", { a: fmtNum(state.offset - state.start), b: fmtNum(state.total) });
    if (expand.classList.contains("on")) list.querySelectorAll("details.tool-row").forEach((d) => (d.open = true));
  }
  async function loadEarlier() {
    const from = Math.max(0, state.start - 400);
    const kinds = [...state.kinds].flatMap((k) => kindMap[k]).join(",");
    const data = await api(`/api/sessions/${sx.id}/events`, { agent: state.agent, kinds, limit: state.start - from, offset: from });
    const holder = h("div");
    const keep = toolBlock;
    sink = holder; toolBlock = null;
    for (const ev of data.items) renderEvent(ev);
    sink = list; toolBlock = keep;
    const anchor = list.firstElementChild, before = anchor?.getBoundingClientRect().top;
    list.prepend(...holder.childNodes);
    if (anchor) $("#app").scrollTop += anchor.getBoundingClientRect().top - before; // keep your place
    state.start = from;
    syncControls();
  }
  function renderEvent(ev) {
    const el = drawEvent(ev);
    if (!el) return;
    bySeq.set(ev.seq, el.closest("[data-seq]") || el);
    if (!state.terms.length) return;
    markTerms(el, state.terms);
    const row = el.closest("details.tool-row"); // a folded tool row with a mention inside says so
    if (row) row.classList.toggle("qmatch", !!row.querySelector("mark.qhit"));
  }
  function drawEvent(ev) {
    if (ev.kind === "tool_use") {
      if (!toolBlock) { toolBlock = h("div", { class: "tools-block" }); sink.append(toolBlock); }
      const row = h("details", { class: "tool-row", "data-seq": ev.seq },
        h("summary", null, h("span", { class: "ticon" }, "•"), h("span", { class: "tname" }, (ev.tool_name || t("tool")).replace(/^mcp__/, "mcp:")),
          h("span", { class: "tsum", title: ev.text }, ev.text.includes(": ") ? ev.text.split(": ").slice(1).join(": ") : ""),
          h("span", { class: "tstat" }, fmtTime(ev.ts))));
      const detail = h("div", { class: "tdetail" });
      if (ev.meta?.input) detail.append(h("div", { class: "plabel" }, t("Input")), h("pre", null, prettyJson(ev.meta.input)));
      row.append(detail);
      toolBlock.append(row);
      if (ev.tool_use_id) toolRows[ev.tool_use_id] = row;
      return row;
    }
    if (ev.kind === "tool_result") {
      const row = ev.tool_use_id && toolRows[ev.tool_use_id];
      const body = [h("div", { class: "plabel" }, ev.is_error ? t("Error") : t("Result")), h("pre", null, ev.text || t("(empty)"))];
      if (row) {
        row.querySelector(".ticon").textContent = ev.is_error ? "✗" : "✓";
        if (ev.is_error) row.classList.add("err");
        row.querySelector(".tdetail").append(...body);
        return body[1];
      }
      if (!toolBlock) { toolBlock = h("div", { class: "tools-block" }); sink.append(toolBlock); }
      const orphan = h("details", { class: `tool-row ${ev.is_error ? "err" : ""}`, "data-seq": ev.seq },
        h("summary", null, h("span", { class: "ticon" }, ev.is_error ? "✗" : "✓"), h("span", { class: "tname" }, ev.tool_name || t("result")), h("span", { class: "tsum" }, (ev.text || "").slice(0, 160)), h("span", { class: "tstat" }, fmtTime(ev.ts))),
        h("div", { class: "tdetail" }, body));
      toolBlock.append(orphan);
      return orphan;
    }
    toolBlock = null;
    let node;
    const who = (label, extra) => h("div", { class: "who" }, h("b", null, label), h("span", null, fmtDT(ev.ts)), extra || null);
    switch (ev.kind) {
      case "prompt":
        node = h("div", { class: "msg user", "data-seq": ev.seq }, who(ev.meta?.subagent ? t("Task prompt") : t("You"), ev.meta?.queued ? h("span", { class: "tag" }, t("queued while {agent} worked", { agent: assistant })) : null),
          h("div", { class: "body" }, ev.text));
        if (onPrompt && !ev.meta?.subagent && !state.agent) onPrompt(ev, node);
        break;
      case "text":
        node = h("div", { class: "msg assistant", "data-seq": ev.seq }, who(assistant, [ev.meta?.model ? h("span", { class: "muted" }, ev.meta.model) : null, ev.meta?.phase === "commentary" ? h("span", { class: "tag" }, t("progress")) : null]), mdEl(ev.text));
        break;
      case "thinking":
        node = h("div", { class: "msg thinking", "data-seq": ev.seq }, who(t("Thinking")), h("div", { style: { whiteSpace: "pre-wrap" } }, ev.text));
        break;
      case "command": case "bash_input":
        node = h("div", { class: "msg user", "data-seq": ev.seq }, who(ev.kind === "command" ? t("Command") : t("Shell")), h("code", null, ev.text));
        break;
      default:
        node = h("div", { class: "msg system", "data-seq": ev.seq }, h("b", null, EVENT_KIND[ev.kind] || ev.kind.replace(/_/g, " ")), " · ", fmtTime(ev.ts), " — ", (ev.text || "").slice(0, 600));
    }
    sink.append(node);
    if (ev.kind === "text" || ev.kind === "prompt") glossify(node, 4);
    return node;
  }
  const chip = (key, label) => {
    const c = h("button", { type: "button", class: `chip ${state.kinds.has(key) ? "on" : ""}`, "aria-pressed": String(state.kinds.has(key)), onclick: () => {
      state.kinds.has(key) ? state.kinds.delete(key) : state.kinds.add(key);
      c.classList.toggle("on");
      c.setAttribute("aria-pressed", String(state.kinds.has(key)));
      load(false);
    } }, label);
    return c;
  };
  const agentSel = h("select", { onchange: (e) => { state.agent = e.target.value; load(false); } },
    sx.agents.map((a) => h("option", { value: a.agent_id, selected: a.agent_id === state.agent }, a.label.slice(0, 90))));
  const expand = h("button", { class: "chip", type: "button", "aria-pressed": "false", onclick: () => {
    const open = !expand.classList.contains("on");
    expand.classList.toggle("on", open);
    expand.setAttribute("aria-pressed", String(open));
    list.querySelectorAll("details.tool-row").forEach((d) => (d.open = open));
  } }, t("Expand tools"));
  load(false);
  return h("section", { class: "transcript-pane" },
    h("div", { class: "transcript-controls" }, sx.agents.length > 1 ? agentSel : null,
      chip("prompt", t("Prompts")), chip("text", assistant), chip("tool", t("Tool calls")), chip("command", t("Commands")), chip("system", t("System")), chip("thinking", t("Thinking")), expand,
      h("span", { class: "spacer" }), info),
    h("div", { class: "load-more earlier" }, earlier), list, h("div", { class: "load-more" }, more));
}
// transcript events shown as a system line (the codes stay; their labels are translated)
const EVENT_KIND = {
  compact: t("compact"), interrupt: t("interrupt"), api_error: t("api error"), notice: t("notice"), notification: t("notification"),
  compact_summary: t("compact summary"), meta: t("meta"), command_output: t("command output"), bash_output: t("bash output"), attachment: t("attachment"),
};
function prettyJson(text) {
  try { return JSON.stringify(JSON.parse(text), null, 2); } catch (e) { return text; }
}

// =====================================================================================
// Knowledge
// =====================================================================================
// `ask`: a case file (hasCase) this browser hasn't answered opens with its scene and question, the lesson after
function knowledgeCard(k, { compact = false, hideSession = false, ask = false } = {}) {
  const card = h("article", { class: `kcard ${k.pinned ? "pinned" : ""}` });
  const head = h("div", { class: "khead" }, kindChip(k.kind),
    h("span", { class: "kmeta" },
      k.scope === "global" ? h("span", { class: "scope-tag", title: t("Applies across projects") }, icon("domain"), t("global")) : null,
      k.source === "memory" ? h("span", { class: "scope-tag", title: t("Imported from the agent's own memory notes") }, icon("knowledge"), t("{agent} memory", { agent: agentShort(k.agent) })) : null,
      k.source === "team" ? teamTag(k) : null,
      stageTag(k.stage, confirmCount(k), k.stage_reason, { quiet: true }),
      confidenceMeter(k.confidence)));
  const foot = h("div", { class: "kfoot" },
    h("span", { class: "kfoot-meta" },
      k.project_name ? h("span", { class: "meta-item", title: k.project_path || "" }, icon("projects"), k.project_name) : null,
      k.who ? h("span", { class: "meta-item" }, whoTag(k.who, k.who_key, "#/knowledge/all")) : null,
      !hideSession && k.session_id ? h("a", { class: "meta-item session-link", href: `#/session/${k.session_id}`, title: k.session_title || "" }, icon("sessions"), h("span", { class: "ellipsis" }, k.session_title || t("session"))) : null,
      k.created_at ? h("span", { class: "meta-item" }, fmtDate(k.created_at)) : null),
    knowledgeActions(k, card, () => card.remove()));
  const fill = (asking, parts) => {
    card.classList.toggle("asking", asking);
    card.replaceChildren();
    append(card, [head, parts, foot]);
    if (card.isConnected) card.querySelectorAll(".gloss").forEach((el) => glossify(el)); // the first render: by render()
  };
  const again = ask && hasCase(k) ? () => { forgetCase(k.id); askNow(); } : null;
  // the lesson itself; `picked`: the answer just chosen, when there was a question
  const show = (picked) => fill(false, lessonParts(k, compact, picked, again));
  const askNow = () => fill(true, caseQuestion(k, show));
  if (again && !caseAnswers()[k.id]) askNow();
  else show(null);
  return card;
}
function lessonParts(k, compact, picked, again) {
  const c = hasCase(k) ? k.case : null;
  const body = mdEl(k.body || "", `kbody ${compact ? "clamp" : ""}`);
  const leads = c ? c.ruled_out.filter((x) => x.lead) : [];
  const parts = [
    picked ? caseVerdict(c, picked) : null,
    h("div", { class: "ktitle" }, k.title),
    c && !picked ? caseScene(c) : null, // just answered: the scene was read a moment ago
    body,
  ];
  if (compact && (k.body || "").length > 380) {
    const more = h("button", { class: "link-btn more-btn", type: "button", onclick: () => { body.classList.toggle("clamp"); more.textContent = body.classList.contains("clamp") ? t("Show more") : t("Show less"); } }, t("Show more"));
    parts.push(more);
  }
  if (leads.length) parts.push(compact && !picked
    ? h("details", { class: "kcase-more" }, h("summary", null, tn(leads.length, "{n} lead ruled out", "{n} leads ruled out")), caseLeads(leads))
    : h("div", { class: "kcase-block" }, h("span", { class: "kcase-label" }, t("Ruled out")), caseLeads(leads)));
  parts.push(k.tags?.length ? h("div", { class: "ktags" }, k.tags.slice(0, 6).map((t) => h("span", { class: "tag" }, t))) : null);
  if (again) parts.push(h("button", { type: "button", class: "link-btn kcase-again", onclick: again }, icon("again"), t("Ask me again")));
  return parts;
}

// Case files (analyze.case_of): a fix, gotcha or decision opens with the scene and asks before it tells. The answers
// to pick from are the real one and the leads the session ruled out, never made-up ones. What this browser answered,
// and whether it asks at all, stay in this browser.
const CASES_KEY = "interlatch.cases", ASK_KEY = "interlatch.cases.ask", SKIPS_KEY = "interlatch.cases.skips";
const SKIPS_TO_STOP = 5; // someone who keeps skipping stops being asked
let caseLog = null;
// a case's one-line texts: inline code only, since some sit inside buttons (no links there)
function codeSpans(text) { return escapeHtml(text || "").replace(/`([^`\n]+)`/g, "<code>$1</code>"); }
function hasCase(k) { return !!(k.case && k.case.scene && k.case.question && k.case.answer && Array.isArray(k.case.ruled_out)); }
function caseScene(c) { return h("div", { class: "kcase-block" }, h("span", { class: "kcase-label" }, t("The scene")), mdEl(c.scene, "kcase-scene")); }
function caseLeads(leads) {
  return h("ul", { class: "kcase-leads" }, leads.map((x) =>
    h("li", null, icon("x"), h("span", null, h("span", { class: "lead", html: codeSpans(x.lead) }), x.why ? h("span", { class: "why", html: codeSpans(x.why) }) : null))));
}
function caseAnswers() {
  if (!caseLog) {
    try { caseLog = JSON.parse(localStorage.getItem(CASES_KEY) || "{}") || {}; } catch { caseLog = {}; }
  }
  return caseLog;
}
function saveCases() {
  const ids = Object.keys(caseLog);
  ids.slice(0, Math.max(0, ids.length - 3000)).forEach((id) => delete caseLog[id]); // the oldest lessons go first
  try { localStorage.setItem(CASES_KEY, JSON.stringify(caseLog)); } catch { /* remembered for this tab only */ }
}
function forgetCase(id) { delete caseAnswers()[id]; saveCases(); }
function askFirst() { try { return localStorage.getItem(ASK_KEY) !== "0"; } catch { return true; } }
function setAskFirst(on) {
  try {
    if (on) localStorage.removeItem(ASK_KEY); else localStorage.setItem(ASK_KEY, "0");
    localStorage.removeItem(SKIPS_KEY);
  } catch { /* this tab only */ }
}
function recordCase(id, outcome) { // right | wrong | recalled (thought it over, then looked) | skipped
  caseAnswers()[id] = outcome;
  saveCases();
  let skips = 0;
  try {
    skips = outcome === "skipped" ? Number(localStorage.getItem(SKIPS_KEY) || 0) + 1 : 0;
    localStorage.setItem(SKIPS_KEY, String(skips));
  } catch { /* not counted */ }
  if (skips >= SKIPS_TO_STOP) {
    setAskFirst(false);
    document.querySelectorAll(".ask-chip").forEach((c) => { c.classList.remove("on"); c.setAttribute("aria-pressed", "false"); });
    toast(t("You skipped {n} questions in a row, so cases now open with the answer. Turn Ask first back on in All knowledge.", { n: skips }), 7000);
  }
}
function caseOptions(k) { // the real answer and up to two ruled-out leads; the order is fixed per case, not always first
  const opts = [{ label: k.case.answer, right: true }, ...k.case.ruled_out.filter((x) => x.lead).slice(0, 2).map((x) => ({ label: x.lead, why: x.why }))];
  const turn = k.id % opts.length;
  return [...opts.slice(turn), ...opts.slice(0, turn)];
}
function caseQuestion(k, show) {
  const c = k.case, opts = caseOptions(k);
  const answer = (outcome, picked) => { recordCase(k.id, outcome); show(picked); };
  return [
    caseScene(c),
    h("div", { class: "kcase-q", html: codeSpans(c.question) }),
    opts.length > 1
      ? h("div", { class: "kcase-options", role: "group", "aria-label": t("Your call") }, opts.map((o, i) =>
        h("button", { type: "button", class: "kcase-option", onclick: () => answer(o.right ? "right" : "wrong", o) }, h("b", null, "ABC"[i]), h("span", { html: codeSpans(o.label) }))))
      : h("p", { class: "kcase-hint" }, t("Think of your answer first.")),
    h("div", { class: "kcase-acts" },
      opts.length > 1 ? null : h("button", { type: "button", class: "btn small", onclick: () => answer("recalled", { recalled: true }) }, t("Show the answer")),
      h("button", { type: "button", class: "link-btn", onclick: () => answer("skipped", null) }, t("Skip, just show me"))),
  ];
}
function caseVerdict(c, picked) {
  if (picked.right) return h("div", { class: "kcase-verdict right", role: "status" }, icon("completed"), h("span", null, h("b", null, t("Right.")), " ", h("span", { html: codeSpans(c.answer) })));
  return h("div", { class: `kcase-verdict ${picked.recalled ? "" : "wrong"}`, role: "status" }, icon(picked.recalled ? "sparkles" : "x"),
    h("span", null,
      picked.recalled ? null : [h("b", null, t("Ruled out in the session.")), " ", picked.why ? h("span", { html: codeSpans(picked.why) }) : null, h("br")],
      h("b", null, t("The answer:")), " ", h("span", { html: codeSpans(c.answer) })));
}
// a teammate's lesson, sent back by the team hub: whose computers stated it, each with its initials like your own lessons'
function teamTag(k) {
  const names = k.team_from || [];
  const who = h("span", { class: "who-list" }, names.slice(0, 3).map((n) => whoTag(n)),
    names.length > 3 ? h("span", { class: "muted" }, `+${names.length - 3}`) : null);
  return h("span", { class: "scope-tag team-tag", title: t("A teammate's lesson, sent back by the team hub. Your agent gets it too.") }, icon("team"),
    names.length ? tx("from {names}", { names: who }) : t("from teammates"));
}
function knowledgeActions(k, node, onDismiss) {
  const pin = h("button", { type: "button", title: k.pinned ? t("Unpin") : t("Pin (always kept in syntheses)"), "aria-pressed": String(!!k.pinned), onclick: async () => {
    await post(`/api/knowledge/${k.id}`, { pinned: !k.pinned });
    k.pinned = !k.pinned;
    node.classList.toggle("pinned", k.pinned);
    pin.title = k.pinned ? t("Unpin") : t("Pin (always kept in syntheses)");
    pin.setAttribute("aria-pressed", String(k.pinned));
    toast(k.pinned ? t("Pinned") : t("Unpinned"));
  } }, icon("pin"));
  return h("div", { class: "kactions" }, pin,
    h("button", { type: "button", title: t("Dismiss (hide and exclude from knowledge bases)"), onclick: async () => {
      await post(`/api/knowledge/${k.id}`, { status: "dismissed" });
      k.dismissed = true;
      onDismiss();
      toast(t("Dismissed"));
    } }, icon("x")));
}
function knowledgeTable(items) {
  const scopeOf = (k) => (k.scope === "global" ? t("global") : k.project_name || "");
  const cols = [
    { key: "kind", label: t("Kind"), value: (k) => kindLabel(k.kind) },
    { key: "title", label: t("Knowledge"), value: (k) => k.title },
    { key: "project", label: t("Project"), value: scopeOf },
    { key: "session", label: t("From") },
    { key: "stage", label: t("Stage"), value: (k) => (STAGE_RANK[k.stage] ?? 2) * 1000 - confirmCount(k) },
    { key: "confidence", label: t("Confidence"), value: (k) => ({ high: 0, medium: 1, low: 2 })[k.confidence] ?? 3 },
    { key: "created", label: tc("date", "Added"), desc: true, value: (k) => k.created_at },
    { key: "actions", label: "" },
  ];
  const row = (k) => {
    if (k.dismissed) return [];
    const tr = h("tr", { class: k.pinned ? "pinned" : "" },
      h("td", { class: "nowrap kind-cell" }, icon(KIND[k.kind] ? k.kind : "dot"), kindLabel(k.kind)),
      h("td", { class: "title-cell" }, h("div", { class: "t" }, k.title), k.body ? h("div", { class: "s" }, plainText(k.body)) : null),
      h("td", { class: "nowrap" }, scopeOf(k) || "–"),
      h("td", { class: "from-cell" }, k.session_id ? h("a", { href: `#/session/${k.session_id}` }, (k.session_title || t("session")).slice(0, 44))
        : k.source === "team" ? teamTag(k)
        : h("span", { class: "muted" }, k.source === "memory" ? t("{agent} memory", { agent: agentShort(k.agent) }) : "–"),
        k.who ? h("div", { class: "who-line" }, whoTag(k.who, k.who_key, "#/knowledge/all")) : null),
      h("td", { class: "nowrap" }, stageTag(k.stage, confirmCount(k), k.stage_reason) || h("span", { class: "muted" }, "–")),
      h("td", { class: "nowrap" }, confidenceMeter(k.confidence) || h("span", { class: "muted" }, "–")),
      h("td", { class: "nowrap" }, fmtDate(k.created_at)));
    tr.append(h("td", { class: "actions-cell" }, knowledgeActions(k, tr, () => { tr.closeDetail(); tr.remove(); })));
    const leads = hasCase(k) ? k.case.ruled_out.filter((x) => x.lead) : [];
    return expandable(tr, cols.length, () => [hasCase(k) ? caseScene(k.case) : null, mdEl(k.body || "", "kbody"),
      leads.length ? h("div", { class: "kcase-block" }, h("span", { class: "kcase-label" }, t("Ruled out")), caseLeads(leads)) : null,
      k.tags?.length ? h("div", { style: { marginTop: "6px" } }, k.tags.map((t) => h("span", { class: "tag" }, t))) : null], { indent: 1 });
  };
  return localTable(items, cols, row, { empty: t("No knowledge matches"), cls: "knowledge-table" });
}

// The section's landing page: one card per way into the knowledge, each with a glance at what is inside
route(/^\/knowledge$/, async (params) => {
  if (limited() || params.q || params.kind || params.project || params.source) { // older links filtered the list here
    history.replaceState(null, "", `#/knowledge/all?${new URLSearchParams(params)}`);
    lastHash = location.hash;
    return knowledgeListView(params);
  }
  // the Learn band's picks come with the page; without them it is a plain link into the library
  const [data, glance] = await Promise.all([api("/api/knowledge/hub"), api("/api/learning/glance").catch(() => null)]);
  const k = data.knowledge, g = data.glossary, rv = data.review;
  const hubCard = (href, iconName, title, desc, num, numLabel, ...body) => h("a", { class: "card hub-card", href },
    h("div", { class: "hub-top" }, h("span", { class: "hub-icon" }, icon(iconName)),
      h("div", { class: "hub-title" }, h("h2", null, title), h("p", null, desc)), icon("arrow", "hub-go")),
    h("div", { class: "hub-num" }, h("b", null, num), h("span", null, numLabel)),
    h("div", { class: "hub-body" }, ...body));

  const cats = Object.entries(g.categories).sort((a, b) => b[1] - a[1]);
  const map = hubCard("#/map", "map", t("Map"), t("Your glossary as a mindmap, by project and theme"),
    fmtNum(data.map.projects), t("projects · {n} categories", { n: fmtNum(cats.length) }) + (data.map.themes ? " · " + t("{n} themes", { n: fmtNum(data.map.themes) }) : ""),
    g.total ? miniMindmap(cats.slice(0, 7)) : h("div", { class: "hub-empty" }, t("Drawn from the glossary once it is built")));

  const kinds = Object.keys(KIND).filter((x) => k.counts[x]).sort((a, b) => k.counts[b] - k.counts[a]);
  const all = hubCard("#/knowledge/all", "knowledge", t("All knowledge"), t("Fixes, gotchas, decisions and facts from your sessions"),
    fmtNum(k.total), k.new_week ? t("items · {n} from the last 7 days", { n: fmtNum(k.new_week) }) : t("items"),
    k.total ? [
      h("div", { class: "stackbar thin hub-kinds", "aria-hidden": "true" }, kinds.map((x, i) =>
        h("span", { class: "seg", style: { flexGrow: String(k.counts[x]), background: `var(--series-${(i % 8) + 1})` } }))),
      h("div", { class: "hub-legend" }, kinds.slice(0, 5).map((x, i) =>
        h("span", null, h("i", { style: { background: `var(--series-${(i % 8) + 1})` } }), kindPlural(x), h("b", null, fmtNum(k.counts[x]))))),
      h("ul", { class: "hub-recent" }, k.recent.slice(0, 3).map((x) => h("li", null, icon(KIND[x.kind] ? x.kind : "dot"), h("span", null, x.title)))),
    ] : h("div", { class: "hub-empty" }, t("Sessions are analyzed automatically once idle")));

  const gloss = hubCard("#/glossary", "glossary", t("Glossary"), t("Every term, acronym and file name your sessions use, defined"),
    fmtNum(g.total), t("terms in {n} categories", { n: fmtNum(cats.length) }),
    g.top.length ? h("div", { class: "hub-terms" }, g.top.slice(0, 16).map((gt) =>
      h("span", { class: "hub-term", title: tn(gt.n_sessions, "{n} session", "{n} sessions", { n: fmtNum(gt.n_sessions) }) }, icon(ICONS[gt.category] ? gt.category : "dot"), gt.term)))
      : h("div", { class: "hub-empty" }, t("Built from your knowledge items")));

  const days = rv ? weekDays(rv.start) : [];
  const reviews = hubCard("#/reviews", "reviews", t("Weekly reviews"), t("Your week at a glance: wins, open threads, what to try next"),
    rv ? t("Week {n}", { n: rv.period.replace(/^\d+-W/, "") }) : "–",
    rv ? `${tn(rv.stats.sessions, "{n} session", "{n} sessions", { n: fmtNum(rv.stats.sessions) })} · ${t("{dur} active", { dur: fmtHours(rv.stats.active_s) })}` : t("no reviews yet"),
    rv ? [
      rv.headline ? h("p", { class: "hub-headline" }, rv.headline) : null,
      dayBars(rv.daily, days, { height: 44 }),
    ] : h("div", { class: "hub-empty" }, t("Written automatically once a week's sessions are analyzed")));

  return h("div", null,
    h("div", { class: "page-head" }, titled("book", h("h1", null, t("Knowledge")),
      h("div", { class: "sub" }, t("What your sessions taught you, and how to put it to use"))),
      h("div", { class: "head-actions" },
        k.cases ? h("a", { class: "btn", href: "#/knowledge/all?cases=1", title: t("Fixes, gotchas and decisions told as cases: the scene, a question, then the answer") },
          icon("reviews"), t("Case files"), h("span", { class: "count" }, fmtNum(k.cases))) : null,
        h("a", { class: "btn", href: `#/project?path=${encodeURIComponent("__global__")}` }, t("Global playbook")))),
    learningGlance(glance),
    h("div", { class: "hub-grid" }, map, all, gloss, reviews));
});

// A decorative mindmap: the biggest glossary categories around a centre, dots sized by term count
function miniMindmap(cats) {
  const W = 300, H = 120, cx = W / 2, cy = H / 2, max = Math.max(...cats.map((c) => c[1]));
  const svg = s("svg", { class: "hub-mm", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("Largest categories: {list}", { list: cats.map(([c, n]) => `${catLabel(c)} ${n}`).join(t(", ")) }) });
  cats.forEach(([cat, n], i) => {
    const a = (i / cats.length) * Math.PI * 2 - Math.PI / 2;
    const x = cx + Math.cos(a) * 112, y = cy + Math.sin(a) * 42, r = 3 + (n / max) * 6;
    svg.append(s("path", { class: "hub-mm-link", d: `M${cx},${cy} Q${(cx + x) / 2},${y} ${x},${y}` }),
      s("circle", { class: "hub-mm-node", cx: x, cy: y, r, style: { fill: `var(--series-${(i % 8) + 1})` } }),
      s("text", { x: x + (Math.cos(a) >= 0 ? r + 4 : -r - 4), y: y + 3.5, "text-anchor": Math.cos(a) >= 0 ? "start" : "end" }, catLabel(cat)));
  });
  svg.append(s("circle", { class: "hub-mm-root", cx, cy, r: 7 }));
  return svg;
}
// The seven local days of a week starting at `start`
function weekDays(start) {
  const d0 = new Date(start);
  return Array.from({ length: 7 }, (_, i) => new Date(d0.getTime() + i * 86400000 + 43200000));
}
const dayFmt = new Intl.DateTimeFormat(LOCALE, { weekday: "short" });
function dayBars(values, days, { height = 90 } = {}) { // active time per day of one week, labelled Mon..Sun
  const max = Math.max(0, ...values);
  return h("div", { class: "day-bars", style: { "--h": `${height}px` }, role: "img",
    "aria-label": values.map((v, i) => `${days[i] ? dayFmt.format(days[i]) : i}: ${fmtDur(v)}`).join(", ") },
    values.map((v, i) => {
      const col = h("div", { class: "db-col" },
        h("div", { class: "db-track" }, h("i", { class: v ? (v === max ? "peak" : "") : "zero", style: v ? { height: `${Math.max(6, (v / max) * 100)}%` } : null })),
        h("span", null, days[i] ? dayFmt.format(days[i]).slice(0, 2) : ""));
      hoverable(col, () => [v ? t("{dur} active", { dur: fmtDur(v) }) : t("No sessions"), days[i] ? fmtDate(days[i].toISOString()) : ""], { focusable: false });
      return col;
    }));
}

route(/^\/learn$/, (params) => learningView(params));
route(/^\/knowledge\/all$/, (params) => knowledgeListView(params));
async function knowledgeListView(params) {
  setCrumbs(defaultCrumbs("/knowledge/all", params));
  const state = { q: params.q || "", kind: params.kind || "", project: params.project || "", source: params.source || "", who: params.who || "", cases: params.cases === "1" ? "1" : "" };
  const mode = viewMode("knowledge", "cards");
  const box = h("div");
  const count = h("span", { class: "sub" });
  const chipsBox = h("div", { class: "filters", style: { marginBottom: "14px" } });
  let loaded = false;
  let sources = {};
  async function load() {
    const data = await api("/api/knowledge", { ...state, limit: 400 });
    sources = data.sources || {};
    const ask = askFirst() && !state.q; // a search looks something up: its cases open with the answer
    box.replaceChildren(mode === "list" ? h("section", { class: "card flush" }, knowledgeTable(data.items))
      : data.items.length ? h("div", { class: "kgrid" }, data.items.map((k) => knowledgeCard(k, { compact: true, ask })))
      : h("div", { class: "card empty" }, state.cases ? t("No case files match. A fix, gotcha or decision gets one when its session is analyzed.") : t("No knowledge matches")));
    if (loaded) box.querySelectorAll(".gloss").forEach((el) => glossify(el)); // the first render is glossified by render()
    loaded = true;
    count.textContent = tn(data.items.length, "{n} item", "{n} items", { n: fmtNum(data.items.length) });
    const total = Object.values(data.counts).reduce((a, b) => a + b, 0);
    chipsBox.replaceChildren();
    append(chipsBox, [
      h("button", { type: "button", class: `chip ${!state.kind ? "on" : ""}`, onclick: () => { state.kind = ""; refresh(); } }, t("All"), h("span", { class: "count" }, total)),
      Object.entries(KIND).filter(([k]) => data.counts[k]).map(([k, label]) =>
        h("button", { type: "button", class: `chip ${state.kind === k ? "on" : ""}`, onclick: () => { state.kind = k; refresh(); } }, icon(k), label, h("span", { class: "count" }, data.counts[k]))),
      data.cases || state.cases ? [h("span", { class: "spacer" }),
        h("button", { type: "button", class: `chip ${state.cases ? "on" : ""}`, "aria-pressed": String(!!state.cases),
          title: t("Fixes, gotchas and decisions told as cases: the scene, a question, then the answer"),
          onclick: () => { state.cases = state.cases ? "" : "1"; refresh(); } }, icon("reviews"), t("Case files"), h("span", { class: "count" }, fmtNum(data.cases))),
        mode === "cards" ? h("button", { type: "button", class: `chip ask-chip ${askFirst() ? "on" : ""}`, "aria-pressed": String(askFirst()),
          title: t("A case you haven't answered asks its question before it shows the lesson. Your answers stay in this browser."),
          onclick: () => { setAskFirst(!askFirst()); load(); } }, icon("completed"), t("Ask first")) : null] : null,
    ]);
  }
  const refresh = () => { setParams(state); load(); };
  let debounce;
  await load();
  return h("div", null,
    h("div", { class: "page-head" }, titled("book", h("h1", null, t("All knowledge")), count),
      h("div", { class: "head-actions" }, h("a", { class: "btn primary", href: `#/learn?${new URLSearchParams({ project: state.project })}` }, icon("learning"), t("Learn from your work")),
        viewToggle("knowledge", mode), h("a", { class: "btn", href: `#/project?path=${encodeURIComponent("__global__")}` }, t("Global playbook")))),
    h("div", { class: "filters" },
      h("input", { class: "input", type: "search", placeholder: t("Search knowledge…"), value: state.q, style: { minWidth: "280px" },
        oninput: (e) => { clearTimeout(debounce); debounce = setTimeout(() => { state.q = e.target.value; refresh(); }, 250); } }),
      h("select", { onchange: (e) => { state.project = e.target.value; refresh(); } }, await projectOptions(state.project)),
      h("select", { onchange: (e) => { state.source = e.target.value; refresh(); } },
        [["", t("All sources")], ["analysis", t("Extracted from sessions")], ["memory", t("Agent memory files")],
          ...(sources.team || state.source === "team" ? [["team", t("From teammates")]] : [])].map(([v, l]) => h("option", { value: v, selected: state.source === v }, l))),
      await whoFilter(state.who, (v) => { state.who = v; refresh(); })),
    chipsBox, box);
}

// =====================================================================================
// Projects
// =====================================================================================
route(/^\/projects$/, async () => {
  const projects = await loadProjects(true);
  const mode = viewMode("projects", "cards");
  const edit = canGroup();
  const href = projectHref;
  // under its hub project, a project leaves out the badge that names it
  const listView = (projects, { inHub = false } = {}) => h("section", { class: "card flush" }, localTable(projects, [
    { key: "label", label: t("Project"), value: (p) => p.label },
    { key: "sessions", label: t("Sessions"), num: true, desc: true, value: (p) => p.sessions },
    { key: "active", label: t("Active"), num: true, desc: true, value: (p) => p.active_s },
    { key: "knowledge", label: t("Knowledge"), num: true, desc: true, value: (p) => p.knowledge },
    { key: "cost", label: t("Est. cost"), num: true, desc: true, value: (p) => p.cost },
    { key: "last", label: t("Last session"), desc: true, value: (p) => p.last },
    { key: "kb", label: t("Knowledge base"), desc: true, value: (p) => p.kb_updated },
    ...(edit ? [{ key: "group", label: "" }] : []),
  ], (p) => h("tr", { class: href(p) ? "row-link" : "", onclick: (e) => { if (!e.target.closest("a, details") && href(p)) go(href(p)); } },
    h("td", { class: "title-cell" }, h("div", { class: "t" }, h("a", { href: href(p), class: "plain" }, p.label)),
      h("div", { class: "s", title: p.project_path }, shortPath(p.project_path), p.exists ? "" : t(" (not on disk)"), p.shared ? [" ", sharedBadge()] : null, p.hub && !inHub ? [" ", hubBadge(p)] : null)),
    h("td", { class: "num" }, fmtNum(p.sessions)),
    h("td", { class: "num" }, fmtDur(p.active_s)),
    h("td", { class: "num" }, fmtNum(p.knowledge)),
    h("td", { class: "num" }, fmtCost(p.cost)),
    h("td", { class: "nowrap" }, fmtDate(p.last)),
    h("td", { class: "nowrap muted" }, p.kb_updated ? t("updated {ago}", { ago: ago(p.kb_updated) }) : t("none yet")),
    edit ? h("td", { class: "pg-cell" }, groupMenu(p)) : null), { empty: t("No projects yet"), cls: "projects-table" }));
  const cards = (list, { inHub = false } = {}) => h("div", { class: "proj-cards" }, list.map((p) => {
    const card = projectCard(p, href(p), { hubBadge: !inHub });
    return edit ? h("div", { class: "pc-wrap" }, card, groupMenu(p)) : card;
  }));
  const body = mode === "list" ? listView : cards;
  const n = tn(projects.length, "{n} project", "{n} projects");
  const hubs = groupsHub ? byHubProject(projects) : []; // first, each over the projects here that go to it
  return h("div", null,
    h("div", { class: "page-head" }, titled("folder", h("h1", null, t("Projects")),
      h("div", { class: "sub" }, groupsCache.length ? t("{projects} in {groups}", { projects: n, groups: tn(groupsCache.length, "{n} group", "{n} groups") }) : n)),
      h("div", { class: "head-actions" }, viewToggle("projects", mode),
        edit ? h("button", { type: "button", class: "btn", onclick: () => groupDialog(null) }, t("New group")) : null,
        h("a", { class: "btn", href: `#/project?path=${encodeURIComponent("__global__")}` }, t("Global playbook")))),
    groupsCache.length || hubs.length ? [...hubs.map(([hp, ps]) => hubProjectSection(hp, ps, body)),
      ...byGroup(projects, { hub: true }).filter(([g, ps]) => g || ps.length).map(([g, ps]) => groupSection(g, ps, body))] : body(projects));
});

function weeklyBars(values) { // active time per week, oldest first; hover for the week
  const box = h("div", { class: "wbars", role: "img", "aria-label": t("Active time per week, last 12 weeks") });
  const max = Math.max(0, ...values);
  values.forEach((v, i) => {
    const start = new Date(Date.now() - ((values.length - 1 - i) * 7 + 6) * 86400000);
    const bar = h("i", { class: v ? "" : "zero", style: v ? { height: `${Math.max(10, (v / max) * 100)}%` } : null });
    hoverable(bar, () => [v ? t("{dur} active", { dur: fmtDur(v) }) : t("No sessions"), t("Week of {date}", { date: fmtDate(start.toISOString()) })], { focusable: false });
    box.append(bar);
  });
  return box;
}
function miniOutcomes(outs) { // thin stacked status bar + the top labels in words
  const entries = OUTCOME_ORDER.filter((o) => outs[o]).map((o) => [o, outs[o]]);
  const total = entries.reduce((a, [, n]) => a + n, 0);
  if (!total) return null;
  const cls = (o) => (OUTCOME[o] ? OUTCOME[o][0] || "neutral" : "none");
  const label = (o) => (OUTCOME[o] ? OUTCOME[o][1].toLowerCase() : t("not analyzed"));
  return h("div", { class: "mini-outcomes" },
    h("div", { class: "stackbar thin", "aria-hidden": "true" }, entries.map(([o, n]) => h("span", { class: `seg s-${cls(o)}`, style: { flexGrow: String(n) } }))),
    h("div", { class: "mo-text" }, entries.slice(0, 3).map(([o, n]) => h("span", { class: `s-${cls(o)}` }, icon(OUTCOME[o] ? o : "queued"), t("{n} {outcome}", { n, outcome: label(o) })))));
}
function projectCard(p, href, { hubBadge: withHub = true } = {}) {
  return h("a", { class: "card proj-card", href },
    h("div", { class: "pc-head" },
      h("div", { style: { minWidth: 0 } },
        h("div", { class: "pname" }, p.label, p.shared ? sharedBadge() : null, p.hub && withHub ? hubBadge(p) : null, Object.entries(p.agents || {}).filter(([a]) => a !== "claude").map(([a, n]) => agentTag(a, tn(n, "{n} {agent} session", "{n} {agent} sessions", { agent: agentName(a) })))),
        h("div", { class: "ppath", title: p.project_path }, shortPath(p.project_path), p.exists ? "" : ` · ${t("not on disk")}`)),
      p.kb_updated ? h("span", { class: "badge", title: t("Knowledge base updated {when}", { when: fmtDT(p.kb_updated) }) }, icon("knowledge"), "KB")
        : h("span", { class: "badge muted-badge", title: t("No knowledge base yet") }, t("no KB"))),
    weeklyBars(p.weekly || []),
    h("div", { class: "pstats" },
      h("span", { title: t("Sessions") }, icon("sessions"), h("b", null, fmtNum(p.sessions))),
      h("span", { title: t("Active time") }, icon("clock"), h("b", null, fmtDur(p.active_s))),
      h("span", { title: t("Knowledge items") }, icon("sparkles"), h("b", null, fmtNum(p.knowledge))),
      h("span", { title: t("Estimated API cost") }, icon("cost"), h("b", null, fmtCost(p.cost)))),
    miniOutcomes(p.outcomes || {}),
    h("div", { class: "pfoot" }, p.last ? t("Last session {ago}", { ago: ago(p.last) }) : t("Set up on the hub: no sessions yet"), h("span", { class: "muted" }, t("12 weeks"))));
}

// A knowledge base or the global playbook: TL;DR up top, then each section as a card of short titled bullets that
// open to their detail and sources; a filter for the long ones; the overview folded away
// section titles are in the knowledge language (synthesize.py suggests them in English and in Japanese)
const KB_TONES = [[/prefer|convention|claude|好み|規約|慣習/i, "preference", "accent"], [/gotcha|fix|pitfall|落とし穴|修正|注意/i, "gotcha", "serious"],
  [/pattern|command|reusable|run|test|deploy|パターン|コマンド|実行|テスト|デプロイ|再利用/i, "command", "good"], [/decision|rationale|決定|判断|理由/i, "decision", "accent"],
  [/learn|学び|気づき/i, "learning", "accent"], [/problem|recurring|open|friction|問題|課題|未解決|繰り返|つまずき/i, "todo", "warning"],
  [/architecture|fact|overview|構成|アーキテクチャ|事実|概要/i, "fact", "accent"]];
function kbView(p, isGlobal) {
  const box = h("div", { class: "kb" });
  if (!p.kb) {
    box.append(h("section", { class: "card empty" }, isGlobal ? t("No playbook yet. It is built automatically once enough cross-project knowledge is analyzed, or synthesize it now.")
      : t("No synthesized knowledge base yet. It is built automatically once a few sessions have been analyzed, or synthesize it now.")));
    return box;
  }
  const data = JSON.parse(p.kb.kb_json || "{}");
  const kIndex = Object.fromEntries((p.knowledge || []).map((k) => [k.id, k]));
  const sections = (data.sections || []).filter((sec) => sec.items && sec.items.length)
    .map((sec, i) => ({ ...sec, id: `kbs-${i}`, tone: KB_TONES.find(([re]) => re.test(sec.title)) || [null, "dot", "accent"] }));
  const bullets = sections.reduce((a, sec) => a + sec.items.length, 0);
  const cited = new Set(sections.flatMap((sec) => sec.items.flatMap((it) => it.sources || [])));
  const projects = new Set([...cited].map((id) => kIndex[id]?.project_name).filter(Boolean));
  const plain = (s) => String(s || "").replace(/`/g, "");

  const hero = h("section", { class: "card rv-hero kb-hero" },
    h("div", { class: "rv-kicker" }, icon(isGlobal ? "playbook" : "knowledge"), isGlobal ? t("Playbook") : t("Knowledge base"),
      h("span", null, t("updated {ago}", { ago: ago(p.kb.updated_at) }))),
    data.tldr && data.tldr.length ? h("ul", { class: "rv-tldr" }, data.tldr.map((x) => h("li", { class: "gloss", html: mdInline(escapeHtml(x)) })))
      : data.overview ? h("p", { class: "kb-lead" }, plainText(data.overview)) : null,
    h("div", { class: "hero-facts" },
      fact(tn(bullets, "entry", "entries"), fmtNum(bullets)), fact(tn(sections.length, "section", "sections"), fmtNum(sections.length)),
      fact(t("knowledge items read"), fmtNum(p.kb.n_items)),
      isGlobal && projects.size ? fact(t("projects it draws on"), fmtNum(projects.size)) : null));

  const filter = h("input", { class: "input", type: "search", placeholder: t("Filter {n} entries…", { n: fmtNum(bullets) }), "aria-label": t("Filter entries") });
  const nav = h("div", { class: "kb-nav" }, sections.map((sec) => h("button", { type: "button", class: `kb-chip t-${sec.tone[2]}`,
    onclick: () => document.getElementById(sec.id)?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" }) },
    icon(sec.tone[1]), sec.title, h("b", null, fmtNum(sec.items.length)))));
  const grid = h("div", { class: "kb-grid" });
  const sourceLinks = (it) => {
    const ks = (it.sources || []).map((id) => kIndex[id]).filter(Boolean).slice(0, 4);
    return ks.length ? h("div", { class: "kb-src" }, tx("From {sources}", { sources: ks.map((k, i) => [i ? " · " : "",
      k.session_id ? h("a", { href: `#/session/${k.session_id}` }, isGlobal && k.project_name ? `${k.project_name}: ${k.title}` : k.title) : k.title]) })) : null;
  };
  const trust = (it) => it.stage === "established" || it.stage === "canonical"
    ? [stageTag(it.stage, it.sessions || 0, tn(it.sessions || 1, "backed by {n} session", "backed by {n} sessions")), " "] : null;
  const render = (it) => [h("span", null, it.title ? [h("b", null, it.title), " ", trust(it)] : trust(it),
    h("span", { html: mdInline(escapeHtml(it.text || "")) })), sourceLinks(it)];
  const draw = () => {
    const q = filter.value.trim().toLowerCase();
    const shown = sections.map((sec) => ({ ...sec, items: q ? sec.items.filter((it) => `${it.title || ""} ${plain(it.text)}`.toLowerCase().includes(q)) : sec.items }))
      .filter((sec) => sec.items.length);
    grid.replaceChildren(...shown.map((sec) => h("section", { class: `card rv-list kb-sec t-${sec.tone[2]}`, id: sec.id },
      h("div", { class: "rv-list-head" }, icon(sec.tone[1]), h("h3", null, sec.title), h("span", null, fmtNum(sec.items.length))),
      clampList(sec.items, { limit: q ? Infinity : 5, render }))));
    if (!shown.length) grid.append(h("div", { class: "card empty" }, t("No entries match")));
  };
  let debounce;
  filter.addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(draw, 150); });
  draw();
  const full = data.overview && data.tldr && data.tldr.length ? h("details", { class: "card rv-full" }, h("summary", null, t("Read the overview")), mdEl(data.overview))
    : data.overview ? h("details", { class: "card rv-full" }, h("summary", null, t("Read the full overview")), mdEl(data.overview)) : null;
  append(box, [hero, !isGlobal && p.diagram && window.rough ? diagramCard(p) : null,
    h("div", { class: "kb-tools" }, nav, bullets > 10 ? filter : null), grid, full]);
  return box;
}

// A knowledge base's architecture sketch, hand-drawn with rough.js on the layout the server computed (the same layout
// the .excalidraw download uses). Every part and connection cites the knowledge items that state it.
const DIAGRAM_KINDS = { component: t("Code it owns"), interface: t("Way in (CLI, UI, API)"), store: t("Data it keeps"), external: t("External") };
const ROUGH_FILL = "#010203"; // marks rough.js's hachure strokes, so CSS can colour fills and outlines by theme
function roughEls(gen, drawable, cls = "") {
  return gen.toPaths(drawable).map((p) => s("path", { d: p.d, class: p.stroke === ROUGH_FILL ? "f" : `o ${cls}`.trim(), "stroke-width": p.strokeWidth }));
}
function roughSeed(str) {
  let x = 7;
  for (const c of str) x = (x * 31 + c.codePointAt(0)) >>> 0;
  return (x % 2147483646) + 1; // stable per part: the sketch keeps its wobble between renders, as Excalidraw does
}
function diagramCard(p) {
  const dg = p.diagram, L = dg.layout;
  const kIndex = Object.fromEntries((p.knowledge || []).map((k) => [k.id, k]));
  const byId = Object.fromEntries(dg.nodes.map((n) => [n.id, n]));
  const cited = new Set([...dg.nodes, ...dg.edges].flatMap((x) => x.sources || []));
  const detail = h("div", { class: "kbd-detail", "aria-live": "polite" });
  const sources = (ids) => {
    const ks = (ids || []).map((id) => kIndex[id]).filter(Boolean).slice(0, 6);
    return ks.length ? h("div", { class: "kbd-src" }, tx("From {sources}", { sources: ks.map((k, i) => [i ? " · " : "",
      k.session_id ? h("a", { href: `#/session/${k.session_id}` }, k.title) : k.title]) })) : null;
  };
  let picked = null; // ["node", id] or ["edge", index]: survives a redraw on resize

  const draw = (body) => {
    const gen = rough.generator();
    const svg = s("svg", { class: "kbd-svg", viewBox: `0 0 ${L.width} ${L.height}`, role: "img",
      "aria-label": t("Architecture sketch: {parts} parts, {edges} connections", { parts: dg.nodes.length, edges: dg.edges.length }) });
    Object.assign(svg.style, { maxWidth: `${L.width}px`, minWidth: `${Math.round(L.width * 0.72)}px` });
    const edgeG = s("g"), nodeG = s("g"), labelG = s("g");
    const els = { node: {}, edge: [], label: [] };
    dg.edges.forEach((e, i) => {
      const lay = L.edges[i], o = { seed: roughSeed(`${e.from}>${e.to}`), roughness: 0.9, bowing: 0.6, strokeWidth: 1.2, stroke: "#000" };
      const g = s("g", { class: "kbd-edge" }, s("path", { class: "hit", d: lay.d }),
        roughEls(gen, gen.path(lay.d, o)), roughEls(gen, gen.linearPath(lay.head, o)));
      g.addEventListener("click", (ev) => { ev.stopPropagation(); select(["edge", i]); });
      hoverable(g, () => [`${byId[e.from].label} → ${byId[e.to].label}`, e.label || null], { focusable: false });
      edgeG.append(g);
      els.edge.push(g);
      if (lay.label) {
        const lb = lay.label, n = lb.lines.length;
        const t = s("text", { class: "kbd-elabel", x: lb.x, y: lb.y, "text-anchor": "middle", style: { fontSize: `${L.label_font}px` } },
          lb.lines.map((ln, j) => s("tspan", { x: lb.x, y: lb.y + (j - (n - 1) / 2) * L.label_line, "dominant-baseline": "central" }, ln)));
        labelG.append(t);
        els.label[i] = t;
      }
    });
    dg.nodes.forEach((n, i) => {
      const b = L.nodes[i], o = { seed: roughSeed(n.id), roughness: 1.15, bowing: 0.9, strokeWidth: 1.4, stroke: "#000",
        fill: ROUGH_FILL, fillStyle: "hachure", hachureGap: 5.5, hachureAngle: -41, fillWeight: 0.9 };
      let shape;
      if (n.kind === "store") { // a cylinder: the body, then the front rim of its cap
        const ry = L.font * 0.45, rx = b.w / 2, { x, y, w } = b, bottom = b.y + b.h - ry;
        shape = [roughEls(gen, gen.path(`M${x},${y + ry} A${rx},${ry} 0 0 1 ${x + w},${y + ry} V${bottom} A${rx},${ry} 0 0 1 ${x},${bottom} Z`, o)),
          roughEls(gen, gen.path(`M${x},${y + ry} A${rx},${ry} 0 0 0 ${x + w},${y + ry}`, { ...o, fill: undefined }))];
      } else if (n.kind === "interface") {
        const r = 11, { x, y, w } = b, y2 = b.y + b.h, x2 = b.x + w;
        shape = roughEls(gen, gen.path(`M${x + r},${y}H${x2 - r}Q${x2},${y} ${x2},${y + r}V${y2 - r}Q${x2},${y2} ${x2 - r},${y2}` +
          `H${x + r}Q${x},${y2} ${x},${y2 - r}V${y + r}Q${x},${y} ${x + r},${y}Z`, o));
      } else {
        shape = roughEls(gen, gen.rectangle(b.x, b.y, b.w, b.h, n.kind === "external" ? { ...o, disableMultiStroke: true } : o), n.kind === "external" ? "dash" : "");
      }
      const cy = b.y + b.h / 2 + (n.kind === "store" ? L.font * 0.45 : 0), k = b.lines.length;
      const label = s("text", { class: "kbd-label", "text-anchor": "middle", style: { fontSize: `${L.font}px` } },
        b.lines.map((ln, j) => s("tspan", { x: b.x + b.w / 2, y: cy + (j - (k - 1) / 2) * L.line, "dominant-baseline": "central" }, ln)));
      const g = s("g", { class: `kbd-node k-${n.kind}`, role: "button", "aria-label": `${n.label}: ${DIAGRAM_KINDS[n.kind]}` }, shape, label);
      g.addEventListener("click", (ev) => { ev.stopPropagation(); select(["node", n.id]); });
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(["node", n.id]); } });
      hoverable(g, () => [n.label, DIAGRAM_KINDS[n.kind], n.note || null]);
      nodeG.append(g);
      els.node[n.id] = g;
    });
    svg.append(edgeG, nodeG, labelG);
    svg.addEventListener("click", () => select(null));

    function select(what) {
      picked = what && picked && what[0] === picked[0] && what[1] === picked[1] ? null : what; // a second click lets go
      svg.classList.toggle("picking", !!picked);
      [...Object.values(els.node), ...els.edge, ...els.label].forEach((e) => e && e.classList.remove("on"));
      if (!picked) {
        detail.replaceChildren(h("span", { class: "muted" }, t("Click a part or a connection to see the knowledge it comes from.")));
        return;
      }
      if (picked[0] === "node") {
        const n = byId[picked[1]];
        if (!n) return select(null);
        els.node[n.id].classList.add("on");
        dg.edges.forEach((e, i) => {
          if (e.from !== n.id && e.to !== n.id) return;
          [els.edge[i], els.label[i], els.node[e.from], els.node[e.to]].forEach((x) => x && x.classList.add("on"));
        });
        detail.replaceChildren(h("div", null, h("b", null, n.label), h("span", { class: "muted" }, ` · ${DIAGRAM_KINDS[n.kind]}`),
          n.note ? h("span", null, ` · ${n.note}`) : null), sources(n.sources));
      } else {
        const e = dg.edges[picked[1]];
        [els.edge[picked[1]], els.label[picked[1]], els.node[e.from], els.node[e.to]].forEach((x) => x && x.classList.add("on"));
        detail.replaceChildren(h("div", null, h("b", null, byId[e.from].label), ` → ${e.label ? `${e.label} → ` : ""}`, h("b", null, byId[e.to].label)),
          sources(e.sources));
      }
    }
    const kinds = Object.keys(DIAGRAM_KINDS).filter((k) => dg.nodes.some((n) => n.kind === k));
    body.append(h("div", { class: "kbd-scroll" }, svg),
      h("div", { class: "kbd-foot" }, h("div", { class: "kbd-legend" }, kinds.map((k) => h("span", { class: `k-${k}` }, h("i"), DIAGRAM_KINDS[k]))), detail));
    const keep = picked; // keep the selection across a redraw (select toggles, so start from none)
    picked = null;
    select(keep);
  };
  const table = () => ({ columns: [t("From"), t("Connection"), t("To"), t("Based on")],
    rows: dg.edges.map((e) => [byId[e.from].label, e.label || "→", byId[e.to].label,
      (e.sources || []).map((id) => kIndex[id]?.title).filter(Boolean).join(" · ") || tn((e.sources || []).length, "{n} item", "{n} items")]) });
  const download = h("a", { class: "btn small kbd-dl", href: `/api/diagram?path=${encodeURIComponent(p.project_path)}`, download: "",
    title: t("Download as an .excalidraw file to edit it in excalidraw.com or the Excalidraw VS Code extension") }, icon("download"), "Excalidraw");
  return chartCard(t("Architecture"), tn(cited.size, "Sketched from {n} knowledge item · click a part to see its sources", "Sketched from {n} knowledge items · click a part to see its sources", { n: fmtNum(cited.size) }),
    (body) => draw(body), table, [download], "system");
}

route(/^\/project$/, async (params) => {
  const path = params.path || "";
  const token = renderSeq;
  const p = await api("/api/project", { path });
  ART_LOCAL = p.local;
  const isGlobal = path === "__global__";
  setCrumbs(isGlobal ? [[t("Knowledge"), "#/knowledge"], [t("Global playbook")]] : [[t("Projects"), "#/projects"], ...(p.group ? [[p.group.name, "#/projects"]] : []), [p.label || shortPath(path)]], token);
  const synth = h("button", { class: "btn primary admin-only", type: "button", onclick: async () => {
    synth.disabled = true; synth.textContent = t("Synthesizing…");
    const r = await post("/api/synthesize", { path });
    toast(r.started ? t("Synthesizing the knowledge base with {agent}…", { agent: analyzer() }) : t("Already running"));
    watchJob(`synthesize:${path}`);
  } }, p.kb ? t("Re-synthesize") : t("Synthesize now"));
  const kbCard = kbView(p, isGlobal);
  if (isGlobal) {
    return h("div", null, h("div", { class: "page-head" }, titled("flag", h("h1", null, t("Global playbook")),
      h("div", { class: "sub" }, t("Cross-project learnings and your working preferences, distilled from every session"))), synth), kbCard);
  }
  const st = p.stats;
  const tiles = h("div", { class: "tiles" },
    tile(t("Sessions"), fmtNum(st.sessions), { iconName: "sessions" }), tile(t("Active time"), fmtDur(st.active_s), { iconName: "clock" }),
    tile(t("Prompts"), fmtNum(st.prompts), { iconName: "prompts" }), tile(t("Tokens"), fmtCompact(st.tokens), { iconName: "tokens" }),
    tile(t("Est. API cost"), fmtCost(st.cost), { iconName: "cost" }), tile(t("Lines"), `+${fmtCompact(st.lines_added)}`, { iconName: "diff", delta: `−${fmtCompact(st.lines_removed)}` }));
  const filesCard = h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, t("Most edited files"))),
    hbars(p.files, { label: (f) => shortPath(f.path, path), value: (f) => f.edits, fmt: fmtNum, sub: (f) => `· +${fmtCompact(f.added)}/−${fmtCompact(f.removed)}` }));
  const sessions = h("section", { class: "card", style: { padding: "4px 6px" } }, h("div", { class: "table-wrap" }, h("table", { class: "data sessions" },
    h("thead", null, h("tr", null, [t("Started"), t("Session"), t("Project"), t("Agent"), t("Prompts"), t("Tools"), t("Active"), t("Tokens"), t("Est. cost"), t("Outcome")].map((c, i) => h("th", { class: i >= 4 && i <= 8 ? "num" : "" }, c)))),
    h("tbody", null, p.sessions.map((x) => sessionRow(x))))));
  return h("div", null,
    h("div", { class: "page-head" }, titled("folder", h("div", { class: "muted", style: { fontSize: "12.5px" } }, h("a", { href: "#/projects" }, t("Projects")), " / "),
      h("h1", null, p.label, p.shared ? [" ", sharedBadge()] : null, p.hub ? [" ", hubBadge(p)] : null), h("div", { class: "sub mono", style: { fontSize: "12px" } }, path, ` · ${fmtDateY(st.first)} – ${fmtDateY(st.last)}`)),
      h("div", { class: "head-actions" }, h("a", { class: "btn", href: `#/systems?system=${encodeURIComponent(path)}` }, icon("systems"), t("System map")), synth)),
    tiles,
    h("div", { class: "section-gap" }, kbCard),
    p.artifacts?.total ? h("div", { class: "section-gap" }, artifactsCard(p.artifacts.recent, { project: false,
      hint: ART_KINDS.filter(([k]) => p.artifacts.counts[k]).map(([k, plural]) => `${plural} ${fmtNum(p.artifacts.counts[k])}`).join(" · "),
      all: `#/artifacts?project=${encodeURIComponent(path)}` })) : null,
    p.files && p.files.length ? h("div", { class: "section-gap" }, filesCard) : null,
    p.glossary && p.glossary.length ? [
      h("h2", { style: { margin: "22px 0 10px" } }, t("Glossary ({n})", { n: p.glossary.length }), " ",
        h("a", { class: "hint", style: { fontSize: "12.5px", fontWeight: 400 }, href: `#/glossary?project=${encodeURIComponent(path)}` }, t("open →"))),
      h("section", { class: "card" }, h("dl", { class: "kv", style: { marginTop: 0 } }, p.glossary.map((g) => [
        h("dt", null, h("a", { href: `#/glossary?term=${encodeURIComponent(g.term)}` }, g.term)),
        h("dd", null, g.definition, g.context ? h("div", { class: "muted" }, g.context) : null)])))] : null,
    h("h2", { class: "section-gap", style: { margin: "22px 0 10px" } }, t("Knowledge ({n})", { n: p.knowledge.length })),
    h("div", { class: "kgrid" }, p.knowledge.map((k) => knowledgeCard(k, { compact: true }))),
    h("h2", { style: { margin: "22px 0 10px" } }, t("Sessions ({n})", { n: p.sessions.length })), sessions);
});

// =====================================================================================
// Artifacts: what sessions made (documents, pages, diagrams, decks, images, published links, PRs, commits)
// =====================================================================================
const ART_KINDS = [["doc", t("Documents"), t("document")], ["page", t("Pages"), t("page")], ["diagram", t("Diagrams"), t("diagram")],
  ["deck", t("Decks"), t("deck")], ["sheet", t("Sheets"), t("sheet")], ["image", t("Images"), t("image")],
  ["published", t("Published"), t("published link")], ["pr", t("Pull requests"), t("pull request")], ["commit", t("Commits"), t("commit")]];
const ART_KIND = Object.fromEntries(ART_KINDS.map(([k, plural, one]) => [k, { plural, one }]));
let ART_LOCAL = null; // "mac" or "linux" when this browser is on the computer Interlatch runs on (files can open there)
const ART_STATUS = { present: [t("on disk"), "good", t("The file is on disk as the agent wrote it")],
  changed: [t("changed since"), "warning", t("The file is on disk, but changed after the agent wrote it")],
  gone: [t("gone"), "muted", t("The file is no longer on disk; the session's transcript still holds what was written")],
  chat: [t("in the chat"), "accent", t("Made in a claude.ai chat; it lives there, not on this machine")],
  elsewhere: [t("another machine"), "muted", t("Made on another of your machines")] };
function artifactWhere(a) {
  if (a.url) { try { const u = new URL(a.url); return u.host + u.pathname.replace(/\/$/, ""); } catch (e) { return a.url; } }
  if (a.path) return shortPath(a.path, a.project_path);
  return a.meta?.sha ? `${a.meta.branch ? a.meta.branch + " · " : ""}${a.meta.sha.slice(0, 7)}` : "";
}
function artifactSessionHref(a) {
  const q = a.seq != null ? `?seq=${a.seq}${a.agent_id ? `&agent=${encodeURIComponent(a.agent_id)}` : ""}` : "";
  return `#/session/${a.session_id}${q}`;
}
function artifactRow(a, { project = true, session = true, compact = false } = {}) {
  const st = ART_STATUS[a.status];
  const title = a.title || artifactWhere(a) || ART_KIND[a.kind]?.one || a.kind;
  const openHint = a.status === "gone" ? t("Gone from disk: open it as the agent wrote it") : t("Open the file in a new tab");
  const chat = artifactChatUrl(a);
  const main = a.url ? extLink(a.url, title)
    : a.openable ? h("a", { href: artifactOpenUrl(a), target: "_blank", rel: "noopener", title: openHint }, title)
    : chat ? h("a", { href: chat, target: "_blank", rel: "noopener", title: t("It lives in this claude.ai chat: open it there") }, title)
    : h("a", { href: artifactSessionHref(a), title: t("Open where the session made it") }, title);
  const open = compact ? null
    : a.openable ? h("a", { class: "btn small", href: artifactOpenUrl(a), target: "_blank", rel: "noopener", title: openHint }, t("Open"))
    : chat ? h("a", { class: "btn small", href: chat, target: "_blank", rel: "noopener", title: t("It lives in this claude.ai chat: open it there to download it") }, "claude.ai ↗")
    : null;
  const more = !compact ? artifactMenu(a, title) : null;
  const sub = [
    st ? h("span", { class: `ar-status t-${st[1]}`, title: st[2] }, st[0]) : null,
    artifactWhere(a) ? h("span", { class: "mono", title: a.url || a.path || "" }, artifactWhere(a)) : null,
    project && a.project_name ? h("a", { href: `#/project?path=${encodeURIComponent(a.project_path || "")}` }, a.project_name) : null,
    a.agent && a.agent !== "claude" ? agentTag(a.agent) : null,
    h("span", { title: fmtDT(a.ts) }, ago(a.ts)),
    a.versions > 1 ? h("span", null, tn(a.versions, "{n} version", "{n} versions")) : null,
    a.sessions > 1 ? h("span", null, tn(a.sessions, "{n} session", "{n} sessions")) : null,
  ].filter(Boolean);
  const lead = a.preview ? h("button", { class: "ar-thumb", type: "button", title: t("View"), "aria-label": t("View {title}", { title }), onclick: () => lightbox(a) },
    h("img", { src: artifactFileUrl(a), alt: "", loading: "lazy", decoding: "async" }))
    : h("span", { class: "ar-icon", title: ART_KIND[a.kind]?.one || a.kind }, icon(a.kind));
  return h("div", { class: `ar-row k-${a.kind}${a.status === "gone" ? " is-gone" : ""}${compact ? " compact" : ""}${a.preview ? " has-thumb" : ""}` },
    lead,
    h("div", { class: "ar-main" },
      h("div", { class: "ar-title" }, main),
      h("div", { class: "ar-sub" }, sub.flatMap((x, i) => (i && !(i === 1 && st) ? [h("i", { "aria-hidden": "true" }, "·"), x] : [x])))),
    h("div", { class: "ar-acts" }, open, more,
      session ? h("a", { class: "btn small", href: artifactSessionHref(a), title: a.session_title || t("The session that made it") }, t("Session")) : null));
}
function artifactFileUrl(a) { return `/api/artifacts/${a.id}/file`; }
function artifactOpenUrl(a) { return `/api/artifacts/${a.id}/open`; }
// A file made in a claude.ai chat stays there (the export leaves it out): the chat is where to get it
function artifactChatUrl(a) { return a.status === "chat" && a.agent === "claude-ai" && /^[\w-]+$/.test(a.session_id) ? `https://claude.ai/chat/${a.session_id}` : null; }
// More ways to reach a file: in its own app or in Finder (only on this computer, while it is on disk), or its path
function artifactMenu(a, title) {
  const onDisk = a.status === "present" || a.status === "changed";
  const items = [];
  const item = (label, hint, run) => items.push(h("button", { type: "button", role: "menuitem", onclick: () => { menu.open = false; run(); } },
    h("b", null, label), h("span", null, hint)));
  if (ART_LOCAL && onDisk) {
    item(ART_LOCAL === "mac" ? t("Open on this Mac") : t("Open on this computer"), t("In the app that opens this kind of file"), () => revealArtifact(a, "open"));
    item(ART_LOCAL === "mac" ? t("Show in Finder") : t("Show in its folder"), shortPath(a.path), () => revealArtifact(a, "reveal"));
  }
  if (a.path && a.status !== "chat") item(t("Copy the path"), shortPath(a.path), () => copyPath(a.path));
  if (!items.length) return null;
  const menu = h("details", { class: "menu ar-menu" },
    h("summary", { class: "btn small", title: t("More"), "aria-label": t("More for {title}", { title }) }, "⋯"),
    h("div", { class: "menu-list", role: "menu" }, items));
  return menu;
}
async function revealArtifact(a, how) {
  const r = await post(`/api/artifacts/${a.id}/reveal`, { how });
  if (r.error) toast(r.error, 6000);
  else if (how === "reveal") toast(ART_LOCAL === "mac" ? t("Shown in Finder") : t("Opened its folder"));
}
// A tile for the image and diagram views: the picture first (when it is still on disk), then what and where
function artifactTile(a) {
  const st = ART_STATUS[a.status];
  const title = a.title || artifactWhere(a) || ART_KIND[a.kind]?.one || a.kind;
  const media = a.preview
    ? h("button", { class: "ar-tile-media", type: "button", "aria-label": t("View {title}", { title }), onclick: () => lightbox(a) },
      h("img", { src: artifactFileUrl(a), alt: "", loading: "lazy", decoding: "async" }))
    : artifactChatUrl(a) ? h("a", { class: "ar-tile-media empty", href: artifactChatUrl(a), target: "_blank", rel: "noopener", title: t("Open the claude.ai chat it lives in") },
      icon(a.kind), h("span", null, t("In claude.ai ↗")))
    : h("div", { class: "ar-tile-media empty" }, icon(a.kind), h("span", null, a.status === "gone" ? t("No longer on disk") : st ? st[0] : ""));
  return h("div", { class: `ar-tile k-${a.kind}` }, media,
    h("div", { class: "ar-tile-body" },
      h("a", { class: "ar-tile-title", href: artifactSessionHref(a), title: a.session_title ? t("Made in: {title}", { title: a.session_title }) : title }, title),
      h("div", { class: "ar-sub" }, st ? h("span", { class: `ar-status t-${st[1]}`, title: st[2] }, st[0]) : null,
        a.project_name ? h("span", null, a.project_name) : null, h("span", { title: fmtDT(a.ts) }, ago(a.ts)))));
}
// Full size, over the page: Esc, a click outside or the close button puts it away
function lightbox(a) {
  closeLightbox();
  const url = artifactFileUrl(a);
  const close = h("button", { class: "icon-btn", type: "button", "aria-label": t("Close"), onclick: closeLightbox }, icon("x"));
  const box = h("div", { id: "lightbox", class: "lightbox", role: "dialog", "aria-modal": "true", "aria-label": a.title || t("Image"),
    onclick: (e) => { if (e.target === box) closeLightbox(); } },
    h("figure", null, h("img", { src: url, alt: a.title || "" }),
      h("figcaption", null, h("b", null, a.title || ""), h("span", { class: "mono", title: a.path || "" }, artifactWhere(a)),
        h("a", { href: artifactSessionHref(a), onclick: closeLightbox }, t("Session")),
        h("a", { href: artifactOpenUrl(a), target: "_blank", rel: "noopener" }, t("Open original")),
        ART_LOCAL ? h("button", { class: "link-btn", type: "button", onclick: () => revealArtifact(a, "open") }, ART_LOCAL === "mac" ? t("Open on this Mac") : t("Open on this computer")) : null,
        close)));
  lightbox.returnTo = document.activeElement;
  document.body.append(box);
  close.focus();
}
function closeLightbox() {
  const box = $("#lightbox");
  if (!box) return;
  box.remove();
  lightbox.returnTo?.focus?.();
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#lightbox")) { e.stopPropagation(); closeLightbox(); } }, true);
window.addEventListener("hashchange", closeLightbox);
async function copyPath(path) {
  try { await navigator.clipboard.writeText(path); toast(t("Path copied")); } catch (e) { toast(path, 6000); }
}
function artifactKindChips(counts, current, hrefFor) {
  const total = Object.values(counts || {}).reduce((x, y) => x + y, 0);
  return h("div", { class: "ar-chips", role: "group", "aria-label": t("Kind") },
    h("a", { class: `kb-chip${!current ? " on" : ""}`, href: hrefFor("") }, t("All"), h("b", null, fmtNum(total))),
    ART_KINDS.filter(([k]) => counts?.[k]).map(([k, plural]) =>
      h("a", { class: `kb-chip k-${k}${current === k ? " on" : ""}`, href: hrefFor(k) }, icon(k), plural, h("b", null, fmtNum(counts[k])))));
}

route(/^\/artifacts$/, async (params) => {
  const token = renderSeq;
  const filters = { kind: params.kind || "", project: params.project || "", q: params.q || "", hide_gone: params.hide_gone || "" };
  const data = await api("/api/artifacts", { ...filters, limit: 100 });
  ART_LOCAL = data.local;
  const hrefWith = (changes) => {
    const p = Object.fromEntries(Object.entries({ ...filters, ...changes }).filter(([, v]) => v));
    const qs = new URLSearchParams(p).toString();
    return `#/artifacts${qs ? "?" + qs : ""}`;
  };
  const projectName = data.projects.find((p) => p.project_path === filters.project)?.label;
  setCrumbs([[t("Artifacts"), "#/artifacts"], ...(projectName ? [[projectName]] : []), ...(filters.kind ? [[ART_KIND[filters.kind]?.plural || filters.kind]] : [])], token);
  const search = h("input", { class: "input", type: "search", value: filters.q, placeholder: t("Search titles, paths and links…"), "aria-label": t("Search artifacts") });
  let debounce;
  search.addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(() => go(hrefWith({ q: search.value.trim() })), 350); });
  const projectSel = h("select", { "aria-label": t("Project"), onchange: () => go(hrefWith({ project: projectSel.value })) },
    h("option", { value: "" }, t("All projects")),
    data.projects.map((p) => h("option", { value: p.project_path, selected: p.project_path === filters.project }, `${p.label || shortPath(p.project_path)} (${p.n})`)));
  const hide = h("label", { class: "ar-toggle" }, h("input", { type: "checkbox", checked: filters.hide_gone === "1",
    onchange: (e) => go(hrefWith({ hide_gone: e.target.checked ? "1" : "" })) }), t("Hide files that are gone"));
  const tiles = ["image", "diagram", "deck"].includes(filters.kind); // pictures (and first slides) read better as a grid
  const list = h("div", { class: tiles ? "ar-tiles" : "ar-list" });
  let shown = 0, lastGroup = null;
  const add = (items) => {
    for (const a of items) {
      const g = dayGroup(a.ts);
      if (g !== lastGroup) { list.append(h("div", { class: "ar-group" }, g)); lastGroup = g; }
      list.append(tiles ? artifactTile(a) : artifactRow(a));
    }
    shown += items.length;
  };
  add(data.items);
  const more = h("button", { class: "btn", type: "button", hidden: shown >= data.total, onclick: async () => {
    more.disabled = true;
    const next = await api("/api/artifacts", { ...filters, limit: 100, offset: shown });
    add(next.items);
    more.disabled = false;
    more.hidden = shown >= next.total;
  } }, t("Show more"));
  const empty = !data.total ? h("section", { class: "card empty" }, filters.q || filters.kind || filters.project
    ? t("Nothing matches these filters.") : t("No artifacts yet. They are recorded as sessions sync: files an agent creates, pages it publishes, PRs it opens and commits it makes.")) : null;
  return h("div", null,
    h("div", { class: "page-head" }, titled("container", h("h1", null, t("Artifacts")),
      h("div", { class: "sub" }, t("What your agents made, each linked to the session that made it, and whether it is still where they left it")))),
    artifactKindChips(data.counts, filters.kind, (k) => hrefWith({ kind: k })),
    h("div", { class: "ar-tools" }, search, projectSel, hide, h("span", { class: "muted" }, t("{n} shown", { n: fmtNum(data.total) }))),
    empty || h("section", { class: "card ar-card" }, list, h("div", { class: "ar-more" }, more)));
});
async function artifactsSidebar(box) {
  const data = await api("/api/artifacts", { limit: 0 });
  const total = Object.values(data.counts).reduce((x, y) => x + y, 0);
  box.replaceChildren(h("div", { class: "sb-head" }, h("h2", null, t("Artifacts")), h("span", null, fmtNum(total))),
    h("div", { class: "sb-scroll" },
      sbRow(t("Everything"), "#/artifacts", "artifacts", total, ["/artifacts", "kind", ""]),
      h("div", { class: "sb-group" }, t("Kinds")),
      ART_KINDS.filter(([k]) => data.counts[k]).map(([k, plural]) => sbRow(plural, `#/artifacts?kind=${k}`, k, data.counts[k], ["/artifacts", "kind", k])),
      data.projects.length ? h("div", { class: "sb-group" }, t("Projects")) : null,
      data.projects.slice(0, 30).map((p) => sbRow(p.label || shortPath(p.project_path), `#/artifacts?project=${encodeURIComponent(p.project_path)}`, "projects", p.n,
        ["/artifacts", "project", p.project_path]))));
}
function artifactsCard(items, { title = t("Artifacts"), hint, all, project = true, session = true, compact = false } = {}) {
  return h("section", { class: "card ar-card" },
    cardHead(title, { iconName: "artifacts", hint, tools: all ? h("a", { class: "hint", href: all }, t("All →")) : null }),
    h("div", { class: "ar-list" }, items.map((a) => artifactRow(a, { project, session, compact }))));
}

// =====================================================================================
// Search
// =====================================================================================
route(/^\/search$/, async (params) => {
  const q = params.q || "", sort = params.sort || "hits";
  const box = h("form", { class: "search-form", role: "search", onsubmit: (e) => { e.preventDefault(); const v = e.target.q.value.trim(); if (v) go(`#/search?q=${encodeURIComponent(v)}${sort !== "hits" ? "&sort=" + sort : ""}`); } },
    icon("search"), h("input", { class: "input", type: "search", name: "q", value: q, placeholder: t("Search all sessions"), "aria-label": t("Search all sessions"), autocomplete: "off" }));
  const head = (sub) => h("div", { class: "page-head" }, titled("magnifier", h("h1", null, t("Search all sessions")), h("div", { class: "sub" }, sub)));
  if (!q.trim()) return h("div", null, head(t("Every session that mentions a word or phrase, each mention highlighted and a click away. Matches any 3+ character substring, in any language.")), box);
  const data = await api("/api/search", { q, sort });
  const qp = encodeURIComponent(q); // carried into each session, which keeps the mentions marked
  const who = (m, agent) => m.kind === "prompt" ? t("You") : m.kind === "text" ? agentShort(agent) : m.kind === "thinking" ? t("Thinking")
    : m.tool_name ? m.tool_name.replace(/^mcp__/, "mcp:") : EVENT_KIND[m.kind] || m.kind.replace(/_/g, " ");
  const hitRow = (sx, m) => h("li", null, h("a", { class: "sr-hit", href: `#/session/${sx.session_id}?seq=${m.seq}${m.agent_id ? "&agent=" + m.agent_id : ""}&q=${qp}` },
    h("span", { class: `sr-who ${m.kind === "prompt" ? "you" : ""}` }, who(m, sx.agent), m.agent_id ? h("small", null, ` · ${t("subagent")}`) : null),
    h("span", { class: "sr-snip" }, snippetEl(m.text ?? m.snippet)), h("span", { class: "sr-time" }, fmtTime(m.ts))));
  const group = (sx) => {
    const hits = h("ol", { class: "sr-hits" }, sx.snippets.map((m) => hitRow(sx, m)));
    const rest = sx.hits - sx.snippets.length;
    const all = rest > 0 ? h("button", { class: "link-btn sr-all", type: "button", onclick: async () => {
      all.disabled = true; all.textContent = t("Loading…");
      const every = await api(`/api/sessions/${sx.session_id}/matches`, { q });
      hits.replaceChildren(...every.map((m) => hitRow(sx, m)));
      all.replaceWith(...(sx.hits > every.length ? [h("div", { class: "muted small sr-all" }, t("Showing the first {n}; open the session for the rest.", { n: fmtNum(every.length) }))] : []));
    } }, t("Show all {n} mentions", { n: fmtNum(sx.hits) })) : null;
    return h("article", { class: "sr-group" },
      h("div", { class: "sr-head" },
        h("a", { class: "sr-title", href: `#/session/${sx.session_id}?q=${qp}` }, sx.title || t("(untitled)")), agentTag(sx.agent),
        h("span", { class: "sr-count" }, sx.hits ? tn(sx.hits, "{n} mention", "{n} mentions", { n: fmtNum(sx.hits) }) : t("in summary"))),
      h("div", { class: "sr-meta" }, `${sx.project_name || "–"} · ${fmtDT(sx.started_at)}`),
      sx.summary ? h("div", { class: "sr-summary" }, snippetEl(sx.summary)) : null,
      sx.snippets.length ? hits : null, all);
  };
  const list = h("div", { class: "sr-list" }, data.sessions.map(group));
  let offset = data.sessions.length;
  const more = h("button", { class: "btn", type: "button", hidden: offset >= data.total, onclick: async () => {
    more.disabled = true;
    const next = await api("/api/search", { q, sort, offset });
    list.append(...next.sessions.map(group));
    offset += next.sessions.length;
    more.disabled = false; more.hidden = offset >= data.total;
  } }, t("More sessions"));
  const sortSel = h("select", { "aria-label": t("Order sessions"), onchange: (e) => go(`#/search?q=${qp}${e.target.value !== "hits" ? "&sort=" + e.target.value : ""}`) },
    [["hits", t("Most mentions")], ["newest", t("Newest first")], ["oldest", t("Oldest first")]].map(([v, l]) => h("option", { value: v, selected: v === sort }, l)));
  const knowledge = data.knowledge.length ? h("section", { class: "card section-gap", id: "search-knowledge" }, h("div", { class: "card-head" }, h("h2", null, t("Knowledge ({n})", { n: data.knowledge.length }))),
    h("div", { class: "grid" }, data.knowledge.map((k) => knowledgeCard(k, { compact: true })))) : null;
  const sub = data.total
    ? [`${tn(data.total, "{n} session", "{n} sessions", { n: fmtNum(data.total) })} · ${tn(data.mentions, "{n} mention", "{n} mentions", { n: fmtNum(data.mentions) })}`,
      knowledge ? [" · ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); knowledge.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth" }); } }, t("{n} knowledge items", { n: data.knowledge.length }))] : null]
    : knowledge ? t("No sessions mention “{q}”; {n} knowledge items do", { q, n: data.knowledge.length }) : t("No sessions mention “{q}”", { q });
  return h("div", null, head(sub),
    h("div", { class: "filters" }, box, data.total ? sortSel : null),
    data.total ? h("section", { class: "card flush" }, list) : null,
    h("div", { class: "load-more" }, more), knowledge);
});

// =====================================================================================
// Glossary: term linking everywhere + the A–Z page
// =====================================================================================
let glossaryTerms = null, glossaryRes = [], glossaryIndex = {}, glossaryLoaded = 0;
const escRe = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
async function loadGlossary(force) {
  if (glossaryTerms && !force && Date.now() - glossaryLoaded < 300000) return;
  if (limited()) glossaryTerms = []; // the glossary spans every project of this hub
  else try { glossaryTerms = await api("/api/glossary/terms"); } catch (e) { glossaryTerms = []; }
  glossaryLoaded = Date.now();
  glossaryIndex = {};
  const insensitive = [], sensitive = [];
  const cjk = /[぀-ヿ㐀-鿿가-힯]/;
  for (const t of glossaryTerms) {
    for (const name of [t.term, ...(t.aliases || [])]) {
      if (!name || name.length < 3) continue;
      if (/^[a-z]+$/.test(name)) continue; // plain lowercase words ("derived", "node") would underline ordinary prose
      if (name !== t.term) { // an alias that is just a broader word of the term ("Azure" for "Azure OpenAI") misleads
        const inside = new RegExp(`(^|[^\\p{L}\\p{N}])${escRe(name.toLowerCase())}([^\\p{L}\\p{N}]|$)`, "u");
        if (name.length < t.term.length && inside.test(t.term.toLowerCase())) continue;
      }
      if (glossaryIndex[name]) continue;
      glossaryIndex[name] = t;
      glossaryIndex[name.toLowerCase()] ||= t;
      const isCjk = cjk.test(name);
      const pattern = isCjk ? escRe(name) : `(?<![\\p{L}\\p{N}_])${escRe(name)}(?![\\p{L}\\p{N}_])`;
      // single words match as written (so "Storage" never underlines "storage"); phrases and identifiers ignore case
      (!isCjk && /^[\p{L}\p{N}]+$/u.test(name) ? sensitive : insensitive).push([name.length, pattern]);
    }
  }
  const build = (alts, flags) => {
    alts.sort((a, b) => b[0] - a[0]);
    return alts.length ? new RegExp(alts.map((a) => a[1]).join("|"), flags) : null;
  };
  glossaryRes = [build(insensitive, "giu"), build(sensitive, "gu")].filter(Boolean);
}
function glossify(root, max = 12) {
  if (!glossaryRes.length || !root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement.closest("code, pre, a, button, select, input, textarea, .gterm, .who, .kfoot, .tag")
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  const seen = new Set();
  let count = 0;
  for (const node of nodes) {
    if (count >= max) break;
    const text = node.nodeValue;
    const found = [];
    for (const re of glossaryRes) {
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(text))) found.push({ index: m.index, text: m[0] });
    }
    if (!found.length) continue;
    found.sort((a, b) => a.index - b.index || b.text.length - a.text.length);
    let last = 0, frag = null;
    for (const f of found) {
      if (count >= max || f.index < last) continue; // overlaps an earlier, longer match
      const entry = glossaryIndex[f.text] || glossaryIndex[f.text.toLowerCase()];
      if (!entry || seen.has(entry.term)) continue;
      seen.add(entry.term);
      count++;
      frag ||= document.createDocumentFragment();
      frag.append(text.slice(last, f.index));
      const span = h("span", { class: "gterm", role: "link", onclick: (e) => { e.stopPropagation(); go(`#/glossary?term=${encodeURIComponent(entry.term)}`); } }, f.text);
      hoverable(span, () => [entry.term, entry.definition, catLabel(entry.category)]);
      frag.append(span);
      last = f.index + f.text.length;
    }
    if (frag) { frag.append(text.slice(last)); node.replaceWith(frag); }
  }
}

route(/^\/glossary$/, async (params) => {
  const state = { q: params.q || "", project: params.project || "", category: params.category || "" };
  const data = await api("/api/glossary", state);
  const refresh = () => { setParams({ ...state }); render(); };
  const rebuild = h("button", { class: "btn admin-only", type: "button", onclick: async () => {
    rebuild.disabled = true;
    const r = await post("/api/glossary/rebuild", { path: state.project || "" });
    toast(r.started ? t("{agent} is rebuilding the glossary…", { agent: analyzerShort() }) : t("Already running"));
    watchJob(`glossary:${state.project || "all"}`);
  } }, state.project ? t("Rebuild this project's glossary") : t("Rebuild glossary"));
  let debounce;
  const mode = viewMode("glossary", "cards");
  const letterOf = (e) => (e.term[0] && /\p{L}/u.test(e.term[0]) ? e.term[0].toUpperCase() : "#");
  const groups = {};
  for (const e of data.items) (groups[letterOf(e)] ||= []).push(e);
  const letters = Object.keys(groups).sort((a, b) => (a === "#") - (b === "#") || a.localeCompare(b));
  const usage = (e) => e.usage.some((u) => u.context) ? h("ul", { class: "gusage" }, e.usage.filter((u) => u.context).map((u) =>
    h("li", null, u.project_path === "__global__" ? h("b", null, t("Everywhere")) : h("a", { href: `#/project?path=${encodeURIComponent(u.project_path)}` }, h("b", null, String(u.project_name || "").startsWith("/") ? shortPath(u.project_name) : u.project_name)), t(": "), u.context))) : null;
  const related = (e) => e.related.length ? h("div", { style: { marginTop: "8px" } }, e.related.map((r) =>
    h("a", { class: "tag", href: `#/glossary?term=${encodeURIComponent(r)}` }, r))) : null;
  const foot = (e) => h("div", { class: "gfoot" },
    e.n_sessions ? h("span", null, `${tn(e.n_sessions, "{n} session", "{n} sessions")} · ${tn(e.n_mentions, "{n} mention", "{n} mentions", { n: fmtCompact(e.n_mentions) })} · ${fmtDate(e.first_seen)} → ${fmtDate(e.last_seen)}`)
      : h("span", null, t("not mentioned verbatim in transcripts")),
    e.top_sessions.slice(0, 3).map((ts) => h("a", { href: `#/session/${ts.id}` }, (ts.title || ts.id.slice(0, 8)).slice(0, 48))),
    e.n_sessions ? h("a", { href: `#/search?q=${encodeURIComponent(e.term)}` }, t("all mentions →")) : null,
    h("a", { href: `#/map?term=${encodeURIComponent(e.term)}${MAP_HIDDEN.has(e.category) ? "&all=1" : ""}` }, t("on the map →")));
  const card = (e) => h("article", { class: "card gcard", id: `g-${slugId(e.term)}` },
    h("div", { class: "ghead" },
      h("div", { class: "gname" }, h("div", { class: "gterm-title" }, e.term), e.aliases.length ? h("div", { class: "galiases" }, t("also {names}", { names: e.aliases.join(t(", ")) })) : null),
      catBadge(e.category)),
    h("div", { class: "gdef" }, e.definition || ""), usage(e), related(e), foot(e));
  const listView = () => h("section", { class: "card flush" }, localTable(data.items, [
    { key: "term", label: t("Term"), value: (e) => (letterOf(e) === "#" ? "￿" : "") + e.term.toLowerCase() }, // symbols last, as in the A–Z bar
    { key: "category", label: t("Category"), value: (e) => catLabel(e.category) },
    { key: "definition", label: t("Definition") },
    { key: "sessions", label: t("Sessions"), num: true, desc: true, value: (e) => e.n_sessions || 0 },
    { key: "mentions", label: t("Mentions"), num: true, desc: true, value: (e) => e.n_mentions || 0 },
    { key: "last", label: t("Last seen"), desc: true, value: (e) => e.last_seen },
  ], (e) => expandable(h("tr", { class: "grow", id: `g-${slugId(e.term)}` },
    h("td", { class: "term-cell" }, h("div", { class: "gterm-title" }, e.term), e.aliases.length ? h("div", { class: "s" }, t("also {names}", { names: e.aliases.join(t(", ")) })) : null),
    h("td", null, catBadge(e.category)),
    h("td", { class: "def-cell" }, e.definition || ""),
    h("td", { class: "num" }, e.n_sessions ? fmtNum(e.n_sessions) : "–"),
    h("td", { class: "num" }, e.n_mentions ? fmtCompact(e.n_mentions) : "–"),
    h("td", { class: "nowrap" }, e.last_seen ? fmtDate(e.last_seen) : "–")), 6, () => [usage(e), related(e), foot(e)]),
  { sort: "term", group: { key: "term", of: letterOf, id: (l) => `gl-${l}` }, empty: t("No terms match."), cls: "glossary-table" }));
  const page = h("div", null,
    h("div", { class: "page-head" }, titled("dex", h("h1", null, t("Glossary")),
      h("div", { class: "sub" }, data.total ? t("{a} of {b} terms from your sessions. Hover underlined terms anywhere in Interlatch.", { a: fmtNum(data.items.length), b: fmtNum(data.total) }) : t("No glossary yet"))),
      h("div", { class: "head-actions" }, viewToggle("glossary", mode), rebuild)),
    h("div", { class: "filters" },
      h("input", { class: "input", type: "search", placeholder: t("Search terms and definitions…"), value: state.q, style: { minWidth: "280px" },
        oninput: (e) => { clearTimeout(debounce); debounce = setTimeout(() => { state.q = e.target.value; refresh(); }, 300); } }),
      h("select", { onchange: (e) => { state.project = e.target.value; refresh(); } }, await projectOptions(state.project))),
    h("div", { class: "filters" },
      h("button", { type: "button", class: `chip ${!state.category ? "on" : ""}`, onclick: () => { state.category = ""; refresh(); } }, t("All"), h("span", { class: "count" }, data.total)),
      Object.entries(data.counts).sort((a, b) => b[1] - a[1]).map(([c, n]) =>
        h("button", { type: "button", class: `chip ${state.category === c ? "on" : ""}`, onclick: () => { state.category = c; refresh(); } }, icon(ICONS[c] ? c : "dot"), catLabel(c), h("span", { class: "count" }, n)))),
    letters.length > 3 ? h("div", { class: "letters" }, letters.map((l) => h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); document.getElementById(`gl-${l}`)?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth" }); } }, l))) : null,
    !data.items.length ? h("div", { class: "card empty" }, data.total ? t("No terms match.") : t("The glossary is built from your knowledge items. Click Rebuild glossary to create it now."))
      : mode === "list" ? listView()
      : letters.map((l) => [h("h2", { class: "gletter", id: `gl-${l}` }, l), h("div", { class: "ggrid" }, groups[l].map(card))]));
  if (params.term) {
    setTimeout(() => {
      const target = document.getElementById(`g-${slugId(params.term)}`) ||
        [...document.querySelectorAll(".gcard, .grow")].find((c) => c.querySelector(".gterm-title").textContent.toLowerCase() === params.term.toLowerCase());
      if (!target) return;
      target.classList.add("focus");
      if (target.classList.contains("grow")) target.click(); // open its details
      target.scrollIntoView({ block: "center" });
    }, 50);
  }
  return page;
});
function slugId(t) { return String(t).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-"); }

// =====================================================================================
// Map: the glossary as a collapsible mindmap, grouped by any stack of levels (project, category, theme, agent)
// =====================================================================================
// Branch colour follows the category (fixed slots, never by rank); smaller categories share a neutral, and
// project/agent levels are neutral. Every node is labelled, so colour is never the only cue; the Glossary list is
// the table view. Terms open into the knowledge items they were distilled from and the sessions that mention them.
const MAP_HUES = { concept: 1, component: 2, tool: 3, data: 4, domain: 5, library: 6, system: 7, platform: 8 };
const MAP_HIDDEN = new Set(["file", "command"]); // hundreds of paths and commands would drown the rest
const MAP_DIMS = ["category", "theme", "project", "agent"];
const MAP_DIM_NAMES = { project: t("Project"), category: t("Category"), theme: t("Theme"), agent: t("Agent") };
const MAP_PLURAL = { root: t("branches"), project: t("projects"), category: t("categories"), theme: t("themes"), agent: t("agents"), term: t("terms"), kitem: t("items"), session: t("sessions") };
const MAP_PRESETS = [["category", "theme"], ["project", "category", "theme"], ["category", "project"], ["agent", "category", "theme"]];
const MAP_CAP = { root: 12, project: 10, category: 10, theme: 10, agent: 10, term: 10 }; // the rest is browsed in the side panel
const MAP_BOX = { root: 36, project: 30, agent: 30, category: 28, theme: 26, term: 22, kitem: 22, session: 22, more: 24 }; // node heights
const MAP_PITCH = { root: 46, project: 40, agent: 40, category: 38, theme: 34, term: 26, kitem: 26, session: 26, more: 32 }; // per leaf row
const MAP_GAP = 52, MAP_GROUP_GAP = 10, MAP_KNOB = 8, MAP_SEP = "\u0001";
const mapColor = (cat) => (MAP_HUES[cat] ? `var(--series-${MAP_HUES[cat]})` : "var(--muted)");
const mapDot = (t) => (t.n_sessions >= 5 ? 5 : t.n_sessions >= 2 ? 4 : 3); // dot radius: how much it was discussed
const mapNeutral = (kind) => kind === "project" || kind === "agent" || kind === "root";
const mapState = { open: new Set(["root"]), pinned: new Set(), only: null, view: null }; // survives re-renders in this tab
const reducedMotion = () => document.documentElement.dataset.motion === "off"; // Appearance › Animations, or the system setting (boot.js)

function mapLevels(params, data) {
  if (params.levels != null) {
    const picked = [...new Set(String(params.levels).split(",").filter((d) => MAP_DIMS.includes(d)))];
    if (picked.length) return picked;
  }
  if (params.by === "project") return ["project", "category", "theme"]; // links from before levels existed
  return Object.keys(data.themes || {}).length ? ["category", "theme"] : ["category"];
}

function mapTree(data, levels, withHidden, projectName) {
  const terms = data.terms.filter((t) => withHidden || !MAP_HIDDEN.has(t.category))
    .sort((a, b) => b.n_sessions - a.n_sessions || a.term.localeCompare(b.term));
  const DIMS = {
    project: { values: (t) => t.projects.map((u) => u.path), label: projectName },
    category: { values: (t) => [t.category], label: (v) => catLabel(v), cat: (v) => v },
    theme: { values: (t) => [t.theme ? `${t.category}${MAP_SEP}${t.theme}` : null], label: (v) => v.split(MAP_SEP)[1], cat: (v) => v.split(MAP_SEP)[0] },
    agent: { values: (t) => (t.agents.length ? t.agents : ["unknown"]), label: (v) => (v === "unknown" ? t("No linked sessions") : agentName(v)) },
  };
  const termNode = (t, prefix) => {
    const id = `${prefix}/t:${t.id}`;
    const ks = t.knowledge.map((k) => data.knowledge[k]).filter(Boolean);
    return { id, kind: "term", label: t.term, cat: t.category, term: t, children: [
      ...ks.map((k) => ({ id: `${id}/k:${k.id}`, kind: "kitem", label: k.title, cat: t.category, k, children: [] })),
      ...t.sessions.map((s) => ({ id: `${id}/s:${s.id}`, kind: "session", label: s.title || s.id.slice(0, 8), cat: t.category, session: s, children: [] })),
    ] };
  };
  function build(list, dims, prefix, cat) {
    if (!dims.length) return list.map((t) => termNode(t, prefix));
    const [dim, ...rest] = dims, D = DIMS[dim];
    const buckets = new Map();
    for (const t of list) for (const v of new Set(D.values(t))) { if (!buckets.has(v)) buckets.set(v, []); buckets.get(v).push(t); }
    if (buckets.size === 1 && buckets.has(null)) return build(list, rest, prefix, cat); // nothing to group by here
    return [...buckets]
      .sort(([va, a], [vb, b]) => (va === null) - (vb === null) || b.length - a.length || String(va).localeCompare(String(vb)))
      .map(([v, ts]) => {
        const id = `${prefix}/${dim}:${v}`;
        const own = v !== null && D.cat ? D.cat(v) : cat;
        return { id, kind: dim, value: v, label: v === null ? t("Not grouped yet") : D.label(v), cat: mapNeutral(dim) ? undefined : own,
          count: ts.length, terms: ts, children: build(ts, rest, id, own) };
      });
  }
  const root = { id: "root", kind: "root", label: t("Your work"), count: terms.length, terms, children: build(terms, levels, "root", undefined) };
  (function link(n, parent) { n.parent = parent; n.children.forEach((c) => link(c, n)); })(root, null);
  return root;
}

function mapKids(n) {
  if (!mapState.open.has(n.id) || !n.children.length) return [];
  const cap = MAP_CAP[n.kind];
  if (mapState.only?.has(n.id)) { // a search is on: this branch shows just the way to its matches
    const shown = n.children.filter((c) => mapState.pinned.has(c.id)), rest = n.children.length - shown.length;
    return rest ? [...shown, { id: `${n.id}/+more`, kind: "more", label: t("+{n} more", { n: rest }), cat: n.cat, of: n, parent: n, children: [] }] : shown;
  }
  if (!cap || n.children.length <= cap + 1) return n.children;
  // the most discussed first (a term: its first knowledge items and sessions), plus anything picked or searched for
  const quota = n.kind === "term" ? { kitem: 6, session: 4 } : null, used = { kitem: 0, session: 0 };
  const shown = n.children.filter((c, i) => mapState.pinned.has(c.id) || (quota ? used[c.kind]++ < quota[c.kind] : i < cap));
  const rest = n.children.length - shown.length;
  return rest ? [...shown, { id: `${n.id}/+more`, kind: "more", label: t("+{n} more", { n: rest }), cat: n.cat, of: n, parent: n, children: [] }] : shown;
}

let mapCtx;
function mapMeasure(n) { // node width; also sets the label text shown
  mapCtx ||= document.createElement("canvas").getContext("2d");
  const font = getComputedStyle(document.body).fontFamily;
  const text = (t, size, weight = 400) => { mapCtx.font = `${weight} ${size}px ${font}`; return mapCtx.measureText(t).width; };
  const max = n.kind === "kitem" || n.kind === "session" ? 58 : 44;
  n.text = n.label.length > max ? n.label.slice(0, max - 2) + "…" : n.label;
  n.countText = n.kind === "session" ? fmtDate(n.session.started_at) : n.count != null && n.kind !== "term" ? fmtNum(n.count) : "";
  const count = n.countText ? text(n.countText, 12) + 7 : 0;
  const w = n.kind === "root" ? 18 + text(n.text, 15, 600) + count + 18
    : n.kind === "project" || n.kind === "agent" ? 12 + text(n.text, 13.5, 600) + count + 14
    : n.kind === "category" ? 31 + text(n.text, 13, 500) + count + 14
    : n.kind === "theme" ? 22 + text(n.text, 13, 500) + count + 13
    : n.kind === "more" ? 11 + text(n.text, 12.5) + 11
    : n.kind === "kitem" || n.kind === "session" ? 22 + text(n.text, 12.5) + count + 6
    : 17 + text(n.text, 13, n.term.n_sessions >= 5 ? 500 : 400) + 7;
  return Math.ceil(w + (n.children.length ? MAP_KNOB : 0)); // the +/− knob sits on the right edge: keep it clear of the count
}

function mapLayout(root) {
  let y = 0, lastParent = null;
  const nodes = [], links = [];
  (function walk(n, x) {
    n.x = x;
    n.w = mapMeasure(n);
    const kids = mapKids(n);
    if (!kids.length) {
      if (lastParent && n.parent !== lastParent) y += MAP_GROUP_GAP;
      lastParent = n.parent;
      n.y = y + MAP_PITCH[n.kind] / 2;
      y += MAP_PITCH[n.kind];
    } else {
      kids.forEach((k) => { k.parent = n; walk(k, x + n.w + MAP_GAP); links.push([n, k]); });
      n.y = (kids[0].y + kids[kids.length - 1].y) / 2;
    }
    nodes.push(n);
  })(root, 0);
  return { nodes, links };
}

function mapLinkPath(p, c) { // a rounded bracket: out of the parent's knob, down a shared spine, into the child
  const x1 = p.x + p.w + MAP_KNOB, xs = c.x - 22, dy = c.y - p.y;
  if (Math.abs(dy) < 0.5) return `M${x1},${p.y}H${c.x}`;
  const sg = Math.sign(dy), r = Math.min(10, Math.abs(dy) / 2);
  return `M${x1},${p.y}H${xs - r}Q${xs},${p.y} ${xs},${p.y + sg * r}V${c.y - sg * r}Q${xs},${c.y} ${xs + r},${c.y}H${c.x}`;
}

function mapFindTerm(terms, q) {
  const s = q.trim().toLowerCase();
  if (!s) return null;
  const names = (t) => [t.term, ...t.aliases].map((x) => x.toLowerCase());
  return terms.find((t) => names(t).includes(s)) || terms.find((t) => names(t).some((x) => x.startsWith(s)))
    || terms.find((t) => names(t).some((x) => x.includes(s)));
}

function mapMatch(terms, q) { // every term with all the words in its name or aliases, then in its definition
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const has = (text) => words.every((w) => text.includes(w));
  const names = (t) => [t.term, ...t.aliases].join("\n").toLowerCase();
  const s = words.join(" ");
  const rank = (t) => (t.term.toLowerCase() === s ? 0 : t.term.toLowerCase().startsWith(s) ? 1 : 2);
  const named = terms.filter((t) => has(names(t))).sort((a, b) => rank(a) - rank(b)); // stable: most discussed first
  const described = terms.filter((t) => !has(names(t)) && has((t.definition || "").toLowerCase()));
  return { named, described };
}
// a knowledge item or session under a term: the same one can sit under several terms, so it is matched by what it is
const mapLeafKey = (n) => (n.kind === "kitem" ? `k:${n.k.id}` : n.kind === "session" ? `s:${n.session.id}` : null);
const mapLeafText = (n) => (n.kind === "kitem" ? `${n.k.title}\n${n.k.body || ""}` : n.session.title || "").toLowerCase();

const MAP_TOOL_ICONS = {
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  collapse: "M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5",
};

route(/^\/map$/, async (params) => {
  const withHidden = params.all === "1";
  const data = await api("/api/map");
  const levels = mapLevels(params, data);
  const projectsByPath = Object.fromEntries(data.projects.map((p) => [p.path, p]));
  const projectName = (path) => (path === "__global__" ? t("Everywhere") : projectsByPath[path]?.label || shortPath(path));
  const tree = mapTree(data, levels, withHidden, projectName);
  const shown = tree.terms;
  const realProjects = (tm) => new Set(tm.projects.map((u) => u.path).filter((p) => p !== "__global__")).size;
  const themeOf = (cat, name) => (data.themes[cat] || []).find((tm) => tm.name === name);
  let query = "", hits = null; // hits: what the search box found, drawn highlighted and listed in the side panel
  mapState.only = null;
  const urlState = (extra = {}) => ({ levels: levels.join(","), all: withHidden ? "1" : "", q: query, ...extra });
  const refresh = (changes) => { setParams(urlState(changes)); render(); };

  const svg = s("svg", { class: "mm-svg", role: "tree", "aria-label": t("Mindmap of glossary terms") });
  const viewG = s("g"), linkG = s("g", { class: "mm-links" }), nodeG = s("g", { class: "mm-nodes" });
  viewG.append(linkG, nodeG);
  svg.append(viewG);
  const box = h("div", { class: "mm-canvas" }, svg);
  const aside = h("aside", { class: "mm-detail" });
  let layout = null, selected = null, seen = null;
  const viewKey = `${levels.join(",")}|${withHidden}`;
  const view = mapState.view && mapState.view.key === viewKey ? mapState.view : (mapState.view = { key: viewKey, k: 1, tx: 32, ty: 0, placed: false });
  const applyView = () => {
    viewG.setAttribute("transform", `translate(${view.tx},${view.ty}) scale(${view.k})`);
    box.style.backgroundSize = `${22 * view.k}px ${22 * view.k}px`; // the dot grid moves with the map
    box.style.backgroundPosition = `${view.tx}px ${view.ty}px`;
  };

  // ------------------------------------------------------------------ drawing
  function draw() {
    layout = mapLayout(tree);
    const fresh = (n) => seen && !seen.has(n.id);
    linkG.replaceChildren(...layout.links.map(([p, c]) => {
      const leaf = c.kind === "kitem" || c.kind === "session";
      const path = s("path", { d: mapLinkPath(p, c), class: `${mapNeutral(c.kind) ? "neutral" : leaf ? "leaf" : ""}${fresh(c) ? " enter" : ""}` });
      if (c.cat && !mapNeutral(c.kind)) path.style.setProperty("--c", mapColor(c.cat));
      return path;
    }));
    nodeG.replaceChildren(...layout.nodes.map((n) => nodeEl(n, fresh(n))));
    seen = new Set(layout.nodes.map((n) => n.id));
    applyView();
  }

  function nodeEl(n, isNew) {
    const hasKids = n.children.length > 0, open = mapState.open.has(n.id) && hasKids;
    const hgt = MAP_BOX[n.kind];
    const hit = hits && (n.kind === "term" ? hits.ids.has(n.term.id) : mapLeafKey(n) ? hits.leafKeys.has(mapLeafKey(n)) : hits.groups.includes(n));
    const g = s("g", { class: `mm-node ${n.kind}${n.value === null ? " ungrouped" : ""}${hit ? " hit" : ""}${selected === n.id ? " sel" : ""}${isNew ? " enter" : ""}`,
      transform: `translate(${n.x},${n.y})`, role: "treeitem", tabindex: "0", "aria-label": n.label,
      "aria-expanded": hasKids ? String(open) : null, "aria-selected": selected === n.id ? "true" : null });
    if (n.cat) g.style.setProperty("--c", mapColor(n.cat));
    const flat = ["term", "kitem", "session"].includes(n.kind);
    const rx = n.kind === "root" ? hgt / 2 : flat ? 6 : 8;
    g.append(s("rect", { class: "mm-box", x: flat ? -6 : 0, y: -hgt / 2, width: n.w + (flat ? 8 : 0), height: hgt, rx }));
    const base = n.kind === "root" ? 5.5 : 4.5;
    const glyph = (name, x, size) => {
      const ic = icon(ICONS[name] ? name : "dot", "mm-icon");
      setAttrs(ic, { x, y: -size / 2, width: size, height: size });
      return ic;
    };
    if (n.kind === "category") g.append(glyph(n.cat, 10, 14));
    if (n.kind === "theme") g.append(s("circle", { class: "mm-tdot", cx: 11, cy: 0, r: 4 }));
    if (n.kind === "term") g.append(s("circle", { class: "mm-dot", cx: 6, cy: 0, r: mapDot(n.term) }));
    if (n.kind === "kitem") g.append(glyph(KIND[n.k.kind] ? n.k.kind : "dot", 0, 14));
    if (n.kind === "session") g.append(glyph("sessions", 0, 14));
    const tx = { root: 18, project: 12, agent: 12, category: 31, theme: 22, more: 11, term: 17, kitem: 20, session: 20 }[n.kind];
    g.append(s("text", { x: tx, y: base, class: n.kind === "term" && n.term.n_sessions >= 5 ? "strong" : "" },
      n.text, n.countText ? s("tspan", { class: "mm-count", dx: 7 }, n.countText) : null));
    if (hasKids) {
      g.append(s("g", { class: "mm-knob", transform: `translate(${n.w},0)` },
        s("circle", { r: MAP_KNOB }), s("path", { d: open ? "M-3.5,0H3.5" : "M-3.5,0H3.5M0,-3.5V3.5" })));
    }
    g.append(s("rect", { class: "mm-hit", x: -6, y: -hgt / 2 - 2, width: n.w + (hasKids ? MAP_KNOB + 8 : 10), height: hgt + 4 }));
    hoverable(g, () => tipFor(n, open), { focusable: false });
    g.addEventListener("click", (e) => { if (!dragMoved) activate(n); e.stopPropagation(); });
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(n); } });
    return g;
  }
  function tipFor(n, open) {
    const act = n.children.length ? ` · ${open ? t("click to close") : t("click to open")}` : "";
    const terms = (c) => tn(c, "{n} term", "{n} terms", { n: fmtNum(c) });
    if (n.kind === "term") {
      const tm = n.term;
      return [tm.term, tm.definition, `${catLabel(tm.category)}${tm.theme ? ` · ${tm.theme}` : ""} · ${tm.n_sessions ? tn(tm.n_sessions, "{n} session", "{n} sessions") : t("not mentioned verbatim")}`];
    }
    if (n.kind === "more") return [n.label, t("Browse and filter them in the side panel")];
    if (n.kind === "theme") return [n.label, themeOf(n.cat, n.label)?.description || "", `${terms(n.count)}${act}`];
    if (n.kind === "kitem") return [n.k.title, `${kindLabel(n.k.kind)} · ${agentName(n.k.agent)}`];
    if (n.kind === "session") return [n.label, `${fmtDate(n.session.started_at)} · ${agentName(n.session.agent)} · ${projectName(n.session.project_path)}`];
    if (n.kind === "project") {
      const p = projectsByPath[n.value];
      return [n.label, `${terms(n.count)}${p?.sessions ? ` · ${tn(p.sessions, "{n} session", "{n} sessions", { n: fmtNum(p.sessions) })}` : ""}${act}`];
    }
    return [n.label, `${terms(n.count)}${act}`];
  }

  // ------------------------------------------------------------------ camera
  let anim = 0;
  function animateTo(k, tx, ty) {
    const id = ++anim, from = { k: view.k, tx: view.tx, ty: view.ty }, t0 = performance.now(), ms = reducedMotion() ? 0 : 320;
    const step = (now) => {
      if (id !== anim) return; // the user took over
      const p = ms ? Math.min(1, (now - t0) / ms) : 1, e = 1 - Math.pow(1 - p, 3);
      view.k = from.k + (k - from.k) * e;
      view.tx = from.tx + (tx - from.tx) * e;
      view.ty = from.ty + (ty - from.ty) * e;
      applyView();
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  function ensureVisible(n) { // after opening a branch, glide just enough to show it
    const sub = [];
    (function walk(m) { sub.push(m); mapKids(m).forEach(walk); })(n);
    const W = box.clientWidth, H = box.clientHeight, pad = 28, k = view.k;
    const minY = Math.min(...sub.map((m) => m.y - 20)), maxY = Math.max(...sub.map((m) => m.y + 20));
    const maxX = Math.max(...sub.map((m) => m.x + m.w + 24));
    let { tx, ty } = view;
    if ((maxY - minY) * k > H - 2 * pad) ty = H / 2 - n.y * k;
    else if (ty + maxY * k > H - pad) ty = H - pad - maxY * k;
    else if (ty + minY * k < pad) ty = pad - minY * k;
    const over = tx + maxX * k - (W - pad);
    if (over > 0) tx -= Math.min(over, tx + n.x * k - pad);
    if (tx !== view.tx || ty !== view.ty) animateTo(k, tx, ty);
  }
  function centerOn(n) { animateTo(view.k, box.clientWidth * 0.4 - (n.x + n.w / 2) * view.k, box.clientHeight / 2 - n.y * view.k); }
  function fit(minK = 0.25) {
    if (!layout) return;
    const xs = layout.nodes.map((n) => n.x + n.w + 20), ys = layout.nodes.map((n) => n.y);
    const w = Math.max(...xs) + 24, top = Math.min(...ys) - 30, hgt = Math.max(...ys) - top + 30;
    const k = Math.max(minK, Math.min(1.2, (box.clientWidth - 48) / w, (box.clientHeight - 90) / hgt));
    const spare = box.clientHeight - 30 - hgt * k;
    animateTo(k, 28, (spare > 0 ? 30 + spare / 2 : 60) - top * k); // too tall even so: start at the top, below Group by
  }
  function zoom(f, cx = box.clientWidth / 2, cy = box.clientHeight / 2) {
    anim++;
    const k = Math.max(0.2, Math.min(2.5, view.k * f));
    view.tx = cx - (cx - view.tx) * (k / view.k);
    view.ty = cy - (cy - view.ty) * (k / view.k);
    view.k = k;
    applyView();
  }

  // ------------------------------------------------------------------ interaction
  function activate(n) {
    if (n.kind === "more") { selected = n.id; draw(); showMore(n); return; }
    const opening = n.children.length && !mapState.open.has(n.id);
    if (n.children.length) {
      const before = n.y;
      mapState.open.has(n.id) ? mapState.open.delete(n.id) : mapState.open.add(n.id);
      selected = n.id;
      draw();
      anim++;
      view.ty += (before - n.y) * view.k; // the clicked node stays under the pointer while the tree re-flows
      applyView();
      if (opening) ensureVisible(n);
    }
    select(n);
  }
  function select(n) {
    selected = n.id;
    draw();
    showDetail(n);
    setParams(urlState({ term: n.kind === "term" ? n.term.term : "" }));
  }
  function openPath(node, focus = false) { // open every ancestor, pinning the node wherever a branch is capped
    for (let c = node, p = node.parent; p; c = p, p = p.parent) {
      mapState.open.add(p.id);
      if (focus) { mapState.only.add(p.id); mapState.pinned.add(c.id); } // a search: show only the way to it
      else if (MAP_CAP[p.kind] && p.children.indexOf(c) >= MAP_CAP[p.kind]) mapState.pinned.add(c.id);
    }
  }
  function reveal(tm) { // a term's first place in the tree: open the path to it, centre and select it
    let node = null;
    (function find(n) { if (!node) { if (n.kind === "term" && n.term.id === tm.id) node = n; else n.children.forEach(find); } })(tree);
    if (!node) { toast(t("{term} is hidden by the current filter", { term: tm.term })); return; }
    openPath(node);
    select(node);
    centerOn(node);
  }
  function jumpTo(node) { openPath(node); mapState.open.add(node.id); select(node); ensureVisible(node); }

  // pan with drag or two-finger scroll, zoom with pinch / ctrl+wheel
  let drag = null, dragMoved = false;
  svg.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty }; dragMoved = false; });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!dragMoved && Math.hypot(dx, dy) < 4) return;
    if (!dragMoved) { dragMoved = true; anim++; svg.setPointerCapture(e.pointerId); box.classList.add("dragging"); hideTip(); }
    view.tx = drag.tx + dx;
    view.ty = drag.ty + dy;
    applyView();
  });
  const endDrag = () => { drag = null; box.classList.remove("dragging"); setTimeout(() => (dragMoved = false)); };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    anim++;
    const r = svg.getBoundingClientRect();
    if (e.ctrlKey || e.metaKey) zoom(Math.exp(-e.deltaY * 0.01), e.clientX - r.left, e.clientY - r.top);
    else { view.tx -= e.deltaX; view.ty -= e.deltaY; applyView(); }
  }, { passive: false });

  // ------------------------------------------------------------------ details
  const termChip = (tm) => {
    const b = h("button", { type: "button", class: "mm-tchip", onclick: () => reveal(tm), title: tm.definition || "" },
      h("i", { class: "mm-cdot" }), tm.term);
    b.style.setProperty("--c", mapColor(tm.category));
    return b;
  };
  const relatedChip = (name) => {
    const tm = mapFindTerm(shown, name);
    return tm ? termChip(tm) : h("span", { class: "mm-tchip off", title: t("hidden by the current filter") }, name);
  };
  const catTag = (cat) => {
    const e = h("span", { class: "mm-cat" }, icon(ICONS[cat] ? cat : "dot"), catLabel(cat));
    e.style.setProperty("--c", mapColor(cat));
    return e;
  };
  const neutralLabel = (iconName, text) => h("span", { class: "mm-cat neutral" }, icon(iconName), text);
  const chipsOf = (terms, n = 16) => h("div", { class: "mm-chips" }, terms.slice(0, n).map(termChip));
  const closeBtn = () => h("button", { class: "icon-btn mm-close", type: "button", "aria-label": t("Close details"),
    onclick: () => { selected = null; draw(); aside.replaceChildren(...home()); setParams(urlState({ term: "" })); } }, icon("x"));
  const where = (n) => { // "in concept › Auth & identity" for the groups above a node
    const trail = [];
    for (let p = n.parent; p && p.kind !== "root"; p = p.parent) trail.unshift(p.label);
    return trail.length ? t("In {path}. ", { path: trail.join(" › ") }) : "";
  };
  function showDetail(n) {
    let body;
    if (n.kind === "term") {
      const tm = n.term;
      const ks = tm.knowledge.map((id) => data.knowledge[id]).filter(Boolean);
      body = [
        catTag(tm.category), tm.theme ? h("span", { class: "mm-theme-tag" }, tm.theme) : null,
        h("h3", null, tm.term),
        tm.aliases.length ? h("div", { class: "mm-aliases" }, t("also {names}", { names: tm.aliases.slice(0, 4).join(t(", ")) + (tm.aliases.length > 4 ? " " + t("+{n} more", { n: tm.aliases.length - 4 }) : "") })) : null,
        h("p", { class: "mm-def gloss" }, tm.definition || ""),
        h("div", { class: "mm-stats" },
          tm.n_sessions ? [h("span", null, h("b", null, fmtNum(tm.n_sessions)), " ", tn(tm.n_sessions, "session", "sessions")),
            h("span", null, h("b", null, fmtCompact(tm.n_mentions)), " ", tn(tm.n_mentions, "mention", "mentions")),
            h("span", null, `${fmtDate(tm.first_seen)} → ${fmtDate(tm.last_seen)}`)] : h("span", null, t("not mentioned verbatim in transcripts")),
          tm.agents.length ? h("span", null, tm.agents.map(agentName).join(t(", "))) : null),
        tm.projects.length ? [h("h4", null, t("Where it is used")), h("div", { class: "mm-uses" }, tm.projects.map((u) => h("div", { class: "mm-use" },
          u.path === "__global__" ? h("b", null, t("Everywhere")) : h("a", { href: `#/project?path=${encodeURIComponent(u.path)}` }, projectName(u.path)),
          u.context ? h("div", { class: "gloss" }, u.context) : null)))] : null,
        tm.related.length ? [h("h4", null, t("Related")), h("div", { class: "mm-chips" }, tm.related.map(relatedChip))] : null,
        ks.length ? [h("h4", null, t("Learned from · {n}", { n: ks.length })), h("ul", { class: "mm-klist" }, ks.slice(0, 8).map((k) => h("li", null, kindChip(k.kind),
          k.session_id ? h("a", { href: `#/session/${k.session_id}` }, k.title) : h("span", null, k.title)))),
          ks.length > 8 ? h("a", { class: "mm-more", href: `#/knowledge?q=${encodeURIComponent(tm.term)}` }, t("{n} more in Knowledge →", { n: ks.length - 8 })) : null] : null,
        tm.sessions.length ? [h("h4", null, t("Mentioned most in")), h("ul", { class: "mm-klist" }, tm.sessions.map((x) => h("li", null,
          h("span", { class: "mm-date" }, fmtDate(x.started_at)), h("a", { href: `#/session/${x.id}` }, x.title || x.id.slice(0, 8)))))] : null,
        h("div", { class: "mm-links-row" },
          h("a", { class: "btn small", href: `#/glossary?term=${encodeURIComponent(tm.term)}` }, t("Glossary entry")),
          tm.n_sessions ? h("a", { class: "btn small", href: `#/search?q=${encodeURIComponent(tm.term)}` }, t("All mentions")) : null),
      ];
    } else if (n.kind === "project") {
      const p = projectsByPath[n.value] || { path: n.value, label: n.label, knowledge: {} };
      const kinds = Object.entries(p.knowledge || {}).sort((a, b) => b[1] - a[1]);
      body = [
        neutralLabel("projects", p.path === "__global__" ? t("cross-project") : t("project")),
        h("h3", null, n.label),
        h("p", { class: "mm-def" }, where(n) + (p.path === "__global__" ? t("Terms found across all your projects.") : "")),
        h("div", { class: "hero-facts" }, p.sessions ? [fact(t("sessions"), fmtNum(p.sessions)), fact(t("active"), fmtHours(p.active_s)), fact(t("last session"), fmtDate(p.last))] : null, fact(t("terms"), fmtNum(n.count))),
        kinds.length ? [h("h4", null, t("Knowledge")), h("div", { class: "mm-chips" }, kinds.map(([k, c]) => h("span", { class: "kind-chip" }, icon(KIND[k] ? k : "dot"), `${kindLabel(k)} ${c}`)))] : null,
        h("h4", null, t("Most discussed here")), chipsOf(n.terms, 12),
        p.path !== "__global__" ? h("div", { class: "mm-links-row" }, h("a", { class: "btn small", href: `#/project?path=${encodeURIComponent(p.path)}` }, t("Open project"))) : null,
      ];
    } else if (n.kind === "category") {
      const themes = (data.themes[n.cat] || []);
      const themeKids = n.children.filter((c) => c.kind === "theme");
      body = [
        catTag(n.cat),
        h("h3", null, t("{n} {category} terms", { n: fmtNum(n.count), category: catLabel(n.cat) })),
        h("p", { class: "mm-def" }, where(n) + t("Most discussed first.")),
        themes.length ? [h("h4", null, t("Themes · {n}", { n: themes.length })), h("div", { class: "mm-themes" }, themes.map((th) => {
          const kid = themeKids.find((c) => c.label === th.name);
          return h(kid ? "button" : "div", { class: "mm-theme", type: kid ? "button" : null, onclick: kid ? () => jumpTo(kid) : null },
            h("span", { class: "mm-theme-name" }, th.name, h("span", { class: "mm-count-inline" }, kid ? fmtNum(kid.count) : fmtNum(th.n_terms))),
            th.description ? h("span", { class: "mm-theme-desc" }, th.description) : null);
        }))] : null,
        h("h4", null, t("Most discussed")), chipsOf(n.terms),
        h("div", { class: "mm-links-row" }, h("a", { class: "btn small", href: `#/glossary?category=${encodeURIComponent(n.cat)}` }, t("Show as a list"))),
      ];
    } else if (n.kind === "theme") {
      const th = n.value !== null ? themeOf(n.cat, n.label) : null;
      body = [
        catTag(n.cat),
        h("h3", null, n.label),
        h("p", { class: "mm-def" }, n.value === null ? t("Terms added since this category was last grouped; they are grouped on the next run.") : th?.description || ""),
        h("p", { class: "mm-def muted" }, where(n) + t("{n} terms, most discussed first.", { n: fmtNum(n.count) })),
        chipsOf(n.terms, 24),
      ];
    } else if (n.kind === "agent") {
      body = [
        neutralLabel("sessions", t("agent")),
        h("h3", null, n.label),
        h("p", { class: "mm-def" }, where(n) + t("{n} terms learned from {agent} sessions.", { n: fmtNum(n.count), agent: n.label })),
        h("h4", null, t("Most discussed")), chipsOf(n.terms),
      ];
    } else if (n.kind === "kitem") {
      const k = n.k;
      body = [
        kindChip(k.kind),
        h("h3", null, k.title),
        h("p", { class: "mm-def gloss" }, k.body || ""),
        h("div", { class: "mm-stats" }, h("span", null, projectName(k.project_path)), h("span", null, agentName(k.agent)),
          h("span", null, t("about {term}", { term: n.parent.label }))),
        h("div", { class: "mm-links-row" },
          k.session_id ? h("a", { class: "btn small", href: `#/session/${k.session_id}` }, t("Open session")) : null,
          h("a", { class: "btn small", href: `#/knowledge?q=${encodeURIComponent(k.title)}` }, t("In Knowledge"))),
      ];
    } else if (n.kind === "session") {
      const x = n.session;
      body = [
        neutralLabel("sessions", t("session")),
        h("h3", null, x.title || x.id.slice(0, 8)),
        h("div", { class: "mm-stats" }, h("span", null, fmtDT(x.started_at)), h("span", null, agentName(x.agent)), h("span", null, projectName(x.project_path))),
        h("p", { class: "mm-def muted" }, t("One of the sessions that mention {term} most.", { term: n.parent.label })),
        h("div", { class: "mm-links-row" }, h("a", { class: "btn small", href: `#/session/${x.id}` }, t("Open session"))),
      ];
    } else {
      aside.replaceChildren(...home());
      return;
    }
    aside.replaceChildren(closeBtn(), ...[body].flat(Infinity).filter(Boolean));
    aside.scrollTop = 0;
    aside.querySelectorAll(".gloss").forEach((el) => glossify(el)); // not the heading: it is the term itself
  }
  function showMore(more) { // the rest of a big branch, as a filterable list; picking one pins it onto the map
    const parent = more.of, drawn = new Set(mapKids(parent));
    const hidden = parent.children.filter((c) => !drawn.has(c));
    const what = MAP_PLURAL[hidden[0]?.kind] || t("items");
    const sub = (c) => c.kind === "term" ? (c.term.n_sessions ? t("{n} sess.", { n: fmtNum(c.term.n_sessions) }) : "")
      : c.kind === "kitem" ? kindLabel(c.k.kind) : c.kind === "session" ? fmtDate(c.session.started_at) : tn(c.count, "{n} term", "{n} terms", { n: fmtNum(c.count) });
    const hay = (c) => [c.label, ...(c.term ? [...c.term.aliases, c.term.definition || ""] : []), c.k?.body || ""].join(" ").toLowerCase();
    const pick = (c) => {
      mapState.pinned.add(c.id);
      if (c.children.length && c.kind !== "term") mapState.open.add(c.id);
      select(c);
      if (c.kind === "term") centerOn(c); else ensureVisible(c);
    };
    const list = h("ul", { class: "mm-pick" });
    const fill = (q) => {
      const words = q.toLowerCase().split(/\s+/).filter(Boolean);
      const named = (c) => words.every((w) => [c.label, ...(c.term?.aliases || [])].join(" ").toLowerCase().includes(w));
      const rows = hidden.filter((c) => words.every((w) => hay(c).includes(w)))
        .sort((a, b) => named(b) - named(a)); // name matches before definition matches (stable: most discussed first)
      list.replaceChildren(...rows.map((c) => {
        const b = h("button", { type: "button", onclick: () => pick(c), title: c.term?.definition || "" },
          h("i", { class: "mm-cdot" }), h("span", { class: "mm-pick-label" }, c.label), h("span", { class: "mm-pick-sub" }, sub(c)));
        if (c.cat && !mapNeutral(c.kind)) b.style.setProperty("--c", mapColor(c.cat));
        return h("li", null, b);
      }));
      if (!rows.length) list.append(h("li", { class: "mm-pick-none" }, t("Nothing matches.")));
    };
    const filter = h("input", { class: "input mm-pick-filter", type: "search", placeholder: t("Filter {n} {what}…", { n: hidden.length, what }),
      oninput: (e) => fill(e.target.value),
      onkeydown: (e) => { if (e.key === "Enter") list.querySelector("button")?.click(); } });
    fill("");
    aside.replaceChildren(closeBtn(),
      parent.cat && !mapNeutral(parent.kind) ? catTag(parent.cat) : neutralLabel("projects", MAP_PLURAL[parent.kind] || t("branch")),
      h("h3", null, t("{n} more {what}", { n: fmtNum(hidden.length), what })),
      h("p", { class: "mm-def" }, (parent.kind === "root" ? "" : t("In {path}. ", { path: parent.label })) + t("Most discussed first; pick one to add it to the map.")),
      filter, list);
    aside.scrollTop = 0;
    filter.focus({ preventScroll: true });
  }
  function themesNote() {
    const due = (data.themes_due || []).filter((c) => withHidden || !MAP_HIDDEN.has(c));
    const have = Object.keys(data.themes || {}).length > 0;
    if (!due.length) return have && !levels.includes("theme")
      ? h("p", { class: "mm-tip" }, t("The big categories are grouped into themes: add a Theme level to see them.")) : null;
    const btn = h("button", { type: "button", class: "btn small primary admin-only", onclick: async () => {
      btn.disabled = true;
      const r = await post("/api/map/themes", {});
      toast(r.started ? t("{agent} is grouping categories into themes…", { agent: analyzerShort() }) : t("Already running"));
      watchJob("themes");
    } }, have ? t("Regroup into themes") : t("Group into themes"));
    return h("div", { class: "mm-note" },
      h("b", null, have ? tn(due.length, "{n} category has changed since grouping", "{n} categories have changed since grouping") : t("Group big categories into themes")),
      h("p", null, t("{agent} splits each category with 25+ terms into named themes, so no branch is a long list. One call per category ({list}).",
        { agent: analyzerShort(), list: due.map(catLabel).join(t(", ")) })),
      btn);
  }
  function overview() {
    const sharedTerms = shown.filter((tm) => realProjects(tm) > 1).sort((a, b) => realProjects(b) - realProjects(a) || b.n_sessions - a.n_sessions).slice(0, 10);
    const cats = new Set(shown.map((tm) => tm.category)).size;
    const presetLabel = (p) => p.map((d) => MAP_DIM_NAMES[d]).join(" › ");
    return [
      h("h3", null, t("Your map")),
      h("div", { class: "hero-facts" }, fact(t("terms"), fmtNum(tree.count)), fact(t("projects"), fmtNum(data.projects.filter((p) => p.path !== "__global__").length)),
        fact(t("categories"), fmtNum(cats)), fact(t("themes"), fmtNum(Object.values(data.themes || {}).reduce((a, l) => a + l.length, 0)))),
      themesNote(),
      h("h4", null, t("Views")), h("div", { class: "mm-chips" }, MAP_PRESETS.map((p) => h("button", { type: "button",
        class: `mm-tchip preset${p.join(",") === levels.join(",") ? " on" : ""}`, onclick: () => refresh({ levels: p.join(","), term: "" }) }, presetLabel(p)))),
      sharedTerms.length ? [h("h4", null, t("Shared by the most projects")), chipsOf(sharedTerms, 10)] : null,
      h("h4", null, t("Most discussed")), chipsOf(shown, 10),
      h("p", { class: "mm-tip" }, t("Click a node to open it; terms open into the knowledge and sessions behind them. Drag or scroll to move; pinch or ⌘-scroll to zoom."),
        withHidden ? null : t(" File names and commands are hidden.")),
    ].flat(Infinity).filter(Boolean);
  }
  function results() { // the side panel while a search is on: every match, each a click away on the map
    const { named, described, groups } = hits;
    const row = (label, sub, cat, onclick, title = "") => {
      const b = h("button", { type: "button", onclick, title },
        h("i", { class: "mm-cdot" }), h("span", { class: "mm-pick-label" }, label), h("span", { class: "mm-pick-sub" }, sub));
      if (cat) b.style.setProperty("--c", mapColor(cat));
      return h("li", null, b);
    };
    const termRow = (tm) => row(tm.term, tm.n_sessions ? t("{n} sess.", { n: fmtNum(tm.n_sessions) }) : "", tm.category, () => reveal(tm), tm.definition || "");
    const groupRow = (g) => row(g.label, `${MAP_DIM_NAMES[g.kind].toLowerCase()} · ${fmtNum(g.count)}`, mapNeutral(g.kind) ? null : g.cat,
      () => { select(g); centerOn(g); }, where(g).replace(/(\. |。)$/, ""));
    const leafRow = (n) => row(n.label, n.kind === "kitem" ? kindLabel(n.k.kind) : fmtDate(n.session.started_at), n.cat, () => goLeaf(n),
      t("about {term}", { term: n.parent.label }));
    const section = (title, items, rowOf) => items.length ? [h("h4", null, `${title} · ${fmtNum(items.length)}`), h("ul", { class: "mm-pick" }, items.map(rowOf))] : null;
    const total = named.length + described.length;
    const kitems = hits.leaves.filter((n) => n.kind === "kitem"), sessions = hits.leaves.filter((n) => n.kind === "session");
    const counts = [
      total && tn(total, "{n} term", "{n} terms", { n: fmtNum(total) }),
      groups.length && tn(groups.length, "{n} group", "{n} groups", { n: fmtNum(groups.length) }),
      kitems.length && tn(kitems.length, "{n} knowledge item", "{n} knowledge items", { n: fmtNum(kitems.length) }),
      sessions.length && tn(sessions.length, "{n} session", "{n} sessions", { n: fmtNum(sessions.length) }),
    ].filter(Boolean);
    return [
      closeBtnSearch(),
      h("span", { class: "mm-cat neutral" }, icon("search"), t("search")),
      h("h3", null, t("“{q}”", { q: query })),
      h("p", { class: "mm-def" }, !counts.length ? t("Nothing on the map matches.")
        : t("{what} match, all opened on the map and highlighted. Click one to go to it.", { what: counts.join(t(", ")) })),
      section(t("Groups"), groups, groupRow),
      section(t("Named"), named, termRow),
      section(t("Mentioned in the definition"), described, termRow),
      section(t("Knowledge"), kitems, leafRow),
      section(t("Sessions"), sessions, leafRow),
      hits.hidden ? h("p", { class: "mm-tip" }, t("{n} more among file names and commands. ", { n: fmtNum(hits.hidden) }),
        h("button", { type: "button", class: "link-btn", onclick: () => refresh({ all: "1" }) }, t("Show them"))) : null,
      h("div", { class: "mm-links-row" },
        h("a", { class: "btn small", href: `#/search?q=${encodeURIComponent(query)}` }, t("Search sessions")),
        h("a", { class: "btn small", href: `#/knowledge?q=${encodeURIComponent(query)}` }, t("In Knowledge"))),
    ].flat(Infinity).filter(Boolean);
  }
  const closeBtnSearch = () => h("button", { class: "icon-btn mm-close", type: "button", "aria-label": t("Clear search"),
    onclick: () => { search.value = ""; find(""); } }, icon("x"));
  const home = () => (hits ? results() : overview());
  function goLeaf(n) { openPath(n); select(n); centerOn(n); }
  function find(q) { // open every match on the map: terms by name, alias or definition, groups by name, and the knowledge and sessions under terms
    q = q.trim();
    if (q === query) return;
    query = q;
    selected = null;
    if (!q) {
      hits = null;
      mapState.only = null;
      draw();
    } else {
      const { named, described } = mapMatch(shown, q);
      const words = q.toLowerCase().split(/\s+/);
      const groups = [];
      (function walk(n) {
        if (n.kind === "term") return;
        if (n.kind !== "root" && n.value !== null && words.every((w) => n.label.toLowerCase().includes(w))) groups.push(n);
        n.children.forEach(walk);
      })(tree);
      const ids = new Set([...named, ...described].map((tm) => tm.id));
      const other = withHidden ? { named: [], described: [] } : mapMatch(data.terms.filter((tm) => MAP_HIDDEN.has(tm.category)), q);
      const leafNodes = [], leaves = new Map(); // every place a matching item or session sits, and its first place
      (function walk(n) {
        if (n.kind === "term") {
          for (const c of n.children) if (words.every((w) => mapLeafText(c).includes(w))) { leafNodes.push(c); if (!leaves.has(mapLeafKey(c))) leaves.set(mapLeafKey(c), c); }
          return;
        }
        n.children.forEach(walk);
      })(tree);
      hits = { named, described, groups, ids, leaves: [...leaves.values()], leafKeys: new Set(leaves.keys()), hidden: other.named.length + other.described.length };
      mapState.open = new Set(["root"]);
      mapState.pinned.clear();
      mapState.only = new Set();
      (function walk(n) { // every place each term sits (a term can be under several projects or agents)
        if (n.kind === "term") { if (ids.has(n.term.id)) openPath(n, true); return; }
        n.children.forEach(walk);
      })(tree);
      for (const c of leafNodes) openPath(c, true); // its term opens to show it
      for (const g of groups) { openPath(g, true); mapState.open.add(g.id); }
      for (const g of groups) mapState.only.delete(g.id); // a matching group shows what is in it
      draw();
      if (ids.size || groups.length || leaves.size) fit(0.65); // many matches: stay readable and start at the top
    }
    setParams(urlState({ term: "" }));
    aside.replaceChildren(...home());
    aside.scrollTop = 0;
    if (hits && !hits.groups.length && hits.ids.size + hits.leaves.length === 1) { // just one: open it
      if (hits.ids.size) reveal([...hits.named, ...hits.described][0]); else goLeaf(hits.leaves[0]);
    }
  }
  aside.append(...overview());

  // ------------------------------------------------------------------ page
  function levelBar() {
    const set = (next) => refresh({ levels: next.join(","), term: "" });
    const choose = (value, i) => h("select", { class: "mm-level", "aria-label": t("Level {n}", { n: i + 1 }), onchange: (e) => {
      const next = [...levels];
      if (e.target.value) next[i] = e.target.value; else next.splice(i, 1);
      set(next);
    } }, MAP_DIMS.filter((d) => d === value || !levels.includes(d)).map((d) => h("option", { value: d, selected: d === value }, MAP_DIM_NAMES[d])),
      levels.length > 1 ? h("option", { value: "" }, t("Remove")) : null);
    const spare = MAP_DIMS.filter((d) => !levels.includes(d));
    return h("div", { class: "mm-levels" }, h("span", { class: "mm-levels-label" }, t("Group by")),
      levels.map((d, i) => [i ? h("span", { class: "mm-sep" }, "›") : null, choose(d, i)]),
      spare.length ? h("select", { class: "mm-level add", "aria-label": t("Add a level"), onchange: (e) => e.target.value && set([...levels, e.target.value]) },
        h("option", { value: "", selected: true }, t("+ level")), spare.map((d) => h("option", { value: d }, MAP_DIM_NAMES[d]))) : null,
      h("span", { class: "mm-sep" }, "›"), h("span", { class: "mm-levels-end" }, t("terms")));
  }
  const search = h("input", { class: "input mm-search", type: "search", placeholder: t("Find terms…"), list: "mm-terms",
    "aria-label": t("Find terms: every match opens on the map"), value: params.q || "",
    onchange: (e) => find(e.target.value),
    oninput: (e) => { if (!e.target.value) find(""); } });
  const datalist = h("datalist", { id: "mm-terms" }, shown.map((tm) => h("option", { value: tm.term })));
  const toolBtn = (label, content, onclick) => h("button", { type: "button", title: label, "aria-label": label, onclick }, content);
  const toolIcon = (d) => s("svg", { viewBox: "0 0 24 24", class: "icon", "aria-hidden": "true" }, s("path", { d }));
  const legend = h("div", { class: "mm-legend" },
    [...Object.keys(MAP_HUES), "other"].map((c) => { const e = h("span", null, h("i", { class: "mm-cdot" }), catLabel(c)); e.style.setProperty("--c", mapColor(c)); return e; }),
    h("span", { class: "mm-sizes", title: t("Dot size: sessions that mention the term") }, h("i", { class: "d1" }), h("i", { class: "d2" }), h("i", { class: "d3" }), t("sessions")));
  const tools = h("div", { class: "mm-tools" },
    toolBtn(t("Zoom out"), "−", () => zoom(1 / 1.25)), toolBtn(t("Zoom in"), "+", () => zoom(1.25)),
    toolBtn(t("Fit to screen"), toolIcon(MAP_TOOL_ICONS.fit), fit),
    toolBtn(t("Collapse all"), toolIcon(MAP_TOOL_ICONS.collapse), () => {
      search.value = ""; query = ""; hits = null; mapState.only = null; setParams(urlState({ term: "" }));
      mapState.open = new Set(["root"]); mapState.pinned.clear(); selected = null; draw(); fit(); aside.replaceChildren(...overview());
    }));
  box.append(levelBar(), legend, tools);
  const page = h("div", { class: "mm-page" },
    h("div", { class: "page-head" },
      titled("globe", h("h1", null, t("Map")),
        h("div", { class: "sub" }, data.terms.length ? t("Your glossary as a mindmap: {terms} terms across {projects} projects.", { terms: fmtNum(tree.count), projects: fmtNum(data.projects.filter((p) => p.path !== "__global__").length) }) : t("No glossary yet"))),
      h("div", { class: "head-actions" },
        h("button", { type: "button", class: `chip ${withHidden ? "on" : ""}`, "aria-pressed": String(withHidden), onclick: () => refresh({ all: withHidden ? "" : "1" }) }, t("Files & commands")),
        search, datalist,
        h("a", { class: "btn", href: "#/glossary" }, t("Glossary list")))),
    !data.terms.length ? h("div", { class: "card empty" }, t("The map is drawn from the glossary. Build it on the Glossary page first."))
      : h("section", { class: "card flush mm-card" }, box, aside));

  if (data.terms.length) {
    const ro = new ResizeObserver(() => {
      if (!box.clientWidth) return;
      if (!view.placed) { view.placed = true; draw(); view.tx = 32; view.ty = box.clientHeight / 2 - tree.y; applyView(); }
      else draw();
      if (params.q && !hits && !query) find(params.q);
      if (params.term && !selected) { const tm = mapFindTerm(shown, params.term); if (tm) reveal(tm); }
    });
    ro.observe(box);
  }
  return page;
});

// =====================================================================================
// Systems: every folder your agents worked in as a system, grouped by where it lives, and each system's parts in
// five roles, all from evidence (manifests and what sessions did). Click anything to see why it is there.
// =====================================================================================
const SY_ROLES = [["way_in", t("Ways in")], ["code", t("Code")], ["data", t("Data")], ["delivery", t("Delivery")], ["runtime", t("Runs on & uses")]];
const SY_ROLE_NAME = Object.fromEntries(SY_ROLES);
const SY_HUE = { way_in: "var(--series-1)", code: "var(--series-7)", data: "var(--series-3)", delivery: "var(--series-4)", runtime: "var(--series-2)" };
const SY_KIND = {
  ui: t("UI"), api: t("API"), cli: t("Command line"), extension: t("Extension"), desktop: t("Desktop app"), mcp: t("MCP server"),
  worker: t("Worker"), service: t("Service"), library: t("Package"), component: t("Folder"), store: t("Data store"), ci: t("CI"),
  infra: t("Infrastructure"), image: t("Container image"), deployed: t("Deployed"), server: t("Server"), platform: t("Platform"),
  external: t("External service"),
};
const SY_EDGE = { calls: tc("edge", "calls"), "depends on": t("depends on"), uses: t("uses"), "stores in": t("stores in"),
  "deploys to": t("deploys to"), "deployed as": t("deployed as"), "runs on": t("runs on"), provisions: t("provisions") };
const syEdgeLabel = (l) => SY_EDGE[l] || (l && l.startsWith("calls ") ? t("calls {path}", { path: l.slice(6) }) : l || "");
function syEvText(ev) { // what commands showed, in the dashboard's language
  if (ev.kind !== "commands" || !ev.key) return ev.text;
  const sessions = tn(ev.sessions, "{n} session", "{n} sessions", { n: fmtNum(ev.sessions) });
  const head = ev.key === "host" ? tn(ev.n, "reached in {n} command", "reached in {n} commands", { n: fmtNum(ev.n) })
    : ev.key.startsWith("cli:") ? tn(ev.n, "{tool} in {n} command", "{tool} in {n} commands", { tool: ev.key.slice(4), n: fmtNum(ev.n) })
      : ev.key.startsWith("port:") ? tn(ev.n, "started on :{port} in {n} command", "started on :{port} in {n} commands", { port: ev.key.slice(5), n: fmtNum(ev.n) })
        : ev.text;
  return `${head} · ${sessions}`;
}
const SY_GROUP_HUES = [1, 2, 3, 7, 4, 5, 6, 8].map((n) => `var(--series-${n})`);
const SY_CARD = { w: 212, h: 76, gap: 12 }, SY_PAD = 14, SY_HEAD = 30, SY_ITEM_GAP = 18;
const SY_NODE = { w: 198, h: 62, gap: 22, line: 16, row: 58, perLine: 4 };
const syState = { views: {} }; // the camera per view (landscape, or one system), kept while you move between them

let syCtx;
function syClip(text, px, size = 13, weight = 400) { // the text, cut with … to fit px
  syCtx ||= document.createElement("canvas").getContext("2d");
  syCtx.font = `${weight} ${size}px ${getComputedStyle(document.body).fontFamily}`;
  if (syCtx.measureText(text).width <= px) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (syCtx.measureText(text.slice(0, mid) + "…").width <= px) lo = mid; else hi = mid - 1; }
  return text.slice(0, lo) + "…";
}

// ------------------------------------------------------------------ the canvas: pan, zoom, fit (as on the Map)
function syCanvas(key) {
  const svg = s("svg", { class: "mm-svg sy-svg", role: "img" });
  const defs = s("defs", null, ["sy-arrow", "sy-arrow-on"].map((id) => s("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5,
    markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, s("path", { d: "M0,1 L9,5 L0,9 z", class: id }))));
  const viewG = s("g");
  svg.append(defs, viewG);
  const box = h("div", { class: "mm-canvas sy-canvas" }, svg);
  const cam = syState.views[key] ||= { k: 1, tx: 24, ty: 24, placed: false };
  const apply = () => {
    viewG.setAttribute("transform", `translate(${cam.tx},${cam.ty}) scale(${cam.k})`);
    box.style.backgroundSize = `${22 * cam.k}px ${22 * cam.k}px`;
    box.style.backgroundPosition = `${cam.tx}px ${cam.ty}px`;
  };
  let anim = 0;
  const animateTo = (k, tx, ty) => {
    const id = ++anim, from = { ...cam }, t0 = performance.now(), ms = reducedMotion() ? 0 : 320;
    const step = (now) => {
      if (id !== anim) return;
      const p = ms ? Math.min(1, (now - t0) / ms) : 1, e = 1 - Math.pow(1 - p, 3);
      cam.k = from.k + (k - from.k) * e; cam.tx = from.tx + (tx - from.tx) * e; cam.ty = from.ty + (ty - from.ty) * e;
      apply();
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
  const zoom = (f, cx = box.clientWidth / 2, cy = box.clientHeight / 2) => {
    anim++;
    const k = Math.max(0.15, Math.min(2.5, cam.k * f));
    cam.tx = cx - (cx - cam.tx) * (k / cam.k); cam.ty = cy - (cy - cam.ty) * (k / cam.k); cam.k = k;
    apply();
  };
  const fit = (b, maxK = 1.1, animate = true, minK = 0.15) => {
    const W = box.clientWidth, H = box.clientHeight;
    if (!W || !H || !b.w) return;
    const k = Math.max(minK, Math.min(maxK, (W - 56) / b.w, (H - 70) / b.h));
    const tx = Math.max(28, (W - b.w * k) / 2) - b.x * k, ty = Math.max(28, (H - b.h * k) / 2) - b.y * k;
    if (animate) animateTo(k, tx, ty); else { cam.k = k; cam.tx = tx; cam.ty = ty; apply(); }
  };
  const centerOn = (x, y) => animateTo(cam.k, box.clientWidth / 2 - x * cam.k, box.clientHeight / 2 - y * cam.k);
  let drag = null;
  const state = { moved: false };
  svg.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, tx: cam.tx, ty: cam.ty }; state.moved = false; });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!state.moved && Math.hypot(dx, dy) < 4) return;
    if (!state.moved) { state.moved = true; anim++; svg.setPointerCapture(e.pointerId); box.classList.add("dragging"); hideTip(); }
    cam.tx = drag.tx + dx; cam.ty = drag.ty + dy;
    apply();
  });
  const endDrag = () => { drag = null; box.classList.remove("dragging"); setTimeout(() => (state.moved = false)); };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    anim++;
    const r = svg.getBoundingClientRect();
    if (e.ctrlKey || e.metaKey) zoom(Math.exp(-e.deltaY * 0.01), e.clientX - r.left, e.clientY - r.top);
    else { cam.tx -= e.deltaX; cam.ty -= e.deltaY; apply(); }
  }, { passive: false });
  const toolBtn = (label, content, onclick) => h("button", { type: "button", title: label, "aria-label": label, onclick }, content);
  const tools = (onFit) => h("div", { class: "mm-tools" },
    toolBtn(t("Zoom out"), "−", () => zoom(1 / 1.25)), toolBtn(t("Zoom in"), "+", () => zoom(1.25)),
    toolBtn(t("Fit to screen"), s("svg", { viewBox: "0 0 24 24", class: "icon", "aria-hidden": "true" }, s("path", { d: MAP_TOOL_ICONS.fit })), onFit));
  return { svg, viewG, box, cam, apply, fit, centerOn, tools, state };
}

// a curve from one box to another, leaving and arriving on the sides that face each other
function syCurve(a, b) {
  const ac = { x: a.x + a.w / 2, y: a.y + a.h / 2 }, bc = { x: b.x + b.w / 2, y: b.y + b.h / 2 };
  const dx = bc.x - ac.x, dy = bc.y - ac.y;
  if (Math.abs(dx) * 0.6 >= Math.abs(dy) || Math.abs(dx) > (a.w + b.w) / 2) {
    const sx = dx >= 0 ? a.x + a.w : a.x, ex = dx >= 0 ? b.x : b.x + b.w, c = Math.max(40, Math.abs(ex - sx) * 0.45) * (dx >= 0 ? 1 : -1);
    return { d: `M${sx},${ac.y} C${sx + c},${ac.y} ${ex - c},${bc.y} ${ex},${bc.y}`, mx: (sx + ex) / 2, my: (ac.y + bc.y) / 2 };
  }
  const sy = dy >= 0 ? a.y + a.h : a.y, ey = dy >= 0 ? b.y : b.y + b.h, c = Math.max(30, Math.abs(ey - sy) * 0.45) * (dy >= 0 ? 1 : -1);
  return { d: `M${ac.x},${sy} C${ac.x},${sy + c} ${bc.x},${ey - c} ${bc.x},${ey}`, mx: (ac.x + bc.x) / 2, my: (sy + ey) / 2 };
}

// ------------------------------------------------------------------ landscape layout: folders as boxes, systems as cards
function syLayoutGroup(g, groups, byId, hidden) {
  const kids = g.groups.map((id) => syLayoutGroup(groups[id], groups, byId, hidden)).filter((k) => k.count);
  const sys = g.systems.map((id) => byId[id]).filter((x) => x && !hidden(x))
    .sort((a, b) => b.sessions - a.sessions || a.label.localeCompare(b.label));
  const items = [];
  if (sys.length) {
    const cols = Math.min(sys.length, 4, Math.max(1, Math.ceil(Math.sqrt(sys.length * 1.4))));
    const rows = Math.ceil(sys.length / cols);
    items.push({ kind: "block", sys, cols, w: cols * (SY_CARD.w + SY_CARD.gap) - SY_CARD.gap, h: rows * (SY_CARD.h + SY_CARD.gap) - SY_CARD.gap });
  }
  kids.sort((a, b) => b.w * b.h - a.w * a.h).forEach((k) => items.push({ kind: "group", node: k, w: k.w, h: k.h }));
  const area = items.reduce((a, it) => a + (it.w + SY_ITEM_GAP) * (it.h + SY_ITEM_GAP), 0);
  const target = Math.max(...items.map((it) => it.w), 0, Math.sqrt(area * (g.id === "" ? 1.3 : 2.4))); // folders inside wide, the whole map squarer
  let x = 0, y = 0, rowH = 0, width = 0;
  for (const it of items) { // shelves: left to right, a new row when the next would pass the target width
    if (x > 0 && x + it.w > target) { x = 0; y += rowH + SY_ITEM_GAP; rowH = 0; }
    it.x = x; it.y = y;
    x += it.w + SY_ITEM_GAP; rowH = Math.max(rowH, it.h); width = Math.max(width, x - SY_ITEM_GAP);
  }
  const pad = g.id ? SY_PAD : 0, top = g.id ? SY_HEAD : 0;
  return { g, items, pad, top, w: width + 2 * pad, h: (items.length ? y + rowH : 0) + top + pad,
    count: sys.length + kids.reduce((a, k) => a + k.count, 0) };
}
function syPlace(node, ox, oy, out, depth = 0, hue = null) {
  if (node.g.id) out.groups.push({ g: node.g, x: ox, y: oy, w: node.w, h: node.h, depth, hue, count: node.count });
  for (const it of node.items) {
    const bx = ox + node.pad + it.x, by = oy + node.top + it.y;
    if (it.kind === "block") {
      it.sys.forEach((sys, i) => out.cards.push({ sys, hue, x: bx + (i % it.cols) * (SY_CARD.w + SY_CARD.gap),
        y: by + Math.floor(i / it.cols) * (SY_CARD.h + SY_CARD.gap), w: SY_CARD.w, h: SY_CARD.h }));
    } else {
      syPlace(it.node, bx, by, out, depth + 1, hue || SY_GROUP_HUES[out.hues++ % SY_GROUP_HUES.length]);
    }
  }
  return out;
}

route(/^\/systems$/, async (params) => (params.system ? systemView(params) : landscapeView(params)));

async function landscapeView(params) {
  const data = await api("/api/systems");
  const groups = Object.fromEntries(data.groups.map((g) => [g.id, g]));
  const byId = Object.fromEntries(data.systems.map((x) => [x.id, x]));
  const showSmall = params.small === "1", showLinks = params.links !== "0";
  const hidden = (x) => !showSmall && x.sessions <= 1 && !data.links.some((l) => l.from === x.id || l.to === x.id);
  const tree = syLayoutGroup(groups[""], groups, byId, hidden);
  const lay = syPlace(tree, 0, 0, { groups: [], cards: [], hues: 0 });
  const cardOf = Object.fromEntries(lay.cards.map((c) => [c.sys.id, c]));
  const maxSessions = Math.max(1, ...data.systems.map((x) => x.sessions));
  const cv = syCanvas("landscape");
  const groupG = s("g", { class: "sy-groups" }), linkG = s("g", { class: "sy-links" }), cardG = s("g", { class: "sy-cards" });
  cv.viewG.append(groupG, linkG, cardG);
  const aside = h("aside", { class: "mm-detail sy-detail" });
  let selected = params.focus || null, query = "";
  const refresh = (changes) => { setParams({ small: showSmall ? "1" : "", links: showLinks ? "" : "0", focus: selected || "", ...changes }); render(); };

  for (const gb of lay.groups) {
    const g = s("g", { class: `sy-group d${Math.min(gb.depth, 3)}` });
    g.style.setProperty("--c", gb.hue || "var(--accent)");
    g.append(s("rect", { x: gb.x, y: gb.y, width: gb.w, height: gb.h, rx: 14 }),
      s("text", { x: gb.x + SY_PAD, y: gb.y + 20, class: "sy-gtitle" }, syClip(gb.g.label, gb.w - 70, 13, 600)),
      s("text", { x: gb.x + gb.w - SY_PAD, y: gb.y + 20, class: "sy-gcount", "text-anchor": "end" }, fmtNum(gb.count)));
    groupG.append(g);
  }
  const links = showLinks ? data.links.filter((l) => cardOf[l.from] && cardOf[l.to]) : [];
  const linkEls = links.map((l) => {
    const c = syCurve(cardOf[l.from], cardOf[l.to]);
    const p = s("path", { d: c.d, class: `sy-link ${l.kind}`, "marker-end": "url(#sy-arrow)" });
    p.dataset.from = l.from; p.dataset.to = l.to;
    hoverable(p, () => [`${byId[l.from].label} → ${byId[l.to].label}`, l.kind === "files" ? l.evidence[0].text : t("mentioned in its glossary"),
      l.evidence[0].text && l.kind !== "files" ? syOne(l.evidence[0].text, 140) : ""], { focusable: false });
    return p;
  });
  linkG.append(...linkEls);
  const cardEls = {};
  for (const c of lay.cards) {
    const x = c.sys, g = s("g", { class: "sy-card", tabindex: "0", role: "button", "aria-label": x.label, transform: `translate(${c.x},${c.y})` });
    g.style.setProperty("--c", c.hue || "var(--accent)");
    const sub = [tn(x.sessions, "{n} session", "{n} sessions", { n: fmtNum(x.sessions) }), x.last ? ago(x.last) : ""].filter(Boolean).join(" · ");
    const third = x.stack.length ? x.stack.slice(0, 3).join(" · ") : x.platforms.slice(0, 3).join(" · ");
    const runs = x.deployed.length ? x.deployed.map((d0) => d0.label) : [];
    const bar = Math.max(6, (SY_CARD.w - 28) * Math.log(1 + x.sessions) / Math.log(1 + maxSessions));
    g.append(s("rect", { class: "sy-cbox", width: c.w, height: c.h, rx: 10 }),
      s("rect", { class: "sy-cstripe", x: 0, y: 10, width: 3.5, height: c.h - 20, rx: 1.75 }),
      s("text", { x: 14, y: 22, class: "sy-ctitle" }, syClip(x.label, c.w - 28 - (runs.length ? 18 : 0), 13.5, 600)),
      s("text", { x: 14, y: 40, class: "sy-csub" }, syClip(sub, c.w - 28, 12)),
      s("text", { x: 14, y: 57, class: "sy-cstack" }, syClip(third || (x.on_disk ? "" : t("not on this computer")), c.w - 28, 11.5)),
      s("rect", { class: "sy-cbar-bg", x: 14, y: c.h - 9, width: c.w - 28, height: 3, rx: 1.5 }),
      s("rect", { class: "sy-cbar", x: 14, y: c.h - 9, width: bar, height: 3, rx: 1.5 }));
    if (runs.length) {
      const ic = icon("cloud", "sy-cicon");
      setAttrs(ic, { x: c.w - 28, y: 9, width: 15, height: 15 });
      g.append(ic);
    }
    hoverable(g, () => [x.label, shortPath(x.path || x.id), [tn(x.n_parts, "{n} part", "{n} parts", { n: fmtNum(x.n_parts) }),
      runs.length ? t("runs as {names}", { names: runs.slice(0, 3).join(t(", ")) }) : ""].filter(Boolean).join(" · ")], { focusable: false });
    g.addEventListener("click", (e) => { e.stopPropagation(); if (!cv.state.moved) select(x.id); });
    g.addEventListener("dblclick", (e) => { e.stopPropagation(); go(`#/systems?system=${encodeURIComponent(x.id)}`); });
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); go(`#/systems?system=${encodeURIComponent(x.id)}`); }
      else if (e.key === " ") { e.preventDefault(); select(x.id); }
    });
    cardEls[x.id] = g;
    cardG.append(g);
  }
  cv.svg.addEventListener("click", () => { if (!cv.state.moved && selected) select(null); });

  function paint() { // what is selected or matches the search stands out; links of the selection light up
    const q = query.toLowerCase();
    const match = (x) => q && (x.label.toLowerCase().includes(q) || (x.path || "").toLowerCase().includes(q) || x.stack.some((st) => st.toLowerCase().includes(q))
      || x.deployed.some((d0) => d0.label.toLowerCase().includes(q)) || x.services.some((sv) => sv.toLowerCase().includes(q)));
    for (const [id, el] of Object.entries(cardEls)) {
      el.classList.toggle("sel", id === selected);
      el.classList.toggle("hit", !!match(byId[id]));
      el.classList.toggle("dim", !!q && !match(byId[id]) && id !== selected);
    }
    const near = new Set(selected ? links.filter((l) => l.from === selected || l.to === selected).flatMap((l) => [l.from, l.to]) : []);
    for (const p of linkEls) {
      const on = selected && (p.dataset.from === selected || p.dataset.to === selected);
      p.classList.toggle("on", !!on);
      p.classList.toggle("off", !!selected && !on);
      p.setAttribute("marker-end", on ? "url(#sy-arrow-on)" : "url(#sy-arrow)");
    }
    for (const [id, el] of Object.entries(cardEls)) el.classList.toggle("near", near.has(id) && id !== selected);
  }
  function select(id) {
    selected = id;
    paint();
    setParams({ small: showSmall ? "1" : "", links: showLinks ? "" : "0", focus: id || "" });
    aside.replaceChildren(...syKids(id ? systemSummary(byId[id]) : overview()));
    aside.scrollTop = 0;
  }
  const bounds = { x: 0, y: 0, w: tree.w, h: tree.h };

  // ------------------------------------------------------------------ side panel
  const linkRow = (l, dir) => {
    const other = byId[dir === "out" ? l.to : l.from];
    return h("div", { class: "mm-use sy-linkrow" },
      h("a", { href: `#/systems?system=${encodeURIComponent(other.id)}`, onclick: (e) => { if (cardEls[other.id]) { e.preventDefault(); select(other.id); const c = cardOf[other.id]; cv.centerOn(c.x + c.w / 2, c.y + c.h / 2); } } },
        dir === "out" ? "→ " : "← ", other.label),
      h("span", { class: `sy-lkind ${l.kind}` }, l.kind === "files" ? t("files") : t("glossary")),
      h("div", { class: "gloss" }, l.kind === "files" ? l.evidence[0].text : l.evidence.map((e) => e.text).filter(Boolean)[0] || ""));
  };
  function systemSummary(x) {
    const out = data.links.filter((l) => l.from === x.id), inc = data.links.filter((l) => l.to === x.id);
    const gpath = []; for (let g = groups[x.group]; g && g.id; g = groups[g.parent]) gpath.unshift(g.label);
    return [
      h("button", { class: "icon-btn mm-close", type: "button", "aria-label": t("Close details"), onclick: () => select(null) }, icon("x")),
      gpath.length ? h("span", { class: "mm-cat neutral" }, icon("projects"), gpath.join(" › ")) : null,
      h("h3", null, x.label),
      h("div", { class: "mono sy-path" }, shortPath(x.path || x.id)),
      h("div", { class: "mm-stats" },
        h("span", null, h("b", null, fmtNum(x.sessions)), " ", tn(x.sessions, "session", "sessions")),
        x.last ? h("span", null, t("last {when}", { when: ago(x.last) })) : null,
        Object.keys(x.agents).length ? h("span", null, Object.keys(x.agents).map(agentName).join(t(", "))) : null),
      x.stack.length ? [h("h4", null, t("Built with")), h("div", { class: "mm-chips" }, x.stack.map((st) => h("span", { class: "sy-chip" }, st)))] : null,
      x.deployed.length ? [h("h4", null, t("Runs on")), h("ul", { class: "mm-klist" }, x.deployed.map((d0) => h("li", null, icon("cloud"),
        h("span", null, h("b", null, d0.label), d0.platform || d0.what ? h("span", { class: "muted" }, ` · ${[d0.platform, d0.what].filter((v) => v && v !== "deployed" && v !== "server").join(" ") || SY_KIND[d0.what] || ""}`) : null))))] : null,
      x.services.length ? [h("h4", null, t("Uses")), h("div", { class: "mm-chips" }, x.services.map((sv) => h("span", { class: "sy-chip" }, sv)))] : null,
      out.length || inc.length ? [h("h4", null, t("Other systems · {n}", { n: out.length + inc.length })),
        h("div", { class: "mm-uses" }, out.map((l) => linkRow(l, "out")), inc.map((l) => linkRow(l, "in")))] : null,
      h("div", { class: "mm-links-row" },
        h("a", { class: "btn small primary", href: `#/systems?system=${encodeURIComponent(x.id)}` }, t("Open system")),
        x.project ? h("a", { class: "btn small", href: `#/project?path=${encodeURIComponent(x.project)}` }, t("Project page")) : null,
        x.git ? h("a", { class: "btn small", href: x.git.url, target: "_blank", rel: "noopener" }, x.git.host.includes("github") ? "GitHub" : x.git.host) : null),
    ];
  }
  function overview() {
    const busiest = [...data.systems].sort((a, b) => b.sessions - a.sessions).slice(0, 8);
    const shown = lay.cards.length, hiddenN = data.systems.length - shown;
    return [
      h("h3", null, t("Your systems")),
      h("p", { class: "mm-def" }, t("Every folder your agents worked in, grouped by where it lives. Parts and links come from evidence only: the manifests in each project and what sessions did there.")),
      h("div", { class: "mm-stats" },
        h("span", null, h("b", null, fmtNum(shown)), " ", tn(shown, "system", "systems")),
        h("span", null, h("b", null, fmtNum(data.links.length)), " ", tn(data.links.length, "link", "links")),
        hiddenN ? h("span", null, t("{n} one-session folders hidden", { n: fmtNum(hiddenN) })) : null),
      h("h4", null, t("Links")),
      h("div", { class: "sy-legend" },
        h("div", null, s("svg", { width: 34, height: 10 }, s("path", { d: "M2,5 H32", class: "sy-link files" })), t("sessions in one project edited or read files of another")),
        h("div", null, s("svg", { width: 34, height: 10 }, s("path", { d: "M2,5 H32", class: "sy-link mentions" })), t("its glossary says how it uses the other"))),
      h("h4", null, t("Most active")),
      h("ul", { class: "mm-klist" }, busiest.map((x) => h("li", null, h("span", { class: "mm-date" }, fmtNum(x.sessions)),
        h("a", { href: "#", onclick: (e) => { e.preventDefault(); if (cardEls[x.id]) { select(x.id); const c = cardOf[x.id]; cv.centerOn(c.x + c.w / 2, c.y + c.h / 2); } } }, x.label)))),
      h("p", { class: "muted sy-hint" }, t("Click a system for its summary, double-click to open it. Drag to move, pinch or ⌘-scroll to zoom.")),
      data.read_manifests ? null : h("p", { class: "muted sy-hint" }, t("Manifests are not read ([systems] read_manifests = false): parts come from sessions only.")),
    ];
  }

  const search = h("input", { class: "input mm-search", type: "search", placeholder: t("Find systems…"), "aria-label": t("Find systems by name, stack or where they run"),
    oninput: (e) => {
      query = e.target.value.trim(); paint();
      const first = query && lay.cards.find((c) => cardEls[c.sys.id].classList.contains("hit"));
      if (first && e.inputType !== "deleteContentBackward") cv.centerOn(first.x + first.w / 2, first.y + first.h / 2);
    } });
  cv.box.append(cv.tools(() => cv.fit(bounds)));
  const page = h("div", { class: "mm-page sy-page" },
    h("div", { class: "page-head" },
      titled("rack", h("h1", null, t("Systems")),
        h("div", { class: "sub" }, t("{systems} systems in {groups} folders, and how they connect", { systems: fmtNum(lay.cards.length), groups: fmtNum(lay.groups.length) }))),
      h("div", { class: "head-actions" },
        h("button", { type: "button", class: `chip ${showLinks ? "on" : ""}`, "aria-pressed": String(showLinks), onclick: () => refresh({ links: showLinks ? "0" : "" }) }, t("Links")),
        h("button", { type: "button", class: `chip ${showSmall ? "on" : ""}`, "aria-pressed": String(showSmall), onclick: () => refresh({ small: showSmall ? "" : "1" }) }, t("One-session folders")),
        search)),
    data.systems.length ? h("section", { class: "card flush mm-card" }, cv.box, aside)
      : h("div", { class: "card empty" }, t("No systems yet: they appear once sessions are recorded in project folders.")));
  aside.append(...syKids(selected && byId[selected] ? systemSummary(byId[selected]) : overview()));
  paint();
  if (data.systems.length) {
    const ro = new ResizeObserver(() => {
      if (!cv.box.clientWidth) return;
      if (!cv.cam.placed) { cv.cam.placed = true; cv.fit(bounds, 1, false, 0.62); } // readable cards, from the top left when it is big
      else cv.apply();
    });
    ro.observe(cv.box);
  }
  return page;
}
const syKids = (items) => [items].flat(Infinity).filter((x) => x != null && x !== false); // a panel's parts, without the gaps
const syOne = (text, n) => (text && text.length > n ? text.slice(0, n - 1) + "…" : text || "");

// ------------------------------------------------------------------ one system: its parts in five role columns
function syRows(x) { // roles top to bottom (ways in, code, data, delivery, runs on), each a row of at most four parts a line
  const roles = SY_ROLES.map(([r]) => r).filter((r) => x.parts.some((p) => p.role === r));
  const byRole = Object.fromEntries(roles.map((r) => [r, x.parts.filter((p) => p.role === r).sort((a, b) => b.weight - a.weight)]));
  const nbrs = {};
  for (const e of x.edges) { (nbrs[e.from] ||= []).push(e.to); (nbrs[e.to] ||= []).push(e.from); }
  for (let it = 0; it < 6; it++) { // order each row by where its neighbours sit, to cut crossings
    const pos = {};
    for (const r of roles) byRole[r].forEach((p, i) => (pos[p.id] = i - (byRole[r].length - 1) / 2));
    for (const r of roles) {
      const key = (p) => { const ns = (nbrs[p.id] || []).filter((n) => pos[n] != null); return ns.length ? ns.reduce((a, n) => a + pos[n], 0) / ns.length : pos[p.id]; };
      byRole[r] = [...byRole[r]].sort((a, b) => key(a) - key(b) || b.weight - a.weight);
    }
  }
  const { w, h, gap, line, row, perLine } = SY_NODE;
  const widest = Math.min(perLine, Math.max(...roles.map((r) => byRole[r].length)));
  const fullW = widest * (w + gap) - gap;
  const nodes = {}, heads = [];
  let y = 0;
  roles.forEach((r, ri) => {
    heads.push({ role: r, y });
    y += 24;
    const list = byRole[r];
    for (let i = 0; i < list.length; i += perLine) {
      const chunk = list.slice(i, i + perLine), x0 = (fullW - (chunk.length * (w + gap) - gap)) / 2;
      chunk.forEach((p, j) => (nodes[p.id] = { p, x: x0 + j * (w + gap), y, w, h, row: ri }));
      y += h + line;
    }
    y += row - line;
  });
  return { roles, heads, nodes, w: fullW, h: y - row };
}

async function systemView(params) {
  let x;
  try { x = await api("/api/system", { id: params.system }); }
  catch { return h("div", { class: "card empty" }, t("No such system."), " ", h("a", { href: "#/systems" }, t("All systems"))); }
  const token = renderSeq;
  const gpath = (x.group || "").split("/").filter(Boolean);
  setCrumbs([[t("Projects"), "#/projects"], [t("Systems"), "#/systems"], [x.label]], token);
  const lay = syRows(x);
  const cv = syCanvas(`system:${x.id}`);
  const headG = s("g", { class: "sy-colheads" }), nodeG = s("g", { class: "sy-nodes" });
  const edgeG = s("g", { class: `sy-edges${x.edges.length > 10 ? " quiet" : ""}` }); // many arrows: their labels show on selection
  cv.viewG.append(headG, edgeG, nodeG);
  const aside = h("aside", { class: "mm-detail sy-detail" });
  const parts = Object.fromEntries(x.parts.map((p) => [p.id, p]));
  let selected = params.part && parts[params.part] ? params.part : null;

  for (const hd of lay.heads) {
    const g = s("g", { class: "sy-rowhead" });
    g.style.setProperty("--c", SY_HUE[hd.role]);
    g.append(s("line", { x1: SY_ROLE_NAME[hd.role].length * 7.6 + 12, x2: lay.w, y1: hd.y - 2, y2: hd.y - 2 }),
      s("text", { x: 0, y: hd.y + 2, class: "sy-colhead" }, SY_ROLE_NAME[hd.role].toUpperCase()));
    headG.append(g);
  }
  const edgeEls = x.edges.filter((e) => lay.nodes[e.from] && lay.nodes[e.to]).map((e) => {
    const a = lay.nodes[e.from], b = lay.nodes[e.to];
    const c = syCurve(a, b);
    const g = s("g", { class: `sy-edge ${e.kind || "uses"}` });
    g.dataset.from = e.from; g.dataset.to = e.to;
    const path = s("path", { d: c.d, "marker-end": "url(#sy-arrow)" });
    const label = e.label ? s("text", { x: c.mx, y: c.my - 4, "text-anchor": "middle", class: "sy-elabel" }, syEdgeLabel(e.label)) : null;
    g.append(path, label);
    hoverable(g, () => [`${parts[e.from].label} → ${parts[e.to].label}`, syEdgeLabel(e.label), e.evidence?.[0]?.text || ""], { focusable: false });
    return g;
  });
  edgeG.append(...edgeEls);
  const nodeEls = {};
  for (const n of Object.values(lay.nodes)) {
    const p = n.p, g = s("g", { class: `sy-node ${p.role}`, tabindex: "0", role: "button", "aria-label": p.label, transform: `translate(${n.x},${n.y})` });
    g.style.setProperty("--c", SY_HUE[p.role]);
    const sub = [SY_KIND[p.kind] || p.kind, p.ports.length ? p.ports.slice(0, 2).map((q) => `:${q.port}`).join(" ") : "",
      p.what && p.what !== p.kind ? p.what : "", p.platform && p.platform !== p.label ? p.platform : ""].filter(Boolean).join(" · ");
    const third = p.stack.length ? p.stack.slice(0, 3).join(" · ") : p.folder ? `${p.folder}/` : "";
    const act = p.activity.sessions;
    g.append(s("rect", { class: "sy-nbox", width: n.w, height: n.h, rx: 9 }),
      s("rect", { class: "sy-nstripe", x: 0, y: 9, width: 3.5, height: n.h - 18, rx: 1.75 }),
      s("text", { x: 13, y: 20, class: "sy-ntitle" }, syClip(p.label, n.w - 26 - (act ? 30 : 0), 13, 600)),
      s("text", { x: 13, y: 37, class: "sy-nsub" }, syClip(sub, n.w - 26, 11.5)),
      s("text", { x: 13, y: 53, class: "sy-nstack" }, syClip(third, n.w - 26, 11)));
    if (act) g.append(s("text", { x: n.w - 11, y: 20, class: "sy-nact", "text-anchor": "end" }, fmtNum(act)));
    hoverable(g, () => [p.label, sub, act ? tn(act, "{n} session edited or read files here", "{n} sessions edited or read files here", { n: fmtNum(act) }) : ""], { focusable: false });
    g.addEventListener("click", (e) => { e.stopPropagation(); if (!cv.state.moved) select(p.id === selected ? null : p.id); });
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(p.id); } });
    nodeEls[p.id] = g;
    nodeG.append(g);
  }
  cv.svg.addEventListener("click", () => { if (!cv.state.moved && selected) select(null); });
  function paint() {
    const near = new Set(selected ? x.edges.filter((e) => e.from === selected || e.to === selected).flatMap((e) => [e.from, e.to]) : []);
    for (const [id, el] of Object.entries(nodeEls)) {
      el.classList.toggle("sel", id === selected);
      el.classList.toggle("dim", !!selected && id !== selected && !near.has(id));
    }
    for (const el of edgeEls) {
      const on = selected && (el.dataset.from === selected || el.dataset.to === selected);
      el.classList.toggle("on", !!on);
      el.classList.toggle("off", !!selected && !on);
      el.querySelector("path").setAttribute("marker-end", on ? "url(#sy-arrow-on)" : "url(#sy-arrow)");
    }
  }
  function select(id) {
    selected = id;
    paint();
    setParams({ system: params.system, part: id || "" });
    aside.replaceChildren(...syKids(id ? partDetail(parts[id]) : systemHome()));
    aside.scrollTop = 0;
  }

  // ------------------------------------------------------------------ side panel
  const evItem = (ev) => {
    if (ev.kind === "commands") {
      return h("li", { class: "sy-ev" }, icon("terminal"), h("div", null, h("div", null, syEvText(ev)),
        ev.examples?.length ? h("div", { class: "sy-examples" }, ev.examples.map((ex) => h("div", { class: "sy-example" },
          h("code", null, ex.text), ex.session ? h("a", { href: `#/session/${ex.session}`, class: "mm-date" }, ex.ts ? fmtDate(ex.ts) : t("session")) : null))) : null));
    }
    const ic = { manifest: "file", remote: "cloud", glossary: "glossary", files: "file" }[ev.kind] || "dot";
    return h("li", { class: "sy-ev" }, icon(ic), h("div", null, ev.text));
  };
  const edgeRow = (e, dir) => {
    const other = parts[dir === "out" ? e.to : e.from];
    return h("li", null, h("span", { class: "mm-date" }, dir === "out" ? "→" : "←"), h("div", null,
      h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); select(other.id); const n = lay.nodes[other.id]; cv.centerOn(n.x + n.w / 2, n.y + n.h / 2); } }, other.label),
      h("span", { class: "muted" }, ` · ${syEdgeLabel(e.label)}`),
      (e.evidence || []).slice(0, 2).map((ev) => h("div", { class: "muted sy-evline" }, ev.text))));
  };
  function partDetail(p) {
    const out = x.edges.filter((e) => e.from === p.id), inc = x.edges.filter((e) => e.to === p.id);
    const tag = h("span", { class: "mm-cat" }, h("i", { class: "mm-cdot" }), SY_ROLE_NAME[p.role]);
    tag.style.setProperty("--c", SY_HUE[p.role]);
    const a = p.activity;
    return [
      h("button", { class: "icon-btn mm-close", type: "button", "aria-label": t("Close details"), onclick: () => select(null) }, icon("x")),
      tag, h("span", { class: "mm-theme-tag" }, SY_KIND[p.kind] || p.kind),
      h("h3", null, p.label),
      p.folder != null && p.folder !== undefined ? h("div", { class: "mono sy-path" }, `${shortPath(x.path || "")}/${p.folder}`.replace(/\/$/, "")) : null,
      p.platform || p.what || p.resource || p.package ? h("div", { class: "mm-aliases" }, [p.platform, p.what, p.resource, p.package && p.package !== p.label ? p.package : ""].filter(Boolean).join(" · ")) : null,
      a.sessions ? h("div", { class: "mm-stats" },
        h("span", null, h("b", null, fmtNum(a.sessions)), " ", tn(a.sessions, "session", "sessions")),
        a.edits ? h("span", null, h("b", null, fmtNum(a.edits)), " ", tn(a.edits, "edit", "edits")) : null,
        a.last ? h("span", null, t("last {when}", { when: ago(a.last) })) : null) : null,
      p.stack.length ? h("div", { class: "mm-chips sy-chips" }, p.stack.map((st) => h("span", { class: "sy-chip" }, st))) : null,
      p.ports.length || p.calls.length ? [h("h4", null, t("Ports")), h("div", { class: "sy-ports" },
        p.ports.map((q) => h("span", { class: "sy-chip mono" }, `:${q.port}`, h("em", null, ` ×${q.n}`))),
        p.calls.length ? h("div", { class: "muted" }, t("agents called it at {paths}", { paths: p.calls.map((c) => `${c.path} ×${c.n}`).join(t(", ")) })) : null)] : null,
      out.length || inc.length ? [h("h4", null, t("Connections")), h("ul", { class: "mm-klist" }, out.map((e) => edgeRow(e, "out")), inc.map((e) => edgeRow(e, "in")))] : null,
      p.evidence.length ? [h("h4", null, t("Why it is here")), h("ul", { class: "mm-klist sy-evlist" }, p.evidence.map(evItem))] : null,
    ];
  }
  function systemHome() {
    const gl = x.links || [];
    return [
      gpath.length ? h("span", { class: "mm-cat neutral" }, icon("projects"), gpath.join(" › ")) : null,
      h("h3", null, x.label),
      h("div", { class: "mono sy-path" }, shortPath(x.path || x.id)),
      h("div", { class: "mm-stats" },
        h("span", null, h("b", null, fmtNum(x.sessions)), " ", tn(x.sessions, "session", "sessions")),
        x.last ? h("span", null, t("last {when}", { when: ago(x.last) })) : null,
        Object.keys(x.agents).length ? h("span", null, Object.keys(x.agents).map(agentName).join(t(", "))) : null),
      h("p", { class: "muted sy-hint" }, t("Click a part to see why it is here: the manifest lines and commands behind it.")),
      gl.length ? [h("h4", null, t("Other systems · {n}", { n: gl.length })), h("div", { class: "mm-uses" }, gl.map((l) => h("div", { class: "mm-use sy-linkrow" },
        h("a", { href: `#/systems?system=${encodeURIComponent(l.direction === "out" ? l.to : l.from)}` }, `${l.direction === "out" ? "→" : "←"} ${l.other}`),
        h("span", { class: `sy-lkind ${l.kind}` }, l.kind === "files" ? t("files") : t("glossary")),
        h("div", { class: "gloss" }, l.evidence.map((e) => e.text).filter(Boolean)[0] || ""))))] : null,
      x.remote_runs?.length ? [h("h4", null, t("Also runs on")), h("ul", { class: "mm-klist" }, x.remote_runs.map((r) => h("li", null, icon("cloud"),
        h("span", null, h("b", null, r.host), ` ${r.path} · `, tn(r.sessions, "{n} session", "{n} sessions", { n: fmtNum(r.sessions) })))))] : null,
      x.folders?.length ? [h("h4", null, t("Where sessions worked")), h("ul", { class: "mm-klist" }, x.folders.slice(0, 8).map((f) => h("li", null,
        h("span", { class: "mm-date" }, fmtNum(f.files)), h("span", { class: "mono" }, f.folder))))] : null,
      x.ports_unplaced?.length ? [h("h4", null, t("Other ports seen")), h("div", { class: "sy-ports" }, x.ports_unplaced.map((q) => h("span", { class: "sy-chip mono" }, `:${q.port}`, h("em", null, ` ×${q.n}`))))] : null,
      x.recent?.length ? [h("h4", null, t("Recent sessions")), h("ul", { class: "mm-klist" }, x.recent.map((r) => h("li", null,
        h("span", { class: "mm-date" }, fmtDate(r.started_at)), h("a", { href: `#/session/${r.id}` }, r.title || r.id.slice(0, 8)))))] : null,
      h("div", { class: "mm-links-row" },
        x.project ? h("a", { class: "btn small", href: `#/project?path=${encodeURIComponent(x.project)}` }, t("Project page")) : null,
        x.git ? h("a", { class: "btn small", href: x.git.url, target: "_blank", rel: "noopener" }, x.git.host.includes("github") ? "GitHub" : x.git.host) : null,
        h("a", { class: "btn small", href: `#/systems?focus=${encodeURIComponent(x.id)}` }, t("All systems"))),
    ];
  }
  const legend = h("div", { class: "mm-legend sy-rolelegend" }, SY_ROLES.filter(([r]) => lay.roles.includes(r)).map(([r, label]) => {
    const e = h("span", null, h("i", { class: "mm-cdot" }), label); e.style.setProperty("--c", SY_HUE[r]); return e;
  }));
  const bounds = { x: -16, y: -16, w: lay.w + 32, h: lay.h + 32 };
  cv.box.append(legend, cv.tools(() => cv.fit(bounds)));
  const page = h("div", { class: "mm-page sy-page" },
    h("div", { class: "page-head" },
      titled("rack", h("div", { class: "muted", style: { fontSize: "12.5px" } }, h("a", { href: "#/systems" }, t("Systems")), gpath.length ? ` / ${gpath.join(" / ")}` : "", " /"),
        h("h1", null, x.label),
        h("div", { class: "sub" }, [tn(x.parts.filter((p) => p.role === "way_in" || p.role === "code").length, "{n} part of its own", "{n} parts of its own", { n: fmtNum(x.parts.filter((p) => p.role === "way_in" || p.role === "code").length) }),
          tn(x.parts.filter((p) => !(p.role === "way_in" || p.role === "code")).length, "{n} thing it ships to, stores in or uses", "{n} things it ships to, stores in or uses", { n: fmtNum(x.parts.filter((p) => !(p.role === "way_in" || p.role === "code")).length) }),
          tn(x.edges.length, "{n} connection", "{n} connections", { n: fmtNum(x.edges.length) })].join(" · "))),
      h("div", { class: "head-actions" },
        x.project ? h("a", { class: "btn", href: `#/project?path=${encodeURIComponent(x.project)}` }, t("Project page")) : null,
        h("a", { class: "btn", href: `#/systems?focus=${encodeURIComponent(x.id)}` }, t("All systems")))),
    x.parts.length ? h("section", { class: "card flush mm-card" }, cv.box, aside)
      : h("div", { class: "card empty" }, t("Nothing to draw yet: no manifests were read and sessions left no trace of parts.")));
  aside.append(...syKids(selected ? partDetail(parts[selected]) : systemHome()));
  paint();
  if (x.parts.length) {
    const ro = new ResizeObserver(() => {
      if (!cv.box.clientWidth) return;
      if (!cv.cam.placed) { cv.cam.placed = true; cv.fit(bounds, 1.05, false); }
      else cv.apply();
    });
    ro.observe(cv.box);
  }
  return page;
}

// =====================================================================================
// Sources: which agents are connected
// =====================================================================================
const srcOpen = new Set(); // rows the user opened; kept across re-renders (connect, disconnect, import)
let srcTouched = false;
function srcRow(key, summary, body, { open = false } = {}) { // one expandable row of a Sources list
  const d = h("details", { class: "src-row", open: srcTouched ? srcOpen.has(key) : open },
    h("summary", null, h("span", { class: "chev", "aria-hidden": "true" }, "›"), summary), h("div", { class: "src-body" }, body));
  if (!srcTouched && open) srcOpen.add(key);
  d.addEventListener("toggle", () => { srcTouched = true; if (d.open) srcOpen.add(key); else srcOpen.delete(key); });
  return d;
}
const inSummary = (fn) => (e) => { e.preventDefault(); e.stopPropagation(); fn(e); }; // a button in a row's summary does not toggle it
const stateBadge = (cls, text) => h("span", { class: `badge ${cls}` }, h("span", { class: "sdot" }), text);
const listSection = (title, hint, tools, rows) => h("section", { class: "card src-list" },
  h("div", { class: "src-list-head" }, h("div", null, h("h2", null, title), hint ? h("div", { class: "muted" }, hint) : null), tools), rows);
function mcpClientRow(c) { // another MCP client (Claude Desktop, Cursor, …): one line, Add or Remove
  const btn = h("button", { class: `btn small${c.registered ? "" : " primary"}`, type: "button", disabled: !c.registered && !c.detected,
    onclick: async () => {
      btn.disabled = true;
      const r = await post(`/api/connectors/${c.name}/${c.registered ? "disconnect" : "connect"}`);
      toast((r.actions || [r.error]).join(" · "), 7000);
      render();
    } }, c.registered ? t("Remove") : t("Add"));
  const state = c.registered ? ["good", t("Added")] : c.detected ? ["", t("Detected")] : ["", t("Not installed")];
  return h("div", { class: "src-row flat" }, h("div", { class: "src-line" },
    h("span", { class: "chev", "aria-hidden": "true" }), h("span", { "aria-hidden": "true" }),
    h("span", { class: "src-title" }, h("b", null, c.label), h("small", null, `${c.vendor} · ${shortPath(c.config)}`)),
    stateBadge(state[0], state[1]), btn));
}
route(/^\/sources$/, async () => {
  const [sources, imports] = await Promise.all([api("/api/connectors"), api("/api/imports")]);
  const formats = Object.entries(imports).map(([key, f]) => ({ key, ...f }));
  const lastOf = (f) => f.last_import;
  const picker = h("input", { type: "file", accept: ".zip,.json,application/zip", hidden: true, onchange: async () => {
    const file = picker.files[0];
    if (!file) return;
    importBtn.disabled = true; importBtn.textContent = t("Uploading {size}B…", { size: fmtCompact(file.size) });
    const res = await fetch("/api/import", { method: "POST", headers: { "X-Interlatch": "1", "X-Interlatch-Lang": LANG, "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) }, body: file });
    const r = await res.json().catch(() => ({}));
    importBtn.disabled = false; importBtn.textContent = t("Import export…"); picker.value = "";
    if (!r.started) { toast(r.error || t("An import is already running")); return; }
    toast(t("Importing chats…"), 5000);
    watchJob("import");
  } });
  const importBtn = h("button", { class: "btn primary small", type: "button", onclick: () => picker.click() }, t("Import export…"));
  const stat = (n, label) => h("span", null, tx("{n} {label}", { n: h("b", null, typeof n === "number" ? fmtNum(n) : n), label }));
  const badge = stateBadge;

  // ---- coding agents
  const agentRow = (c) => {
    const issues = c.checks.filter((k) => k.ok === false && !k.optional);
    const state = c.connected ? (issues.length ? ["warning", tn(issues.length, "{n} issue", "{n} issues")] : ["good", t("Connected")])
      : c.detected ? ["warning", t("Not connected")] : ["", t("Not installed")];
    const connect = async (e) => {
      e.target.disabled = true; e.target.textContent = t("Connecting…");
      const r = await post(`/api/connectors/${c.name}/connect`);
      toast((r.actions || [r.error]).join(" · ") + (r.sync_started ? ` · ${t("syncing now")}` : ""), 7000);
      if (r.sync_started) watchJob("sync");
      render();
    };
    const action = c.connected
      ? h("button", { class: "btn small danger", type: "button", onclick: async (e) => {
          if (!confirm(t("Stop recording {agent}? Recorded sessions stay in the vault.", { agent: c.label }))) return;
          e.target.disabled = true;
          const r = await post(`/api/connectors/${c.name}/disconnect`);
          toast((r.actions || [r.error]).join(" · "), 6000);
          render();
        } }, t("Disconnect"))
      : h("button", { class: "btn small primary", type: "button", disabled: !c.detected, onclick: connect }, t("Connect {agent}", { agent: c.label }));
    const summary = [
      h("span", { class: `src-dot a-${c.name}`, "aria-hidden": "true" }),
      h("span", { class: "src-title" }, h("b", null, c.label), h("small", null, [c.vendor, c.version].filter(Boolean).join(" · "))),
      h("span", { class: "src-sum" }, c.connected || c.recorded.sessions
        ? [stat(c.recorded.sessions, t("recorded")), h("span", null, tx("last {when}", { when: h("b", null, c.recorded.last_session ? ago(c.recorded.last_session) : t("never")) }))]
        : h("span", null, c.detected ? t("found on this Mac") : "")),
      !c.connected && c.detected ? h("button", { class: "btn small primary", type: "button", onclick: inSummary(connect) }, t("Connect")) : badge(state[0], state[1])];
    const body = [
      h("div", { class: "src-stats" },
        stat(c.on_disk, c.on_disk_label || t("on disk")), stat(c.recorded.sessions, t("recorded")), stat(c.recorded.analyzed, t("analyzed")),
        c.recovered ? stat(c.recovered, c.name === "codex" ? t("Claude sessions recovered") : t("recovered")) : null,
        c.binary ? h("span", { class: "muted" }, shortPath(c.binary)) : null),
      h("div", { class: "checks" }, c.checks.map((k) => h("div", { class: "check" },
        h("span", { class: `mark ${k.ok ? "ok" : k.ok === null || k.optional ? "na" : "no"}` }, k.ok ? "✓" : k.ok === null || k.optional ? "–" : "✗"),
        h("div", null, h("div", null, k.label), h("div", { class: "d" }, k.detail))))),
      c.notes.length ? h("div", { class: "src-note" }, c.notes.join(" · ")) : null,
      h("div", { class: "src-foot" }, h("span", { class: "muted" }, t("Recording: {mode}", { mode: c.recording })),
        h("div", { class: "src-actions" }, c.recorded.sessions ? h("a", { class: "btn small", href: `#/sessions?agent=${c.agent || c.name}` }, t("Sessions")) : null, action))];
    return srcRow(`agent:${c.name}`, summary, body, { open: issues.length > 0 && c.connected });
  };
  const connected = sources.filter((c) => c.connected).length;

  // ---- chat exports
  const HOW = { "claude-ai": "claude.ai › Settings › Privacy › Export data", chatgpt: "ChatGPT › Settings › Data controls › Export data" };
  const formatRow = (f) => {
    const last = lastOf(f), sc = f.screen || {};
    const screened = (sc.analyze || 0) + (sc.maybe || 0) + (sc.skip || 0);
    const verdict = (v, n, label) => h("a", { href: `#/sessions?agent=${f.agent}&screen=${v}`, title: t("Show these chats") }, tx("{n} {label}", { n: h("b", null, fmtNum(n)), label }));
    const screenBtn = h("button", { class: `btn small${sc.to_queue ? "" : " primary"}`, type: "button", onclick: async () => {
      screenBtn.disabled = true; screenBtn.textContent = t("Starting…");
      const r = await post("/api/screen", { source: f.key });
      if (!r.started) { toast(r.error || t("Screening is already running")); render(); return; }
      toast(t("Screening {n} {source} chats; the status bar shows progress", { n: fmtNum(sc.unscreened), source: f.label }), 6000);
      watchJob("screen");
    } }, tn(sc.unscreened, "Screen {n} chat", "Screen {n} chats", { n: fmtNum(sc.unscreened) }));
    const queueBtn = h("button", { class: "btn small primary", type: "button", onclick: async () => {
      if (!confirm(t("Queue {n} chats for analysis? The background agent analyzes a few every 15 minutes, newest first, on your {agent} login.", { n: fmtNum(sc.to_queue), agent: analyzer() }))) return;
      queueBtn.disabled = true;
      const r = await post("/api/screen/queue", { source: f.key });
      toast(r.queued ? t("Queued {n} chats: {per} are analyzed every 15 minutes, newest first", { n: fmtNum(r.queued), per: fmtNum(r.per_run) }) : t("Nothing to queue"), 7000);
      render();
    } }, t("Queue {n} worth analyzing", { n: fmtNum(sc.to_queue) }));
    const summary = [
      h("span", { class: `src-dot a-${f.agent}`, "aria-hidden": "true" }),
      h("span", { class: "src-title" }, h("b", null, f.label), h("small", null, t("from a data export"))),
      h("span", { class: "src-sum" }, f.sessions ? [stat(f.sessions, t("chats")), screened ? stat(sc.analyze, t("worth analyzing")) : null,
        h("span", null, tx("imported {when}", { when: h("b", null, last ? ago(last.at) : t("never")) }))] : h("span", null, t("nothing imported yet"))),
      f.sessions ? badge("good", t("Imported")) : badge("", t("Not imported"))];
    const body = [
      h("div", { class: "src-stats" }, stat(f.sessions, t("chats")), stat(f.analyzed, t("analyzed")),
        sc.queued ? stat(sc.queued, t("queued")) : null,
        last ? h("span", { class: "muted" }, t("last: {file}, {new} new, {updated} updated", { file: last.file, new: fmtNum(last.new), updated: fmtNum(last.updated) })) : null),
      screened ? h("div", { class: "src-stats screen-stats" }, h("span", { class: "muted" }, t("Screened:")),
        verdict("analyze", sc.analyze, t("worth analyzing")), verdict("maybe", sc.maybe, t("maybe")), verdict("skip", sc.skip, t("not worth it")),
        sc.unscreened ? verdict("none", sc.unscreened, t("not screened")) : null) : null,
      h("div", { class: "src-note" }, tx("Export from {where}; the email's link downloads a .zip to import here or with {command}. Import newer exports any time: only new and changed chats are added.",
        { where: h("b", null, HOW[f.agent] || f.label), command: h("code", null, "interlatch import <zip>") })),
      h("div", { class: "src-foot" }, h("span", { class: "muted" }, t("Not analyzed automatically. Screening reads only each chat's opening (title, first and last prompt) to sort out the ones worth analyzing; nothing is analyzed until you queue them.")),
        h("div", { class: "src-actions" }, f.sessions ? h("a", { class: "btn small", href: `#/sessions?agent=${f.agent}` }, t("Sessions")) : null,
          sc.unscreened ? screenBtn : null, sc.to_queue ? queueBtn : null))];
    return srcRow(`chat:${f.key}`, summary, body, { open: !!(sc.unscreened || sc.to_queue) });
  };

  const section = listSection;
  return h("div", { class: "narrow-page wide" },
    h("div", { class: "page-head" }, titled("tink", h("h1", null, t("Sources")),
      h("div", { class: "sub" }, tx("The coding agents Interlatch records. Connecting starts archiving and analyzing their sessions and gives the agent Interlatch's MCP tools. To give other tools the MCP server, see {link}.",
        { link: h("a", { href: "#/mcp" }, "MCP") })))),
    section(t("Coding agents"), t("{a} of {b} connected · click a row for its checks", { a: connected, b: sources.length }), null, sources.map(agentRow)),
    section(t("Chat exports"), t("Chats on claude.ai and chatgpt.com are not stored on your Mac, so they come in from a data export."),
      h("div", { class: "src-actions" }, importBtn, picker), formats.map(formatRow)),
  );
});

// =====================================================================================
// MCP: which agents can search Interlatch, and how to add it to any other
// =====================================================================================
const MCP_SETUPS = [ // [key, tab label, where it goes, snippet key]
  ["json", t("Most clients"), t("Claude Desktop, Cursor, Windsurf, Gemini CLI, Cline, Zed and most others: add to the client's MCP config (an mcpServers object)."), "json"],
  ["vscode", "VS Code", t("VS Code (Copilot Chat): the user mcp.json (⌘⇧P › MCP: Open User Configuration) or .vscode/mcp.json in a workspace."), "vscode"],
  ["codex", "Codex", t("OpenAI Codex: ~/.codex/config.toml."), "codex"],
  ["claude", "Claude Code", t("Claude Code: run this in a terminal (user scope: every project)."), "claude"],
];
const MCP_EXAMPLES = [t("Have we solved this before? The build fails with …"), t("How is this project deployed?"),
  t("What did I decide about … last month, and why?"), t("What does the term … mean here?"), t("Summarize what I worked on this week.")];
async function copyText(text, btn) {
  let ok = true;
  try { await navigator.clipboard.writeText(text); } catch (e) {
    const ta = h("textarea", { style: { position: "fixed", opacity: "0" } }, text); document.body.append(ta); ta.select();
    try { ok = document.execCommand("copy"); } catch (e2) { ok = false; } ta.remove();
  }
  if (!ok) { toast(t("Could not copy here: select the text and copy it yourself."), 6000); return; }
  const was = btn.textContent; btn.textContent = t("Copied"); setTimeout(() => (btn.textContent = was), 1500);
}
let mcpTab = "json";
route(/^\/mcp$/, async () => {
  const [info, sources, clients] = await Promise.all([api("/api/mcp"), api("/api/connectors"), api("/api/mcp-clients")]);
  // ---- coding agents: connecting one in Sources also gives it the MCP server
  // each agent's MCP checks, by their stable key (the server translates the labels)
  const agentRows = sources.map((c) => [c, c.checks.filter((k) => k.key === "mcp")]).filter(([, ks]) => ks.length).map(([c, ks]) => {
    const on = ks.filter((k) => k.ok).length;
    const state = on === ks.length ? ["good", t("Added")] : on ? ["warning", t("{a} of {b}", { a: on, b: ks.length })] : ["", c.detected ? t("Not added") : t("Not installed")];
    return h("div", { class: "src-row flat" }, h("div", { class: "src-line" },
      h("span", { class: "chev", "aria-hidden": "true" }), h("span", { class: `src-dot a-${c.name}`, "aria-hidden": "true" }),
      h("span", { class: "src-title" }, h("b", null, c.label), h("small", null, ks.length > 1 ? ks.map((k) => k.label.replace(/^MCP server in /, "").replace(/ の MCP サーバー$/, "")).join(" · ") : c.vendor)),
      stateBadge(state[0], state[1]),
      on === ks.length ? null : h("a", { class: "btn small", href: "#/sources" }, c.connected ? t("Fix in Sources") : t("Connect"))));
  });
  // ---- manual setup, one tab per config format
  const code = h("pre", { class: "mcp-code" });
  const where = h("div", { class: "muted mcp-where" });
  const copyBtn = h("button", { class: "btn small", type: "button", onclick: () => copyText(info.snippets[MCP_SETUPS.find((x) => x[0] === mcpTab)[3]], copyBtn) }, t("Copy"));
  const tabs = h("div", { class: "seg", role: "tablist" });
  const showTab = (key) => {
    mcpTab = key;
    const [, , hint, snip] = MCP_SETUPS.find((x) => x[0] === key);
    code.textContent = info.snippets[snip];
    where.textContent = hint;
    tabs.querySelectorAll("button").forEach((b) => { const on = b.dataset.key === key; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); });
  };
  tabs.append(...MCP_SETUPS.map(([key, label]) => h("button", { type: "button", role: "tab", "data-key": key, onclick: () => showTab(key) }, label)));
  showTab(mcpTab);
  const toolRows = info.tools.map((t) => h("div", { class: "mcp-tool" },
    h("div", null, h("code", null, t.name), t.params.length ? h("span", { class: "muted" }, ` (${t.params.map((p) => (t.required.includes(p) ? p : p + "?")).join(", ")})`) : null),
    h("div", { class: "muted" }, t.description)));
  return h("div", { class: "narrow-page wide" },
    h("div", { class: "page-head" }, titled("plug", h("h1", null, "MCP"),
      h("div", { class: "sub" }, t("Interlatch's MCP server lets an agent search your past sessions, knowledge and glossary while it works. It runs on your Mac, only reads, and needs no network.")))),
    listSection(t("Coding agents"), tx("Connecting an agent in {link} also gives it the MCP server.", { link: h("a", { href: "#/sources" }, t("Sources")) }), null, agentRows),
    listSection(t("Other MCP clients"), t("Not recorded: they only get the MCP server. Add writes Interlatch into the client's own config (backed up first); restart the client to load it."),
      null, clients.map(mcpClientRow)),
    h("section", { class: "card mcp-card" },
      h("div", { class: "card-head" }, h("h2", null, t("Add it to any other client")), copyBtn),
      h("div", { class: "muted", style: { fontSize: "12.5px", marginBottom: "10px" } },
        tx("Interlatch speaks MCP over stdio: the client starts {command} and talks to it. No port, token or environment variable is needed.",
          { command: h("span", { class: "codeline" }, [info.command, ...info.args].join(" ")) })),
      tabs, where, code),
    h("section", { class: "card mcp-card" },
      h("div", { class: "card-head" }, h("h2", null, t("Tools")), h("span", { class: "hint" }, t("all read-only"))),
      h("div", { class: "mcp-tools" }, toolRows),
      h("div", { class: "subhead" }, t("Try asking")),
      h("ul", { class: "bullets" }, MCP_EXAMPLES.map((x) => h("li", null, x))),
      h("div", { class: "muted mcp-note" }, t("Answers join the client's conversation, so they reach that client's model; secrets are redacted as in analysis digests. Give the server only to clients whose model provider you trust with your sessions."))));
});

// =====================================================================================
// Weekly reviews
// =====================================================================================
// One week at a time: the numbers as charts, the model's words as short lists, the long write-up folded away
const weekShort = (period) => t("W{n}", { n: period.replace(/^\d+-W0?/, "") });
function weekRange(r) {
  if (!r.start || !r.end) return "";
  return `${fmtDate(r.start)} – ${fmtDateY(new Date(new Date(r.end).getTime() - 86400000).toISOString())}`;
}
function weekDelta(cur, prev) {
  if (!prev) return null;
  const pct = ((cur - prev) / prev) * 100;
  if (!isFinite(pct) || Math.abs(pct) < 1) return t("same as the week before");
  return `${pct > 0 ? "▲" : "▼"} ${t("{pct}% vs the week before", { pct: Math.abs(pct).toFixed(0) })}`;
}
function clampList(items, { limit = 3, cls = "", render } = {}) { // short bullets; each opens to its full text, the rest behind "+N more"
  const li = (x) => {
    const el = h("li", { class: cls, tabindex: "0", "aria-expanded": "false" }, render ? render(x) : h("span", null, x)); // no term underlines: too busy in a short list
    const toggle = () => el.setAttribute("aria-expanded", String(el.classList.toggle("open")));
    el.addEventListener("click", (e) => { if (!e.target.closest("a, .gterm")) toggle(); });
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    return el;
  };
  const list = h("ul", { class: "rv-items" }, items.slice(0, limit).map(li));
  if (items.length <= limit) return list;
  const more = h("button", { class: "link-btn", type: "button", onclick: () => {
    items.slice(limit).forEach((x) => list.append(li(x)));
    more.remove();
  } }, t("+{n} more", { n: items.length - limit }));
  return h("div", null, list, more);
}
function reviewView(r) {
  const rv = r.review || {}, st = r.stats || {}, prev = r.previous || {}, gl = r.glance;
  const days = weekDays(r.start);
  const hero = h("section", { class: "card rv-hero" },
    h("div", { class: "rv-kicker" }, icon("calendar"), t("Week {n}", { n: r.period.split("-W")[1].replace(/^0/, "") }), h("span", null, weekRange(r))),
    h("h2", { class: "rv-headline gloss" }, rv.headline || t("Week {period}", { period: r.period })),
    rv.tldr && rv.tldr.length ? h("ul", { class: "rv-tldr" }, rv.tldr.map((x) => h("li", { class: "gloss" }, x))) : null);
  const kpis = h("div", { class: "tiles rv-kpis" },
    tile(t("Active time"), fmtHours(st.active_s), { iconName: "clock", delta: weekDelta(st.active_s, prev.active_s) }),
    tile(t("Sessions"), fmtNum(st.sessions), { iconName: "sessions", delta: weekDelta(st.sessions, prev.sessions) }),
    tile(t("Projects"), fmtNum(st.projects), { iconName: "projects", delta: gl && gl.projects[0] ? t("most time: {name}", { name: gl.projects[0].label }) : null }),
    tile(t("Lines added"), `+${fmtCompact(st.lines_added)}`, { iconName: "diff", delta: weekDelta(st.lines_added, prev.lines_added) }),
    tile(t("Est. API cost"), fmtCost(st.cost), { iconName: "cost", delta: weekDelta(st.cost, prev.cost), title: t("API list-price equivalent; subscriptions are billed differently") }));
  const kinds = gl ? Object.keys(KIND).filter((x) => gl.knowledge[x]).sort((a, b) => gl.knowledge[b] - gl.knowledge[a]) : [];
  const charts = gl ? h("div", { class: "rv-charts" },
    h("section", { class: "card" }, cardHead(t("Day by day"), { iconName: "calendar", hint: t("active time") }), dayBars(gl.daily, days, { height: 170 })),
    h("section", { class: "card" }, cardHead(t("Where the time went"), { iconName: "projects", hint: t("active time · sessions") }),
      hbars(gl.projects, { label: (x) => x.label, value: (x) => x.active_s, fmt: fmtDur, sub: (x) => `· ${x.sessions}`,
        href: (x) => `#/project?path=${encodeURIComponent(x.path)}` })),
    h("section", { class: "card" }, cardHead(t("How it went"), { iconName: "completed", hint: t("session outcomes") }),
      miniOutcomes(gl.outcomes) || h("div", { class: "empty" }, t("No sessions")),
      kinds.length ? [h("div", { class: "subhead" }, t("Knowledge captured")),
        h("div", { class: "rv-kinds" }, kinds.map((x) => h("a", { class: "kind-chip", href: `#/knowledge/all?kind=${x}` }, icon(x), kindPlural(x), h("b", null, fmtNum(gl.knowledge[x])))))] : null)) : null;
  const themes = rv.themes && rv.themes.length ? h("div", { class: "rv-themes" }, rv.themes.map((t) => {
    const el = h("div", { class: "card rv-theme", tabindex: "0" },
      h("b", { class: "gloss" }, t.title),
      t.projects && t.projects.length ? h("div", { class: "rv-tags" }, t.projects.map((p) => h("span", { class: "tag" }, p))) : null,
      t.detail ? h("p", { class: "gloss" }, t.detail) : null);
    el.addEventListener("click", (e) => { if (!e.target.closest("a, .gterm")) el.classList.toggle("open"); });
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.classList.toggle("open"); } });
    return el;
  })) : null;
  const LISTS = [["accomplishments", t("Shipped"), "completed", "good"], ["learnings", t("Learned"), "learning", "accent"],
    ["open_threads", t("Still open"), "todo", "warning"], ["friction", t("Slowed you down"), "gotcha", "serious"], ["suggestions", t("Try next"), "sparkles", "accent"],
    ["overturned", t("Overturned"), "gotcha", "warning"]]; // trusted knowledge a newer session replaced
  const lists = LISTS.filter(([key]) => rv[key] && rv[key].length);
  const spans = { 1: [6], 2: [3, 3], 3: [2, 2, 2], 4: [3, 3, 3, 3], 5: [2, 2, 2, 3, 3], 6: [2, 2, 2, 2, 2, 2] }[lists.length] || [];
  const listGrid = lists.length ? h("div", { class: "rv-lists" }, lists.map(([key, title, ic, tone], i) =>
    h("section", { class: `card rv-list t-${tone}`, style: { "--span": String(spans[i] || 2) } },
      h("div", { class: "rv-list-head" }, icon(ic), h("h3", null, title), h("span", null, fmtNum(rv[key].length))),
      clampList(rv[key])))) : null;
  const full = rv.summary ? h("details", { class: "card rv-full" }, h("summary", null, t("Read the full write-up")), mdEl(rv.summary)) : null;
  const legacy = !rv.headline && !rv.summary && r.markdown ? h("section", { class: "card" }, mdEl(r.markdown)) : null; // written before reviews were stored as data
  return h("div", { class: "rv" }, hero, kpis, charts,
    themes ? [h("h3", { class: "rv-section" }, t("Themes")), themes] : null,
    listGrid, full, legacy);
}
route(/^\/reviews$/, async (params) => {
  const data = await api("/api/reviews");
  const btn = (label, week, primary) => {
    const b = h("button", { class: `btn admin-only${primary ? " primary" : ""}`, type: "button", onclick: async () => {
      b.disabled = true;
      const r = await post("/api/review", { week });
      toast(r.started ? t("{agent} is writing the review…", { agent: analyzerShort() }) : t("Already running"));
      watchJob(`review:${week || "last"}`);
    } }, label);
    return b;
  };
  const has = (p) => data.items.some((r) => r.period === p);
  const pick = data.items.find((r) => r.period === params.week) || data.items[0];
  const weeks = data.items.length > 1 ? h("div", { class: "rv-weeks", role: "tablist", "aria-label": t("Week") }, data.items.map((r) =>
    h("a", { class: r === pick ? "on" : "", href: `#/reviews?week=${r.period}`, role: "tab", "aria-selected": String(r === pick), title: weekRange(r) },
      h("b", null, weekShort(r.period)), h("span", null, r.start ? fmtDate(r.start) : r.period.slice(0, 4))))) : null;
  return h("div", null,
    h("div", { class: "page-head" }, titled("camera", h("h1", null, t("Weekly reviews")),
      h("div", { class: "sub" }, data.auto_ready ? t("Review of {week} will be written on the next background run", { week: data.last_week })
        : has(data.last_week) ? t("A new review is written automatically each week once its sessions are analyzed")
        : t("Written automatically once a week's sessions are analyzed ({week}: {note})", { week: data.last_week, note: data.auto_note }))),
      h("div", { class: "head-actions" }, btn(has(data.last_week) ? t("Rewrite {week}", { week: weekShort(data.last_week) }) : t("Write {week}", { week: weekShort(data.last_week) }), data.last_week, !has(data.last_week)),
        btn(t("This week so far"), data.current_week))),
    weeks,
    pick ? reviewView(pick) : h("div", { class: "card empty" }, t("No reviews yet. One is written automatically once a week's sessions are analyzed, or write one now.")));
});

// =====================================================================================
// Status
// =====================================================================================
function updatesCard() {
  const box = h("section", { class: "card", id: "updates" }, h("div", { class: "card-head" }, h("h2", null, t("Updates"))), h("div", { class: "muted" }, t("Loading…")));
  const draw = (u) => {
    const online = !u.source && u.kind !== "source"; // PyPI installs and the app compare against the latest release
    box.classList.toggle("update-ready", !!u.available);
    const state = u.error ? h("div", { style: { color: "var(--critical-ink)" } }, u.error)
      : u.available ? h("div", { class: "upd-headline" }, icon("sync"), h("b", null, u.local ? checkoutHeadline(u) : t("Interlatch {version} is available", { version: u.latest })),
        u.notes_url ? h("a", { href: u.notes_url, target: "_blank", rel: "noopener" }, t("What's new")) : null)
      : online && !u.checked_at ? h("div", { class: "muted" }, u.check_daily ? t("Checking pypi.org for the latest version…") : t("Not checked yet. Checking asks pypi.org for the latest version."))
      : online ? h("div", null, t("You're on the latest version (checked {ago})", { ago: ago(new Date(u.checked_at * 1000).toISOString()) })) : null;
    const check = online ? h("button", { class: "btn admin-only", type: "button", onclick: async () => {
      check.disabled = true; check.textContent = t("Checking…");
      const r = await post("/api/update/check");
      draw(r);
      pollStatus(); // the status bar and the notification pick up what the check found
    } }, t("Check for updates")) : null;
    const label = u.local ? t("Reinstall from checkout") : u.source ? t("Reinstall") : t("Update to {version}", { version: u.latest });
    const run = u.can_update && (u.available || u.source) ? h("button", { class: `btn admin-only${u.available ? " primary" : ""}`, type: "button", onclick: async () => {
      run.disabled = true; run.textContent = t("Updating…");
      const r = await post("/api/update");
      if (!r.started) { toast(r.error || t("An update is already running")); run.disabled = false; run.textContent = label; return; }
      try { sessionStorage.setItem("interlatch-updating", u.current); } catch (e) { /* private mode */ }
      toast(u.restartable ? t("Updating Interlatch; the dashboard restarts when it is done") : t("Updating Interlatch…"), 6000);
      if (u.restartable) restartAfter.add("update");
      watchJob("update");
    } }, label) : null;
    const setting = (key, path, label) => {
      const sw = h("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(!!u[key]), "aria-label": label, disabled: !canAdmin(),
        onclick: async () => {
          sw.disabled = true;
          const r = await post(path, { on: !u[key] });
          if (r.error) { toast(r.error); sw.disabled = false; return; }
          draw(r);
          if (key === "check_daily" && r.check_daily && !r.checked_at) setTimeout(async () => { draw(await api("/api/update")); pollStatus(); }, 4000); // the first check runs now
        } });
      return sw;
    };
    const daily = online ? setting("check_daily", "/api/update/daily", t("Check for updates daily")) : null;
    const notify = online ? setting("notify", "/api/update/notify", t("Notify me about new versions")) : null;
    const download = u.kind === "app" && u.available ? h("a", { class: "btn primary", href: u.releases_url, target: "_blank", rel: "noopener" }, t("Download {version}", { version: u.latest })) : null;
    box.replaceChildren(h("div", { class: "card-head" }, h("h2", null, t("Updates")), h("div", { class: "tools" }, check, run, download)),
      h("div", { class: "status-list" },
        h("div", null, `Interlatch ${u.current} · ${u.method}`, u.installed_at ? h("span", { class: "muted" }, ` · ${t("installed {ago}", { ago: ago(new Date(u.installed_at * 1000).toISOString()) })}`) : null),
        state,
        u.changes ? changesList(u.changes) : null,
        u.note ? h("div", { class: "muted" }, u.note) : null,
        daily ? h("div", { class: "set-row upd-daily" }, h("div", null, h("b", null, t("Check for updates daily")),
          h("div", { class: "muted" }, t("Asks pypi.org for the latest version number once a day while Interlatch is open. Sends nothing about you."))), daily) : null,
        notify ? h("div", { class: "set-row upd-daily" }, h("div", null, h("b", null, t("Notify me about new versions")),
          h("div", { class: "muted" }, t("A desktop notification when a new version is out, even with the dashboard closed: the background sync asks pypi.org once a day. Sends nothing about you."))), notify) : null,
        u.command && (u.available || u.source) ? h("div", { class: "muted" }, u.restartable
          ? tx("Runs {command}, then restarts the dashboard.", { command: h("span", { class: "codeline" }, u.command) })
          : tx("Runs {command}. Quit and reopen Interlatch afterwards.", { command: h("span", { class: "codeline" }, u.command) })) : null));
    if (parseHash().params.focus === "updates") { // from the notification or the status bar: show this card, once
      setParams({});
      requestAnimationFrame(() => box.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" }));
      box.classList.add("flash");
    }
  };
  api("/api/update").then(draw).catch((e) => box.replaceChildren(h("div", { class: "card-head" }, h("h2", null, t("Updates"))), h("div", { style: { color: "var(--critical-ink)" } }, e.message)));
  return box;
}
function checkoutHeadline(u) { // a checkout's version number often stays put while its code moves on
  const n = u.changes?.commits?.length || 0;
  if (u.latest && u.latest !== u.current) return t("Your checkout is at {latest}; this install is {current}", { latest: u.latest, current: u.current });
  return n ? tn(n, "Your checkout has {n} new commit since this install", "Your checkout has {n} new commits since this install", { n: `${n}${n >= 30 ? "+" : ""}` })
    : t("Your checkout's files have changed since this install");
}
const commitRow = (x) => h("li", null, h("code", null, x.sha), h("span", { title: x.subject }, x.subject), h("span", { class: "muted" }, ago(new Date(x.at * 1000).toISOString())));
function changesList(c) { // what a checkout reinstall brings in: commits since the install, then the changed files
  const files = c.files || [], commits = c.commits || [];
  const shown = files.slice(0, 12);
  return h("div", { class: "upd-changes" },
    commits.length ? [h("div", { class: "subhead" }, t("Commits")),
      h("ul", { class: "upd-commits" }, commits.slice(0, 8).map(commitRow)),
      commits.length > 8 ? h("details", null, h("summary", null, t("Show {n} more", { n: commits.length - 8 })), h("ul", { class: "upd-commits" }, commits.slice(8).map(commitRow))) : null] : null,
    files.length ? h("details", { open: !commits.length }, h("summary", null, tn(files.length, "{n} changed file", "{n} changed files") + (commits.length ? "" : t(" (not committed yet)"))),
      h("ul", { class: "upd-files" }, shown.map((f) => h("li", null, h("span", { class: "codeline" }, f))),
        files.length > shown.length ? h("li", { class: "muted" }, t("and {n} more", { n: files.length - shown.length })) : null)) : null);
}
// What analyzes sessions: a coding agent installed here (Claude Code and Codex through the user's own login, IBM Bob
// with a Bob API key), or a model provider's API (providers.py). An older server sends no "kind": every choice is an agent.
let paneTab = null; // the tab open while it isn't what analyzes now: an agent's name, or "api"
let apiPane = null; // the provider whose settings are open on the API tab
const providerModels = {}; // provider -> model ids its endpoint offers, fetched once per page load
const CLAUDE_MODELS = ["sonnet", "opus", "haiku", "fable", "claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5", "claude-fable-5-1"];
const EFFORTS = ["low", "medium", "high", "xhigh", "max"];
function analyzerPicker(a) {
  const choices = a?.choices || [];
  const current = choices.find((c) => c.name === a.backend);
  const agents = choices.filter((c) => c.kind !== "api"), apis = choices.filter((c) => c.kind === "api");
  const home = current?.kind === "api" ? "api" : current?.name; // the tab of what analyzes now
  const tab = paneTab || home;
  const open = (name) => { paneTab = name === home ? null : name; render(); };
  const use = async (name) => {
    const r = await post("/api/analysis/backend", { backend: name });
    if (r.error) { toast(r.error); return; }
    paneTab = null;
    apiPane = null;
    toast(t("Sessions are now analyzed with {agent}.", { agent: choices.find((c) => c.name === name)?.label }));
    render();
  };
  // a green dot on the tab that analyzes now; a tab whose agent isn't installed here says so
  const tag = (inUse, missing) => inUse ? h("span", { class: "seg-dot", role: "img", "aria-label": t("In use"), title: t("In use") })
    : missing ? h("span", { class: "seg-tag" }, t("Not installed")) : null;
  const tabs = [...agents.map((c) =>
    h("button", { type: "button", role: "radio", class: tab === c.name ? "on" : "", "aria-checked": String(tab === c.name),
      disabled: (!c.path || !canAdmin()) && c.name !== a.backend, title: c.path ? `${c.path} · ${c.model}` : t("{agent} is not installed", { agent: c.label }),
      onclick: () => open(c.name) }, c.label, tag(c.name === a.backend, !c.path))),
    apis.length ? h("button", { type: "button", role: "radio", class: tab === "api" ? "on" : "", "aria-checked": String(tab === "api"),
      disabled: !canAdmin() && tab !== "api", onclick: () => { if (tab !== "api") { apiPane = apiPane || "anthropic"; open("api"); } } },
      t("API provider"), tag(home === "api", false)) : null];
  const agent = agents.find((c) => c.name === tab);
  return h("div", { class: "analyzer" },
    h("div", { class: "seg", role: "radiogroup", "aria-label": t("Analyzed by") }, tabs),
    tab === "api" ? providerPane(apis, current, use) : agent ? agentPane(agent, current, use) : null);
}
const engineBadge = (inUse, ready) => inUse ? stateBadge("accent", t("In use")) : ready ? stateBadge("good", t("Ready"))
  : stateBadge("warning", t("Not set up"));
// A coding agent's settings: Claude Code's models (sessions, knowledge bases, screening) and effort, Codex's model and
// effort, IBM Bob's API key (Bob picks its own model)
function agentPane(c, current, use) {
  const s = c.settings || {}, admin = canAdmin(), inUse = current?.name === c.name, key = c.key;
  const field = (k, label, attrs = {}) => h("label", null, label, h("input", { class: "input", name: k, value: s[k] ?? "", autocomplete: "off",
    spellcheck: "false", disabled: !admin, ...attrs }));
  const effort = () => h("label", null, t("Effort"), h("select", { name: "effort", disabled: !admin },
    EFFORTS.map((e) => h("option", { value: e, selected: e === (s.effort || "medium") }, e))));
  const fields = [];
  if (c.name === "claude") {
    fields.push(h("datalist", { id: "agent-models-claude" }, CLAUDE_MODELS.map((m) => h("option", { value: m }))),
      field("model", t("Model (sessions)"), { list: "agent-models-claude", placeholder: "sonnet" }),
      field("synthesis_model", t("Knowledge bases"), { list: "agent-models-claude", placeholder: "sonnet" }),
      field("screen_model", t("Screening imported chats"), { list: "agent-models-claude", placeholder: "haiku" }),
      effort());
  } else if (c.name === "codex") {
    fields.push(field("model", t("Model"), { placeholder: t("Codex's default") }), effort());
  }
  const keyInput = key ? h("input", { class: "input", name: "key", type: "password", autocomplete: "new-password", disabled: !admin,
    placeholder: key.source === "stored" ? t("stored; type to replace it") : key.source ? t("from {variable}", { variable: key.source }) : t("required") }) : null;
  if (key) fields.push(h("label", { class: "wide" }, t("API key ({variable})", { variable: key.env }), h("div", { class: "pv-key" }, keyInput,
    key.source === "stored" && admin ? h("button", { class: "btn small", type: "button", onclick: async () => {
      if (await save({ key: "" })) { paneTab = c.name === current?.name ? null : c.name; toast(t("API key removed.")); render(); }
    } }, t("Remove key")) : null)));
  const form = fields.length ? h("form", { class: "pv-form", onsubmit: (e) => e.preventDefault() }, fields) : null;
  const values = () => Object.fromEntries([...(form?.querySelectorAll("input[name]:not([name=key]), select[name]") || [])].map((x) => [x.name, x.value.trim()]));
  const result = h("div", { class: "pv-result" }, c.available ? null : h("div", { class: "warn-line" }, c.reason));
  const say = (text, bad) => result.replaceChildren(h("div", { class: bad ? "warn-line" : "ok-line" }, text));
  async function save(extra = {}) {
    let r = { choices: null };
    if (key) {
      const body = { provider: c.name, ...extra };
      if (keyInput.value.trim()) body.key = keyInput.value.trim();
      if ("key" in body) r = await post("/api/analysis/provider", body).catch((e) => ({ error: e.message }));
    } else if (form) {
      r = await post("/api/analysis/agent", { agent: c.name, settings: values() }).catch((e) => ({ error: e.message }));
    }
    if (r.error) { say(r.error, true); return null; }
    return r;
  }
  const busy = (b, on) => { b.disabled = on; };
  const saveBtn = form ? h("button", { class: "btn", type: "button", disabled: !admin, onclick: async () => {
    busy(saveBtn, true);
    if (await save()) { paneTab = c.name === current?.name ? null : c.name; toast(t("{provider} settings saved.", { provider: c.label })); render(); }
    busy(saveBtn, false);
  } }, t("Save")) : null;
  const testBtn = h("button", { class: "btn", type: "button", disabled: !admin || !c.path, onclick: async () => {
    busy(testBtn, true);
    say(t("Saving and asking {provider}…", { provider: c.label }));
    if (await save()) {
      const r = await post("/api/analysis/provider/test", { provider: c.name }).catch((e) => ({ error: e.message }));
      if (r.error || !r.ok) say(r.error || t("The model answered, but not as asked."), true);
      else say(t("Works: {model} answered in {seconds}s.", { model: r.model, seconds: (r.ms / 1000).toFixed(1) }));
      if (keyInput) keyInput.value = "";
    }
    busy(testBtn, false);
  } }, t("Test connection"));
  const useBtn = h("button", { class: "btn primary", type: "button", disabled: !admin || inUse || !c.path, onclick: async () => {
    busy(useBtn, true);
    const r = await save();
    const now = r?.choices?.find((x) => x.name === c.name) || c;
    if (r && !now.available) say(now.reason, true);
    else if (r) await use(c.name);
    busy(useBtn, false);
  } }, inUse ? t("In use") : t("Use for analysis"));
  const note = !c.path ? t("{agent} was not found: sessions wait in the queue until it is installed and signed in.", { agent: c.label })
    : c.name === "bob" ? t("Runs Bob Shell headless (bob run) with no tools and Bob's own model; only a redacted digest of each session is sent.")
    : t("Analyzed by {agent} through your own login; only a redacted digest of each session is sent.", { agent: c.label });
  return h("div", { class: "provider-pane" },
    h("div", { class: "pv-head" }, h("span", { class: "pv-agent" }, c.label), engineBadge(inUse, c.available)),
    h("div", { class: "pv-where muted" }, note),
    form,
    result,
    h("div", { class: "pv-actions" }, testBtn, saveBtn, h("span", { class: "grow" }), useBtn),
    c.name === "bob" ? h("div", { class: "pv-foot muted" }, t("Bob keeps each analysis in its own task list; Interlatch doesn't record them as sessions.")) : null);
}
function providerPane(apis, current, use) {
  const name = apiPane || (current?.kind === "api" ? current.name : apis[0].name);
  const p = apis.find((c) => c.name === name) || apis[0];
  const s = p.settings || {}, d = p.defaults || {}, key = p.key || {};
  const admin = canAdmin();
  const inUse = current?.name === p.name;
  const pick = h("select", { "aria-label": t("Provider"), disabled: !admin, onchange: (e) => { apiPane = e.target.value; render(); } },
    apis.map((c) => h("option", { value: c.name, selected: c.name === p.name }, c.label + (c.name === current?.name ? ` · ${t("in use")}` : c.available ? " ✓" : ""))));
  const state = engineBadge(inUse, p.available);
  let host = "";
  try { host = new URL(p.path).host; } catch (e) { host = p.path || ""; }
  const where = h("div", { class: "pv-where muted" }, icon(p.local ? "home" : "cloud"), " ", p.local ? t("Runs on this computer: transcripts never leave it.")
    : h("span", null, t("Sends a redacted digest of each session to {host}.", { host: host || p.label }), " ", h("b", null, t("Each call is billed to your API key."))));
  const listId = `models-${p.name}`;
  const models = h("datalist", { id: listId }, (providerModels[p.name] || []).map((m) => h("option", { value: m })));
  const field = (k, label, attrs = {}, wide = false) => h("label", { class: wide ? "wide" : null }, label, h("input", { class: "input", name: k, value: s[k] ?? "",
    autocomplete: "off", spellcheck: "false", disabled: !admin, ...attrs }));
  const endpoint = (wide) => field("base_url", t("Endpoint"), { placeholder: d.base_url || d.hint || "", type: "url" }, wide);
  const needsEndpoint = !d.base_url && p.name !== "bedrock" && p.name !== "azure"; // openai-compatible: no address of its own
  const main = [];
  if (needsEndpoint) main.push(endpoint(true));
  if (p.name === "azure") main.push(field("resource", t("Resource name"), { placeholder: "my-openai" }, true));
  main.push(field("model", t("Model"), { placeholder: d.model || (p.name === "azure" ? t("deployment name") : ""), list: listId }),
    field("small_model", t("Small model (screening)"), { placeholder: d.small_model || t("same as Model"), list: listId }));
  if (p.name === "bedrock") main.push(field("region", t("AWS region"), { placeholder: "us-east-1" }), field("profile", t("AWS profile"), { placeholder: "default" }));
  const keyHint = key.source === "stored" ? t("stored; type to replace it")
    : key.source ? t("from {variable}", { variable: key.source })
    : key.alt === "aws" ? t("optional: without one, your AWS sign-in is used")
    : key.alt === "entra" ? t("optional: without one, your az login is used")
    : key.needed ? t("required") : t("optional");
  const keyInput = h("input", { class: "input", name: "key", type: "password", autocomplete: "new-password", placeholder: keyHint, disabled: !admin });
  const forget = key.source === "stored" && admin ? h("button", { class: "btn small", type: "button", onclick: async () => {
    if (await save({ key: "" })) { apiPane = p.name; toast(t("API key removed.")); render(); }
  } }, t("Remove key")) : null;
  if (p.dialect !== "ollama") main.push(h("label", { class: "wide" }, key.env ? t("API key ({variable})", { variable: key.env }) : t("API key"),
    h("div", { class: "pv-key" }, keyInput, forget)));
  const adv = [];
  if (!needsEndpoint) adv.push(endpoint(false));
  if (p.dialect === "ollama") adv.push(field("num_ctx", t("Context window (tokens)"), { inputmode: "numeric", placeholder: String(d.num_ctx || "") }));
  adv.push(field("chunk_chars", t("Characters per call"), { inputmode: "numeric", placeholder: String(d.chunk_chars || "") }));
  const advOpen = adv.some((x) => x.querySelector("input").value);
  const form = h("form", { class: "pv-form", onsubmit: (e) => e.preventDefault() }, main,
    h("details", { class: "pv-adv wide", open: advOpen }, h("summary", null, t("Advanced")), h("div", { class: "pv-form" }, adv)), models);
  const values = () => Object.fromEntries([...form.querySelectorAll("input[name]:not([name=key])")].map((x) => [x.name,
    ["num_ctx", "chunk_chars"].includes(x.name) ? Number(x.value) || 0 : x.value.trim()]));
  const result = h("div", { class: "pv-result" }, p.available ? null : h("div", { class: "warn-line" }, p.reason));
  const say = (text, bad) => result.replaceChildren(h("div", { class: bad ? "warn-line" : "ok-line" }, text));
  async function save(extra = {}) {
    const body = { provider: p.name, settings: values(), ...extra };
    if (keyInput.value.trim()) body.key = keyInput.value.trim();
    const r = await post("/api/analysis/provider", body).catch((e) => ({ error: e.message }));
    if (r.error) { say(r.error, true); return null; }
    return r;
  }
  const busy = (b, on) => { b.disabled = on; };
  const saveBtn = h("button", { class: "btn", type: "button", disabled: !admin, onclick: async () => {
    busy(saveBtn, true);
    if (await save()) { apiPane = p.name; toast(t("{provider} settings saved.", { provider: p.label })); render(); }
    busy(saveBtn, false);
  } }, t("Save"));
  const testBtn = h("button", { class: "btn", type: "button", disabled: !admin, onclick: async () => {
    busy(testBtn, true);
    say(t("Saving and asking {provider}…", { provider: p.label }));
    if (await save()) {
      const r = await post("/api/analysis/provider/test", { provider: p.name }).catch((e) => ({ error: e.message }));
      if (r.error || !r.ok) say(r.error || t("The model answered, but not as asked."), true);
      else say(t("Works: {model} answered in {seconds}s.", { model: r.model, seconds: (r.ms / 1000).toFixed(1) }));
      keyInput.value = "";
    }
    busy(testBtn, false);
  } }, t("Test connection"));
  const useBtn = h("button", { class: "btn primary", type: "button", disabled: !admin || inUse, onclick: async () => {
    busy(useBtn, true);
    const r = await save();
    const now = r?.choices?.find((c) => c.name === p.name);
    if (now && !now.available) say(now.reason, true);
    else if (r) await use(p.name);
    busy(useBtn, false);
  } }, inUse ? t("In use") : t("Use for analysis"));
  if (admin && !(p.name in providerModels)) {
    providerModels[p.name] = [];
    post("/api/analysis/provider/models", { provider: p.name }).then((r) => {
      providerModels[p.name] = r.models || [];
      models.replaceChildren(...providerModels[p.name].map((m) => h("option", { value: m })));
    }).catch(() => {});
  }
  return h("div", { class: "provider-pane" },
    h("div", { class: "pv-head" }, pick, state),
    where,
    form,
    result,
    h("div", { class: "pv-actions" }, testBtn, saveBtn, h("span", { class: "grow" }), useBtn),
    h("div", { class: "pv-foot muted" }, tx("Saved in config.toml under {section}. The key goes in provider-keys.json, which only you can read.",
      { section: h("span", { class: "codeline" }, `[providers.${p.name}]`) })));
}
// What Interlatch writes its knowledge in (analysis.language); an older server sends no languages, so no control
function knowledgeLangPicker(a) {
  const langs = a?.languages;
  if (!Array.isArray(langs) || !langs.length) return null;
  const pick = async (code) => {
    if (code === a.language) return;
    const r = await post("/api/analysis/language", { language: code });
    if (!r || r.error) { toast(r?.error || t("Could not change the knowledge language")); return; }
    toast(t("Knowledge is now written in {language}.", { language: langs.find((l) => l.code === code)?.label || code }));
    render();
  };
  return h("div", { class: "analyzer klang" },
    h("div", { class: "subhead" }, t("Knowledge language")),
    h("div", { class: "seg", role: "radiogroup", "aria-label": t("Knowledge language") }, langs.map((l) =>
      h("button", { type: "button", role: "radio", lang: l.code, class: l.code === a.language ? "on" : "", "aria-checked": String(l.code === a.language),
        disabled: !canAdmin() && l.code !== a.language, onclick: () => pick(l.code) }, l.label))),
    h("div", { class: "muted" }, t("Language of summaries, knowledge, reviews and the lines proposed for CLAUDE.md and AGENTS.md. Applies to sessions analyzed from now on.")));
}
// Status › Recording: the macOS menu-bar icon on or off, adding the app extra (PyObjC) first when it is missing.
// An older server sends no menu_bar, and the app (which always shows the icon) sends supported: false.
function menuBarRow(mb) {
  if (!mb?.supported) return null;
  const on = !!(mb.on && mb.app_extra); // on without the extra shows nothing: switching it on installs the extra
  const code = (c) => h("span", { class: "codeline" }, c);
  // shown: this dashboard put the icon there. macOS may still hide it (not allowed in the menu bar, or behind the
  // notch), and the dashboard can't tell, so the note says where to look instead of promising it is visible
  const note = on && mb.shown ? tx("On. Don't see it? Check System Settings › Menu Bar, where it is listed as {name}, and on a MacBook whether the notch hides it.",
      { name: code(mb.listed_as || "python3") })
    : on && !mb.agent ? tx("On. It shows while the dashboard runs at login, which {command} sets up.", { command: code("interlatch install") })
    : on ? t("On. It shows the next time the dashboard that runs at login starts.")
    : !mb.app_extra && mb.can_install ? t("Turning it on installs the app extra (PyObjC, about 30 MB) with uv, then restarts the dashboard.")
    : !mb.app_extra ? tx("Needs the app extra: {command}", { command: code(mb.command) })
    : t("Interlatch's icon in the menu bar: what it's doing, a search and your recent sessions, a click away.");
  const sw = h("button", { class: "switch admin-only", type: "button", role: "switch", "aria-checked": String(on), "aria-label": t("Menu-bar icon"),
    disabled: !canAdmin() || (!on && !mb.app_extra && !mb.can_install), onclick: async () => {
      sw.disabled = true;
      const r = await post("/api/menu-bar", { on: !on });
      if (!r.started) { toast(r.error || t("The menu-bar icon is already changing")); sw.disabled = false; return; }
      sw.setAttribute("aria-checked", String(!on));
      if (!on && !mb.app_extra) toast(t("Installing the app extra; the dashboard restarts when it is done"), 6000);
      else if (r.restarts) toast(t("The dashboard restarts to show or hide the icon"), 6000);
      if (r.restarts) restartAfter.add("menu-bar");
      watchJob("menu-bar");
    } });
  return h("div", { class: "set-row upd-daily" }, h("div", null, h("b", null, t("Menu-bar icon")), h("div", { class: "muted" }, note)), sw);
}
route(/^\/status$/, async () => {
  const st = await api("/api/status");
  const row = (ok, label, detail) => h("div", { class: "status-row" }, h("span", { class: ok ? "ok" : "no" }, ok ? "✓" : "✗"), h("span", null, label), detail ? h("span", { class: "muted" }, detail) : null);
  const counts = st.counts || {};
  return h("div", null,
    h("div", { class: "page-head" }, titled("gears", h("h1", null, t("Status")), h("div", { class: "sub" }, `Interlatch ${st.version} · ${t("last sync {ago}", { ago: ago(st.last_sync) })}`))),
    h("div", { class: "grid cols-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, t("Recording"))),
        h("div", { class: "status-list" },
          row(st.hooks?.SessionEnd, t("SessionEnd hook"), t("archives + ingests each session as it ends")),
          row(st.launchd?.loaded, t("Background agent"), st.launchd?.loaded ? t("runs every 15 min · {n} runs · last exit {code}", { n: st.launchd.runs || 0, code: st.launchd.last_exit ?? "-" }) : t("not loaded")),
          row(st.mcp, t("MCP server registered"), t("Claude Code can search this vault")),
          row(!!st.hooks?.SessionStart, t("SessionStart knowledge injection"), t("optional: {command}", { command: "interlatch install --inject-context" })),
          row(!!st.statusline?.installed, t("Status-line usage collector"), !st.statusline?.installed ? t("optional: {command}", { command: "interlatch install --statusline" })
            : planLine(st.statusline.plan) || t("no plan limits seen yet (Pro and Max plans only)"))),
        menuBarRow(st.menu_bar),
        h("div", { class: "subhead" }, t("Storage")),
        h("div", null, h("span", { class: "codeline" }, st.archive_dir), t(" raw transcripts (kept forever, gzip)")),
        h("div", { style: { marginTop: "6px" } }, h("span", { class: "codeline" }, st.notes_dir), t(" Markdown vault")),
        h("div", { class: "muted", style: { marginTop: "6px" } }, t("Database {size}B", { size: fmtCompact(st.db_size) }))),
      updatesCard(),
      h("section", { class: "card analysis-card" }, h("div", { class: "card-head" }, h("h2", null, t("Analysis"))),
      h("div", { class: "analysis-grid" },
        h("div", { class: "analysis-main" }, h("div", { class: "subhead" }, t("Analyzed by")), analyzerPicker(st.analysis)),
        h("div", { class: "analysis-side" },
          h("div", { class: "subhead" }, t("Queue")),
          h("div", { class: "hero-facts queue-facts" },
            h("div", { class: "fact" }, h("b", null, fmtNum(st.pending.ready)), h("span", null, t("ready now"))),
            h("div", { class: "fact" }, h("b", null, fmtNum(st.pending.queued)), h("span", null, t("queued"))),
            h("div", { class: "fact" }, h("b", null, fmtNum(st.pending.held || 0)), h("span", null, t("held"))),
            h("div", { class: "fact", title: t("API-equivalent: what the tokens would cost at list prices") }, h("b", null, fmtCost(st.analysis_cost)), h("span", null, t("spent")))),
          // the server words why the queue is stopped as a clause: a sentence in English, 「。」 in Japanese
          st.pending.block ? h("div", { class: "warn-line" }, icon("pause"), " ", LANG === "en"
            ? `${st.pending.block[0].toUpperCase()}${st.pending.block.slice(1)}.` : `${st.pending.block}。`) : null,
          Object.keys(st.pending.reasons || {}).length ? h("div", { class: "tags" }, h("span", { class: "muted" }, t("Waiting because: ")),
            Object.entries(st.pending.reasons).map(([k, v]) =>
              h("span", { class: "tag", title: QUEUE_REASON_HINT[k] || "" }, `${st.pending.labels?.[k] || QUEUE_REASON_LABEL[k] || k}${t(": ")}${fmtNum(v)}`))) : null,
          queueRunRow(st),
          h("div", { class: "tags" }, h("span", { class: "muted" }, t("Sessions: ")),
            Object.entries(counts).map(([k, v]) => h("span", { class: "tag" }, `${STATUS_LABEL[k] || k}${t(": ")}${fmtNum(v)}`))),
          h("div", { class: "muted analysis-meta" }, tx("Model {model} · auto {auto} · backfill {backfill} · {n} per run", { model: h("code", null, st.config.model),
            auto: st.config.auto ? t("on") : t("off"), backfill: st.config.backfill ? t("on") : t("off"), n: st.config.max_per_run })),
          knowledgeLangPicker(st.analysis))),
      st.errors.length ? [h("div", { class: "subhead" }, t("Recent failures")), h("ul", { class: "bullets" }, st.errors.map((e) => h("li", null, h("a", { href: `#/session/${e.id}` }, e.title || e.id.slice(0, 8)), h("div", { class: "muted" }, (e.analysis_reason || "").slice(0, 200)))))] : null)));
});

// The Queue's Analyze now: the sessions ready now, or (none ready) the ones that may still be going, analyzed at once
// instead of on the background run. It runs with automatic analysis off, and through a usage-limit pause that may be over.
function queueRunRow(st) {
  if (st.hub_url) return null; // its sessions are analyzed on the hub
  const ready = st.pending.ready || 0, active = st.pending.reasons?.active || 0;
  const which = ready ? "ready" : active ? "active" : null;
  if (!which) return null;
  const n = which === "ready" ? ready : active;
  const running = st.jobs?.["analyze:queue"]?.state === "running";
  const label = running ? t("Analyzing…") : which === "ready" ? t("Analyze {n} now", { n: fmtNum(n) })
    : tn(n, "Analyze the {n} still active", "Analyze the {n} still active", { n: fmtNum(n) });
  const btn = h("button", { class: "btn small primary admin-only", type: "button", disabled: running || !canAdmin(), onclick: async () => {
    if (n > 25 && !confirm(st.analysis?.billed
      ? t("Analyze {n} sessions now? Each one is a call to {agent}, billed to your API key.", { n, agent: analyzer() })
      : t("Analyze {n} sessions now? Each one is a call on your {agent} login.", { n, agent: analyzer() }))) return;
    btn.disabled = true; btn.textContent = t("Starting…");
    const r = await post("/api/analysis/run", { which }).catch((e) => ({ error: e.message }));
    if (!r.started) { toast(r.error || t("Could not start")); btn.disabled = false; btn.textContent = label; return; }
    toast(tn(r.count, "Analyzing {n} session; the status bar shows progress", "Analyzing {n} sessions; the status bar shows progress", { n: fmtNum(r.count) }), 7000);
    btn.textContent = t("Analyzing…");
    watchJob("analyze:queue");
  } }, label);
  const note = which === "active" ? t("Nothing is ready yet. These may still be going: one that continues is analyzed again later.")
    : st.pending.block ? t("Runs now anyway, without waiting for the background run.")
    : t("Without waiting for the background run (every 15 minutes).");
  return h("div", { class: "queue-run" }, btn, h("span", { class: "muted" }, note));
}

// =====================================================================================
// Devices: this computer as a hub or as one that sends to a hub, and the dashboard on a phone (Tailscale)
// =====================================================================================
// On a computer that sends to a hub (Settings › Devices): the hub, this computer's projects there, and what it
// shares. What leaves the computer changes only at the computer itself (dv.here), never from a phone.
function hubHost(url) { try { return new URL(url).host; } catch { return url || ""; } }
async function devicesPost(path, body) { // null when it failed, after saying why
  try {
    const r = await post(path, body);
    if (r.error) { toast(r.error, 7000); return null; }
    return r;
  } catch (e) { if (!e.handled) toast(e.message, 7000); return null; }
}
const notHere = () => t("Change this on the computer itself, not from another device.");

function spokeHubCard(dv) {
  const lp = dv.last_push, knowledge = dv.share === "knowledge", host = hubHost(dv.hub_url);
  const failed = !!lp?.errors?.length;
  const sendNow = h("button", { class: "btn small primary", type: "button", onclick: async () => {
    sendNow.disabled = true;
    const r = await devicesPost("/api/devices/push");
    if (!r) { sendNow.disabled = false; return; }
    toast(r.started ? t("Sending to the hub…") : t("Already sending"));
    if (r.started) watchJob("push");
  } }, knowledge ? t("Share now") : t("Send now"));
  const leave = h("button", { class: "btn small danger", type: "button", disabled: !dv.here, title: dv.here ? "" : notHere(), onclick: async () => {
    if (!confirm(t("Leave the hub at {host}? This computer stops sending to it, and records and analyzes its own sessions again. The hub keeps what it was sent. Joining again takes a new invite.", { host }))) return;
    leave.disabled = true;
    if (await devicesPost("/api/devices/leave-hub")) { toast(t("This computer left the hub.")); render(); } else leave.disabled = false;
  } }, t("Leave the hub…"));
  const fact = (value, label) => h("div", { class: "fact" }, h("b", null, value), h("span", null, label));
  const state = !lp ? h("span", { class: "badge" }, h("i", { class: "sdot" }), t("Nothing sent yet"))
    : failed ? h("span", { class: "badge warning" }, h("i", { class: "sdot" }), t("Last send had problems"))
      : h("span", { class: "badge good" }, h("i", { class: "sdot" }), t("Connected"));
  return h("section", { class: "card hl-card" },
    cardHead(t("Hub"), { iconName: "organization", tools: state }),
    h("div", { class: "hl-name" }, h("b", null, dv.hub_name || host), dv.hub_name ? h("span", { class: "codeline" }, host) : null),
    lp && lp.sent != null ? h("div", { class: "hero-facts" },
      fact(ago(lp.at), knowledge ? t("last shared") : t("last sent")),
      fact(fmtNum(lp.sent + lp.unchanged), knowledge ? t("sessions shared") : t("files sent")),
      knowledge && lp.kept ? fact(fmtNum(lp.kept), t("kept here")) : null,
      knowledge ? fact(fmtNum(dv.team?.lessons || 0), t("teammates' lessons")) : null)
      : lp ? h("div", { class: "muted hl-note" }, t("Last sent {ago}: {summary}", { ago: ago(lp.at), summary: lp.summary })) : null,
    ...(lp?.errors || []).map((e) => h("div", { class: "warn-line" }, e)),
    knowledge ? h("p", { class: "muted hl-note" }, dv.team
      ? [t("Teammates' lessons are read-only here: your MCP tools answer with them, and the start-of-session notes list them as teammates'."), " ",
        h("a", { href: "#/knowledge/all?source=team" }, t("Read them"))]
      : t("No teammates' lessons yet: a hub with a team store sends them back after each share.")) : null,
    h("div", { class: "hl-actions" }, sendNow, hubDashboardLink(dv.hub_url, "btn small"), h("span", { class: "grow" }), leave));
}

// A search box over a list (the hub's projects, this computer's folders), or a typed folder when `typed` allows it
function pickBox({ items, label, placeholder, line, typed }) {
  let chosen = null, matches = [], active = -1;
  const listId = `pk-${Math.random().toString(36).slice(2)}`;
  const input = h("input", { class: "input", type: "search", placeholder, autocomplete: "off", spellcheck: "false", role: "combobox",
    "aria-autocomplete": "list", "aria-expanded": "false", "aria-controls": listId, "aria-label": label });
  const list = h("ul", { class: "sp-options", id: listId, role: "listbox", hidden: true });
  const isPath = (q) => typed && (q.startsWith("/") || q.startsWith("~"));
  const close = () => { list.hidden = true; active = -1; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); };
  const choose = (x) => { chosen = x; input.value = x.typed ? x.path : x.name; close(); };
  const mark = (i) => {
    active = i;
    [...list.children].forEach((li, j) => li.setAttribute("aria-selected", String(j === i)));
    if (i >= 0 && list.children[i]) { input.setAttribute("aria-activedescendant", list.children[i].id); list.children[i].scrollIntoView({ block: "nearest" }); }
  };
  const filter = () => {
    chosen = null;
    const q = input.value.trim(), terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    matches = items.filter((x) => terms.every((w) => `${x.name} ${x.path}`.toLowerCase().includes(w))).slice(0, 8);
    if (isPath(q)) matches = [{ path: q, name: q, typed: true }, ...matches];
    list.replaceChildren(...matches.map((x, i) => h("li", { id: `${listId}-${i}`, role: "option", "aria-selected": "false", class: "sp-option",
      onmousedown: (e) => { e.preventDefault(); choose(x); } },
      x.typed ? [icon("projects"), h("span", null, tx("The folder {path}", { path: h("b", null, x.path) }))]
        : [h("b", null, x.name), h("span", { class: "muted" }, ` ${line(x)}`), h("small", { class: "muted sp-opt-path" }, shortPath(x.path))])),
      ...(q && !matches.length ? [h("li", { class: "sp-option muted", "aria-disabled": "true" }, typed ? t("Nothing matches. Type a folder, starting with / or ~.") : t("Nothing matches."))] : []));
    list.hidden = !matches.length && !q;
    input.setAttribute("aria-expanded", String(!list.hidden));
    mark(matches.length ? 0 : -1);
  };
  input.addEventListener("input", filter);
  input.addEventListener("focus", filter);
  input.addEventListener("blur", () => setTimeout(close, 100));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (list.hidden) filter();
      if (matches.length) mark((active + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length);
    } else if (e.key === "Enter" && !list.hidden && active >= 0 && matches[active]) {
      e.preventDefault(); choose(matches[active]);
    } else if (e.key === "Escape" && !list.hidden) { e.preventDefault(); close(); }
  });
  const value = () => { // what was picked, the one match, or a typed folder
    const q = input.value.trim();
    return chosen || (isPath(q) ? { path: q, typed: true } : matches.length === 1 ? matches[0] : null);
  };
  return { el: h("label", { class: "pk-field" }, h("span", { class: "pk-label" }, label), h("div", { class: "sp-combo" }, input, list)), input, value };
}

// The hub's projects this computer is in: through a folder added to one, or a repository whose git remote the hub
// files there. Join (add a folder), leave and rejoin (what was shared stays), or remove a folder added by mistake
// (the hub deletes what it shared from there).
function spokeProjectsCard(dv) {
  const knowledge = dv.share === "knowledge", projects = dv.projects || [];
  const panel = h("div", { class: "mp-join", hidden: true });
  const act = async (verb, body, said) => {
    const r = await devicesPost(`/api/devices/projects/${verb}`, body);
    if (!r) return false;
    toast(said(r), 7000);
    if (r.pushing) watchJob("push");
    render();
    return true;
  };
  const removeBtn = (f, p) => {
    const b = h("button", { class: "link-btn danger", type: "button", disabled: !dv.here, title: dv.here ? t("For a folder added by mistake") : notHere(), onclick: async () => {
      if (!confirm(knowledge
        ? t("Remove {folder} from {name}? Its sessions stop going there, and the hub deletes what this computer shared from it, with the lessons. Only for a folder added by mistake: to keep what you shared, leave the project instead.", { folder: f.folder, name: p.name })
        : t("Remove {folder} from {name}? The hub files its sessions by their git remote or their own folder again.", { folder: f.folder, name: p.name }))) return;
      b.disabled = true;
      if (!await act("remove", { folder: f.folder }, (r) => r.taken == null ? t("{folder} no longer goes to {name}.", { folder: shortPath(f.folder), name: p.name })
        : tn(r.taken, "Removed. The hub deleted the {n} session this computer shared from there.", "Removed. The hub deleted the {n} sessions this computer shared from there.", { n: fmtNum(r.taken) }))) b.disabled = false;
    } }, t("Remove"));
    return b;
  };
  const row = (p) => {
    const btn = knowledge ? h("button", { class: "btn small", type: "button", disabled: !dv.here, title: dv.here ? "" : notHere(), onclick: async () => {
      if (!p.left && !confirm(t("Leave {name}? This computer stops sharing its sessions there and stops getting its teammates' lessons. What it already shared stays on the hub.", { name: p.name }))) return;
      btn.disabled = true;
      if (!await act(p.left ? "rejoin" : "leave", { project: p.path }, () => (p.left ? t("Rejoined {name}: its sessions are shared again.", { name: p.name })
        : t("Left {name}. What this computer shared there stays on the hub.", { name: p.name })))) btn.disabled = false;
    } }, p.left ? t("Rejoin") : t("Leave")) : null;
    return h("li", { class: `sp-row mp-row${p.left ? " is-left" : ""}` },
      h("div", { class: "sp-main" },
        h("div", { class: "mp-head" }, icon("hubproject"), h("span", { class: "sp-name" }, p.name),
          p.left ? h("span", { class: "badge", title: t("This computer no longer shares here or gets its lessons. What it shared stays on the hub.") }, t("Left")) : null),
        ...p.folders.map((f) => h("div", { class: "mp-src" }, icon(p.left ? "projects" : "linked"),
          h("span", { class: "codeline", title: f.folder }, shortPath(f.folder)),
          h("span", { class: "muted" }, f.sessions == null ? t("not shared yet") : tn(f.sessions, "{n} session shared", "{n} sessions shared", { n: fmtNum(f.sessions) })),
          removeBtn(f, p),
          ...f.overridden.map((o) => h("div", { class: "muted mp-over" }, t("{repo} goes to {project} instead: the hub knows its git remote", { repo: shortPath(o.repo), project: o.project.split("/").pop() }))))),
        ...p.repos.map((r) => h("div", { class: "mp-src" }, icon("branch"),
          h("span", { class: "codeline", title: r.folder }, shortPath(r.folder)), h("span", { class: "muted" }, t("by its git remote {remote}", { remote: r.remote })))),
        ...(p.groups || []).map((g) => h("div", { class: "mp-src" }, icon("projects"),
          h("a", { href: "#/projects" }, g.name), h("span", { class: "muted" }, t("your group, with every project in it")))),
        !p.folders.length && !p.repos.length && !(p.groups || []).length ? h("div", { class: "muted" }, t("No folder here goes to it now.")) : null),
      btn);
  };
  const openJoin = async () => {
    panel.hidden = false;
    panel.replaceChildren(h("div", { class: "muted" }, t("Asking the hub for its projects…")));
    const r = await devicesPost("/api/devices/projects/choices");
    if (!r) { panel.hidden = true; return; }
    const proj = pickBox({ items: r.projects, label: t("Project on the hub"), placeholder: t("Search the hub's projects"),
      line: (x) => tn(x.sessions, "{n} session", "{n} sessions", { n: fmtNum(x.sessions) }) });
    const folder = pickBox({ items: r.folders, label: t("Folder on this computer"), placeholder: t("Search your folders, or type one like ~/code/app"),
      line: (x) => tn(x.sessions, "{n} session", "{n} sessions", { n: fmtNum(x.sessions) }), typed: true });
    const join = h("button", { class: "btn primary", type: "submit" }, t("Join"));
    panel.replaceChildren(h("form", { class: "mp-form", onsubmit: async (e) => {
      e.preventDefault();
      const p = proj.value(), f = folder.value();
      if (!p) { toast(t("Pick one of the hub's projects.")); proj.input.focus(); return; }
      if (!f) { toast(t("Pick a folder, or type its full path.")); folder.input.focus(); return; }
      join.disabled = true;
      if (!await act("add", { project: p.path, folder: f.path }, (x) => x.remote
        ? t("Its sessions already go to {name}: the hub knows its git remote ({remote}).", { name: x.name, remote: x.remote })
        : t("Sessions in {folder} now go to {name}.", { folder: shortPath(x.folder), name: x.name }))) join.disabled = false;
    } },
      h("p", { class: "muted" }, t("Pick the project, then the folder here that holds it. Sessions in that folder and the folders below it are shared there, and its teammates' lessons come back.")),
      h("div", { class: "mp-fields" }, proj.el, folder.el),
      h("div", { class: "mp-actions" }, join, h("button", { class: "btn", type: "button", onclick: () => { panel.hidden = true; } }, t("Cancel")))));
    proj.input.focus();
  };
  const joinBtn = h("button", { class: "btn small", type: "button", disabled: !dv.here, title: dv.here ? "" : notHere(), onclick: openJoin }, t("Join a project"));
  return h("section", { class: "card" },
    cardHead(t("Projects on the hub"), { iconName: "projects", tools: joinBtn }),
    panel,
    projects.length ? h("ul", { class: "sp-list" }, projects.map(row))
      : h("p", { class: "muted" }, t("This computer isn't in any of the hub's projects yet. Join one to share its sessions there and get its teammates' lessons.")),
    knowledge && projects.length ? h("p", { class: "muted mp-foot" }, t("Leaving a project keeps what this computer shared there. Remove is for a folder added by mistake: the hub deletes what it shared from it.")) : null);
}

// What this computer sends: transcripts, or only what it learned; and from which folders
function spokeSharingCard(dv) {
  const knowledge = dv.share === "knowledge";
  const pickShare = async (share) => {
    if (share === dv.share) return;
    if (share === "everything" && !confirm(t("Send this computer's transcripts to the hub? The hub then records and analyzes them, and this computer stops analyzing its own sessions."))) return;
    if (!await devicesPost("/api/devices/share", { share })) return;
    toast(share === "knowledge" ? t("This computer now shares only summaries and project lessons.") : t("This computer now sends its transcripts to the hub."));
    render();
  };
  const pickFolders = async (all) => {
    if (all === dv.all_folders) return;
    if (all && !confirm(t("Share the summaries and project lessons of sessions in every folder on this computer, including projects the hub doesn't have?"))) return;
    if (!await devicesPost("/api/devices/share", { all_folders: all })) return;
    toast(all ? t("This computer now shares sessions from every folder.") : t("This computer now shares only sessions in the hub's projects."));
    render();
  };
  const seg = (label, options, current, pick) => h("div", { class: "seg", role: "radiogroup", "aria-label": label }, options.map(([v, text]) =>
    h("button", { type: "button", role: "radio", class: v === current ? "on" : "", "aria-checked": String(v === current),
      disabled: !dv.here && v !== current, onclick: () => pick(v) }, text)));
  return h("section", { class: "card" },
    cardHead(t("What this computer shares"), { iconName: "sync" }),
    h("div", { class: "set-row" },
      h("div", null, h("b", null, t("What it sends")),
        h("div", { class: "muted" }, knowledge ? t("Analyzed here with your own login. The hub gets each session's summary and project lessons; transcripts and lessons about you stay here.")
          : t("The hub records and analyzes the transcripts; this computer no longer analyzes its own sessions. This dashboard shows what it had before it joined."))),
      seg(t("What it sends"), [["knowledge", t("Summaries and lessons")], ["everything", t("Transcripts")]], dv.share, pickShare)),
    knowledge && dv.all_folders != null ? h("div", { class: "set-row" },
      h("div", null, h("b", null, t("Which sessions")),
        h("div", { class: "muted" }, dv.all_folders ? t("Every folder's, including projects the hub doesn't have.")
          : t("Only those in the hub's projects: the folders above, and repositories whose git remote the hub knows. The rest stay here."))),
      seg(t("Which sessions"), [[false, t("The hub's projects")], [true, t("Every folder")]], dv.all_folders, pickFolders)) : null,
    dv.here ? null : h("p", { class: "muted" }, notHere()));
}

// On a hub: keep the team's record in Postgres as well (team_store.py). The password goes to the server, never back.
function teamStoreCard(dv) {
  const st = dv.store;
  const saved = st.settings || {};
  let mode = st.enabled ? "postgres" : "off";
  const status = h("div", { class: "status-list" }, h("div", { class: "muted" }, st.enabled ? t("Checking the team store…")
    : t("Off: what computers share is kept in this hub's own database only.")));
  if (st.enabled) api("/api/team-store").then((s) => status.replaceChildren(...(s.error
    ? [h("div", { class: "warn-line" }, s.error)]
    : [h("div", null, t("On · {where} · PostgreSQL {server}", { where: s.where, server: s.server })),
      h("div", { class: "muted" }, t("{computers} computers · {sessions} sessions · {lessons} lessons · {audit} audit entries",
        { computers: fmtNum(s.counts.computers), sessions: fmtNum(s.counts.sessions), lessons: fmtNum(s.counts.lessons), audit: fmtNum(s.counts.audit) }))])))
    .catch((e) => status.replaceChildren(h("div", { class: "warn-line" }, e.message)));
  const head = [
    h("p", null, t("Keep what your team's computers share in a Postgres database as well, and send each computer that shares knowledge its teammates' lessons. Only this hub connects to the database; the other computers never get its address or password.")),
    status,
    st.driver ? null : h("div", { class: "warn-line" }, t("The Postgres driver isn't installed here. Run {command}, then restart the dashboard.", { command: "uv tool install 'interlatch[team]'" }))];
  if (!(dv.can_admin ?? dv.here)) { // an older server sends only "here"
    return h("section", { class: "card" }, cardHead(t("Team store"), { iconName: "data" }), head,
      st.settings ? h("div", { class: "muted" }, tx("Database {db} on {host}, as {user}", { db: h("span", { class: "codeline" }, saved.dbname), host: h("span", { class: "codeline" }, saved.host), user: h("span", { class: "codeline" }, saved.user) })) : null,
      h("p", { class: "muted" }, dv.people_mode ? t("Only an admin of this hub can change these settings.") : t("Change these settings on the hub itself, not from another device.")));
  }
  const field = (key, label, attrs = {}) => h("label", null, label, h("input", { class: "input", name: key, value: saved[key] || "", autocomplete: "off", spellcheck: "false", ...attrs }));
  const ssl = h("select", { name: "sslmode" }, st.sslmodes.map((m) => h("option", { value: m, selected: m === (saved.sslmode || "require") }, m)));
  const form = h("form", { class: "ts-form", hidden: mode === "off", onsubmit: (e) => e.preventDefault() },
    field("host", t("Server address"), { placeholder: "example.postgres.database.azure.com" }),
    field("port", t("Port"), { inputmode: "numeric", placeholder: "5432", value: saved.port || "5432" }),
    field("dbname", t("Database")),
    field("user", t("User")),
    h("label", null, t("Password"), h("input", { class: "input", name: "password", type: "password", autocomplete: "new-password",
      placeholder: saved.password_set ? t("saved; leave empty to keep it") : "" })),
    h("label", null, t("SSL mode"), ssl));
  const values = () => Object.fromEntries([...form.querySelectorAll("input, select")].map((x) => [x.name, x.value]));
  const result = h("div", { class: "ts-result" });
  const say = (text, bad) => result.replaceChildren(h("div", { class: bad ? "warn-line" : "muted" }, text));
  const test = h("button", { class: "btn", type: "button", hidden: mode === "off", onclick: async () => {
    test.disabled = true;
    say(t("Connecting…"));
    const r = await post("/api/team-store/test", values()).catch((e) => ({ error: e.message }));
    test.disabled = false;
    if (!r.ok) { say(r.error || t("Could not connect"), true); return; }
    say(t("Connected to {where} (PostgreSQL {server}).", { where: r.where, server: r.server }) + " " + (r.steps.length
      ? t("The team store is there: {lessons} lessons from {computers} computers.", { lessons: fmtNum(r.counts.lessons), computers: fmtNum(r.counts.computers) })
      : r.can_create ? t("Its tables are created when you save.") : t("This user can't create the team's tables here.")), !r.steps.length && !r.can_create);
  } }, t("Test connection"));
  const save = h("button", { class: "btn primary", type: "button", onclick: async () => {
    save.disabled = true;
    say(mode === "off" ? t("Saving…") : t("Connecting and saving…"));
    const r = await post("/api/team-store/save", { enabled: mode === "postgres", ...(mode === "postgres" ? values() : {}) }).catch((e) => ({ error: e.message }));
    save.disabled = false;
    if (!r.ok) { say(r.error || t("Could not save"), true); return; }
    toast(mode === "postgres" ? t("Team store on: computers that share knowledge now get their teammates' lessons.") : t("Team store off. Its settings are kept."));
    render();
  } }, t("Save"));
  const modes = segControl([["off", t("Off")], ["postgres", "Postgres"]], mode, (v) => {
    mode = v;
    modes.querySelectorAll("button").forEach((b, i) => { const on = (i === 0 ? "off" : "postgres") === v; b.className = on ? "on" : ""; b.setAttribute("aria-pressed", String(on)); });
    form.hidden = test.hidden = v === "off";
    result.replaceChildren();
  });
  return h("section", { class: "card" }, cardHead(t("Team store"), { iconName: "data" }), head,
    h("div", { class: "analyzer ts-modes" }, modes), form,
    h("div", { class: "ts-actions" }, test, save,
      h("span", { class: "muted" }, tx("Saved in {file}, readable by your user only.", { file: h("span", { class: "codeline" }, st.file) }))),
    result);
}

// On a computer that sends to a hub: the hub's dashboard opens in a new tab signed in as this computer's person,
// through a short-lived link the hub gives this computer's token, on page (a hash route) when given. An older hub
// opens on Home; no token: the plain address.
async function openHub(hubUrl, page = "") {
  let url = `${hubUrl}/${page ? `#${page}` : ""}`;
  try {
    const r = await post("/api/devices/hub-signin", page ? { next: page } : {});
    if (safeUrl(r.url)) url = r.url;
    else if (r.error) toast(t("Opening the hub's dashboard without signing in: {error}", { error: r.error }), 6000);
  } catch (err) { /* already said, or not reachable: the plain address */ }
  const a = h("a", { href: url, target: "_blank", rel: "noopener" });
  document.body.append(a);
  a.click();
  a.remove();
}
// a hub project's page on the hub; one with nothing filed yet has none, and the hub's Projects page lists it
const hubProjectPage = (hp) => (hp.sessions === 0 ? "/projects" : `/project?path=${encodeURIComponent(hp.path)}`);
function hubDashboardLink(hubUrl, cls) {
  return h("a", { href: `${hubUrl}/`, target: "_blank", rel: "noopener", class: cls || null, onclick: (e) => { e.preventDefault(); openHub(hubUrl); } },
    t("Open the hub's dashboard"));
}

// On a hub, for an admin: the people who may send here and open this dashboard, their invites, computers and
// browser sessions, the shared hub token, and what changed (people.py). The server checks every action again.
const ROLE_HINT = {
  admin: t("Invites people, changes roles and this hub's settings"),
  member: t("Their computers send here and get teammates' lessons back"),
  readonly: t("Opens this dashboard; sends and changes nothing"),
};
function uaLabel(ua) { // a dashboard session is labelled with the browser's User-Agent: say it in two words
  const s = String(ua || "");
  const browser = /Edg\//.test(s) ? "Edge" : /Firefox\//.test(s) ? "Firefox" : /Chrome\//.test(s) ? "Chrome" : /Safari\//.test(s) ? "Safari" : null;
  const os = /iPhone/.test(s) ? "iPhone" : /iPad/.test(s) ? "iPad" : /Android/.test(s) ? "Android" : /Mac OS X|Macintosh/.test(s) ? "macOS"
    : /Windows/.test(s) ? "Windows" : /Linux/.test(s) ? "Linux" : null;
  return browser && os ? t("{browser} on {os}", { browser, os }) : browser || os || t("A browser");
}
function projectOf(path) { return String(path || "").split("/").filter(Boolean).pop() || path || ""; }
function auditText(a) {
  const actor = !a.actor || a.actor === "this computer" ? t("Someone at the hub") : a.actor_name || a.actor;
  const vars = { actor, person: a.person_name || t("someone"), role: ROLE_LABEL[a.detail?.role] || a.detail?.role,
    before: ROLE_LABEL[a.detail?.before] || a.detail?.before, after: ROLE_LABEL[a.detail?.after] || a.detail?.after,
    computer: a.detail?.name || (a.detail?.machine || "").slice(0, 8) || t("a computer") };
  const on = a.detail?.on;
  return {
    add: () => t("{actor} added {person} as {role}", vars),
    role: () => t("{actor} changed {person}'s role from {before} to {after}", vars),
    remove: () => t("{actor} removed {person}", vars),
    invite: () => t("{actor} made an invite for {person}", vars),
    join: () => t("{person} joined from {computer}", vars),
    signin: () => t("{person} signed in to the dashboard", vars),
    revoke: () => t("{actor} revoked a computer or browser of {person}", vars),
    projects: () => t("{actor} changed which projects {person} sees", vars),
    "shared-token": () => (on ? t("{actor} turned the shared hub token on", vars) : t("{actor} turned the shared hub token off", vars)),
    accept: () => (a.detail?.accept === "knowledge" ? t("{actor} made this hub take knowledge only", vars) : t("{actor} let this hub take transcripts again", vars)),
    "project-add": () => t("{actor} shared the project {path}", { ...vars, path: a.detail?.path || "" }),
    "project-remove": () => t("{actor} stopped sharing the project {path}", { ...vars, path: a.detail?.path || "" }),
    leave: () => t("{computer} left the project {project}", { ...vars, project: projectOf(a.detail?.path) }),
    rejoin: () => t("{computer} rejoined the project {project}", { ...vars, project: projectOf(a.detail?.path) }),
    withdraw: () => tn(a.detail?.sessions || 0, "{computer} took back {n} session it shared to {project}", "{computer} took back {n} sessions it shared to {project}",
      { ...vars, n: fmtNum(a.detail?.sessions || 0), project: projectOf(a.detail?.path) }),
    purge: () => tn(a.detail?.sessions || 0, "{actor} purged {n} session", "{actor} purged {n} sessions", { ...vars, n: fmtNum(a.detail?.sessions || 0) }),
    settings: () => ("address" in (a.detail || {}) ? t("{actor} set this hub's address to {address}", { ...vars, address: a.detail.address || t("none") })
      : t("{actor} renamed this hub to {name}", { ...vars, name: a.detail?.name || t("this computer's name") })),
  }[String(a.action).replace("_", "-")]?.() || `${actor}: ${a.action}`;
}
// What an admin passes on to the person: shown once, since the hub keeps only the code's hash
function inviteResult(r, close) {
  const row = (label, text, block) => {
    const copy = h("button", { class: "btn small", type: "button", onclick: () => copyText(text, copy) }, t("Copy"));
    return h("div", { class: "ir-row" }, h("div", { class: "ir-label" }, label),
      h("div", { class: "ir-value" }, block ? h("pre", { class: "mcp-code" }, text) : h("span", { class: "codeline" }, text), copy));
  };
  const name = r.person?.name || "";
  return h("div", { class: "invite-result", role: "status" },
    h("div", { class: "ir-head" }, h("b", null, t("Invite for {name}", { name })),
      h("button", { class: "btn small", type: "button", onclick: close }, t("Done"))),
    h("div", { class: "warn-line" }, t("Shown once: copy what you need now. The code works once and expires {when}.", { when: fmtDT(r.expires_at) })),
    row(t("Code"), r.code),
    r.join ? row(t("To join their computer, they run"), r.join, true) : null,
    r.link ? row(t("Or, to open this dashboard in a browser"), r.link) : null,
    r.note ? h("div", { class: "muted" }, r.note) : null,
    h("p", { class: "muted" }, t("Send them one of these by chat or email: the hub sends nothing itself. A computer that joined can open this dashboard later without a code, from Settings › Devices.")));
}
function peopleCard(dv) {
  const box = h("section", { class: "card people-card" }, cardHead(t("People"), { iconName: "preference" }), h("div", { class: "muted" }, t("Loading…")));
  const slot = h("div", { class: "invite-slot" }); // the last invite stays up while the list reloads
  const showInvite = (r) => {
    slot.replaceChildren(inviteResult(r, () => slot.replaceChildren()));
    slot.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
  };
  const send = async (path, body) => { // -> the answer, or null once the failure is said
    try {
      const r = await post(path, body);
      if (r.error) { toast(r.error, 6000); return null; }
      return r;
    } catch (e) { if (!e.handled) toast(e.message, 6000); return null; }
  };
  const load = async () => {
    try { draw(await api("/api/people")); } catch (e) {
      if (!e.handled) box.replaceChildren(cardHead(t("People"), { iconName: "preference" }), h("div", { class: "warn-line" }, e.message));
    }
  };
  const roleOptions = (roles, selected) => roles.map((r) => h("option", { value: r, selected: r === selected, title: ROLE_HINT[r] || "" }, ROLE_LABEL[r] || r));
  let hubProjects = []; // [{path, name, sessions, set_up}] from /api/people
  const projectName = (path) => hubProjects.find((x) => x.path === path)?.name || path.split("/").pop() || path;
  const projectsText = (p) => (p.role === "admin" || p.projects == null ? t("Every project")
    : p.projects.length ? p.projects.map(projectName).join(", ") : t("No project yet"));
  // which projects someone sees: every one, or only those ticked (a member or read-only person sees nothing else)
  const projectPicker = (selected) => {
    const every = h("input", { type: "radio", name: `pp-scope-${Math.random().toString(36).slice(2)}`, checked: selected == null });
    const only = h("input", { type: "radio", name: every.name, checked: selected != null });
    const boxes = hubProjects.map((x) => h("label", { class: "pp-proj", title: x.path },
      h("input", { type: "checkbox", value: x.path, checked: (selected || []).includes(x.path), onchange: () => { only.checked = true; refresh(); } }),
      h("span", null, x.name), h("small", { class: "muted" }, x.sessions ? tn(x.sessions, "{n} session", "{n} sessions", { n: fmtNum(x.sessions) }) : t("set up, no sessions yet"))));
    const ticked = () => boxes.map((b) => b.querySelector("input")).filter((i) => i.checked);
    // a long list gets a search box: it hides the projects that don't match by name or folder, and keeps what is ticked
    const search = hubProjects.length > 6 ? h("input", { class: "input pp-search", type: "search", autocomplete: "off", spellcheck: "false",
      placeholder: t("Search projects by name or folder"), "aria-label": t("Search projects by name or folder") }) : null;
    const count = h("span", { class: "muted", "aria-live": "polite" });
    const none = h("div", { class: "muted", hidden: true });
    const refresh = () => {
      const q = (search?.value || "").trim(), terms = q.toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      boxes.forEach((b, i) => {
        b.hidden = !terms.every((w) => `${hubProjects[i].name} ${hubProjects[i].path}`.toLowerCase().includes(w));
        if (!b.hidden) shown += 1;
      });
      none.hidden = shown > 0;
      none.textContent = t("No project matches “{q}”.", { q });
      const n = ticked().length;
      count.textContent = n ? t("{n} chosen", { n: fmtNum(n) }) : "";
    };
    search?.addEventListener("input", refresh);
    search?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { // never submits the invite; with one project left, ticks it
        e.preventDefault();
        const left = boxes.filter((b) => !b.hidden);
        if (left.length === 1) { const box = left[0].querySelector("input"); box.checked = !box.checked; only.checked = true; refresh(); }
      } else if (e.key === "Escape" && search.value) { e.preventDefault(); search.value = ""; refresh(); }
    });
    const el = h("fieldset", { class: "pp-scope" }, h("legend", null, t("Projects they see")),
      h("label", { class: "pp-proj" }, every, h("span", null, t("Every project on this hub"))),
      h("label", { class: "pp-proj" }, only, h("span", null, t("Only these projects: their summaries and project lessons, never transcripts"))),
      search ? h("div", { class: "pp-tools" }, search, count) : null,
      h("div", { class: "pp-projs" }, boxes.length ? [...boxes, none] : h("span", { class: "muted" }, tx("No projects yet: set one up on the hub with {command}.",
        { command: h("span", { class: "codeline" }, "interlatch hub project add <folder>") }))));
    refresh();
    // refresh: after the form around it was reset, show every project again
    return { el, refresh, value: () => (every.checked ? "all" : ticked().map((i) => i.value)) };
  };

  const tokenRow = (p, x, kind) => {
    const label = kind === "computer" ? x.name || t("A computer") : uaLabel(x.label);
    const revoke = h("button", { class: "link-btn danger", type: "button", onclick: async () => {
      if (!confirm(kind === "computer" ? t("Stop {computer} from sending to this hub? It can join again only with a new invite.", { computer: label })
        : t("End {name}'s dashboard session in {browser}? That browser has to sign in again.", { name: p.name, browser: label }))) return;
      revoke.disabled = true;
      if (await send("/api/people/revoke", { id: p.id, token: x.id })) { toast(t("Revoked.")); load(); } else revoke.disabled = false;
    } }, t("Revoke"));
    return h("div", { class: "pp-tok" }, icon(kind === "computer" ? "devices" : "page"),
      h("span", { title: kind === "browser" ? x.label || "" : x.machine_id || "" }, label),
      h("span", { class: "muted" }, x.last_used ? t("used {ago}", { ago: ago(x.last_used) }) : t("since {ago}", { ago: ago(x.since) })), revoke);
  };
  const personRow = (p, roles) => {
    const me = ME?.viewer?.id != null && ME.viewer.id === p.id;
    const sel = h("select", { "aria-label": t("Role of {name}", { name: p.name }), onchange: async () => {
      const role = sel.value;
      if (me && p.role === "admin" && role !== "admin"  // giving up your own admin: only another admin can give it back
          && !confirm(t("Make yourself {role}? You stop being an admin of this hub at once, and only another admin can make you one again.", { role: ROLE_LABEL[role] || role }))) {
        sel.value = p.role;
        return;
      }
      sel.disabled = true;
      const r = await send("/api/people/role", { id: p.id, role });
      sel.disabled = false;
      if (!r) { sel.value = p.role; return; }
      toast(t("{name} is now: {role}.", { name: p.name, role: ROLE_LABEL[role] || role }));
      if (me) location.reload(); else load(); // your own role changed: the whole dashboard follows
    } }, roleOptions(roles, p.role));
    const reinvite = h("button", { class: "btn small", type: "button", title: t("A new one-time code for another computer or browser"), onclick: async () => {
      reinvite.disabled = true;
      const r = await send("/api/people/invite", { id: p.id });
      reinvite.disabled = false;
      if (r) { showInvite(r); load(); }
    } }, t("New invite"));
    const remove = h("button", { class: "btn small danger", type: "button", onclick: async () => {
      if (!confirm(t("Remove {name} from this hub? Their computers stop sending and their dashboard sessions end at once. What they already sent stays here.", { name: p.name }))) return;
      remove.disabled = true;
      if (await send("/api/people/remove", { id: p.id })) { toast(t("{name} was removed.", { name: p.name })); if (me) location.reload(); else load(); }
      else remove.disabled = false;
    } }, t("Remove"));
    const computers = p.computers || [], browsers = p.browsers || [], invites = p.invites || [];
    const lastInvite = invites[invites.length - 1];
    const scope = h("td", { class: "pp-scope-cell" }, h("span", null, projectsText(p)), p.role === "admin" ? null
      : h("button", { class: "link-btn", type: "button", onclick: () => editScope() }, t("Change")));
    const editRow = h("tr", { class: "pp-edit", hidden: true });
    const editScope = () => { // under the person, across the table: the list of projects needs the width
      const picker = projectPicker(p.projects);
      const save = h("button", { class: "btn small primary", type: "button", onclick: async () => {
        const projects = picker.value();
        if (Array.isArray(projects) && !projects.length && !confirm(t("{name} will see no project on this hub. Save anyway?", { name: p.name }))) return;
        save.disabled = true;
        const r = await send("/api/people/access", { id: p.id, projects });
        save.disabled = false;
        if (r) { toast(t("{name} now sees: {projects}.", { name: p.name, projects: projectsText({ ...p, projects: r.people.find((x) => x.id === p.id)?.projects }) })); load(); }
      } }, t("Save"));
      const cancel = h("button", { class: "btn small", type: "button", onclick: () => { editRow.hidden = true; editRow.replaceChildren(); } }, t("Cancel"));
      editRow.replaceChildren(h("td", { colspan: "5" }, picker.el, h("div", { class: "ts-actions" }, save, cancel)));
      editRow.hidden = false;
    };
    return [h("tr", null,
      h("td", null, h("b", null, p.name), me ? h("span", { class: "muted" }, t(" (you)")) : null,
        p.email ? h("div", { class: "muted" }, p.email) : null,
        lastInvite ? h("div", { class: "muted" }, t("Invite open until {when}", { when: fmtDT(lastInvite.expires_at) })) : null),
      h("td", null, sel),
      scope,
      h("td", null, computers.length || browsers.length
        ? [...computers.map((c) => tokenRow(p, c, "computer")), ...browsers.map((b) => tokenRow(p, b, "browser"))]
        : h("span", { class: "muted" }, lastInvite ? t("Not joined yet") : t("No computer or browser yet: make a new invite"))),
      h("td", { class: "pp-actions" }, reinvite, remove)), editRow];
  };

  const inviteForm = (roles, first) => {
    const name = h("input", { class: "input", name: "name", required: true, maxlength: "120", autocomplete: "off" });
    const email = h("input", { class: "input", name: "email", type: "email", autocomplete: "off", spellcheck: "false", placeholder: t("optional") });
    const role = h("select", { name: "role" }, roleOptions(roles, first ? "admin" : "member"));
    const hint = h("div", { class: "muted" }, ROLE_HINT[role.value] || "");
    const picker = projectPicker([]); // nothing until granted: tick their projects, or choose every project
    const showPicker = () => { picker.el.hidden = role.value === "admin"; }; // admins see everything
    role.addEventListener("change", () => { hint.textContent = ROLE_HINT[role.value] || ""; showPicker(); });
    showPicker();
    const submit = h("button", { class: "btn primary", type: "submit" }, t("Create invite"));
    const form = h("form", { class: "people-form", onsubmit: async (e) => {
      e.preventDefault();
      if (!name.value.trim()) { name.focus(); return; }
      const projects = role.value === "admin" ? "all" : picker.value();
      if (Array.isArray(projects) && !projects.length) { toast(t("Choose the projects they see, or every project."), 5000); return; }
      submit.disabled = true;
      const r = await send("/api/people/add", { name: name.value.trim(), email: email.value.trim(), role: role.value, projects });
      submit.disabled = false;
      if (!r) return;
      form.reset();
      picker.refresh();
      showInvite(r);
      load();
    } },
    h("div", { class: "ts-form" },
      h("label", null, t("Name"), name),
      h("label", null, t("Email"), email),
      h("label", null, t("Role"), role)),
    picker.el,
    h("div", { class: "ts-actions" }, submit, hint));
    return [h("div", { class: "subhead" }, t("Invite someone")), form,
      h("div", { class: "muted" }, t("Email is optional; it lets a company sign-in in front of the hub recognize the person."))];
  };

  const sharedToken = (data) => {
    const sw = h("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(!!data.shared_token), "aria-label": t("Shared hub token"),
      onclick: async () => {
        const on = !data.shared_token;
        if (!on && !confirm(t("Turn off the shared hub token? Computers that joined with it stop sending until someone joins them again with an invite."))) return;
        sw.disabled = true;
        const r = await send("/api/people/shared-token", { on });
        sw.disabled = false;
        if (!r) return;
        toast(on ? t("Computers with the shared hub token can send again.") : t("Only computers that joined with an invite can send now."));
        load();
      } });
    return h("div", { class: "set-row" },
      h("div", null, h("b", null, t("Shared hub token")),
        h("div", { class: "muted" }, data.shared_token
          ? t("On: computers that joined with the hub's shared token, before there were people, can still send. Turn it off once everyone has joined with their own invite.")
          : t("Off: only computers that joined with a person's invite can send. Computers that joined with the shared token are turned away."))),
      sw);
  };

  const auditList = (items) => h("details", { class: "pp-audit" },
    h("summary", null, t("Recent changes"), h("span", { class: "muted" }, ` · ${fmtNum(items.length)}`)),
    h("ul", { class: "bullets" }, items.map((a) => h("li", null, auditText(a), h("span", { class: "muted", title: fmtDT(a.at) }, ` · ${ago(a.at)}`)))));

  const draw = (data) => {
    const people = data.people || [];
    const roles = data.roles || ["admin", "member", "readonly"];
    hubProjects = data.projects || [];
    box.replaceChildren(); append(box, [ // append() flattens the arrays and skips nulls
      cardHead(t("People"), { iconName: "preference", hint: people.length ? tn(people.length, "{n} person", "{n} people") : null }),
      people.length ? [
        h("div", { class: "table-wrap" }, h("table", { class: "data pp-table" },
          h("thead", null, h("tr", null, ...[t("Person"), t("Role"), t("Projects"), t("Computers and browsers"), ""].map((x) => h("th", null, x)))),
          h("tbody", null, people.map((p) => personRow(p, roles))))),
        h("p", { class: "muted" }, t("Admins invite people and change this hub's settings; members' computers send here and get their teammates' lessons back; read-only people only open this dashboard. Whoever is at this computer is always an admin."))]
      : [
        h("p", null, t("Give each person a role: admins invite people and change this hub's settings, members' computers send here and get their teammates' lessons back, and read-only people only open this dashboard. The first person you add as admin can manage the hub from their own computer.")),
        h("p", { class: "muted" }, t("Until you add someone, nothing changes: computers send with the hub's shared token, as they do now."))],
      slot,
      inviteForm(roles, !people.length),
      data.address ? null : h("div", { class: "muted pp-note" }, tx("This hub has no address set, so invites use the one this page was opened with ({host}). Set the address others reach it at with {command}.",
        { host: h("span", { class: "codeline" }, location.host), command: h("span", { class: "codeline" }, "interlatch hub enable --url <address>") })),
      people.length ? sharedToken(data) : null,
      data.audit?.length ? auditList(data.audit) : null]);
  };
  load();
  return box;
}

route(/^\/devices$/, async () => {
  const dv = await api("/api/devices");
  const ts = dv.allowed_hosts.find((x) => x.endsWith(".ts.net"));
  const cmd = (text) => h("pre", { class: "mcp-code" }, text);
  const role = {
    single: t("Records and analyzes its own sessions."),
    hub: t("The hub: records and analyzes its own sessions and the ones your other computers send it."),
  }[dv.role]; // a computer that sends to a hub has cards of its own (spokeHubCard)
  const thisCard = h("section", { class: "card" }, cardHead(t("This computer"), { iconName: "devices" }),
    h("p", null, h("b", null, dv.this.name), ` · ${role}`));
  const phone = h("section", { class: "card" }, cardHead(t("On your phone"), { iconName: "devices" }),
    ts ? [
      h("p", null, tx("This dashboard is on your tailnet at {url}. Open it on your phone with the Tailscale app on, then add it to the Home Screen (iPhone: Share › Add to Home Screen) to open it like an app.",
        { url: extLink(`https://${ts}/`, `https://${ts}/`) })),
      h("div", { class: "muted" }, dv.allowed_users.length ? t("Only {users} can open it. Nothing is reachable from the internet.", { users: dv.allowed_users.join(", ") })
        : t("Anyone in your tailnet can open it. Nothing is reachable from the internet.")),
      h("div", { class: "muted", style: { marginTop: "6px" } }, t("{command} takes it off the tailnet.", { command: "interlatch tailnet off" }))]
    : [
      h("p", null, t("Open this dashboard on your phone through Tailscale, a private network between your own devices: nothing is opened to the internet, and only your Tailscale login gets in. Install Tailscale on this computer and your phone, sign both in to the same account, then run here:")),
      cmd("interlatch tailnet on")]);
  const computers = dv.role === "hub" ? teamPointer()
    : dv.role === "single" ? h("section", { class: "card" }, cardHead(t("Computers"), { iconName: "devices" }),
        h("p", null, t("Keep the sessions of your other computers here too. This computer becomes the hub, the only one that records and analyzes (so each session is analyzed once); the others send it their Claude Code and Codex sessions over your tailnet. Run here:")),
        cmd("interlatch hub enable"),
        h("p", { class: "muted" }, t("It prints a {command} command to run on each other computer.", { command: "interlatch hub join …" }))) : null;
  return h("div", { class: "narrow-page" },
    h("div", { class: "page-head" }, titled("laptop", h("h1", null, t("Devices")), h("div", { class: "sub" }, t("One Interlatch for your computers and your phone.")))),
    h("div", { class: "grid" }, ...(dv.role === "spoke"
      ? [spokeHubCard(dv), spokeProjectsCard(dv), spokeSharingCard(dv), phone] : [thisCard, phone, computers])));
});

// Settings › Devices on a hub: its people, computers, shared projects and team store have a section of their own
function teamPointer() {
  const pages = [["#/team", t("People")], ["#/team/projects", t("Shared projects")], ["#/team/computers", t("Computers")], ["#/team/store", t("Team store")],
    ["#/team/settings", t("Hub settings")]];
  return h("section", { class: "card" }, cardHead(t("Team"), { iconName: "team" }),
    h("p", null, t("This computer is the hub. Its people, shared projects, computers and team store are under Team.")),
    canAdmin() ? h("ul", { class: "bullets" }, pages.map(([href, label]) => h("li", null, h("a", { href }, label))))
      : h("p", { class: "muted" }, t("Only an admin of this hub sees them.")));
}

// Team › Hub settings › Knowledge only ([hub] accept): whether this hub takes transcripts at all
function acceptRow(dv) {
  const only = dv.accept === "knowledge";
  const sw = h("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(only), "aria-label": t("Knowledge only"),
    onclick: async () => {
      const accept = only ? "everything" : "knowledge";
      if (accept === "knowledge" && !confirm(t("Take knowledge only? Computers that send transcripts are turned away until they share knowledge only. Transcripts already here stay."))) return;
      sw.disabled = true;
      try {
        const r = await post("/api/team/accept", { accept });
        if (r.error) { toast(r.error, 6000); sw.disabled = false; return; }
      } catch (e) { if (!e.handled) toast(e.message, 6000); sw.disabled = false; return; }
      toast(accept === "knowledge" ? t("This hub takes knowledge only now.") : t("This hub takes transcripts again."));
      render();
    } });
  return h("div", { class: "set-row" },
    h("div", null, h("b", null, t("Knowledge only")),
      h("div", { class: "muted" }, only
        ? t("On: every computer analyzes its own sessions and sends only summaries and project lessons. One that sends transcripts is turned away until it shares knowledge only.")
        : t("Off: computers may send their transcripts, which this hub records and analyzes, or share knowledge only. Turn it on to keep every transcript off this hub."))),
    sw);
}

// Team › Computers: every computer the hub hears from, whose it is, and the folders they map
function computersCard(dv) {
  const others = dv.machines.filter((m) => !m.this);
  const refused = (m) => dv.accept === "knowledge" && !m.this && m.share !== "knowledge";
  const maps = Object.entries(dv.path_map || {});
  const added = dv.machines.flatMap((m) => (m.folders || []).map((f) => [m, f]));
  const table = h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", null, h("tr", null, ...[t("Computer"), t("Person"), t("Sessions"), t("Latest session"), t("Last sent")].map((x, i) => h("th", { class: i === 2 ? "num" : "" }, x)))),
    h("tbody", null, ...dv.machines.map((m) => h("tr", null,
      h("td", null, h("b", null, m.name || m.id.slice(0, 8)), m.this ? h("span", { class: "muted" }, t(" (this one)")) : null,
        m.share === "knowledge" ? h("span", { class: "muted", title: t("Analyzes its own sessions and sends only summaries and project lessons") }, t(" · knowledge only")) : null,
        refused(m) ? h("span", { class: "warn-line", title: t("Run {command} on it, then {push}.", { command: "interlatch config set hub.share knowledge", push: "interlatch push" }) }, t(" · sends transcripts: turned away")) : null,
        m.platform ? h("div", { class: "muted" }, m.platform) : null),
      h("td", null, m.person || h("span", { class: "muted" }, "–")),
      h("td", { class: "num" }, fmtNum(m.sessions)),
      h("td", null, m.last_session ? ago(m.last_session) : "–"),
      h("td", { title: m.last_seen ? t("Last heard from {ago}", { ago: ago(m.last_seen) }) : "" }, m.this ? "–" : m.last_push ? ago(m.last_push) : t("nothing yet")))))));
  return h("section", { class: "card" }, cardHead(t("Computers"), { iconName: "devices", hint: t("{n} sending here", { n: others.length }) }),
    table,
    h("p", { class: "muted" }, t("To add a computer, run {command} here: it prints the command to run on the other one. Sessions from each computer are matched to the same projects here by their git remote.", { command: "interlatch hub enable" })),
    maps.length ? [h("div", { class: "subhead" }, t("Folders mapped ([hub] path_map)")), h("ul", { class: "bullets" }, maps.map(([a, b]) => h("li", null, h("span", { class: "codeline" }, a), " → ", h("span", { class: "codeline" }, b))))] : null,
    added.length ? [h("div", { class: "subhead" }, t("Folders added on other computers")),
      h("ul", { class: "bullets" }, added.map(([m, f]) => h("li", null, h("b", null, m.name || m.id.slice(0, 8)), ": ",
        h("span", { class: "codeline" }, f.folder), " → ", h("span", { title: f.project }, f.name)))),
      h("p", { class: "muted" }, t("Sessions in these folders go to the project shown, unless a repository inside has a git remote this hub knows."))] : null);
}

// Team: a hub's admins manage it here, a page per part
async function teamPage(title, sub, card) {
  if (!canAdmin()) return h("div", { class: "card empty" }, t("Only an admin of this hub can see this."));
  const dv = await api("/api/devices");
  if (dv.role !== "hub") {
    return h("div", { class: "narrow-page" }, h("div", { class: "card" },
      h("p", null, tx("This computer isn't a hub, so it has no team. {page} says how to make it one.", { page: h("a", { href: "#/devices" }, t("Devices")) }))));
  }
  return h("div", { class: "narrow-page" },
    h("div", { class: "page-head" }, titled("gateway", h("h1", null, title), h("div", { class: "sub" }, sub))),
    h("div", { class: "grid" }, card(dv)));
}
route(/^\/team$/, () => teamPage(t("People"), t("Who may send to this hub and open its dashboard, with which role and projects."), (dv) => peopleCard(dv)));
route(/^\/team\/projects$/, () => teamPage(teamProjectsLabel(), dedicated()
  ? t("This hub's projects: who sees each, and which computers send to it. Make one here, then each computer adds its folder to it.")
  : t("Projects set up on this hub: who sees each, and which computers send to it."), () => sharedProjectsCard()));
route(/^\/team\/computers$/, () => teamPage(t("Computers"), t("Every computer this hub hears from, and whose it is."), (dv) => computersCard(dv)));
route(/^\/team\/store$/, () => teamPage(t("Team store"), t("The team's record in Postgres, which only this hub connects to."), (dv) => teamStoreCard(dv)));

// Team › Hub settings: this hub's name and address, what it takes, what builds its knowledge bases, updates and backups.
// A container's .env sets some of them on every start (hub_settings' "managed"): those are shown, not edited.
function hubTextRow({ label, hint, value, placeholder, managed, managedBy, field, saved }) {
  const input = h("input", { class: "input", type: field === "address" ? "url" : "text", value: value || "", placeholder, maxlength: field === "name" ? 80 : 300,
    spellcheck: "false", "aria-label": label, disabled: managed });
  const save = h("button", { class: "btn small", type: "submit", disabled: true }, t("Save"));
  input.addEventListener("input", () => { save.disabled = input.value.trim() === (value || ""); });
  const form = h("form", { class: "hs-edit", onsubmit: async (e) => {
    e.preventDefault();
    save.disabled = true;
    const r = await devicesPost("/api/team/settings", { [field]: input.value.trim() });
    if (!r) { save.disabled = false; return; }
    toast(saved);
    await loadMe(); // the band and the status bar name the hub
    render();
  } }, input, managed ? null : save);
  return h("div", { class: "set-row hs-row" },
    h("div", null, h("b", null, label), h("div", { class: "muted" }, managed ? tx("Set by {name} in the hub's .env: change it there, then run {command}.",
      { name: h("code", null, managedBy), command: h("code", null, "docker compose up -d") }) : hint)),
    form);
}
async function hubSettingsPage() {
  if (!canAdmin()) return h("div", { class: "card empty" }, t("Only an admin of this hub can see this."));
  const [hs, st] = await Promise.all([api("/api/team/settings"), api("/api/status")]);
  if (hs.error) {
    return h("div", { class: "narrow-page" }, h("div", { class: "card" },
      h("p", null, tx("This computer isn't a hub, so it has no hub settings. {page} says how to make it one.", { page: h("a", { href: "#/devices" }, t("Devices")) }))));
  }
  const cmd = (text) => h("pre", { class: "mcp-code" }, text);
  const general = h("section", { class: "card" }, cardHead(t("This hub"), { iconName: "organization" }),
    hubTextRow({ label: t("Name"), field: "name", value: hs.name, placeholder: hs.default_name, managed: hs.managed.name, managedBy: "INTERLATCH_HUB_NAME",
      hint: t("Shown in the band at the top of every page, in invites and on members' computers."), saved: t("The hub's name is saved.") }),
    hubTextRow({ label: t("Address"), field: "address", value: hs.address, placeholder: "https://interlatch.example.com", managed: hs.managed.address, managedBy: "INTERLATCH_HUB_URL",
      hint: t("Where computers and browsers reach this hub. Invites and sign-in links use it."), saved: t("The hub's address is saved. New invites use it.") }),
    acceptRow({ accept: hs.accept }));
  const analysis = h("section", { class: "card" }, cardHead(t("Analysis"), { iconName: "sparkles" }),
    h("p", { class: "muted" }, hs.accept === "knowledge"
      ? t("Computers analyze their own sessions. This hub's model writes each project's knowledge base from the lessons they share, so it needs one to have knowledge bases.")
      : t("This hub's model analyzes the transcripts computers send, and writes each project's knowledge base.")),
    analyzerPicker(st.analysis));
  const backup = hs.container ? [
    h("p", null, t("Copy the database while the hub runs, then download the copy. Run these where the hub's compose.yaml is:")),
    cmd(`docker compose exec hub python -c "import sqlite3; sqlite3.connect('/data/chronicle.db').backup(sqlite3.connect('/data/backup.db'))"\ndocker compose cp hub:/data/backup.db ./interlatch-backup.db`),
    hs.store === "postgres" ? [h("p", null, t("And the team store:")), cmd("docker compose exec -T postgres pg_dump -U chronicle chronicle > team-store.sql")] : null]
    : [h("p", null, t("Copy the database while the hub runs:")),
      cmd(`sqlite3 ${/^[\w@%+=:,./-]+$/.test(hs.db) ? hs.db : `'${hs.db.replace(/'/g, "'\\''")}'`} ".backup interlatch-backup.db"`),
      hs.store === "postgres" ? h("p", { class: "muted" }, t("Back up the team store's Postgres database with your usual tools, such as pg_dump.")) : null];
  const backups = h("section", { class: "card" }, cardHead(t("Back up"), { iconName: "download" }), backup,
    h("p", { class: "muted" }, t("Never copy chronicle.db itself while the hub runs: the copy can be corrupted.")));
  return h("div", { class: "narrow-page" },
    h("div", { class: "page-head" }, titled("gateway", h("h1", null, t("Hub settings")), h("div", { class: "sub" }, t("What this hub is called, where it's reached, and how it's kept.")))),
    h("div", { class: "grid" }, general, analysis, updatesCard(), backups));
}
route(/^\/team\/settings$/, hubSettingsPage);

// Settings › Devices › Shared projects (on a hub): which of its projects leave this computer, who sees each, which
// computers send to it; set one up or stop sharing it (only at the hub itself: it decides what leaves this computer).
const sharedBadge = () => h("span", { class: "badge accent", title: t("Shared from this hub: its summaries and project lessons go to the people given it") }, icon("devices"), t("Shared"));
// on a computer that sends to a hub: the hub project this one is in (p.hub), under the hub's name when it differs
const hubTitle = (p) => t("In {name} on the hub: what it shares goes there, and teammates' lessons come back here", { name: p.hub.name });
const hubBadge = (p) => h("span", { class: "badge accent", title: hubTitle(p) }, icon("linked"),
  p.hub.name === p.label ? t("On the hub") : t("On the hub as {name}", { name: p.hub.name }));
// a project's page; one set up on a hub with nothing filed yet has none: Team › Projects, for whoever may see it
function projectHref(p) {
  if (p.sessions) return `#/project?path=${encodeURIComponent(p.project_path || "")}`;
  return canAdmin() && !limited() ? "#/team/projects" : null;
}
function sharedProjectsCard() {
  const box = h("section", { class: "card shared-card" }, cardHead(t("Shared projects"), { iconName: "projects" }), h("div", { class: "muted" }, t("Loading…")));
  const send = async (path, body) => {
    try {
      const r = await post(path, body);
      if (r.error) { toast(r.error, 7000); return null; }
      return r;
    } catch (e) { if (!e.handled) toast(e.message, 7000); return null; }
  };
  const load = async () => {
    try { draw(await api("/api/projects/shared")); } catch (e) {
      if (!e.handled) box.replaceChildren(cardHead(t("Shared projects"), { iconName: "projects" }), h("div", { class: "warn-line" }, e.message));
    }
  };
  const row = (x, editable, dedicatedHub) => {
    const stop = editable ? h("button", { class: "btn small danger", type: "button", onclick: async () => {
      if (!confirm(dedicatedHub
        ? t("Remove {name} from this hub's projects? What computers already sent stays filed under it, and people keep it in their list until you change it.", { name: x.name })
        : t("Stop sharing {name}? This computer's sessions there go back to their own folders and stop going to the team store. What others sent stays, and people keep it in their list until you change it.", { name: x.name }))) return;
      stop.disabled = true;
      const r = await send("/api/projects/shared/remove", { path: x.path });
      if (r) { toast(dedicatedHub ? t("{name} was removed from this hub's projects.", { name: x.name }) : t("{name} is no longer shared.", { name: x.name })); projectsCache = null; draw(r); }
      else stop.disabled = false;
    } }, dedicatedHub ? t("Remove") : t("Stop sharing")) : null;
    const who = x.people.length ? x.people.map((p) => p.name).join(", ") : t("no one limited to it yet");
    return h("li", { class: "sp-row" },
      h("div", { class: "sp-main" },
        h("div", null, h("a", { href: `#/project?path=${encodeURIComponent(x.path)}`, class: "sp-name" }, x.name), " ",
          h("span", { class: "muted" }, tn(x.sessions, "{n} session", "{n} sessions", { n: fmtNum(x.sessions) }))),
        dedicatedHub ? null : h("div", { class: "codeline sp-path", title: x.path }, x.path), // on a dedicated hub: its own folder, which says nothing
        h("div", { class: "sp-line" }, h("span", { class: "muted" }, t("Seen by")), " ", who,
          x.everyone ? h("span", { class: "muted" }, t(" · and {n} who see every project", { n: fmtNum(x.everyone) })) : null),
        x.folders.length ? h("div", { class: "sp-line" }, h("span", { class: "muted" }, t("Sent from")), " ",
          x.folders.map((f, i) => [i ? ", " : null, h("b", null, f.computer), " ", h("span", { class: "codeline" }, f.folder),
            f.left ? h("span", { class: "muted", title: t("It no longer shares here or gets this project's lessons. What it shared stays.") }, t(" (left)")) : null])) : null),
      stop);
  };
  const addForm = (data) => {
    // one search box: type part of a project's name or folder to filter this hub's projects, or a folder (/… or ~/…)
    let chosen = null, matches = [], active = -1;
    const listId = `sp-list-${Math.random().toString(36).slice(2)}`;
    const input = h("input", { class: "input", type: "search", placeholder: t("Search this hub's projects, or type a folder like ~/work/client-x"),
      autocomplete: "off", spellcheck: "false", role: "combobox", "aria-autocomplete": "list", "aria-expanded": "false",
      "aria-controls": listId, "aria-label": t("Project to share") });
    const list = h("ul", { class: "sp-options", id: listId, role: "listbox", hidden: true });
    const asFolder = (q) => q.startsWith("/") || q.startsWith("~");
    const choose = (c) => {
      chosen = c;
      input.value = c.folder ? c.path : `${c.name} — ${shortPath(c.path)}`;
      close();
    };
    const close = () => { list.hidden = true; active = -1; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); };
    const mark = (i) => {
      active = i;
      [...list.children].forEach((li, j) => li.setAttribute("aria-selected", String(j === i)));
      if (i >= 0 && list.children[i]) { input.setAttribute("aria-activedescendant", list.children[i].id); list.children[i].scrollIntoView({ block: "nearest" }); }
    };
    const filter = () => {
      chosen = null;
      const q = input.value.trim(), terms = q.toLowerCase().split(/\s+/).filter(Boolean);
      matches = data.candidates.filter((c) => terms.every((w) => `${c.name} ${c.path}`.toLowerCase().includes(w))).slice(0, 8);
      if (asFolder(q)) matches = [{ path: q, name: q, folder: true }, ...matches];
      list.replaceChildren(...matches.map((c, i) => h("li", { id: `${listId}-${i}`, role: "option", "aria-selected": "false", class: "sp-option",
        onmousedown: (e) => { e.preventDefault(); choose(c); } },
        c.folder ? [icon("projects"), h("span", null, tx("Share the folder {path}", { path: h("b", null, c.path) }))]
          : [h("b", null, c.name), h("span", { class: "muted" }, ` ${tn(c.sessions, "{n} session", "{n} sessions", { n: fmtNum(c.sessions) })}`),
            h("small", { class: "muted sp-opt-path" }, shortPath(c.path))])),
        ...(q && !matches.length ? [h("li", { class: "sp-option muted", "aria-disabled": "true" }, t("No project matches. Type a folder, starting with / or ~, to share it."))] : []));
      list.hidden = !matches.length && !q; // focused and empty: the busiest projects; typed: matches, or why none
      input.setAttribute("aria-expanded", String(!list.hidden));
      mark(matches.length ? 0 : -1);
    };
    input.addEventListener("input", filter);
    input.addEventListener("focus", filter);
    input.addEventListener("blur", () => setTimeout(close, 100));
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (list.hidden) filter();
        if (matches.length) mark((active + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length);
      } else if (e.key === "Enter" && !list.hidden && active >= 0 && matches[active]) {
        e.preventDefault(); choose(matches[active]);
      } else if (e.key === "Escape" && !list.hidden) { e.preventDefault(); close(); }
    });
    const go2 = h("button", { class: "btn primary", type: "submit" }, t("Share project"));
    const form = h("form", { class: "sp-form", onsubmit: async (e) => {
      e.preventDefault();
      const q = input.value.trim();
      const f = chosen ? chosen.path : asFolder(q) ? q : matches.length === 1 ? matches[0].path : ""; // ~ is expanded by the hub
      if (!f) { toast(t("Pick a project from the list, or type its folder.")); input.focus(); return; }
      go2.disabled = true;
      const r = await send("/api/projects/shared/add", { folder: f });
      go2.disabled = false;
      if (!r) return;
      toast(r.result?.existed ? t("{name} was already shared.", { name: r.result.name })
        : t("Shared {name}: {n} sessions of this hub are in it. Give people access in People › Change.", { name: r.result.name, n: fmtNum(r.result.sessions ?? 0) }), 7000);
      projectsCache = null;
      draw(r);
    } }, h("div", { class: "sp-fields sp-combo" }, input, list), go2);
    return [h("div", { class: "subhead" }, t("Share another project")), form,
      h("p", { class: "muted" }, t("The folder and everything below it becomes one project. This computer's sessions there are filed under it, and other computers can add their own folder to it before anything was sent."))];
  };
  // a dedicated hub has no sessions of its own: a project there is a name its computers file their folders under
  const newProjectForm = () => {
    const name = h("input", { class: "input", name: "name", maxlength: "80", autocomplete: "off", spellcheck: "false",
      placeholder: t("Project name, e.g. Website"), "aria-label": t("Project name") });
    const go2 = h("button", { class: "btn primary", type: "submit" }, t("Create project"));
    const form = h("form", { class: "sp-form", onsubmit: async (e) => {
      e.preventDefault();
      if (!name.value.trim()) { name.focus(); return; }
      go2.disabled = true;
      const r = await send("/api/projects/shared/add", { name: name.value.trim() });
      go2.disabled = false;
      if (!r) return;
      toast(r.result?.existed ? t("{name} already exists.", { name: r.result.name })
        : t("Created {name}. Give people access in People, and have each computer add its folder to it.", { name: r.result.name }), 7000);
      projectsCache = null;
      draw(r);
    } }, h("div", { class: "sp-fields" }, name), go2);
    return [h("div", { class: "subhead" }, t("New project")), form,
      h("p", { class: "muted" }, tx("Each computer adds its own folder for the project with {command}. Once one computer has sent sessions from a git repository, other clones of it go there on their own.",
        { command: h("span", { class: "codeline" }, "interlatch hub add-folder <folder> --project <name>") }))];
  };
  const draw = (data) => {
    const here = data.here !== false, dedicatedHub = !!data.dedicated;
    box.replaceChildren(); append(box, [
      cardHead(dedicatedHub ? t("Projects") : t("Shared projects"), { iconName: "projects", hint: data.shared.length ? tn(data.shared.length, "{n} project", "{n} projects") : null }),
      h("p", null, dedicatedHub
        ? t("Each computer sends a project's analyzed sessions here as summaries and project lessons, never prompts or transcripts. Only the people given the project see them.")
        : data.store
        ? t("Only these projects leave this computer: each analyzed session's summary and project lessons go to the team store and to the people given the project. Prompts, transcripts and every other project stay here.")
        : t("Only these projects leave this computer: people given them see each analyzed session's summary and project lessons on this hub's dashboard. Prompts, transcripts and every other project stay here.")),
      data.shared.length ? h("ul", { class: "sp-list" }, data.shared.map((x) => row(x, here || dedicatedHub, dedicatedHub)))
        : h("p", { class: "muted" }, dedicatedHub ? t("No project yet.") : t("No project is shared yet.")),
      dedicatedHub ? newProjectForm() : here ? addForm(data)
        : h("p", { class: "muted" }, t("Projects are shared, or stop being shared, at the hub computer itself: it decides what leaves it."))]);
  };
  load();
  return box;
}

// =====================================================================================
// Suggestions (one approval queue of proposed fixes) and What goes wrong (recurring failure causes)
// =====================================================================================
const SG_KIND = { instruction: [t("Instruction"), "prompts"], config: [t("Config change"), "settings"], environment: [t("Setup step"), "command"] };
const SG_STATUS = [["new", t("To review")], ["applied", t("Applied")], ["done", t("Done")], ["stale", t("Stale")], ["dismissed", t("Dismissed")]];
const SG_STATUS_ICON = { new: "suggestions", applied: "completed", done: "completed", stale: "history", dismissed: "x" };
const FR_CATEGORY = { "tool-misuse": t("Tool use"), "mcp-config": t("MCP setup"), environment: t("Environment"), "agent-behaviour": t("Agent behaviour"),
  permissions: t("Permissions"), noise: t("Noise"), other: t("Other") };
const baseName = (p) => String(p || "").replace(/\/+$/, "").split("/").pop();
const homePath = (p) => shortPath(p).replace(/^\/home\/[^/]+/, "~");
const compactPath = (p) => { const x = homePath(p); return x.startsWith("~") ? x : `…/${x.split("/").slice(-2).join("/")}`; }; // for one-line rows
function sgKindChip(kind) {
  const [label, ic] = SG_KIND[kind] || [kind, "dot"];
  return h("span", { class: "kind-chip" }, icon(ic), label);
}
function sgTarget(x) { // where a suggestion goes, in a few words
  if (x.kind === "environment") return t("your shell (you run it)");
  if (!x.project_path) return compactPath(x.target_path);
  return `${baseName(x.project_path)}/${baseName(x.target_path)}`;
}
function sgGroupOf(x) { // [key, title, subtitle] of the file a suggestion changes
  if (x.kind === "environment") return ["__env__", t("Setup steps"), t("Interlatch shows the command; you run it and mark it done")];
  if (x.kind === "config") return [x.target_path, homePath(x.target_path), t("Claude Code's MCP servers")];
  const reader = /AGENTS\.md$/.test(x.target_path) ? t("Codex and other agents that read AGENTS.md") : "Claude Code";
  if (!x.project_path) return [x.target_path, homePath(x.target_path), t("Read by every {reader} session", { reader })];
  return [x.target_path, sgTarget(x), `${homePath(x.target_path)} · ${t("read by {reader} in this project", { reader })}`];
}
function sgEvidence(x) {
  const e = x.evidence || {};
  const since = x.status === "applied" && e.sessions_since_applied != null
    ? ` · ${e.sessions_since_applied ? tn(e.sessions_since_applied, "seen in {n} session since applied", "seen in {n} sessions since applied", { n: fmtNum(e.sessions_since_applied) }) : t("not seen since applied")}` : "";
  return (e.line || "") + since;
}
function sgHasWarnings(w, kind) { // anything that should be read before a one-click write
  w = w || {};
  return !!(w.public_repo || w.untracked || w.overlap || (kind === "instruction" && (w.sensitive || []).length));
}
function sgWarnings(w, kind) {
  w = w || {};
  const out = [];
  if (w.public_repo) out.push(h("span", { class: "badge critical", title: t("Anyone can read this repository, so anyone can read this line") }, h("span", { class: "sdot" }), t("Public repository")));
  if (kind === "instruction" && (w.sensitive || []).length) {
    out.push(h("span", { class: "badge serious", title: w.sensitive.join("\n") }, h("span", { class: "sdot" }), t("Looks sensitive: {items}", { items: `${w.sensitive.slice(0, 2).join(", ")}${w.sensitive.length > 2 ? "…" : ""}` })));
  }
  if (w.untracked) out.push(h("span", { class: "badge warning", title: t("git does not track this file, so the line stays on this computer") }, h("span", { class: "sdot" }), t("File not tracked by git")));
  if (w.overlap) out.push(h("span", { class: "badge muted-badge" }, h("span", { class: "sdot" }), t("Already covered in this file")));
  return out.length ? h("div", { class: "sg-warn" }, out) : null;
}
function diffEl(text) {
  if (!text) return h("div", { class: "sg-diff empty-diff" }, t("No change: the file already says this."));
  const lines = text.replace(/\n$/, "").split("\n");
  return h("pre", { class: "sg-diff", "aria-label": t("Changes to the file") }, lines.map((l) => {
    const cls = /^(\+\+\+|---)/.test(l) ? "dh" : l.startsWith("@@") ? "dhunk" : l[0] === "+" ? "dadd" : l[0] === "-" ? "ddel" : "";
    return h("span", { class: cls }, `${l}\n`);
  }));
}

let unseenCount = 0;
async function pollUnseen() {
  if (limited()) return; // suggestions are this hub's own, not theirs
  try { drawUnseen((await api("/api/suggestions/unseen")).unseen || 0); } catch (e) { /* an older server, or restarting */ }
}
function drawUnseen(n) {
  unseenCount = n;
  railBadge("suggestions", n, n ? t("Suggestions, {n} new", { n }) : t("Suggestions"));
}
function railBadge(section, n, label) { // a count on a rail icon, e.g. what's new there
  const a = $(`#rail a[data-section="${section}"]`);
  if (!a) return;
  let badge = a.querySelector(".rail-badge");
  a.setAttribute("aria-label", label);
  if (!n) { badge?.remove(); return; }
  if (!badge) a.append((badge = h("span", { class: "rail-badge", "aria-hidden": "true" })));
  badge.textContent = n > 99 ? "99+" : String(n);
}

// On a hub with people: what other computers sent (news.py), as a count on Team overview's rail icon and in the tab's
// title, and a toast for what comes in while the dashboard is open. The first poll only counts; later ones ask for what
// was added to since, and a row a computer keeps adding to is told by what it gained.
let newsUnseen = 0, newsCursor = null, newsVisit = null;
const newsKnown = new Map(); // row id -> the row as last seen here
async function pollNews() {
  if (!ME?.hub?.team) return;
  let r;
  try { r = await api("/api/team/news", newsCursor ? { after: newsCursor } : {}); } catch (e) { return; }
  const gained = [];
  for (const x of r.items) {
    const was = newsKnown.get(x.id);
    const d = { ...x, sessions: x.sessions - (was?.sessions || 0), lessons: x.lessons - (was?.lessons || 0) };
    if (newsCursor && (!was || d.sessions > 0 || d.lessons > 0)) gained.push(d);
    newsKnown.set(x.id, x);
  }
  newsCursor = r.now;
  drawNews(r.unseen);
  if (gained.length === 1) toast(newsText(gained[0]), 6000);
  else if (gained.length) toast(tn(gained.length, "{n} update from the team: see Team overview", "{n} updates from the team: see Team overview"), 6000);
}
function drawNews(n) {
  newsUnseen = n;
  railBadge("teamhome", n, n ? t("Team overview, {n} new", { n }) : t("Team overview"));
  document.title = newsPrefix() + document.title.replace(/^\(\d+\+?\) /, "");
}
function newsPrefix() { return newsUnseen ? `(${newsUnseen > 99 ? "99+" : newsUnseen}) ` : ""; }
function newsText(x) {
  const computer = x.computer || t("A computer");
  if (x.kind === "joined") return x.person ? t("{computer} joined the hub as {name}", { computer, name: x.person }) : t("{computer} joined the hub", { computer });
  const what = [x.sessions ? tn(x.sessions, "{n} session", "{n} sessions") : null, x.lessons ? tn(x.lessons, "{n} lesson", "{n} lessons") : null];
  return t("New from {who} in {project}: {what}", { who: x.person || computer, project: x.project, what: what.filter(Boolean).join(", ") });
}
function suggestionsChanged() { // counts in the sidebar and the rail follow an action
  if (sbSection === "suggestions") buildSidebar("suggestions");
  pollUnseen();
}

// The actions on one suggestion; each returns true when it worked (and says so)
const sgAct = {
  async apply(x, text) {
    const r = await post(`/api/suggestions/${x.id}/apply`, text != null ? { text } : {});
    if (!r.ok) { toast(t("Not applied: {error}", { error: r.error || t("unknown error") }), 6000); return false; }
    Object.assign(x, { status: "applied", applied_at: new Date().toISOString(), applied_text: r.applied_text ?? text ?? x.text, text: text ?? x.text });
    toast(t("Added to {path} (the previous file is backed up)", { path: homePath(r.path) }));
    return true;
  },
  async undo(x) {
    const r = await post(`/api/suggestions/${x.id}/unapply`);
    if (!r.ok) { toast(t("Could not undo: {error}", { error: r.error || t("unknown error") }), 6000); return false; }
    const was = x.status;
    Object.assign(x, { status: "new", applied_at: null, applied_text: null, dismissed_reason: null });
    toast(was === "applied" ? t("Taken back out of {path}", { path: homePath(r.path || x.target_path) }) : t("Back in the queue"));
    return true;
  },
  async dismiss(x) {
    const r = await post(`/api/suggestions/${x.id}/dismiss`);
    if (!r.ok) { toast(t("Could not dismiss: {error}", { error: r.error || t("unknown error") }), 6000); return false; }
    x.status = "dismissed";
    return true;
  },
  async done(x) {
    const r = await post(`/api/suggestions/${x.id}/done`);
    if (!r.ok) { toast(r.error || t("Could not mark it done"), 6000); return false; }
    Object.assign(x, { status: "done", applied_at: new Date().toISOString() });
    return true;
  },
};
async function busy(btn, label, fn) { // disable a button while its request runs
  const was = btn.textContent;
  btn.disabled = true;
  btn.textContent = label;
  try { return await fn(); } finally { if (btn.isConnected) { btn.disabled = false; btn.textContent = was; } }
}

// One suggestion: what it is, why, where it goes, the editable text, and Preview / Apply / Dismiss
function suggestionCard(x, { onStatus } = {}) {
  const e = x.evidence || {};
  const open = x.status === "new" || x.status === "stale";
  let warnBox = sgWarnings(x.warnings, x.kind);
  const diffBox = h("div", { class: "sg-diffbox", hidden: true });
  const rows = Math.min(8, Math.max(2, Math.ceil(String(x.text || "").length / 86)));
  const editor = x.kind === "instruction" && open
    ? h("textarea", { class: "input sg-text", rows, "aria-label": t("Line to add: {title}", { title: x.title }), spellcheck: "true" }, x.text) : null;
  const current = () => (editor ? editor.value.trim() : x.text);
  const generated = e.generated_text || x.text;
  let checked = generated; // the text the warnings on show were worked out for (refresh checks the proposed text)
  const showWarnings = (w) => {
    const fresh = sgWarnings({ ...x.warnings, ...w }, x.kind);
    if (warnBox) warnBox.replaceWith(fresh || h("span")); else if (fresh) diffBox.before(fresh);
    warnBox = fresh;
  };
  // Edited text is checked before it is written: new sensitive hits are shown, and a second Apply confirms them
  const checkBeforeApply = async () => {
    const text = current();
    if (!editor || text === checked) return true;
    const r = await api(`/api/suggestions/${x.id}/preview`, { text }).catch((err) => ({ error: err.message }));
    if (!r.ok) { toast(t("Not applied: {error}", { error: r.error || t("could not check the text") }), 6000); return false; }
    checked = text;
    showWarnings(r.warnings);
    if (!(r.warnings?.sensitive || []).length) return true;
    toast(t("Your text looks sensitive: read the warning, then Apply again to write it"), 6000);
    return false;
  };
  const reset = h("button", { class: "link-btn", type: "button", hidden: !editor || x.text === generated, onclick: async () => {
    editor.value = generated;
    await post(`/api/suggestions/${x.id}/edit`, { text: generated });
    x.text = generated;
    reset.hidden = true;
    diffBox.hidden = true;
  } }, t("Reset to the proposed text"));
  editor?.addEventListener("change", async () => { // keep your wording, so the next refresh does not replace it
    const text = current();
    if (!text || text === x.text) return;
    const r = await post(`/api/suggestions/${x.id}/edit`, { text });
    if (r.ok) { x.text = text; reset.hidden = text === generated; }
    diffBox.hidden = true;
  });
  const redraw = (prev) => {
    const next = suggestionCard(x, { onStatus });
    card.replaceWith(next);
    onStatus?.(prev, x.status);
    suggestionsChanged();
  };
  const act = (verb, label, cls = "btn small") => {
    const b = h("button", { class: cls, type: "button", onclick: () => busy(b, label, async () => {
      const prev = x.status;
      if (verb === "apply" && !(await checkBeforeApply())) return;
      if (await sgAct[verb](x, verb === "apply" && editor ? current() : undefined)) redraw(prev);
    }) }, { apply: t("Apply"), undo: x.status === "dismissed" ? t("Restore") : t("Undo"), dismiss: t("Dismiss"), done: t("Mark done") }[verb]);
    return b;
  };
  const preview = h("button", { class: "btn small", type: "button", "aria-expanded": "false", onclick: () => {
    if (!diffBox.hidden) { diffBox.hidden = true; preview.setAttribute("aria-expanded", "false"); preview.textContent = t("Preview"); return; }
    return busy(preview, t("Loading…"), async () => {
      const r = await api(`/api/suggestions/${x.id}/preview`, editor ? { text: current() } : null).catch((err) => ({ error: err.message }));
      diffBox.replaceChildren(r.ok ? diffEl(r.diff) : h("div", { class: "warn-line" }, icon("gotcha"), ` ${r.error || t("Could not preview")}`));
      if (r.ok && x.kind === "instruction") { // the warnings follow the text as you edit it
        showWarnings(r.warnings);
        if (editor) checked = current();
      }
      diffBox.hidden = false;
    }).then(() => { if (!diffBox.hidden) { preview.textContent = t("Hide preview"); preview.setAttribute("aria-expanded", "true"); } });
  } }, t("Preview"));
  let body;
  if (x.kind === "environment") {
    const copy = h("button", { class: "btn small", type: "button", onclick: () => copyText(x.text, copy) }, t("Copy"));
    body = h("div", { class: "sg-cmd" }, h("pre", { class: "sg-code" }, x.text), open ? copy : null);
  } else if (editor) body = editor;
  else body = h("pre", { class: "sg-code" }, x.status === "applied" ? x.applied_text || x.text : x.text);
  let actions;
  if (x.status === "applied" || x.status === "done") {
    actions = [h("span", { class: "muted" }, x.status === "done" ? t("Marked done {ago}", { ago: ago(x.applied_at) }) : t("Applied {ago}", { ago: ago(x.applied_at) })), act("undo", t("Undoing…"))];
  } else if (x.status === "dismissed") {
    actions = [h("span", { class: "muted" }, x.dismissed_reason ? t("Dismissed: {reason}", { reason: x.dismissed_reason }) : t("Dismissed")), act("undo", t("Restoring…"))];
  } else if (x.kind === "environment") {
    actions = [act("done", t("Saving…"), "btn small primary"), act("dismiss", t("Dismissing…"))];
  } else {
    actions = [preview, act("apply", t("Applying…"), "btn small primary"), act("dismiss", t("Dismissing…"))];
  }
  // where the line goes: your user-level file, read in every project, or the files of the projects it came from
  if (x.status === "new" && x.kind === "instruction" && (x.origin === "friction" || x.origin === "knowledge")) {
    const toUser = !!x.project_path, names = e.project_names || [];
    const move = h("button", { class: "link-btn sg-move", type: "button",
      title: toUser ? t("Put it in your user-level file instead, which every project reads")
        : t("Put it in the file of each project it came from instead (up to 5)"),
      onclick: () => busy(move, t("Moving…"), async () => {
        const r = await post(`/api/suggestions/${x.id}/move`, { to: toUser ? "user" : "project" });
        if (!r.ok) { toast(t("Could not move it: {error}", { error: r.error || t("unknown error") }), 6000); return; }
        toast(r.moved ? t("Moved to {targets}", { targets: r.targets.map(homePath).join(", ") }) : t("Moved, but it was dismissed or applied there before"), 5000);
        suggestionsChanged();
        render();
      }) }, toUser ? t("Move to every project") : e.projects === 1 && names[0] ? t("Move to {name} only", { name: names[0] }) : t("Move to its projects"));
    actions.push(move);
  }
  const examples = (e.examples || []).slice(0, 2);
  const card = h("article", { class: `sg-card st-${x.status}`, id: `sg-${x.id}` },
    h("div", { class: "sg-head" }, sgKindChip(x.kind),
      h("span", { class: "tag" }, x.origin === "knowledge" ? t("from knowledge") : t("from what goes wrong")),
      x.agent && x.agent !== "all" ? agentTag(x.agent) : null,
      x.status === "stale" ? h("span", { class: "badge muted-badge", title: t("The cause has not been seen lately") }, h("span", { class: "sdot" }), t("not seen lately")) : null),
    h("div", { class: "sg-title" }, x.title),
    h("div", { class: "sg-evidence" }, sgEvidence(x)),
    examples.length ? h("ul", { class: "sg-examples one-line" }, examples.map((ex) => h("li", { title: ex.note || null },
      h("a", { href: `#/session/${ex.session_id}` }, ex.title || ex.session_id.slice(0, 8)),
      ex.note ? h("span", { class: "muted" }, ` · ${ex.note}`) : null))) : null,
    warnBox, body, open ? reset : null, diffBox,
    h("div", { class: "sg-actions" }, actions));
  return card;
}

// A compact row of the Home card: the top few, approved or dismissed in place
function suggestionRow(x) {
  const row = h("div", { class: "sg-row" });
  const kindIcon = () => h("span", { class: "sg-row-icon", title: SG_KIND[x.kind]?.[0] }, icon(SG_KIND[x.kind]?.[1] || "dot"));
  const draw = (note) => {
    row.classList.toggle("settled", !!note);
    if (note) {
      row.replaceChildren(kindIcon(), h("div", { class: "sg-row-main" }, h("div", { class: "t" }, x.title), h("div", { class: "m" }, note)),
        h("div", { class: "sg-row-act" }, x.status === "applied" || x.status === "dismissed" ? h("button", { class: "link-btn", type: "button", onclick: async () => {
          if (await sgAct.undo(x)) { draw(); suggestionsChanged(); }
        } }, x.status === "applied" ? t("Undo") : t("Restore")) : null));
      return;
    }
    const act = (verb, label, busyLabel, cls) => {
      const b = h("button", { class: cls, type: "button", onclick: () => busy(b, busyLabel, async () => {
        if (!(await sgAct[verb](x))) return;
        draw(verb === "apply" ? t("Applied to {path}", { path: homePath(x.target_path) }) : verb === "done" ? t("Marked done") : t("Dismissed"));
        suggestionsChanged();
      }) }, label);
      return b;
    };
    const copy = h("button", { class: "btn small", type: "button", onclick: () => copyText(x.text, copy) }, t("Copy command"));
    // a warning (public repo, sensitive text, untracked file) is read on the full card before anything is written
    const warned = sgHasWarnings(x.warnings, x.kind);
    const approve = warned ? h("a", { class: "btn small primary", href: `#/suggestions?focus=${x.id}`, title: t("Read the warnings and the change first") }, t("Review"))
      : act("apply", t("Approve"), t("Approving…"), "btn small primary");
    row.replaceChildren(kindIcon(),
      h("div", { class: "sg-row-main" }, h("a", { class: "t", href: `#/suggestions?focus=${x.id}` }, x.title),
        h("div", { class: "m" }, `${sgTarget(x)} · ${x.evidence?.line || ""}`), warned ? sgWarnings(x.warnings, x.kind) : null),
      h("div", { class: "sg-row-act" }, x.kind === "environment" ? copy : approve, act("dismiss", t("Dismiss"), t("Dismissing…"), "btn small")));
  };
  draw();
  return row;
}
async function suggestionsHomeCard(box, project) {
  if (limited()) return; // the hub's own fixes, not theirs
  let data;
  // the top 3 are all Home shows; a project page filters in the browser, so it takes the whole list
  try { data = await api("/api/suggestions", project ? { status: "new" } : { status: "new", limit: 3 }); } catch (e) { return; }
  const items = data.suggestions.filter((x) => !project || !x.project_path || x.project_path === project);
  if (!items.length) return;
  const total = project ? items.length : (data.total ?? items.length);
  box.classList.add("section-gap");
  box.replaceChildren(h("section", { class: "card sg-home" },
    cardHead(t("Suggestions"), { iconName: "suggestions", hint: t("{n} to review · nothing is written until you approve", { n: fmtNum(total) }),
      tools: h("a", { href: "#/suggestions", class: "hint link-arrow" }, t("All suggestions"), icon("arrow")) }),
    h("div", { class: "sg-rows" }, items.slice(0, 3).map((x) => suggestionRow(x)))));
}

route(/^\/suggestions$/, async (params) => {
  const status = params.status || "new", scope = params.scope || "", cause = params.cause || "", focus = +params.focus || 0;
  const data = await api("/api/suggestions", { status });
  post("/api/suggestions/seen").then(() => drawUnseen(0)).catch(() => {});
  const counts = data.counts;
  const update = (patch) => { setParams({ status: status === "new" ? "" : status, scope, cause, ...patch }); render(); };
  const items = data.suggestions.filter((x) => (!cause || x.cause_id === cause)
    && (!scope || (scope === "user" ? !x.project_path : x.project_path === scope)));
  const projects = [...new Set(data.suggestions.map((x) => x.project_path).filter(Boolean))].sort((a, b) => baseName(a).localeCompare(baseName(b)));
  const chipCount = {};
  // one joined control, like the period pickers: it reads as "pick one view", not as five more buttons
  const chips = h("div", { class: "filters sg-filters" }, h("div", { class: "seg sg-status", role: "group", "aria-label": t("Status") }, SG_STATUS.map(([v, label]) => {
    chipCount[v] = h("span", { class: "count" }, fmtNum(counts[v] || 0));
    return h("button", { type: "button", class: v === status ? "on" : "", "aria-pressed": String(v === status), onclick: () => update({ status: v === "new" ? "" : v }) }, label, chipCount[v]);
  })), h("span", { class: "spacer" }),
  projects.length ? h("select", { "aria-label": t("Where"), onchange: (ev) => update({ scope: ev.target.value }) },
    h("option", { value: "" }, t("Everywhere")), h("option", { value: "user", selected: scope === "user" }, t("User level (every project)")),
    projects.map((p) => h("option", { value: p, selected: p === scope }, baseName(p)))) : null);
  const onStatus = (prev, next) => { // keep the status counts honest as cards change in place
    counts[prev] = Math.max(0, (counts[prev] || 0) - 1);
    counts[next] = (counts[next] || 0) + 1;
    for (const [v] of SG_STATUS) chipCount[v].textContent = fmtNum(counts[v] || 0);
  };
  const refreshBtn = h("button", { class: "btn admin-only", type: "button", title: t("Look at the latest sessions and knowledge for new suggestions"), onclick: () => busy(refreshBtn, t("Checking…"), async () => {
    const r = await post("/api/suggestions/refresh");
    toast(r.error ? t("Could not check: {error}", { error: r.error }) : t("{new} new, {updated} updated, {stale} no longer happening", { new: fmtNum(r.new), updated: fmtNum(r.updated), stale: fmtNum(r.stale) }));
    suggestionsChanged();
    render();
  }) }, t("Check again"));
  const groups = new Map();
  for (const x of items) {
    const [key, title, sub] = sgGroupOf(x);
    if (!groups.has(key)) groups.set(key, { title, sub, items: [] });
    groups.get(key).items.push(x);
  }
  const titles = {};
  for (const g of groups.values()) titles[g.title] = (titles[g.title] || 0) + 1;
  for (const [key, g] of groups) if (titles[g.title] > 1) g.title = homePath(key); // two projects with one name: the whole path
  const groupCard = (g) => {
    const shown = g.items.some((x, i) => i >= 4 && x.id === focus) ? g.items.length : 4; // the one opened from Home is never folded away
    const list = h("div", { class: "sg-list" }, g.items.slice(0, shown).map((x) => suggestionCard(x, { onStatus })));
    const more = g.items.length > shown ? h("button", { class: "link-btn sg-more", type: "button", onclick: () => {
      list.append(...g.items.slice(4).map((x) => suggestionCard(x, { onStatus })));
      more.remove();
    } }, t("Show {n} more", { n: g.items.length - 4 })) : null;
    return h("section", { class: "card sg-group" },
      h("div", { class: "sg-ghead" }, h("div", { style: { minWidth: 0 } }, h("h2", { title: g.title }, g.title), h("div", { class: "muted" }, g.sub)),
        h("span", { class: "hint" }, tn(g.items.length, "{n} suggestion", "{n} suggestions", { n: fmtNum(g.items.length) }))),
      list, more);
  };
  if (focus) setTimeout(() => { // opened from Home: show that card
    const card = document.getElementById(`sg-${focus}`);
    if (!card) return;
    setParams({ status: status === "new" ? "" : status, scope, cause });
    card.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
    card.classList.add("flash");
    setTimeout(() => card.classList.remove("flash"), 1600);
  }, 60);
  const statusName = SG_STATUS.find(([v]) => v === status)?.[1].toLowerCase() || status;
  const empty = h("div", { class: "card empty sg-empty" },
    !counts.new && !counts.applied && !counts.dismissed && !counts.done && !counts.stale
      ? [t("Nothing to suggest yet. After a sync, Interlatch proposes a fix once a failure keeps coming back, or once knowledge is confirmed often enough to belong in an instruction file.")]
      : [cause || scope ? t("Nothing {status} here.", { status: statusName }) : t("Nothing {status}.", { status: statusName }), cause || scope ? [" ", h("a", { href: "#/suggestions" }, t("Show everything"))] : null]);
  return h("div", { class: "sg-page" },
    h("div", { class: "page-head" }, titled("lumi", h("h1", null, t("Suggestions")),
      h("div", { class: "sub" }, t("Fixes for what keeps going wrong, and knowledge worth telling your agents. Nothing is written until you apply it; the file is backed up first."))),
      h("div", { class: "head-actions" }, h("a", { class: "link-arrow sg-why", href: "#/friction" }, t("What goes wrong"), icon("arrow")), refreshBtn)),
    chips,
    cause ? h("div", { class: "sg-scope muted" }, t("Only the fixes for “{cause}”. ", { cause: items[0]?.evidence?.cause || cause }), h("a", { href: "#/suggestions" }, t("Show all"))) : null,
    groups.size ? h("div", { class: "sg-groups" }, [...groups.values()].map(groupCard)) : empty);
});

// What goes wrong: recurring causes, trends, and the tools that fail most
function causeDetail(c) {
  const ex = c.examples || [];
  return h("div", { class: "fr-detail" },
    h("div", { class: "fr-facts" },
      fact(t("of sessions in those projects"), pctText(c.rate || 0, 1)),
      fact(t("occurrences"), fmtNum(c.occurrences)),
      c.immediate_repeats ? fact(t("retried the same way"), fmtNum(c.immediate_repeats)) : null,
      fact(t("first seen"), c.first_seen || "–")),
    c.agents?.length ? h("div", { class: "fr-line" }, h("span", { class: "muted" }, t("Agents"), " "), c.agents.map((a) => h("span", { class: "tag" }, agentShort(a)))) : null,
    c.projects?.length ? h("div", { class: "fr-line" }, h("span", { class: "muted" }, t("Projects"), " "), c.projects.slice(0, 12).map((p) => h("span", { class: "tag" }, p)),
      c.projects.length > 12 ? h("span", { class: "muted" }, ` +${c.projects.length - 12}`) : null) : null,
    ex.length ? [h("div", { class: "subhead" }, t("Examples")), h("ul", { class: "sg-examples" }, ex.slice(0, 5).map((x) => h("li", null,
      h("a", { href: `#/session/${x.session_id}` }, x.title || x.session_id.slice(0, 8)), x.note ? h("span", { class: "muted" }, ` · ${x.note}`) : null)))] : null,
    c.fixes?.length ? [h("div", { class: "subhead" }, t("Fixes")), h("ul", { class: "fr-fixes" }, c.fixes.map((f) => h("li", null, sgKindChip(f.kind), " ", f.title)))] : null);
}
function causesTable(causes, links) {
  const max = Math.max(1, ...causes.map((c) => c.sessions));
  const trendOf = (c) => (c.weekly || []).map((w) => w.sessions);
  return localTable(causes, [
    { key: "name", label: t("Cause"), value: (c) => c.name },
    { key: "sessions", label: t("Sessions"), num: true, desc: true, value: (c) => c.sessions },
    { key: "projects", label: t("Projects"), num: true, desc: true, value: (c) => c.projects.length },
    { key: "trend", label: t("Last 12 weeks") },
    { key: "last", label: t("Last seen"), desc: true, value: (c) => c.last_seen || "" },
    { key: "fix", label: t("Fix") },
  ], (c) => {
    const n = links[c.id] || { open: 0, applied: 0 };
    const trend = trendOf(c);
    const fix = c.noise ? h("span", { class: "muted", title: t("Expected failures need no fix") }, "–") : n.open ? h("a", { href: `#/suggestions?cause=${encodeURIComponent(c.id)}` }, t("{n} to review", { n: n.open }))
      : n.applied ? h("a", { href: `#/suggestions?status=applied&cause=${encodeURIComponent(c.id)}` }, t("applied"))
      : h("span", { class: "muted", title: c.category === "other" ? t("Recurring, but there is no known fix to propose") : t("Not frequent enough to propose a fix") }, "–");
    return expandable(h("tr", null,
      h("td", { class: "title-cell" }, h("div", { class: "t" }, c.name),
        h("div", { class: "fr-sub" }, h("span", { class: "tag" }, FR_CATEGORY[c.category] || c.category),
          c.still_happening && !c.noise ? h("span", { class: "badge serious" }, h("span", { class: "sdot" }), t("still happening")) : h("span", { class: "badge muted-badge" }, h("span", { class: "sdot" }), c.noise ? t("expected") : t("quiet lately")))),
      barCell(fmtNum(c.sessions), c.sessions, max),
      h("td", { class: "num" }, fmtNum(c.projects.length)),
      h("td", { class: "fr-trend", title: t("Sessions per week: {list}", { list: trend.join(", ") }) }, sparkEl(trend, { height: 26 })),
      h("td", { class: "nowrap" }, c.last_seen ? fmtDate(c.last_seen + "T12:00:00") : "–"),
      h("td", { class: "nowrap" }, fix)), 6, () => causeDetail(c));
  }, { empty: t("No recurring causes in this period") });
}
route(/^\/friction$/, async (params) => {
  const days = params.days || "90", project = params.project || "", noise = params.noise === "1";
  const [data, sg] = await Promise.all([
    api("/api/friction", { days: days === "all" ? "" : days, project, noise: 1 }),
    api("/api/suggestions").catch(() => ({ suggestions: [] })),
  ]);
  const update = (patch) => { setParams({ days, project, noise: noise ? "1" : "", ...patch }); render(); };
  const links = {};
  for (const x of sg.suggestions) {
    if (!x.cause_id) continue;
    const n = (links[x.cause_id] ||= { open: 0, applied: 0 });
    if (x.status === "new" || x.status === "stale") n.open++;
    if (x.status === "applied" || x.status === "done") n.applied++;
  }
  const waste = data.causes.filter((c) => !c.noise), noiseCauses = data.causes.filter((c) => c.noise);
  const ns = data.noise_summary || {};
  const live = waste.filter((c) => c.still_happening && c.category !== "other").length;
  const toolMax = Math.max(1, ...data.tool_errors.map((t) => t.errors));
  const toolName = (t) => t.replace(/^mcp__(.+?)__/, (m, srv) => `${srv.replace(/^claude_ai_/, "")} · `);
  const tools = h("section", { class: "card" }, cardHead(t("Tools that fail most"), { iconName: "zap", hint: t("failed calls, duplicates counted once") }),
    localTable(data.tool_errors, [
      { key: "tool", label: t("Tool"), value: (te) => te.tool },
      { key: "calls", label: t("Calls"), num: true, desc: true, value: (te) => te.calls },
      { key: "errors", label: t("Failed"), num: true, desc: true, value: (te) => te.errors },
      { key: "rate", label: t("Rate"), num: true, desc: true, value: (te) => te.rate },
    ], (t) => h("tr", null, h("td", { class: "mono fr-tool", title: t.tool }, toolName(t.tool)), h("td", { class: "num" }, fmtNum(t.calls)),
      barCell(fmtNum(t.errors), t.errors, toolMax), h("td", { class: "num" }, pctText(t.errors, t.calls))), { empty: t("No failed tool calls") }));
  const noiseBody = h("div", { hidden: !noise }, causesTable(noiseCauses, links));
  const noiseBtn = h("button", { class: "link-btn", type: "button", "aria-expanded": String(noise), onclick: () => {
    noiseBody.hidden = !noiseBody.hidden;
    noiseBtn.textContent = noiseBody.hidden ? t("Show") : t("Hide");
    noiseBtn.setAttribute("aria-expanded", String(!noiseBody.hidden));
    setParams({ days, project, noise: noiseBody.hidden ? "" : "1" });
  } }, noise ? t("Hide") : t("Show"));
  const noiseCard = h("section", { class: "card" }, cardHead(t("Noise"), { iconName: "skipped", hint: t("expected failures, left out above"), tools: noiseCauses.length ? noiseBtn : null }),
    h("p", { class: "fr-noise muted" }, ns.occurrences
      ? t("{n} failures in {sessions} sessions were expected: tests failing inside a dev loop, read-only command chains that ended non-zero, provider outages and tool calls you turned down.", { n: fmtNum(ns.occurrences), sessions: fmtNum(ns.sessions) })
      : t("No expected failures in this period.")),
    noiseBody);
  const sgLink = h("a", { href: "#/suggestions" }, t("Suggestions"));
  return h("div", null,
    h("div", { class: "page-head" }, titled("glitch", h("h1", null, t("What goes wrong")),
      h("div", { class: "sub" }, live ? tx("Recurring failure causes across your sessions, noise kept apart. {n} still happening; fixes wait in {link}.", { n: String(live), link: sgLink })
        : tx("Recurring failure causes across your sessions, noise kept apart. Fixes wait in {link}.", { link: sgLink }))),
      h("div", { class: "head-actions" },
        segControl([["30", t("30d")], ["90", t("90d")], ["180", t("180d")], ["all", t("All")]], days, (v) => update({ days: v })),
        h("select", { "aria-label": t("Project"), onchange: (e) => update({ project: e.target.value }) }, await projectOptions(project)))),
    h("section", { class: "card" }, cardHead(t("Causes"), { iconName: "gotcha", hint: t("click a row for examples and fixes") }), causesTable(waste, links)),
    h("div", { class: "section-gap" }, tools), h("div", { class: "section-gap" }, noiseCard));
});

// =====================================================================================
// Storage: a copy of the archive in a Postgres database you choose (mirror.py). The password goes in, never out.
// =====================================================================================
route(/^\/storage$/, async () => {
  const mi = await api("/api/mirror");
  return h("div", { class: "narrow-page" },
    h("div", { class: "page-head" }, titled("rack", h("h1", null, t("Storage")),
      h("div", { class: "sub" }, t("Interlatch keeps your archive in its own database on this computer. A copy can go to Postgres as well.")))),
    mirrorCard(mi));
});

function mirrorCard(mi) {
  const saved = mi.settings || {};
  let mode = mi.enabled ? "postgres" : "off";
  let include = mi.include;
  const counts = (c) => [
    c.sessions != null ? tn(c.sessions, "{n} session", "{n} sessions", { n: fmtNum(c.sessions) }) : null,
    c.knowledge != null ? tn(c.knowledge, "{n} lesson", "{n} lessons", { n: fmtNum(c.knowledge) }) : null,
    c.artifacts != null ? tn(c.artifacts, "{n} artifact", "{n} artifacts", { n: fmtNum(c.artifacts) }) : null,
    c.events != null ? tn(c.events, "{n} transcript event", "{n} transcript events", { n: fmtNum(c.events) }) : null].filter(Boolean).join(" · ");
  const last = mi.last ? (mi.last.error
    ? h("div", { class: "warn-line" }, t("Last write failed {ago}: {error}", { ago: ago(mi.last.at), error: mi.last.error }))
    : mi.last.at ? h("div", { class: "muted" }, t("Last written {ago}", { ago: ago(mi.last.at) }) + " · "
      + tn(mi.last.sessions || 0, "{n} session updated", "{n} sessions updated", { n: fmtNum(mi.last.sessions || 0) })) : null) : null;
  const status = h("div", { class: "status-list" }, h("div", { class: "muted" }, mi.enabled ? t("Checking the mirror…")
    : t("Off: your archive is kept on this computer only.")));
  if (mi.enabled) api("/api/mirror/status").then((s) => status.replaceChildren(...(s.error
    ? [h("div", { class: "warn-line" }, s.error), last]
    : [h("div", null, t("On · {where} · PostgreSQL {server} · schema {schema}", { where: s.where, server: s.server, schema: s.schema })),
      Object.keys(s.counts || {}).length ? h("div", { class: "muted" }, counts(s.counts)) : null, last])))
    .catch((e) => status.replaceChildren(h("div", { class: "warn-line" }, e.message)));
  const head = [
    h("p", null, t("Keep a copy of your archive in a Postgres database you choose: on this computer, in Docker, or in the cloud. Query it with SQL or a BI tool, or keep it as a copy somewhere else. Interlatch writes it after every background run and never reads it back.")),
    status,
    mi.driver ? null : h("div", { class: "warn-line" }, t("The Postgres driver isn't installed here. Run {command}, then restart the dashboard.", { command: mi.install }))];
  const field = (key, label, attrs = {}) => h("label", null, label, h("input", { class: "input", name: key, value: saved[key] || "", autocomplete: "off", spellcheck: "false", ...attrs }));
  const ssl = h("select", { name: "sslmode" }, mi.sslmodes.map((m) => h("option", { value: m, selected: m === (saved.sslmode || "require") }, m)));
  const form = h("form", { class: "ts-form", onsubmit: (e) => e.preventDefault() },
    field("host", t("Server address"), { placeholder: "127.0.0.1" }),
    field("port", t("Port"), { inputmode: "numeric", placeholder: "5432", value: saved.port || "5432" }),
    field("dbname", t("Database")),
    field("user", t("User")),
    h("label", null, t("Password"), h("input", { class: "input", name: "password", type: "password", autocomplete: "new-password",
      placeholder: saved.password_set ? t("saved; leave empty to keep it") : "" })),
    h("label", null, t("SSL mode"), ssl),
    field("schema", t("Schema"), { value: mi.schema, placeholder: "chronicle" }));
  const holds = { knowledge: t("Each session's details, summary and analysis, your lessons, knowledge bases, weekly reviews, glossary, artifacts and token usage. No prompts, no transcripts."),
    everything: t("All of that, plus your prompts and full transcripts, with secrets redacted as they are before analysis.") };
  const holdsNote = h("div", { class: "muted" }, holds[include]);
  // what a full transcript can carry, said before it goes: shown while Everything is picked, and asked again on Save
  const everythingWarn = h("div", { class: "warn-line mirror-warn", role: "note", hidden: include !== "everything" }, icon("gotcha"), " ",
    t("Everything copies whole transcripts: your code, file contents, command output and whatever was pasted into a session. Redaction goes by patterns, so a password or key that looks like nothing in particular still goes. Pick it only for a database you run yourself or trust with all of that."));
  const includeSeg = segControl([["knowledge", t("Knowledge")], ["everything", t("Everything")]], include, (v) => {
    include = v;
    includeSeg.querySelectorAll("button").forEach((b, i) => { const on = (i === 0 ? "knowledge" : "everything") === v; b.className = on ? "on" : ""; b.setAttribute("aria-pressed", String(on)); });
    holdsNote.textContent = holds[v];
    everythingWarn.hidden = v !== "everything";
  });
  const what = h("div", { class: "set-row" }, h("div", null, h("b", null, t("What it holds")), holdsNote, everythingWarn), includeSeg);
  const docker = h("details", { class: "mirror-docker" }, h("summary", null, t("Postgres in Docker on this computer")),
    h("p", { class: "muted" }, t("Start one, then fill in server address 127.0.0.1, port 5432, database and user interlatch, the password you chose, and SSL mode disable.")),
    h("pre", { class: "mcp-code" }, "docker run -d --name interlatch-pg --restart unless-stopped \\\n  -e POSTGRES_USER=interlatch -e POSTGRES_DB=interlatch -e POSTGRES_PASSWORD=<a password> \\\n  -p 127.0.0.1:5432:5432 -v interlatch-pg:/var/lib/postgresql/data postgres:17"));
  const values = () => ({ ...Object.fromEntries([...form.querySelectorAll("input, select")].map((x) => [x.name, x.value])), include });
  const result = h("div", { class: "ts-result" });
  const say = (text, bad) => result.replaceChildren(h("div", { class: bad ? "warn-line" : "muted" }, text));
  const test = h("button", { class: "btn", type: "button", onclick: async () => {
    test.disabled = true;
    say(t("Connecting…"));
    const r = await post("/api/mirror/test", values()).catch((e) => ({ error: e.message }));
    test.disabled = false;
    if (!r.ok) { say(r.error || t("Could not connect"), true); return; }
    say(t("Connected to {where} (PostgreSQL {server}).", { where: r.where, server: r.server }) + " "
      + (r.mine ? t("This computer's mirror is there; saving brings it up to date.") : t("Its tables are created when you save.")));
  } }, t("Test connection"));
  const save = h("button", { class: "btn primary", type: "button", onclick: async () => {
    const turningOn = mode === "postgres" && include === "everything" && !(mi.enabled && mi.include === "everything");
    if (turningOn && !confirm(t("Copy your prompts and full transcripts to this database? Secrets that redaction misses go with them."))) return;
    save.disabled = true;
    say(mode === "off" ? t("Saving…") : t("Connecting and saving…"));
    const r = await post("/api/mirror/save", { enabled: mode === "postgres", ...(mode === "postgres" ? values() : {}) }).catch((e) => ({ error: e.message }));
    save.disabled = false;
    if (!r.ok) { say(r.error || t("Could not save"), true); return; }
    toast(mode === "postgres" ? t("Mirror on: writing the copy now.") : t("Mirror off. Its settings and the copy in Postgres are kept."));
    if (r.started) watchJob("mirror");
    render();
  } }, t("Save"));
  const now = mi.enabled ? h("button", { class: "btn", type: "button", disabled: mi.running, onclick: async () => {
    now.disabled = true;
    const r = await post("/api/mirror/sync").catch((e) => ({ error: e.message }));
    if (r.error) { now.disabled = false; say(r.error, true); return; }
    toast(r.started ? t("Writing the mirror…") : t("Already running"));
    watchJob("mirror");
  } }, t("Write now")) : null;
  const setup = h("div", { hidden: mode === "off" }, form, what, docker);
  const modes = segControl([["off", t("Off")], ["postgres", "Postgres"]], mode, (v) => {
    mode = v;
    modes.querySelectorAll("button").forEach((b, i) => { const on = (i === 0 ? "off" : "postgres") === v; b.className = on ? "on" : ""; b.setAttribute("aria-pressed", String(on)); });
    setup.hidden = test.hidden = v === "off";
    result.replaceChildren();
  });
  test.hidden = mode === "off";
  return h("section", { class: "card" }, cardHead(t("Copy in Postgres"), { iconName: "data" }), head,
    h("div", { class: "analyzer ts-modes" }, modes), setup,
    h("div", { class: "ts-actions" }, test, save, now,
      h("span", { class: "muted" }, tx("Saved in {file}, readable by your user only.", { file: h("span", { class: "codeline" }, mi.file) }))),
    result,
    h("p", { class: "muted" }, t("Sessions in projects you excluded never go. One computer per schema: give each computer a schema of its own.")));
}

// =====================================================================================
// Appearance: theme and language, remembered in this browser
// =====================================================================================
route(/^\/appearance$/, async () => {
  const root = document.documentElement;
  const theme = root.dataset.theme || "system";
  const setTheme = (v) => {
    if (v === "system") delete root.dataset.theme; else root.dataset.theme = v;
    try { v === "system" ? localStorage.removeItem("interlatch-theme") : localStorage.setItem("interlatch-theme", v); } catch (e) { /* private mode */ }
    themeChanged();
    render();
  };
  let motion = "system";
  try { const m = localStorage.getItem("interlatch-motion"); if (m === "on" || m === "off") motion = m; } catch (e) { /* private mode */ }
  const setMotion = (v) => {
    try { v === "system" ? localStorage.removeItem("interlatch-motion") : localStorage.setItem("interlatch-motion", v); } catch (e) { /* private mode */ }
    root.dataset.motion = v !== "system" ? v : matchMedia("(prefers-reduced-motion: reduce)").matches ? "off" : "on";
    render();
  };
  const row = (label, hint, control) => h("div", { class: "set-row" }, h("div", null, h("b", null, label), hint ? h("div", { class: "muted" }, hint) : null), control);
  return h("div", { class: "narrow-page" },
    h("div", { class: "page-head" }, h("div", null, h("h1", null, t("Appearance")), h("div", { class: "sub" }, t("Saved in this browser (and in the app window).")))),
    h("section", { class: "card set-card" },
      row(t("Theme"), t("System follows your Mac's light or dark setting."),
        segControl([["system", t("System")], ["light", t("Light")], ["dark", t("Dark")]], theme, setTheme)),
      // the language names stay in their own language, so each is findable whichever one is showing
      row(t("Language"), t("System follows your browser's language. The page reloads to switch."),
        segControl([["system", t("System")], ["en", "English"], ["ja", "日本語"]], langPref(), (v) => { if (v !== langPref()) setLang(v); })),
      row(t("Animations"), t("System follows your Mac's Reduce motion setting. Off stops loading shimmers, slides and fades; spinners turn slowly."),
        segControl([["system", t("System")], ["on", t("On")], ["off", t("Off")]], motion, setMotion)),
      row(t("Sidebar"), t("The button at the top left, ⌘B, or the rail icon of the page you're on shows or hides it. While it's hidden, pointing at the rail brings it out until the pointer moves away."),
        h("button", { class: "btn", type: "button", onclick: toggleSidebar }, t("Toggle sidebar")))));
});

// =====================================================================================
// Shell: rail, section sidebar, toolbar, status bar, command palette
// =====================================================================================
const SECTIONS = [
  { key: "home", href: "#/", // a hub with people names it for what it shows, beside its Team overview
    get label() { return ME?.hub?.team ? t("Activity") : t("Home"); },
    get icon() { return ME?.hub?.team ? "status" : "home"; } },
  { key: "teamhome", label: t("Team overview"), href: "#/overview", icon: "overview", team: true },
  { key: "sessions", label: t("Sessions"), href: "#/sessions" },
  { key: "knowledge", label: t("Knowledge"), href: "#/knowledge" },
  { key: "artifacts", label: t("Artifacts"), href: "#/artifacts" },
  { key: "projects", label: t("Projects"), href: "#/projects" },
  { key: "suggestions", label: t("Suggestions"), href: "#/suggestions" },
  { key: "team", label: t("Team"), href: "#/team" }, // a hub's admins
  { key: "settings", label: t("Settings"), href: "#/status" },
];
const SECTION_OF = { overview: "home", activity: "home", teamhome: "teamhome", sessions: "sessions", knowledge: "knowledge", learn: "knowledge", artifacts: "artifacts", glossary: "knowledge", map: "knowledge", reviews: "knowledge",
  projects: "projects", systems: "projects", suggestions: "suggestions", friction: "suggestions", status: "settings", sources: "settings", mcp: "settings", devices: "settings", appearance: "settings", storage: "settings",
  team: "team", teamprojects: "team", teamcomputers: "team", teamstore: "team", teamsettings: "team" };
const PAGE_LABEL = { activity: t("Activity"), team: t("People"), get teamprojects() { return teamProjectsLabel(); }, teamcomputers: t("Computers"), teamstore: t("Team store"), teamsettings: t("Hub settings"), friction: t("What goes wrong"), glossary: t("Glossary"), map: t("Map"), systems: t("Systems"), reviews: t("Weekly reviews"), status: t("Status"), sources: t("Sources"), mcp: "MCP", devices: t("Devices"), storage: t("Storage"), appearance: t("Appearance") };
let shellSection = null, lastPath = null, lastHash = null, sbSeq = 0;
let sbSection = null; // the section the sidebar shows: the page's, or while it peeks, the one under the pointer

function sectionOf(path, params) {
  const key = navKey(path);
  if (key === "projects" && params.path === "__global__") return "knowledge"; // the global playbook is knowledge, not a project
  if (path.startsWith("/session/") && ["home", "teamhome"].includes(shellSection)) return shellSection; // opened from Home's list: keep that list
  return SECTION_OF[key] || null; // search and unknown pages: no section lit, the sidebar stays as it was
}
function sectionLink(key) { const sx = SECTIONS.find((x) => x.key === key); return sx ? [sx.label, sx.href] : ["Interlatch", "#/"]; }
function setCrumbs(items, token = renderSeq) { // [[label, href?], ...]; the last is the current page
  if (token !== renderSeq) return; // a view that finished after the user moved on
  const page = items.length ? items[items.length - 1][0] : "";
  document.title = newsPrefix() + (page && page !== t("Home") ? `${page} — Interlatch` : "Interlatch");
  $("#crumbs").replaceChildren(...items.flatMap(([label, href], i) => [
    i ? h("span", { class: "sep" }, "›") : null,
    href && i < items.length - 1 ? h("a", { href }, label) : h("b", { title: label }, label)]).filter(Boolean));
}
function defaultCrumbs(path, params) {
  const key = navKey(path), section = sectionOf(path, params);
  if (key === "overview") return [[sectionLink("home")[0]]];
  if (key === "teamhome") return [[t("Team overview")]];
  if (key === "learn") return [sectionLink("knowledge"), [t("Learn from your work")]];
  if (key === "activity" && ME?.hub?.team) return [[t("Activity")]]; // the section is Activity itself there
  if (key === "knowledge") return path === "/knowledge" ? [[t("Knowledge")]] : [sectionLink("knowledge"), [params.kind ? kindPlural(params.kind) : t("All knowledge")]];
  if (key === "projects" && path === "/projects") return [[t("Projects")]];
  if (key === "sessions" && path === "/sessions") return [[t("Sessions")]];
  if (path === "/search") return [[t("Search")], ...(params.q ? [[params.q]] : [])];
  if (!section) return [[t("Not found")]];
  if (PAGE_LABEL[key]) return [sectionLink(section), [PAGE_LABEL[key]]];
  return [sectionLink(section)];
}

function renderRail() {
  const rail = $("#rail");
  const link = (sx) => h("a", { href: sx.href, "data-section": sx.key, "aria-label": sx.label,
    onclick: (e) => railClick(e, sx) }, icon(sx.icon || sx.key));
  const settings = SECTIONS.find((x) => x.key === "settings");
  const shown = SECTIONS.filter((x) => (!x.team || ME?.hub?.team) && !(dedicated() && DEDICATED_HIDDEN.has(x.key)));
  if (limited()) { // no transcripts to search, no settings of this hub to see
    rail.replaceChildren(...shown.filter((x) => LIMITED_SECTIONS.has(x.key)).map(link));
    return;
  }
  const team = SECTIONS.find((x) => x.key === "team");
  rail.replaceChildren(...shown.filter((x) => x !== settings && x !== team).map(link),
    link({ key: "search", label: t("Search all sessions"), href: "#/search" }),
    h("div", { class: "spacer" }), ...(ME?.hub && canAdmin() ? [link(team)] : []), link(settings));
  drawUnseen(unseenCount);
  drawNews(newsUnseen);
}

// ------------------------------------------------------------------ sidebar
const sbState = { q: "", agent: "", offset: 0, total: 0, projectQ: "" };
function sbRow(label, href, iconName, count, match) {
  return h("a", { class: "sb-row", href, title: label, "data-route": match[0], "data-key": match[1] || "", "data-val": match[2] ?? "" },
    iconName ? icon(iconName) : null, h("span", null, label), count != null ? h("em", null, fmtNum(count)) : null);
}
function markSidebar(path, params) {
  document.querySelectorAll("#sidebar [data-route]").forEach((a) => {
    const on = a.dataset.route === path && (!a.dataset.key || (params[a.dataset.key] || "") === a.dataset.val);
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
}
function dayGroup(ts) {
  const d0 = new Date(); d0.setHours(0, 0, 0, 0);
  const ms = new Date(ts).getTime(), day = 86400000;
  if (ms >= d0.getTime()) return t("Today");
  if (ms >= d0.getTime() - day) return t("Yesterday");
  if (ms >= d0.getTime() - 7 * day) return t("Previous 7 days");
  if (ms >= d0.getTime() - 30 * day) return t("Previous 30 days");
  return new Intl.DateTimeFormat(LOCALE, { month: "long", year: "numeric" }).format(new Date(ts));
}
async function sessionsSidebar(box, title) {
  const list = h("div", { class: "sb-scroll" });
  const count = h("span");
  const more = h("button", { class: "sb-more", type: "button", onclick: () => load(true) }, t("Show more"));
  let lastGroup = null, debounce, loadSeq = 0;
  async function load(append) {
    const mine = ++loadSeq; // a newer filter or page wins; older responses are dropped
    const offset = append ? sbState.offset : 0;
    const data = await api("/api/sessions", { q: sbState.q, agent: sbState.agent, sort: "ended_at", limit: 60, offset });
    if (mine !== loadSeq) return;
    if (!append) { list.replaceChildren(); lastGroup = null; sbState.offset = 0; }
    more.remove();
    for (const x of data.items) {
      const g = dayGroup(x.ended_at || x.started_at); // by last activity, so a long-running live session stays under Today
      if (g !== lastGroup) { lastGroup = g; list.append(h("div", { class: "sb-group" }, g)); }
      const [cls, , label] = outcomeOf(x);
      list.append(h("a", { class: `sb-item s-${cls || "none"}`, href: `#/session/${x.id}`, "data-route": `/session/${x.id}`, title: x.title || t("(untitled session)") },
        h("span", { class: "odot", title: label }), h("b", null, x.title || t("(untitled session)")),
        h("small", null, [x.project_name, agentShort(x.agent), x.active_s ? fmtDur(x.active_s) : null].filter(Boolean).join(" · "))));
    }
    if (!data.items.length && !append) list.append(h("div", { class: "sb-empty" }, t("No sessions match")));
    sbState.offset += data.items.length;
    sbState.total = data.total;
    count.textContent = fmtNum(data.total);
    if (sbState.offset < data.total) list.append(more);
    const { path, params } = parseHash();
    markSidebar(path, params);
  }
  const input = h("input", { type: "search", placeholder: t("Filter sessions"), value: sbState.q, "aria-label": t("Filter sessions"), autocomplete: "off",
    oninput: (e) => { clearTimeout(debounce); debounce = setTimeout(() => { sbState.q = e.target.value.trim(); load(false); }, 250); } });
  const chips = h("div", { class: "sb-chips" }, [["", t("All")], ...Object.entries(AGENT_SHORT)].map(([v, l]) =>
    h("button", { type: "button", class: sbState.agent === v ? "on" : "", "aria-pressed": String(sbState.agent === v), onclick: (e) => {
      sbState.agent = v;
      chips.querySelectorAll("button").forEach((b) => { b.classList.toggle("on", b === e.currentTarget); b.setAttribute("aria-pressed", String(b === e.currentTarget)); });
      load(false);
    } }, l)));
  box.replaceChildren(h("div", { class: "sb-head" }, h("h2", null, title), count),
    h("label", { class: "sb-filter" }, icon("search"), input), chips, list);
  await load(false);
}
async function knowledgeSidebar(box) {
  const data = await api("/api/knowledge", { limit: 1 });
  const total = Object.values(data.counts).reduce((a, b) => a + b, 0);
  const kinds = Object.keys(KIND).filter((k) => data.counts[k]).map((k) => sbRow(kindPlural(k), `#/knowledge/all?kind=${k}`, k, data.counts[k], ["/knowledge/all", "kind", k]));
  if (limited()) { // the lessons of their projects; the glossary, map, playbook and reviews span every project
    box.replaceChildren(h("div", { class: "sb-head" }, h("h2", null, t("Knowledge")), h("span", null, fmtNum(total))),
      h("div", { class: "sb-scroll" }, sbRow(t("All knowledge"), "#/knowledge/all", "knowledge", total, ["/knowledge/all", "kind", ""]),
        sbRow(t("Learn from your work"), "#/learn", "learning", null, ["/learn"]),
        h("div", { class: "sb-group" }, t("Kinds")), kinds));
    return;
  }
  box.replaceChildren(h("div", { class: "sb-head" }, h("h2", null, t("Knowledge")), h("span", null, fmtNum(total))),
    h("div", { class: "sb-scroll" },
      sbRow(t("Overview"), "#/knowledge", "overview", null, ["/knowledge"]),
      sbRow(t("Learn from your work"), "#/learn", "learning", null, ["/learn"]),
      sbRow(t("All knowledge"), "#/knowledge/all", "knowledge", total, ["/knowledge/all", "kind", ""]),
      h("div", { class: "sb-group" }, t("Kinds")),
      kinds,
      h("div", { class: "sb-group" }, t("Explore")),
      sbRow(t("Glossary"), "#/glossary", "glossary", glossaryTerms?.length || null, ["/glossary"]),
      sbRow(t("Map"), "#/map", "map", null, ["/map"]),
      sbRow(t("Global playbook"), `#/project?path=${encodeURIComponent("__global__")}`, "playbook", null, ["/project", "path", "__global__"]),
      sbRow(t("Weekly reviews"), "#/reviews", "reviews", null, ["/reviews"])));
}
async function projectsSidebar(box) {
  const projects = await loadProjects();
  const list = h("div", { class: "sb-scroll" });
  const row = (p, sub = false) => {
    const a = sbRow(p.label, projectHref(p) || "#/projects", projIcon(p), p.sessions, ["/project", "path", p.project_path || ""]);
    if (sub) a.classList.add("sb-sub");
    if (p.hub) a.title = `${p.label} · ${hubTitle(p)}`;
    else if (p.shared) a.title = `${p.label} · ${p.sessions ? t("Shared from this hub") : t("Set up on the hub: no sessions yet")}`;
    return a;
  };
  // On a computer that sends to a hub: the hub's projects this computer's folders go to, each over those folders. A
  // hub project opens on the hub, signed in; the team's sessions there are as of the last push.
  const hubs = groupsHub ? byHubProject(projects) : [];
  const hubName = groupsHub?.name || t("Hub"), hubUrl = safeUrl(groupsHub?.url);
  const hubRow = (hp) => h("a", { class: "sb-row sb-hubproj", href: hubUrl ? `${hubUrl}/#${hubProjectPage(hp)}` : "#/devices",
    target: hubUrl ? "_blank" : null, rel: hubUrl ? "noopener" : null,
    title: [t("{name} on {hub}", { name: hp.name, hub: hubName }),
      hp.sessions != null ? tn(hp.sessions, "{n} session from the team", "{n} sessions from the team", { n: fmtNum(hp.sessions) }) : null,
      hubUrl ? t("Opens on the hub in a new tab") : null].filter(Boolean).join(" · "),
    onclick: (e) => { if (hubUrl) { e.preventDefault(); openHub(hubUrl, hubProjectPage(hp)); } } },
    icon("hubproject"), h("span", null, hp.name), hp.sessions != null ? h("em", null, fmtNum(hp.sessions)) : null, hubUrl ? icon("external", "sb-ext") : null);
  const draw = () => {
    const q = sbState.projectQ.toLowerCase();
    const match = (p) => !q || p.label.toLowerCase().includes(q) || (p.project_path || "").toLowerCase().includes(q);
    const top = [sbRow(t("All projects"), "#/projects", "overview", projects.length, ["/projects"]),
      sbRow(t("Systems map"), "#/systems", "systems", null, ["/systems"])];
    const fold = (key, label, n, open, { lead = null, title = null, cls = "" } = {}) => h("button", { type: "button", class: `sb-group sb-fold-head${cls}`,
      "aria-expanded": String(open), title, onclick: () => { foldGroup(key, open); draw(); } },
      icon("right", "sb-caret"), lead, h("span", null, label), h("em", null, fmtNum(n)));
    const hubShown = hubs.map(([hp, ps]) => [hp, q && hp.name.toLowerCase().includes(q) ? ps : ps.filter(match)]).filter(([, ps]) => ps.length);
    const hubOpen = !!q || !groupFolded("hub");
    const hubPart = hubShown.length ? h("div", { class: "sb-fold sb-hub" },
      fold("hub", hubName, hubs.length, hubOpen, { lead: icon("cloud", "sb-hub-ic"), cls: " sb-hub-head",
        title: t("The projects on {hub} that this computer's folders go to", { hub: hubName }) }),
      hubOpen ? hubShown.map(([hp, ps]) => [hubRow(hp), ...ps.map((p) => row(p, true))]) : null) : null;
    const groups = groupsCache.length || hubs.length ? byGroup(projects, { hub: true }).map(([g, ps]) => { // a group's name matches all of it
      const shown = q && g && g.name.toLowerCase().includes(q) ? ps : ps.filter(match);
      if (!shown.length && (q || !g)) return null;
      const key = g ? String(g.id) : "none", open = !!q || !groupFolded(key);
      return h("div", { class: "sb-fold" }, fold(key, g ? g.name : t("Other projects"), ps.length, open), open ? shown.map((p) => row(p)) : null);
    }).filter(Boolean) : projects.some(match) ? [h("div", { class: "sb-group" }, t("Most recent first")), ...projects.filter(match).map((p) => row(p))] : [];
    const parts = [hubPart, ...groups].filter(Boolean);
    list.replaceChildren(...top, ...parts,
      ...(parts.length ? [] : [h("div", { class: "sb-empty" }, t("No projects match"))])); // replaceChildren would print a null
    const { path, params } = parseHash();
    markSidebar(path, params);
  };
  box.replaceChildren(h("div", { class: "sb-head" }, h("h2", null, t("Projects")),
    h("div", { class: "sb-head-end" }, h("span", null, fmtNum(projects.length)),
      canGroup() ? h("button", { type: "button", class: "sb-add", title: t("New group"), "aria-label": t("New group"), onclick: () => groupDialog(null) }, icon("newGroup")) : null)),
    h("label", { class: "sb-filter" }, icon("search"), h("input", { type: "search", placeholder: t("Filter projects"), value: sbState.projectQ, "aria-label": t("Filter projects"),
      oninput: (e) => { sbState.projectQ = e.target.value; draw(); } })), list);
  draw();
}
async function suggestionsSidebar(box) {
  const { counts } = await api("/api/suggestions", { status: "done" }); // the smallest list that still carries every count
  box.replaceChildren(h("div", { class: "sb-head" }, h("h2", null, t("Suggestions")), h("span", null, fmtNum(counts.new || 0))),
    h("div", { class: "sb-scroll" },
      SG_STATUS.map(([v, label]) => sbRow(label, v === "new" ? "#/suggestions" : `#/suggestions?status=${v}`, SG_STATUS_ICON[v],
        counts[v] || 0, ["/suggestions", "status", v === "new" ? "" : v])),
      h("div", { class: "sb-group" }, t("Understand")),
      sbRow(t("What goes wrong"), "#/friction", "gotcha", null, ["/friction"])));
}
function settingsSidebar(box) {
  const own = !dedicated(); // sources, MCP and devices belong to a person's own computer, which a dedicated hub isn't
  box.replaceChildren(h("div", { class: "sb-head" }, h("h2", null, t("Settings"))),
    h("div", { class: "sb-scroll" },
      sbRow(t("Status"), "#/status", "status", null, ["/status"]),
      own ? sbRow(t("Sources"), "#/sources", "sources", null, ["/sources"]) : null,
      own ? sbRow("MCP", "#/mcp", "mcp", null, ["/mcp"]) : null,
      own ? sbRow(t("Devices"), "#/devices", "devices", null, ["/devices"]) : null,
      canAdmin() ? sbRow(t("Storage"), "#/storage", "data", null, ["/storage"]) : null, // a password: admins only
      sbRow(t("Appearance"), "#/appearance", "appearance", null, ["/appearance"])));
}
function teamSidebar(box) {
  box.replaceChildren(h("div", { class: "sb-head" }, h("h2", null, t("Team"))),
    h("div", { class: "sb-scroll" },
      sbRow(t("People"), "#/team", "preference", null, ["/team"]),
      sbRow(teamProjectsLabel(), "#/team/projects", "projects", null, ["/team/projects"]),
      sbRow(t("Computers"), "#/team/computers", "devices", null, ["/team/computers"]),
      sbRow(t("Team store"), "#/team/store", "data", null, ["/team/store"]),
      sbRow(t("Hub settings"), "#/team/settings", "settings", null, ["/team/settings"])));
}
async function buildSidebar(section) {
  const mine = ++sbSeq;
  sbSection = section;
  const box = h("div", { class: "sb-body" }); // drawn off-screen, swapped in only if still wanted
  try {
    if (section === "home" || section === "teamhome") await sessionsSidebar(box, t("Recent sessions"));
    else if (section === "sessions") await sessionsSidebar(box, t("Sessions"));
    else if (section === "knowledge") await knowledgeSidebar(box);
    else if (section === "projects") await projectsSidebar(box);
    else if (section === "artifacts") await artifactsSidebar(box);
    else if (section === "suggestions") await suggestionsSidebar(box);
    else if (section === "team") teamSidebar(box);
    else settingsSidebar(box);
  } catch (e) {
    box.replaceChildren(h("div", { class: "sb-empty" }, t("Could not load: {error}", { error: e.message })));
  }
  if (mine !== sbSeq) return;
  $("#sidebar").setAttribute("aria-label", t("{section} navigation", { section: SECTIONS.find((x) => x.key === section).label }));
  $("#sidebar").replaceChildren(box);
  const { path, params } = parseHash();
  markSidebar(path, params);
}
function toggleSidebar() {
  const root = document.documentElement;
  if (matchMedia("(max-width: 860px)").matches) root.classList.toggle("show-sidebar");
  else {
    const hidden = root.classList.toggle("no-sidebar");
    try { localStorage.setItem("interlatch-sidebar", hidden ? "0" : "1"); } catch (e) { /* private mode */ }
  }
  peekSidebar(false);
  sidebarExpanded();
}
// A rail icon toggles the sidebar, as the sidebar button would (wide windows: a narrow one's sidebar covers the page):
// a click on the page that's showing hides a shown sidebar; any other click opens a hidden one. Search has no sidebar.
function railClick(e, sx) {
  if (sx.key === "search") return;
  const root = document.documentElement;
  const here = (location.hash || "#/") === sx.href;
  if (here && !root.classList.contains("no-sidebar") && !matchMedia("(max-width: 860px)").matches) {
    e.preventDefault();
    toggleSidebar();
  } else showSidebar();
}
// A hidden sidebar opens for good. It was peeking already: it stays where it is, with the clicked section's lists.
function showSidebar() {
  const root = document.documentElement;
  if (!root.classList.contains("no-sidebar") || matchMedia("(max-width: 860px)").matches) return;
  root.classList.remove("no-sidebar");
  try { localStorage.setItem("interlatch-sidebar", "1"); } catch (e) { /* private mode */ }
  clearTimeout(peekTimer);
  clearTimeout(peekHoverTimer);
  root.classList.remove("peek-sidebar"); // not peekSidebar(false): that would draw the old page's lists first
  sidebarExpanded();
}
// a hidden sidebar peeks out while the pointer rests on the rail or on it, and hides once the pointer leaves both:
// only a click on an icon (showSidebar) or the sidebar button changes whether the sidebar is shown
let peekTimer = 0, peekHoverTimer = 0;
function peekSidebar(on, delay = 0) {
  clearTimeout(peekTimer);
  const set = () => {
    document.documentElement.classList.toggle("peek-sidebar", on);
    if (!on && shellSection && sbSection !== shellSection) buildSidebar(shellSection); // back to the page's own
  };
  if (delay) peekTimer = setTimeout(set, delay); else set();
}
function wirePeek() {
  const canPeek = () => document.documentElement.classList.contains("no-sidebar") && matchMedia("(hover: hover) and (min-width: 861px)").matches;
  for (const el of [$("#rail"), $("#sidebar")]) {
    el.addEventListener("mouseenter", () => { if (canPeek()) peekSidebar(true, el.id === "rail" ? 180 : 0); });
    el.addEventListener("mouseleave", () => { clearTimeout(peekHoverTimer); peekSidebar(false, 220); });
  }
  // while it peeks, the sidebar follows the icon under the pointer (search has none of its own: it keeps the last one)
  $("#rail").addEventListener("mouseover", (e) => {
    const key = e.target.closest("a[data-section]")?.dataset.section;
    if (!key || !canPeek() || !SECTIONS.some((x) => x.key === key)) return;
    clearTimeout(peekHoverTimer);
    if (key !== sbSection) peekHoverTimer = setTimeout(() => buildSidebar(key), 120);
  });
}
function sidebarExpanded() {
  const root = document.documentElement;
  const open = matchMedia("(max-width: 860px)").matches ? root.classList.contains("show-sidebar") : !root.classList.contains("no-sidebar");
  $("#sidebar-btn").setAttribute("aria-expanded", String(open));
}
function updateShell(path, params) {
  const section = sectionOf(path, params);
  document.querySelectorAll("#rail a").forEach((a) => {
    const on = a.dataset.section === (path === "/search" ? "search" : section); // search has no sidebar of its own
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  if (section && section !== shellSection) { shellSection = section; buildSidebar(section); }
  markSidebar(path, params);
  setCrumbs(defaultCrumbs(path, params));
  if (path !== lastPath) { $("#app").scrollTop = 0; lastPath = path; }
  if (location.hash !== lastHash) { // navigation, not a refresh after a background job
    lastHash = location.hash;
    document.documentElement.classList.remove("show-sidebar");
    sidebarExpanded();
    closePalette();
  }
}

// ------------------------------------------------------------------ command palette (⌘K, ⌘P, ⌘⇧P or /)
const pal = { sel: 0, items: [], seq: 0, open: false, timer: 0, query: "" };
function paletteCommands() {
  const dark = isDark();
  const goTo = t("Go to"), cmds = t("Commands"), model = t("uses the analysis model");
  const nav = (label, href, iconName, hint = "") => ({ group: goTo, label, hint, icon: iconName, href, run: () => go(href) });
  return [
    nav(sectionLink("home")[0], "#/", ME?.hub?.team ? "status" : "home"), { ...nav(t("Team overview"), "#/overview", "overview", t("team projects")), team: true }, nav(t("Sessions"), "#/sessions", "sessions"), nav(t("Knowledge"), "#/knowledge", "knowledge"), nav(t("All knowledge"), "#/knowledge/all", "knowledge"),
    nav(t("Learn from your work"), "#/learn", "learning"),
    nav(t("Glossary"), "#/glossary", "glossary"), nav(t("Map"), "#/map", "map"), nav(t("Projects"), "#/projects", "projects"), nav(t("Systems map"), "#/systems", "systems", t("every project, its parts and links")), nav(t("Artifacts"), "#/artifacts", "artifacts", t("what your agents made")),
    nav(t("Global playbook"), `#/project?path=${encodeURIComponent("__global__")}`, "playbook"), nav(t("Weekly reviews"), "#/reviews", "reviews"),
    nav(t("Suggestions"), "#/suggestions", "suggestions", t("fixes to approve")), nav(t("What goes wrong"), "#/friction", "gotcha", t("recurring failures")),
    nav(t("Status"), "#/status", "status"), nav(t("Sources"), "#/sources", "sources"), nav("MCP", "#/mcp", "mcp", t("connect other agents")), nav(t("Devices"), "#/devices", "devices", t("phone, other computers")), { ...nav(t("Storage"), "#/storage", "data", t("a copy in Postgres")), admin: true }, nav(t("Appearance"), "#/appearance", "appearance"),
    ...[[t("People"), "#/team", "preference", t("team")], [teamProjectsLabel(), "#/team/projects", "projects", t("team")], [t("Computers"), "#/team/computers", "devices", t("team")],
      [t("Team store"), "#/team/store", "data", t("team")], [t("Hub settings"), "#/team/settings", "settings", t("name, address, backups")]].map((x) => ({ ...nav(...x), hub: true, admin: true })),
    { group: cmds, label: t("Sync now"), icon: "sync", hint: "", run: syncNow, admin: true },
    { group: cmds, label: t("Analyze the queue now"), icon: "queued", hint: "", run: analyzeQueueNow, admin: true },
    { group: cmds, label: t("Toggle sidebar"), icon: "sidebar", hint: "⌘B", run: toggleSidebar },
    { group: cmds, label: dark ? t("Switch to light theme") : t("Switch to dark theme"), icon: dark ? "sun" : "moon", hint: "", run: flipTheme },
    // named in the language it switches to, like the status bar button
    { group: cmds, label: LANG === "ja" ? "Switch to English" : "日本語に切り替え", icon: "domain", hint: "", run: () => setLang(LANG === "ja" ? "en" : "ja") },
    { group: cmds, label: t("Group glossary themes"), icon: "sparkles", hint: model, admin: true, run: async () => {
      const r = await post("/api/map/themes");
      toast(r.started ? t("{agent} is grouping the glossary into themes…", { agent: analyzerShort() }) : t("Already running"));
      watchJob("themes");
    } },
    { group: cmds, label: t("Rebuild the glossary"), icon: "glossary", hint: model, admin: true, run: async () => {
      const r = await post("/api/glossary/rebuild", { path: "" });
      toast(r.started ? t("{agent} is rebuilding the glossary…", { agent: analyzerShort() }) : t("Already running"));
      watchJob("glossary:all");
    } },
  ].filter((x) => (!x.admin || canAdmin()) && (!x.hub || ME?.hub) && (!x.team || ME?.hub?.team)
    && !(dedicated() && (DEDICATED_HIDDEN_HREFS.has(x.href) || x.run === syncNow))
    && (!limited() || !x.href || ["#/", "#/overview", "#/sessions", "#/knowledge/all", "#/learn", "#/projects"].includes(x.href)));
}
async function paletteSearch(q) {
  const lq = q.toLowerCase();
  const out = [];
  const [sessions, knowledge] = await Promise.all([
    api("/api/sessions", { q, limit: 6 }).catch(() => ({ items: [] })),
    api("/api/knowledge", { q, limit: 6 }).catch(() => ({ items: [] })),
    loadProjects().catch(() => {}),
  ]);
  for (const x of sessions.items) out.push({ group: t("Sessions"), label: x.title || t("(untitled session)"), icon: "sessions",
    hint: [x.project_name, fmtDate(x.started_at)].filter(Boolean).join(" · "), run: () => go(`#/session/${x.id}`) });
  for (const k of knowledge.items) out.push({ group: t("Knowledge"), label: k.title, icon: KIND[k.kind] ? k.kind : "knowledge",
    hint: [kindLabel(k.kind), k.project_name].filter(Boolean).join(" · "), run: () => go(`#/knowledge?q=${encodeURIComponent(k.title)}`) });
  for (const p of (projectsCache || []).filter((p) => p.label.toLowerCase().includes(lq)).slice(0, 5)) out.push({ group: t("Projects"), label: p.label, icon: projIcon(p),
    hint: tn(p.sessions, "{n} session", "{n} sessions", { n: fmtNum(p.sessions) }), run: () => go(`#/project?path=${encodeURIComponent(p.project_path || "")}`) });
  const terms = (glossaryTerms || []).filter((t) => t.term.toLowerCase().includes(lq) || (t.aliases || []).some((a) => a.toLowerCase().includes(lq)))
    .sort((a, b) => (b.term.toLowerCase().startsWith(lq) - a.term.toLowerCase().startsWith(lq)) || a.term.length - b.term.length).slice(0, 5);
  for (const gt of terms) out.push({ group: t("Glossary"), label: gt.term, icon: ICONS[gt.category] ? gt.category : "glossary", hint: gt.category ? catLabel(gt.category) : "",
    run: () => go(`#/glossary?term=${encodeURIComponent(gt.term)}`) });
  return out;
}
function drawPalette() {
  const list = $("#pal-list");
  if (!list) return;
  let group = null;
  list.replaceChildren(...pal.items.flatMap((x, i) => {
    const head = x.group !== group ? h("div", { class: "pal-group" }, (group = x.group)) : null;
    return [head, h("div", { class: `pal-item ${i === pal.sel ? "on" : ""}`, role: "option", "aria-selected": String(i === pal.sel), "data-i": i },
      icon(x.icon), h("span", null, x.label), x.hint ? h("small", null, x.hint) : null)].filter(Boolean);
  }));
  if (!pal.items.length) list.append(h("div", { class: "pal-empty" }, t("Nothing matches")));
  list.querySelector(".pal-item.on")?.scrollIntoView({ block: "nearest" });
}
function searchItem(q) {
  return { group: t("Search"), label: t("Search all sessions for “{q}”", { q }), icon: "search", hint: "↵", run: () => go(`#/search?q=${encodeURIComponent(q)}`) };
}
async function refreshPalette(q) {
  clearTimeout(pal.timer);
  const seq = ++pal.seq, query = q.trim(), lq = query.toLowerCase();
  pal.query = q;
  // a command is found by its label or group, and in Japanese also by the English it was translated from
  const cmds = paletteCommands().filter((c) => !lq || [c.label, c.group, enOf(c.label), enOf(c.group)].some((x) => x.toLowerCase().includes(lq)));
  pal.items = lq ? [...cmds, searchItem(query)] : cmds; pal.sel = 0; drawPalette();
  if (lq.length < 2) return;
  const found = await paletteSearch(query);
  if (seq !== pal.seq) return;
  pal.items = [...cmds, ...found, searchItem(query)]; drawPalette();
}
function openPalette() {
  if (pal.open) return;
  pal.open = true;
  const box = $("#palette");
  const input = h("input", { id: "pal-q", type: "text", placeholder: t("Search sessions, knowledge, terms and commands"), autocomplete: "off", spellcheck: "false",
    "aria-label": t("Search or run a command"), role: "combobox", "aria-controls": "pal-list", "aria-expanded": "true",
    oninput: (e) => { clearTimeout(pal.timer); const v = e.target.value; pal.timer = setTimeout(() => refreshPalette(v), 140); },
    onkeydown: (e) => {
      if (e.key === "ArrowDown") { pal.sel = Math.min(pal.sel + 1, pal.items.length - 1); drawPalette(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { pal.sel = Math.max(pal.sel - 1, 0); drawPalette(); e.preventDefault(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (e.target.value !== pal.query) refreshPalette(e.target.value); // typed faster than the debounce
        runPaletteItem(pal.items[pal.sel]);
      }
    } });
  const list = h("div", { class: "pal-list", id: "pal-list", role: "listbox",
    onclick: (e) => { const it = e.target.closest(".pal-item"); if (it) runPaletteItem(pal.items[+it.dataset.i]); },
    onmousemove: (e) => { const it = e.target.closest(".pal-item"); if (it && +it.dataset.i !== pal.sel) { pal.sel = +it.dataset.i; drawPalette(); } } });
  pal.returnFocus = document.activeElement;
  box.replaceChildren(h("div", { class: "pal", role: "dialog", "aria-modal": "true", "aria-label": t("Search or jump to") },
    h("div", { class: "pal-in" }, icon("search"), input, h("kbd", null, "esc")), list,
    h("div", { class: "pal-foot" }, h("span", null, h("kbd", null, "↑"), " ", h("kbd", null, "↓"), " ", t("move")), h("span", null, h("kbd", null, "↵"), " ", t("open")),
      h("span", null, h("kbd", null, "⌘K"), " ", h("kbd", null, "⌘P"), " ", h("kbd", null, "/"), " ", t("open this")))));
  box.hidden = false;
  box.onmousedown = (e) => { if (e.target === box) closePalette(); };
  refreshPalette("");
  input.focus();
}
function closePalette() {
  if (!pal.open) return;
  pal.open = false; pal.seq++; clearTimeout(pal.timer);
  $("#palette").hidden = true;
  $("#palette").replaceChildren();
  if (pal.returnFocus?.isConnected) pal.returnFocus.focus({ preventScroll: true });
}
function runPaletteItem(x) { if (!x) return; closePalette(); x.run(); }

// ------------------------------------------------------------------ who is viewing (a hub with people), sign-in
// GET /api/me: {viewer, people_mode, can_admin}; null from an older server, which has no people.
let ME = null, signinShown = false;
const ROLE_LABEL = { admin: t("Admin"), member: t("Member"), readonly: t("Read-only") };
// may use the controls that change things: an admin, someone at the hub computer, or anyone on a hub without people
// (the server's POST rule). The hub's own settings (People, team store) follow the stricter can_admin.
function canAdmin() { return !ME || (ME.viewer?.role ?? "admin") === "admin"; }
// someone limited to some projects of this hub: the server answers only Home, Sessions, Knowledge and Projects for
// them, each session as its summary and project lessons (access.py); the rest of the dashboard is hidden
function limited() { return Array.isArray(ME?.viewer?.projects) && ME.viewer.role !== "admin"; }
const LIMITED_SECTIONS = new Set(["home", "teamhome", "sessions", "knowledge", "projects"]);
// A dedicated hub (`[hub] dedicated`, which the Docker image sets): a server for the team with no sessions of its own.
// Team overview is its home, and what only a person's own computer needs (Sync, artifacts, suggestions, sources, MCP,
// devices) is left out.
function dedicated() { return !!ME?.hub?.dedicated && !!ME?.hub?.team; }
const DEDICATED_HIDDEN = new Set(["home", "artifacts", "suggestions"]);
const DEDICATED_HIDDEN_HREFS = new Set(["#/", "#/artifacts", "#/suggestions", "#/friction", "#/sources", "#/mcp", "#/devices"]);
function teamProjectsLabel() { return dedicated() ? t("Projects") : t("Shared projects"); }
async function loadMe() {
  try { ME = await api("/api/me"); } catch (e) { ME = null; return; } // an older server, or the sign-in screen is up
  applyViewer();
}
// Non-admins: the controls that change things are hidden (CSS :root.not-admin); the server refuses them anyway.
// Someone signed in (not at the hub computer): their name, role and Sign out in the toolbar.
function applyViewer() {
  document.documentElement.classList.toggle("not-admin", !canAdmin());
  document.documentElement.classList.toggle("limited", limited());
  document.documentElement.classList.toggle("on-hub", !!ME?.hub);
  document.documentElement.classList.toggle("dedicated-hub", dedicated());
  renderRail();
  renderHubFrame();
  $("#sync-btn").parentElement.hidden = !canAdmin() || dedicated();
  const v = ME?.viewer;
  $("#viewer-pill")?.remove();
  if (!ME?.people_mode || !v || v.here) return;
  const out = h("button", { class: "text-btn", type: "button", onclick: async () => {
    out.disabled = true;
    try { await post("/api/signout"); } catch (e) { /* the page reloads either way */ }
    location.reload();
  } }, t("Sign out"));
  $("#sync-btn").parentElement.before(h("div", { class: "glass viewer-pill", id: "viewer-pill", title: v.email ? `${v.name} · ${v.email}` : v.name },
    icon("preference"), h("span", { class: "vp-name" }, v.name),
    h("span", { class: `vp-role${v.role === "readonly" ? " ro" : ""}` }, ROLE_LABEL[v.role] || v.role), out));
}
// A hub says so on every page: a band across the top (its name, address and what kind of hub it is) and a line in the
// status bar. Phones hide the status bar, so the band stays there.
function renderHubFrame() {
  const hub = ME?.hub, band = $("#hub-band"), foot = $("#status-hub");
  document.documentElement.classList.toggle("hub-framed", !!hub);
  band.hidden = foot.hidden = !hub;
  if (!hub) return;
  const host = hub.address ? hub.address.replace(/^https?:\/\//, "") : null;
  const notes = [hub.dedicated ? t("The team's hub: computers send what they learn here") : t("A hub: other computers send what they learn here"),
    hub.knowledge_only ? t("Knowledge only") : null].filter(Boolean);
  band.replaceChildren(...[icon("team"), h("span", { class: "hb-kind" }, t("Hub")), h("b", { class: "hb-name" }, hub.name),
    host ? h("span", { class: "hb-host" }, host) : null, h("span", { class: "hb-note" }, notes.join(" · "))].filter(Boolean)); // replaceChildren prints a null
  band.setAttribute("aria-label", t("This is the hub {name}", { name: hub.name }));
  foot.replaceChildren(h("i", { class: "dot" }), [t("Hub"), hub.name, host].filter(Boolean).join(" · "));
  foot.title = notes.join(" · ");
}
// A hub with people answered 401: nothing shows until this browser signs in, with a link from the person's own
// Interlatch or an invite code (GET /signin?code=… sets the session cookie and comes back here).
function showSignin(note) {
  if (signinShown) return;
  signinShown = true;
  clearTimeout(pollTimer);
  document.documentElement.classList.add("signed-out");
  const code = h("input", { class: "input", name: "code", placeholder: "XXXX-XXXX-XXXX", autocomplete: "one-time-code", autocapitalize: "characters",
    spellcheck: "false", "aria-label": t("Invite code") });
  const form = h("form", { class: "signin-form", onsubmit: (e) => {
    e.preventDefault();
    const c = code.value.trim();
    if (c) location.href = `/signin?code=${encodeURIComponent(c)}`; else code.focus();
  } }, code, h("button", { class: "btn primary", type: "submit" }, t("Sign in")));
  const theme = h("button", { class: "btn small", type: "button", onclick: () => { flipTheme(); theme.replaceChildren(icon(isDark() ? "sun" : "moon")); },
    "aria-label": t("Toggle theme"), title: t("Toggle theme") }, icon(isDark() ? "sun" : "moon"));
  const other = LANG === "ja" ? "en" : "ja";
  const lang = h("button", { class: "btn small", type: "button", lang: other, onclick: () => setLang(other) }, other === "ja" ? "日本語" : "English");
  document.body.append(h("div", { class: "signin-screen", id: "signin", role: "dialog", "aria-modal": "true", "aria-labelledby": "signin-title" },
    h("div", { class: "card signin-card" },
      h("div", { class: "signin-brand" }, h("img", { src: "icon.png", width: "26", height: "26", alt: "" }), "Interlatch"),
      h("h1", { id: "signin-title" }, t("Sign in to this hub")),
      note ? h("p", { class: "signin-note" }, note) : null,
      h("p", null, t("This hub's dashboard opens only for people an admin has added. There is no password: you sign in from your own Interlatch, or with an invite code.")),
      h("div", { class: "subhead" }, t("From your own computer")),
      h("p", null, tx("If your computer already sends to this hub, open Interlatch there and choose {path}.",
        { path: h("b", null, [t("Settings"), t("Devices"), t("Open the hub's dashboard")].join(" › ")) })),
      h("div", { class: "subhead" }, t("With an invite code")),
      form,
      h("p", { class: "muted" }, t("A code works once. No code, or it has expired? Ask an admin of this hub for a new one.")),
      h("div", { class: "signin-foot" }, lang, theme))));
  code.focus();
}

// ------------------------------------------------------------------ jobs, status bar, theme
let watched = new Set(), pollTimer, uiBuild = null;
const restartAfter = new Set(); // jobs that end by restarting the dashboard: an update, the menu-bar switch
function watchJob(name) { watched.add(name); pollStatus(); }
async function pollStatus() {
  clearTimeout(pollTimer);
  if (signinShown) return; // nothing to show until this browser signs in
  let busy = false;
  try {
    const st = await api("/api/jobs");
    if (uiBuild && st.ui_build && st.ui_build !== uiBuild) { location.reload(); return; } // upgraded under us
    lastStatus = st;
    if (!$("#activity").hidden) drawActivity(st);
    uiBuild ||= st.ui_build;
    const jobs = st.jobs || {};
    const running = Object.entries(jobs).filter(([, j]) => j.state === "running");
    busy = running.length > 0;
    drawSyncBtn(jobs.sync?.state === "running");
    const paused = st.paused_until && st.paused_until > new Date().toISOString();
    const pill = $("#status-pill");
    pill.className = busy ? "busy" : paused ? "warn" : "";
    pill.replaceChildren(h("span", { class: "dot" }), busy ? (running[0][1].message || jobLabel(running[0][0])) : paused ? t("Analysis paused until {time}", { time: fmtTime(st.paused_until) }) : t("Up to date"));
    const waiting = st.pending.queued || 0; // ready is part of queued
    $("#status-queue").textContent = waiting ? tn(waiting, "{n} session queued for analysis", "{n} sessions queued for analysis", { n: fmtNum(waiting) }) : "";
    $("#status-sync").textContent = dedicated() ? "" // nothing of its own to sync: the status bar's hub line says what it is
      : st.hub_url ? t("Sends its sessions to {host}", { host: st.hub_url.replace(/^https?:\/\//, "") })
      : st.last_sync ? t("Synced {ago}", { ago: ago(st.last_sync) }) : t("Not synced yet");
    $("#status-version").textContent = st.version ? `Interlatch ${st.version}` : "";
    showUpdate(st.update, st.version);
    pollUnseen();
    pollNews();
    for (const name of [...watched]) {
      const j = jobs[name];
      if (restartAfter.has(name) && (!j || j.state === "done")) {
        watched.delete(name);
        restartAfter.delete(name);
        if (!j) { location.reload(); return; } // the restart came before this poll: a fresh process knows no such job
        awaitRestart(name);
        continue;
      }
      if (j && j.state !== "running") {
        watched.delete(name);
        toast(j.state === "done" ? t("Done: {result}", { result: String(j.result || jobLabel(name)).slice(0, 160) }) : t("Failed: {result}", { result: String(j.result).slice(0, 200) }), 6000);
        if (name === "sync" || name === "import" || name.startsWith("glossary")) { shellSection = null; projectsCache = null; } // the sidebar's lists and counts may have changed
        render();
      }
    }
  } catch (e) { /* server restarting */ }
  pollTimer = setTimeout(pollStatus, busy || watched.size || !$("#activity").hidden ? 2500 : 20000);
}

// after an update the dashboard restarts itself on the new code: cover the page, and reload it once the new process answers
function awaitRestart(job = "update") {
  const note = h("div", { class: "muted" }, job === "update" ? t("The update is installed. This page reloads by itself when the dashboard is back.")
    : t("This page reloads by itself when the dashboard is back."));
  document.body.append(h("div", { id: "restart-wait", role: "alertdialog", "aria-modal": "true", "aria-label": t("Restarting Interlatch") },
    h("div", { class: "rw-card" }, h("div", { class: "rw-mark", "aria-hidden": "true" }, h("i"), h("i"), icon("sync", "spin")),
      h("b", null, t("Restarting Interlatch…")), note, h("div", { class: "rw-bar", "aria-hidden": "true" }, h("i")))));
  const since = Date.now();
  const tick = async () => {
    try {
      const res = await fetch("/api/jobs", { cache: "no-store", headers: { "X-Interlatch-Lang": LANG } });
      if (res.ok && !(await res.json()).jobs?.[job]) { location.reload(); return; } // a fresh process: it has no record of the job
    } catch (e) { /* down while it restarts */ }
    if (Date.now() - since > 60000 && !note.dataset.late) {
      note.dataset.late = "1";
      note.replaceChildren(t("The dashboard is taking longer than usual to come back."), " ",
        h("button", { class: "btn small", type: "button", onclick: () => location.reload() }, t("Reload")));
    }
    setTimeout(tick, 1000);
  };
  setTimeout(tick, 1000);
}

// ------------------------------------------------------------------ activity panel (click the status bar)
let lastStatus = null;
function jobLabel(name) {
  const [kind, arg] = name.split(/:(.*)/);
  const tail = (p) => (p || "").replace(/\/+$/, "").split("/").pop();
  return {
    sync: t("Sync"), mirror: t("Writing the mirror"), push: t("Sending to the hub"), import: t("Importing chats"), screen: t("Screening imported chats"), update: t("Updating Interlatch"), "menu-bar": t("Setting up the menu-bar icon"), themes: t("Grouping glossary themes"),
    analyze: arg === "selection" ? t("Analyzing selected sessions") : arg === "queue" ? t("Analyzing the queue") : t("Analyzing session {id}", { id: (arg || "").slice(0, 8) }),
    glossary: arg ? t("Glossary: {name}", { name: tail(arg) }) : t("Glossary"), review: t("Weekly review"),
    synthesize: arg === "__global__" ? t("Global playbook") : t("Knowledge base: {name}", { name: tail(arg) }),
  }[kind] || name;
}
function fmtSecs(s) {
  s = Math.max(0, Math.round(s));
  return s < 60 ? t("{n}s", { n: s }) : s < 3600 ? t("{m}m {s}s", { m: Math.floor(s / 60), s: String(s % 60).padStart(2, "0") })
    : t("{h}h {m}m", { h: Math.floor(s / 3600), m: Math.floor((s % 3600) / 60) });
}
function toggleActivity(force) {
  const box = $("#activity");
  const open = force ?? box.hidden;
  box.hidden = !open;
  $("#status-pill").setAttribute("aria-expanded", String(open));
  if (open) { if (lastStatus) drawActivity(lastStatus); pollStatus(); }
}
function drawActivity(st) {
  const box = $("#activity");
  const now = Date.now() / 1000;
  const jobs = Object.entries(st.jobs || {});
  const running = jobs.filter(([, j]) => j.state === "running").sort((a, b) => a[1].started - b[1].started);
  const recent = jobs.filter(([, j]) => j.state !== "running" && now - (j.finished || 0) < 6 * 3600)
    .sort((a, b) => b[1].finished - a[1].finished).slice(0, 6);
  const runRow = ([name, j]) => {
    const elapsed = now - j.started;
    const frac = j.total ? j.done / j.total : null;
    const left = frac && j.done ? (elapsed / j.done) * (j.total - j.done) : null;
    return h("div", { class: "act-job" },
      h("div", { class: "act-top" }, h("b", null, jobLabel(name)),
        h("span", { class: "muted" }, j.total ? t("{a} of {b}", { a: fmtNum(j.done), b: fmtNum(j.total) }) : fmtSecs(elapsed))),
      h("div", { class: `act-bar${frac == null ? " indeterminate" : ""}`, role: "progressbar", "aria-label": jobLabel(name),
        ...(frac == null ? {} : { "aria-valuemin": "0", "aria-valuemax": String(j.total), "aria-valuenow": String(j.done) }) },
        h("i", { style: frac == null ? {} : { width: `${Math.max(2, frac * 100)}%` } })),
      h("div", { class: "act-msg muted" }, [j.message || t("starting…"), j.total ? t("{time} so far", { time: fmtSecs(elapsed) }) : null,
        left && j.done >= 2 ? t("about {time} left", { time: fmtSecs(left) }) : null].filter(Boolean).join(" · ")));
  };
  const doneRow = ([name, j]) => h("div", { class: "act-done" },
    h("span", { class: `act-mark ${j.state === "done" ? "ok" : "no"}` }, j.state === "done" ? "✓" : "✗"),
    h("div", null, h("div", null, h("b", null, jobLabel(name)), h("span", { class: "muted" }, ` · ${ago(new Date(j.finished * 1000).toISOString())}`)),
      h("div", { class: "act-msg muted" }, String(j.result || (j.state === "done" ? t("done") : t("failed"))).slice(0, 220))));
  const waiting = st.pending?.queued || 0; // ready is part of queued
  const paused = st.paused_until && st.paused_until > new Date().toISOString();
  const held = st.pending?.held || 0;
  const queue = (paused ? t("Analysis paused until {time} ({agent} usage limit or sign-in); it resumes by itself.", { time: fmtTime(st.paused_until), agent: analyzer() })
    : !waiting ? t("Nothing waiting for analysis.")
    : tn(waiting, "{n} session waiting for analysis", "{n} sessions waiting for analysis", { n: fmtNum(waiting) })
      + (st.pending?.block ? t(", but {reason}.", { reason: st.pending.block }) : t("; the background agent analyzes up to {n} every 15 minutes.", { n: st.analysis?.max_per_run })))
    + (held ? " " + t("{n} held until a setting changes (see Status).", { n: fmtNum(held) }) : "");
  box.replaceChildren(...[
    h("div", { class: "act-head" }, h("b", null, t("Activity")),
      h("button", { class: "un-close", type: "button", "aria-label": t("Close"), onclick: () => toggleActivity(false) }, "×")),
    h("div", { class: "act-section" }, running.length ? running.map(runRow) : h("div", { class: "muted act-idle" }, t("Nothing running right now."))),
    h("div", { class: `act-queue${paused ? " warn" : ""}` }, icon(paused ? "pause" : "queued"), h("span", null, queue)),
    recent.length ? h("div", { class: "act-section" }, h("div", { class: "subhead" }, t("Recent")), recent.map(doneRow)) : null,
    h("div", { class: "act-foot" },
      h("span", { class: "muted" }, st.last_sync ? t("Synced {ago}", { ago: ago(st.last_sync) }) : t("Not synced yet")),
      h("span", null, h("button", { class: "btn small", type: "button", onclick: () => { toggleActivity(false); go("#/status"); } }, t("Status")), " ",
        h("button", { class: "btn small primary admin-only", type: "button", disabled: running.some(([n]) => n === "sync"), onclick: syncNow }, t("Sync now")))),
  ].filter(Boolean));
}
// an update on offer: a chip in the status bar, a dot on Settings, and once per update a notification card
function showUpdate(u, version) {
  const upd = $("#status-update");
  upd.hidden = !u;
  document.documentElement.classList.toggle("has-update", !!u);
  if (u) { upd.replaceChildren(h("span", { class: "dot" }), u.to === "build" ? t("Update available") : t("Update to {version}", { version: u.to })); upd.title = t("Open Status to update Interlatch"); }
  let from = null;
  try { from = sessionStorage.getItem("interlatch-updating"); } catch (e) { /* private mode */ }
  if (from && !u) { // back after an update ran: say so once
    try { sessionStorage.removeItem("interlatch-updating"); } catch (e) { /* private mode */ }
    toast(version && version !== from ? t("Interlatch updated to {version}", { version }) : t("Interlatch updated"), 6000);
  }
  let seen = null;
  try { seen = localStorage.getItem("interlatch-update-seen"); } catch (e) { /* private mode */ }
  const card = $("#update-note");
  if (!u || seen === u.key || from) { card.hidden = true; return; }
  if (!card.hidden && card.dataset.key === u.key) return;
  const dismiss = () => { try { localStorage.setItem("interlatch-update-seen", u.key); } catch (e) { /* private mode */ } card.hidden = true; };
  card.dataset.key = u.key;
  card.replaceChildren(
    h("div", { class: "un-icon" }, icon("sync")),
    h("div", { class: "un-body" },
      h("b", null, u.to === "build" ? t("Your checkout has new changes") : t("Interlatch {version} is available", { version: u.to })),
      h("div", null, u.to === "build" ? t("Reinstall to run them in the dashboard.") : t("You're on {version}. Update from Settings › Status.", { version })),
      h("div", { class: "un-actions" },
        h("button", { class: "btn primary small", type: "button", onclick: () => { dismiss(); go("#/status?focus=updates"); } }, t("View update")),
        h("button", { class: "btn small", type: "button", onclick: dismiss }, t("Later")))),
    h("button", { class: "un-close", type: "button", "aria-label": tc("notification", "Dismiss"), onclick: dismiss }, "×"));
  card.hidden = false;
}
async function analyzeQueueNow() { // the palette's way to the Queue's Analyze now: the sessions ready now
  const n = lastStatus?.pending?.ready || 0;
  if (n > 25 && !confirm(lastStatus?.analysis?.billed
    ? t("Analyze {n} sessions now? Each one is a call to {agent}, billed to your API key.", { n, agent: analyzer() })
    : t("Analyze {n} sessions now? Each one is a call on your {agent} login.", { n, agent: analyzer() }))) return;
  const r = await post("/api/analysis/run", { which: "ready" }).catch((e) => ({ error: e.message }));
  if (!r.started) { toast(r.error || t("Could not start")); return; }
  toast(tn(r.count, "Analyzing {n} session; the status bar shows progress", "Analyzing {n} sessions; the status bar shows progress", { n: fmtNum(r.count) }), 7000);
  watchJob("analyze:queue");
}
async function syncNow() {
  drawSyncBtn(true); // at once: the next status poll puts the sync icon back if it didn't start
  const r = await post("/api/sync").catch((e) => { drawSyncBtn(false); throw e; });
  toast(r.started ? t("Syncing transcripts and processing the queue…") : t("A sync is already running"));
  watchJob("sync");
}
function drawSyncBtn(syncing) { // a spinner in place of the sync icon while a sync runs
  const btn = $("#sync-btn");
  if ((btn.getAttribute("aria-busy") === "true") === syncing) return;
  btn.setAttribute("aria-busy", String(syncing));
  btn.querySelector(".icon").replaceWith(icon(syncing ? "running" : "sync", syncing ? "spin" : ""));
}
function isDark() {
  const root = document.documentElement;
  return root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}
function themeChanged() {
  const dark = isDark();
  $("#theme-btn").replaceChildren(icon(dark ? "sun" : "moon"));
  $("#theme-btn").title = dark ? t("Switch to light theme") : t("Switch to dark theme");
  // the app window's native glass follows the page's theme, not only the system's
  try { window.pywebview?.api?.set_appearance?.(document.documentElement.dataset.theme || "system"); } catch (e) { /* not in the app */ }
}
function flipTheme() {
  const root = document.documentElement;
  root.dataset.theme = isDark() ? "light" : "dark";
  try { localStorage.setItem("interlatch-theme", root.dataset.theme); } catch (e) { /* private mode */ }
  themeChanged();
  if (parseHash().path === "/appearance") render();
}

// index.html's own text, in the page's language (before the icons are added to those elements)
function translateStatic() {
  const set = (sel, attrs) => { const el = $(sel); if (el) for (const [k, v] of Object.entries(attrs)) k === "text" ? (el.textContent = v) : el.setAttribute(k, v); };
  set(".brand", { "aria-label": t("Interlatch home") });
  set("#rail", { "aria-label": t("Sections") });
  set("#sidebar-btn", { title: t("Toggle sidebar (⌘B)"), "aria-label": t("Toggle sidebar") });
  set("#back-btn", { title: t("Back"), "aria-label": t("Back") });
  set("#fwd-btn", { title: t("Forward"), "aria-label": t("Forward") });
  set("#search-pill", { "aria-label": t("Search or jump to (⌘K)") });
  set("#search-pill .sp-label", { text: t("Search or jump to…") });
  set("#sync-btn", { title: t("Sync transcripts and process the analysis queue"), text: t("Sync") });
  set("#theme-btn", { "aria-label": t("Toggle theme") });
  set("#activity", { "aria-label": t("Activity") });
  // the language switch names the other language in that language, so it is found whichever one is showing
  const other = LANG === "ja" ? "en" : "ja", label = other === "ja" ? "日本語に切り替え" : "Switch to English";
  set("#lang-btn", { text: other === "ja" ? "日本語" : "EN", title: label, "aria-label": label, lang: other });
}
translateStatic();
renderRail();
$("#sidebar-btn").append(icon("sidebar"));
$("#back-btn").append(icon("left"));
$("#fwd-btn").append(icon("right"));
$("#search-pill").prepend(icon("search"));
$("#sync-btn").prepend(icon("sync"));
$("#sidebar-btn").addEventListener("click", toggleSidebar);
$("#sidebar-btn").setAttribute("aria-controls", "sidebar");
sidebarExpanded();
wirePeek();
matchMedia("(max-width: 860px)").addEventListener?.("change", sidebarExpanded);
$("#back-btn").addEventListener("click", () => history.back());
$("#fwd-btn").addEventListener("click", () => history.forward());
$("#search-pill").addEventListener("click", openPalette);
$("#sync-btn").addEventListener("click", syncNow);
$("#status-pill").addEventListener("click", () => toggleActivity());
$("#status-pill").setAttribute("aria-haspopup", "dialog");
$("#status-pill").setAttribute("aria-expanded", "false");
$("#status-pill").title = t("Background work: click for progress");
document.addEventListener("mousedown", (e) => { if (!$("#activity").hidden && !e.target.closest("#activity, #status-pill")) toggleActivity(false); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#activity").hidden) toggleActivity(false); });
$("#status-update").addEventListener("click", () => go("#/status?focus=updates"));
$("#theme-btn").addEventListener("click", flipTheme);
$("#lang-btn")?.addEventListener("click", () => setLang(LANG === "ja" ? "en" : "ja"));
themeChanged();
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", themeChanged);
window.addEventListener("pywebviewready", themeChanged);
document.addEventListener("mousedown", (e) => { // the narrow-window sidebar floats over the page; a click elsewhere closes it
  if (document.documentElement.classList.contains("show-sidebar") && !e.target.closest("#sidebar, #rail, #sidebar-btn")) document.documentElement.classList.remove("show-sidebar");
});
// the app window has no title bar: its toolbar and title strip move the window (double-click zooms it)
document.addEventListener("mousedown", (e) => {
  const api = window.pywebview?.api;
  if (!api?.start_drag || e.button !== 0 || !e.target.closest(".drag-region")) return;
  if (e.target.closest("a, button, input, select, textarea, label, kbd")) return;
  e.preventDefault();
  if (e.detail === 2) api.title_double_click(); else api.start_drag();
});
const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
document.addEventListener("keydown", (e) => {
  const mod = IS_MAC ? e.metaKey : e.ctrlKey, key = e.key.toLowerCase();
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if (mod && !e.altKey && (key === "k" || key === "p")) { e.preventDefault(); pal.open ? closePalette() : openPalette(); }
  else if (mod && !e.shiftKey && !e.altKey && key === "b") { e.preventDefault(); toggleSidebar(); }
  else if (e.key === "Escape" && pal.open) { e.preventDefault(); closePalette(); }
  else if (e.key === "Escape" && document.documentElement.classList.contains("show-sidebar")) document.documentElement.classList.remove("show-sidebar");
  else if (e.key === "Escape" && document.documentElement.classList.contains("peek-sidebar")) peekSidebar(false);
  else if (e.key === "/" && !mod && !typing && !pal.open) { e.preventDefault(); openPalette(); }
});
window.addEventListener("hashchange", render);
loadMe().finally(() => { render(); pollStatus(); }); // who is viewing decides which controls show
