"""Study battles (Innerlatch): one of the person's own lessons as a multiple-choice question, its options written for
that lesson by the analysis model (the right one, and plausible wrong ones about the same problem: the leads its
session ruled out first). The first pick in a fight is the one kept. What they understood fills their readiness for the
lesson's roadmap module, the step before that module's gate. The agents' levels never move: their XP is coverage, never
the person's level (party.py).

Per-person learning data stays on the person's own computer (docs/design/learning.md): only someone at this computer
(viewer None) is recorded; a person signed in to a hub gets no study battles there. A lesson's question and options,
written once, are the lesson's, not anyone's answer, and are kept with it.

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

ASK_KEY = "party.ask:"  # + lesson id: {v, scene, question, options, principle}, written once for the lesson
ASK_VERSION = 2  # what's kept under ASK_KEY; another version is written again
OPTIONS = 4
STUDY_KEY = "party.study:"  # + scope key: [{"id", "on", "verdict"}], each first pick, oldest first
STUDY_MAX = 20_000
READY_AT = 5  # lessons understood in a module: it is ready for its gate
BODY_MAX = 2500
VERDICTS = ("got_it", "partly", "not_yet")
RETURN_DAYS = (3, 10, 30, 90)  # rest after the 1st, 2nd, 3rd, 4th+ "got it" in a row (on separate days)
OWNED_AT = 2  # "got it" on this many separate days in a row: owned
TOOLING_TAGS = frozenset({"shell", "zsh", "bash", "alias", "playwright", "mcp", "tooling", "sandbox", "scratchpad", "excalidraw"})
NOT_HERE = "study battles run on your own computer"
NO_MODEL = "the analysis model isn't available: {reason}"
NO_CHOICE = "pick one of the answers"


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
    """The scope `key`'s first picks, oldest first: {"id": lesson id, "on": "YYYY-MM-DD", "verdict": VERDICTS}."""
    got = loads(kv_get(conn, STUDY_KEY + key), [])
    return [x for x in got if isinstance(x, dict) and isinstance(x.get("id"), int) and not isinstance(x["id"], bool)
            and isinstance(x.get("on"), str) and x.get("verdict") in VERDICTS] if isinstance(got, list) else []


def understood(log_: list[dict]) -> list[int]:
    """The lessons whose newest pick was "got it", in the order first understood."""
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


# ------------------------------------------------------------------ the question and its options
FORMS = {
    "fix": "what caused it and what fixed it",
    "gotcha": "what the trap is and how to avoid it",
    "decision": "what was chosen and why, over the alternative",
    "learning": "the insight",
    "pattern": "when to use it and its key steps",
    "command": "the command (or its key part) that does it",
}
ASK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["scene", "question", "options", "principle"],
    "properties": {
        "scene": {"type": "string", "description": "1-2 sentences: the situation as the developer met it, before the "
                                                   "answer was known. Never give the answer away."},
        "question": {"type": "string", "description": "One question, at most 20 words, whose answer is the lesson's key point"},
        "options": {"type": "array", "minItems": OPTIONS, "maxItems": OPTIONS, "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "right", "why"],
            "properties": {
                "text": {"type": "string", "description": "One answer, at most 20 words"},
                "right": {"type": "boolean", "description": "True for exactly one option: what the lesson says"},
                "why": {"type": "string", "description": "One sentence, from the lesson: why this one is wrong (or, for "
                                                         "the right one, why it is right)"},
            }}},
        "principle": {"type": "string", "description": "The transferable takeaway in one sentence, true beyond this project"},
    },
}
ASK_SYSTEM = (
    "You turn a lesson from a developer's own past work into one multiple-choice question they answer from memory, so "
    "they learn it. Ask for {form}.\n"
    "- The scene sets up the moment without revealing the answer. When the lesson has a case file, keep its scene and "
    "question.\n"
    "- Exactly one option is right: what the lesson says. The others are plausible for the same situation and wrong by "
    "the lesson's own text: the leads the session ruled out first, then likely misconceptions. Never an option that is "
    "also true, never \"all\" or \"none of the above\".\n"
    "- All options alike in length and form, so the wording never gives the answer away: the right one is never the "
    "longest.\n"
    "- Nothing invented beyond the lesson.")


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


