"""The browser extension's files: the folder Chrome loads stays put and follows the installed version's files."""

from __future__ import annotations

import json
import re
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
    for f in ("background.js", "capture.js", "popup.html", "popup.js", "popup.css", "plex-sans-400.woff2",
              "plex-sans-600.woff2", "plex-mono-400.woff2", f"_locales/{manifest['default_locale']}/messages.json",
              *manifest["icons"].values()):
        assert (browser_extension.BUNDLED / f).is_file(), f


def _messages(lang):
    return json.loads((browser_extension.BUNDLED / "_locales" / lang / "messages.json").read_text(encoding="utf-8"))


def test_both_languages_say_the_same_things():
    en, ja = _messages("en"), _messages("ja")
    assert en.keys() == ja.keys()
    subs = lambda m: sorted(re.findall(r"\$\d", m["message"]))  # noqa: E731
    assert not [k for k in en if subs(en[k]) != subs(ja[k])]
    for messages in (en, ja):  # the Chrome Web Store's limits
        assert len(messages["name"]["message"]) <= 75 and len(messages["description"]["message"]) <= 132


def test_every_word_the_extension_uses_has_a_message():
    root = browser_extension.BUNDLED
    used = set(re.findall(r'data-i18n(?:-title|-label)?="(\w+)"', (root / "popup.html").read_text(encoding="utf-8")))
    for script in ("popup.js", "background.js", "capture.js"):
        source = (root / script).read_text(encoding="utf-8")
        used |= set(re.findall(r'\bt\(\s*"(\w+)"', source))
    popup = (root / "popup.js").read_text(encoding="utf-8")
    for table in re.findall(r"const (?:TITLES|DETAILS) = \{(.*?)\};", popup, re.S):
        used |= set(re.findall(r':\s*"(\w+)"', table))
    used |= set(re.findall(r't\(st\.slowed \? "(\w+)" : "(\w+)"', popup)[0])
    assert used >= {"stInstall", "stStopped", "stSaving", "siteSlowed", "errNotRunning"}  # the search found them
    assert not used - _messages("en").keys()
