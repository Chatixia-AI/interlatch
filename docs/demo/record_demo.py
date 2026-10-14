"""Record the README demo video: a scripted walk through the dashboard on the demo data.

    uv run python docs/demo/make_demo.py /tmp/interlatch-demo
    INTERLATCH_HOME=/tmp/interlatch-demo/home uv run python -m interlatch ui --port 8898
    uv run --with playwright python docs/demo/record_demo.py --port 8898

It drives headless Chromium through Home, a session (a glossary hover, an expanded tool call), the ⌘K palette, the
Map and a weekly review, and writes `docs/images/demo.mp4` (the whole tour) and `docs/images/demo.gif` (the session
part, a few seconds, under the 600 KB the large-file hook allows). Playwright's own recorder is blurry, so frames
come from Chrome's screencast at 2x and ffmpeg (on PATH) encodes them. Headless pages draw no pointer, so the page
gets a drawn cursor and a ripple on each click.

The video is too big for the repository (git ignores it): the README links it as an asset of the latest release.

    gh release upload v<latest> docs/images/demo.mp4 --clobber
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

W, H = 1440, 900
IMAGES = Path(__file__).resolve().parent.parent / "images"

CURSOR_JS = """
(() => {
  const add = () => {
    if (document.getElementById('__cur')) return;
    const c = document.createElement('div');
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2 L4 19 L8.5 14.8 L11.6 21.6 L14.4 20.4 L11.3 13.7 L17.5 13.7 Z" fill="#111" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    c.id = '__cur';
    Object.assign(c.style, {position: 'fixed', left: '-40px', top: '-40px', zIndex: 2147483647, pointerEvents: 'none',
      transform: 'translate(-4px,-2px)', filter: 'drop-shadow(0 1px 2px rgba(0,0,0,.35))'});
    const r = document.createElement('div');
    Object.assign(r.style, {position: 'fixed', width: '34px', height: '34px', borderRadius: '50%', zIndex: 2147483646,
      pointerEvents: 'none', background: 'rgba(37,99,235,.28)', transform: 'translate(-50%,-50%) scale(0)', opacity: '0'});
    document.documentElement.append(c, r);
    addEventListener('mousemove', (e) => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', (e) => {
      r.style.transition = 'none'; r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
      r.style.transform = 'translate(-50%,-50%) scale(.3)'; r.style.opacity = '1';
      requestAnimationFrame(() => requestAnimationFrame(() => {
        r.style.transition = 'transform .35s ease-out, opacity .45s ease-out';
        r.style.transform = 'translate(-50%,-50%) scale(1.4)'; r.style.opacity = '0';
      }));
    }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""


def move_to(page, x, y, ms=700):
    page.mouse.move(x, y, steps=max(8, ms // 16))


def point(page, locator, ms=700):
    locator.scroll_into_view_if_needed()
    b = locator.bounding_box()
    move_to(page, b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, ms)


def click(page, locator, ms=700, pause=900):
    point(page, locator, ms)
    page.wait_for_timeout(180)
    page.mouse.down()
    page.mouse.up()
    page.wait_for_timeout(pause)


def scroll(page, dy, steps=30, pause=600):
    for _ in range(steps):
        page.mouse.wheel(0, dy / steps)
        page.wait_for_timeout(16)
    page.wait_for_timeout(pause)


def walkthrough(page, mark):
    sidebar = page.locator(".sidebar, aside")

    # Home: the headline numbers, then the charts
    point(page, page.get_by_text("Active time").first, 900)
    page.wait_for_timeout(700)
    move_to(page, 1000, 520)
    scroll(page, 700, pause=1400)
    scroll(page, 600, pause=1400)
    scroll(page, -1300, steps=35)

    # a session: hover a glossary term, glance at its knowledge (the README's GIF), expand a tool call
    mark("gif")
    click(page, page.locator("aside, nav").get_by_text("Stop double charges when Strip").first, 900, pause=1600)
    point(page, page.locator(".s-summary .gterm").first, 900)
    page.wait_for_timeout(1800)
    point(page, page.locator(".s-kchip.k-gotcha").first, 800)
    page.wait_for_timeout(1000)
    mark("gif_end")  # before the transcript: a whole new page each frame would blow the GIF's budget
    click(page, page.locator('button[data-tab="transcript"]'), 800, pause=1000)  # a session opens on Details
    move_to(page, 760, 600, 600)
    scroll(page, 380, pause=800)
    click(page, page.locator("details.tool-row summary").nth(1), 800, pause=1600)
    scroll(page, 450, pause=1200)
    scroll(page, 450, pause=1200)

    # ⌘K: one search across sessions, knowledge and the glossary; open the gotcha
    page.keyboard.press("Meta+k")
    page.wait_for_timeout(700)
    page.keyboard.type("webhook", delay=110)
    page.wait_for_timeout(1400)
    for _ in range(2):
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(450)
    page.wait_for_timeout(500)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2600)

    # the Map: open two categories, then a term
    click(page, page.locator('a[data-section="knowledge"]'), 900, pause=1500)
    click(page, sidebar.get_by_text("Map", exact=True).first, 800, pause=1500)
    click(page, page.locator(".mm-node", has_text="concept").first.locator(".mm-knob"), 900, pause=1400)
    click(page, page.locator(".mm-node", has_text="library").first.locator(".mm-knob"), 800, pause=1400)
    click(page, page.locator(".mm-node.term", has_text="Celery").first, 800, pause=2200)

    # the latest weekly review
    click(page, sidebar.get_by_text("Weekly reviews", exact=True).first, 900, pause=1800)
    move_to(page, 900, 600, 600)
    scroll(page, 600, pause=1500)
    scroll(page, 600, pause=1500)

    click(page, page.locator('a[data-section="home"]'), 900, pause=2200)


def record(url: str, frames_dir: Path) -> tuple[Path, tuple[float, float]]:
    """Run the walkthrough and return an ffconcat list of the captured frames with their real timing, and where the
    GIF's part starts and ends in the video, in seconds."""
    frames: list[tuple[Path, float]] = []
    marks: dict[str, float] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=2, color_scheme="light")
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)

        def on_frame(ev):
            path = frames_dir / f"f{len(frames):05d}.jpg"
            path.write_bytes(base64.b64decode(ev["data"]))
            frames.append((path, ev["metadata"]["timestamp"]))
            cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

        cdp.on("Page.screencastFrame", on_frame)
        page.goto(url)
        page.wait_for_selector("text=Active time")
        page.mouse.move(W / 2, H / 2)
        page.wait_for_timeout(300)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92})
        page.wait_for_timeout(1800)
        walkthrough(page, lambda name: marks.__setitem__(name, time.time()))
        cdp.send("Page.stopScreencast")
        end = time.time()
        browser.close()

    # the screencast only sends a frame when the page changes, so each frame lasts until the next one
    concat = frames_dir / "frames.ffconcat"
    ends = [t for _, t in frames[1:]] + [max(end, frames[-1][1] + 1.5)]
    lines = ["ffconcat version 1.0"]
    for (path, t), nxt in zip(frames, ends, strict=True):
        lines += [f"file '{path}'", f"duration {nxt - t:.4f}"]
    lines.append(f"file '{frames[-1][0]}'")
    concat.write_text("\n".join(lines) + "\n")
    t0 = frames[0][1]
    return concat, (marks["gif"] - t0, marks["gif_end"] - t0)


