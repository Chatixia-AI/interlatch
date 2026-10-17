"""Study battles (Innerlatch): one of the person's own lessons as a question they answer in their own words, judged by
the analysis model against the lesson. What they understood fills their readiness for the lesson's roadmap module, the
step before that module's gate. The agents' levels never move: their XP is coverage, never the person's level (party.py).

Per-person learning data stays on the person's own computer (docs/design/learning.md): only someone at this computer
(viewer None) is judged and recorded; a person signed in to a hub gets no study battles there. A lesson's question,
written once by the model for a lesson without a case file, is the lesson's, not anyone's answer, and is kept with it.

Which lessons are worth studying: a lesson with study material (a case file's question, or a principle) first; a trap
the agents keep hitting in this setup (an aliased rm, zsh, where screenshots land) is friction for the agents' rules,
not the person's career, and is never offered (friction()).

Coming back (schedule()): a lesson understood rests, then returns as it would start to fade: 3 days after the first
"got it", then 10, 30 and 90 as it is got right again on separate days; "partly" or "not yet" puts it back at once. Got
right on two separate days in a row, it is owned. Nothing is ever overdue: a returning lesson just waits on the field,
and time away builds no backlog (docs/design/learning.md).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, timedelta

from ..core.config import Config
from ..core.util import loads, utcnow
from ..storage.kv import kv_get, kv_set, kv_transaction

log = logging.getLogger("interlatch.study")

ASK_KEY = "party.ask:"  # + lesson id: {scene, question, answer}, written once for a lesson without a case file
STUDY_KEY = "party.study:"  # + scope key: [{"id", "on", "verdict"}], each judged answer, oldest first
STUDY_MAX = 20_000
READY_AT = 5  # lessons understood in a module: it is ready for its gate
ANSWER_MAX = 2000  # characters of a typed answer the model reads
BODY_MAX = 2500
VERDICTS = ("got_it", "partly", "not_yet")
RETURN_DAYS = (3, 10, 30, 90)  # rest after the 1st, 2nd, 3rd, 4th+ "got it" in a row (on separate days)
OWNED_AT = 2  # "got it" on this many separate days in a row: owned
TOOLING_TAGS = frozenset({"shell", "zsh", "bash", "alias", "playwright", "mcp", "tooling", "sandbox", "scratchpad", "excalidraw"})
NOT_HERE = "study battles run on your own computer"
NO_MODEL = "the analysis model isn't available: {reason}"
NO_ANSWER = "write an answer first"


class StudyError(ValueError):
    """Something asked of a study battle that can't be done: the message is an English template (tr())."""


# ------------------------------------------------------------------ which lessons are worth studying
def friction(kind: str, scope: str | None, stage: str, tags) -> bool:
    """A trap the agents keep hitting in this setup, for the agents' rules rather than the person's career: a gotcha
    that applies everywhere and has recurred, or one tagged as shell or agent tooling."""
    if kind != "gotcha":
        return False
    names = {str(t).lower() for t in tags or [] if isinstance(t, str)}
    return (scope == "global" and stage in ("established", "canonical")) or bool(names & TOOLING_TAGS)


def material(raw) -> dict:
    """A lesson's study material from its case_json: scene, question, answer, principle, checklist and ruled-out
    leads, each present only when it has text."""
    got = loads(raw, None)
    if not isinstance(got, dict):
        return {}
    text = lambda v: v.strip() if isinstance(v, str) and v.strip() else None  # noqa: E731
    out = {k: text(got.get(k)) for k in ("scene", "question", "answer", "principle")}
    checks = got.get("checklist")
    out["checklist"] = [c.strip() for c in checks if isinstance(c, str) and c.strip()] if isinstance(checks, list) else []
    leads = got.get("ruled_out")
    out["ruled_out"] = [{"lead": text(x.get("lead")), "why": text(x.get("why"))} for x in leads
                        if isinstance(x, dict) and text(x.get("lead"))] if isinstance(leads, list) else []
    return {k: v for k, v in out.items() if v}


def flags(conn: sqlite3.Connection, ids) -> dict[int, tuple[bool, bool]]:
    """For each of these lessons: (it has study material, it is the agents' friction)."""
    want = set(ids)
    if not want:
        return {}
    out = {}
    for r in conn.execute("SELECT id, kind, scope, stage, tags_json, case_json FROM knowledge WHERE status = 'active'"):
        if r["id"] not in want:
            continue
        m = material(r["case_json"])
        out[r["id"]] = (bool(m.get("question") or m.get("principle")),
                        friction(r["kind"], r["scope"], r["stage"] or "", loads(r["tags_json"], [])))
    return out


