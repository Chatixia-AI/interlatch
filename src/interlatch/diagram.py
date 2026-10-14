"""A project's architecture sketch: the parts and connections its knowledge base synthesis draws, kept only where cited
knowledge items back them, laid out left to right, and exported as an Excalidraw scene or a Mermaid flowchart.

The dashboard draws the layout with rough.js; the Excalidraw export reuses the same layout (measured for Excalidraw's
hand-drawn font), so the downloaded file looks like what the dashboard shows and stays editable."""

from __future__ import annotations

import json
import random
import re
import unicodedata
from itertools import pairwise

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


def for_kb(kb_json: str | None) -> dict | None:
    """The diagram stored in a knowledge base row, with its layout for the dashboard (None if it has none)."""
    try:
        data = json.loads(kb_json or "{}")
    except (TypeError, ValueError):
        return None
    d = normalize_diagram(data.get("diagram")) if isinstance(data, dict) else None
    return {**d, "layout": layout(d)} if d else None


# ---------------------------------------------------------------------------------------------------- layout
class Metrics:
    """Text sizes for one renderer: the dashboard's UI font, or Excalidraw's hand-drawn one (wider)."""

    def __init__(self, size: float = 13, char: float = 0.58, label_scale: float = 0.88):
        self.size, self.char = size, char
        self.label = size * label_scale  # edge labels
        self.line = round(size * 1.38, 1)
        self.label_line = round(self.label * 1.3, 1)

    def width(self, text: str, size: float | None = None) -> float:
        size = size or self.size
        return sum(size if unicodedata.east_asian_width(c) in "WF" else size * self.char for c in text)

    def wrap(self, text: str, max_w: float, size: float | None = None, max_lines: int = 3) -> list[str]:
        lines: list[str] = []

        def add(word):
            if lines and self.width(lines[-1] + " " + word, size) <= max_w:
                lines[-1] += " " + word
            else:
                lines.append(word)

        for word in text.split():
            while self.width(word, size) > max_w:  # wider than a line (a long identifier, CJK without spaces): split it
                cut = next((i for i in range(len(word) - 1, 0, -1) if self.width(word[:i], size) <= max_w), 1)
                lines.append(word[:cut])
                word = word[cut:]
            add(word)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1].rstrip() + "…"
        return lines or [""]


UI = Metrics()


