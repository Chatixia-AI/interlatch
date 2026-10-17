"""The Party: each coding agent as a character in a small RPG, levelled by the lessons its sessions produced, matched
to the modules of a technical career roadmap (roadmap.json beside this file: generic content, no one's own notes).

What it shows is knowledge coverage, what the agents' lessons speak to on the roadmap, never the person's own level:
the roadmap keeps that separate. Evidence for a milestone is found here; only the person marks one done.

Counting:
- a lesson counts when it is active, a fix, gotcha, decision, learning, pattern or command, past `wip`, and from a
  session of a coding agent (never an imported chat) in the viewer's scope (scope());
- it earns STAGE_XP for its stage in each of the modules (at most MAX_MODULES) whose keywords it hits most;
- a module's skill level, the agent's character level and tier, its attributes (XP per attr) and its class (the track
  its modules cover best) all follow from those XP (skill_level, char_level, tier_of, coverage).

Scope: on a personal dashboard, and at a hub computer itself, this computer's sessions; for a person signed in to a
hub, their own computers' sessions only. A teammate's session never levels your agents. A person limited to projects
reads through access.limit's views, so their party is built from those projects' lessons (and finds no artifacts).

compute() is cached in this process per scope, the lessons as they stand (count, last change, stages), the quests and
goal kept for that scope, the catalog and the language.

The Adventure (adventure_map, encounter, set_cleared) lays the same lessons out as encounters on a map; battles change
no level or XP, they only mark a lesson cleared in the viewer's own log. A first clear also pays gold and maybe loot
into the viewer's bag (bag.py: a game economy that changes only battle numbers, never a level).
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ..core.agents import CODING_AGENTS, full_name, short_name
from ..core.i18n import lang
from ..core.util import loads, utcnow, utcnow_iso
from ..storage.kv import kv_get, kv_set, kv_transaction

CATALOG_PATH = Path(__file__).with_name("roadmap.json")
KINDS = ("fix", "gotcha", "decision", "learning", "pattern", "command")
STAGE_XP = {"provisional": 1, "established": 3, "canonical": 6}  # wip never counts
MAX_MODULES = 3  # a lesson supports at most a few modules: one lesson can't level everything
HEAD_WEIGHT = 2  # a hit in a lesson's title or tags counts double one in its body
BODY_HITS = 2  # without one there, a module needs this many in the body: one incidental word levels nothing
CLASS_COUNTED = 10  # an agent has a class once this many of its lessons earned XP...
CLASS_COVERAGE = 0.05  # ...and its best track is covered this much; before that it only leans towards some
FULL_XP = 51  # a module at skill level 5 covers its part of a track fully
TIERS = ((25, "master"), (12, "expert"), (5, "adept"), (1, "novice"))
EVIDENCE_KINDS = ("pr", "commit", "doc", "page", "published", "deck", "diagram")
EVIDENCE_MAX = 3
SIDE_TRACKS = 3
JOURNEY_SKILLS = 120  # the journey keeps every join, level, class and quest, and this many of the newest skill-ups
RECENT_DAYS = 7  # "recent": the counted lessons of today and the 6 days before (the journey's dates, in UTC)...
RECENT_MAX = 60  # ...newest first, at most this many
SKILL_LESSONS_MAX = 200
GOAL_KEY = "party.goal:"  # + scope key: the track the person picked
QUESTS_KEY = "party.quests:"  # + scope key: {milestone id: {"at": iso}}, the milestones the person marked done
CLEARED_KEY = "party.cleared:"  # + scope key: [lesson id, ...], the Adventure's encounters the person has beaten
CLEARED_MAX = 100_000
MAP_TITLE_MAX = 140  # the Adventure map's lesson titles are cut to this; an encounter has the whole title
ENCOUNTER_BODY_MAX = 4000


class PartyError(ValueError):
    """Something asked of the Party that isn't in its catalog: the message is an English template (tr())."""


# ------------------------------------------------------------------ keywords
# A module's keywords are one regex (case-insensitive) of many alternatives, most a word like \bpytest\b. Running all
# 70 over every lesson tries each alternative at every position: seconds on a few thousand lessons. So each
# alternative also says what a text must contain for it to match at all (its literal start: a word starting so, or a
# substring), the catalog indexes those, and a lesson runs only the alternatives its own words make possible, in their
# order. That is the same regex wherever it can match, so the hits are the same.
_WORD = re.compile(r"\w+")
_WORD_KEY = 4  # word starts are indexed by up to this many characters
_LOOK = ("(?=", "(?!", "(?<=", "(?<!")
_PLAIN = set("()[]{}.^$|?*+\\")
_REPEAT = re.compile(r"\{\d*(?:,\d*)?\}")  # a {m,n} quantifier
_ESCAPE = re.compile(r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|N\{[^}]*\}|[0-7]{1,3}|.)", re.S)
_SCOPED = re.compile(r"\(\?[aiLmsux]*(?:-[imsx]+)?:")  # a group with flags of its own, such as (?-i:...)
_BAD = re.compile(r"\\[1-9]|\(\?P=|\(\?\(|^\(\?[aiLmsux-]+\)")  # backreferences, conditionals, global flags


def _skip_class(p: str, j: int) -> int:
    """The index after the character class that starts at p[j] ("[")."""
    j += 1
    if j < len(p) and p[j] == "^":
        j += 1
    if j < len(p) and p[j] == "]":
        j += 1
    while j < len(p) and p[j] != "]":
        j += 2 if p[j] == "\\" else 1
    return j + 1


def _alternatives(p: str) -> list[list[str]]:
    """The top-level alternatives of a pattern, each as tokens: an escape, a character class, a whole group, or one
    character (quantifiers are tokens of their own)."""
    out, cur, i = [], [], 0
    while i < len(p):
        c = p[i]
        if c == "\\":
            esc = _ESCAPE.match(p, i)  # \x22 is one character, not \x then 22
            end = esc.end() if esc else i + 2
            cur.append(p[i:end])
            i = end
        elif c == "[":
            j = _skip_class(p, i)
            cur.append(p[i:j])
            i = j
        elif c == "(":
            depth, j = 1, i + 1
            while j < len(p) and depth:
                if p[j] == "\\":
                    j += 2
                elif p[j] == "[":
                    j = _skip_class(p, j)
                else:
                    depth += {"(": 1, ")": -1}.get(p[j], 0)
                    j += 1
            cur.append(p[i:j])
            i = j
        elif c == "|":
            out.append(cur)
            cur = []
            i += 1
        elif c == "{" and (q := _REPEAT.match(p, i)):
            cur.append(q.group(0))
            i = q.end()
        else:
            cur.append(c)
            i += 1
    out.append(cur)
    return out


def _literal(tok: str) -> str | None:
    """The text a token matches as itself, casefolded (so it compares with casefolded text), or None."""
    if len(tok) == 1 and tok not in _PLAIN:
        return tok.casefold()
    if len(tok) == 2 and tok[0] == "\\" and not tok[1].isalnum():
        return tok[1]
    return None


def _optional(after: str) -> bool:
    """The token before `after` may match nothing (? * {0,…})."""
    return after[:1] in ("?", "*") or after.startswith(("{0", "{,"))


def _group_body(tok: str) -> str | None:
    """What a group token holds, when one of its alternatives must match where it stands; None for anything else
    (lookarounds, conditionals: never here)."""
    if tok.startswith("(?:"):
        return tok[3:-1]
    if tok.startswith("(?P<") and ">" in tok:
        return tok[tok.index(">") + 1:-1]
    if tok.startswith("(") and not tok.startswith("(?"):
        return tok[1:-1]
    scoped = _SCOPED.match(tok)
    return tok[scoped.end():-1] if scoped else None


def _need(toks: list[str], word: bool = False) -> list[tuple[str, str]] | None:
    """What a text must contain for this alternative to match, as any one of: ("w", start) a word beginning so, or
    ("s", text) a substring. None when that can't be told (it is then tried on every text)."""
    i = 0
    while i < len(toks) and (toks[i] in ("\\b", "^") or toks[i].startswith(_LOOK)):  # zero-width: skipped
        word = word or toks[i] == "\\b"
        i += 1
    lead = _lead(toks, i, word)
    if lead is not None:
        return lead
    run = _longest_run(toks, i)
    return [("s", run)] if run else None


