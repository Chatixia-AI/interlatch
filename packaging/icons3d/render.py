"""Render Interlatch's 3D art with the Chatixia Studio Blueprint Cast and export it.

Writes src/interlatch/web/art-<name>.webp (dashboard art) and packaging/macos/icon-3d.webp (the app icon source,
which packaging/macos/make_icon.py turns into every app, dashboard and phone icon).

    uv run --no-project --with playwright --with pillow python packaging/icons3d/render.py

Needs blueprint-cast.js beside this file (see README.md) and Chromium for Playwright.
"""

import base64
import functools
import http.server
import json
import socket
import threading
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
WEB = ROOT / "src" / "interlatch" / "web"
APP_ICON = ROOT / "packaging" / "macos" / "icon-3d.webp"
ART_PX = 256  # shown at up to 112 CSS px, so 2x stays sharp



class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # no line per request
        pass

def render_all(specs: dict) -> dict[str, Image.Image]:
    if not (HERE / "blueprint-cast.js").exists():
        raise SystemExit("blueprint-cast.js is missing: see packaging/icons3d/README.md")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    handler = functools.partial(Quiet, directory=str(HERE))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            page = browser.new_page()
            page.on("pageerror", lambda e: print("page error:", e))
            page.goto(f"http://127.0.0.1:{port}/render.html")
            page.wait_for_function("window.ready === true")
            for name, spec in specs.items():
                url = page.evaluate("s => renderIcon(s)", spec)
                out[name] = Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGBA")
                print("rendered", name)
            browser.close()
    finally:
        srv.shutdown()
    return out


def trimmed(im: Image.Image, px: int) -> Image.Image:
    """Crop to the drawn pixels, centre in a square with a small even margin, resize."""
    box = im.getchannel("A").point(lambda a: 255 if a > 6 else 0).getbbox()
    im = im.crop(box)
    side = int(max(im.size) * 1.06)
    sq = Image.new("RGBA", (side, side))
    sq.alpha_composite(im, ((side - im.width) // 2, (side - im.height) // 2))
    return sq.resize((px, px), Image.Resampling.LANCZOS)


def app_tile(emblem: Image.Image) -> Image.Image:
    """The emblem on a navy rounded tile drawn from the films' blueprint floor, on the 1024 macOS icon grid."""
    S, M, R = 1024, 100, 185
    T = S - 2 * M
    tile = Image.new("RGBA", (T, T))
    d = ImageDraw.Draw(tile)
    for y in range(T):  # floor navy at the top to sky navy at the bottom
        k = y / T
        d.line([(0, y), (T, y)], fill=(round(16 - 8 * k), round(46 - 20 * k), round(86 - 37 * k), 255))
    grid = Image.new("RGBA", (T, T))
    g = ImageDraw.Draw(grid)
    for i in range(0, T + 1, 41):
        major = (i // 41) % 4 == 0
        fill = (124, 199, 255, 46 if major else 24)
        g.line([(i, 0), (i, T)], fill=fill, width=2 if major else 1)
        g.line([(0, i), (T, i)], fill=fill, width=2 if major else 1)
    tile.alpha_composite(grid)
    glow = Image.new("RGBA", (T, T))
    ImageDraw.Draw(glow).ellipse([T * 0.18, T * 0.2, T * 0.82, T * 0.86], fill=(124, 199, 255, 60))
    tile.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))
    art = emblem.crop(emblem.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox())
    scale = T * 0.68 / max(art.size)
    art = art.resize((round(art.width * scale), round(art.height * scale)), Image.Resampling.LANCZOS)
    x, y = (T - art.width) // 2, (T - art.height) // 2 + 6
    shadow = Image.new("RGBA", (T, T))
    ImageDraw.Draw(shadow).ellipse([x + art.width * 0.05, y + art.height * 0.9, x + art.width * 0.95, y + art.height * 1.04], fill=(0, 8, 20, 150))
    tile.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))
    tile.alpha_composite(art, (x, y))
    mask = Image.new("L", (T, T))
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, T - 1, T - 1], R, fill=255)
    icon = Image.new("RGBA", (S, S))
    icon.paste(tile, (M, M), mask)
    return icon


def main():
    specs = json.loads((HERE / "art.json").read_text())
    images = render_all(specs)
    for name, im in images.items():
        if name == "app-icon":
            app_tile(im).save(APP_ICON, "WEBP", lossless=True)
            print("wrote", APP_ICON.relative_to(ROOT))
        else:
            path = WEB / f"art-{name}.webp"
            trimmed(im, ART_PX).save(path, "WEBP", quality=90, method=6)
            print("wrote", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
