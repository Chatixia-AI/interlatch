"""Prompt text as coding agents record it: the wrapper tags IDEs and harnesses add around what you typed, and
slash commands."""

from __future__ import annotations

import re

_WRAPPER_TAGS = ("ide_opened_file", "ide_selection", "ide_diagnostics", "system-reminder")
_WRAPPER_RE = re.compile(r"<(%s)\b[^>]*>.*?</\1>" % "|".join(_WRAPPER_TAGS), re.S)
_CMD_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_CMD_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
TAG_RE = re.compile(r"</?[a-z][\w-]*>")


def clean_prompt(text: str) -> str:
    text = _WRAPPER_RE.sub("", text)
    return text.strip()


def parse_command(text: str) -> tuple[str, str]:
    name = (_CMD_NAME_RE.search(text) or [None, ""])[1].strip()
    args = (_CMD_ARGS_RE.search(text) or [None, ""])[1].strip()
    return name, args