def _lead(toks: list[str], i: int, word: bool) -> list[tuple[str, str]] | None:
    """What the alternative starts with (from toks[i]): its literal characters, or one of a group's alternatives."""
    if i >= len(toks):
        return None
    body = _group_body(toks[i])
    if body is not None:
        if _optional(toks[i + 1] if i + 1 < len(toks) else ""):
            return None
        out = []
        for alt in _alternatives(body):
            got = _need(alt, word)
            if got is None:
                return None
            out += got
        return out
    prefix = ""
    while i < len(toks) and (c := _literal(toks[i])) is not None:
        after = toks[i + 1] if i + 1 < len(toks) else ""
        if _optional(after):
            break
        prefix += c
        if after[:1] in ("+", "{"):
            break
        i += 1
    if not prefix:
        return None
    start = _WORD.match(prefix)
    if word and start:  # \b then a word character: the match begins a word, so one of the text's words starts so
        return [("w", start.group(0)[:_WORD_KEY])]
    return [("s", prefix)]


def _longest_run(toks: list[str], i: int) -> str:
    """The longest run of literal characters every match contains (at least 2), or ""."""
    best = run = ""
    while i < len(toks):
        after = toks[i + 1] if i + 1 < len(toks) else ""
        quantified = after[:1] in ("?", "*", "+", "{")
        c = _literal(toks[i])
        if c is None or _optional(after):
            run = ""
        else:
            run += c
            if quantified:  # at least once, maybe more: what follows comes after its last one
                best, run = max(best, run, key=len), c
        best = max(best, run, key=len)
        i += 2 if quantified else 1
    return best if len(best) >= 2 else ""


def _lowercase(p: str) -> str | None:
    """A pattern for lowercased text: what it matches literally lowercased, its escapes kept (\\S stays \\S)."""
    out, i = [], 0
    while i < len(p):
        if p[i] == "\\":
            esc = _ESCAPE.match(p, i)
            end = esc.end() if esc else i + 2
            out.append(p[i:end])
            i = end
        else:
            low = p[i].lower()
            if len(low) != 1:
                return None
            out.append(low)
            i += 1
    return "".join(out)


def _lowered(toks: list[str]) -> re.Pattern | None:
    """An alternative as a case-sensitive regex for lowercased text, its leading \\b moved behind its first letters
    (\\bretry → retry(?<=\\bretry)): the same matches, but the regex engine can jump to where those letters are
    instead of trying every position, about ten times faster. None when it has flags of its own ((?-i:...))."""
    if "(?-" in "".join(toks):
        return None
    low = [_lowercase(t) for t in toks]
    if None in low:
        return None
    if len(low) > 1 and low[0] == "\\b":
        j = 1
        while j < len(low) and _literal(low[j]) is not None and (low[j + 1] if j + 1 < len(low) else "")[:1] not in (
                "?", "*", "+", "{"):
            j += 1
        if j > 1:
            run = "".join(low[1:j])
            low = [run, f"(?<=\\b{run})", *low[j:]]
    try:
        return re.compile("".join(low))
    except re.error:
        return None


class Keywords:
    """One keyword regex, and its alternatives, each compiled on its own when first needed: a text runs only the
    ones Catalog.candidates() says may match it, as the whole regex would (the first alternative that matches at the
    leftmost place wins, and the next match starts after it). Alternatives run on the lowercased text when they can
    (_lowered), else case-insensitively on the text itself."""

    def __init__(self, pattern: str, owner: str):
        try:
            self.exact = re.compile(pattern, re.I)
        except re.error as e:
            raise PartyError(f"roadmap.json: {owner} has keywords that don't compile: {e}") from None
        self._tokens = None if _BAD.search(pattern) else _alternatives(pattern)
        self.alternatives = ["".join(a) for a in self._tokens] if self._tokens else None
        self.needs = [_need(a) for a in self._tokens] if self._tokens else [None]
        self._fast: list = [None] * len(self.needs)  # per alternative: _lowered(), False when it can't be
        self._slow: list[re.Pattern | None] = [None] * len(self.needs)

    def _some(self, which: tuple[int, ...]) -> tuple[list[re.Pattern], bool]:
        """The regexes for alternatives `which`, and whether they run on the lowercased text."""
        if self.alternatives is None:
            return [self.exact], False
        fast = []
        for w in which:
            if self._fast[w] is None:
                self._fast[w] = _lowered(self._tokens[w]) or False
            if self._fast[w] is False:
                break
            fast.append(self._fast[w])
        else:
            return fast, True
        slow = []  # one can't run lowercased: all of them on the text itself, so their places compare
        for w in which:
            if self._slow[w] is None:
                try:
                    self._slow[w] = re.compile(self.alternatives[w], re.I)
                except re.error:  # can't stand alone after all: the whole regex, from now on
                    self.alternatives = None
                    return [self.exact], False
            slow.append(self._slow[w])
        return slow, False

    def count(self, text: str, low: str, which: tuple[int, ...]) -> int:
        """How many (non-empty) matches the regex finds in `text` (`low`: text.lower()), running only alternatives
        `which`, in order."""
        alts, lowered = self._some(which)
        if lowered:
            text = low
        if len(alts) == 1:
            return sum(1 for m in alts[0].finditer(text) if m.group(0))
        nxt = [rx.search(text) for rx in alts]
        pos = n = 0
        while pos <= len(text):
            best = None
            for i, m in enumerate(nxt):
                if m is not None and m.start() < pos:
                    m = nxt[i] = alts[i].search(text, pos)
                if m is not None and (best is None or m.start() < best.start()):
                    best = m  # leftmost; at the same place, the earlier alternative
            if best is None:
                break
            if best.end() > best.start():
                n += 1
                pos = best.end()
            else:
                pos = best.end() + 1
        return n

    def found(self, text: str, low: str, which: tuple[int, ...]) -> bool:
        alts, lowered = self._some(which)
        return any(rx.search(low if lowered else text) is not None for rx in alts)


# ------------------------------------------------------------------ the catalog (roadmap.json)
class Catalog:
    """roadmap.json, with its keywords compiled and indexed and its lists indexed."""

    def __init__(self, raw: dict, key: tuple):
        self.raw, self.key = raw, key  # key: what the cached matches depend on (path, mtime, size)
        self.attrs = list(raw.get("attrs") or [])
        self.categories = {c["id"]: c for c in raw.get("categories") or []}
        self.modules = list(raw.get("modules") or [])
        self.module = {m["id"]: m for m in self.modules}
        self.module_index = {m["id"]: i for i, m in enumerate(self.modules)}
        # keywords by key: a module's index, or a milestone's id
        self.keywords: dict = {i: Keywords(m["keywords"], m["id"]) for i, m in enumerate(self.modules) if m.get("keywords")}
        self.tracks = list(raw.get("tracks") or [])
        self.track = {t["id"]: t for t in self.tracks}
        self.track_index = {t["id"]: i for i, t in enumerate(self.tracks)}
        self.milestone: dict[str, tuple[dict, int, dict]] = {}  # id: (track, n, milestone)
        self.weights: list[list[tuple[int, int]]] = []  # per track: (module index, weight) for each of its modules
        for t in self.tracks:
            critical = set(t.get("critical") or [])
            self.weights.append([(self.module_index[m], 2 if m in critical else 1)
                                 for m in t.get("modules") or [] if m in self.module_index])
            for n, ms in enumerate(t.get("milestones") or [], 1):
                self.milestone[ms["id"]] = (t, n, ms)
                if ms.get("keywords"):
                    self.keywords[ms["id"]] = Keywords(ms["keywords"], ms["id"])
        self.by_word: dict[str, list[tuple]] = {}  # a word start: the (keywords, alternative) it makes possible
        self.by_text: dict[str, list[tuple]] = {}  # a substring: the same
        self.always: list[tuple] = []  # alternatives tried on every text
        for key, kw in self.keywords.items():
            for n, need in enumerate(kw.needs):
                if need is None:
                    self.always.append((key, n))
                for kind, text in need or ():
                    (self.by_word if kind == "w" else self.by_text).setdefault(text, []).append((key, n))
        self._by_text_ascii = [(t, v) for t, v in self.by_text.items() if t.isascii()]
        self._word_keys = frozenset(self.by_word)
        self._lengths = sorted({len(k) for k in self.by_word})

    def candidates(self, text: str) -> dict:
        """{keywords key: the alternatives (indexes, in order) that may match `text`}; keywords that can't, left out."""
        low = text.casefold()
        found: dict = defaultdict(list)
        for key, n in self.always:
            found[key].append(n)
        words = set(_WORD.findall(low))
        for start in {w[:k] for w in words for k in self._lengths} & self._word_keys:  # w[:k] of a shorter word: w
            for key, n in self.by_word[start]:
                found[key].append(n)
        for sub, keys in self._by_text_ascii if low.isascii() else self.by_text.items():
            if sub in low:
                for key, n in keys:
                    found[key].append(n)
        return {key: tuple(sorted(set(ns))) for key, ns in found.items()}


