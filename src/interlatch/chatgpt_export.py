"""Parse ChatGPT data export conversations (chatgpt.com › Settings › Data controls › Export data).

Each conversation in conversations.json is a tree (`mapping`: node id -> message, parent, children), because edits
and regenerated answers branch it. The branch ChatGPT shows ends at `current_node`; only that branch is read.
Messages are OpenAI-style: author role user / assistant / tool / system, content with a `content_type` (text,
multimodal_text, code, execution_output, thoughts, ...), and an assistant message addressed to a tool (`recipient`
other than "all") is a tool call whose output follows as a tool message.
"""

from __future__ import annotations

from .copilot_parser import _Builder, finish_session
from .parser import ParsedSession
from .util import parse_ts, safe_text, to_iso

CHATGPT_PARSER_VERSION = 1
AGENT = "chatgpt"
SOURCE = "chatgpt-export"
_SKIP = {"user_editable_context", "model_editable_context", "reasoning_recap", "system_error"}


def _iso(value) -> str | None:
    return to_iso(parse_ts(value))


def is_chatgpt(conversations: list[dict]) -> bool:
    return any("mapping" in c for c in conversations[:5])


def conversation_id(conv: dict) -> str | None:
    return conv.get("conversation_id") or conv.get("id")


def conversation_signature(conv: dict) -> str:
    return f"{conv.get('update_time')}|{len(conv.get('mapping') or {})}|{conv.get('current_node')}|v{CHATGPT_PARSER_VERSION}"


def thread(conv: dict) -> list[dict]:
    """The messages of the branch ChatGPT displays, oldest first."""
    mapping = conv.get("mapping") or {}
    node = conv.get("current_node")
    if node not in mapping:  # no current node recorded: the newest leaf
        leaves = [k for k, v in mapping.items() if isinstance(v, dict) and not v.get("children")]
        node = max(leaves, key=lambda k: ((mapping[k].get("message") or {}).get("create_time") or 0), default=None)
    out, seen = [], set()
    while node in mapping and node not in seen:
        seen.add(node)
        if isinstance(mapping[node].get("message"), dict):
            out.append(mapping[node]["message"])
        node = mapping[node].get("parent")
    return out[::-1]


def _text(content: dict) -> tuple[str, int]:
    """A message's text and how many images it carries."""
    if isinstance(content.get("text"), str):  # code, execution_output, tether_quote
        return content["text"].strip(), 0
    texts, images = [], 0
    for part in content.get("parts") or []:
        if isinstance(part, str):
            texts.append(part)
        elif isinstance(part, dict):
            if part.get("content_type") == "image_asset_pointer":
                images += 1
            elif isinstance(part.get("text"), str):
                texts.append(part["text"])
    if not texts and isinstance(content.get("result"), str):  # browsing output
        texts.append(content["result"])
    return "\n".join(t for t in texts if t).strip(), images


def _thoughts(content: dict) -> str:
    return "\n\n".join(t.get("content") or t.get("summary") or "" for t in content.get("thoughts") or []
                       if isinstance(t, dict)).strip()


def parse_conversation(conv: dict, projects: dict | None = None) -> ParsedSession | None:
    msgs = [m for m in thread(conv) if not (m.get("metadata") or {}).get("is_visually_hidden_from_conversation")]
    if not any((m.get("author") or {}).get("role") == "user" and any(_text(m.get("content") or {})) for m in msgs):
        return None  # an empty chat
    ps = ParsedSession(id=conversation_id(conv))
    ps.project_path = "chatgpt.com"
    ps.entrypoint = "chatgpt"
    ps.custom_title = safe_text(conv.get("title") or "") or None
    b = _Builder(ps)
    b.stamps += [t for t in (_iso(conv.get("create_time")), _iso(conv.get("update_time"))) if t]
    calls: list[tuple] = []  # tool calls waiting for their output: (id, recipient, ts, args)
    for m in msgs:
        role = (m.get("author") or {}).get("role")
        content = m.get("content") or {}
        kind = content.get("content_type")
        meta = m.get("metadata") or {}
        ts = _iso(m.get("create_time"))
        if meta.get("model_slug"):
            ps.models[meta["model_slug"]] += 1
        if role == "system" or kind in _SKIP:
            continue
        text, images = _text(content)
        if role == "user":
            ps.n_images += images
            notes = [f"[attached {a.get('name') or 'a file'}]" for a in meta.get("attachments") or [] if isinstance(a, dict)]
            if images:
                notes.append(f"[{images} image{'s' if images > 1 else ''}]")
            b.prompt(ts, "\n\n".join([text, *notes]).strip())
        elif role == "assistant" and kind == "thoughts":
            if _thoughts(content):
                b.event(ts, "assistant", "thinking", _thoughts(content))
        elif role == "assistant" and m.get("recipient") not in (None, "all"):
            calls.append((m.get("id"), m["recipient"], ts, {"code" if kind == "code" else "input": text} if text else {}))
        elif role == "assistant":
            if text:
                b.event(ts, "assistant", "text", text)
        elif role == "tool":
            name = (m.get("author") or {}).get("name") or "tool"
            call = next((c for c in calls if c[1] == name or name.startswith(c[1].split(".")[0])), calls[0] if calls else None)
            status = (meta.get("aggregate_result") or {}).get("status")
            result = text or f"[{kind or 'output'}]"
            if call:
                calls.remove(call)
                b.tool(call[2], call[0], call[1], call[3], result=result, is_error=status not in (None, "success"))
            else:  # output of a step ChatGPT did not show as a call (browsing, image generation)
                b.tool(ts, m.get("id"), name, {}, result=result)
    for call in calls:  # calls whose output is not in the export
        b.tool(call[2], call[0], call[1], call[3])
    if conv.get("default_model_slug") and not ps.models:
        ps.models[conv["default_model_slug"]] += 1
    return finish_session(ps, b.stamps)
