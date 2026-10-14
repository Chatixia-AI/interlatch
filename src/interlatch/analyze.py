"""Per-session analysis: overview + knowledge extraction by the analysis model (map-reduce for long sessions)."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import timedelta

from . import ladder, news
from .cases import LESSON_KINDS, VISUAL_SCHEMA, lesson_of
from .config import Config
from .digest import build_digest
from .ingest import best_title
from .learning import TOPICS, normalize_interests, store_interests
from .llm import (BudgetExceededError, LLMError, LLMResult, Runner, SleepInterruptedError, UsageLimitError, make_runner,
                  written_in)
from .redact import redact
from .util import dumps, fingerprint, to_iso, utcnow, utcnow_iso

log = logging.getLogger("interlatch.analyze")

PROMPT_VERSION = 3  # 2: fixes, gotchas and decisions carry a case file; 3: lessons carry a principle, checks, topics, a diagram

KNOWLEDGE_KINDS = {
    "fix": "a bug or failure with its root cause and the fix",
    "gotcha": "a pitfall or surprising behavior, and how to avoid it",
    "learning": "an insight about a technology, library, API or this codebase",
    "decision": "a design or architecture choice and its rationale",
    "pattern": "a reusable approach, technique or code snippet that worked",
    "command": "a useful command or invocation and when to use it",
    "fact": "a fact about the project: layout, config, endpoints, data, deployment",
    "preference": "how the developer wants the coding agent to work or communicate",
    "reference": "a pointer to an external resource (URL, doc, dashboard, ticket)",
    "todo": "a follow-up task worth tracking",
}
WORK_TYPES = [
    "feature", "bugfix", "debugging", "refactor", "research", "exploration", "ops", "deploy", "config",
    "docs", "testing", "review", "planning", "data", "design", "learning", "other",
]
OUTCOMES = ["completed", "partial", "blocked", "abandoned", "exploratory", "unclear"]
FRICTION_KINDS = ["tool_error", "environment", "misunderstanding", "rework", "permissions", "performance", "external", "other"]
CASE_KINDS = ("fix", "gotcha", "decision")  # shown to people as case files: the scene and a question before the answer
# knowledge.case_json also holds lesson material without a case file (a learning's principle, say): a case file has a question
CASE_FILE_SQL = "json_extract(case_json, '$.question') IS NOT NULL"
MAX_LEADS = 4

RULED_OUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lead", "why"],
    "properties": {
        "lead": {"type": "string", "description": "The wrong lead, phrased as an answer to the question, at most 12 words"},
        "why": {"type": "string", "description": "What showed it wrong, one sentence"},
    },
}
KNOWLEDGE_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "title", "body", "tags", "scope", "confidence", "evidence", "scene", "question", "answer",
                 "ruled_out", "clues", "principle", "checklist", "topics", "visual"],
    "properties": {
        "kind": {
            "type": "string",
            "enum": list(KNOWLEDGE_KINDS),
            "description": "; ".join(f"{k}: {v}" for k, v in KNOWLEDGE_KINDS.items()),
        },
        "title": {"type": "string", "description": "Specific headline, at most ~90 characters"},
        "body": {"type": "string", "description": "Self-contained explanation (2-6 sentences, or a short snippet). Markdown allowed."},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "2-5 lowercase tags"},
        "scope": {"type": "string", "enum": ["project", "global"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence": {"type": "string", "description": "Where the session established this, e.g. 'prompt 4: tests passed after the fix'"},
        "scene": {"type": "string", "description": "fix, gotcha, decision: what was being done and what was seen, before "
                  "the answer was known (1-3 sentences, the error verbatim); a decision: the problem it settled. Never "
                  "gives the answer away. Empty for other kinds"},
        "question": {"type": "string", "description": "fix, gotcha, decision: the question the scene raises, as the "
                     "developer would have asked it then. Empty for other kinds"},
        "answer": {"type": "string", "description": "fix, gotcha, decision: the answer to the question, at most 12 "
                   "words (the cause or the call, not the fix). Empty for other kinds"},
        "ruled_out": {"type": "array", "items": RULED_OUT_SCHEMA, "description": "fix, gotcha, decision: leads the "
                      "session tried and found wrong (a decision: the options turned down), at most 4. Only dead ends "
                      "the transcript shows; empty when there were none or for other kinds"},
        "clues": {"type": "array", "items": {"type": "string"}, "description": "fix, gotcha: up to 6 facts the session "
                  "observed before the cause was known (errors, test results), in order. Never invented. Empty otherwise"},
        "principle": {"type": "string", "description": "fix, gotcha, decision, learning, pattern: the general idea this "
                      "is an instance of, stated so it applies beyond this project, and when it applies (1-2 sentences). "
                      "Empty when the session doesn't support one; never generic advice or the title again"},
        "checklist": {"type": "array", "items": {"type": "string"}, "description": "fix, gotcha, decision, learning, "
                      "pattern: up to 4 concrete checks for similar work next time, grounded in what the session found. "
                      "Empty otherwise"},
        "topics": {"type": "array", "items": {"type": "string", "enum": list(TOPICS)},
                   "description": "Up to 4 engineering areas this item actually teaches. Empty for other kinds"},
        "visual": VISUAL_SCHEMA,
    },
}
FRICTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "note"],
    "properties": {"kind": {"type": "string", "enum": FRICTION_KINDS}, "note": {"type": "string"}},
}
INTEREST_SCHEMA = {"type": "array", "items": {"type": "object", "additionalProperties": False,
                   "required": ["topic", "question"], "properties": {
                       "topic": {"type": "string", "enum": list(TOPICS)},
                       "question": {"type": "string", "description": "Verbatim quote of the USER's question or expressed curiosity; never assistant text."}}},
                   "description": "Only explicit curiosity, requests for explanation, comparison or understanding. Execution tasks alone are not learning interests. Empty if none."}
ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "goal", "outcome", "outcome_note", "work_types", "tags", "highlights",
                 "knowledge", "open_threads", "friction", "sentiment", "learning_interests"],
    "properties": {
        "title": {"type": "string", "description": "Specific title of what the session was about, at most ~70 characters"},
        "summary": {"type": "string", "description": "3-6 sentences: what was asked, what was done, how it ended"},
        "goal": {"type": "string", "description": "The developer's underlying goal, one sentence"},
        "outcome": {"type": "string", "enum": OUTCOMES},
        "outcome_note": {"type": "string", "description": "One sentence on the end state"},
        "work_types": {"type": "array", "items": {"type": "string", "enum": WORK_TYPES}},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "3-8 lowercase topic and technology tags"},
        "highlights": {"type": "array", "items": {"type": "string"}, "description": "Key accomplishments or findings, one line each"},
        "knowledge": {"type": "array", "items": KNOWLEDGE_ITEM_SCHEMA},
        "learning_interests": INTEREST_SCHEMA,
        "open_threads": {"type": "array", "items": {"type": "string"}, "description": "Unfinished work, follow-ups, open questions"},
        "friction": {"type": "array", "items": FRICTION_SCHEMA, "description": "What slowed the session down"},
        "sentiment": {"type": "string", "enum": ["positive", "neutral", "frustrated", "mixed", "unclear"],
                      "description": "The developer's apparent sentiment by the end"},
    },
}
CHUNK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "highlights", "knowledge", "open_threads", "friction", "tags", "learning_interests"],
    "properties": {
        "summary": {"type": "string", "description": "What happened in this part, 3-6 sentences"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "knowledge": {"type": "array", "items": KNOWLEDGE_ITEM_SCHEMA},
        "learning_interests": INTEREST_SCHEMA,
        "open_threads": {"type": "array", "items": {"type": "string"}},
        "friction": {"type": "array", "items": FRICTION_SCHEMA},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}
# The merge of a long session's parts names the part notes' knowledge items it keeps instead of writing them out
# again: retyping a few dozen lessons with their case files in one reply came back empty, and wiped the session's.
REDUCE_SCHEMA = {
    **ANALYSIS_SCHEMA,
    "required": [k if k != "knowledge" else "knowledge_ids" for k in ANALYSIS_SCHEMA["required"]],
    "properties": {**{k: v for k, v in ANALYSIS_SCHEMA["properties"].items() if k != "knowledge"},
                   "knowledge_ids": {"type": "array", "items": {"type": "string"},
                                     "description": "The ids of the part notes' knowledge items to keep, in order"}},
}

SYSTEM_PROMPT = """\
You are Interlatch, an analyst that turns AI coding-agent session transcripts (Claude Code, OpenAI Codex, GitHub Copilot, IBM Bob, \
Google Antigravity) into a durable record and reusable knowledge for the developer who ran them.

