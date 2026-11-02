"""The ZIP to upload to the Chrome Web Store: the extension's files as they ship, named after its version, in dist/."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "interlatch" / "browser_extension"


def main() -> None:
    version = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))["version"]
    target = ROOT / "dist" / f"interlatch-browser-extension-{version}.zip"
    target.parent.mkdir(exist_ok=True)
    files = sorted(p for p in SOURCE.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(SOURCE).as_posix())
    print(f"{target} ({len(files)} files)")


if __name__ == "__main__":
    main()
