"""The package's layers, enforced: which part of src/interlatch may import which (docs/development.md, Code layout).

    core  <-  storage, sources  <-  features  <-  application  <-  interfaces
                                    (ingestion, analysis, knowledge, projects, artifacts, sharing)
    runtime: installing and running Interlatch on this computer; it starts the dashboard only from the modules in
    COMPOSITION.

Every import counts, at any depth (a function-local import is still a dependency). A new import against these rules
fails here; so does an exception in ALLOWED that the code no longer needs, so the list only shrinks.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
FEATURES = {"ingestion", "analysis", "knowledge", "projects", "artifacts", "sharing"}
# a package may not import these (beyond what ALLOWED and COMPOSITION grant)
FORBIDDEN = {
    "core": {"storage", "sources", *FEATURES, "application", "interfaces", "runtime"},
    "storage": {"sources", *FEATURES, "application", "interfaces", "runtime"},
    "sources": {*FEATURES, "application", "interfaces", "runtime"},
    **{f: {"application", "interfaces"} for f in FEATURES},
    "application": {"interfaces"},
    "runtime": {"interfaces"},
}
# runtime modules that start the dashboard (or run beside it) and so compose it: the outer boundary, where the plan
# puts desktop and server composition although the files live under runtime/
COMPOSITION = {"interlatch.runtime.container", "interlatch.runtime.platforms.desktop",
               "interlatch.runtime.platforms.menubar", "interlatch.runtime.platforms.windows"}
# the one deliberate exception, and why
ALLOWED = {
    ("interlatch.storage.migrations", "interlatch.knowledge.ladder"):
        "schema 8's data migration derives each lesson's maturity stage with the ladder's own rules",
}
# entry points and the name before the rename: run from outside, never imported from inside
ENTRY = {"interlatch.cli", "interlatch.windows", "interlatch.__main__"}


def _modules() -> dict[str, Path]:
    out = {}
    for f in (SRC / "interlatch").rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        parts = list(f.relative_to(SRC).with_suffix("").parts)
        out[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = f
    return out


MODULES = _modules()


def _imports(module: str, path: Path, *, top_level_only: bool = False):
    """(imported module, line) for each import in `module`: `from pkg import mod` names the module itself."""
    pkg = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body if top_level_only else ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                base = ".".join(up + ([node.module] if node.module else []))
            for alias in node.names:
                full = f"{base}.{alias.name}"
                yield (full if full in MODULES else base), node.lineno
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno


def _layer(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 2 or (len(parts) == 2 and parts[1] in FORBIDDEN) else ""


def _edges():
    for module, path in MODULES.items():
        for target, line in _imports(module, path):
            if target.startswith("interlatch.") and target != module:
                yield module, target, line


def test_each_layer_imports_only_below_it():
    wrong, used = [], set()
    for module, target, line in _edges():
        layer, target_layer = _layer(module), _layer(target)
        if target_layer not in FORBIDDEN.get(layer, ()):
            continue
        if (module, target) in ALLOWED:
            used.add((module, target))
        elif not (layer == "runtime" and target_layer == "interfaces" and module in COMPOSITION):
            wrong.append(f"{module}:{line} imports {target} ({layer} may not import {target_layer})")
    assert not wrong, "\n".join(wrong)
    assert used == set(ALLOWED), f"no longer needed, remove from ALLOWED: {set(ALLOWED) - used}"


def test_runner_contracts_dont_know_the_runners():
    """analysis.runners.base is what every runner implements; only the factory knows the concrete runners."""
    runners = "interlatch.analysis.runners"
    wrong = []
    for module, target, line in _edges():
        if module == f"{runners}.base" and target.startswith(f"{runners}.") and target != f"{runners}.replies":
            wrong.append(f"{module}:{line} imports {target}")
        if module.startswith(f"{runners}.") and module != f"{runners}.factory" and target == f"{runners}.factory":
            wrong.append(f"{module}:{line} imports {target}")
    assert not wrong, "\n".join(wrong)


def test_nothing_inside_imports_an_entry_point_or_the_old_name():
    wrong = [f"{module}:{line} imports {target}" for module, target, line in _edges() if target in ENTRY
             and module not in ENTRY]  # python -m interlatch runs cli.main, as the console script does
    for path in sorted((SRC / "interlatch").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else []
            wrong += [f"{path.relative_to(SRC)}: imports {n}" for n in names if n.split(".")[0] == "chronicle"]
    assert not wrong, "\n".join(wrong)


def test_no_module_level_import_cycles():
    graph = {m: {t for t, _ in _imports(m, p, top_level_only=True) if t in MODULES and t != m} for m, p in MODULES.items()}
    cycles, index, low, stack, on_stack, counter = [], {}, {}, [], set(), [0]
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10_000))

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            group = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                group.append(w)
                if w == v:
                    break
            if len(group) > 1:
                cycles.append(sorted(group))

    for v in graph:
        if v not in index:
            visit(v)
    assert not cycles, cycles


def test_importing_interlatch_loads_no_optional_extra():
    """The base install runs the command, the dashboard and the parsers: pywebview, PyObjC and psycopg stay optional."""
    code = ("import sys, interlatch, interlatch.cli, interlatch.interfaces.cli.parser, interlatch.interfaces.web.handler, "
            "interlatch.sources.claude.parser, interlatch.interfaces.mcp, chronicle; "
            "interlatch.interfaces.cli.parser.build_parser(); "
            "print(sorted(m for m in ('webview', 'objc', 'AppKit', 'psycopg') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
