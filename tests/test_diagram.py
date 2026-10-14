"""Architecture sketch: what synthesis may draw, how it is laid out, and the Excalidraw and Mermaid exports."""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from interlatch.analysis.session import analyze_session
from interlatch.core.diagrams import MAX_NODES, normalize_diagram, to_mermaid
from interlatch.interfaces.exports.diagrams import Metrics, layout, to_excalidraw
from interlatch.knowledge.synthesize import GLOBAL, GLOBAL_SCHEMA, KB_SCHEMA, synthesize_project

from conftest import CWD, SID


def node(i, kind="component", sources=(1,)):
    return {"id": i, "label": f"Part {i}", "kind": kind, "note": "", "sources": list(sources)}


def edge(a, b, sources=(1,), label="calls"):
    return {"from": a, "to": b, "label": label, "sources": list(sources)}


SAMPLE = {
    "nodes": [node("cli", "interface"), node("worker"), node("db", "store"), node("llm", "external"), node("ui", "interface"),
              node("loose")],
    "edges": [edge("cli", "worker"), edge("worker", "db"), edge("worker", "llm"), edge("ui", "db"), edge("ui", "worker"),
              edge("db", "cli", label="a cycle")],
}


def test_normalize_keeps_only_what_cited_items_support():
    d = normalize_diagram({
        "nodes": [node("a", "bogus-kind"), node("b"), node("c"), node("lone"), {"label": ""}, "junk", node("a")],
        "edges": [edge("a", "b"), edge("a", "b", sources=(2,)), edge("b", "c", sources=()), edge("b", "c", sources=(99,)),
                  edge("a", "a"), edge("a", "nowhere"), edge("c", "a", sources=("2", "x"))],
    }, valid_ids={1, 2})
    assert [n["id"] for n in d["nodes"]] == ["a", "b", "c"]  # "lone" sits on no edge
    assert d["nodes"][0]["kind"] == "component"
    pairs = [(e["from"], e["to"]) for e in d["edges"]]
    assert pairs == [("a", "b"), ("c", "a")]  # unsourced, invented-source, self and dangling edges are gone
    assert d["edges"][0]["sources"] == [1, 2]  # the same connection twice pools its evidence
    assert d["edges"][1]["sources"] == [2]
    assert normalize_diagram({"nodes": [node("a")], "edges": []}) is None
    assert normalize_diagram("nope") is None


