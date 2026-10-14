"""Where a Claude Code session's files are: its main transcript's folder of subagent transcripts."""

from __future__ import annotations

from pathlib import Path


def session_dir_for(main_path: Path) -> Path:
    name = main_path.name
    for suffix in (".jsonl.gz", ".jsonl"):
        if name.endswith(suffix):
            return main_path.with_name(name[: -len(suffix)])
    return main_path.with_suffix("")


def subagent_files(session_dir: Path) -> list[Path]:
    if not session_dir.is_dir():
        return []
    sub = session_dir / "subagents"
    if not sub.is_dir():
        return []
    found = [p for p in sub.rglob("agent-*.jsonl*") if p.name.endswith((".jsonl", ".jsonl.gz"))]
    return sorted(found)


def peek_cwd(proj: Path) -> str | None:
    """The working directory recorded in the first lines of a project's transcripts (exact, unlike the folder name)."""
    import json

    for main in sorted(proj.glob("*.jsonl"))[:3]:
        try:
            with open(main, errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > 60:
                        break
                    if '"cwd"' not in line:
                        continue
                    try:
                        cwd = json.loads(line).get("cwd")
                    except ValueError:
                        continue
                    if cwd:
                        return cwd
        except OSError:
            continue
    return None


def decode_project_dir(name: str) -> str:
    """Best-effort inverse of Claude Code's cwd -> folder-name encoding (lossy: '/' and '.' become '-')."""
    tokens = name[1:].split("-") if name.startswith("-") else name.split("-")
    path = Path("/")
    i = 0
    while i < len(tokens):
        matched = False
        for j in range(len(tokens), i, -1):
            for sep in ("-", ".", "_", " "):
                cand = sep.join(tokens[i:j])
                if not cand:
                    continue
                if (path / cand).exists():
                    path, i, matched = path / cand, j, True
                    break
                if tokens[i] == "" and j > i + 1 and (path / ("." + "-".join(tokens[i + 1 : j]))).exists():
                    path, i, matched = path / ("." + "-".join(tokens[i + 1 : j])), j, True
                    break
            if matched:
                break
        if not matched:
            return str(path / "/".join(t for t in tokens[i:] if t))
    return str(path)
