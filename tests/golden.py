"""Golden files for the safety-net tests (tests/goldens/): compare a value with the JSON written for it.

Run with UPDATE_GOLDENS=1 to rewrite the files from what the code produces now, then review the diff:
    UPDATE_GOLDENS=1 uv run --locked pytest -q tests/test_cli_pins.py tests/test_parser_goldens.py tests/test_layout_goldens.py

Files are written with sorted keys and "\\n" line endings, and read back with any line endings (a Windows checkout),
so they compare the same on every OS.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

GOLDENS = Path(__file__).parent / "goldens"


def dumps(data) -> str:
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def assert_golden(rel: str, data) -> None:
    """`data` (JSON-able) serializes exactly as tests/goldens/<rel> holds it."""
    path = GOLDENS / rel
    text = dumps(data)
    if os.environ.get("UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return
    assert path.exists(), f"no golden {path}: run the test with UPDATE_GOLDENS=1 to write it"
    expected = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert text == expected, f"{rel} changed: if intended, rerun with UPDATE_GOLDENS=1 and review the diff"