def test_normalize_caps_the_parts_at_the_best_connected():
    hub = [edge("hub", f"p{i}") for i in range(MAX_NODES + 4)]
    d = normalize_diagram({"nodes": [node("hub")] + [node(f"p{i}") for i in range(MAX_NODES + 4)], "edges": hub})
    assert len(d["nodes"]) <= MAX_NODES and any(n["id"] == "hub" for n in d["nodes"])
    ids = {n["id"] for n in d["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in d["edges"])


def test_layout_places_parts_without_overlap_and_routes_every_edge():
    d = normalize_diagram(SAMPLE)
    lay = layout(d)
    boxes = {n["id"]: n for n in lay["nodes"]}
    assert set(boxes) == {"cli", "worker", "db", "llm", "ui"}
    for n in lay["nodes"]:
        assert 0 <= n["x"] and n["x"] + n["w"] <= lay["width"] and 0 <= n["y"] and n["y"] + n["h"] <= lay["height"]
    items = list(boxes.values())
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            assert not (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]), (a, b)
    assert boxes["cli"]["x"] < boxes["worker"]["x"] < boxes["db"]["x"]  # flows left to right
    assert len(lay["edges"]) == len(d["edges"])
    for e, le in zip(d["edges"], lay["edges"], strict=True):
        start, end = le["points"][0], le["points"][-1]
        a, b = boxes[e["from"]], boxes[e["to"]]
        assert a["y"] <= start[1] <= a["y"] + a["h"] and b["y"] <= end[1] <= b["y"] + b["h"]  # ports on the right parts
        assert le["d"].startswith("M") and le["label"]["lines"] == [e["label"]]
    assert layout(d) == lay  # deterministic: the dashboard and the download agree


def test_layout_wraps_long_and_cjk_labels():
    m = Metrics()
    assert m.width("データ") == 3 * m.size and m.width("abc") < m.width("データ")
    lines = m.wrap("データ基盤の取り込みパイプラインとダッシュボード", m.size * 11)
    assert len(lines) >= 2 and all(m.width(ln) <= m.size * 11 for ln in lines)
    assert m.wrap("one two three four five six seven eight nine ten eleven twelve", 60, max_lines=2)[-1].endswith("…")


def test_excalidraw_scene_binds_labels_and_arrows():
    d = normalize_diagram(SAMPLE)
    scene = to_excalidraw(d, "demo: architecture")
    json.dumps(scene)
    assert scene["type"] == "excalidraw" and scene["version"] == 2
    els = {e["id"]: e for e in scene["elements"]}
    assert len(els) == len(scene["elements"])
    for e in scene["elements"]:
        if e["type"] == "text" and e["containerId"]:
            assert {"type": "text", "id": e["id"]} in els[e["containerId"]]["boundElements"]
        if e["type"] == "arrow":
            for key in ("startBinding", "endBinding"):
                target = els[e[key]["elementId"]]
                assert {"type": "arrow", "id": e["id"]} in target["boundElements"]
            assert e["points"][0] == [0, 0] and len(e["points"]) >= 4  # a sampled curve
    labels = {e["text"] for e in scene["elements"] if e["type"] == "text"}
    assert {"Part cli", "Part db", "demo: architecture", "calls", "a cycle"} <= labels
    assert sum(e["type"] == "arrow" for e in scene["elements"]) == len(d["edges"])


def test_mermaid_escapes_and_shapes():
    d = normalize_diagram({"nodes": [{"id": "a", "label": 'say "hi" <b>', "kind": "interface", "note": "", "sources": [1]},
                                     node("b", "store")], "edges": [edge("a", "b", label='"q"')]})
    out = to_mermaid(d)
    assert out.splitlines()[0] == "flowchart LR"
    assert 'n0(["say #quot;hi#quot; #lt;b#gt;"])' in out and 'n1[("Part b")]' in out
    assert 'n0 -->|"#quot;q#quot;"| n1' in out


def test_only_project_knowledge_bases_draw_a_sketch():
    assert "diagram" in KB_SCHEMA["required"] and "diagram" in KB_SCHEMA["properties"]
    assert "diagram" not in GLOBAL_SCHEMA["required"] and "diagram" not in GLOBAL_SCHEMA["properties"]


def test_synthesis_stores_the_sketch_and_serves_it(synced, monkeypatch):
    from interlatch.interfaces.web.app import App
    from interlatch.interfaces.web.handler import make_handler

    monkeypatch.setenv("FAKE_CLAUDE_MODE", "diagram")
    cfg, conn = synced["cfg"], synced["conn"]
    analyze_session(conn, cfg, SID)
    data = synthesize_project(conn, cfg, CWD)
    assert [n["id"] for n in data["diagram"]["nodes"]] == ["api", "db"]  # the loose part is dropped
    assert [e["label"] for e in data["diagram"]["edges"]] == ["writes tokens"]  # so is the invented connection
    row = conn.execute("SELECT kb_json, markdown FROM project_kb WHERE project_path = ?", (CWD,)).fetchone()
    assert json.loads(row["kb_json"])["diagram"] == data["diagram"]
    assert "## Architecture" in row["markdown"] and '-->|"writes tokens"|' in row["markdown"]
    assert "diagram" not in synthesize_project(conn, cfg, GLOBAL)  # the reply drew one: the playbook drops it

    app = App(cfg)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    httpd.RequestHandlerClass = make_handler(app, httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/api/project?path={urllib.parse.quote(CWD)}", timeout=10) as r:
            proj = json.loads(r.read())
        assert proj["diagram"]["layout"]["nodes"] and len(proj["diagram"]["layout"]["edges"]) == 1
        with urllib.request.urlopen(f"{base}/api/diagram?path={urllib.parse.quote(CWD)}", timeout=10) as r:
            assert "attachment" in r.headers["Content-Disposition"] and ".excalidraw" in r.headers["Content-Disposition"]
            scene = json.loads(r.read())
        assert scene["type"] == "excalidraw" and any(e.get("text") == "Auth API" for e in scene["elements"])
        try:
            urllib.request.urlopen(f"{base}/api/diagram?path={urllib.parse.quote(GLOBAL)}", timeout=10)
            raise AssertionError("the playbook has no sketch to download")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        with urllib.request.urlopen(f"{base}/rough.js", timeout=10) as r:
            assert b"rough" in r.read(200)
    finally:
        httpd.shutdown()
