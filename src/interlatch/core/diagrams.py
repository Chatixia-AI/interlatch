"""A project's architecture sketch: the parts and connections its knowledge base synthesis draws, kept only where cited
knowledge items back them, and written as a Mermaid flowchart for the Markdown knowledge base. Laying it out and the
Excalidraw scene are interfaces/exports/diagrams.py."""

from __future__ import annotations

import re

from .util import one_line

KINDS = ("component", "interface", "store", "external")
MAX_NODES, MAX_EDGES = 14, 24

DIAGRAM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nodes", "edges"],
    "description": "Architecture sketch; empty nodes and edges when the items do not say how the project is built",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "label", "kind", "note", "sources"],
                "properties": {
                    "id": {"type": "string", "description": "short unique slug, used by edges"},
                    "label": {"type": "string", "description": "1-4 words, the project's own name for the part"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "note": {"type": "string", "description": "At most 15 words: what it is or does; keep concrete identifiers"},
                    "sources": {"type": "array", "items": {"type": "integer"}, "description": "ids of the items that state it"},
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["from", "to", "label", "sources"],
                "properties": {
                    "from": {"type": "string", "description": "node id"},
                    "to": {"type": "string", "description": "node id"},
                    "label": {"type": "string", "description": "1-3 words: calls, reads, writes, serves…"},
                    "sources": {"type": "array", "items": {"type": "integer"}, "description": "ids of the items that state it"},
                },
            },
        },
    },
}


def _ids(values, valid: set[int] | None) -> list[int]:
    out = []
    for v in values if isinstance(values, list) else []:
        try:
            i = int(v)
        except (TypeError, ValueError):
            continue
        if (valid is None or i in valid) and i not in out:
            out.append(i)
    return out


def normalize_diagram(raw, valid_ids: set[int] | None = None) -> dict | None:
    """Keep what a model reply draws only where it holds together: every edge cites a knowledge item (one of
    `valid_ids` when given) and joins two different nodes, and every node sits on an edge. None if nothing is left."""
    if not isinstance(raw, dict):
        return None
    nodes: dict[str, dict] = {}
    for n in raw.get("nodes") if isinstance(raw.get("nodes"), list) else []:
        if not isinstance(n, dict):
            continue
        label = one_line(n.get("label") or "", 48)
        nid = one_line(n.get("id") or label, 64)
        if not label or not nid or nid in nodes:
            continue
        nodes[nid] = {"id": nid, "label": label, "kind": n.get("kind") if n.get("kind") in KINDS else "component",
                      "note": one_line(n.get("note") or "", 200), "sources": _ids(n.get("sources"), valid_ids)}
    edges: dict[tuple[str, str], dict] = {}
    for e in raw.get("edges") if isinstance(raw.get("edges"), list) else []:
        if not isinstance(e, dict):
            continue
        a, b = one_line(e.get("from") or "", 64), one_line(e.get("to") or "", 64)
        sources = _ids(e.get("sources"), valid_ids)
        if a not in nodes or b not in nodes or a == b or not sources:
            continue
        if (a, b) in edges:  # the same connection twice: one arrow, the evidence pooled
            edges[(a, b)]["sources"] += [i for i in sources if i not in edges[(a, b)]["sources"]]
            continue
        edges[(a, b)] = {"from": a, "to": b, "label": one_line(e.get("label") or "", 40), "sources": sources}
    edge_list = list(edges.values())[:MAX_EDGES]
    degree: dict[str, int] = {}
    for e in edge_list:
        degree[e["from"]] = degree.get(e["from"], 0) + 1
        degree[e["to"]] = degree.get(e["to"], 0) + 1
    order = list(nodes)
    keep = set(sorted(degree, key=lambda k: (-degree[k], order.index(k)))[:MAX_NODES])  # the best-connected parts
    edge_list = [e for e in edge_list if e["from"] in keep and e["to"] in keep]
    used = {e["from"] for e in edge_list} | {e["to"] for e in edge_list}
    if not edge_list:
        return None
    return {"nodes": [nodes[k] for k in order if k in used], "edges": edge_list}


# ---------------------------------------------------------------------------------------------------- Mermaid
_MERMAID_SHAPE = {"component": ('["', '"]'), "interface": ('(["', '"])'), "store": ('[("', '")]'), "external": ('{{"', '"}}')}


def _mermaid_text(s: str) -> str:
    return re.sub(r'["<>]', lambda m: {'"': "#quot;", "<": "#lt;", ">": "#gt;"}[m.group()], s)


def to_mermaid(d: dict) -> str:
    """A Mermaid flowchart of the diagram (for the Markdown knowledge base: Obsidian, GitHub, and agents read it)."""
    ref = {n["id"]: f"n{i}" for i, n in enumerate(d["nodes"])}
    lines = ["flowchart LR"]
    for n in d["nodes"]:
        a, b = _MERMAID_SHAPE[n["kind"]]
        lines.append(f"  {ref[n['id']]}{a}{_mermaid_text(n['label'])}{b}")
    for e in d["edges"]:
        label = f'|"{_mermaid_text(e["label"])}"|' if e["label"] else ""
        lines.append(f"  {ref[e['from']]} -->{label} {ref[e['to']]}")
    return "\n".join(lines)
