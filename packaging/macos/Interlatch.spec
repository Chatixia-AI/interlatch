# PyInstaller spec for Interlatch.app. Build with packaging/macos/build.sh (it sets the environment below).
#   INTERLATCH_CODESIGN_IDENTITY  "Developer ID Application: …" to sign for distribution (default: ad-hoc)
#   INTERLATCH_TARGET_ARCH        arm64 (default: the building Python's architecture)
import os
import re
from importlib.metadata import version
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parents[1]
HERE = Path(SPECPATH)
# the installed package's version (from the git tag); the bundle takes its release numbers only (0.7.1.dev3+g… -> 0.7.1)
VERSION = re.match(r"\d+(?:\.\d+)*", version("interlatch")).group(0)
BUNDLE_ID = "com.interlatch.app"  # keep in sync with interlatch.runtime.install.APP_BUNDLE_ID
IDENTITY = os.environ.get("INTERLATCH_CODESIGN_IDENTITY") or None

a = Analysis(
    [str(HERE / "interlatch_app.py")],
    pathex=[str(ROOT / "src")],
    # the package metadata carries the version (interlatch.__version__ reads it)
    datas=[(str(ROOT / "src" / "interlatch" / "web"), "interlatch/web")] + copy_metadata("interlatch"),
    # interlatch imports most modules lazily inside functions, and rich loads some of its own on demand
    hiddenimports=collect_submodules("interlatch") + collect_submodules("rich"),
    excludes=["tkinter", "pytest", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Interlatch",  # Contents/MacOS/Interlatch, which interlatch.runtime.install's shim runs
    console=False,
    argv_emulation=False,
    target_arch=os.environ.get("INTERLATCH_TARGET_ARCH") or None,
    codesign_identity=IDENTITY,
    entitlements_file=str(HERE / "entitlements.plist"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Interlatch")
app = BUNDLE(
    coll,
    name="Interlatch.app",
    icon=str(HERE / "Chronicle.icns"),
    bundle_identifier=BUNDLE_ID,
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "Interlatch",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "13.0",
        "LSApplicationCategoryType": "public.app-category.developer-tools",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "Shared memory for your coding agents.",
    },
)
