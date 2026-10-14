"""Reading a model's reply as the one JSON object it was asked for, past fences, prose and stray escapes."""

from __future__ import annotations

import json
import re

_INVALID_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def parse_json_object(text: str) -> dict | None:
    """Parse a JSON object from model text, tolerating code fences and surrounding prose."""
    text = (text or "").strip()
    if not text:
        return None
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        candidates.append(fence.group(1))
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand, strict=False)  # tolerate raw control characters inside strings
        except ValueError:
            try:  # stray backslashes (regexes, Windows paths) are the usual culprit
                obj = json.loads(_INVALID_ESCAPE.sub(r"\\\\", cand), strict=False)
            except ValueError:
                continue
        if isinstance(obj, dict):
            # a model occasionally wraps the answer in a single key like "parameter"
            if len(obj) == 1 and isinstance(next(iter(obj.values())), dict):
                inner = next(iter(obj.values()))
                if len(inner) > 1:
                    return inner
            return obj
    return None