# ------------------------------------------------------------------ the person's record (their own computer only)
def studied_of(conn: sqlite3.Connection, key: str) -> list[dict]:
    """The scope `key`'s judged answers, oldest first: {"id": lesson id, "on": "YYYY-MM-DD", "verdict": VERDICTS}."""
    got = loads(kv_get(conn, STUDY_KEY + key), [])
    return [x for x in got if isinstance(x, dict) and isinstance(x.get("id"), int) and not isinstance(x["id"], bool)
            and isinstance(x.get("on"), str) and x.get("verdict") in VERDICTS] if isinstance(got, list) else []


def understood(log_: list[dict]) -> list[int]:
    """The lessons whose newest answer was judged "got it", in the order first understood."""
    last: dict[int, str] = {}
    for x in log_:
        last[x["id"]] = x["verdict"]
    return [i for i, v in last.items() if v == "got_it"]


def schedule(log_: list[dict], today: date) -> dict[int, dict]:
    """Each lesson answered "got it" last: {streak (separate days in a row it was got right), owned, due (the day it
    comes back, ISO), resting (not back yet)}. A lesson answered otherwise last isn't here: it is out on the field."""
    days: dict[int, list] = {}
    for x in log_:  # per lesson, oldest first: (day, verdict), one a day (the day's last answer counts)
        seq = days.setdefault(x["id"], [])
        if seq and seq[-1][0] == x["on"]:
            seq[-1] = (x["on"], x["verdict"])
        else:
            seq.append((x["on"], x["verdict"]))
    out = {}
    for lid, seq in days.items():
        if seq[-1][1] != "got_it":
            continue
        streak = 0
        for _, v in reversed(seq):
            if v != "got_it":
                break
            streak += 1
        rest = RETURN_DAYS[min(streak, len(RETURN_DAYS)) - 1]
        due = date.fromisoformat(seq[-1][0]) + timedelta(days=rest)
        out[lid] = {"streak": streak, "owned": streak >= OWNED_AT, "due": due.isoformat(), "resting": today < due}
    return out


def readiness(lessons, catalog, log_: list[dict]) -> dict[str, int]:
    """Lessons understood per roadmap module (each counted once, in its best module): {module id: n}."""
    done = set(understood(log_))
    out: dict[str, int] = {}
    for k in lessons:
        if k.id in done and k.modules:
            mid = catalog.modules[k.modules[0]]["id"]
            out[mid] = out.get(mid, 0) + 1
    return out


# ------------------------------------------------------------------ the question, and the judge
FORMS = {
    "fix": "what caused it and what fixed it",
    "gotcha": "what the trap is and how to avoid it",
    "decision": "what was chosen and why, over the alternative",
    "learning": "the insight, in their own words",
    "pattern": "when to use it and its key steps",
    "command": "the command (or its key part) that does it",
}
ASK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["scene", "question", "answer"],
    "properties": {
        "scene": {"type": "string", "description": "1-2 sentences: the situation as the developer met it, before the "
                                                   "answer was known. Never give the answer away."},
        "question": {"type": "string", "description": "One question, at most 20 words, whose answer is the lesson's key point"},
        "answer": {"type": "string", "description": "The expected answer, at most 2 sentences"},
    },
}
ASK_SYSTEM = ("You turn a lesson from a developer's own past work into one question they can answer from memory, so "
              "they learn it. Ask for {form}. The scene sets up the moment without revealing the answer; the question "
              "is answerable in a sentence or two; the answer is what the lesson says, nothing invented.")
JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdict", "feedback", "principle"],
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "feedback": {"type": "string", "description": "At most 2 sentences to the developer (\"you\"): what they got "
                                                      "right and what was missing"},
        "principle": {"type": "string", "description": "The transferable takeaway in one sentence, true beyond this project"},
    },
}
JUDGE_SYSTEM = (
    "You check a developer's own answer to a question about a lesson from their past work. Judge understanding, not "
    "wording or spelling.\n"
    "- got_it: they state the core of the expected answer ({form}); missing details are fine.\n"
    "- partly: on the right track, but the key part is missing or vague.\n"
    "- not_yet: wrong, empty, unrelated, or a guess.\n"
    "The expected answer was written by an AI from a session transcript and can be incomplete: when their answer is "
    "right by the lesson's own text, count it even if it differs from the expected answer. Never invent facts beyond "
    "the lesson. Be warm and brief.")


def _runner(cfg: Config, runner):
    from ..analysis.runners.factory import make_runner

    r = runner or make_runner(cfg)
    if not r.available():
        raise StudyError(NO_MODEL.replace("{reason}", r.unavailable_reason() or r.label))
    return r


def _lesson_row(conn: sqlite3.Connection, lesson_id: int):
    return conn.execute("SELECT id, kind, title, body, project_name, case_json FROM knowledge WHERE id = ?", (lesson_id,)).fetchone()


