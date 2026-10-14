"""Screen imported chats: which ones are worth a full analysis (`interlatch screen`, Sources › Chat exports).

Years of claude.ai and ChatGPT chats are mostly lookups, rewrites and everyday questions that analysis turns into
nothing, and analyzing them all would take the plan's limits for days. Screening reads only each chat's opening
(title, date, first and last prompt, the start of the first reply) and sorts it, so analysis goes where it pays:

1. Rules, no model call, for what is certain: nothing in the chat to analyze, no reply in the export, or a one- or
   two-prompt chore on pasted text (translate, summarize, proofread): `skip`.
2. The screening model (`analysis.screen_model`, Haiku by default) reads the rest, 60 chats a call, and answers
   analyze / maybe / skip with a topic and a one-line reason. It is told what analysis keeps and which projects the
   developer works on, from their recorded sessions.

Screening analyzes nothing: `queue()` (`interlatch screen --queue`, **Queue for analysis**) puts the chats worth it in
the background queue. A chat is screened once, and again when a newer export changes it.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .chat_import import FORMATS
from .config import Config
from .llm import LLMError, Runner, UsageLimitError, make_runner, written_in
from .redact import redact
from .util import one_line, utcnow_iso

log = logging.getLogger("interlatch.screen")

VERDICTS = ("analyze", "maybe", "skip")
VERDICT_LABEL = {"analyze": "worth analyzing", "maybe": "maybe", "skip": "not worth it"}
BATCH = 60
SOURCES = tuple(f.source for f in FORMATS)

# the first line asks for a chore on the text pasted after it; code after it makes it a coding question instead
_CHORE = re.compile(r"\b(?:translat(?:e|ion)|summari[sz]e|summary of|tl;?dr|proofread|paraphrase|rephrase|reword|"
                    r"correct (?:the |my )?(?:grammar|spelling|english|japanese))\b|翻訳|和訳|英訳|日本語訳|要約|校正|添削",
                    re.I)
_CODE = re.compile(r"```|^\s*(?:def |class |import |from \S+ import |function |const |let |var |SELECT |#include)"
                   r"|[{};]\s*$|^\s*\$ ", re.M)

SCREEN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["chats"],
    "properties": {"chats": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["n", "verdict", "topic", "reason"],
        "properties": {
            "n": {"type": "integer", "description": "The chat's number"},
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "topic": {"type": "string", "description": "2-4 words, e.g. 'docker compose', 'procurement AI', 'childcare'"},
            "reason": {"type": "string", "description": "One short line on why"},
        },
    }}},
}

SYSTEM_PROMPT = """\
You screen a developer's imported chat history (claude.ai or ChatGPT) to decide which chats are worth a full analysis.

A full analysis is one long model call per chat. It keeps only knowledge that would save this developer time later: \
fixes with their root cause, gotchas, decisions with their reasons, facts about their own projects, work, systems and \
setup, commands or snippets that worked, and preferences about how they want AI assistants to work. It drops generic \
advice and anything quick to look up again.

For each chat you see only its opening: number, date, prompt count, title, the first prompt, the start of the first \
reply and the last prompt. Answer for every chat:

- analyze: about their own projects, systems, employer or clients in a way worth recording as a fact, decision or \
fix. The chat names or describes something specific to them (a product they build, an internal system, a client, \
their deployment or environment) and works on it: a design or architecture choice with its trade-offs, planning or \
strategy for their work, debugging their setup towards a cause, how their systems or data work, preferences they state.
- maybe: could go either way. Their code pasted with a generic request (optimize, refactor, convert, add a small \
feature, fix a common error); a technical question with a little of their context; about their work but thin; a long \
chat whose opening does not show where it went.
- skip: nothing reusable. A generic question or common error any documentation or search answers, even with their \
code pasted; translating, summarizing, rewriting or proofreading pasted text; image requests; trivia; everyday life \
(health, family, children, shopping, food, travel, hobbies, pets, housing, money); small talk; tests of the assistant.