GIF_MAX = 590_000  # the large-file hook stops at 600 KB


def encode(concat: Path, clip: tuple[float, float], out: Path) -> None:
    video = out / "demo.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-vf", f"fps=30,scale={W}:{H}:flags=lanczos,format=yuv420p", "-c:v", "libx264",
                    "-preset", "slow", "-crf", "20", "-movflags", "+faststart", str(video)], check=True)
    start, end = clip
    for width, colors in ((960, 96), (840, 64), (720, 48)):  # the largest that fits
        gif = (f"fps=8,scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors={colors}:stats_mode=diff[p];"
               "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(video),
                        "-vf", gif, str(out / "demo.gif")], check=True)
        if (out / "demo.gif").stat().st_size <= GIF_MAX:
            return
    raise SystemExit(f"demo.gif is {(out / 'demo.gif').stat().st_size // 1000} KB even at {width} px: shorten its part")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8898, help="port of a dashboard running on the demo data")
    ap.add_argument("--out", type=Path, default=IMAGES, help="where demo.mp4 and demo.gif go (default docs/images)")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        concat, clip = record(f"http://127.0.0.1:{args.port}/", Path(tmp))
        encode(concat, clip, args.out)
    print(f"wrote {args.out / 'demo.mp4'} and {args.out / 'demo.gif'}")


if __name__ == "__main__":
    main()
