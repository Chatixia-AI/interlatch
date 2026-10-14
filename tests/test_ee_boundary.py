"""The MIT core must work without ee/ (Interlatch Enterprise License): no module in src/interlatch may import it."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "interlatch"


def test_core_never_imports_ee():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            offenders += [f"{path.relative_to(SRC)}: {n}" for n in names if n.split(".")[0] == "interlatch_ee"]
    assert not offenders, offenders
