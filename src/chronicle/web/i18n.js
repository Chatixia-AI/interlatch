/* Interlatch dashboard: the UI language (English or Japanese). Loaded after ja.js, before app.js. */
"use strict";

// Resolved once: the stored choice (Settings › Appearance, the statusbar button), else the browser's first language.
// Changing it reloads the page, since many labels are built at load time.
const LANG_KEY = "interlatch-lang";
function langPref() { // "en" | "ja" | "system"
  try { const v = localStorage.getItem(LANG_KEY); if (v === "en" || v === "ja") return v; } catch (e) { /* storage blocked */ }
  return "system";
}
const LANG = (() => {
  const p = langPref();
  if (p !== "system") return p;
  const first = (navigator.languages && navigator.languages[0]) || navigator.language || "";
  return /^ja\b/i.test(first) ? "ja" : "en";
})();
const LOCALE = LANG === "ja" ? "ja-JP" : undefined; // every Intl formatter takes it
document.documentElement.lang = LANG;
function setLang(lang) { // "en" | "ja" | "system"
  try {
    if (lang === "en" || lang === "ja") localStorage.setItem(LANG_KEY, lang);
    else localStorage.removeItem(LANG_KEY);
  } catch (e) { /* storage blocked: the choice lasts until reload */ }
  location.reload();
}

// Keys are the exact English strings with {name} placeholders; a missing translation falls back to English.
const JA = LANG === "ja" ? window.INTERLATCH_JA || {} : null;
function fillVars(str, vars) {
  return vars ? str.replace(/\{(\w+)\}/g, (m, k) => (k in vars && vars[k] != null ? String(vars[k]) : m)) : str;
}
function t(en, vars) { return fillVars((JA && JA[en]) || en, vars); }
// the same English in two places that Japanese words differently ("Added" a date column, "Added" a state):
// ja.js keys the variant "Added [date]"
function tc(context, en, vars) { return fillVars((JA && (JA[`${en} [${context}]`] || JA[en])) || en, vars); }
// plural: English picks by n, Japanese has one form (the translation of `other`); {n} is n unless vars.n is given
function tn(n, one, other, vars) { return t(JA ? other : n === 1 ? one : other, { n, ...vars }); }
// the English a Japanese label was translated from, so the ⌘K palette finds commands by either name
let EN_OF = null;
function enOf(text) {
  if (!JA) return text;
  EN_OF ||= Object.fromEntries(Object.entries(JA).map(([en, ja]) => [ja, en.replace(/ \[[^\]]*\]$/, "")]));
  return EN_OF[text] || text;
}
// a sentence with inline elements: tx("Open {link} to see it.", { link: h("a", …) }) -> ["Open ", <a>, " to see it."]
// the translation may put the slots in any order; strings in `nodes` stay text (h() appends them as text nodes)
function tx(en, nodes) {
  return t(en).split(/\{(\w+)\}/).map((part, i) => (i % 2 ? (nodes && part in nodes ? nodes[part] : `{${part}}`) : part))
    .filter((x) => x !== "" && x != null);
}
