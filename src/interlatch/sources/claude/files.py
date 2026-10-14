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
