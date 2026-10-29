"""The browser extension's files: the folder Chrome loads stays put and follows the installed version's files."""

from __future__ import annotations

import json
import types

from interlatch.runtime import browser_extension


def test_folder_follows_the_bundled_files(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "manifest.json").write_text('{"version": "1.0.0"}')
    (bundled / "capture.js").write_text("one")
    monkeypatch.setattr(browser_extension, "BUNDLED", bundled)
    cfg = types.SimpleNamespace(home=tmp_path / "home")

    dst = browser_extension.folder(cfg)
    assert dst == cfg.home / "browser-extension" and (dst / "capture.js").read_text() == "one"
    (bundled / "capture.js").write_text("two")  # a newer Interlatch
    (bundled / "capture.js.map").write_text("")
    (dst / "old.js").write_text("gone in this version")
    browser_extension.folder(cfg)
    assert (dst / "capture.js").read_text() == "two" and (dst / "capture.js.map").exists() and not (dst / "old.js").exists()


def test_no_folder_without_an_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_extension, "BUNDLED", tmp_path / "missing")
    assert browser_extension.folder(types.SimpleNamespace(home=tmp_path)) is None


def test_the_shipped_manifest():
    manifest = json.loads((browser_extension.BUNDLED / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    # it reads only the two sites, and sends only to this computer's dashboard
    assert manifest["host_permissions"] == ["http://127.0.0.1/*"] and manifest["permissions"] == ["storage"]
    assert manifest["content_scripts"][0]["matches"] == ["https://claude.ai/*", "https://chatgpt.com/*"]
    for f in ("background.js", "capture.js", "popup.html", "popup.js", "popup.css",
              *manifest["icons"].values()):
        assert (browser_extension.BUNDLED / f).is_file(), f
