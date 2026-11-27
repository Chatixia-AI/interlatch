"""Parse the Hark chats the browser extension reads on hark.com (browser_extension/capture.js).

Hark keeps its chats on its servers; the Mac app (Hark.app) is a window onto hark.com and stores none of them. Hark
has no data export either, so the extension is the only way in. It reads the site's own endpoints: GET
/api/conversations lists the chats (one `main` chat that never ends, and one per Hark project), and GET
/api/messages/history pages back through a chat's messages, newest first.

The main chat runs for months, so the extension sends each chat one day at a time (local time): an `id` of
<chat id>_<YYYY-MM-DD>, the day's messages oldest first, and the work Hark dispatched that day (`panels`: a browser
run, with its narration and steps). It keeps only the fields read here: never the sign-in screens Hark asks you to
fill in (its `asks`), screenshots or live-view links.

A message is user or assistant. An assistant message can carry `thought` (shown as thinking), `toolCalls` (a tool
Hark used: `toolName`, `input`, `status`, `dispatchId`), and be a scheduled check-in (`task_name`, `brief`) that no
prompt asked for.
"""

from __future__ import annotations

from .builder import SessionBuilder, finish_session, ms_to_iso
from ..core.sessions import ParsedSession
from ..core.util import safe_text

HARK_PARSER_VERSION = 1
AGENT = "hark"
SOURCE = "hark-capture"
SITE = "hark.com"
_FAILED = {"failed", "error", "errored", "cancelled", "canceled", "timed_out", "refused"}


def is_hark(conversations: list[dict]) -> bool:
    return any(isinstance(c, dict) and c.get("hark") == 1 for c in conversations[:5])


def conversation_id(conv: dict) -> str | None:
    sid = conv.get("id")
    return sid if isinstance(sid, str) and sid else None


def conversation_signature(conv: dict) -> str:
    return (f"{conv.get('updated')}|{len(conv.get('messages') or [])}|{len(conv.get('panels') or [])}"
            f"|v{HARK_PARSER_VERSION}")


def _ms(value) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _panel_result(panel: dict) -> str:
    """What a dispatched run reported: its narration, then each step with its status."""
    lines = [safe_text(panel.get("latestNarration") or panel.get("narrative") or "").strip()]
    for step in panel.get("phases") or []:
        if isinstance(step, dict) and step.get("title"):
            lines.append(f"- {safe_text(step['title'])} ({step.get('status') or '?'})")
    return "\n".join(x for x in lines if x)


def parse_conversation(conv: dict, projects: dict | None = None) -> ParsedSession | None:
    messages = [m for m in conv.get("messages") or [] if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if not messages:
        return None
    chat = conv.get("conversation") if isinstance(conv.get("conversation"), dict) else {}
    ps = ParsedSession(id=conversation_id(conv))
    title = safe_text(chat.get("title") or "").strip()
    ps.project_path = f"{SITE}/{title}" if chat.get("kind") == "project" and title else SITE
    ps.entrypoint = "hark"
    ps.custom_title = f"{title} · {conv.get('day')}" if title and conv.get("day") else title or None
    b = SessionBuilder(ps)
    panels = {p["dispatchId"]: p for p in conv.get("panels") or [] if isinstance(p, dict) and p.get("dispatchId")}
    used: set[str] = set()
    for m in sorted(messages, key=lambda m: _ms(m.get("timestamp")) or 0):  # stable: same-time messages keep their order
        ts = ms_to_iso(_ms(m.get("timestamp")))
        text = safe_text(m.get("content") if isinstance(m.get("content"), str) else "").strip()
        if m["role"] == "user":
            b.prompt(ts, text)
            continue
        if isinstance(m.get("thought"), str) and m["thought"].strip():
            b.event(ts, "assistant", "thinking", m["thought"].strip())
        if text:
            b.event(ts, "assistant", "text", text)
        for call in m.get("toolCalls") or []:
            if not isinstance(call, dict):
                continue
            dispatch = call.get("dispatchId")
            panel = panels.get(dispatch) if dispatch else None
            if panel:
                used.add(dispatch)
            start, end = _ms(call.get("dispatchedAt")), _ms(call.get("updatedAt"))
            status = (call.get("status") or (panel or {}).get("status") or "").lower()
            args = call.get("input") if isinstance(call.get("input"), dict) else {}
            if not args and panel and panel.get("narrative"):
                args = {"description": panel["narrative"]}
            b.tool(ms_to_iso(start) or ts, dispatch, call.get("toolName") or (panel or {}).get("kind") or "tool", args,
                   result=_panel_result(panel) if panel else None, is_error=status in _FAILED,
                   duration_ms=end - start if start and end and end >= start else None)
    for dispatch, panel in panels.items():  # work Hark dispatched with no message naming it
        if dispatch not in used:
            ts = ms_to_iso(_ms(panel.get("startedAt")))
            b.tool(ts, dispatch, panel.get("kind") or "tool",
                   {"description": panel["narrative"]} if panel.get("narrative") else {},
                   result=_panel_result(panel), is_error=(panel.get("status") or "").lower() in _FAILED)
    if not ps.n_prompts and not ps.events:
        return None
    return finish_session(ps, b.stamps)
