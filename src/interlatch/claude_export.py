"""Parse claude.ai data export conversations (claude.ai › Settings › Privacy › Export data).

Each conversation in conversations.json has `chat_messages` (sender human / assistant) whose `content` blocks
hold text, thinking, tool use and tool results, plus attached files; projects.json names the claude.ai projects.
Importing is in chat_import.py, shared with ChatGPT exports.
"""

from __future__ import annotations

import json
from collections import Counter

from . import artifacts
from .copilot_parser import _Builder, finish_session
from .parser import ParsedSession
from .util import parse_ts, safe_text, to_iso

CLAUDE_AI_PARSER_VERSION = 3  # 3: artifacts (claude.ai artifacts, files presented); 2: only the branch shown, pasted images
AGENT = "claude-ai"
SOURCE = "claude-ai-export"


def _iso(value) -> str | None:
    return to_iso(parse_ts(value))


def is_claude(conversations: list[dict]) -> bool:
    return any("chat_messages" in c for c in conversations[:5])


def conversation_id(conv: dict) -> str | None:
    return conv.get("uuid")


def project_names(raw: bytes | None) -> dict[str, str]:
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return {}
    return {p["uuid"]: p.get("name") or "" for p in data if isinstance(p, dict) and p.get("uuid")} if isinstance(data, list) else {}


def conversation_signature(conv: dict) -> str:
    msgs = conv.get("chat_messages") or []
    last = msgs[-1].get("uuid") if msgs and isinstance(msgs[-1], dict) else None
    return f"{conv.get('updated_at')}|{len(msgs)}|{last}|v{CLAUDE_AI_PARSER_VERSION}"


def _blocks(msg: dict) -> list[dict]:
    content = msg.get("content")
    if isinstance(content, list) and any(isinstance(b, dict) for b in content):
        return [b for b in content if isinstance(b, dict)]
    return [{"type": "text", "text": msg.get("text") or ""}]


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return content if isinstance(content, str) else ""


def thread(msgs: list[dict]) -> list[dict]:
    """The branch claude.ai shows. Editing a prompt or retrying an answer forks the chat (messages point to their
    parent), and the export has every branch: keep the chain that ends at the newest message."""
    by_id = {m.get("uuid"): m for m in msgs}
    children = Counter(m.get("parent_message_uuid") for m in msgs if m.get("parent_message_uuid") in by_id)
    if not children or max(children.values()) < 2:
        return msgs  # no forks (or no parent links, as in older exports)
    node, chain, seen = max(msgs, key=lambda m: m.get("created_at") or ""), [], set()
    while node is not None and node.get("uuid") not in seen:
        seen.add(node.get("uuid"))
        chain.append(node)
        node = by_id.get(node.get("parent_message_uuid"))
    return chain[::-1]


def parse_conversation(conv: dict, projects: dict[str, str]) -> ParsedSession | None:
    msgs = thread([m for m in conv.get("chat_messages") or [] if isinstance(m, dict)])
    if not any(m.get("sender") == "human" for m in msgs):
        return None  # an empty chat
    ps = ParsedSession(id=conv["uuid"])
    project = conv.get("project") if isinstance(conv.get("project"), dict) else {}
    name = project.get("name") or projects.get(conv.get("project_uuid") or project.get("uuid") or "")
    ps.project_path = f"claude.ai/{name}" if name else "claude.ai"
    ps.entrypoint = "claude.ai"
    ps.custom_title = safe_text(conv.get("name") or "") or None
    b = _Builder(ps, where="claude.ai")
    b.stamps += [t for t in (_iso(conv.get("created_at")), _iso(conv.get("updated_at"))) if t]
    for m in msgs:
        ts = _iso(m.get("created_at"))
        blocks = _blocks(m)
        if m.get("sender") == "human":
            text = "\n\n".join(bl.get("text") or "" for bl in blocks if bl.get("type") == "text").strip()
            notes = [f"[attached {a.get('file_name') or 'a file'}, {len(a.get('extracted_content') or ''):,} chars]"
                     for a in m.get("attachments") or [] if isinstance(a, dict)]
            files = [f for f in m.get("files") or m.get("files_v2") or [] if isinstance(f, dict)]
            ps.n_images += sum(1 for f in files if f.get("file_kind") in (None, "image"))
            notes += [f"[file {f.get('file_name') or 'image'}]" for f in files]
            known = {f.get("file_uuid") for f in files}
            pasted = sum(1 for bl in blocks if bl.get("type") == "image" and (not bl.get("file_uuid") or bl["file_uuid"] not in known))
            if pasted:  # images in the prompt that are not listed as files
                ps.n_images += pasted
                notes.append(f"[{pasted} image{'s' if pasted > 1 else ''}]")
            b.prompt(ts, "\n\n".join([text, *notes]).strip())
            continue
        results = [bl for bl in blocks if bl.get("type") == "tool_result"]
        for bl in blocks:
            bts = _iso(bl.get("start_timestamp")) or ts
            kind = bl.get("type")
            if kind == "thinking" and (bl.get("thinking") or "").strip():
                b.event(bts, "assistant", "thinking", bl["thinking"])
            elif kind == "text" and (bl.get("text") or "").strip():
                b.event(bts, "assistant", "text", bl["text"])
            elif kind == "tool_use":
                match = next((r for r in results if bl.get("id") and r.get("tool_use_id") == bl.get("id")), None) \
                    or next((r for r in results if r.get("name") == bl.get("name")), None)
                if match:
                    results.remove(match)
                inp = bl.get("input") if isinstance(bl.get("input"), dict) else {}
                b.tool(bts, bl.get("id"), bl.get("name") or "tool", inp, result=_result_text(match) if match else None,
                       is_error=bool(match and match.get("is_error")))
                if bl.get("name") == "artifacts":
                    artifacts.claude_ai_artifact(ps, inp, ts=bts, tool_use_id=bl.get("id"))
                elif bl.get("name") == "present_files" and match and not match.get("is_error"):
                    artifacts.claude_ai_presented(ps, match.get("content"), ts=bts, tool_use_id=bl.get("id"))
    if conv.get("model"):
        ps.models[conv["model"]] += 1
    return finish_session(ps, b.stamps)
