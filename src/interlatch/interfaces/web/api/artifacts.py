"""What sessions made: the listing, one artifact's file, and opening or revealing it."""

from __future__ import annotations

from ....projects.labels import project_labels
from ..context import Context


def _artifacts(ctx: Context, **filters) -> tuple[list[dict], dict[str, int]]:
    from ....artifacts.queries import query
    from ....core.machine import local_machine_id

    return query(ctx.conn, local_machine=local_machine_id(ctx.cfg), **filters)


def _artifact_summary(ctx: Context, project: str) -> dict:
    items, counts = _artifacts(ctx, project=project)
    return {"total": len(items), "counts": counts, "recent": items[:8]}


def artifacts(ctx: Context, q: dict) -> dict:
    """The artifacts gallery: what sessions made, newest first, filtered by kind, project, agent, words and status."""
    items, counts = _artifacts(ctx, kind=q.get("kind") or None, project=q.get("project") or None,
                                    agent=q.get("agent") or None, q=(q.get("q") or "").strip() or None)
    if q.get("hide_gone") == "1":
        items = [i for i in items if i["status"] != "gone"]
    limit, offset = min(int(q.get("limit") or 100), 500), int(q.get("offset") or 0)
    projects = [dict(r) for r in ctx.conn.execute(
        "SELECT s.project_path, MAX(s.project_name) project_name, COUNT(DISTINCT a.key) n FROM artifacts a "
        "JOIN sessions s ON s.id = a.session_id GROUP BY s.project_path ORDER BY n DESC")]
    labels = project_labels(ctx.conn)
    for p in projects:
        p["label"] = labels.get(p["project_path"], p["project_name"])
    return {"total": len(items), "counts": counts, "items": items[offset:offset + limit], "projects": projects}


def artifact_open(ctx: Context, artifact_id: int) -> dict | None:
    from ....artifacts.files import open_file
    from ....core.machine import local_machine_id

    return open_file(ctx.conn, artifact_id, local_machine_id(ctx.cfg))


def artifact_reveal(ctx: Context, artifact_id: int, how: str) -> str | None:
    from ....artifacts.files import reveal
    from ....core.machine import local_machine_id

    return reveal(ctx.conn, artifact_id, how, local_machine_id(ctx.cfg))


def artifact_file(ctx: Context, artifact_id: int) -> tuple[bytes, str] | None:
    from ....artifacts.queries import preview_file
    from ....core.machine import local_machine_id

    return preview_file(ctx.conn, artifact_id, local_machine_id(ctx.cfg), cache_dir=ctx.cfg.home / "thumbs")