def _pack(desired: list[float], seps: list[float]) -> list[float]:
    """Positions closest (least squares) to `desired` that keep their order and at least `seps` apart."""
    off = [0.0]
    for sp in seps:
        off.append(off[-1] + sp)
    blocks: list[list[float]] = []  # pool adjacent violators: [sum, count]
    for t in (d - o for d, o in zip(desired, off, strict=True)):
        blocks.append([t, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    z: list[float] = []
    for s, c in blocks:
        z += [s / c] * int(c)
    return [zi + o for zi, o in zip(z, off, strict=True)]


def _crossings(order: dict[int, list], adj_down: dict) -> int:
    n = 0
    for r in sorted(order)[:-1]:
        pos_next = {v: i for i, v in enumerate(order.get(r + 1, []))}
        pairs = [(i, pos_next[w]) for i, v in enumerate(order[r]) for w in adj_down.get(v, []) if w in pos_next]
        n += sum(1 for a in range(len(pairs)) for b in range(a + 1, len(pairs))
                 if (pairs[a][0] - pairs[b][0]) * (pairs[a][1] - pairs[b][1]) < 0)
    return n


def _bezier(p0, p1, t):
    """Point at t on the cubic from p0 to p1 that leaves and arrives horizontally (the curve every edge segment uses)."""
    (x0, y0), (x3, y3) = p0, p1
    dx = (x3 - x0) / 2
    c1, c2 = (x0 + dx, y0), (x3 - dx, y3)
    u = 1 - t
    return (u ** 3 * x0 + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * x3,
            u ** 3 * y0 + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * y3)


def layout(d: dict, m: Metrics = UI) -> dict:
    """Layered left-to-right layout: cycles broken, parts ranked by longest path, long edges routed through gaps,
    rows ordered to cut crossings, then placed so each part lines up with its neighbours."""
    s = m.size
    nodes, edges = d["nodes"], d["edges"]
    idx = {n["id"]: i for i, n in enumerate(nodes)}
    n_real = len(nodes)
    pairs = [(idx[e["from"]], idx[e["to"]]) for e in edges]

    # 1. break cycles: an edge back into the DFS stack is laid out reversed
    out: dict[int, list[int]] = {i: [] for i in range(n_real)}
    for a, b in pairs:
        out[a].append(b)
    state, back = [0] * n_real, set()

    def dfs(v):
        state[v] = 1
        for w in out[v]:
            if state[w] == 1:
                back.add((v, w))
            elif state[w] == 0:
                dfs(w)
        state[v] = 2

    for v in range(n_real):
        if state[v] == 0:
            dfs(v)
    dag = [(b, a) if (a, b) in back else (a, b) for a, b in pairs]

    # 2. rank by longest path from the sources
    rank = [0] * n_real
    for _ in range(n_real):
        changed = False
        for a, b in dag:
            if rank[b] < rank[a] + 1:
                rank[b], changed = rank[a] + 1, True
        if not changed:
            break
    # pull each part with no incoming edge right up to the rank before its nearest successor
    for v in range(n_real):
        succ = [b for a, b in dag if a == v]
        if succ and not any(b == v for _, b in dag):
            rank[v] = min(rank[b] for b in succ) - 1

    # 3. chains through virtual points so long edges pass between parts, not through them
    vrank: list[int] = list(rank)
    chains: list[list[int]] = []
    for a, b in dag:
        chain = [a]
        for r in range(rank[a] + 1, rank[b]):
            vrank.append(r)
            chain.append(len(vrank) - 1)
        chain.append(b)
        chains.append(chain)
    down: dict[int, list[int]] = {}
    up: dict[int, list[int]] = {}
    for chain in chains:
        for u, w in pairwise(chain):
            down.setdefault(u, []).append(w)
            up.setdefault(w, []).append(u)

    # 4. order each rank by the barycenter of its neighbours, keeping the order with the fewest crossings
    order: dict[int, list[int]] = {}
    for v in range(len(vrank)):
        order.setdefault(vrank[v], []).append(v)
    best, best_x = {r: list(o) for r, o in order.items()}, _crossings(order, down)
    ranks = sorted(order)
    for it in range(12):
        sweep, nbrs = (ranks[1:], up) if it % 2 == 0 else (ranks[-2::-1], down)
        for r in sweep:
            ref = {v: i for i, v in enumerate(order.get(r - 1 if nbrs is up else r + 1, []))}
            cur = {v: i for i, v in enumerate(order[r])}

            def key(v, ref=ref, cur=cur, nbrs=nbrs):
                ps = [ref[w] for w in nbrs.get(v, []) if w in ref]
                return sum(ps) / len(ps) if ps else cur[v]
            order[r].sort(key=key)
        x = _crossings(order, down)
        if x < best_x:
            best, best_x = {r: list(o) for r, o in order.items()}, x
    order = best

    # sizes: wrapped labels for parts, thin slots for the points long edges pass through
    pad_x, pad_y = s * 1.1, s * 0.85
    boxes = []
    for n in nodes:
        lines = m.wrap(n["label"], s * 11)
        w = max(s * 7, max(m.width(ln) for ln in lines) + 2 * pad_x)
        hgt = len(lines) * m.line + 2 * pad_y + (s * 0.9 if n["kind"] == "store" else 0)  # a cylinder's cap
        boxes.append({"w": round(w, 1), "h": round(hgt, 1), "lines": lines})
    size = [(b["w"], b["h"]) for b in boxes] + [(0, s * 0.9)] * (len(vrank) - n_real)

    # 5. columns: as wide as their widest part, gaps wide enough for the labels that sit in them
    labels = []
    for e, chain in zip(edges, chains, strict=True):
        lines = m.wrap(e["label"], s * 9, m.label, max_lines=2) if e["label"] else []
        labels.append({"lines": lines, "w": max((m.width(ln, m.label) for ln in lines), default=0),
                       "h": len(lines) * m.label_line, "gap": vrank[chain[0]]})
    col_w = {r: max((size[v][0] for v in order[r]), default=0) for r in ranks}
    gap = {r: max(s * 4.5, max((lb["w"] for lb in labels if lb["gap"] == r), default=0) + s * 3.2) for r in ranks}
    margin = s * 1.3
    col_x, x = {}, margin
    for r in ranks:
        col_x[r] = x
        x += col_w[r] + gap[r]
    width = x - gap[ranks[-1]] + margin

    # 6. vertical placement: stack each rank, then pull parts toward their neighbours without overlapping
    def seps(r):
        o = order[r]
        return [(size[a][1] + size[b][1]) / 2 + (s * 1.7 if a < n_real and b < n_real else s * 0.8) for a, b in pairwise(o)]

    y = [0.0] * len(vrank)
    for r in ranks:
        pos = _pack([0.0] * len(order[r]), seps(r))
        mid = (pos[0] + pos[-1]) / 2
        for v, p in zip(order[r], pos, strict=True):
            y[v] = p - mid
    for it in range(16):
        sweep, nbrs = (ranks[1:], up) if it % 2 == 0 else (ranks[-2::-1], down)
        if it >= 8:  # settle against both sides
            sweep, nbrs = ranks, None
        for r in sweep:
            desired = []
            for v in order[r]:
                ns = (up.get(v, []) + down.get(v, [])) if nbrs is None else nbrs.get(v, [])
                desired.append(sum(y[w] for w in ns) / len(ns) if ns else y[v])
            for v, p in zip(order[r], _pack(desired, seps(r)), strict=True):
                y[v] = p
    top = min(y[v] - size[v][1] / 2 for v in range(len(vrank)))
    shift = margin - top
    y = [v + shift for v in y]
    height = max(y[v] + size[v][1] / 2 for v in range(len(vrank))) + margin

    def cx(v):
        r = vrank[v]
        return col_x[r] + col_w[r] / 2

    out_nodes = []
    for i, n in enumerate(nodes):
        b = boxes[i]
        out_nodes.append({"id": n["id"], "x": round(cx(i) - b["w"] / 2, 1), "y": round(y[i] - b["h"] / 2, 1),
                          "w": b["w"], "h": b["h"], "lines": b["lines"]})

    # 7. ports: edges leaving or entering one side of a part spread along it, in the order of their far ends
    sides: dict[tuple[int, str], list[tuple[int, int]]] = {}
    routes = []
    for k, ((a, b), chain) in enumerate(zip(pairs, chains, strict=True)):
        pts = chain if (a, b) not in back else chain[::-1]  # drawn from the real source to the real target
        routes.append(pts)
        fwd = vrank[pts[-1]] > vrank[pts[0]]
        sides.setdefault((pts[0], "r" if fwd else "l"), []).append((k, pts[1]))
        sides.setdefault((pts[-1], "l" if fwd else "r"), []).append((k, pts[-2]))
    port: dict[tuple[int, int], tuple[float, float]] = {}
    for (v, side), lst in sides.items():
        lst.sort(key=lambda kw: y[kw[1]])
        nb = out_nodes[v]
        span = nb["h"] * 0.6
        for j, (k, _) in enumerate(lst):
            py = y[v] + (span * ((j + 1) / (len(lst) + 1) - 0.5) if len(lst) > 1 else 0)
            px = nb["x"] - 1 if side == "l" else nb["x"] + nb["w"] + 1
            port[(k, v)] = (round(px, 1), round(py, 1))

    placed = [(nb["x"], nb["y"], nb["w"], nb["h"]) for nb in out_nodes]
    out_edges = []
    for k, pts in enumerate(routes):
        points = [port[(k, pts[0])]] + [(round(cx(v), 1), round(y[v], 1)) for v in pts[1:-1]] + [port[(k, pts[-1])]]
        d_path = f"M{points[0][0]},{points[0][1]}"
        for p, q in pairwise(points):
            dx = (q[0] - p[0]) / 2
            d_path += f" C{round(p[0] + dx, 1)},{p[1]} {round(q[0] - dx, 1)},{q[1]} {q[0]},{q[1]}"
        ex, ey = points[-1]
        sgn = 1 if points[-1][0] > points[-2][0] else -1
        head = [[round(ex - sgn * s * 0.7, 1), round(ey - s * 0.42, 1)], [ex, ey],
                [round(ex - sgn * s * 0.7, 1), round(ey + s * 0.42, 1)]]
        lb = labels[k]
        label = None
        if lb["lines"]:
            # the segment that crosses the label's gap, at the first spot clear of parts and earlier labels
            seg = next(((p, q) for p, q in pairwise(points) if min(p[0], q[0]) <= col_x[lb["gap"]] + col_w[lb["gap"]] + 1),
                       (points[0], points[1]))
            for t in (0.5, 0.35, 0.65, 0.22, 0.78):
                lx, ly = _bezier(seg[0], seg[1], t)
                bx = (lx - lb["w"] / 2 - 3, ly - lb["h"] / 2 - 2, lb["w"] + 6, lb["h"] + 4)
                if not any(bx[0] < px + pw and px < bx[0] + bx[2] and bx[1] < py + ph and py < bx[1] + bx[3] for px, py, pw, ph in placed):
                    break
            placed.append(bx)
            label = {"x": round(lx, 1), "y": round(ly, 1), "w": round(lb["w"], 1), "lines": lb["lines"]}
        out_edges.append({"from": edges[k]["from"], "to": edges[k]["to"], "points": [list(p) for p in points],
                          "d": d_path, "head": head, "label": label})
    return {"width": round(width, 1), "height": round(height, 1), "font": s, "line": m.line, "label_font": round(m.label, 1),
            "label_line": m.label_line, "nodes": out_nodes, "edges": out_edges}


# ---------------------------------------------------------------------------------------------------- exports
# Excalidraw's own palette: light fills, dark ink
_FILL = {"component": "#a5d8ff", "interface": "#b2f2bb", "store": "#ffec99", "external": "#e9ecef"}
_KIND_NAME = {"component": "Code it owns", "interface": "Way in (CLI, UI, API)", "store": "Data it keeps", "external": "External"}
_INK, _MUTED = "#1e1e1e", "#495057"
_HAND = Metrics(size=16, char=0.62, label_scale=0.9)  # Virgil


def to_excalidraw(d: dict, title: str = "") -> dict:
    """An Excalidraw scene of the diagram: parts with their labels bound, arrows bound to both ends, and a legend."""
    lay = layout(d, _HAND)
    rng = random.Random(7)
    els: list[dict] = []
    count = [0]

    def el(type_, x, y, w, h, **kw):
        count[0] += 1
        e = {"id": f"{type_[:3]}{count[0]}", "type": type_, "x": x, "y": y, "width": w, "height": h, "angle": 0,
             "strokeColor": _INK, "backgroundColor": "transparent", "fillStyle": "solid", "strokeWidth": 2,
             "strokeStyle": "solid", "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None, "roundness": None,
             "seed": rng.randint(1, 2 ** 31), "version": 1, "versionNonce": rng.randint(1, 2 ** 31), "isDeleted": False,
             "boundElements": [], "updated": 1, "link": None, "locked": False}
        e.update(kw)
        els.append(e)
        return e

    def text(x, y, s, size, color=_INK, container=None, align="left"):
        lines = s.split("\n")
        w = max(_HAND.width(ln, size) for ln in lines)
        h = len(lines) * size * 1.25
        return el("text", x - (w / 2 if container else 0), y - (h / 2 if container else 0), w, h, strokeColor=color,
                  text=s, originalText=s, fontSize=size, fontFamily=1, textAlign=align,
                  verticalAlign="middle" if container else "top", containerId=container, lineHeight=1.25,
                  baseline=int(h - size * 0.25), autoResize=True)

    shapes = {}
    by_id = {n["id"]: n for n in d["nodes"]}
    for nb in lay["nodes"]:
        kind = by_id[nb["id"]]["kind"]
        # Excalidraw has no cylinder: a store is a rounded box in its own colour
        shape = el("rectangle", nb["x"], nb["y"], nb["w"], nb["h"], backgroundColor=_FILL[kind],
                   strokeStyle="dashed" if kind == "external" else "solid",
                   roundness={"type": 3} if kind in ("interface", "store") else None)
        t = text(nb["x"] + nb["w"] / 2, nb["y"] + nb["h"] / 2, "\n".join(nb["lines"]), _HAND.size,
                 container=shape["id"], align="center")
        shape["boundElements"].append({"type": "text", "id": t["id"]})
        shapes[nb["id"]] = shape
    for eb in lay["edges"]:
        pts = []
        for p, q in pairwise(eb["points"]):  # sample each curve: Excalidraw smooths through the points
            pts += [_bezier(p, q, t) for t in ((0, 0.33, 0.67) if not pts else (0.33, 0.67))] + [tuple(q)]
        x0, y0 = pts[0]
        rel = [[round(px - x0, 1), round(py - y0, 1)] for px, py in pts]
        xs, ys = [p[0] for p in rel], [p[1] for p in rel]
        a, b = shapes[eb["from"]], shapes[eb["to"]]
        arrow = el("arrow", round(x0, 1), round(y0, 1), max(xs) - min(xs), max(ys) - min(ys), points=rel,
                   strokeColor=_MUTED, roundness={"type": 2}, startArrowhead=None, endArrowhead="arrow",
                   lastCommittedPoint=None, elbowed=False,
                   startBinding={"elementId": a["id"], "focus": 0, "gap": 2},
                   endBinding={"elementId": b["id"], "focus": 0, "gap": 2})
        a["boundElements"].append({"type": "arrow", "id": arrow["id"]})
        b["boundElements"].append({"type": "arrow", "id": arrow["id"]})
        if eb["label"]:
            t = text(eb["label"]["x"], eb["label"]["y"], "\n".join(eb["label"]["lines"]), round(_HAND.label),
                     color=_MUTED, container=arrow["id"], align="center")
            arrow["boundElements"].append({"type": "text", "id": t["id"]})
    if title:
        text(0, -64, title, 24)
    lx, ly = 0, lay["height"] + 24
    for kind in KINDS:
        if any(n["kind"] == kind for n in d["nodes"]):
            el("rectangle", lx, ly, 34, 22, backgroundColor=_FILL[kind], strokeStyle="dashed" if kind == "external" else "solid",
               roundness={"type": 3} if kind in ("interface", "store") else None)
            t = text(lx + 44, ly + 1, _KIND_NAME[kind], 15, color=_MUTED)
            lx += 44 + t["width"] + 32
    return {"type": "excalidraw", "version": 2, "source": "https://interlatch.com", "elements": els,
            "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"}, "files": {}}


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