You receive metadata (including which agent ran the session) and a condensed transcript of one session: a \
developer (USER) working with an AI coding agent (CLAUDE, CODEX, COPILOT, BOB or ANTIGRAVITY). Tool calls appear as "→ Tool: summary", \
tool output excerpts as "⮑" and failed tool calls as "✗". Long outputs are truncated and secrets are redacted.

Produce two things:
1. An accurate overview: a specific title, what the developer wanted, what was done, and how it ended.
2. Knowledge items: what is worth remembering because it would save this developer time in a future session.

Good knowledge items are:
- Specific and non-obvious: root causes with their fixes, gotchas, design decisions with their reasons, \
facts about the project (where things live; how to run, test or deploy it; which service does what), commands \
or snippets that worked, and preferences the developer expressed about how the agent should work.
- Self-contained: someone who never saw this session must understand the item. Name the concrete files, \
commands, config keys, versions and error messages.
- Grounded: include only what the transcript supports. Confidence is "high" when the session verified it \
(tests passed, the developer confirmed), "medium" when likely but unverified, "low" for hypotheses.

Leave out generic programming advice, restatements of the task, and trivia that is quick to rediscover. \
A routine session may have few or no knowledge items; that is a correct answer.

The developer also reads fixes, gotchas and decisions as case files, which ask before they tell: the scene, then the \
question, and only then the answer. For those three kinds, fill in:
- scene: what was being done and what was seen first, before the cause was known (the symptom, the error message \
verbatim). For a decision, the problem it had to settle. The scene must not give the answer away.
- question: the question the scene raises, as the developer would have asked it then ("Why didn't the edit show up?").
- answer: the cause, or the call made, in at most 12 words. The explanation and the fix belong in the body.
- ruled_out: the leads the session actually tried and found wrong, each with what showed it wrong. Phrase each lead \
like the answer, also in at most 12 words, so it reads as an alternative to it and the right one doesn't stand out \
by its length. For a decision, the options weighed and turned down. Only dead ends the transcript shows: an invented \
one would teach something false. Leave it empty when there were none.
For every other kind, leave these four empty.

