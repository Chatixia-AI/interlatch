/* Interlatch: save web chats. The toolbar popup: is it saving your chats and if not what to do, what each site's last
sync did, settings. Its words are in _locales/. */
"use strict";

const $ = (id) => document.getElementById(id);
const t = (key, ...subs) => chrome.i18n.getMessage(key, subs.map(String));
const SITES = ["claude-ai", "chatgpt"];
const STALE = 15 * 60e3; // a sync "running" longer than this ended with its tab

let dash = null; // what the service worker last said about the dashboard; null until it has
let asked = 0; // when Sync now was clicked, until its result is shown

function ago(ms) {
  const s = Math.round((Date.now() - ms) / 1000);
  if (s < 60) return t("agoNow");
  if (s < 3600) return t("agoMin", Math.round(s / 60));
  if (s < 86400) return t("agoHour", Math.round(s / 3600));
  return t("agoDay", Math.round(s / 86400));
}

function siteLine(st) {
  if (st?.running && Date.now() - st.running < STALE) {
    return st.error ? t("siteRetrying", st.error) : t("siteSyncing", st.progress || 0); // the failure stays in view until it's fixed
  }
  if (!st?.lastRun) return t(dash?.ok === false ? "siteNotYetOff" : "siteNotYet"); // opening a tab won't help then
  if (st.error) return t("siteStopped", ago(st.lastRun), st.error);
  if (st.more) return t(st.slowed ? "siteSlowed" : "siteCatching", st.captured || 0);
  const n = st.captured || 0;
  return t("siteSynced", ago(st.lastRun), n === 1 ? t("chatsOne") : t("chatsMany", n));
}

/* The one line at the top. A dashboard that doesn't answer, on a browser that has never reached one, most likely isn't
installed; one that has answered before has stopped. */
function overall(store) {
  const states = SITES.map((s) => store[`sync:${s}`]);
  if (!dash) return "checking";
  if (!store.consented) return "consent";
  if (!dash.ok) return !dash.down ? "problem" : store.seenDashboard || states.some((st) => st?.captured) ? "stopped" : "install";
  if (store.paused) return "paused";
  if (states.some((st) => st?.error)) return "trouble";
  return states.some((st) => st?.lastRun || st?.running) ? "saving" : "waiting";
}

const TITLES = { checking: "checking", consent: "stConsent", saving: "stSaving", waiting: "stWaiting", paused: "stPaused", trouble: "stTrouble",
  stopped: "stStopped", install: "stInstall", problem: "stProblem" };
const DETAILS = { consent: "stConsentDetail", saving: "stSavingDetail", waiting: "stWaitingDetail", paused: "stPausedDetail", trouble: "stTroubleDetail",
  stopped: "stStoppedDetail", install: "stInstallDetail" };

function renderStatus(state) {
  $("status").dataset.state = state;
  $("st-title").textContent = t(TITLES[state]);
  const detail = state === "problem" ? dash.error : DETAILS[state] && t(DETAILS[state]);
  $("st-detail").textContent = detail || "";
  $("st-detail").hidden = !detail;
  const off = state === "install" || state === "stopped";
  $("st-cta").hidden = $("st-installed").hidden = state !== "install";
  $("st-cmd").hidden = $("st-port").hidden = !off;
  $("st-start").hidden = state !== "consent";
  $("sites").hidden = $("actions").hidden = state === "install" || state === "consent";
  $("sync").disabled = !dash?.ok || state === "paused" || !!$("sync").dataset.asked;
  $("dashboard").hidden = !dash?.ok;
  if (dash?.base) $("dashboard").href = dash.base;
}

/* What Sync now did, once every site that answered has finished. */
function renderResult(store) {
  if (!asked) return;
  const states = SITES.filter((s) => store[`alive:${s}`] >= asked).map((s) => store[`sync:${s}`]);
  if (!states.length || !states.every((st) => st?.lastRun >= asked && !st.running)) return;
  asked = 0;
  const n = states.reduce((sum, st) => sum + (st.lastSaved || 0), 0);
  const chats = n === 1 ? t("chatsOne") : t("chatsMany", n);
  $("result").textContent = states.some((st) => st.error) ? t("resError") : states.some((st) => st.more) ? t("resMore", chats)
    : n ? t("resSaved", chats) : t("resNothing");
  $("result").hidden = false;
}

async function render() {
  const store = await chrome.storage.local.get([...SITES.flatMap((s) => [`sync:${s}`, `alive:${s}`]), "port", "days", "paused",
    "theme", "seenDashboard", "noOverlay", "consented"]);
  for (const key of SITES) {
    const st = store[`sync:${key}`];
    const line = document.querySelector(`.site[data-site="${key}"] .line`);
    line.classList.toggle("bad", !!st?.error);
    line.textContent = siteLine(st);
    line.title = st?.lastSent ? t("lastSaved", ago(st.lastSent)) : "";
  }
  renderStatus(overall(store));
  renderResult(store);
  // not while it's being typed in: a sync writing to storage renders again
  if (document.activeElement !== $("port")) $("port").value = store.port || "";
  if (document.activeElement !== $("days")) $("days").value = store.days || "";
  $("paused").checked = !!store.paused;
  $("overlay").checked = !store.noOverlay;
  if (store.theme) document.documentElement.dataset.theme = store.theme;
}

async function checkDashboard() {
  dash = await chrome.runtime.sendMessage({ type: "status" }).catch(() => null) || { ok: false, down: true };
  render();
}

function translate() {
  document.documentElement.lang = chrome.i18n.getUILanguage();
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
  document.querySelectorAll("[data-i18n-label]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.i18nLabel)));
  $("st-cta").href = t("installUrl");
  $("how").href = t("helpUrl");
}

$("sync").addEventListener("click", async () => {
  asked = Date.now();
  const clicked = asked;
  $("sync").dataset.asked = "1";
  $("result").hidden = true;
  $("sync").disabled = true;
  $("note").hidden = true;
  await chrome.storage.local.set({ syncNow: asked });
  setTimeout(async () => {
    const alive = await chrome.storage.local.get(SITES.map((s) => `alive:${s}`));
    $("note").hidden = Object.values(alive).some((v) => v >= clicked); // no tab of either site answered
    if ($("note").hidden === false) asked = 0;
    delete $("sync").dataset.asked;
    render();
  }, 2500);
});
$("copy").addEventListener("click", async (e) => {
  await navigator.clipboard.writeText(document.querySelector("#st-cmd code").textContent).catch(() => {});
  e.target.textContent = t("copied");
  setTimeout(() => { e.target.textContent = t("copy"); }, 1500);
});
$("paused").addEventListener("change", (e) => chrome.storage.local.set({ paused: e.target.checked }));
$("overlay").addEventListener("change", (e) => chrome.storage.local.set({ noOverlay: !e.target.checked }));
$("port").addEventListener("change", async (e) => {
  const port = Number(e.target.value);
  await (port > 0 && port < 65536 ? chrome.storage.local.set({ port }) : chrome.storage.local.remove("port"));
  checkDashboard();
});
$("days").addEventListener("change", (e) => {
  const days = Number(e.target.value);
  return days > 0 ? chrome.storage.local.set({ days }) : chrome.storage.local.remove("days");
});
$("reset").addEventListener("click", async () => {
  await chrome.storage.local.remove(SITES.flatMap((s) => [`sync:${s}`, `lock:${s}`]));
  render();
});
$("theme").addEventListener("click", async () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : !matchMedia("(prefers-color-scheme: light)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  await chrome.storage.local.set({ theme: root.dataset.theme });
});
chrome.storage.onChanged.addListener(render);

translate();
render();
checkDashboard();
