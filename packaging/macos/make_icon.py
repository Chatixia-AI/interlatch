"""Export Interlatch's committed 3D artwork to app, dashboard, favicon and phone icons.

The artwork, icon-3d.webp, is rendered by packaging/icons3d/render.py.

Pillow writes the PNGs on any platform; macOS's iconutil also writes Chronicle.icns (Interlatch.app's icon).

    uv run --group build python packaging/macos/make_icon.py
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).parent
WEB = HERE.parents[1] / "src" / "interlatch" / "web"
SOURCE = HERE / "icon-3d.webp"
S, M = 1024, 100  # macOS icon grid: artwork fits within an 824px square centred in 1024
NAVY = (8, 26, 49)


def load_icon() -> Image.Image:
    """Fit the rounded tile to the Dock grid, ignoring nearly transparent pixels outside the artwork."""
    with Image.open(SOURCE) as source:
        artwork = source.convert("RGBA")
    bounds = artwork.getchannel("A").point(lambda alpha: 255 if alpha >= 16 else 0).getbbox()
    if bounds is None:
        raise ValueError(f"icon artwork is empty: {SOURCE}")
    tile = ImageOps.contain(artwork.crop(bounds), (S - 2 * M, S - 2 * M), Image.Resampling.LANCZOS)
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.alpha_composite(tile, ((S - tile.width) // 2, (S - tile.height) // 2))
    return icon


def phone_icon(icon: Image.Image) -> Image.Image:
    """Opaque navy, full bleed: phones apply their own mask; keep the emblem inside the safe area."""
    base = Image.new("RGBA", icon.size, NAVY + (255,))
    base.alpha_composite(icon)
    return base.convert("RGB")


def write_web_icons(icon: Image.Image) -> None:
    phone = phone_icon(icon)
    for size in (180, 192, 512):  # iOS home screen; the web app manifest's two sizes
        phone.resize((size, size), Image.Resampling.LANCZOS).save(WEB / f"icon-{size}.png", optimize=True)


def main() -> None:
    icon = load_icon()
    icon.save(HERE / "icon.png", optimize=True)
    # the dashboard's touch icon, and the Dock icon when the app runs from source (Interlatch.app uses the .icns)
    icon.resize((256, 256), Image.Resampling.LANCZOS).save(WEB / "icon.png", optimize=True)
    icon.resize((32, 32), Image.Resampling.LANCZOS).save(WEB / "favicon.png", optimize=True)
    write_web_icons(icon)
    if not shutil.which("iconutil"):
        print(f"iconutil not found (macOS only): wrote PNG icons in {HERE} and {WEB}")
        return
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Chronicle.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                icon.resize((px, px), Image.Resampling.LANCZOS).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "Chronicle.icns")], check=True)
    print(f"wrote {HERE / 'Chronicle.icns'} and {HERE / 'icon.png'}")


if __name__ == "__main__":
    main()
