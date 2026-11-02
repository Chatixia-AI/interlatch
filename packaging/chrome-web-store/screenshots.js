/* The store's screenshots (1280x800), from the extension itself. The site is a plain stand-in page, not claude.ai or
ChatGPT, and the dashboard a stub, so no real chats or other companies' pages appear.
  npm i playwright-core  (anywhere on NODE_PATH), then: node packaging/chrome-web-store/screenshots.js */
const { chromium } = require("playwright-core"), http = require("http"), fs = require("fs"), os = require("os"), path = require("path");

const OUT = path.join(__dirname, "screenshots");
const EXT = path.join(__dirname, "..", "..", "src", "interlatch", "browser_extension");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const SITE = `<!doctype html><title>Chat</title><body style="margin:0;background:#f7f7f5;font:15px system-ui;color:#555">
  <div style="max-width:720px;margin:0 auto;padding:64px 24px;display:grid;gap:18px">${[80, 95, 60, 88, 72, 91, 45].map((w, i) =>
    `<div style="height:14px;width:${w}%;background:#e2e2de;border-radius:7px;${i % 3 === 2 ? "margin-bottom:22px" : ""}"></div>`).join("")}</div></body>`;

(async () => {
  const dash = http.createServer((req, res) => {
    req.resume();
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(req.url === "/api/capture" ? { id: "x", result: "new" } : { viewer: { here: true } }));
  });
  await new Promise((r) => dash.listen(0, "127.0.0.1", r));
  const ctx = await chromium.launchPersistentContext(fs.mkdtempSync(path.join(os.tmpdir(), "shots-")), {
    channel: "chromium", headless: true, viewport: { width: 1280, height: 800 }, colorScheme: "dark", deviceScaleFactor: 2,
    args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
  });
  const sw = ctx.serviceWorkers()[0] || await ctx.waitForEvent("serviceworker");
  const id = new URL(sw.url()).host;
  await sw.evaluate((port) => chrome.storage.local.set({ port }), dash.address().port);
  await ctx.route(/https:\/\/claude\.ai\/.*/, (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/api/organizations") return route.fulfill({ json: [{ uuid: "o" }] });
    if (p.startsWith("/api/")) return route.fulfill({ json: [] });
    return route.fulfill({ contentType: "text/html", body: SITE });
  });

  await sleep(800);
  const welcome = ctx.pages().find((p) => p.url().endsWith("/welcome.html"));
  await welcome.reload();
  await sleep(600);
  await welcome.screenshot({ path: path.join(OUT, "1-asks-first.png"), scale: "css" });

  await welcome.getByRole("button", { name: "Start saving", exact: true }).click();
  const site = await ctx.newPage();
  await site.goto("https://claude.ai/");
  await sleep(2500);
  await site.screenshot({ path: path.join(OUT, "2-says-where-chats-go.png"), scale: "css" });

  await sw.evaluate(() => chrome.storage.local.set({
    "sync:claude-ai": { lastRun: Date.now() - 120e3, captured: 42, lastSent: Date.now() - 120e3 },
    "sync:chatgpt": { lastRun: Date.now() - 300e3, captured: 17, lastSent: Date.now() - 600e3 },
  }));
  const popup = await ctx.newPage();
  await popup.setViewportSize({ width: 340, height: 500 });
  await popup.addInitScript(() => {
    const send = chrome.runtime.sendMessage.bind(chrome.runtime);
    chrome.runtime.sendMessage = async (m) => (m.type === "status" ? { base: "http://127.0.0.1:11524", ok: true } : send(m));
  });
  await popup.goto(`chrome-extension://${id}/popup.html`);
  await sleep(600);
  const shot = (await popup.locator("body").screenshot({ scale: "device" })).toString("base64");
  const frame = await ctx.newPage();
  await frame.setContent(`<!doctype html><body style="margin:0;width:1280px;height:800px;display:flex;align-items:center;justify-content:center;
    background:#081a31;background-image:linear-gradient(rgba(93,134,182,.13) 1px,transparent 1px),linear-gradient(90deg,rgba(93,134,182,.13) 1px,transparent 1px);background-size:32px 32px">
    <img src="data:image/png;base64,${shot}" style="width:510px;border:1px solid #5d86b6;border-radius:10px"></body>`);
  await frame.screenshot({ path: path.join(OUT, "3-toolbar-button.png"), scale: "css" });

  await ctx.close();
  dash.close();
})().catch((e) => { console.error(e); process.exit(1); });