def _clean(got) -> dict | None:
    """A written question as kept: {scene, question, options: [{text, right, why}], principle}, or None when it isn't
    one (no scene or question, not OPTIONS options with text, not exactly one right, two alike)."""
    if not isinstance(got, dict):
        return None
    text = lambda v: v.strip() if isinstance(v, str) and v.strip() else ""  # noqa: E731
    opts = [{"text": text(o.get("text")), "right": o.get("right") is True, "why": text(o.get("why"))}
            for o in got.get("options") or [] if isinstance(o, dict)]
    if not text(got.get("scene")) or not text(got.get("question")) or len(opts) != OPTIONS:
        return None
    if sum(o["right"] for o in opts) != 1 or not all(o["text"] for o in opts):
        return None
    if len({" ".join(o["text"].casefold().split()) for o in opts}) != OPTIONS:
        return None
    return {"scene": text(got["scene"]), "question": text(got["question"]), "options": opts, "principle": text(got.get("principle"))}


def ask(conn: sqlite3.Connection, cfg: Config, lesson_id: int, *, runner=None) -> dict:
    """The question a study battle asks, with OPTIONS options written for this lesson: the right one and plausible wrong
    ones about the same problem (its ruled-out leads first), each with why. Written once by the analysis model and kept
    with the lesson. {scene, question, options: [{text, right, why}], principle}."""
    row = _lesson_row(conn, lesson_id)
    if row is None:
        raise StudyError("no such lesson")
    kept = loads(kv_get(conn, ASK_KEY + str(lesson_id)), None)
    if isinstance(kept, dict) and kept.get("v") == ASK_VERSION and (got := _clean(kept)) is not None:
        return got
    m = material(row["case_json"])
    r = _runner(cfg, runner)
    form = FORMS.get(row["kind"], FORMS["learning"])
    case = "".join(f"<{k}>{m[k]}</{k}>\n" for k in ("scene", "question", "answer", "principle") if m.get(k))
    leads = "".join(f"<ruled_out lead=\"{x['lead']}\">{x.get('why') or ''}</ruled_out>\n" for x in m.get("ruled_out", []))
    prompt = (f"<lesson kind=\"{row['kind']}\" project=\"{row['project_name'] or ''}\">\n<title>{row['title']}</title>\n"
              f"<body>\n{(row['body'] or '')[:BODY_MAX]}\n</body>\n"
              + (f"<case_file>\n{case}{leads}</case_file>\n" if case or leads else "")
              + f"</lesson>\n\nWrite the scene, the question, its {OPTIONS} options and the principle.")
    got = None
    for _ in range(2):  # one more try when the options don't hold together
        res = r.run(prompt, ASK_SCHEMA, system=_system(cfg, ASK_SYSTEM.replace("{form}", form)),
                    model=cfg.analysis.screen_model, effort="low", timeout=120)
        got = _clean(res.data)
        if got is not None:
            break
    if got is None:
        raise StudyError(NO_MODEL.replace("{reason}", "no usable question came back"))
    if not got["principle"] and m.get("principle"):
        got["principle"] = m["principle"]
    kv_set(conn, ASK_KEY + str(lesson_id), json.dumps({"v": ASK_VERSION, **got}, ensure_ascii=False))
    conn.commit()
    return got


def pick(conn: sqlite3.Connection, cfg: Config, scope_key: str, lesson_id: int, choice, *, here: bool,
         runner=None, today: date | None = None) -> dict:
    """The person's pick among a lesson's options, kept in their own record (on this computer only: `here` False
    refuses): the right one is "got it", any other "not yet". {verdict, right (its index), answer (the right option),
    feedback (why the pick is right or wrong), principle, back_in, owned}. The lesson must be one the caller checked is
    the person's."""
    if not here:
        raise StudyError(NOT_HERE)
    q = ask(conn, cfg, lesson_id, runner=runner)
    if not isinstance(choice, int) or isinstance(choice, bool) or not 0 <= choice < len(q["options"]):
        raise StudyError(NO_CHOICE)
    right = next(i for i, o in enumerate(q["options"]) if o["right"])
    verdict = "got_it" if choice == right else "not_yet"
    day = today or utcnow().date()
    with kv_transaction(conn):
        kept = studied_of(conn, scope_key) + [{"id": lesson_id, "on": day.isoformat(), "verdict": verdict}]
        kv_set(conn, STUDY_KEY + scope_key, json.dumps(kept[-STUDY_MAX:]))
    sched = schedule(kept, day).get(lesson_id)
    return {"verdict": verdict, "right": right, "answer": q["options"][right]["text"], "feedback": q["options"][choice]["why"],
            "principle": q["principle"], "back_in": (date.fromisoformat(sched["due"]) - day).days if sched else 0,
            "owned": bool(sched and sched["owned"])}