def _system(cfg: Config, system: str) -> str:
    from ..analysis.language import written_in

    return written_in(cfg, system)


def ask(conn: sqlite3.Connection, cfg: Config, lesson_id: int, *, runner=None) -> dict:
    """The question a study battle asks: the case file's scene and question when the lesson has one; otherwise one
    written by the model once and kept with the lesson. {scene, question, answer, source: "case" | "written"}."""
    row = _lesson_row(conn, lesson_id)
    if row is None:
        raise StudyError("no such lesson")
    m = material(row["case_json"])
    if m.get("scene") and m.get("question") and m.get("answer"):
        return {"scene": m["scene"], "question": m["question"], "answer": m["answer"], "source": "case"}
    kept = loads(kv_get(conn, ASK_KEY + str(lesson_id)), None)
    if isinstance(kept, dict) and all(isinstance(kept.get(k), str) and kept[k].strip() for k in ("scene", "question", "answer")):
        return {**{k: kept[k] for k in ("scene", "question", "answer")}, "source": "written"}
    r = _runner(cfg, runner)
    form = FORMS.get(row["kind"], FORMS["learning"])
    prompt = (f"<lesson kind=\"{row['kind']}\" project=\"{row['project_name'] or ''}\">\n<title>{row['title']}</title>\n"
              f"<body>\n{(row['body'] or '')[:BODY_MAX]}\n</body>\n"
              + (f"<principle>{m['principle']}</principle>\n" if m.get("principle") else "")
              + "</lesson>\n\nWrite the scene, the question and its answer.")
    res = r.run(prompt, ASK_SCHEMA, system=_system(cfg, ASK_SYSTEM.replace("{form}", form)),
                model=cfg.analysis.screen_model, effort="low", timeout=120)
    got = {k: str(res.data.get(k) or "").strip() for k in ("scene", "question", "answer")}
    if not all(got.values()):
        raise StudyError(NO_MODEL.replace("{reason}", "no question came back"))
    kv_set(conn, ASK_KEY + str(lesson_id), json.dumps(got, ensure_ascii=False))
    conn.commit()
    return {**got, "source": "written"}


def judge(conn: sqlite3.Connection, cfg: Config, scope_key: str, lesson_id: int, answer: str, *, here: bool,
          runner=None, today: date | None = None) -> dict:
    """Judge the person's answer to a lesson's question and keep the verdict in their own record (on this computer
    only: `here` False refuses). {verdict, feedback, principle, answer (the lesson's), recorded}. The lesson must be
    one the caller checked is the person's."""
    if not here:
        raise StudyError(NOT_HERE)
    text = (answer or "").strip() if isinstance(answer, str) else ""
    if not text:
        raise StudyError(NO_ANSWER)
    q = ask(conn, cfg, lesson_id, runner=runner)
    row = _lesson_row(conn, lesson_id)
    m = material(row["case_json"])
    r = _runner(cfg, runner)
    form = FORMS.get(row["kind"], FORMS["learning"])
    leads = "".join(f"<ruled_out lead=\"{x['lead']}\">{x.get('why') or ''}</ruled_out>\n" for x in m.get("ruled_out", []))
    prompt = (f"<lesson kind=\"{row['kind']}\">\n<title>{row['title']}</title>\n<body>\n{(row['body'] or '')[:BODY_MAX]}\n</body>\n"
              + (f"<principle>{m['principle']}</principle>\n" if m.get("principle") else "") + leads
              + f"</lesson>\n<question>{q['question']}</question>\n<expected_answer>{q['answer']}</expected_answer>\n"
              f"<developer_answer>\n{text[:ANSWER_MAX]}\n</developer_answer>\n\nJudge the developer's answer.")
    res = r.run(prompt, JUDGE_SCHEMA, system=_system(cfg, JUDGE_SYSTEM.replace("{form}", form)),
                model=cfg.analysis.screen_model, effort="low", timeout=120)
    verdict = res.data.get("verdict") if res.data.get("verdict") in VERDICTS else "not_yet"
    day = today or utcnow().date()
    with kv_transaction(conn):
        kept = studied_of(conn, scope_key) + [{"id": lesson_id, "on": day.isoformat(), "verdict": verdict}]
        kv_set(conn, STUDY_KEY + scope_key, json.dumps(kept[-STUDY_MAX:]))
    sched = schedule(kept, day).get(lesson_id)
    return {"verdict": verdict, "feedback": str(res.data.get("feedback") or "").strip(),
            "principle": str(res.data.get("principle") or m.get("principle") or "").strip(), "answer": q["answer"], "recorded": True,
            "back_in": (date.fromisoformat(sched["due"]) - day).days if sched else 0, "owned": bool(sched and sched["owned"])}
