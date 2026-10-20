"""Feedback (runtime/feedback.py): `interlatch feedback`, Settings › Feedback and the VS Code extension open a GitHub
page with this install's details filled in, and the details never say who or where the person is."""

from __future__ import annotations

import dataclasses
from urllib.parse import parse_qs, urlsplit

import pytest

from interlatch import __version__
from interlatch.interfaces.cli.main import main
from interlatch.interfaces.web import api
from interlatch.interfaces.web.context import Context
from interlatch.runtime import feedback


def _query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}


def test_a_problem_is_a_new_issue_and_an_idea_a_discussion(env):
    rows = [("Interlatch", "1.2.3")]
    bug = feedback.report("bug", rows, "It crashed")
    assert bug["url"].startswith("https://github.com/Chatixia-AI/interlatch/issues/new?")
    q = _query(bug["url"])
    assert q["title"] == "It crashed" and "| Interlatch | 1.2.3 |" in q["body"] and "Steps to reproduce" in q["body"]
    assert "labels" not in q  # GitHub answers 404 to a labels parameter from anyone outside the project
    idea = feedback.report("idea", rows)
    assert idea["url"].startswith("https://github.com/Chatixia-AI/interlatch/discussions/new?category=ideas&")
    assert "What would help" in _query(idea["url"])["body"]
    with pytest.raises(ValueError):
        feedback.report("rant", rows)


def test_spaces_are_percent_encoded_not_plus(env):
    # a + is a space only to form decoders; %20 is one to everything that reads the address
    url = feedback.report("bug", [("Python", "3.12 x")])["url"]
    assert "+" not in urlsplit(url).query and "%20" in url


def test_details_say_what_the_install_is_never_where(synced):
    cfg = dataclasses.replace(synced["cfg"], hub_url="https://hub.example.internal:8443")
    rows = dict(feedback.details(cfg, synced["conn"]))
    assert rows["Interlatch"] == __version__
    assert rows["Hub"] == "sends its sessions to a hub"
    assert rows["Agents recorded"] == "claude"
    assert set(rows) == {"Interlatch", "Installed with", "System", "Python", "Analysis", "Hub", "Agents recorded"}
    text = feedback.report("bug", list(rows.items()))["body"]
    for private in ("hub.example.internal", str(cfg.home), str(synced["tmp"]), "/Users/", "\\Users\\"):
        assert private not in text


def test_the_cli_prints_the_details_and_the_address_without_a_browser(synced, monkeypatch, capsys):
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: pytest.fail("opened a browser"))
    assert main(["feedback", "idea", "--title", "Dark mode for exports"]) == 0  # tests have no terminal: no question
    out = capsys.readouterr().out
    assert "Suggest an idea" in out and f"Interlatch:       {__version__}" in out
    assert "discussions/new?category=ideas&title=Dark%20mode%20for%20exports" in out.replace("\n", "")
    assert main(["bug", "--print"]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("https://github.com/Chatixia-AI/interlatch/issues/new?") and "\n" not in printed


def _ctx(env):
    return Context(lambda: env["cfg"], lambda c: None, lambda: env["conn"], None, None)


def test_the_dashboard_gives_the_full_details_here_and_only_the_version_to_others(synced):
    full = api.settings.feedback(_ctx(synced), {"kind": "bug"}, full=True)
    labels = [k for k, v in full["details"]]
    assert labels[:2] == ["Interlatch", "Installed with"] and "Agents recorded" in labels
    assert labels[-1] == "Dashboard language" and "filled in by the dashboard" in full["body"]
    other = api.settings.feedback(_ctx(synced), {"kind": "idea"}, full=False)
    assert [k for k, v in other["details"]] == ["Interlatch", "Hub", "Dashboard language"]
    assert "/discussions/new?category=ideas&" in other["url"]
    assert api.settings.feedback(_ctx(synced), {"kind": "nonsense"}, full=True)["kind"] == "bug"


def test_the_vscode_extension_adds_the_editor_kept_to_a_plain_line(synced):
    r = api.settings.feedback(_ctx(synced), {"kind": "bug", "client": "vscode", "extension": "0.4.0",
                                             "editor": "Visual Studio Code 1.95.0 | <b>&x=1\n## hi"}, full=True)
    rows = dict(r["details"])
    assert rows["Editor"] == "Visual Studio Code 1.95.0  bx1 hi" and rows["Extension"] == "0.4.0"
    assert "Dashboard language" not in rows and "filled in by the VS Code extension" in r["body"]
    no_editor = api.settings.feedback(_ctx(synced), {"kind": "bug", "client": "vscode", "editor": "<>|"}, full=True)
    assert "Editor" not in dict(no_editor["details"])