_catalogs: dict[str, Catalog] = {}
_lock = threading.Lock()


def load_catalog(path=None) -> Catalog:
    """The catalog at `path` (default: roadmap.json beside this file), read again only when the file changed."""
    p = Path(path) if path is not None else CATALOG_PATH
    st = p.stat()
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _catalogs.get(key[0])
    if hit is not None and hit.key == key:
        return hit
    cat = Catalog(json.loads(p.read_text(encoding="utf-8")), key)
    with _lock:
        _catalogs[key[0]] = cat
    return cat


def _t(obj: dict | None, key: str):
    """obj[key] in the dashboard's language: its `<key>_ja` when the dashboard is in Japanese and the catalog has one."""
    if not obj:
        return None
    if lang.get() == "ja" and obj.get(f"{key}_ja"):
        return obj[f"{key}_ja"]
    return obj.get(key)


# ------------------------------------------------------------------ the formulas
def skill_threshold(level: int) -> int:
    """The XP a module needs for skill `level`: L1 at 1, L2 at 6, L3 at 16, L4 at 31, L5 at 51, L10 at 226."""
    return 5 * level * (level - 1) // 2 + 1


def skill_level(xp: int) -> int:
    """The largest L with skill_threshold(L) <= xp; 0 for no XP."""
    if xp < 1:
        return 0
    level = max(1, int((1 + math.sqrt(1 + 8 * (xp - 1) / 5)) / 2))
    while skill_threshold(level + 1) <= xp:
        level += 1
    while level > 1 and skill_threshold(level) > xp:
        level -= 1
    return level


