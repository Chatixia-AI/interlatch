"""Topic classification and personal curiosity signals, grounded in the viewer's own questions."""

from __future__ import annotations

import functools
import re
import sqlite3
from datetime import datetime, timedelta

from .db import kv_set
from .redact import redact
from .util import dumps, loads, to_iso, utcnow

TOPICS = {
    "system_design": r"\bsystem design\b|\barchitect(?:ure|ural)\b|\bdistributed\b|\bmicroservices?\b|\btrade.?offs?\b|システム設計|アーキテクチャ|システムデザイン|系统设计|架构",
    "frontend": r"\bfront.?end\b|\breact\b|\bvue\b|\bsvelte\b|\bcss\b|\bhtml\b|\bui\b|\bux\b|\bcomponent\b|フロントエンド|前端",
    "backend": r"\bback.?end\b|\bserver\b|\bworker\b|\bdjango\b|\bfastapi\b|\bservice layer\b|バックエンド|後端|后端",
    "api": r"\bapi\b|\brest\b|\bgraphql\b|\bgrpc\b|\bendpoint\b|\bwebhooks?\b|\bhttp\b|エンドポイント|接口",
    "data_modeling": r"\bdata model(?:ing|ling)?\b|\bschema\b|\bnormaliz(?:e|ation)\b|\bdenormaliz\w*\b|\bentity\b|\bforeign key\b|データモデル|データモデリング|正規化|数据模型|数据建模",
    "databases": r"\bdatabases?\b|\bsql\b|\bpostgres\w*\b|\bmysql\b|\bsqlite\b|\bqueries\b|\btransactions?\b|\bindexes?\b|データベース|数据库",
    "testing": r"\btest(?:ing|s)?\b|\bpytest\b|\bplaywright\b|\bmock(?:ing)?\b|\bcoverage\b|テスト|测试",
    "security": r"\bsecurity\b|\bauth(?:entication|orization)?\b|\boauth\b|\bpermissions?\b|\bcsrf\b|\bxss\b|セキュリティ|認証|認可|安全|权限",
    "devops": r"\bdeploy\w*\b|\bdocker\b|\bkubernetes\b|\bci/cd\b|\binfrastructure\b|\bdevops\b|\bcloud\b|デプロイ|インフラ|部署|云计算",
    "performance": r"\bperformance\b|\blatency\b|\bcach(?:e|ing)\b|\bprofiling\b|\boptimiza\w*\b|パフォーマンス|性能|优化",
    "ai_ml": r"\bmachine learning\b|\bllm\b|\brag\b|\bembeddings?\b|\bneural\b|機械学習|机器学习|大模型",
    "mobile": r"\bandroid\b|\bios\b|\bswiftui\b|\bflutter\b|\bmobile\b|モバイル|移动开发",
    "languages": r"\btype system\b|\bcompiler\b|\bownership\b|\bborrow checker\b|\basync\b|\bconcurrency\b|型システム|コンパイラ|並行|类型系统|并发",
}
_TOPIC_RE = {key: re.compile(pattern, re.I) for key, pattern in TOPICS.items()}
_CURIOSITY = re.compile(
    r"\b(?:why|how (?:does|do|did|is|are|can|would|should|to)|what (?:is|are|does|happens|makes|if)|"
    r"explain|understand|learn|curious|compare|difference between|trade.?offs?|choose between)\b|"
    r"なぜ|どうして|仕組み|教えて|学びたい|知りたい|違い|为什么|为何|怎么|如何|解释|理解|学习|区别", re.I)
_DELEGATION = re.compile(r"\b(?:can|could|would) you (?:implement|build|fix|add|create|deploy|write|update)\b", re.I)
SIGNAL_PREFIX = "learning.interests:"
PRACTICE_SQL = "(k.kind IN ('fix','gotcha','decision','learning','pattern') " \
               "AND (k.case_json IS NOT NULL OR LENGTH(TRIM(COALESCE(k.body,''))) > 0))"


def topic_ids(value) -> list[str]:
    return list(dict.fromkeys(x for x in value if isinstance(x, str) and x in TOPICS))[:4] if isinstance(value, list) else []


def topics_for(item: dict) -> list[str]:
    case = item.get("case")
    explicit = topic_ids(case.get("topics")) if isinstance(case, dict) else []
    if explicit:
        return explicit
    text = " ".join(str(item.get(key) or "") for key in ("title", "body", "tags"))
    text = text.replace("_", " ").replace("-", " ")
    tagged = topic_ids(item.get("tags"))
    return list(dict.fromkeys([*tagged, *_text_topics(text)]))[:4]


@functools.lru_cache(maxsize=20000)
def _text_topics(text: str) -> tuple[str, ...]:
    """Every lesson's text through 13 patterns takes most of a second on a few thousand lessons; a lesson's topics
    change only with its text, and the dashboard asks for all of them on each library and Knowledge page."""
    return tuple(key for key, pattern in _TOPIC_RE.items() if pattern.search(text))


def normalize_interests(value) -> list[dict]:
    out = []
    for signal in value if isinstance(value, list) else []:
        if not isinstance(signal, dict) or not isinstance(signal.get("topic"), str) or signal["topic"] not in TOPICS:
            continue
        question = signal.get("question")
        if isinstance(question, str) and len(question.strip()) >= 8:
            clean = {"topic": signal["topic"], "question": redact(question.strip())[:600]}
            if clean not in out:
                out.append(clean)
    return out[:12]


