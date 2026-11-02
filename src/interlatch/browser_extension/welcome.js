/* Interlatch: save web chats. The page that opens when the extension is installed: what it reads and where that goes,
and nothing is read until you choose Start saving (capture.js checks `consented`). */
"use strict";

const $ = (id) => document.getElementById(id);
const t = (key, ...subs) => chrome.i18n.getMessage(key, subs.map(String));

function done(text) {
  $("choice").hidden = true;
  $("done").textContent = text;
  $("done").hidden = false;
}

document.documentElement.lang = chrome.i18n.getUILanguage();
document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
document.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
document.querySelectorAll("[data-i18n-label]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.i18nLabel)));
$("privacy").href = t("privacyUrl");
$("install").href = t("installUrl");

chrome.storage.local.get(["consented", "theme"]).then(({ consented, theme }) => {
  if (theme) document.documentElement.dataset.theme = theme;
  if (consented) done(t("wlStarted"));
});
chrome.runtime.sendMessage({ type: "reachable" }).then((ok) => { $("needs").hidden = !!ok; }).catch(() => {});

$("start").addEventListener("click", async () => {
  await chrome.storage.local.set({ consented: true });
  done(t("wlStarted"));
});
$("later").addEventListener("click", () => done(t("wlLaterDone")));
$("theme").addEventListener("click", async () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : !matchMedia("(prefers-color-scheme: light)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  await chrome.storage.local.set({ theme: root.dataset.theme });
});