The developer also learns from fixes, gotchas, decisions, learnings and patterns, so for those five kinds fill in, \
only where the transcript supports it:
- principle: the general idea the item is an instance of, stated so it applies beyond this project, and when it \
applies ("A write that replaces the whole set must read the set when it is applied, not when it was planned"). Not \
generic advice and not the title again; leave it empty rather than stretch.
- checklist: up to 4 concrete checks for similar work next time.
- clues: for a fix or gotcha, the facts observed in order before the cause was known.
- topics: the engineering areas the item actually teaches.
- visual: a small diagram, only when the explanation describes a flow, parts that connect, or real alternatives: \
2-4 nodes named in the session's own terms, each with a one-sentence detail of its role, and up to 5 labeled edges. \
Use flow for request paths or steps, relationship for components, data entities or state ownership, comparison for \
alternatives weighed. Draw only what the explanation establishes, never a generic architecture from topic tags; \
otherwise return an empty visual (type flow, empty title, nodes and edges). Two boxes saying "problem" and "solution" \
are not a diagram.
When the session revised a finding or a decision, the title, answer and principle state where it ended up; the body \
says what it replaced.

Extract the concepts the USER is curious about: questions about why or how, follow-up explanations, comparisons, \
architecture tradeoffs, frontend/backend behavior, API contracts and data models. Quote the user's actual question \
in learning_interests and classify its topic. A delegated implementation task or an assistant's unsolicited \
explanation is not evidence of curiosity. Preserve substantive answers as learning or pattern items, even when no \
bug occurred. Connect the concept to the actual project and retain its explanation, not just the task's outcome.