Judge the content, not the length: two prompts that pin down a bug in their own setup are analyze; four hundred \
prompts about where to live are skip. The mistakes are not equal: a chat wrongly skipped is never analyzed and its \
knowledge is lost, while one wrongly marked analyze costs one analysis. So skip only what clearly holds nothing \
reusable; anything about their own work, employer, clients or products is at least maybe, unless it is only a text \
chore; between analyze and maybe, choose maybe when unsure. Chats over two years old about a stack they no longer use \
(compare the projects below) are at most maybe, unless they record a decision about ongoing work. Chats are in any \
language; write topic and reason in English. Return one entry per chat number, each number exactly once."""
WRITE_ENGLISH = "Chats are in any language; write topic and reason in English."
# a rule's (topic, reason) when Interlatch writes in Japanese
RULES_JA = {"no reply": ("返信なし", "エクスポートにこのチャットの返信がない"),
            "too short": ("短すぎる", "分析できる内容がほとんどない"),
            "text chore": ("テキストの作業", "貼り付けたテキストの翻訳、要約、校正")}


@dataclass
class ScreenReport:
    screened: int = 0
    counts: Counter = field(default_factory=Counter)  # verdict -> chats
    by_rules: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    left: int = 0  # chats a failed or incomplete call left unscreened
    error: str | None = None  # why screening stopped early

    def summary(self) -> str:
        if not self.screened and not self.left:
            return "no chats to screen"
        parts = [f"{self.counts[v]:,} {VERDICT_LABEL[v]}" for v in VERDICTS]
        out = f"screened {self.screened:,} chats: " + ", ".join(parts)
        if self.by_rules:
            out += f" ({self.by_rules:,} by rules, without a model call)"
        if self.left:
            out += f"; {self.left:,} not screened, run it again" + (f" ({self.error})" if self.error else "")
        return out


def source_filter(source: str | None) -> tuple[str, list]:
    sources = [f.source for f in FORMATS if source in (None, f.key, f.source, f.agent)]
    return f"source IN ({','.join('?' * len(sources))})", sources


def candidates(conn: sqlite3.Connection, *, source: str | None = None, redo: bool = False, limit: int | None = None,
               sample: bool = False) -> list[sqlite3.Row]:
    """Imported chats not analyzed yet and not screened at their current version (all of them with `redo`), newest
    first, or a random `sample`."""
    where, params = source_filter(source)
    sql = (f"SELECT id, files_sig, title, started_at, n_prompts, substr(first_prompt, 1, 1500) first_prompt, "
           f"substr(last_prompt, 1, 1500) last_prompt, "
           f"(SELECT substr(text, 1, 800) FROM events e WHERE e.session_id = s.id AND e.agent_id = '' AND e.kind = 'text' "
           f"ORDER BY seq LIMIT 1) reply, "
           f"(SELECT SUM(length(text)) FROM events e WHERE e.session_id = s.id AND e.agent_id = '' "
           f"AND e.kind IN ('prompt', 'text')) chars "
           f"FROM sessions s WHERE {where} AND analysis_status != 'done'")
    if not redo:
        sql += " AND (screen_sig IS NULL OR screen_sig != files_sig)"
    sql += " ORDER BY random()" if sample else " ORDER BY started_at DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def rule_verdict(r) -> tuple[str, str] | None:
    """(topic, reason) when a chat is certainly not worth analyzing; None leaves it to the model."""
    if not r["reply"]:
        return "no reply", "the export has no reply in this chat"
    if (r["n_prompts"] or 0) <= 1 and (r["chars"] or 0) < 200:
        return "too short", "too little in it to analyze"
    prompt = (r["first_prompt"] or "").strip()
    first, _, pasted = prompt.partition("\n")
    if (r["n_prompts"] or 0) <= 2 and len(first) <= 160 and _CHORE.search(first) and len(pasted.strip()) >= 200 \
            and not _CODE.search(pasted):
        return "text chore", "translating, summarizing or proofreading pasted text"
    return None


def developer_context(conn: sqlite3.Connection) -> str:
    """What the developer works on, from their recorded (not imported) sessions and knowledge: tells work from trivia."""
    where, params = source_filter(None)
    projects = [r[0] for r in conn.execute(
        f"SELECT project_name FROM sessions WHERE NOT ({where}) AND source != 'history' AND project_name IS NOT NULL "
        f"AND project_name != '' GROUP BY project_name ORDER BY MAX(started_at) DESC LIMIT 25", params)]
    tags: Counter = Counter()
    for (raw,) in conn.execute("SELECT tags_json FROM knowledge WHERE status = 'active' ORDER BY id DESC LIMIT 3000"):
        try:
            tags.update(t for t in json.loads(raw or "[]") if isinstance(t, str))
        except ValueError:
            continue
    lines = []
    if projects:
        lines.append("Projects they work on with coding agents, most recent first: " + ", ".join(projects))
    if tags:
        lines.append("Topics in their knowledge base: " + ", ".join(t for t, _ in tags.most_common(30)))
    return "\n".join(lines)


def _card(i: int, r) -> str:
    n = r["n_prompts"] or 0
    lines = [f"#{i} · {(r['started_at'] or '')[:10]} · {n} prompt{'' if n == 1 else 's'} · {one_line(r['title'], 100) or '(untitled)'}",
             f"first prompt: {one_line(redact(r['first_prompt']), 500)}"]
    if r["reply"]:
        lines.append(f"reply begins: {one_line(redact(r['reply']), 200)}")
    if n > 1 and r["last_prompt"]:
        lines.append(f"last prompt: {one_line(redact(r['last_prompt']), 250)}")
    return "\n".join(lines)


def _ask(cfg: Config, runner: Runner, context: str, batch: list) -> tuple[dict[int, dict], object, int]:
    cards = "\n\n".join(_card(i, r) for i, r in enumerate(batch, 1))
    prompt = (f"<developer>\n{context or 'No recorded projects yet.'}\n</developer>\n\n"
              f"<chats count=\"{len(batch)}\">\n{cards}\n</chats>\n\nScreen these {len(batch)} chats.")
    system = written_in(cfg, SYSTEM_PROMPT, WRITE_ENGLISH, "Chats are in any language; write topic and reason in Japanese.")
    res = runner.run(prompt, SCREEN_SCHEMA, system=system, model=cfg.analysis.screen_model, effort="low")
    out = {}
    for c in res.data.get("chats") or []:
        if not isinstance(c, dict):
            continue
        try:
            i = int(c.get("n"))
        except (TypeError, ValueError):
            continue
        verdict = str(c.get("verdict") or "").strip().lower()
        if 1 <= i <= len(batch) and verdict in VERDICTS:
            out[i] = {"verdict": verdict, "topic": one_line(c.get("topic"), 60), "reason": one_line(c.get("reason"), 200)}
    return out, res, len(prompt)


def _store(conn: sqlite3.Connection, r, verdict: str, topic: str, reason: str, by: str) -> None:
    conn.execute("UPDATE sessions SET screen_verdict = ?, screen_topic = ?, screen_reason = ?, screen_by = ?, "
                 "screen_sig = ?, screened_at = ? WHERE id = ?",
                 (verdict, topic or None, reason or None, by, r["files_sig"], utcnow_iso(), r["id"]))


def screen_chats(cfg: Config, conn: sqlite3.Connection, *, source: str | None = None, redo: bool = False,
                 limit: int | None = None, sample: bool = False, runner: Runner | None = None, progress=None) -> ScreenReport:
    """Screen imported chats: rules first, then the screening model in parallel batches; writes stay on `conn`."""
    report = ScreenReport()
    rest = []
    for r in candidates(conn, source=source, redo=redo, limit=limit, sample=sample):
        ruled = rule_verdict(r)
        if ruled and cfg.analysis.language == "ja":
            ruled = RULES_JA.get(ruled[0], ruled)
        if ruled:
            _store(conn, r, "skip", *ruled, "rules")
            report.counts["skip"] += 1
            report.by_rules += 1
            report.screened += 1
        else:
            rest.append(r)
    conn.commit()
    if not rest:
        return report
    runner = runner or make_runner(cfg)
    if not runner.available():
        report.left, report.error = len(rest), runner.unavailable_reason()
        return report
    context = developer_context(conn)
    batches = [rest[i:i + BATCH] for i in range(0, len(rest), BATCH)]
    if progress:
        progress(f"screening {len(rest):,} chats with {runner.label} ({runner.model_label(cfg.analysis.screen_model)}), "
                 f"{len(batches)} call{'s' if len(batches) > 1 else ''}…")
    pool = ThreadPoolExecutor(max_workers=max(3, cfg.analysis.concurrency))  # small calls: a few at once is fine
    try:
        futures = {pool.submit(_ask, cfg, runner, context, b): b for b in batches}
        for fut in as_completed(futures):
            if fut.cancelled():  # counted in `left` when it was cancelled
                continue
            batch = futures[fut]
            try:
                verdicts, res, chars = fut.result()
            except LLMError as exc:
                report.left += len(batch)
                report.error = one_line(str(exc), 200)
                if isinstance(exc, UsageLimitError):  # the rest would fail the same way
                    for f in futures:
                        if f.cancel():
                            report.left += len(futures[f])
                log.warning("screening call failed: %s", exc)
                continue
            report.calls += 1
            report.cost_usd += res.cost_usd
            for i, r in enumerate(batch, 1):
                v = verdicts.get(i)
                if not v:  # the model left it out: screened on the next run
                    report.left += 1
                    continue
                _store(conn, r, v["verdict"], v["topic"], v["reason"], res.model or cfg.analysis.screen_model)
                report.counts[v["verdict"]] += 1
                report.screened += 1
            conn.execute("INSERT INTO analyses(kind, target, started_at, finished_at, model, status, input_chars, chunks, "
                         "cost_usd, duration_ms) VALUES ('screen', ?, ?, ?, ?, 'done', ?, 1, ?, ?)",
                         (f"{len(batch)} chats", utcnow_iso(), utcnow_iso(), res.model, chars, res.cost_usd, res.duration_ms))
            conn.commit()
            if progress:
                progress(f"screened {report.screened:,} of {len(rest) + report.by_rules:,} chats…")
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        conn.commit()
    log.info("screen: %s", report.summary())
    return report


def queue(conn: sqlite3.Connection, *, source: str | None = None, maybe: bool = False) -> int:
    """Put the chats screened as worth analyzing (and `maybe` ones) in the background analysis queue."""
    where, params = source_filter(source)
    verdicts = ["analyze", "maybe"] if maybe else ["analyze"]
    n = conn.execute(
        f"UPDATE sessions SET analysis_status = 'pending', analysis_reason = NULL, analysis_attempts = 0, "
        f"analysis_not_before = NULL WHERE {where} AND analysis_status = 'skipped' "
        f"AND screen_verdict IN ({','.join('?' * len(verdicts))}) AND screen_sig = files_sig", [*params, *verdicts]).rowcount
    conn.commit()
    return n


def screen_status(conn: sqlite3.Connection, source: str) -> dict:
    """One format's chats by verdict, how many are not screened yet, and how many screened ones wait in the queue."""
    r = conn.execute(
        "SELECT SUM(fresh AND screen_verdict = 'analyze') analyze, SUM(fresh AND screen_verdict = 'maybe') maybe, "
        "SUM(fresh AND screen_verdict = 'skip') skip, SUM(NOT fresh AND analysis_status != 'done') unscreened, "
        "SUM(fresh AND screen_verdict = 'analyze' AND analysis_status = 'skipped') to_queue, "
        "SUM(fresh AND screen_verdict IN ('analyze', 'maybe') AND analysis_status IN ('pending', 'running', 'stale')) queued "
        "FROM (SELECT screen_verdict, analysis_status, COALESCE(screen_sig = files_sig, 0) fresh FROM sessions WHERE source = ?)",
        (source,)).fetchone()
    return {k: r[k] or 0 for k in r.keys()}
