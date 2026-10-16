"""Screenshots of the dashboard in Microsoft Edge on Windows, in Japanese (ja-JP), with what went wrong on each page.

    python shots.py <base url> <out dir> <tag>

Each page at 100% and 150% display scaling (a common Windows laptop setting). Console errors, failed requests and the
Content-Type of every script and stylesheet go to <out dir>/<tag>-report.txt.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE, OUT, TAG = sys.argv[1].rstrip("/"), Path(sys.argv[2]), sys.argv[3]
PAGES = ["#/", "#/sessions", "#/knowledge/all", "#/projects", "#/learn", "#/glossary", "#/map", "#/status",
         "#/sources", "#/reviews", "#/appearance", "#/storage"]
REALM = ["#/innerlatch", "#/innerlatch/quests", "#/innerlatch/journey", "#/innerlatch/agent/claude",
         "#/innerlatch/adventure", "#/innerlatch/adventure?view=list", "#/innerlatch/adventure/bag",
         "#/innerlatch/leaderboard", "#/innerlatch/arena"]
OUT.mkdir(parents=True, exist_ok=True)
lines: list[str] = []


def settle(page) -> None:
    try:
        page.wait_for_function("document.querySelector('#app')?.getAttribute('aria-busy') !== 'true'", timeout=20000)
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  still busy: {exc}")
    page.wait_for_timeout(1500)


def slug(route: str) -> str:
    return route.strip("#/").replace("/", "_").replace("?", "_").replace("=", "-") or "home"


sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge")
    for scale in (1, 1.5):
        ctx = browser.new_context(locale="ja-JP", timezone_id="Asia/Tokyo", viewport={"width": 1366, "height": 860},
                                  device_scale_factor=scale)
        page = ctx.new_page()
        page.on("console", lambda m: m.type in ("error", "warning") and lines.append(f"  console {m.type}: {m.text}"))
        page.on("pageerror", lambda e: lines.append(f"  page error: {e}"))
        page.on("requestfailed", lambda r: lines.append(f"  request failed: {r.url} {r.failure}"))

        def response(r):
            url = r.url.split("?")[0]
            if url.endswith((".js", ".css", ".woff2", ".svg", ".webp", ".png", ".html")) or url.rstrip("/") == BASE:
                lines.append(f"  {r.status} {r.headers.get('content-type')} {url}")
            elif r.status >= 400:
                lines.append(f"  HTTP {r.status} {url}")
        page.on("response", response)
        for i, route in enumerate(PAGES + REALM):
            lines.append(f"[{scale}x] {route}")
            if i == 0:
                page.goto(f"{BASE}/{route}")
            elif route in REALM and route == REALM[0]:
                door = page.locator("a.rail-door")
                if door.count():
                    door.first.click()
                    page.wait_for_timeout(4000)
                else:
                    lines.append("  no Innerlatch door on the rail")
                    page.evaluate("r => { location.hash = r; }", route)
            else:
                page.evaluate("r => { location.hash = r; }", route)
            settle(page)
            page.screenshot(path=str(OUT / f"{TAG}-{scale}x-{i:02d}-{slug(route)}.png"), full_page=False)
        ctx.close()
    browser.close()
(OUT / f"{TAG}-report.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(line for line in lines if not line.startswith("  200 ")))