Use scope "global" for items useful beyond this project (tools, languages, platforms, the developer's working \
preferences), otherwise "project". Write in English, but keep identifiers, error messages and quotes verbatim, \
whatever their language."""
WRITE_ENGLISH = "Write in English, but keep identifiers, error messages and quotes verbatim, whatever their language."


def system_prompt(cfg: Config) -> str:
    """SYSTEM_PROMPT in the language Interlatch writes in ([analysis] language)."""
    return written_in(cfg, SYSTEM_PROMPT, WRITE_ENGLISH, "Tags stay short lowercase English terms, so sessions group by "
                      "topic whichever language they were analyzed in.")


def _session_prompt(header: str, digest: str) -> str:
    return (
        f"<session_metadata>\n{header}\n</session_metadata>\n\n"
        f"<transcript>\n{digest}\n</transcript>\n\n"
        "Analyze this session."
    )


def _chunk_prompt(header: str, chunk: str, i: int, n: int) -> str:
    return (
        f"<session_metadata>\n{header}\n</session_metadata>\n\n"
        f"This is part {i} of {n} of a long session transcript. Extract notes for this part only; "
        "all parts will be merged afterwards.\n\n"
        f"<transcript_part index=\"{i}\" of=\"{n}\">\n{chunk}\n</transcript_part>"
    )


def _reduce_prompt(header: str, notes: list[dict], opening: str, closing: str) -> str:
    parts = "\n\n".join(f"[part {i}]\n{json.dumps(n, ensure_ascii=False, indent=1)}" for i, n in enumerate(notes, 1))
    return (
        f"<session_metadata>\n{header}\n</session_metadata>\n\n"
        f"The session was too long for one pass, so it was analyzed in {len(notes)} parts. "
        "Notes from each part, in order:\n\n"
        f"<part_notes>\n{parts}\n</part_notes>\n\n"
        f"<opening_excerpt>\n{opening}\n</opening_excerpt>\n\n"
        f"<closing_excerpt>\n{closing}\n</closing_excerpt>\n\n"
        "Write the final analysis of the whole session, and describe the end state from the last part. For "
        "knowledge, list in knowledge_ids the ids of the part notes' items to keep: of duplicates only the most "
        "specific, of a finding a later part revised only the later one, and none that later parts show to be wrong."
    )


def _kept(items: dict[str, dict], ids) -> list[dict]:
    """The part notes' knowledge items the merge kept, each once. All of them when it named none it was given: a
    reply that lost its list must not replace the session's lessons with nothing."""
    kept = [items[i] for i in dict.fromkeys(ids if isinstance(ids, list) else []) if isinstance(i, str) and i in items]
    if not kept and items:
        log.warning("the merge kept no knowledge item; keeping all %d from the parts", len(items))
    return kept or list(items.values())


def _str_list(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _leads(value) -> list[dict]:
    out = []
    for lead in value if isinstance(value, list) else []:
        if isinstance(lead, str):
            lead = {"lead": lead}
        if isinstance(lead, dict) and str(lead.get("lead") or "").strip():
            out.append({"lead": str(lead["lead"]).strip(), "why": str(lead.get("why") or "").strip()})
    return out[:MAX_LEADS]


def normalize_knowledge(items) -> list[dict]:
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        kind = str(item.get("kind") or "").lower().strip()
        kind = kind if kind in KNOWLEDGE_KINDS else "learning"
        case = kind in CASE_KINDS
        out.append({
            "kind": kind,
            "title": str(item["title"]).strip(),
            "body": str(item.get("body") or "").strip(),
            "tags": [t.lower() for t in _str_list(item.get("tags"))][:8],
            "scope": item.get("scope") if item.get("scope") in ("project", "global") else "project",
            "confidence": item.get("confidence") if item.get("confidence") in ("high", "medium", "low") else "medium",
            "evidence": str(item.get("evidence") or "").strip(),
            **{k: str(item.get(k) or "").strip() if case else "" for k in ("scene", "question", "answer")},
            "ruled_out": _leads(item.get("ruled_out")) if case else [],
            **(lesson_of(item) if kind in LESSON_KINDS else {}),
        })
    return out


def case_of(item: dict) -> dict | None:
    """A lesson's material, redacted: for a fix, gotcha or decision its case file (scene, question, answer, ruled_out)
    when the analysis gave one, and for any lesson kind the principle, checks, topics and diagram it supported (cases.py).
    `item` is an analysis item, or {"kind", "case": stored case_json}. None when there is nothing."""
    kind = item.get("kind")
    if kind not in LESSON_KINDS:
        return None
    raw = item["case"] if isinstance(item.get("case"), dict) else item
    out = {}
    scene, question, answer = (redact(raw[k]).strip() if isinstance(raw.get(k), str) else "" for k in ("scene", "question", "answer"))
    if kind in CASE_KINDS and scene and question and answer:
        out = {"scene": scene, "question": question, "answer": answer,
               "ruled_out": [{"lead": redact(x["lead"]), "why": redact(x["why"])} for x in _leads(raw.get("ruled_out"))]}
    return {**out, **lesson_of(raw)} or None


def normalize_analysis(data: dict) -> dict:
    """Coerce a model reply into the analysis shape; missing optional fields get neutral defaults."""
    friction = []
    for f in data.get("friction") or []:
        if isinstance(f, dict) and f.get("note"):
            friction.append({"kind": f.get("kind") if f.get("kind") in FRICTION_KINDS else "other", "note": str(f["note"])})
        elif isinstance(f, str) and f.strip():
            friction.append({"kind": "other", "note": f.strip()})
    outcome = str(data.get("outcome") or "").lower()
    sentiment = str(data.get("sentiment") or "").lower()
    return {
        "title": str(data.get("title") or "").strip(),
        "summary": str(data.get("summary") or "").strip(),
        "goal": str(data.get("goal") or "").strip(),
        "outcome": outcome if outcome in OUTCOMES else "unclear",
        "outcome_note": str(data.get("outcome_note") or "").strip(),
        "work_types": [w for w in _str_list(data.get("work_types")) if w in WORK_TYPES],
        "tags": [t.lower() for t in _str_list(data.get("tags"))][:10],
        "highlights": _str_list(data.get("highlights")),
        "knowledge": normalize_knowledge(data.get("knowledge")),
        "learning_interests": normalize_interests(data.get("learning_interests")),
        "open_threads": _str_list(data.get("open_threads")),
        "friction": friction,
        "sentiment": sentiment if sentiment in ("positive", "neutral", "frustrated", "mixed", "unclear") else "unclear",
    }


class AnalysisSkipped(Exception):
    pass


def _log_run(conn, kind: str, target: str, res: LLMResult | None, *, status: str, error: str | None = None,
             input_chars: int = 0, chunks: int = 1, started: str | None = None, result: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO analyses(kind, target, started_at, finished_at, model, status, error, input_chars, chunks, "
        "cost_usd, duration_ms, result_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, target, started, utcnow_iso(), res.model if res else None, status, error, input_chars, chunks,
         res.cost_usd if res else None, res.duration_ms if res else None, dumps(result) if result else None),
    )
    conn.commit()  # never hold a write transaction open across the next (minutes-long) Claude call


def analyze_session(conn: sqlite3.Connection, cfg: Config, session_id: str, runner: Runner | None = None,
                    *, model: str | None = None) -> dict:
    """Analyze one session and store the overview + knowledge. Raises LLM errors after recording them."""
    runner = runner or make_runner(cfg)
    s = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if s is None:
        raise KeyError(session_id)
    if s["source"] == "history":
        raise AnalysisSkipped("history-only session has no transcript")
    if s["source"] == "remote":
        raise AnalysisSkipped("analyzed on the computer it ran on, which keeps its transcript")
    started = utcnow_iso()
    conn.execute("UPDATE sessions SET analysis_status = 'running' WHERE id = ?", (session_id,))
    conn.commit()
    header, digest = build_digest(conn, session_id, runner.chunk_chars)
    if len(digest.text) < 200 and s["n_prompts"] <= 1:
        conn.execute("UPDATE sessions SET analysis_status='skipped', analysis_reason='too little content' WHERE id=?", (session_id,))
        conn.commit()
        raise AnalysisSkipped("too little content")
    total_cost = 0.0
    system = system_prompt(cfg)
    try:
        if len(digest.chunks) == 1:
            res = runner.run(_session_prompt(header, digest.chunks[0]), ANALYSIS_SCHEMA, system=system, model=model)
            total_cost += res.cost_usd
            data = normalize_analysis(res.data)
        else:
            notes, items = [], {}
            for i, chunk in enumerate(digest.chunks, 1):
                part = runner.run(_chunk_prompt(header, chunk, i, len(digest.chunks)), CHUNK_SCHEMA, system=system, model=model)
                total_cost += part.cost_usd
                _log_run(conn, "chunk", session_id, part, status="done", input_chars=len(chunk), started=started)
                shown = []  # the merge judges each item by what it says; the full item is kept here, by id
                for j, item in enumerate(normalize_knowledge(part.data.get("knowledge")), 1):
                    items[f"p{i}.{j}"] = item
                    shown.append({"id": f"p{i}.{j}", **{k: item[k] for k in ("kind", "title", "body", "confidence")}})
                notes.append({**part.data, "knowledge": shown})
            res = runner.run(
                _reduce_prompt(header, notes, digest.chunks[0][:6000], digest.chunks[-1][-6000:]),
                REDUCE_SCHEMA, system=system, model=model,
            )
            total_cost += res.cost_usd
            data = {**normalize_analysis(res.data), "knowledge": _kept(items, res.data.get("knowledge_ids"))}
    except (UsageLimitError, BudgetExceededError, LLMError) as exc:
        if isinstance(exc, (UsageLimitError, SleepInterruptedError)):  # not the session's fault: just retry later
            status, attempts, not_before = "pending", s["analysis_attempts"] or 0, None
        elif isinstance(exc, BudgetExceededError):  # retrying cannot help until the budget is raised
            status, attempts, not_before = "error", 99, None
        else:  # transient: back off 30m, 2h, 8h
            attempts = (s["analysis_attempts"] or 0) + 1
            status, not_before = "error", to_iso(utcnow() + timedelta(minutes=30 * 4 ** (attempts - 1)))
        conn.execute(
            "UPDATE sessions SET analysis_status=?, analysis_reason=?, analysis_attempts=?, analysis_not_before=? WHERE id=?",
            (status, str(exc)[:500], attempts, not_before, session_id),
        )
        _log_run(conn, "session", session_id, None, status="error", error=str(exc)[:1000],
                 input_chars=digest.chars, chunks=len(digest.chunks), started=started)
        conn.commit()
        raise
    res.cost_usd = total_cost
    store_analysis(conn, cfg, session_id, data, res.model, s["n_prompts"])
    _log_run(conn, "session", session_id, res, status="done", input_chars=digest.chars,
             chunks=len(digest.chunks), started=started, result=data)
    conn.commit()
    log.info("analyzed %s (%s chars, %d chunk(s), level %d, $%.3f)", session_id, digest.chars,
             len(digest.chunks), digest.level, total_cost)
    return data


def store_analysis(conn: sqlite3.Connection, cfg: Config, session_id: str, data: dict, model: str | None,
                   n_prompts: int) -> None:
    store_interests(conn, session_id, data.get("learning_interests"))
    # Curiosity quotes stay in local kv; shared analysis_json contains only the teaching material.
    data = {key: value for key, value in data.items() if key != "learning_interests"}
    s = conn.execute("SELECT project_path, project_name, ai_title, first_prompt, machine_id, source FROM sessions "
                     "WHERE id = ?", (session_id,)).fetchone()
    title = (data.get("title") or "").strip() or None
    # prompts that arrived while Claude was analyzing leave the session stale, so they get analyzed too
    conn.execute(
        "UPDATE sessions SET analysis_status = CASE WHEN n_prompts > ? THEN 'stale' ELSE 'done' END, "
        "analysis_reason = CASE WHEN n_prompts > ? THEN 'session continued during analysis' END, "
        "analysis_attempts=0, analysis_not_before=NULL, "
        "analyzed_at=?, analysis_model=?, analyzed_prompts=?, llm_title=?, summary=?, goal=?, outcome=?, outcome_note=?, "
        "sentiment=?, work_types_json=?, tags_json=?, highlights_json=?, open_threads_json=?, friction_json=?, "
        "analysis_json=?, title=? WHERE id=?",
        (
            n_prompts, n_prompts, utcnow_iso(), model, n_prompts, title, redact(data.get("summary")), redact(data.get("goal")),
            data.get("outcome"), redact(data.get("outcome_note")), data.get("sentiment"),
            dumps(data.get("work_types") or []), dumps([t.lower() for t in data.get("tags") or []]),
            dumps([redact(h) for h in data.get("highlights") or []]),
            dumps([redact(o) for o in data.get("open_threads") or []]),
            dumps(data.get("friction") or []), dumps(data),
            best_title({"llm_title": title, "ai_title": s["ai_title"], "first_prompt": s["first_prompt"]}), session_id,
        ),
    )
    kept = ladder.before_reanalysis(conn, session_id)
    from .ingest import local_machine_id

    # a hub analyzing another computer's transcript: the lessons new to it are the Team page's news (news.py)
    other = s["machine_id"] if s["machine_id"] and s["machine_id"] != local_machine_id(cfg) and s["source"] != "history" else None
    before = news.lesson_keys(conn, session_id) if other else None
    conn.execute(
        "DELETE FROM knowledge WHERE session_id = ? AND source = 'analysis' AND pinned = 0 AND status != 'dismissed'",
        (session_id,),
    )
    now = utcnow_iso()
    for item in data.get("knowledge") or []:
        if not item.get("title") or item.get("kind") not in KNOWLEDGE_KINDS:
            continue
        case = case_of(item)
        # a pinned or dismissed item with the same fingerprint is kept as it is, but gains lesson material it lacked,
        # and a diagram it lacked while its text is unchanged
        conn.execute(
            "INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, tags_json, scope, "
            "confidence, evidence, source, source_ref, fingerprint, created_at, updated_at, stage, case_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(fingerprint) DO UPDATE SET case_json = CASE WHEN knowledge.case_json IS NULL "
            "THEN excluded.case_json WHEN knowledge.body = excluded.body AND json_valid(knowledge.case_json) "
            "AND json_extract(knowledge.case_json, '$.visual') IS NULL "
            "AND json_extract(excluded.case_json, '$.visual') IS NOT NULL "
            "THEN json_set(knowledge.case_json, '$.visual', json_extract(excluded.case_json, '$.visual')) "
            "ELSE knowledge.case_json END",
            (
                session_id, s["project_path"], s["project_name"], item["kind"], redact(item["title"]).strip(),
                redact(item.get("body") or "").strip(), dumps([t.lower() for t in item.get("tags") or []]),
                item.get("scope") if item.get("scope") in ("project", "global") else "project",
                item.get("confidence"), redact(item.get("evidence") or ""), "analysis", f"prompt-v{PROMPT_VERSION}",
                fingerprint(session_id, item["kind"], item["title"]), now, now, ladder.initial_stage(item),
                dumps(case) if case else None,
            ),
        )
    ladder.after_reanalysis(conn, session_id, kept)
    if other:
        news.record(conn, other, s["project_path"], lessons=news.learned(conn, session_id, before))
