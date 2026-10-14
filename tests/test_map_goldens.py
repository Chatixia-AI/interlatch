"""Safety net: the exact output of systems.build (the Systems map) and the architecture sketch's layout, with its
Excalidraw and Mermaid exports) on fixed fixtures, compared to tests/goldens/systems.json and tests/goldens/diagram.json.

Splitting or tidying systems.py or the diagram modules must leave both unchanged. For a deliberate change, rerun with
UPDATE_GOLDENS=1 and review the diff.
"""

from __future__ import annotations

from pathlib import Path

from interlatch.projects import systems
from interlatch.core.diagrams import normalize_diagram, to_mermaid
from interlatch.interfaces.exports.diagrams import layout, to_excalidraw

from golden import assert_golden
from test_diagram import SAMPLE, edge, node
from test_systems import world  # noqa: F401  (the fixture: two repos, a worktree and a folder holding both)


def _scrub(value, root: Path):
    """The test's temporary folder as <TMP>, with forward slashes, and floats rounded: the same on every machine."""
    spellings = sorted({str(root), str(root.resolve()), root.as_posix(), root.resolve().as_posix()}, key=len, reverse=True)

    def fix(v):
        if isinstance(v, str):
            for s in spellings:
                if s in v:
                    v = v.replace(s, "<TMP>")
            return v.replace("\\", "/") if "<TMP>" in v else v
        if isinstance(v, dict):
            return {fix(k): fix(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [fix(x) for x in v]
        if isinstance(v, float):
            return round(v, 6)
        return v

    return fix(value)


def test_systems_build_matches_the_golden(world, tmp_path):  # noqa: F811
    data = {"with_manifests": systems.build(world["conn"], world["cfg"]),
            "without_manifests": systems.build(world["conn"], world["cfg"], read_manifests_=False)}
    for built in data.values():
        assert isinstance(built.pop("built_s"), float)  # how long it took: not output
    data = _scrub(data, tmp_path)
    assert_golden("systems.json", data)
    assert {s["label"] for s in data["with_manifests"]["systems"]} >= {"shop", "worker"}


CJK = {
    "nodes": [node("ingest", "interface"), {**node("pipe"), "label": "データ基盤の取り込みパイプラインとダッシュボード"},
              {**node("store", "store"), "label": "A rather long store name that has to wrap over several lines"},
              node("api", "external")],
    "edges": [edge("ingest", "pipe", label="バッチ"), edge("pipe", "store", label="writes rows"), edge("pipe", "api"),
              edge("api", "store", label="reads back what was written earlier today")],
}


def test_diagram_layout_and_exports_match_the_golden():
    data = {}
    for name, raw in (("sample", SAMPLE), ("cjk_and_long_labels", CJK)):
        d = normalize_diagram(raw)
        data[name] = {"normalized": d, "layout": layout(d), "excalidraw": to_excalidraw(d, title=name),
                      "mermaid": to_mermaid(d)}
    assert_golden("diagram.json", _scrub(data, Path("/nonexistent")))
