/* Interlatch: save web chats. The toolbar popup: is the dashboard there, what each site's last sync did, settings. */
"use strict";

const $ = (id) => document.getElementById(id);
const SITES = ["claude-ai", "chatgpt"];
const HOSTS = { "claude-ai": "claude.ai", chatgpt: "chatgpt.com" };
const STALE = 15 * 60e3; // a sync "running" longer than this ended with its tab

function ago(ms) {
  const s = Math.round((Date.now() - ms) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

async function render() {
  const store = await chrome.storage.local.get([...SITES.map((s) => `sync:${s}`), "port", "days", "paused", "theme"]);
  for (const key of SITES) {
    const st = store[`sync:${key}`];
    const line = document.querySelector(`.site[data-site="${key}"] .line`);
    line.classList.toggle("bad", !!st?.error);
    if (st?.running && Date.now() - st.running < STALE) line.textContent = `Syncing… ${st.progress || 0} saved so far`;
    else if (!st?.lastRun) line.textContent = `Not synced yet: open or reload a ${HOSTS[key]} tab.`;
    else if (st.error) line.textContent = `Last try ${ago(st.lastRun)} stopped: ${st.error}`;
    else if (st.more) line.textContent = `Catching up: ${st.captured || 0} saved so far${
      st.slowed ? "; the site asked to slow down, so it goes on in a few minutes" : ""}`;
    else line.textContent = `Synced ${ago(st.lastRun)} · ${st.captured || 0} chat${st.captured === 1 ? "" : "s"} saved${
      st.lastSent ? `, last ${ago(st.lastSent)}` : ""}`;
  }
  $("port").value = store.port || "";
  $("days").value = store.days || "";
  $("paused").checked = !!store.paused;
  if (store.theme) document.documentElement.dataset.theme = store.theme;
}

async function checkDashboard() {
  const pill = $("dash");
  const r = await chrome.runtime.sendMessage({ type: "status" }).catch(() => null);
  pill.className = `pill ${r?.ok ? "good" : "bad"}`;
  const port = r?.base ? new URL(r.base).port : "";
  pill.textContent = `${r?.ok ? "dashboard" : "no dashboard"} on :${port || "?"}`;
  pill.title = r ? `${r.base}${r.error ? `: ${r.error}` : ""}` : "";
}

$("sync").addEventListener("click", async () => {
  const asked = Date.now();
  $("sync").disabled = true;
  $("note").hidden = true;
  await chrome.storage.local.set({ syncNow: asked });
  setTimeout(async () => {
    const alive = await chrome.storage.local.get(SITES.map((s) => `alive:${s}`));
    $("note").hidden = Object.values(alive).some((t) => t >= asked); // no tab of either site answered
    $("sync").disabled = false;
    render();
  }, 2500);
});
$("paused").addEventListener("change", (e) => chrome.storage.local.set({ paused: e.target.checked }));
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
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  await chrome.storage.local.set({ theme: root.dataset.theme });
});
chrome.storage.onChanged.addListener(render);

render();
checkDashboard();
