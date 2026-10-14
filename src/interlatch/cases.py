"""A lesson's teaching material beyond its case file (analyze.case_of): principle, checks, clues, topics and a diagram.
Grounded in the session; personal learning history stays in the browser."""

from __future__ import annotations

import re

from .redact import redact
from .learning import topic_ids

LESSON_KINDS = ("fix", "gotcha", "decision", "learning", "pattern")


VISUAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["type", "title", "nodes", "edges"],
    "properties": {
        "type": {"type": "string", "enum": ["flow", "relationship", "comparison"]},
        "title": {"type": "string", "description": "Short title of the concept being illustrated"},
        "nodes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "label", "detail"],
            "properties": {"id": {"type": "string", "description": "Unique short ASCII slug"},
                           "label": {"type": "string", "description": "1-4 words, using actual session terminology"},
                           "detail": {"type": "string", "description": "One sentence explaining this part or tradeoff"}}}},
        "edges": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["from", "to", "label"],
            "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                           "label": {"type": "string", "description": "Short, established relationship or action"}}}},
    },
    "description": "Small explanatory diagram: 2-4 parts, up to 5 connections, grounded in the lesson. "
                   "A flow shows order, a relationship shows connections, a comparison shows real alternatives. "
                   "Empty title, nodes and edges when unsupported. Never invent components or relationships.",
}


def visual_of(raw) -> dict | None:
    if not isinstance(raw, dict) or raw.get("type") not in ("flow", "relationship", "comparison"):
        return None

    def text(value, limit):
        return redact(value.strip())[:limit] if isinstance(value, str) else ""

    nodes = {}
    for node in raw.get("nodes") if isinstance(raw.get("nodes"), list) else []:
        if not isinstance(node, dict):
            continue
        nid, label = text(node.get("id"), 32), text(node.get("label"), 80)
        if re.fullmatch(r"[A-Za-z0-9_-]+", nid) and label and nid not in nodes and len(nodes) < 4:
            nodes[nid] = {"id": nid, "label": label, "detail": text(node.get("detail"), 600)}
    edges, seen = [], set()
    for edge in raw.get("edges") if isinstance(raw.get("edges"), list) else []:
        if not isinstance(edge, dict):
            continue
        a, b = edge.get("from"), edge.get("to")
        if not isinstance(a, str) or not isinstance(b, str) or a not in nodes or b not in nodes or a == b or (a, b) in seen:
            continue
        seen.add((a, b))
        edges.append({"from": a, "to": b, "label": text(edge.get("label"), 40)})
    if len(nodes) < 2 or not edges and raw["type"] != "comparison":
        return None
    return {"type": raw["type"], "title": text(raw.get("title"), 100), "nodes": list(nodes.values()), "edges": edges[:5]}


def lesson_of(raw: dict) -> dict:
    """The teaching fields the analysis supported, redacted; empty ones are left out, nothing is invented."""
    def text(value) -> str:
        return redact(value.strip())[:6000] if isinstance(value, str) else ""

    def texts(value, limit: int) -> list[str]:
        return [text(x) for x in value if isinstance(x, str) and text(x)][:limit] if isinstance(value, list) else []

    out = {"clues": texts(raw.get("clues"), 6), "principle": text(raw.get("principle")),
           "checklist": texts(raw.get("checklist"), 4), "topics": topic_ids(raw.get("topics")),
           "visual": visual_of(raw.get("visual"))}
    return {k: v for k, v in out.items() if v}
