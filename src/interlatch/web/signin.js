// The /signin page's one script (handler.py, _signin_page): the theme this browser chose, before the first paint, and
// the page's theme and language buttons. Its own file, not inline, so the page's policy can allow scripts from 'self' only.
(function () {
  var root = document.documentElement;
  try { var t = localStorage.getItem("interlatch-theme"); if (t) root.dataset.theme = t; } catch (e) {}
  var dark = function () {
    return root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  };
  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("signin-theme");
    if (btn) {
      var show = function () { // the sun in the dark, the moon in the light: what a press switches to
        btn.querySelector(".ic-sun").toggleAttribute("hidden", !dark());
        btn.querySelector(".ic-moon").toggleAttribute("hidden", dark());
      };
      show();
      btn.addEventListener("click", function () {
        root.dataset.theme = dark() ? "light" : "dark";
        try { localStorage.setItem("interlatch-theme", root.dataset.theme); } catch (e) {}
        show();
      });
    }
    var lang = document.getElementById("signin-lang");
    if (lang) lang.addEventListener("click", function () { // the dashboard opens in it too, once signed in
      try { localStorage.setItem("interlatch-lang", lang.getAttribute("lang")); } catch (e) {}
    });
  });
})();