def char_level(xp: int) -> int:
    """floor(sqrt(xp / 3)) + 1: level L from 3·(L-1)² XP."""
    return math.isqrt(max(int(xp), 0) // 3) + 1


def char_threshold(level: int) -> int:
    return 3 * (level - 1) ** 2


def tier_of(level: int) -> str:
    """novice 1-4, adept 5-11, expert 12-24, master 25 and up."""
    return next(name for floor_, name in TIERS if level >= floor_)


def coverage(cat: Catalog, mxp: list[int]) -> list[tuple[float, int]]:
    """(coverage, track index) for each track the module XP `mxp` covers at all, best first. A track's coverage:
    sum(w · min(xp / FULL_XP, 1)) / sum(w) over its modules, w 2 for a critical gate else 1. Ranked by coverage (to 3
    decimals), then by the same weighted mean of the XP uncapped (a well-covered agent covers many tracks fully: the
    one it went deepest in comes first), then catalog order."""
    ranked = []
    for ti, ws in enumerate(cat.weights):
        total = sum(w for _, w in ws)
        if total:
            got = sum(w * min(mxp[i] / FULL_XP, 1.0) for i, w in ws) / total
            if got > 0:
                ranked.append((-round(got, 3), -sum(w * mxp[i] for i, w in ws) / total, ti, got))
    ranked.sort()
    return [(got, ti) for _, _, ti, got in ranked]


def class_of(covs: list[tuple[float, int]], counted: int) -> tuple[float, int] | None:
    """The agent's class, from coverage() (best first): its best track once CLASS_COUNTED lessons earned XP and that
    track is covered CLASS_COVERAGE; None before."""
    if counted >= CLASS_COUNTED and covs and round(covs[0][0], 3) >= CLASS_COVERAGE:
        return covs[0]
    return None


def _level_marked(level: int) -> bool:  # the character levels the journey tells: each up to 10, then every 5th
    return level <= 10 or level % 5 == 0


def _skill_marked(level: int) -> bool:  # the skill levels it tells: 1, 3, 5, 7, 10, then every 5th
    return level in (1, 3, 5, 7, 10) or (level > 10 and level % 5 == 0)


# ------------------------------------------------------------------ whose lessons
def scope_key(viewer: dict | None) -> str:
    """Where the quests and goal are kept: "here" (this computer, and a hub computer itself) or "person:<id>"."""
    return "here" if viewer is None else f"person:{int(viewer['id'])}"


def scope(viewer: dict | None, here_id: str) -> tuple[str, tuple]:
    """SQL on `sessions s` for the sessions whose lessons level the viewer's agents, and its parameters: this
    computer's (a session with no machine recorded is this computer's), or the person's own computers'."""
    if viewer is None:
        return "COALESCE(s.machine_id, ?) = ?", (here_id, here_id)
    return "COALESCE(s.machine_id, ?) IN (SELECT id FROM machines WHERE person_id = ?)", (here_id, int(viewer["id"]))


_AGENTS_SQL = ", ".join("?" * len(CODING_AGENTS))
_KINDS_SQL = ", ".join("?" * len(KINDS))
_LESSONS_WHERE = (f"COALESCE(k.status, 'active') = 'active' AND k.kind IN ({_KINDS_SQL}) "
                  f"AND COALESCE(k.stage, 'provisional') != 'wip' AND COALESCE(s.agent, k.agent) IN ({_AGENTS_SQL})")
_ACTIVE_WHERE = f"COALESCE(k.status, 'active') = 'active' AND COALESCE(s.agent, k.agent) IN ({_AGENTS_SQL})"
# a lesson's part of the fingerprint: what a change of stage or kind changes, which no date records
_STAGE_SQL = (f"(CASE COALESCE(k.stage, 'provisional') WHEN 'canonical' THEN 6 WHEN 'established' THEN 3 WHEN 'wip' "
              f"THEN 7 ELSE 1 END) * (CASE WHEN k.kind IN ({_KINDS_SQL}) THEN 1 ELSE 2 END)")


@dataclass(frozen=True)
class Lesson:
    id: int
    agent: str
    session_id: str | None
    title: str
    kind: str
    stage: str
    at: str  # when: its session's start (the lesson's own time when there is none), as an ISO timestamp
    created_at: str | None
    project_name: str | None
    head: str  # its title and tags
    body: str
    xp: int  # per module it counts toward
    modules: tuple[int, ...]  # module indexes, best score first


_candidates: dict[tuple, dict] = {}
_matches: dict[tuple, tuple[int, ...]] = {}
_milestone_hits: dict[tuple, bool] = {}


def _cached(cache: dict, key, make, limit: int = 100_000):
    hit = cache.get(key)
    if hit is None:
        if len(cache) >= limit:
            cache.clear()
        hit = cache[key] = make()
    return hit


def _candidates_of(cat: Catalog, text: str) -> dict:
    return _cached(_candidates, (cat.key, hash(text), len(text)), lambda: cat.candidates(text), 20_000)


def keyword_hits(cat: Catalog, text: str) -> dict[int, int]:
    """{module index: how many times the text hits its keywords} for the modules it hits at all."""
    out, low = {}, text.lower()
    for key, which in _candidates_of(cat, text).items():
        if isinstance(key, int):
            n = cat.keywords[key].count(text, low, which)
            if n:
                out[key] = n
    return out


def modules_of(cat: Catalog, head: str, body: str) -> tuple[int, ...]:
    """The modules a lesson counts toward: of those it hits in its title or tags (`head`), or BODY_HITS times in its
    body, the MAX_MODULES that score most (HEAD_WEIGHT per head hit, 1 per body hit; ties: catalog order)."""
    in_head, in_body = keyword_hits(cat, head), keyword_hits(cat, body)
    scored = sorted((-(HEAD_WEIGHT * in_head.get(i, 0) + in_body.get(i, 0)), i) for i in in_head.keys() | in_body.keys()
                    if in_head.get(i, 0) >= 1 or in_body.get(i, 0) >= BODY_HITS)
    return tuple(i for _, i in scored[:MAX_MODULES])


def _modules_of(cat: Catalog, lesson_id: int, head: str, body: str) -> tuple[int, ...]:
    return _cached(_matches, (cat.key, lesson_id, hash(head), hash(body)), lambda: modules_of(cat, head, body))


def milestone_hits(cat: Catalog, ms_id: str, text: str) -> int:
    """How many times `text` hits a milestone's keywords."""
    def make():
        which = _candidates_of(cat, text).get(ms_id)
        return cat.keywords[ms_id].count(text, text.lower(), which) if which else 0

    if ms_id not in cat.keywords or not text:
        return 0
    return _cached(_milestone_hits, (cat.key, ms_id, hash(text), len(text)), make, 200_000)


def _shows_milestone(cat: Catalog, ms_id: str, lesson: Lesson) -> bool:
    """A lesson is evidence for a milestone as it would count toward a module: a hit in its title or tags, or
    BODY_HITS in its body."""
    return milestone_hits(cat, ms_id, lesson.head) >= 1 or milestone_hits(cat, ms_id, lesson.body) >= BODY_HITS


def _fingerprint(conn: sqlite3.Connection, where: str, params: tuple) -> tuple:
    """What the party depends on, cheaply: the archive, the lessons in scope (how many, the last change, their stages,
    which a stage change doesn't date), the sessions in scope and the artifacts."""
    lessons = conn.execute(
        f"SELECT COUNT(*), MAX(k.updated_at), MAX(k.id), TOTAL(k.id * {_STAGE_SQL}) FROM knowledge k "
        f"JOIN sessions s ON s.id = k.session_id WHERE {_ACTIVE_WHERE} AND {where}",
        (*KINDS, *CODING_AGENTS, *params)).fetchone()
    sessions = conn.execute(f"SELECT COUNT(*), MAX(s.started_at) FROM sessions s WHERE s.agent IN ({_AGENTS_SQL}) "
                            f"AND {where}", (*CODING_AGENTS, *params)).fetchone()
    artifacts = conn.execute("SELECT COUNT(*), MAX(id) FROM artifacts").fetchone()
    archive = conn.execute("PRAGMA database_list").fetchone()[2]  # which archive: tests open several in one process
    return (archive, *lessons, *sessions, *artifacts)


def _load_lessons(conn: sqlite3.Connection, cat: Catalog, where: str, params: tuple) -> list[Lesson]:
    rows = conn.execute(
        "SELECT k.id, COALESCE(s.agent, k.agent) AS agent, k.session_id, k.title, k.body, k.tags_json, k.kind, "
        "COALESCE(k.stage, 'provisional') AS stage, k.created_at, k.project_name, "
        "COALESCE(s.started_at, k.created_at, '') AS at FROM knowledge k JOIN sessions s ON s.id = k.session_id "
        f"WHERE {_LESSONS_WHERE} AND {where} ORDER BY at, k.id", (*KINDS, *CODING_AGENTS, *params)).fetchall()
    out = []
    for r in rows:
        head, body = f"{r['title'] or ''} {r['tags_json'] or ''}", r["body"] or ""
        out.append(Lesson(id=r["id"], agent=r["agent"], session_id=r["session_id"], title=r["title"] or "",
                          kind=r["kind"], stage=r["stage"], at=r["at"] or "", created_at=r["created_at"],
                          project_name=r["project_name"], head=head, body=body, xp=STAGE_XP.get(r["stage"], 1),
                          modules=_modules_of(cat, r["id"], head, body)))
    return out


# ------------------------------------------------------------------ the cache
_lesson_cache: dict[tuple, list[Lesson]] = {}
_party_cache: dict[tuple, dict] = {}
CACHE_MAX = 32


def _remember(cache: dict, key, value):
    with _lock:
        if len(cache) >= CACHE_MAX:
            cache.clear()
        cache[key] = value
    return value


@dataclass(frozen=True)
class _Scope:
    key: str  # scope_key
    where: str
    params: tuple
    finger: tuple  # _fingerprint, plus who sees which projects


def _scope_of(conn: sqlite3.Connection, here_id: str, viewer: dict | None) -> _Scope:
    where, params = scope(viewer, here_id)
    # a person limited to projects reads through views of those projects: which ones is part of what they see
    seen = None if viewer is None else (viewer.get("role"), viewer.get("projects_json"))
    return _Scope(scope_key(viewer), where, params, (*_fingerprint(conn, where, params), seen))


def lessons_in(conn: sqlite3.Connection, cat: Catalog, sc: _Scope) -> list[Lesson]:
    """The lessons that count for the scope, oldest first, each with the modules it counts toward."""
    key = (sc.key, sc.finger, cat.key)
    with _lock:
        hit = _lesson_cache.get(key)
    if hit is not None:
        return hit
    return _remember(_lesson_cache, key, _load_lessons(conn, cat, sc.where, sc.params))


def clear_cache() -> None:
    with _lock:
        _lesson_cache.clear()
        _party_cache.clear()
        _matches.clear()
        _milestone_hits.clear()
        _candidates.clear()


# ------------------------------------------------------------------ quests and the goal (the person's own log)
def quests_done(conn: sqlite3.Connection, key: str) -> dict:
    got = loads(kv_get(conn, QUESTS_KEY + key), {})
    return {k: v for k, v in got.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(got, dict) else {}


def goal_of(conn: sqlite3.Connection, key: str) -> str | None:
    return kv_get(conn, GOAL_KEY + key) or None


def set_quest(conn: sqlite3.Connection, viewer: dict | None, milestone_id, done: bool, *, catalog: Catalog | None = None) -> None:
    """Mark a milestone done (or not) in the viewer's own log: evidence is found, but only the person says it's done."""
    cat = catalog or load_catalog()
    if not isinstance(milestone_id, str) or milestone_id not in cat.milestone:
        raise PartyError("no such quest")
    key = scope_key(viewer)
    log = quests_done(conn, key)
    if done:
        log.setdefault(milestone_id, {"at": utcnow_iso()})
    else:
        log.pop(milestone_id, None)
    kv_set(conn, QUESTS_KEY + key, json.dumps(log, sort_keys=True))
    conn.commit()


def set_goal(conn: sqlite3.Connection, viewer: dict | None, track_id, *, catalog: Catalog | None = None) -> None:
    """The track the viewer's main quest follows; None goes back to the default (the strongest agent's class)."""
    cat = catalog or load_catalog()
    key = scope_key(viewer)
    if track_id is None:
        conn.execute("DELETE FROM kv WHERE key = ?", (GOAL_KEY + key,))
    elif isinstance(track_id, str) and track_id in cat.track:
        kv_set(conn, GOAL_KEY + key, track_id)
    else:
        raise PartyError("no such track")
    conn.commit()


# ------------------------------------------------------------------ the party
def _week(at: str) -> tuple[str, str] | None:
    """(ISO week as 2026-W41, its Monday) for a timestamp."""
    try:
        d = date.fromisoformat(at[:10])
    except ValueError:
        return None
    y, w, wd = d.isocalendar()
    return f"{y}-W{w:02d}", (d - timedelta(days=wd - 1)).isoformat()


def _track_ref(cat: Catalog, ti: int, cov: float, *, full: bool = False) -> dict:
    t = cat.tracks[ti]
    out = {"id": t["id"], "name": _t(t, "name"), "coverage": round(cov, 3)}
    if full:
        cg = cat.categories.get(t.get("category"))
        out = {"id": t["id"], "name": _t(t, "name"), "category": t.get("category"), "category_name": _t(cg, "name"),
               "coverage": round(cov, 3)}
    return out


class _Tally:
    """One agent's XP as lessons are added."""

    def __init__(self, n_modules: int):
        self.mxp = [0] * n_modules
        self.mlessons = [0] * n_modules
        self.xp = 0
        self.counted = 0  # lessons that earned XP

    def add(self, lesson: Lesson) -> None:
        if lesson.modules:
            self.counted += 1
        for i in lesson.modules:
            self.mxp[i] += lesson.xp
            self.mlessons[i] += 1
            self.xp += lesson.xp


def _agent_out(cat: Catalog, agent: str, tally: _Tally, sessions: dict) -> tuple[dict, list]:
    level = char_level(tally.xp)
    covs = coverage(cat, tally.mxp)
    attrs = {a["id"]: 0 for a in cat.attrs}
    skills = []
    for i, xp in enumerate(tally.mxp):
        if not xp:
            continue
        m = cat.modules[i]
        attrs[m.get("attr")] = attrs.get(m.get("attr"), 0) + xp
        sl = skill_level(xp)
        skills.append({"module": m["id"], "name": _t(m, "name"), "attr": m.get("attr"), "level": sl, "xp": xp,
                       "level_xp": skill_threshold(sl), "next_xp": skill_threshold(sl + 1), "lessons": tally.mlessons[i]})
    skills.sort(key=lambda s: (-s["xp"], cat.module_index[s["module"]]))
    s = sessions.get(agent) or {}
    return {
        "agent": agent, "name": full_name(agent), "short": short_name(agent),
        "level": level, "xp": tally.xp, "level_xp": char_threshold(level), "next_xp": char_threshold(level + 1),
        "tier": tier_of(level), "sessions": s.get("n", 0), "lessons": s.get("lessons", 0), "counted": tally.counted,
        "since": (s.get("since") or "")[:10] or None, "last": (s.get("last") or "")[:10] or None,
        "attrs": attrs, "attr_levels": {k: skill_level(v) for k, v in attrs.items()},
        "class": _track_ref(cat, cls[1], cls[0], full=True) if (cls := class_of(covs, tally.counted)) else None,
        "classes": [_track_ref(cat, ti, c) for c, ti in covs[:3]],  # also before it has a class: what it leans to
        "skills": skills,
    }, covs


def _artifacts(conn: sqlite3.Connection, where: str, params: tuple) -> list[dict]:
    """What the scope's sessions made that can show a milestone done, newest first, each file or link once."""
    rows = conn.execute(
        "SELECT a.id, a.key, a.kind, a.title, a.url, a.ts, s.agent FROM artifacts a JOIN sessions s ON s.id = a.session_id "
        f"WHERE a.kind IN ({', '.join('?' * len(EVIDENCE_KINDS))}) AND s.agent IN ({_AGENTS_SQL}) AND {where} "
        "AND COALESCE(a.title, '') != '' ORDER BY a.ts DESC, a.id DESC", (*EVIDENCE_KINDS, *CODING_AGENTS, *params)).fetchall()
    seen, out = set(), []
    for r in rows:
        if r["key"] in seen:
            continue
        seen.add(r["key"])
        url = r["url"] if isinstance(r["url"], str) and r["url"].startswith(("https://", "http://")) else None
        out.append({"id": r["id"], "kind": r["kind"], "title": r["title"][:200], "url": url, "at": r["ts"] or "",
                    "agent": r["agent"]})
    return out


def _quest(cat: Catalog, ms_id: str, *, main: bool, done: dict, artifacts: list[dict], newest: list[Lesson]) -> dict:
    track, n, ms = cat.milestone[ms_id]
    evidence = []
    titles: dict[str, int] = {}  # each title once: a commit and its pull request often share one, and the PR is kept
    for a in artifacts:
        if not milestone_hits(cat, ms_id, a["title"]):  # an artifact: its title
            continue
        item = {"kind": "artifact", "id": a["id"], "title": a["title"], "artifact_kind": a["kind"],
                "url": a["url"], "agent": a["agent"], "at": a["at"][:10] or None}
        same = titles.get(key := " ".join(a["title"].split()).casefold())
        if same is not None:
            if a["kind"] == "pr" and evidence[same]["artifact_kind"] != "pr":
                evidence[same] = item
            continue
        if len(evidence) >= EVIDENCE_MAX:
            continue  # later ones may still be a listed title's pull request
        titles[key] = len(evidence)
        evidence.append(item)
    for k in newest:
        if len(evidence) >= EVIDENCE_MAX:
            break
        if " ".join(k.title.split()).casefold() in titles:
            continue
        if _shows_milestone(cat, ms_id, k):
            evidence.append({"kind": "lesson", "id": k.id, "title": k.title, "agent": k.agent, "at": k.at[:10] or None,
                             "session_id": k.session_id})
    mark = done.get(ms_id)
    return {"id": ms_id, "track": track["id"], "track_name": _t(track, "name"), "main": main, "n": n,
            "text": _t(ms, "text"), "status": "done" if mark else "evidence" if evidence else "open",
            "done_at": mark.get("at") if mark else None, "evidence": evidence}


def _journey(cat: Catalog, lessons: list[Lesson], done: dict) -> tuple[list[dict], dict, int | None]:
    """The story so far, newest first (every join, level, class and quest; the newest JOURNEY_SKILLS skill-ups), each
    agent's level and XP week by week, and the module of the newest skill event."""
    tallies = {a: _Tally(len(cat.modules)) for a in CODING_AGENTS}
    level = dict.fromkeys(CODING_AGENTS, 1)
    cls: dict[str, int | None] = dict.fromkeys(CODING_AGENTS)
    series: dict[str, list] = {a: [] for a in CODING_AGENTS}
    events: list[tuple[str, int, dict]] = []

    def event(at: str, week: str, agent, kind: str, *, lvl=None, module=None, track=None, lesson=None, quest=None):
        m = cat.modules[module] if module is not None else None
        t = cat.tracks[track] if isinstance(track, int) else track
        events.append((at, len(events), {
            "at": at[:10], "week": week, "agent": agent, "type": kind, "level": lvl,
            "module": m["id"] if m else None, "module_name": _t(m, "name") if m else None,
            "track": t["id"] if t else None, "track_name": _t(t, "name") if t else None,
            "lesson": {"id": lesson.id, "title": lesson.title, "session_id": lesson.session_id} if lesson else None,
            "text": quest["text"] if quest else None, "quest": quest}))

    week = None
    newest_skill = None  # the module of the newest skill event (the walk goes oldest first)
    active: dict[str, Lesson] = {}  # agent: its last lesson in the week being walked

    def close(week: tuple[str, str]) -> None:
        for agent, last in active.items():
            got = class_of(coverage(cat, tallies[agent].mxp), tallies[agent].counted)
            now = got[1] if got else None
            if now is not None and now != cls[agent]:  # gained a class, or another one
                event(last.at, week[0], agent, "class", lvl=level[agent], track=now, lesson=last)
            cls[agent] = now
            series[agent].append([week[1], level[agent], tallies[agent].xp])
        active.clear()

    for k in lessons:
        if not k.modules:
            continue
        wk = _week(k.at)
        if wk is None:
            continue
        if week is not None and wk != week:
            close(week)
        week = wk
        tally = tallies[k.agent]
        if not tally.counted:
            event(k.at, wk[0], k.agent, "join", lvl=1, lesson=k)
        before = list(tally.mxp[i] for i in k.modules)
        tally.add(k)
        for i, old in zip(k.modules, before, strict=True):
            for sl in range(skill_level(old) + 1, skill_level(tally.mxp[i]) + 1):
                if _skill_marked(sl):
                    event(k.at, wk[0], k.agent, "skill", lvl=sl, module=i, lesson=k)
                    newest_skill = i
        now = char_level(tally.xp)
        for lv in range(level[k.agent] + 1, now + 1):
            if _level_marked(lv):
                event(k.at, wk[0], k.agent, "level", lvl=lv, lesson=k)
        level[k.agent] = now
        active[k.agent] = k
    if week is not None:
        close(week)
    for ms_id, mark in done.items():
        if ms_id in cat.milestone and isinstance(mark.get("at"), str) and (wk := _week(mark["at"])):
            track, _n, ms = cat.milestone[ms_id]
            event(mark["at"], wk[0], None, "quest", track=track, quest={"id": ms_id, "text": _t(ms, "text")})
    events.sort(key=lambda e: (e[0], e[1]), reverse=True)
    out, skills = [], 0
    for e in events:
        if e[2]["type"] == "skill":
            skills += 1
            if skills > JOURNEY_SKILLS:
                continue
        out.append(e[2])
    return out, series, newest_skill


def _recent(cat: Catalog, lessons: list[Lesson], today: date) -> tuple[list[dict], dict]:
    """The counted lessons of the last RECENT_DAYS days (today included), newest first, at most RECENT_MAX; and the
    whole week's tally (`week`), which the cap doesn't cut: its dates, and how many lessons each agent beat."""
    since, until = (today - timedelta(days=RECENT_DAYS - 1)).isoformat(), today.isoformat()
    out = []
    by_agent = {a: 0 for a in CODING_AGENTS}
    for k in reversed(lessons):  # oldest first, so newest first here
        if not k.modules or not since <= k.at[:10] <= until:
            continue
        by_agent[k.agent] += 1
        if len(out) < RECENT_MAX:
            out.append({"id": k.id, "title": k.title, "kind": k.kind, "stage": k.stage, "agent": k.agent,
                        "xp": k.xp * len(k.modules), "at": k.at[:10], "session_id": k.session_id,
                        "modules": [{"id": cat.modules[i]["id"], "name": _t(cat.modules[i], "name"),
                                     "attr": cat.modules[i].get("attr")} for i in k.modules]})
    return out, {"since": since, "until": until, "lessons": sum(by_agent.values()), "by_agent": by_agent}


def preview(conn: sqlite3.Connection, here_id: str, *, today: date | None = None) -> dict:
    """This week's lessons before anyone signs in: this computer's coding-agent lessons of the last RECENT_DAYS days
    (the same lessons and dates as "recent", but every one of them, matched to the roadmap or not), newest first, at
    most RECENT_MAX, and the week's tally. Nothing of the game: no XP, modules, levels or classes. Lessons aren't
    behind the sign-in (the Knowledge page shows them); the Party is."""
    today = today or utcnow().date()
    since, until = (today - timedelta(days=RECENT_DAYS - 1)).isoformat(), today.isoformat()
    after = (today + timedelta(days=1)).isoformat()
    where, params = scope(None, here_id)
    rows = conn.execute(
        "SELECT k.id, k.title, k.kind, COALESCE(s.agent, k.agent) AS agent, k.session_id, "
        "COALESCE(s.started_at, k.created_at, '') AS at FROM knowledge k JOIN sessions s ON s.id = k.session_id "
        f"WHERE {_LESSONS_WHERE} AND {where} AND COALESCE(s.started_at, k.created_at, '') >= ? "
        "AND COALESCE(s.started_at, k.created_at, '') < ? ORDER BY at DESC, k.id DESC",
        (*KINDS, *CODING_AGENTS, *params, since, after)).fetchall()
    by_agent = {a: 0 for a in CODING_AGENTS}
    for r in rows:
        by_agent[r["agent"]] += 1
    return {"recent": [{"id": r["id"], "title": r["title"] or "", "kind": r["kind"], "agent": r["agent"],
                        "at": r["at"][:10], "session_id": r["session_id"]} for r in rows[:RECENT_MAX]],
            "week": {"since": since, "until": until, "lessons": len(rows), "by_agent": by_agent}}


def _zone(cat: Catalog, recent: list[dict], newest_skill: int | None) -> dict | None:
    """Where the party is: the module that earned the most XP in `recent` (ties: the one seen newest), else the
    newest skill event's module, else nowhere yet."""
    xp: dict[str, int] = {}
    for k in recent:  # newest first: a module's place in `xp` is how new its newest lesson is
        for m in k["modules"]:
            xp[m["id"]] = xp.get(m["id"], 0) + k["xp"] // len(k["modules"])
    if xp:
        mid = max(xp, key=lambda m: xp[m])  # max keeps the first of equals: the newest
        m = cat.module[mid]
    elif newest_skill is not None:
        m = cat.modules[newest_skill]
    else:
        return None
    return {"attr": m.get("attr"), "module": m["id"], "module_name": _t(m, "name")}


def _sessions_by_agent(conn: sqlite3.Connection, where: str, params: tuple) -> dict:
    """Per agent: its sessions in scope (how many, the first and last), and every active lesson they produced."""
    out = {a: {"n": 0, "since": None, "last": None, "lessons": 0} for a in CODING_AGENTS}
    for r in conn.execute(f"SELECT s.agent, COUNT(*), MIN(s.started_at), MAX(s.started_at) FROM sessions s "
                          f"WHERE s.agent IN ({_AGENTS_SQL}) AND s.source != 'history' AND {where} GROUP BY s.agent",
                          (*CODING_AGENTS, *params)):
        out[r[0]].update(n=r[1], since=r[2], last=r[3])
    for r in conn.execute(f"SELECT COALESCE(s.agent, k.agent), COUNT(*) FROM knowledge k JOIN sessions s ON "
                          f"s.id = k.session_id WHERE {_ACTIVE_WHERE} AND {where} GROUP BY 1", (*CODING_AGENTS, *params)):
        out[r[0]]["lessons"] = r[1]
    return out


def _build(conn: sqlite3.Connection, cat: Catalog, sc: _Scope, lessons: list[Lesson], done: dict,
           goal: str | None, today: date) -> dict:
    tallies = {a: _Tally(len(cat.modules)) for a in CODING_AGENTS}
    for k in lessons:
        tallies[k.agent].add(k)
    sessions = _sessions_by_agent(conn, sc.where, sc.params)
    agents, covs = [], {}
    for a in CODING_AGENTS:
        out, covs[a] = _agent_out(cat, a, tallies[a], sessions)
        agents.append(out)
    ranked = sorted(agents, key=lambda x: (-x["level"], -x["xp"], CODING_AGENTS.index(x["agent"])))
    chosen = goal in cat.track
    goal_id = goal if chosen else next((x["class"]["id"] for x in ranked if x["class"]), None)
    side = []
    for x in ranked:
        c = x["class"]["id"] if x["class"] else None
        if c and c != goal_id and c not in side and len(side) < SIDE_TRACKS:
            side.append(c)
    artifacts = _artifacts(conn, sc.where, sc.params)
    newest = lessons[::-1]
    quests = []
    for tid, main in ([(goal_id, True)] if goal_id else []) + [(t, False) for t in side]:
        quests += [_quest(cat, ms["id"], main=main, done=done, artifacts=artifacts, newest=newest)
                   for ms in cat.track[tid].get("milestones") or []]
    journey, series, newest_skill = _journey(cat, lessons, done)
    recent, week = _recent(cat, lessons, today)
    return {
        "catalog": {"title": _t(cat.raw, "title"), "version": cat.raw.get("version"), "tracks": len(cat.tracks),
                    "modules": len(cat.modules)},
        "attrs": [{"id": a["id"], "name": _t(a, "name"), "about": _t(a, "about")} for a in cat.attrs],
        "agents": agents,
        "goal": {"id": goal_id, "name": _t(cat.track[goal_id], "name"), "chosen": chosen} if goal_id else None,
        "quests": quests,
        "journey": journey,
        "series": series,
        "recent": recent,
        "week": week,
        "zone": _zone(cat, recent, newest_skill),
    }


def compute(conn: sqlite3.Connection, here_id: str, viewer: dict | None = None, *, catalog: Catalog | None = None,
            today: date | None = None) -> dict:
    """The party as GET /api/party shows it once signed in (without who is signed in): the five agents, the attrs,
    the goal, quests, journey, series, this week's lessons and where the party is. `here_id`: this computer's machine
    id; `today`: the UTC date "recent" ends on. Cached (per day, as "recent" moves); don't change what it returns."""
    cat = catalog or load_catalog()
    return _computed(conn, cat, _scope_of(conn, here_id, viewer), today or utcnow().date())


def _computed(conn: sqlite3.Connection, cat: Catalog, sc: _Scope, today: date) -> dict:
    """compute() for a scope already worked out (the Adventure's endpoints share theirs)."""
    done, goal = quests_done(conn, sc.key), goal_of(conn, sc.key)
    key = (sc.key, sc.finger, json.dumps(done, sort_keys=True), goal, cat.key, lang.get(), today.isoformat())
    with _lock:
        hit = _party_cache.get(key)
    if hit is not None:
        return hit
    return _remember(_party_cache, key, _build(conn, cat, sc, lessons_in(conn, cat, sc), done, goal, today))


def skill(conn: sqlite3.Connection, here_id: str, viewer: dict | None, agent, module, *,
          catalog: Catalog | None = None) -> dict | None:
    """The lessons behind one agent's skill in one module (the "why"): most XP first, then newest. None for an agent
    or module the Party doesn't know."""
    cat = catalog or load_catalog()
    if agent not in CODING_AGENTS or module not in cat.module:
        return None
    i = cat.module_index[module]
    rows = [k for k in lessons_in(conn, cat, _scope_of(conn, here_id, viewer)) if k.agent == agent and i in k.modules]
    xp = sum(k.xp for k in rows)
    rows.sort(key=lambda k: (k.xp, k.at, k.id), reverse=True)
    m = cat.module[module]
    sl = skill_level(xp)
    return {"agent": agent, "module": module, "name": _t(m, "name"), "attr": m.get("attr"),
            "about": _t(m, "study") or [], "practice": _t(m, "practice"), "gate": _t(m, "gate"),
            "level": sl, "xp": xp, "level_xp": skill_threshold(sl) if sl else 0, "next_xp": skill_threshold(sl + 1),
            "lessons_total": len(rows),  # all of them; `lessons` lists the first SKILL_LESSONS_MAX
            "lessons": [{"id": k.id, "title": k.title, "kind": k.kind, "stage": k.stage, "xp": k.xp,
                         "created_at": k.created_at, "session_id": k.session_id, "project_name": k.project_name}
                        for k in rows[:SKILL_LESSONS_MAX]]}


def track(conn: sqlite3.Connection, here_id: str, viewer: dict | None, track_id, *,
          catalog: Catalog | None = None) -> dict | None:
    """One track: its modules with each agent's level there, its milestones as quests, each agent's coverage."""
    cat = catalog or load_catalog()
    if track_id not in cat.track:
        return None
    sc = _scope_of(conn, here_id, viewer)
    lessons = lessons_in(conn, cat, sc)
    tallies = {a: _Tally(len(cat.modules)) for a in CODING_AGENTS}
    for k in lessons:
        tallies[k.agent].add(k)
    t, ti = cat.track[track_id], cat.track_index[track_id]
    critical, targets = set(t.get("critical") or []), t.get("targets") or {}
    modules = []
    for mid in t.get("modules") or []:
        if mid not in cat.module:
            continue
        i, m = cat.module_index[mid], cat.module[mid]
        modules.append({"module": mid, "name": _t(m, "name"), "attr": m.get("attr"), "critical": mid in critical,
                        "target": targets.get(mid) if isinstance(targets.get(mid), int) else None,
                        "agents": {a: {"level": skill_level(tallies[a].mxp[i]), "xp": tallies[a].mxp[i]}
                                   for a in CODING_AGENTS if tallies[a].mxp[i]}})
    done = quests_done(conn, sc.key)
    artifacts, newest = _artifacts(conn, sc.where, sc.params), lessons[::-1]
    cover = {}
    for a in CODING_AGENTS:
        got = dict((x, c) for c, x in coverage(cat, tallies[a].mxp))
        cover[a] = round(got.get(ti, 0.0), 3)
    cg = cat.categories.get(t.get("category"))
    return {"id": t["id"], "name": _t(t, "name"), "category": t.get("category"), "category_name": _t(cg, "name"),
            "family": _t(t, "family"), "owned": _t(t, "owned"), "interview": _t(t, "interview"),
            "evidence": _t(t, "evidence"), "modules": modules,
            "milestones": [_quest(cat, ms["id"], main=False, done=done, artifacts=artifacts, newest=newest)
                           for ms in t.get("milestones") or []],
            "coverage": cover}


def tracks(*, catalog: Catalog | None = None) -> dict:
    """Every track by category, for picking a goal."""
    cat = catalog or load_catalog()
    by: dict[str, list] = {}
    for t in cat.tracks:
        by.setdefault(t.get("category"), []).append({"id": t["id"], "name": _t(t, "name")})
    order = list(cat.categories) + [c for c in by if c not in cat.categories]
    return {"categories": [{"id": c, "name": _t(cat.categories.get(c), "name") or c, "tracks": by[c]}
                           for c in order if c in by]}


# ------------------------------------------------------------------ the Adventure (Innerlatch's map and battles)
# Every counted lesson is an encounter on a map of the roadmap's attrs (regions) and modules (territories); the main
# quest's milestones are its bosses. Battles change no level or XP: beating one only marks the lesson cleared, in the
# person's own log (like the quests). A boss is beaten only by the person marking its milestone done.
def cleared_of(conn: sqlite3.Connection, key: str) -> list[int]:
    """The lesson ids the scope `key` has cleared, each once, in the order they were cleared."""
    got = loads(kv_get(conn, CLEARED_KEY + key), [])
    if not isinstance(got, list):
        return []
    return list(dict.fromkeys(x for x in got if isinstance(x, int) and not isinstance(x, bool)))


def set_cleared(conn: sqlite3.Connection, here_id: str, viewer: dict | None, lesson_id, cleared: bool, *,
                catalog: Catalog | None = None) -> list[int]:
    """Mark a lesson's encounter cleared (or not) in the viewer's own log; answers the log. The lesson must be one of
    the viewer's (lessons_in their scope); one already in the log may always be taken out of it."""
    return clear(conn, here_id, viewer, lesson_id, cleared, catalog=catalog)[0]


def clear(conn: sqlite3.Connection, here_id: str, viewer: dict | None, lesson_id, cleared: bool, *,
          catalog: Catalog | None = None) -> tuple[list[int], dict | None]:
    """set_cleared, and what the win paid: the first time a lesson is cleared, its gold and loot go into the viewer's
    bag (bag.pay_first_clear), in the same transaction as the log; None when nothing was paid (un-clearing, clearing
    again, or a lesson that already paid once)."""
    from . import bag

    cat = catalog or load_catalog()
    sc = _scope_of(conn, here_id, viewer)
    valid = isinstance(lesson_id, int) and not isinstance(lesson_id, bool)
    lesson = next((k for k in lessons_in(conn, cat, sc) if k.id == lesson_id), None) if valid else None
    with kv_transaction(conn):  # two quick POSTs: the second reads the first's log and bag
        log = cleared_of(conn, sc.key)
        if not valid or (lesson is None and (cleared or lesson_id not in log)):
            raise PartyError("no such lesson")
        reward = None
        if cleared and lesson_id not in log:
            log = (log + [lesson_id])[-CLEARED_MAX:]
            reward = bag.pay_first_clear(conn, cat, sc.key, lesson)
        elif not cleared:
            log = [x for x in log if x != lesson_id]
        kv_set(conn, CLEARED_KEY + sc.key, json.dumps(log))
    return log, reward


def _boss_attr(cat: Catalog, track_id: str) -> str | None:
    """The region a track's bosses guard: the attr with the most weight among its modules (critical ones weigh 2;
    ties: catalog order)."""
    weight: dict[str, int] = {}
    for i, w in cat.weights[cat.track_index[track_id]]:
        a = cat.modules[i].get("attr")
        weight[a] = weight.get(a, 0) + w
    order = [a["id"] for a in cat.attrs]
    ranked = sorted(weight, key=lambda a: (-weight[a], order.index(a) if a in order else len(order)))
    return ranked[0] if ranked else None


def adventure_map(conn: sqlite3.Connection, here_id: str, viewer: dict | None = None, *,
                  catalog: Catalog | None = None, today: date | None = None) -> dict:
    """GET /api/party/map: the regions (every attr, every module of it, how many counted lessons stand there: those
    whose best module it is, and their XP), every counted lesson oldest first (`study`: it has study material;
    `friction`: a trap for the agents' rules, never offered to study: study.py), the lessons cleared, the bosses (the
    main quest's milestones), the newest lesson (where the party stands), the agents, the gold in the viewer's bag, a
    town per region (bag.py), and the person's own study: the goal track and its modules, the lessons understood and
    how many per module (study.py; empty for a person on a hub: their record stays on their computer)."""
    from . import bag, study

    cat = catalog or load_catalog()
    sc = _scope_of(conn, here_id, viewer)
    data = _computed(conn, cat, sc, today or utcnow().date())
    counted = [k for k in lessons_in(conn, cat, sc) if k.modules]
    flags = study.flags(conn, [k.id for k in counted])
    studied = study.studied_of(conn, sc.key)
    goal = data.get("goal")
    there: dict[int, list[int]] = {}  # module index: [lessons, xp] of the lessons that stand in it
    for k in counted:
        got = there.setdefault(k.modules[0], [0, 0])
        got[0] += 1
        got[1] += k.xp * len(k.modules)
    regions = []
    for a in cat.attrs:
        mods = [{"id": m["id"], "name": _t(m, "name"), "lessons": there.get(i, [0, 0])[0], "xp": there.get(i, [0, 0])[1]}
                for i, m in enumerate(cat.modules) if m.get("attr") == a["id"]]
        regions.append({"id": a["id"], "name": _t(a, "name"), "about": _t(a, "about"), "modules": mods})
    return {
        "regions": regions,
        "lessons": [{"id": k.id, "title": k.title[:MAP_TITLE_MAX], "kind": k.kind, "stage": k.stage, "agent": k.agent,
                     "at": k.at[:10], "xp": k.xp * len(k.modules), "modules": [cat.modules[i]["id"] for i in k.modules],
                     "session_id": k.session_id, "project": k.project_name, "study": flags.get(k.id, (False, False))[0],
                     "friction": flags.get(k.id, (False, False))[1]} for k in counted],
        "cleared": cleared_of(conn, sc.key),
        "bosses": [{"id": q["id"], "n": q["n"], "text": q["text"], "track": q["track"], "track_name": q["track_name"],
                    "attr": _boss_attr(cat, q["track"]), "status": q["status"]}
                   for q in data["quests"] if q["main"]],
        "newest": counted[-1].id if counted else None,
        "agents": [{x: a[x] for x in ("agent", "name", "short", "level", "tier")} for a in data["agents"]],
        "gold": bag.load(conn, sc.key)["gold"],
        "towns": bag.towns(cat),
        "goal": {**goal, "modules": list(cat.track[goal["id"]].get("modules") or [])} if goal else None,
        "understood": study.understood(studied),
        "readiness": study.readiness(counted, cat, studied),
        "ready_at": study.READY_AT,
        "studying": viewer is None,  # study battles (and their record) only at the person's own computer
    }


def owns(conn: sqlite3.Connection, here_id: str, viewer: dict | None, lesson_id, *, catalog: Catalog | None = None) -> bool:
    """The lesson is one of the viewer's (counted or not), as an encounter would show it."""
    if not isinstance(lesson_id, int) or isinstance(lesson_id, bool):
        return False
    cat = catalog or load_catalog()
    return any(k.id == lesson_id for k in lessons_in(conn, cat, _scope_of(conn, here_id, viewer)))


def _case(raw) -> dict | None:
    """A knowledge row's case file as an encounter tells it: its scene, question and answer, when it has all three."""
    got = loads(raw, None)
    if not isinstance(got, dict):
        return None
    case = {x: got.get(x) for x in ("scene", "question", "answer")}
    return case if all(isinstance(v, str) and v.strip() for v in case.values()) else None


def encounter(conn: sqlite3.Connection, here_id: str, viewer: dict | None, lesson_id, *,
              catalog: Catalog | None = None, today: date | None = None) -> dict | None:
    """GET /api/party/encounter: one of the viewer's lessons (counted or not) with its body and case file, the party
    that meets it (each agent's level, attrs and skill in the lesson's modules, and the battle stats its gear adds),
    whether it's cleared, and the consumables and loot the viewer's bag holds (bag.py). None for a lesson that isn't
    the viewer's."""
    from . import bag

    cat = catalog or load_catalog()
    sc = _scope_of(conn, here_id, viewer)
    k = next((x for x in lessons_in(conn, cat, sc) if x.id == lesson_id), None)
    if k is None:
        return None
    row = conn.execute("SELECT case_json FROM knowledge WHERE id = ?", (k.id,)).fetchone()
    data = _computed(conn, cat, sc, today or utcnow().date())
    mids = [cat.modules[i]["id"] for i in k.modules]
    held = bag.load(conn, sc.key)
    gear = bag.gear_of(cat, held)
    party_ = []
    for a in data["agents"]:
        levels = {s["module"]: s["level"] for s in a["skills"]}
        party_.append({**{x: a[x] for x in ("agent", "name", "short", "level", "tier")},
                       "attr_levels": dict(a["attr_levels"]), "skills": {m: levels.get(m, 0) for m in mids},
                       "gear": gear[a["agent"]]})
    return {
        "lesson": {"id": k.id, "title": k.title, "kind": k.kind, "stage": k.stage, "agent": k.agent, "at": k.at[:10],
                   "xp": k.xp * len(k.modules), "session_id": k.session_id, "project": k.project_name,
                   "body": k.body[:ENCOUNTER_BODY_MAX], "case": _case(row["case_json"] if row else None),
                   "modules": [{"id": cat.modules[i]["id"], "name": _t(cat.modules[i], "name"),
                                "attr": cat.modules[i].get("attr")} for i in k.modules]},
        "party": party_,
        "cleared": k.id in cleared_of(conn, sc.key),
        "items": bag.usable(cat, held),
    }


# ------------------------------------------------------------------ who's hiring (sharing/market.py)
# Job-market numbers per track, beside the Party's own: public, derived numbers read from Interlatch's Firebase project
# (or the copy bundled with the package), nothing about the person. Only for a signed-in Party: the API adds them.
def _market() -> dict | None:
    from ..sharing import market

    return market.current()


def _market_head(m: dict) -> dict:
    return {"status": m.get("status"), "as_of": m.get("as_of"), "source": m.get("source"),
            "window_days": m.get("window_days"), "sample": m.get("sample")}


def _market_class(cat: Catalog, entry: dict) -> dict:
    """One track's numbers, the modules employers ask for named in the dashboard's language (unknown ones left out)."""
    return {**{k: v for k, v in entry.items() if k != "top_modules"},
            "top_modules": [{"module": mid, "name": _t(cat.module[mid], "name"), "attr": cat.module[mid].get("attr")}
                            for mid in entry.get("top_modules") or [] if mid in cat.module]}


def payload_tracks(data: dict) -> list[str]:
    """The tracks a compute() payload names: the agents' classes and the tracks they lean to, the goal, the quests'."""
    ids = [x["id"] for a in data.get("agents") or [] for x in ([a["class"]] if a.get("class") else []) + (a.get("classes") or [])]
    ids += [data["goal"]["id"]] if data.get("goal") else []
    ids += [q["track"] for q in data.get("quests") or []]
    return list(dict.fromkeys(ids))


def market_block(track_ids, *, catalog: Catalog | None = None) -> dict | None:
    """{status, as_of, source, window_days, sample, classes: {track id: its numbers}} for the tracks asked about (those
    the market has); None when there are no numbers at all."""
    m = _market()
    if m is None:
        return None
    cat = catalog or load_catalog()
    classes = m.get("classes") or {}
    return {**_market_head(m), "classes": {i: _market_class(cat, classes[i]) for i in track_ids if i in classes}}


def market_tracks(data: dict) -> dict:
    """tracks() with each track's open roles worldwide and in Japan and its share of postings, and the market's
    status, date and sample (`market`, None without numbers): for sorting the goal picker by demand."""
    m = _market()
    if m is None:
        return {**data, "market": None}
    classes = m.get("classes") or {}

    def numbers(tr: dict) -> dict:
        c = classes.get(tr["id"]) or {}
        return {**tr, "global": c.get("global"), "japan": c.get("japan"), "share_global": c.get("share_global")}

    return {"categories": [{**c, "tracks": [numbers(tr) for tr in c["tracks"]]} for c in data["categories"]],
            "market": _market_head(m)}