def prompt_signals(text: str) -> list[dict]:
    # Instruction envelopes and code examples aren't questions from the current conversation.
    text = re.sub(r"<(INSTRUCTIONS|environment_context|user_instructions)>.*?</\1>|```.*?```", "", text, flags=re.S)
    out = []
    for sentence in re.split(r"(?<=[.!?。！？])\s+|\n+", text):
        if not _CURIOSITY.search(sentence) or _DELEGATION.search(sentence):
            continue
        for topic, pattern in _TOPIC_RE.items():
            if pattern.search(sentence):
                out.append({"topic": topic, "question": sentence.strip()[:600]})
    return normalize_interests(out)


def store_interests(conn: sqlite3.Connection, session_id: str, value) -> None:
    """Keep model-extracted questions locally; reject evidence absent from actual human prompts."""
    prompts = [" ".join(redact(r[0] or "").split()).casefold() for r in conn.execute(
        "SELECT text FROM events WHERE session_id=? AND kind='prompt' AND agent_id=''", (session_id,))]
    signals = [s for s in normalize_interests(value)
               if any(" ".join(s["question"].split()).casefold() in p for p in prompts)]
    kv_set(conn, SIGNAL_PREFIX + session_id, dumps(signals))


def interest_profile(conn: sqlite3.Connection, *, viewer_id: int | None, machine_id: str,
                     project: str = "", now=None) -> dict:
    """Last 90 days of this viewer's questions. Session views also enforce project access."""
    now = now or utcnow()
    where = ["s.started_at >= ?"]
    params = [to_iso(now - timedelta(days=90))]
    if viewer_id is not None:
        where.append("COALESCE(s.machine_id, ?) IN (SELECT id FROM machines WHERE person_id=?)")
        params.extend([machine_id, viewer_id])
    else:
        where.append("(s.machine_id IS NULL OR s.machine_id=?)")
        params.append(machine_id)
    if project:
        where.append("(s.project_path=? OR s.project_name=?)")
        params.extend([project, project])
    sessions = [dict(r) for r in conn.execute(
        "SELECT s.id, s.started_at, k.value FROM sessions s LEFT JOIN kv k ON k.key=? || s.id WHERE "
        + " AND ".join(where) + " ORDER BY s.started_at DESC LIMIT 200", [SIGNAL_PREFIX, *params])]
    if not sessions:
        return {"interests": []}
    by_session = {s["id"]: normalize_interests(loads(s["value"], [])) for s in sessions}
    placeholders = ",".join("?" * len(sessions))
    for row in conn.execute(
        "SELECT e.session_id, substr(e.text,1,6000) text FROM events e JOIN sessions s ON s.id=e.session_id "
        f"WHERE e.kind='prompt' AND e.agent_id='' AND e.session_id IN ({placeholders}) "
        "ORDER BY s.started_at DESC, e.seq DESC LIMIT 2000", list(by_session)):
        by_session[row["session_id"]].extend(prompt_signals(row["text"] or ""))
    found = {}
    for session in sessions:
        # Repeated prompts within one session count once; independent follow-up sessions add weight.
        seen = set()
        try:
            age = max(0, (now - datetime.fromisoformat(session["started_at"].replace("Z", "+00:00"))).days)
        except (ValueError, TypeError):
            age = 90
        for signal in by_session[session["id"]]:
            topic = signal["topic"]
            if topic in seen:
                continue
            seen.add(topic)
            entry = found.setdefault(topic, {"topic": topic, "score": 0, "examples": []})
            entry["score"] += 1 / (1 + age / 30)
            if len(entry["examples"]) < 3:
                entry["examples"].append({"question": signal["question"], "session_id": session["id"],
                                          "at": session["started_at"]})
    return {"interests": sorted(found.values(), key=lambda s: (-s["score"], s["topic"]))[:6]}


def glance(items: list[dict], interests: list[dict], *, now=None) -> dict:
    """The Knowledge page's look into Learn from your work, from lessons shaped like /api/knowledge items (search
    lessons=True, then Server._reason): the newest lesson with a principle (else the newest case file), the newest case
    files for the page to ask (it skips the ones this browser answered; ones with ruled-out leads first, since those
    ask with real choices), and the topic to read next: the one asked
    about most, else the one with the most lessons in the last 30 days. "Other" is never the topic."""
    now = now or utcnow()
    lessons = sorted((k for k in items if k.get("confidence") != "low" and k.get("stage") != "wip"),
                     key=lambda k: str(k.get("session_started") or k.get("created_at") or ""), reverse=True)
    case = lambda k: k.get("case") or {}  # noqa: E731
    latest = next((k for k in lessons if case(k).get("principle")), None) \
        or next((k for k in lessons if case(k).get("question")), None)
    by_topic: dict[str, list[dict]] = {}
    for k in lessons:
        for topic in k.get("learning_topics") or []:
            by_topic.setdefault(topic, []).append(k)
    asked = [i["topic"] for i in interests if by_topic.get(i.get("topic"))]
    since = to_iso(now - timedelta(days=30))
    recent = {t: sum(str(k.get("session_started") or k.get("created_at") or "") >= since for k in ks)
              for t, ks in by_topic.items()}
    topic = asked[0] if asked else max(by_topic, key=lambda t: (recent[t], len(by_topic[t]), t), default=None)
    cases = [k for k in lessons if case(k).get("question")]
    cases = [k for k in cases if case(k).get("ruled_out")] + [k for k in cases if not case(k).get("ruled_out")]
    return {"latest": latest, "cases": cases[:20],
            "topic": {"id": topic, "count": len(by_topic[topic]), "asked": bool(asked), "lessons": by_topic[topic][:2]}
            if topic else None}
