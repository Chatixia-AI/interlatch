// Runs in <head>, before the first paint: the app window's look, the UI language, the theme, animations and the sidebar as
// they were. Its own file, not inline, so the page's Content-Security-Policy can allow scripts from 'self' only.
(function () {
  var root = document.documentElement, q = new URLSearchParams(location.search);
  // the macOS app loads ?app=mac: the page goes transparent over the window's native vibrancy
  if (q.get("app") === "mac") root.classList.add("in-app");
  if (q.get("reduce") === "1") root.classList.add("os-solid");
  // Interlatch was called Chronicle: what this browser remembered under the old names (chronicle-theme, chronicle.view.…)
  // carries over to the new ones once, before anything reads them
  ["localStorage", "sessionStorage"].forEach(function (name) {
    try {
      var store = window[name], old = [];
      for (var i = 0; i < store.length; i++) if (/^chronicle[.-]/.test(store.key(i))) old.push(store.key(i));
      old.forEach(function (k) {
        var to = "interlatch" + k.slice("chronicle".length);
        if (store.getItem(to) === null) store.setItem(to, store.getItem(k));
        store.removeItem(k);
      });
    } catch (e) {}
  });
  // the UI language, resolved as i18n.js does: set before the first paint
  var lang = /^ja\b/i.test((navigator.languages && navigator.languages[0]) || navigator.language || "") ? "ja" : "en";
  try { var l = localStorage.getItem("interlatch-lang"); if (l === "en" || l === "ja") lang = l; } catch (e) {}
  root.lang = lang;
  try {
    var t = localStorage.getItem("interlatch-theme"); if (t) root.dataset.theme = t;
    if (localStorage.getItem("interlatch-sidebar") === "0") root.classList.add("no-sidebar");
  } catch (e) {}
  // animations: "on" or "off" as chosen in Appearance, else the system's Reduce motion setting; app.css keys on data-motion
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)");
  var motion = function () {
    var m = null;
    try { m = localStorage.getItem("interlatch-motion"); } catch (e) {}
    root.dataset.motion = m === "on" || m === "off" ? m : reduce && reduce.matches ? "off" : "on";
  };
  motion();
  if (reduce && reduce.addEventListener) reduce.addEventListener("change", motion);
})();
