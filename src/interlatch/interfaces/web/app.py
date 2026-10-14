"""Local dashboard: JSON API + static single-page app (stdlib only, binds to localhost).

Besides 127.0.0.1 and localhost it answers only to the names in `[server] allowed_hosts` (Tailscale Serve's, set by
`interlatch tailnet on`), and there only to the Tailscale logins in `[server] allowed_users`. On a hub, /api/hub/* takes
other computers' session files, authenticated by the hub's token or a person's computer token instead (hub.py).

Once a hub has people (people.py), its API answers only someone signed in: at the hub computer itself (an admin),
through a company sign-in proxy that names them in `[server] auth_header`, or with a session cookie from an invite or
sign-in link (/signin). Everyone signed in may look; only admins change things. A hub without people answers as before.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from ... import __version__
from ...analysis.runners.factory import BACKENDS, make_runner
from ...application.records import resolve_session_id, session_record
from ...application.search import search_all, search_events, search_knowledge, search_sessions
from ...core.config import LANGUAGES, Config, env
from ...core.i18n import tr
from ...core.paths import abs_path_sql, clean_abs_path, is_abs_path, normalize_project_path, path_kind, path_variants
from ...core.sessions import analysis_reason_text
from ...core.util import loads, to_iso, utcnow
from ...knowledge.ladder import reason_text
from ...knowledge.ladder import refresh as refresh_stage
from ...knowledge.synthesize import GLOBAL
from ...projects.labels import project_labels
from ...sharing import news
from ...storage.kv import kv_get, kv_set
from ...storage.sqlite import connect
from .assets import UI_BUILD
from .jobs import Jobs

log = logging.getLogger("interlatch.server")
MAX_SELECTION = 1000  # sessions one "analyze selected" may cover
# What the dashboard may change for each coding agent: setting -> (config section, key). Effort is shared.
AGENT_SETTINGS = {
    "claude": {"model": ("analysis", "model"), "synthesis_model": ("synthesis", "model"),
               "screen_model": ("analysis", "screen_model"), "effort": ("analysis", "effort")},
    "codex": {"model": ("analysis", "codex_model"), "effort": ("analysis", "effort")},
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TEAM_DAYS = ("7", "30", "90")  # the team Home's periods, first the default
TEAM_MAX_PROJECTS = 500
# Home's Agents card: the metrics a session may carry. An agent none of whose sessions ever had one does not record it
# (a chat export has no tokens), so the card shows a dash there rather than a 0 that reads as "none"
AGENT_METRICS = {"tool_calls": "n_tool_calls > 0", "lines": "lines_added + lines_removed > 0",
                 "tokens": "input_tokens + output_tokens + cache_read_tokens + cache_write_tokens > 0", "cost": "est_cost_usd > 0"}
AGENT_TOTALS = ("sessions", "active_s", "prompts", "tool_calls", "tool_errors", "lines_added", "lines_removed", "tokens", "cost")


SESSION_LIST_COLS = (
    "id, source, agent, title, project_name, project_path, started_at, ended_at, duration_s, active_s, n_prompts, "
    "n_tool_calls, n_tool_errors, n_subagents, n_compactions, n_interrupts, lines_added, lines_removed, n_files, "
    "input_tokens + output_tokens + cache_read_tokens + cache_write_tokens AS tokens, est_cost_usd, primary_model, "
    "git_branch, outcome, sentiment, analysis_status, analysis_reason, tags_json, summary, source_present, peak_context, "
    "CASE WHEN screen_sig = files_sig THEN screen_verdict END screen_verdict, screen_topic, screen_reason, machine_id"
)
# whose a session is, on a hub with people: its computer's person, else that computer; keyed "p:<person id>" or
# "m:<computer id>" (the `who` filter). The hub's own sessions may have no machine_id. WHO_SELECT's one placeholder
# takes the hub computer's name, WHO_JOIN's its id.
WHO_SELECT = "p.id AS person_id, COALESCE(p.name, m.name, ?) AS who, COALESCE('p:' || p.id, 'm:' || m.id) AS who_key"
WHO_JOIN = ("LEFT JOIN machines m ON m.id = COALESCE(s.machine_id, ?) "
            "LEFT JOIN people p ON p.id = m.person_id AND p.removed_at IS NULL")
WHO_KEY = re.compile(r"p:\d{1,12}|m:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SORTABLE = {"started_at", "ended_at", "active_s", "duration_s", "n_prompts", "n_tool_calls", "tokens", "est_cost_usd",
            "title", "project_name", "agent", "lines_added", "n_tool_errors"}


def _local_offset_modifier() -> str:
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    return f"{int(offset.total_seconds() // 60):+d} minutes"


def git_ignored(root: str, paths: list[str]) -> set[str]:
    """The paths git ignores in the repository holding root; none when it isn't one, or git fails."""
    if not paths:
        return set()
    try:
        # root comes from the request: a repository's own config may not run anything (fsmonitor, hooks)
        out = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", root,
                              "check-ignore", "-z", "--stdin"], input="\0".join(paths) + "\0",
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode not in (0, 1):  # 1 means nothing is ignored; 128 is not a repository, or a path outside it
        log.debug("git check-ignore in %s: %s", root, out.stderr.strip())
        return set()
    return {p for p in out.stdout.split("\0") if p}


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.local = threading.local()
        self.jobs = Jobs()
        self._lb_signin: dict | None = None  # a leaderboard sign-in waiting to come back from the hosted page
        self._cfg_sig = self._config_sig()
        self._update_check = threading.Lock()  # one daily update check at a time
        from ...sharing.receiver import IngestTrigger

        self.ingest = IngestTrigger(cfg)  # a hub ingests what other computers send, right after they send it
        from ...sharing.people import CodeAttempts

        self.code_attempts = CodeAttempts()  # wrong invite and sign-in codes per address

    def _config_sig(self):
        try:
            return self.cfg.config_path.stat().st_mtime_ns
        except OSError:
            return None

    def refresh_config(self) -> None:
        """The dashboard runs for days: pick up config.toml edits (e.g. `interlatch connect codex`) without a restart."""
        from ...core.config import load_config

        sig = self._config_sig()
        if sig != self._cfg_sig:
            self.cfg = load_config(self.cfg.home)
            self._cfg_sig = sig

    @property
    def conn(self):
        conn = getattr(self.local, "conn", None)
        if conn is None:
            conn = connect(self.cfg.db_path)
            self.local.conn = conn
        return conn

    def release(self) -> None:
        """Close this request thread's connection (ThreadingHTTPServer uses a new thread per request)."""
        conn = getattr(self.local, "conn", None)
        if conn is not None:
            try:
                conn.rollback()
                conn.close()
            finally:
                self.local.conn = None

    # ------------------------------------------------------------------ helpers
    def _filters(self, q: dict) -> tuple[str, list]:
        where, params = ["1=1"], []
        if q.get("project"):
            where.append("project_path = ?")
            params.append(q["project"])
        if q.get("agent"):
            where.append("agent = ?")
            params.append(q["agent"])
        if q.get("since"):
            where.append("started_at >= ?")
            params.append(q["since"])
        if q.get("until"):
            where.append("started_at < ?")
            params.append(q["until"])
        return " AND ".join(where), params

    @staticmethod
    def _since_from_days(days: str | None) -> str | None:
        if not days or days == "all":
            return None
        return to_iso(utcnow() - timedelta(days=int(days)))

    # ------------------------------------------------------------------ endpoints
    def overview(self, q: dict, limited: bool = False) -> dict:
        c = self.conn
        days = q.get("days") or "90"
        since = self._since_from_days(days)
        project = q.get("project") or None
        agent = q.get("agent") or None
        mod = _local_offset_modifier()
        sw, sp = self._filters({"project": project, "since": since, "agent": agent})
        totals = dict(c.execute(
            f"SELECT COUNT(*) sessions, COALESCE(SUM(n_prompts),0) prompts, COALESCE(SUM(n_tool_calls),0) tool_calls, "
            f"COALESCE(SUM(n_tool_errors),0) tool_errors, COALESCE(SUM(active_s),0) active_s, "
            f"COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens),0) tokens, "
            f"COALESCE(SUM(est_cost_usd),0) cost, COALESCE(SUM(lines_added),0) lines_added, "
            f"COALESCE(SUM(lines_removed),0) lines_removed, COUNT(DISTINCT project_path) projects, "
            f"SUM(CASE WHEN analysis_status='done' THEN 1 ELSE 0 END) analyzed, MIN(started_at) first_at "
            f"FROM sessions WHERE {sw}", sp).fetchone())
        kw = "status = 'active'" + (" AND project_path = ?" if project else "")
        kp = [project] if project else []
        if since:
            kw += " AND created_at >= ?"
            kp.append(since)
        totals["knowledge"] = c.execute(f"SELECT COUNT(*) FROM knowledge WHERE {kw}", kp).fetchone()[0]
        # previous period for deltas
        prev = None
        if since:
            prev_since = to_iso(utcnow() - timedelta(days=2 * int(days)))
            pw, pp = self._filters({"project": project, "since": prev_since, "until": since, "agent": agent})
            prev = dict(c.execute(
                f"SELECT COUNT(*) sessions, COALESCE(SUM(n_prompts),0) prompts, COALESCE(SUM(n_tool_calls),0) tool_calls, "
                f"COALESCE(SUM(active_s),0) active_s, COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens),0) tokens, "
                f"COALESCE(SUM(est_cost_usd),0) cost, COALESCE(SUM(lines_added),0) lines_added FROM sessions WHERE {pw}", pp).fetchone())
        # daily series (sessions by start day; tokens/cost by API-call day)
        daily: dict[str, dict] = {}
        for r in c.execute(
            f"SELECT date(started_at, '{mod}') d, COUNT(*) sessions, SUM(n_prompts) prompts, SUM(active_s) active_s, "
            f"SUM(n_tool_calls) tool_calls, SUM(lines_added) lines_added "
            f"FROM sessions WHERE {sw} AND started_at IS NOT NULL GROUP BY d", sp):
            daily.setdefault(r["d"], {}).update(sessions=r["sessions"], prompts=r["prompts"] or 0, active_s=r["active_s"] or 0,
                                                tool_calls=r["tool_calls"] or 0, lines_added=r["lines_added"] or 0)
        aw = ("a.ts IS NOT NULL" + (" AND a.ts >= ?" if since else "") + (" AND s.project_path = ?" if project else "")
              + (" AND s.agent = ?" if agent else ""))
        ap = ([since] if since else []) + ([project] if project else []) + ([agent] if agent else [])
        for r in c.execute(
            f"SELECT date(a.ts, '{mod}') d, SUM(a.input_tokens+a.output_tokens+a.cache_read_tokens+a.cache_write_tokens) tokens, "
            f"SUM(a.cost_usd) cost FROM api_calls a JOIN sessions s ON s.id = a.session_id WHERE {aw} GROUP BY d", ap):
            daily.setdefault(r["d"], {}).update(tokens=r["tokens"] or 0, cost=r["cost"] or 0)
        daily_list = [{"date": d, "sessions": v.get("sessions", 0), "prompts": v.get("prompts", 0),
                       "active_s": v.get("active_s", 0), "tokens": v.get("tokens", 0), "cost": v.get("cost", 0),
                       "tool_calls": v.get("tool_calls", 0), "lines_added": v.get("lines_added", 0)}
                      for d, v in sorted(daily.items()) if d]
        # calendar: up to a year, whatever the period filter (same project scope)
        cal_since = to_iso(utcnow() - timedelta(days=372))
        cw, cp = self._filters({"project": project, "since": cal_since, "agent": agent})
        calendar = [dict(r) for r in c.execute(
            f"SELECT date(started_at, '{mod}') date, COUNT(*) sessions, SUM(active_s) active_s, SUM(n_prompts) prompts "
            f"FROM sessions WHERE {cw} GROUP BY date ORDER BY date", cp)]
        # hour-of-week from human prompts
        ew = ("e.kind = 'prompt' AND e.agent_id = ''" + (" AND e.ts >= ?" if since else "")
              + (" AND s.project_path = ?" if project else "") + (" AND s.agent = ?" if agent else ""))
        hours = [dict(r) for r in c.execute(
            f"SELECT CAST(strftime('%w', e.ts, '{mod}') AS INTEGER) dow, CAST(strftime('%H', e.ts, '{mod}') AS INTEGER) hour, "
            f"COUNT(*) n FROM events e JOIN sessions s ON s.id = e.session_id WHERE {ew} GROUP BY dow, hour", ap)]
        models = [dict(r) for r in c.execute(
            f"SELECT COALESCE(a.model, 'unknown') model, SUM(a.input_tokens+a.output_tokens+a.cache_read_tokens+a.cache_write_tokens) tokens, "
            f"SUM(a.output_tokens) output_tokens, SUM(a.cost_usd) cost, COUNT(*) calls FROM api_calls a JOIN sessions s ON s.id = a.session_id "
            f"WHERE {aw} GROUP BY model ORDER BY cost DESC", ap)]
        tw = ("1=1" + (" AND t.ts >= ?" if since else "") + (" AND s.project_path = ?" if project else "")
              + (" AND s.agent = ?" if agent else ""))
        tools = [dict(r) for r in c.execute(
            f"SELECT t.name, COUNT(*) n, SUM(t.is_error) errors, AVG(t.duration_ms) avg_ms FROM tool_calls t "
            f"JOIN sessions s ON s.id = t.session_id WHERE {tw} GROUP BY t.name ORDER BY n DESC LIMIT 200", ap)]
        labels = project_labels(c)
        projects = [dict(r) | {"label": labels.get(r["project_path"] or "", r["project_name"])} for r in c.execute(
            f"SELECT project_path, project_name, COUNT(*) sessions, SUM(active_s) active_s, SUM(est_cost_usd) cost, "
            f"MAX(started_at) last FROM sessions WHERE {sw} GROUP BY project_path ORDER BY active_s DESC LIMIT 12", sp)]
        outcomes = [dict(r) for r in c.execute(
            f"SELECT COALESCE(outcome, 'not analyzed') outcome, COUNT(*) n FROM sessions WHERE {sw} AND source != 'history' "
            f"GROUP BY 1 ORDER BY n DESC", sp)]
        work_types: dict[str, int] = {}
        for (wt,) in c.execute(f"SELECT work_types_json FROM sessions WHERE {sw} AND work_types_json IS NOT NULL", sp):
            for w in loads(wt, []) or []:
                work_types[w] = work_types.get(w, 0) + 1
        agents = self._agent_rows(project, since)  # every agent, whichever one is selected
        recent = self._session_rows(f"{sw}", sp, "started_at DESC", 8)
        kq = "SELECT k.*, s.title session_title FROM knowledge k LEFT JOIN sessions s ON s.id = k.session_id WHERE k.status = 'active' AND k.source = 'analysis'"
        kqp = []
        if project:
            kq += " AND k.project_path = ?"
            kqp.append(project)
        knowledge = [self._k(dict(r)) for r in c.execute(kq + " ORDER BY k.id DESC LIMIT 8", kqp)]
        return {
            "days": days, "project": project, "agent": agent, "agents": agents, "totals": totals, "previous": prev, "daily": daily_list,
            "calendar": calendar, "hours": hours, "models": models, "tools": tools, "projects": projects,
            "outcomes": outcomes, "work_types": sorted(work_types.items(), key=lambda x: -x[1]),
            "recent": recent, "knowledge": knowledge, "status": self.status_small(limited=limited),
        }

    def _agent_rows(self, project: str | None, since: str | None) -> list[dict]:
        """Home's Agents card: each agent used in this project scope, its totals for the period (0 when it was idle),
        when it was last used, and `records`: the metrics of AGENT_METRICS its sessions carry at all, in any project."""
        c = self.conn
        gw, gp = self._filters({"project": project, "since": since})
        totals = {r["agent"]: dict(r) for r in c.execute(
            f"SELECT agent, COUNT(*) sessions, COALESCE(SUM(active_s),0) active_s, COALESCE(SUM(n_prompts),0) prompts, "
            f"COALESCE(SUM(n_tool_calls),0) tool_calls, COALESCE(SUM(n_tool_errors),0) tool_errors, "
            f"COALESCE(SUM(lines_added),0) lines_added, COALESCE(SUM(lines_removed),0) lines_removed, "
            f"COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens),0) tokens, "
            f"COALESCE(SUM(est_cost_usd),0) cost FROM sessions WHERE {gw} GROUP BY agent", gp)}
        kw = "k.status = 'active'" + (" AND k.project_path = ?" if project else "") + (" AND k.created_at >= ?" if since else "")
        knowledge = dict(c.execute(f"SELECT s.agent, COUNT(*) FROM knowledge k JOIN sessions s ON s.id = k.session_id "
                                   f"WHERE {kw} GROUP BY s.agent", [x for x in (project, since) if x]).fetchall())
        flags = ", ".join(f"MAX({expr}) {k}" for k, expr in AGENT_METRICS.items())
        rows = []
        for r in c.execute(f"SELECT agent, MAX(CASE WHEN {'project_path = ?' if project else '1'} THEN started_at END) last, "
                           f"{flags} FROM sessions GROUP BY agent", [project] if project else []):
            if r["agent"] in totals or r["last"]:
                rows.append({"agent": r["agent"], **dict.fromkeys(AGENT_TOTALS, 0), **totals.get(r["agent"], {}),
                             "knowledge": knowledge.get(r["agent"], 0), "last": r["last"],
                             "records": [k for k in AGENT_METRICS if r[k]]})
        return sorted(rows, key=lambda x: (x["active_s"], x["sessions"], x["last"] or ""), reverse=True)  # busiest first

    # ---- a hub's team Home: what the team did lately, by project and by person
    def hub_info(self) -> dict | None:
        """This computer as a hub: the name its dashboard shows, whether it has people (then Home is the team's), its
        address, whether it takes knowledge only, and whether it is a dedicated hub (`[hub] dedicated`: a server for
        the team, whose dashboard leaves out a person's own computer). None on a computer that isn't a hub."""
        from ...core.machine import local_machine
        from ...sharing.identity import read_token
        from ...sharing.people import has_people

        if self.cfg.is_spoke or not read_token(self.cfg):
            return None
        return {"name": self.cfg.hub_name or local_machine(self.cfg)["name"], "team": has_people(self.conn),
                "address": self.cfg.hub_address or None, "knowledge_only": self.cfg.hub_accept == "knowledge",
                "dedicated": self.cfg.hub_dedicated}

    def _here(self) -> dict:
        """This computer, recorded in the machines table, which whose-session joins go through."""
        from ...core.machine import local_machine
        from ...ingestion.persistence import register_local

        here = local_machine(self.cfg)
        if not self.conn.execute("SELECT 1 FROM machines WHERE id = ?", (here["id"],)).fetchone():
            register_local(self.conn, self.cfg)
            self.conn.commit()
        return here

    def _whos(self) -> dict | None:
        """{computer id: (who, key)} for labelling rows on a hub with people, None elsewhere; the hub's own sessions,
        whose machine_id may be empty, under None."""
        from ...sharing.people import has_people

        if not has_people(self.conn):
            return None
        here = self._here()
        out = {r["id"]: (r["pname"] or r["name"] or r["id"][:8], f"p:{r['pid']}" if r["pid"] else f"m:{r['id']}")
               for r in self.conn.execute("SELECT m.id, m.name, p.id pid, p.name pname FROM machines m LEFT JOIN people p "
                                          "ON p.id = m.person_id AND p.removed_at IS NULL")}
        out[None] = out.get(here["id"]) or (here["name"], f"m:{here['id']}")
        return out

    def _label_who(self, rows: list[dict], col: str = "machine_id") -> list[dict]:
        whos = self._whos()
        if whos is not None:
            for r in rows:
                mid = r.get(col)
                r["who"], r["who_key"] = whos.get(mid) or (whos[None] if not mid else (mid[:8], f"m:{mid}"))
        return rows

    def _who_clause(self, key: str, col: str = "machine_id") -> tuple[str, list]:
        """SQL on sessions for "this person's or this computer's sessions" (a WHO_KEY); matches nothing if malformed."""
        if not WHO_KEY.fullmatch(key or ""):
            return "0", []
        here = self._here()["id"]
        if key.startswith("p:"):
            return f"COALESCE({col}, ?) IN (SELECT id FROM machines WHERE person_id = ?)", [here, int(key[2:])]
        return f"COALESCE({col}, ?) = ?", [here, key[2:]]

    def who_options(self) -> dict:
        """Who the Sessions and Knowledge pages can be narrowed to on a hub with people: each person and each computer
        no one joined, with the sessions this viewer can see (through the request's connection)."""
        from ...sharing.people import has_people

        if not has_people(self.conn):
            return {"items": []}
        here = self._here()
        return {"items": [{"key": r["who_key"], "name": r["who"], "person": r["person_id"] is not None, "sessions": r["n"]}
                          for r in self.conn.execute(
                              f"SELECT {WHO_SELECT}, COUNT(*) n FROM sessions s {WHO_JOIN} GROUP BY who_key ORDER BY n DESC, who", [here["name"], here["id"]]) if r["who_key"]]}

    def team(self, q: dict, *, scope: list[str] | None, admin: bool, viewer: dict | None = None) -> dict:
        """The team Home of a hub. Team projects are the ones set up on the hub and the ones other computers send
        sessions to; a project only this computer works on stays personal (Home › Activity shows it). Sessions and
        lessons are read through this request's connection, so someone limited to projects sees theirs only
        (access.py), and `scope` keeps the set-up projects to theirs as well. Who: the person whose computer ran the
        session, else that computer's name. People, computers and open invites: admins only. News: what other
        computers sent lately, and what `viewer` saw of it (news.py)."""
        from ...core.paths import project_name_for
        from ...projects.routing import declared_projects
        from ...sharing import people
        from ...sharing.projects import machines
        from ...sharing.protocol import SHARED_AGENTS

        c = self.conn
        days = q.get("days") if q.get("days") in TEAM_DAYS else TEAM_DAYS[0]
        since = to_iso(utcnow() - timedelta(days=int(days)))
        here = self._here()
        paths = set(declared_projects(c)) | {r[0] for r in c.execute(
            "SELECT DISTINCT project_path FROM sessions WHERE project_path IS NOT NULL AND machine_id IS NOT NULL "
            "AND machine_id != ?", (here["id"],))}
        if scope is not None:
            paths &= set(scope)
        paths = sorted(paths)[:TEAM_MAX_PROJECTS]
        base = (f"s.project_path IN ({', '.join('?' for _ in paths) or 'NULL'}) AND s.source != 'history' "
                f"AND s.agent IN ({', '.join('?' for _ in SHARED_AGENTS)})")
        bp = [*paths, *SHARED_AGENTS]
        lesson = f"k.status = 'active' AND k.source = 'analysis' AND k.scope = 'project' AND k.kind != 'preference' AND {base}"
        who, join, wp = WHO_SELECT, WHO_JOIN, [here["name"], here["id"]]  # who's placeholder comes before the join's

        per = {r["project_path"]: dict(r) for r in c.execute(
            f"SELECT s.project_path, COUNT(*) sessions, SUM(s.started_at >= ?) recent, MAX(s.started_at) last "
            f"FROM sessions s WHERE {base} GROUP BY s.project_path", [since, *bp])}
        learned = dict(c.execute(f"SELECT s.project_path, COUNT(*) FROM knowledge k JOIN sessions s ON s.id = k.session_id "
                                 f"WHERE {lesson} AND k.created_at >= ? GROUP BY s.project_path", [*bp, since]).fetchall())
        active: dict[str, list] = {}
        for r in c.execute(f"SELECT s.project_path, {who}, COUNT(*) n FROM sessions s {join} WHERE {base} "
                           f"AND s.started_at >= ? GROUP BY s.project_path, who_key ORDER BY n DESC", [*wp, *bp, since]):
            active.setdefault(r["project_path"], []).append({"key": r["who_key"], "who": r["who"], "sessions": r["n"]})
        labels = project_labels(c)
        projects = [{"path": path, "label": labels.get(path) or project_name_for(path),
                     "sessions": (per.get(path) or {}).get("sessions", 0), "recent": (per.get(path) or {}).get("recent") or 0,
                     "last": (per.get(path) or {}).get("last"), "lessons": learned.get(path, 0), "people": active.get(path, [])}
                    for path in paths]
        projects.sort(key=lambda x: (x["recent"], x["last"] or ""), reverse=True)

        # who worked on what: each person's projects in the period, latest first. No counts per person: a tally of
        # sessions or lessons reads as a score, and a lesson count mostly counts what went wrong in someone's sessions
        # (docs/design/learning.md).
        by_who: dict[str, dict] = {}
        for r in c.execute(f"SELECT {who}, s.project_path, MAX(s.started_at) last FROM sessions s {join} WHERE {base} "
                           f"AND s.started_at >= ? GROUP BY who_key, s.project_path ORDER BY last DESC", [*wp, *bp, since]):
            x = by_who.setdefault(r["who_key"], {"who_key": r["who_key"], "who": r["who"], "last": r["last"], "projects": []})
            x["projects"].append({"path": r["project_path"], "label": labels.get(r["project_path"]) or project_name_for(r["project_path"])})
        everyone = sorted(by_who.values(), key=lambda x: x["last"] or "", reverse=True)[:100]
        lessons = [dict(r) for r in c.execute(
            f"SELECT k.id, k.kind, k.title, k.confidence, k.created_at, k.session_id, s.project_path, s.project_name, {who} "
            f"FROM knowledge k JOIN sessions s ON s.id = k.session_id {join} WHERE {lesson} "
            f"ORDER BY k.created_at DESC, k.id DESC LIMIT 12", [*wp, *bp])]
        recent = [dict(r) for r in c.execute(
            f"SELECT s.id, s.title, s.agent, s.source, s.project_name, s.project_path, s.started_at, s.ended_at, s.active_s, "
            f"s.n_prompts, s.outcome, s.analysis_status, {who} FROM sessions s {join} WHERE {base} "
            f"ORDER BY s.started_at DESC LIMIT 10", [*wp, *bp])]
        out = {"days": days, "hub": self.hub_info(), "projects": projects, "who": everyone, "lessons": lessons,
               "recent": recent, "people": None, "computers": None, "news": self.news(viewer, scope=scope, admin=admin),
               "totals": {"sessions": sum(x["recent"] for x in projects), "lessons": sum(learned.values()),
                          "people": len(everyone), "projects": sum(1 for x in projects if x["recent"]),
                          "team_projects": len(projects)}}
        if not admin:
            return out
        listing = people.listing(c)
        computers = machines(c, self.cfg)
        sent = {m["id"]: m["last_push"] for m in computers}
        out["computers"] = [{"id": m["id"], "name": m["name"] or m["id"][:8], "person": m["person"],
                             "this": m["this"], "share": m["share"], "sessions": m["sessions"], "last_session": m["last_session"],
                             "last_push": m["last_push"], "last_seen": m["last_seen"]} for m in computers]
        out["people"] = [{"id": x["id"], "name": x["name"], "role": x["role"], "projects": x["projects"],
                          "computers": len(x["computers"]), "browsers": len(x["browsers"]),
                          "last_push": max((sent.get(m["machine_id"]) or "" for m in x["computers"]), default="") or None,
                          "last_dashboard": max((b["last_used"] or "" for b in x["browsers"]), default="") or None,
                          "invites": [i["expires_at"] for i in x["invites"]]} for x in listing]
        return out

    def news(self, viewer: dict | None, *, scope: list[str] | None, admin: bool, after: str | None = None) -> dict:
        """What other computers sent lately (news.py): the newest items, or with `after` those added to since; how
        many `viewer` hasn't seen; and `now`, which the page sends back once they saw it (mark_seen)."""
        from ...core.paths import project_name_for

        c = self.conn
        now = to_iso(utcnow())  # before reading: what is added meanwhile stays unseen
        labels = project_labels(c)
        items = news.items(c, scope=scope, admin=admin, after=after)
        for x in items:
            x["project"] = (labels.get(x["project_path"]) or project_name_for(x["project_path"])) if x["project_path"] else None
        return {"now": now, "seen": news.seen_at(c, viewer), "unseen": news.unseen(c, viewer, scope=scope, admin=admin),
                "items": items}

    # ---- the usage leaderboard (leaderboard.py): this computer's person against everyone who signed in
    def leaderboard(self, q: dict) -> dict:
        from ...sharing import leaderboard as lb

        by = q.get("by") if q.get("by") in lb.METRICS else "hours"
        days = int(q["days"]) if q.get("days") in {str(d) for d in lb.WINDOWS} else 30
        me = lb.status(self.cfg)
        mine = lb.totals(self.conn, self.cfg)
        out = {"by": by, "days": days, "me": me, "rows": [], "error": None, "signing_in": self._lb_pending() is not None,
               "mine": {"hours": mine[f"h{days}"], "sessions": mine[f"s{days}"], "tokens": mine[f"t{days}"]}}
        try:
            rows = sorted(lb.board(by, days), key=lambda r: r[by], reverse=True)
        except lb.LeaderboardError as e:
            out["error"] = str(e)
            return out
        out["rows"] = [{"rank": i + 1, **r, "me": bool(me.get("joined") and r["uid"] == me.get("uid"))}
                       for i, r in enumerate(rows)]
        return out

    def _lb_pending(self) -> dict | None:
        """The sign-in this dashboard started and waits to hear back from, while it may still come."""
        s = self._lb_signin
        return s if s and s["expires"] > time.time() else None

    def action_leaderboard_signin(self, return_to: str, lang: str) -> dict:
        """Start a sign-in: the hosted page's address, carrying this dashboard's address and a one-time state."""
        from ...sharing import leaderboard as lb

        state = lb.new_state()
        self._lb_signin = {"state": state, "expires": time.time() + lb.SIGNIN_TTL}
        return {"url": lb.signin_url(return_to, state, "ja" if lang == "ja" else "en")}

    def action_leaderboard_finish(self, body: dict) -> dict:
        """The sign-in page came back: keep its session if it carries the state this dashboard made, then send."""
        from ...sharing import leaderboard as lb

        pending = self._lb_pending()
        if not lb.same_state(pending and pending["state"], body.get("state")):
            raise lb.LeaderboardError("this sign-in wasn't started here, or took too long: start again")
        self._lb_signin = None  # one use
        lb.sign_in(self.cfg, body.get("token"))
        lb.send(self.cfg, self.conn, force=True)
        return lb.status(self.cfg)

    def action_leaderboard_cancel(self) -> None:
        self._lb_signin = None

    def action_leaderboard_send(self) -> dict:
        from ...sharing import leaderboard as lb

        lb.send(self.cfg, self.conn, force=True)
        return lb.status(self.cfg)

    def action_leaderboard_name(self, body: dict) -> dict:
        from ...sharing import leaderboard as lb

        lb.rename(self.cfg, self.conn, body.get("name"))
        return lb.status(self.cfg)

    def action_leaderboard_leave(self) -> dict:
        from ...sharing import leaderboard as lb

        lb.leave(self.cfg)
        return lb.status(self.cfg)

    def _k(self, k: dict) -> dict:
        k["tags"] = loads(k.pop("tags_json", None), []) or []
        return self._reason(k)

    @staticmethod
    def _reason(k: dict) -> dict:
        from ...analysis.session import case_of
        from ...knowledge.learning import topics_for

        raw_case = loads(k.pop("case_json", None), k.get("case"))  # lesson material: a case file, principle, diagram
        k["case"] = case_of({"kind": k.get("kind"), "case": raw_case}) if isinstance(raw_case, dict) else None
        k["learning_topics"] = topics_for(k)
        if k.get("stage_reason"):
            k["stage_reason"] = reason_text(k["stage_reason"])
        if k.get("source") == "team":  # a teammate's lesson: the computers whose sessions stated it
            from ...sharing.pull import team_from

            k["team_from"] = team_from(k)
        return k

    def _session_rows(self, where: str, params: list, order: str, limit: int, offset: int = 0) -> list[dict]:
        rows = []
        for r in self.conn.execute(
            f"SELECT {SESSION_LIST_COLS} FROM sessions WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset]):
            d = dict(r)
            d["tags"] = loads(d.pop("tags_json", None), []) or []
            d["analysis_reason"] = analysis_reason_text(d.get("analysis_reason"))
            rows.append(d)
        return self._label_who(rows)

    def sessions(self, q: dict, limited: bool = False) -> dict:
        where, params = ["1=1"], []
        if q.get("project"):
            where.append("project_path = ?")
            params.append(q["project"])
        elif str(q.get("group") or "").isdigit():  # every project in one of your groups (groups.py)
            from ...projects.groups import paths_in

            paths = paths_in(self.conn, int(q["group"]))
            where.append(f"project_path IN ({','.join('?' * len(paths))})" if paths else "0")
            params += paths
        if q.get("outcome"):
            if q["outcome"] == "none":
                where.append("outcome IS NULL")
            else:
                where.append("outcome = ?")
                params.append(q["outcome"])
        if q.get("status"):
            where.append("analysis_status = ?")
            params.append(q["status"])
        if q.get("source"):
            where.append("source = ?")
            params.append(q["source"])
        if q.get("agent"):
            where.append("agent = ?")
            params.append(q["agent"])
        if q.get("who"):  # a person's or a computer's sessions, on a hub with people
            clause, p = self._who_clause(q["who"])
            where.append(clause)
            params += p
        if q.get("screen") in ("analyze", "maybe", "skip"):  # imported chats, as screening sorted them (screen.py)
            where.append("screen_verdict = ? AND screen_sig = files_sig")
            params.append(q["screen"])
        elif q.get("screen") == "none":
            from ...analysis.screen import SOURCES

            where.append(f"source IN ({','.join('?' * len(SOURCES))}) AND analysis_status != 'done' "
                         "AND (screen_sig IS NULL OR screen_sig != files_sig)")
            params += SOURCES
        since = q.get("since") or self._since_from_days(q.get("days"))
        if since:
            where.append("started_at >= ?")
            params.append(since.replace("+00:00", "Z"))
        if q.get("until"):
            where.append("started_at < ?")
            params.append(q["until"].replace("+00:00", "Z"))
        if q.get("q") and limited:  # transcripts aren't theirs to search: titles and summaries only
            for term in q["q"].split()[:8]:
                like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                where.append("(title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')")
                params += [like, like]
        elif q.get("q"):
            ids = [s["session_id"] for s in search_sessions(self.conn, q["q"], project=q.get("project"), limit=500)]
            if not ids:
                return {"total": 0, "items": []}
            where.append(f"id IN ({','.join('?' * len(ids))})")
            params += ids
        sort = q.get("sort") if q.get("sort") in SORTABLE else "started_at"
        order = "ASC" if q.get("order") == "asc" else "DESC"
        w = " AND ".join(where)
        total = self.conn.execute(f"SELECT COUNT(*) FROM sessions WHERE {w}", params).fetchone()[0]
        if q.get("ids_only"):  # "select all matching" in the list: the ids, not the rows
            rows = self.conn.execute(f"SELECT id FROM sessions WHERE {w} "
                                     f"ORDER BY {sort} {order} NULLS LAST LIMIT {MAX_SELECTION}", params)
            return {"total": total, "ids": [r[0] for r in rows]}
        limit = min(int(q.get("limit") or 50), 500)
        offset = int(q.get("offset") or 0)
        items = self._session_rows(w, params, f"{sort} {order} NULLS LAST", limit, offset)
        return {"total": total, "items": items}

    def session_summary(self, sid: str) -> dict | None:
        """A session as someone limited to projects sees it (access.py): its summary and project lessons."""
        from ...sharing.access import session_page

        real = self.conn.execute("SELECT id FROM sessions WHERE id = ?", (sid,)).fetchone()
        if not real:
            return None
        s = session_record(self.conn, real[0])
        s["knowledge"] = [self._reason(k) for k in s["knowledge"]]
        return session_page(s)

    def session(self, sid: str) -> dict | None:
        real = resolve_session_id(self.conn, sid)
        if not real:
            return None
        s = session_record(self.conn, real)
        s["knowledge"] = [self._reason(k) for k in s["knowledge"]]
        s["analysis_reason"] = analysis_reason_text(s.get("analysis_reason"))
        from ...analysis.queue import waiting_reason

        row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (real,)).fetchone()
        why = waiting_reason(self.conn, self.cfg, row) if row else None
        s["waiting"] = {"code": why[0], "text": why[1]} if why else None
        from ...runtime.statusline import session_usage

        s["usage"] = session_usage(row["statusline_json"]) if row else None  # None: not recorded, never zero
        s["api_calls"] = [dict(r) for r in self.conn.execute(
            "SELECT ts, agent_id, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd "
            "FROM api_calls WHERE session_id = ? ORDER BY ts", (real,))]
        s["markers"] = [dict(r) for r in self.conn.execute(
            "SELECT seq, ts, kind, text FROM events WHERE session_id = ? AND agent_id = '' AND kind IN ('prompt', 'compact', 'interrupt') "
            "ORDER BY seq", (real,))]
        s["tool_stats"] = [dict(r) for r in self.conn.execute(
            "SELECT name, COUNT(*) n, SUM(is_error) errors, AVG(duration_ms) avg_ms, SUM(duration_ms) total_ms "
            "FROM tool_calls WHERE session_id = ? GROUP BY name ORDER BY n DESC", (real,))]
        s["analyses"] = [dict(r) for r in self.conn.execute(
            "SELECT kind, started_at, finished_at, model, status, error, input_chars, chunks, cost_usd, duration_ms "
            "FROM analyses WHERE target = ? ORDER BY id DESC LIMIT 10", (real,))]
        s["outputs"] = self._artifacts(session_id=real)[0]
        s["agents"] = [{"agent_id": "", "label": tr("Main thread")}] + [
            {"agent_id": a["agent_id"], "label": f"{a.get('agent_type') or 'agent'}: {a.get('description') or a['agent_id']}"}
            for a in s["subagents"]]
        return s

    def events(self, sid: str, q: dict) -> dict:
        real = resolve_session_id(self.conn, sid)
        if not real:
            return {"total": 0, "items": []}
        agent = q.get("agent", "")
        kinds = [k for k in (q.get("kinds") or "").split(",") if k]
        where, params = ["session_id = ?", "agent_id = ?"], [real, agent]
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            params += kinds
        w = " AND ".join(where)
        total = self.conn.execute(f"SELECT COUNT(*) FROM events WHERE {w}", params).fetchone()[0]
        limit = min(int(q.get("limit") or 400), 2000)
        offset = int(q.get("offset") or 0)
        if q.get("around"):  # jump to a specific seq
            pos = self.conn.execute(f"SELECT COUNT(*) FROM events WHERE {w} AND seq < ?", [*params, int(q["around"])]).fetchone()[0]
            offset = max(0, pos - 20)
        items = []
        for r in self.conn.execute(
            f"SELECT seq, ts, role, kind, tool_name, tool_use_id, is_error, text, meta_json FROM events WHERE {w} "
            f"ORDER BY seq LIMIT ? OFFSET ?", [*params, limit, offset]):
            d = dict(r)
            d["meta"] = loads(d.pop("meta_json", None), None)
            items.append(d)
        return {"total": total, "offset": offset, "items": items}

    def matches(self, sid: str, q: dict) -> list[dict]:
        """Every transcript event in one session that mentions the query, in transcript order (main thread first)."""
        real = resolve_session_id(self.conn, sid)
        if not real or not (q.get("q") or "").strip():
            return []
        hits = search_events(self.conn, q["q"], session_id=real, limit=500)
        hits.sort(key=lambda e: (e["agent_id"] != "", e["agent_id"], e["seq"]))
        return [{k: e[k] for k in ("seq", "agent_id", "kind", "tool_name", "ts", "snippet")} for e in hits]

    def projects(self, scope: list[str] | None = None) -> list[dict]:
        """Every project with sessions here, most recent first; on a hub, then the ones set up there that have none yet
        (only those in `scope`, for a person limited to some projects)."""
        from ...core.paths import project_name_for
        from ...projects.groups import assign
        from ...projects.routing import declared_projects
        from ...sharing.projects import hub_project_of

        labels = project_labels(self.conn)
        shared = set(declared_projects(self.conn))
        # per project: active time in each of the last 12 weeks (oldest first), outcomes and agents
        weekly: dict[str, list[float]] = {}
        for r in self.conn.execute(
                "SELECT project_path, CAST((julianday('now') - julianday(started_at)) / 7 AS INTEGER) wk, SUM(active_s) a "
                "FROM sessions WHERE started_at >= ? GROUP BY project_path, wk", (to_iso(utcnow() - timedelta(days=84)),)):
            if r["wk"] is not None and 0 <= r["wk"] < 12:
                weekly.setdefault(r["project_path"] or "", [0.0] * 12)[11 - r["wk"]] += r["a"] or 0
        outcomes: dict[str, dict] = {}
        for r in self.conn.execute("SELECT project_path, COALESCE(outcome, 'not analyzed') o, COUNT(*) n FROM sessions "
                                   "WHERE source != 'history' GROUP BY 1, 2"):
            outcomes.setdefault(r["project_path"] or "", {})[r["o"]] = r["n"]
        agents: dict[str, dict] = {}
        for r in self.conn.execute("SELECT project_path, agent, COUNT(*) n FROM sessions GROUP BY 1, 2"):
            agents.setdefault(r["project_path"] or "", {})[r["agent"] or "claude"] = r["n"]
        rows = []
        for r in self.conn.execute(
            "SELECT project_path, project_name, COUNT(*) sessions, SUM(n_prompts) prompts, SUM(active_s) active_s, "
            "SUM(est_cost_usd) cost, SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) tokens, "
            "MIN(started_at) first, MAX(started_at) last, SUM(CASE WHEN analysis_status='done' THEN 1 ELSE 0 END) analyzed "
            "FROM sessions GROUP BY project_path ORDER BY last DESC"):
            d = dict(r)
            d["label"] = labels.get(d["project_path"] or "", d["project_name"])
            d["knowledge"] = self.conn.execute(
                "SELECT COUNT(*) FROM knowledge WHERE project_path = ? AND status = 'active'", (d["project_path"],)).fetchone()[0]
            kb = self.conn.execute("SELECT updated_at, n_items FROM project_kb WHERE project_path = ?", (d["project_path"],)).fetchone()
            d["kb_updated"] = kb["updated_at"] if kb else None
            d["exists"] = bool(d["project_path"]) and Path(d["project_path"]).exists()
            key = d["project_path"] or ""
            d["weekly"], d["outcomes"], d["agents"] = weekly.get(key, [0.0] * 12), outcomes.get(key, {}), agents.get(key, {})
            d["shared"] = key in shared  # set up on this hub: its sessions and lessons go to the people given it
            rows.append(d)
        have = {d["project_path"] for d in rows}
        for path in sorted(shared - have, key=lambda p: project_name_for(p).lower()):  # set up, nothing filed yet
            if scope is None or path in scope:
                rows.append({"project_path": path, "project_name": project_name_for(path), "label": project_name_for(path),
                             "sessions": 0, "prompts": 0, "active_s": 0, "cost": 0, "tokens": 0, "first": None, "last": None,
                             "analyzed": 0, "knowledge": 0, "kb_updated": None, "exists": Path(path).exists(),
                             "weekly": [0.0] * 12, "outcomes": {}, "agents": {}, "shared": True})
        on_hub = hub_project_of(self.cfg, [d["project_path"] for d in rows], self.conn)  # a computer that sends to a hub
        for d in rows:  # the hub project it's in, by the hub's name for it
            d["hub"] = on_hub.get(d["project_path"])
        placed = assign(self.conn, [d["project_path"] for d in rows if d["project_path"]])
        for d in rows:  # the group it's listed under (groups.py), and whether by hand or by its folder
            d["group"], d["group_by"] = placed.get(d["project_path"], (None, None))
        return rows

    def project_groups(self) -> dict:
        """The groups; home: a rule typed as ~/… shows as it'll be kept; hub: on a computer that sends to a hub, the
        hub's projects as of the last push (which a group can be shared as, and the Projects lists show this computer's
        projects under), with the team's sessions in each and the hub's address."""
        from ...projects.groups import groups

        return {"groups": groups(self.conn), "home": str(Path.home()),
                "hub": self._hub_choices() if self.cfg.is_spoke else None}

    def _billed(self) -> bool:
        """Analysis goes to a model provider's API with your key, which bills each call (not a local Ollama, and not a
        coding agent's own login)."""
        from ...analysis.runners.factory import AGENTS

        backend = self.cfg.analysis.backend
        return backend in BACKENDS and backend not in AGENTS and not make_runner(self.cfg).local()

    def _hub_choices(self) -> dict:
        from ...sharing.projects import last_folders

        seen = last_folders(self.cfg) or {}
        return {"name": seen.get("hub"), "url": self.cfg.hub_url or None, "share": self.cfg.hub_share,
                "projects": [{"path": p["path"], "name": p.get("name") or Path(p["path"]).name,
                              "sessions": p["sessions"] if isinstance(p.get("sessions"), int) else None}
                             for p in seen.get("projects") or [] if isinstance(p, dict) and p.get("path")]}

    def action_project_groups(self, verb: str, body: dict) -> tuple[dict, int]:
        """Make, change or remove a group of projects, or move projects into one (groups.py)."""
        from ...projects import groups

        def gid(v):
            try:
                return int(v) if v is not None and v != "" else None
            except (TypeError, ValueError):
                raise groups.GroupError("That group no longer exists.") from None

        try:
            if verb == "save":
                members = body.get("members")
                new = groups.save(self.conn, gid(body.get("id")), str(body.get("name") or ""), body.get("folders") or [],
                                  [str(p) for p in members] if isinstance(members, list) else None)
                return {"ok": True, "id": new}, 200
            if verb == "delete":
                groups.delete(self.conn, gid(body.get("id")))
                return {"ok": True}, 200
            if verb == "share":  # shared as one project on the hub this computer sends to, from its next push
                project = str(body.get("hub_project") or "") or None
                if project and not self.cfg.is_spoke:
                    return {"error": tr("This computer doesn't send to a hub.")}, 400
                if project and project not in {p["path"] for p in self._hub_choices()["projects"]}:
                    return {"error": tr("The hub has no such project, or you don't see it.")}, 400
                groups.share(self.conn, gid(body.get("id")), project)
                return {"ok": True}, 200
            paths = body.get("paths")
            if not isinstance(paths, list) or not paths:
                return {"error": tr("Pick a project to move.")}, 400
            groups.move(self.conn, [str(p) for p in paths], gid(body.get("group")))
            return {"ok": True}, 200
        except groups.GroupError as exc:
            return {"error": tr(str(exc))}, 400

    def project(self, path: str) -> dict | None:
        c = self.conn
        if path == GLOBAL:
            kb = c.execute("SELECT * FROM project_kb WHERE project_path = ?", (GLOBAL,)).fetchone()
            cited = sorted({i for sec in (loads(kb["kb_json"], {}) or {}).get("sections", []) if isinstance(sec, dict)
                            for it in sec.get("items") or [] if isinstance(it, dict)
                            for i in it.get("sources") or [] if isinstance(i, int)}) if kb else []
            knowledge = []
            for i in range(0, len(cited), 500):  # the sources the playbook cites, so each bullet links to them
                chunk = cited[i:i + 500]
                knowledge += [dict(r) for r in c.execute(
                    f"SELECT id, kind, title, project_name, session_id, agent FROM knowledge WHERE id IN ({','.join('?' * len(chunk))})",
                    chunk)]
            return {"project_path": GLOBAL, "label": tr("Global playbook"), "kb": dict(kb) if kb else None,
                    "sessions": [], "knowledge": knowledge, "stats": {}}
        stats = c.execute(
            "SELECT project_name, COUNT(*) sessions, SUM(n_prompts) prompts, SUM(active_s) active_s, SUM(est_cost_usd) cost, "
            "SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) tokens, SUM(lines_added) lines_added, "
            "SUM(lines_removed) lines_removed, MIN(started_at) first, MAX(started_at) last FROM sessions WHERE project_path = ?",
            (path,)).fetchone()
        if not stats or not stats["sessions"]:
            return None
        from ...knowledge.glossary import glossary_entries
        from ..exports.diagrams import for_kb
        kb = c.execute("SELECT * FROM project_kb WHERE project_path = ?", (path,)).fetchone()
        labels = project_labels(c)
        from ...projects.groups import group_of
        from ...projects.routing import declared_projects
        from ...sharing.projects import hub_project_of

        return {
            "project_path": path,
            "shared": path in declared_projects(c),
            "hub": hub_project_of(self.cfg, [path], c).get(path),
            "group": group_of(c, path),
            "label": labels.get(path, stats["project_name"]),
            "stats": dict(stats),
            "kb": dict(kb) if kb else None,
            "diagram": for_kb(kb["kb_json"]) if kb else None,
            "artifacts": self._artifact_summary(path),
            "sessions": self._session_rows("project_path = ?", [path], "started_at DESC", 200),
            "knowledge": [self._k(dict(r)) for r in c.execute(
                "SELECT k.*, s.title session_title FROM knowledge k LEFT JOIN sessions s ON s.id = k.session_id "
                "WHERE k.project_path = ? AND k.status = 'active' ORDER BY k.pinned DESC, k.id DESC LIMIT 300", (path,))],
            "glossary": [{"term": g["term"], "category": g["category"], "definition": g["definition"],
                          "context": next((u["context"] for u in g["usage"] if u["project_path"] == path), "")}
                         for g in glossary_entries(c, project=path)],
            "files": [dict(r) for r in c.execute(
                "SELECT f.path, COUNT(*) sessions, SUM(f.edits + f.writes) edits, SUM(f.lines_added) added, SUM(f.lines_removed) removed "
                "FROM session_files f JOIN sessions s ON s.id = f.session_id WHERE s.project_path = ? AND (f.edits + f.writes) > 0 "
                "GROUP BY f.path ORDER BY edits DESC LIMIT 25", (path,))],
        }

    def file_sessions(self, path: str, limit: int = 50) -> dict:
        """The sessions that read or changed one file, newest first (the VS Code extension's per-file history)."""
        path = clean_abs_path(path)
        if not path:
            return {"path": path, "total": 0, "sessions": []}
        c = self.conn
        # most agents record absolute paths, on Windows with either slash and either case of drive letter (C:\x, c:/x);
        # Codex sometimes records them relative to the session's folder
        spelled = path_variants(path)
        where, params = [f"f.path IN ({','.join('?' * len(spelled))})"], spelled
        for (root,) in c.execute(f"SELECT DISTINCT project_path FROM sessions WHERE {abs_path_sql('project_path')}"):
            base = normalize_project_path(root).rstrip("/") + "/"
            if is_abs_path(root) and path.startswith(base):
                rel = path[len(base):]
                rels = [rel] if path_kind(path) == "posix" or "/" not in rel else [rel, rel.replace("/", "\\")]
                where.append(f"(s.project_path = ? AND f.path IN ({','.join('?' * len(rels))}))")
                params += [root, *rels]
        stats = {r["session_id"]: dict(r) for r in c.execute(
            "SELECT f.session_id, SUM(f.reads) reads, SUM(f.edits + f.writes) changes, SUM(f.lines_added) added, "
            "SUM(f.lines_removed) removed FROM session_files f JOIN sessions s ON s.id = f.session_id "
            f"WHERE {' OR '.join(where)} GROUP BY f.session_id", params)}
        ids = list(stats)[:5000]
        rows = self._session_rows(f"id IN ({','.join('?' * len(ids))})", ids, "started_at DESC", limit) if ids else []
        for r in rows:
            r["file"] = {k: stats[r["id"]][k] for k in ("reads", "changes", "added", "removed")}
        return {"path": path, "total": len(stats), "sessions": rows}

    def folder_files(self, root: str, limit: int = 200, existing: bool = False, ignored: bool = True) -> dict:
        """The files under a folder that sessions read or changed, most recently touched first (the extension's file list)."""
        root = clean_abs_path(root)
        if not root or root == "/" or (path_kind(root) == "drive" and len(root) == 3):  # a whole disk: C:/
            return {"root": root, "total": 0, "files": []}
        base, c = root.rstrip("/") + "/", self.conn
        cols = "f.path, f.session_id, f.edits + f.writes changes, s.started_at"
        # a range on the indexed path for each way it may be spelled (C:\x, c:/x, …; the character after the separator
        # sorts right after it: '0' after '/', ']' after '\\'), so a big archive isn't scanned
        rows = []
        for spelled in path_variants(root):
            sep = "\\" if path_kind(root) != "posix" and "\\" in spelled else "/"
            rows += [{**dict(r), "path": normalize_project_path(r["path"])} for r in c.execute(
                f"SELECT {cols} FROM session_files f JOIN sessions s ON s.id = f.session_id WHERE f.path >= ? AND f.path < ?",
                (spelled + sep, spelled + chr(ord(sep) + 1)))]
        # Codex sometimes records paths relative to the session's folder: a folder inside this one, or one containing it
        for (folder,) in c.execute(f"SELECT DISTINCT project_path FROM sessions WHERE {abs_path_sql('project_path')}"):
            folder_base = normalize_project_path(folder).rstrip("/") + "/"
            if not is_abs_path(folder) or (not folder_base.startswith(base) and not base.startswith(folder_base)):
                continue
            windows = path_kind(folder) != "posix"
            for r in c.execute(f"SELECT {cols} FROM session_files f JOIN sessions s ON s.id = f.session_id "
                               f"WHERE s.project_path = ? AND NOT {abs_path_sql('f.path')}", (folder,)):
                full = clean_abs_path(folder_base + (r["path"].replace("\\", "/") if windows else r["path"]))
                if full.startswith(base):
                    rows.append({**dict(r), "path": full})
        files: dict[str, dict] = {}
        for r in rows:
            f = files.setdefault(r["path"], {"sessions": set(), "changed": set(), "last": ""})
            f["sessions"].add(r["session_id"])
            if r["changes"]:
                f["changed"].add(r["session_id"])
            f["last"] = max(f["last"], r["started_at"] or "")
        paths = sorted(files, key=lambda p: (files[p]["last"], len(files[p]["sessions"])), reverse=True)
        if existing:  # leave out files since deleted, and the scratch paths agents wrote along the way
            paths = [p for p in paths if os.path.isfile(p)]
        if not ignored:  # and what git ignores: screenshots, build output, caches
            hidden = git_ignored(root, paths)
            paths = [p for p in paths if p not in hidden]
        # the ids let a folder tree count each session once across the files beneath it
        return {"root": root, "total": len(paths), "files": [
            {"path": p, "rel": p[len(base):], "sessions": len(files[p]["sessions"]), "changed": len(files[p]["changed"]),
             "last": files[p]["last"], "session_ids": sorted(files[p]["sessions"]),
             "changed_ids": sorted(files[p]["changed"])} for p in paths[:limit]]}

    def _artifacts(self, **filters) -> tuple[list[dict], dict[str, int]]:
        from ...artifacts.queries import query
        from ...core.machine import local_machine_id

        return query(self.conn, local_machine=local_machine_id(self.cfg), **filters)

    def _artifact_summary(self, project: str) -> dict:
        items, counts = self._artifacts(project=project)
        return {"total": len(items), "counts": counts, "recent": items[:8]}

    def artifacts(self, q: dict) -> dict:
        """The artifacts gallery: what sessions made, newest first, filtered by kind, project, agent, words and status."""
        items, counts = self._artifacts(kind=q.get("kind") or None, project=q.get("project") or None,
                                        agent=q.get("agent") or None, q=(q.get("q") or "").strip() or None)
        if q.get("hide_gone") == "1":
            items = [i for i in items if i["status"] != "gone"]
        limit, offset = min(int(q.get("limit") or 100), 500), int(q.get("offset") or 0)
        projects = [dict(r) for r in self.conn.execute(
            "SELECT s.project_path, MAX(s.project_name) project_name, COUNT(DISTINCT a.key) n FROM artifacts a "
            "JOIN sessions s ON s.id = a.session_id GROUP BY s.project_path ORDER BY n DESC")]
        labels = project_labels(self.conn)
        for p in projects:
            p["label"] = labels.get(p["project_path"], p["project_name"])
        return {"total": len(items), "counts": counts, "items": items[offset:offset + limit], "projects": projects}

    def artifact_open(self, artifact_id: int) -> dict | None:
        from ...artifacts.files import open_file
        from ...core.machine import local_machine_id

        return open_file(self.conn, artifact_id, local_machine_id(self.cfg))

    def artifact_reveal(self, artifact_id: int, how: str) -> str | None:
        from ...artifacts.files import reveal
        from ...core.machine import local_machine_id

        return reveal(self.conn, artifact_id, how, local_machine_id(self.cfg))

    def artifact_file(self, artifact_id: int) -> tuple[bytes, str] | None:
        from ...artifacts.queries import preview_file
        from ...core.machine import local_machine_id

        return preview_file(self.conn, artifact_id, local_machine_id(self.cfg), cache_dir=self.cfg.home / "thumbs")

    def knowledge(self, q: dict) -> dict:
        rows = search_knowledge(self.conn, q.get("q") or None, project=q.get("project") or None, kind=q.get("kind") or None,
                                include_inactive=q.get("status") == "all", limit=min(int(q.get("limit") or 200), 1000),
                                sessions=self._who_clause(q["who"]) if q.get("who") else None,
                                source=q.get("source") or None, cases=q.get("cases") == "1",
                                lessons=q.get("lessons") == "1", offset=max(0, int(q.get("offset") or 0)))
        if self._whos() is not None and rows:  # each lesson's person, through the session it came from
            ids = list({r["session_id"] for r in rows if r.get("session_id")})
            machine = {}
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                machine.update(self.conn.execute(f"SELECT id, machine_id FROM sessions WHERE id IN ({','.join('?' * len(chunk))})",
                                                 chunk).fetchall())
            for r in rows:
                r["session_machine"] = machine.get(r.get("session_id"))
            self._label_who([r for r in rows if r.get("session_id") in machine], "session_machine")
            for r in rows:
                r.pop("session_machine", None)
        counts = {r["kind"]: r["n"] for r in self.conn.execute(
            "SELECT kind, COUNT(*) n FROM knowledge WHERE status = 'active' GROUP BY kind")}
        sources = dict(self.conn.execute(
            "SELECT source, COUNT(*) FROM knowledge WHERE status = 'active' GROUP BY source").fetchall())
        from ...analysis.session import CASE_FILE_SQL

        cases = self.conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE status = 'active' AND " + CASE_FILE_SQL).fetchone()[0]
        return {"items": [self._reason(r) for r in rows], "counts": counts, "sources": sources, "cases": cases}

    def learning_interests(self, viewer: dict | None, q: dict) -> dict:
        from ...core.machine import local_machine_id
        from ...knowledge.learning import interest_profile

        return interest_profile(self.conn, viewer_id=(viewer or {}).get("id"), machine_id=local_machine_id(self.cfg),
                                project=q.get("project") or "")

    def learning_glance(self, viewer: dict | None, q: dict) -> dict:
        """The Knowledge page's Learn band (learning.glance), without sending every lesson to the browser."""
        from ...knowledge.learning import glance

        rows = search_knowledge(self.conn, project=q.get("project") or None, lessons=True, limit=10000)
        return glance([self._reason(r) for r in rows], self.learning_interests(viewer, q)["interests"])

    def search(self, q: dict) -> dict:
        query = q.get("q") or ""
        project = q.get("project") or None
        offset = int(q.get("offset") or 0)
        found = search_all(self.conn, query, project=project, sort=q.get("sort") or "hits", offset=offset, limit=25)
        found["knowledge"] = [self._reason(r) for r in search_knowledge(self.conn, query, project=project, limit=20)] \
            if not offset else []
        return found

    def glossary(self, q: dict) -> dict:
        from ...knowledge.glossary import CATEGORIES, glossary_entries

        items = glossary_entries(self.conn, query=q.get("q") or None, project=q.get("project") or None,
                                 category=q.get("category") or None)
        counts = {r[0]: r[1] for r in self.conn.execute("SELECT category, COUNT(*) FROM glossary GROUP BY category")}
        total = self.conn.execute("SELECT COUNT(*) FROM glossary").fetchone()[0]
        return {"items": items, "counts": counts, "categories": CATEGORIES, "total": total}

    def glossary_terms(self) -> list[dict]:
        """Compact term list the UI uses to link terms wherever they appear."""
        return [{"term": r["term"], "aliases": loads(r["aliases_json"], []) or [], "definition": r["definition"],
                 "category": r["category"]} for r in self.conn.execute("SELECT term, aliases_json, definition, category FROM glossary")]

    def map(self) -> dict:
        from ...knowledge.glossary import map_data

        return map_data(self.conn)

    def systems(self) -> dict:
        """The Systems map's top level: groups, systems and the links between them (without each system's parts)."""
        from ...projects.systems import cached, landscape

        data = landscape(cached(self.conn, self.cfg))
        labels = project_labels(self.conn)
        for s in data["systems"]:
            s["project"] = next((p for p in s["project_paths"] if p in labels), None)
        return data

    def system(self, key: str) -> dict | None:
        """One system's parts, connections and links, with the evidence for each."""
        from ...projects.systems import CHAT_SOURCES, cached, system

        out = system(cached(self.conn, self.cfg), key)
        if out:
            labels = project_labels(self.conn)
            out["project"] = next((p for p in out["project_paths"] if p in labels), None)
            out["recent"] = self._session_rows(
                f"project_path IN ({','.join('?' * len(out['project_paths']))}) AND source NOT IN ({','.join('?' * len(CHAT_SOURCES))})",
                [*out["project_paths"], *CHAT_SOURCES], "started_at DESC", 6) if out["project_paths"] else []
        return out

    def action_themes(self, force: bool) -> bool:
        from ...knowledge.glossary import build_themes

        def job(progress):
            results = build_themes(self.cfg, force=force, concurrency=self.cfg.analysis.concurrency + 1, progress=progress)
            done = sum(1 for v in results.values() if isinstance(v, int))
            failed = [f"{c}: {v}" for c, v in results.items() if not isinstance(v, int)]
            return f"themes for {done}/{len(results)} categories" + (f" ({'; '.join(failed)[:200]})" if failed else "")

        return self.jobs.start("themes", job)

    def action_glossary(self, path: str | None) -> bool:
        from ...knowledge.glossary import build_glossaries

        def job(progress):
            conn = connect(self.cfg.db_path)
            try:
                targets = [path] if path else [r[0] for r in conn.execute(
                    "SELECT DISTINCT project_path FROM knowledge WHERE status = 'active' AND project_path IS NOT NULL")] + [GLOBAL]
            finally:
                conn.close()
            results = build_glossaries(self.cfg, targets, concurrency=self.cfg.analysis.concurrency + 1, progress=progress)
            built = sum(1 for v in results.values() if isinstance(v, int))
            return f"glossary rebuilt for {built}/{len(targets)} projects"

        return self.jobs.start(f"glossary:{path or 'all'}", job)

    def connectors(self) -> list[dict]:
        from ...runtime.connectors import all_status

        return all_status(self.cfg, self.conn)

    def mcp_info(self) -> dict:
        """What the MCP page shows: the command that starts the server, its tools, and config to paste."""
        from ...runtime.connectors import mcp_server_entry
        from ...runtime.install import MCP_NAME, executable
        from ..mcp import INSTRUCTIONS, TOOLS

        entry = mcp_server_entry(executable())
        cmd, args = entry["command"], entry["args"]
        toml_args = ", ".join(json.dumps(a) for a in args)
        shell = " ".join(shlex.quote(x) for x in [cmd, *args])
        return {
            "command": cmd, "args": args, "instructions": INSTRUCTIONS,
            "tools": [{"name": t["name"], "description": t["description"],
                       "params": sorted(t["inputSchema"].get("properties", {}),  # required first, then as declared
                                        key=lambda k: k not in t["inputSchema"].get("required", [])),
                       "required": t["inputSchema"].get("required", [])} for t in TOOLS],
            "snippets": {
                "json": json.dumps({"mcpServers": {MCP_NAME: entry}}, indent=2),
                "vscode": json.dumps({"servers": {MCP_NAME: {"type": "stdio", **entry}}}, indent=2),
                "codex": f'[mcp_servers.{MCP_NAME}]\ncommand = {json.dumps(cmd)}\nargs = [{toml_args}]\n',
                "claude": f"claude mcp add --scope user {MCP_NAME} -- {shell}",
            },
        }

    def mcp_clients(self) -> list[dict]:
        from ...runtime.connectors import mcp_clients_status

        return mcp_clients_status()

    def action_connector(self, name: str, action: str) -> dict:
        from ...core.config import load_config
        from ...runtime.connectors import MCP_CLIENTS, connect, disconnect
        from ...runtime.install import executable

        if name in MCP_CLIENTS:  # only another tool's MCP config changes: no config reload, nothing to sync
            return {"actions": connect(self.cfg, name, executable()) if action == "connect" else disconnect(self.cfg, name),
                    "sync_started": False}
        if action == "connect":
            actions = connect(self.cfg, name, executable())
        elif action == "disconnect":
            actions = disconnect(self.cfg, name)
        else:
            raise KeyError(action)
        self.cfg = load_config(self.cfg.home)  # the connector rewrote config.toml
        self._cfg_sig = self._config_sig()
        started = self.action_sync() if action == "connect" else False
        return {"actions": actions, "sync_started": started}

    def reviews(self) -> dict:
        from ...knowledge.reviews import _stats, normalize_review, review_ready, week_bounds, week_glance

        items = [dict(r) for r in self.conn.execute(
            "SELECT period, start, end, created_at, model, n_sessions, markdown, review_json, stats_json FROM reviews "
            "ORDER BY period DESC")]
        for r in items:
            r["stats"] = loads(r.pop("stats_json", None), {}) or {}
            r["review"] = normalize_review(loads(r.pop("review_json", None), {}) or {})
            if r["start"] and r["end"]:  # the charts come from the sessions table, so they follow later analysis
                r["glance"] = week_glance(self.conn, r["start"], r["end"])
                r["stats"] = _stats(self.conn, r["start"], r["end"])
                prev_start = to_iso(datetime.fromisoformat(r["start"].replace("Z", "+00:00")) - timedelta(days=7))
                r["previous"] = _stats(self.conn, prev_start, r["start"])
        ready, why = review_ready(self.conn)
        return {"items": items, "last_week": week_bounds(None)[0], "current_week": week_bounds("current")[0],
                "auto_ready": ready, "auto_note": why}

    def knowledge_hub(self) -> dict:
        """The Knowledge section's landing page: a glance at each of its tools."""
        from ...knowledge.reviews import _stats, normalize_review, week_glance

        counts = {r["kind"]: r["n"] for r in self.conn.execute(
            "SELECT kind, COUNT(*) n FROM knowledge WHERE status = 'active' GROUP BY kind")}
        week_ago = to_iso(utcnow() - timedelta(days=7))
        recent = [dict(r) for r in self.conn.execute(
            "SELECT id, kind, title, project_name FROM knowledge WHERE status = 'active' ORDER BY created_at DESC, id DESC LIMIT 4")]
        new_week = self.conn.execute(  # by when the session ran: a backfill imports old sessions' knowledge today
            "SELECT COUNT(*) FROM knowledge k JOIN sessions s ON s.id = k.session_id WHERE k.status = 'active' "
            "AND s.started_at >= ?", (week_ago,)).fetchone()[0]
        cats = {r[0] or "other": r[1] for r in self.conn.execute("SELECT category, COUNT(*) FROM glossary GROUP BY category")}
        top_terms = [dict(r) for r in self.conn.execute(
            "SELECT term, category, n_sessions FROM glossary ORDER BY n_sessions DESC, n_mentions DESC LIMIT 16")]
        map_projects = self.conn.execute("SELECT COUNT(DISTINCT project_path) FROM glossary_usage WHERE project_path != ?",
                                         (GLOBAL,)).fetchone()[0]
        themes = self.conn.execute("SELECT COUNT(*) FROM glossary_themes").fetchone()[0]
        row = self.conn.execute("SELECT period, start, end, n_sessions, review_json, stats_json FROM reviews "
                                "ORDER BY period DESC LIMIT 1").fetchone()
        review = None
        if row:
            data = normalize_review(loads(row["review_json"], {}) or {})
            review = {"period": row["period"], "start": row["start"], "end": row["end"], "headline": data["headline"],
                      "tldr": data["tldr"],
                      "stats": _stats(self.conn, row["start"], row["end"]) if row["start"] else loads(row["stats_json"], {}) or {},
                      "daily": week_glance(self.conn, row["start"], row["end"])["daily"] if row["start"] else []}
        from ...analysis.session import CASE_FILE_SQL

        cases = self.conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE status = 'active' AND " + CASE_FILE_SQL).fetchone()[0]
        return {"knowledge": {"total": sum(counts.values()), "counts": counts, "new_week": new_week, "recent": recent,
                              "cases": cases},
                "glossary": {"total": sum(cats.values()), "categories": cats, "top": top_terms},
                "map": {"projects": map_projects, "themes": themes},
                "review": review, "reviews": self.conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]}

    def action_review(self, week: str | None) -> bool:
        from ...knowledge.reviews import generate_review

        def job(progress):
            conn = connect(self.cfg.db_path)
            try:
                progress("writing weekly review…")
                generate_review(conn, self.cfg, week)
            finally:
                conn.rollback()
                conn.close()
            return "review written"

        return self.jobs.start(f"review:{week or 'last'}", job)

    def status_small(self, limited: bool = False) -> dict:
        from ...analysis.queue import PAUSE_KEY, count_pending
        from ...runtime.update import available, recall

        if limited:  # the hub's own queue, jobs and updates are not theirs to see
            return {"pending": {"ready": 0, "queued": 0}, "analysis": {"auto": False, "max_per_run": 0, "label": ""}, "paused_until": None,
                    "last_sync": None, "jobs": {}, "version": __version__, "update": None, "ui_build": UI_BUILD,
                    "hub_url": None, "limited": True}

        recall(self.conn)  # a check made by another dashboard process, or before a restart
        self._maybe_check_daily()

        pending = count_pending(self.conn, self.cfg)
        return {
            "pending": pending,
            "analysis": {"auto": self.cfg.analysis.auto, "max_per_run": self.cfg.analysis.max_per_run,
                         "label": BACKENDS.get(self.cfg.analysis.backend, BACKENDS["claude"]),
                         "billed": self._billed()},  # an API provider off this computer: every call costs
            "paused_until": kv_get(self.conn, PAUSE_KEY),
            "last_sync": kv_get(self.conn, "last_sync"),
            "jobs": self.jobs.snapshot(),
            "version": __version__,
            "update": available(),
            "ui_build": UI_BUILD,
            "hub_url": self.cfg.hub_url or None,  # set on a computer that sends its sessions to a hub
        }

    def devices(self) -> dict:
        """This computer's role, the computers a hub hears from, and how the dashboard is reachable."""
        from ...core.machine import local_machine
        from ...sharing.identity import read_token
        from ...sharing.projects import last_folders, machines
        from ...sharing.pull import last_team
        from ...sharing.push import last_push

        role = "spoke" if self.cfg.is_spoke else "hub" if read_token(self.cfg) else "single"
        recorded = (last_folders(self.cfg) or {}) if role == "spoke" else {}
        seen = recorded.get("folders") or {}
        folders = [{"folder": f, "project": p, "name": Path(p).name or p, "sessions": (seen.get(f) or {}).get("sessions"),
                    "overridden": (seen.get(f) or {}).get("overridden") or []}
                   for f, p in sorted(self.cfg.hub_folders.items())] if role == "spoke" else []
        return {
            "this": local_machine(self.cfg),
            "role": role,
            "hub_url": self.cfg.hub_url or None,
            "last_push": last_push(self.cfg) if role == "spoke" else None,
            "share": self.cfg.hub_share if role == "spoke" else None,
            "all_folders": self.cfg.hub_all_folders if role == "spoke" else None,
            "accept": self.cfg.hub_accept if role == "hub" else None,
            "team": last_team(self.cfg) if role == "spoke" else None,
            "store": self.team_store_info() if role != "spoke" else None,
            "folders": folders,
            "projects": self._my_hub_projects(folders, recorded) if role == "spoke" else [],
            "hub_name": recorded.get("hub") if role == "spoke" else None,
            "machines": machines(self.conn, self.cfg),
            "path_map": self.cfg.hub_path_map,
            "allowed_hosts": self.cfg.server_allowed_hosts,
            "allowed_users": self.cfg.server_allowed_users,
            "port": self.cfg.server_port,
        }

    def _my_hub_projects(self, folders: list[dict], recorded: dict) -> list[dict]:
        """The hub's projects this computer is in: through a folder added to one, a repository whose git remote the hub
        files there (as of the last push), a group shared as it, or one it left. Its name as the hub gives it."""
        from ...projects.groups import groups

        names = {p["path"]: p.get("name") for p in recorded.get("projects") or [] if isinstance(p, dict) and p.get("path")}
        out: dict[str, dict] = {}

        def row(path: str) -> dict:  # [hub] left spells a Windows project one way (C:/x), the hub perhaps another
            return out.setdefault(normalize_project_path(path), {
                "path": path, "name": names.get(path) or Path(path).name or path, "folders": [], "repos": [], "groups": [],
                "left": normalize_project_path(path) in self.cfg.hub_left})

        for f in folders:
            row(f["project"])["folders"].append(f)
        for path, repos in (recorded.get("remotes") or {}).items():
            if isinstance(repos, list):
                row(path)["repos"] += [r for r in repos if isinstance(r, dict)]
        for g in groups(self.conn):
            if g["hub_project"]:
                row(g["hub_project"])["groups"].append({"id": g["id"], "name": g["name"]})
        for path in self.cfg.hub_left:
            row(path)
        return sorted(out.values(), key=lambda r: (r["left"], r["name"].lower()))

    # ---- Devices › Projects on the hub (a computer that sends to one): join, leave and rejoin, take a folder back
    def hub_project_choices(self) -> dict:
        """For Join a project: the hub's projects (asked now) and this computer's folders that have sessions."""
        from ...sharing.client import handshake
        from ...sharing.protocol import SHARED_AGENTS, HubError

        if not self.cfg.is_spoke:
            return {"error": tr("this computer has not joined a hub")}
        try:
            hello = handshake(self.cfg)
        except HubError as exc:
            return {"error": tr("Can't reach the hub: {error}", error=str(exc))}
        marks = ", ".join("?" for _ in SHARED_AGENTS)
        local = [{"path": r[0], "name": r[1] or Path(r[0]).name, "sessions": r[2]} for r in self.conn.execute(
            f"SELECT project_path, MAX(project_name), COUNT(*) FROM sessions WHERE {abs_path_sql('project_path')} "
            f"AND source NOT IN ('history', 'remote') AND agent IN ({marks}) GROUP BY project_path "
            "ORDER BY MAX(started_at) DESC LIMIT 300", SHARED_AGENTS)]
        projects = [{"path": p["path"], "name": p.get("name") or Path(p["path"]).name, "sessions": p.get("sessions") or 0}
                    for p in hello.get("projects") or [] if isinstance(p, dict) and isinstance(p.get("path"), str)]
        return {"projects": projects, "folders": local, "hub": hello.get("hub")}

    def action_hub_project(self, verb: str, body: dict) -> tuple[dict, int]:
        """add: file a folder here under one of the hub's projects (joining it); remove: take a wrongly added folder
        back, and what it shared; leave and rejoin: stop or start sharing to a project, keeping what it has."""
        from ...sharing.client import add_folder, handshake, leave_project, rejoin_project, remove_folder
        from ...sharing.protocol import FolderError, HubError

        if not self.cfg.is_spoke:
            return {"error": tr("this computer has not joined a hub")}, 400
        out: dict = {"ok": True}
        try:
            if verb == "add":
                folder = str(body.get("folder") or "").strip()
                if not (folder.startswith("~") or is_abs_path(folder)):
                    return {"error": tr("Give the folder's full path, such as /Users/you/code/app.")}, 400
                folder = normalize_project_path(str(Path(folder).expanduser().resolve()))
                hello = handshake(self.cfg)
                project = next((p for p in hello.get("projects") or [] if isinstance(p, dict)
                                and p.get("path") == body.get("project")), None)
                if not project:
                    return {"error": tr("The hub has no such project, or you don't see it.")}, 400
                self.cfg, remote = add_folder(self.cfg, folder, project, hello)
                out.update(folder=folder, name=project.get("name") or Path(project["path"]).name, remote=remote)
            elif verb == "remove":
                self.cfg, out["taken"] = remove_folder(self.cfg, str(body.get("folder") or ""))
            else:
                path = str(body.get("project") or "")
                if not is_abs_path(path) or len(path) > 1024:
                    return {"error": tr("Which project?")}, 400
                path = normalize_project_path(path)
                self.cfg = leave_project(self.cfg, path) if verb == "leave" else rejoin_project(self.cfg, path)
        except FolderError as exc:
            return {"error": exc.shown()}, 400
        except HubError as exc:
            return {"error": tr("Can't reach the hub: {error}", error=str(exc))}, 502
        self._cfg_sig = self._config_sig()
        out["pushing"] = self.action_push()  # the hub hears of it now, and the lessons that come back change with it
        return out, 200

    def action_leave_hub(self) -> dict:
        """Settings › Devices › Leave the hub (`interlatch hub leave`)."""
        from ...sharing.client import leave_hub

        if not self.cfg.is_spoke:
            return {"error": tr("this computer has not joined a hub")}
        url = self.cfg.hub_url
        self.cfg = leave_hub(self.cfg)
        self._cfg_sig = self._config_sig()
        return {"ok": True, "hub": url}

    # ---- Team › Hub settings: what an admin may change about this hub
    def hub_settings(self) -> dict:
        """This hub's name and address, what it takes, and which of them a container's variables set (each start
        writes them again, so the dashboard can't change them)."""
        from ...core.machine import local_machine
        from ...sharing.identity import read_token

        if self.cfg.is_spoke or not read_token(self.cfg):
            return {"error": tr("this computer is not a hub")}
        container = bool(env("CONTAINER"))
        return {"name": self.cfg.hub_name, "default_name": local_machine(self.cfg)["name"],
                "address": self.cfg.hub_address, "accept": self.cfg.hub_accept, "store": self.cfg.hub_store,
                "container": container, "dedicated": self.cfg.hub_dedicated,
                "managed": {"address": container, "name": container and bool((env("HUB_NAME") or "").strip())},
                "home": str(self.cfg.home), "db": str(self.cfg.db_path)}

    def action_hub_settings(self, body: dict, by: dict | None) -> tuple[dict, int]:
        """Rename this hub, or change the address computers and browsers reach it at (invites and sign-in links
        use it). A new address's host name is added to [server] allowed_hosts, as `interlatch hub enable --url` does."""
        from ...core.config import load_config, set_config_value
        from ...sharing import people
        from ...sharing.identity import read_token

        if self.cfg.is_spoke or not read_token(self.cfg):
            return {"error": tr("this computer is not a hub")}, 400
        managed = self.hub_settings()["managed"]
        changed: dict = {}
        if "name" in body:
            if managed["name"]:
                return {"error": tr("INTERLATCH_HUB_NAME in the hub's .env sets its name: change it there.")}, 400
            name = " ".join("".join(c for c in str(body.get("name") or "") if c.isprintable() or c.isspace()).split())
            if len(name) > 80:
                return {"error": tr("A hub's name is up to 80 characters.")}, 400
            if not name and env("CONTAINER"):  # else it would be the container's random host name
                return {"error": tr("Give the hub a name.")}, 400
            changed["name"] = name
        if "address" in body:
            if managed["address"]:
                return {"error": tr("INTERLATCH_HUB_URL in the hub's .env sets its address: change it there.")}, 400
            address = str(body.get("address") or "").strip().rstrip("/")
            parts = urlparse(address)
            if address and (parts.scheme not in ("http", "https") or not parts.hostname or parts.path or parts.query
                            or parts.fragment or parts.username or parts.password):
                return {"error": tr("An address is like https://interlatch.example.com: http or https and a host name, "
                                    "with a port if needed.")}, 400
            changed["address"] = address
        if not changed:
            return {"error": tr("Nothing to change.")}, 400
        if "name" in changed:
            set_config_value(self.cfg, "hub", "name", json.dumps(changed["name"]))
        if "address" in changed:
            set_config_value(self.cfg, "hub", "address", json.dumps(changed["address"]))
            host = (urlparse(changed["address"]).hostname or "").lower()
            if host and host not in ("127.0.0.1", "localhost", "::1") and host not in self.cfg.server_allowed_hosts:
                set_config_value(self.cfg, "server", "allowed_hosts", json.dumps([*self.cfg.server_allowed_hosts, host]))
        self.cfg = load_config(self.cfg.home)
        self._cfg_sig = self._config_sig()
        people.audit(self.conn, people.actor_of(by), "settings", None, **changed)
        self.conn.commit()
        return {"ok": True, **self.hub_settings()}, 200

    # ---- Devices › Team store (team_store.py): the password goes in, never out
    def team_store_info(self) -> dict:
        from ...storage import team as team_store

        return {"enabled": self.cfg.hub_store == "postgres", "driver": team_store.driver_available(),
                "settings": team_store.read_settings(self.cfg), "file": str(team_store.env_path(self.cfg)),
                "sslmodes": list(team_store.SSLMODES)}

    def team_store_status(self) -> dict:
        """What the store holds; asked for after the page shows, since the database may be far away."""
        from ...storage import team as team_store

        if self.cfg.hub_store != "postgres" or self.cfg.is_spoke:
            return {"enabled": False}
        try:
            return {"enabled": True, **team_store.get(self.cfg).status()}
        except team_store.TeamStoreError as exc:
            return {"enabled": True, "error": str(exc)}

    def _store_params(self, body: dict) -> dict:
        from ...storage import team as team_store

        try:
            saved = team_store.connection_params(self.cfg)
        except (team_store.TeamStoreError, OSError):
            saved = None
        return team_store.check_settings(body, saved)

    def action_team_store_test(self, body: dict) -> dict:
        from ...storage import team as team_store

        try:
            return {"ok": True, **team_store.probe(self._store_params(body))}
        except team_store.TeamStoreError as exc:
            return {"ok": False, "error": tr(str(exc))}

    def action_team_store_save(self, body: dict) -> dict:
        """Turn the team store on with these settings (tested first: nothing is saved if they don't work), or off.
        Off keeps the settings, so turning it on again needs no password."""
        from ...core.config import load_config, set_config_value
        from ...storage import team as team_store

        if self.cfg.is_spoke:
            return {"error": tr("this computer sends to a hub: the team store is set up on the hub")}
        if not body.get("enabled"):
            set_config_value(self.cfg, "hub", "store", '""')
            self.cfg = load_config(self.cfg.home)
            self._cfg_sig = self._config_sig()
            return {"ok": True, "store": self.team_store_info()}
        try:
            params = self._store_params(body)
            found = team_store.probe(params)
            if not found["steps"] and not found["can_create"]:
                raise team_store.TeamStoreError(f"user {params['user']} can't create the team's tables in {found['where']}")
        except team_store.TeamStoreError as exc:
            return {"error": tr(str(exc))}
        team_store.write_settings(self.cfg, params)
        set_config_value(self.cfg, "hub", "store", '"postgres"')
        self.cfg = load_config(self.cfg.home)
        self._cfg_sig = self._config_sig()
        return {"ok": True, "store": self.team_store_info(), "status": self.team_store_status()}

    # ---- Settings › Storage (mirror.py): a copy of this archive in Postgres; the password goes in, never out
    def mirror_info(self) -> dict:
        from ...core.config import MIRROR_INCLUDES
        from ...storage import mirror
        from ...storage import postgres as pg

        return {"enabled": mirror.enabled(self.cfg), "include": self.cfg.mirror_include, "schema": self.cfg.mirror_schema,
                "includes": list(MIRROR_INCLUDES), "driver": pg.driver_available(), "install": mirror.INSTALL,
                "settings": mirror.read_settings(self.cfg), "file": str(mirror.env_path(self.cfg)),
                "sslmodes": list(pg.SSLMODES), "last": mirror.last(self.cfg),
                "running": self.jobs.snapshot().get("mirror", {}).get("state") == "running"}

    def mirror_status(self) -> dict:
        """What the mirror holds; asked for after the page shows, since the database may be far away."""
        from ...storage import mirror

        if not mirror.enabled(self.cfg):
            return {"enabled": False}
        try:
            return {"enabled": True, **mirror.status(self.cfg)}
        except mirror.MirrorError as exc:
            return {"enabled": True, "error": tr(str(exc))}

    def _mirror_params(self, body: dict) -> dict:
        from ...storage import mirror

        try:
            saved = mirror.connection_params(self.cfg)
        except (mirror.MirrorError, OSError):
            saved = None
        return mirror.check_settings(body, saved)

    def _mirror_schema(self, body: dict) -> str:
        from ...storage import mirror

        schema = str(body.get("schema") or self.cfg.mirror_schema).strip()
        if not (schema.isidentifier() and schema.isascii()):
            raise mirror.MirrorError("the schema name can have only letters, digits and _, and can't start with a digit")
        return schema

    def _mirror_check(self, params: dict, schema: str) -> dict:
        """probe(), plus whether this computer may write there: a schema no other computer's mirror is in, and a user
        that can create its tables."""
        from ...core.machine import local_machine
        from ...storage import mirror

        found = mirror.probe(params, schema)
        owner = found["owner"]
        found["mine"] = bool(owner and owner["id"] == local_machine(self.cfg)["id"])
        if owner and not found["mine"]:
            raise mirror.MirrorError(f"schema {schema} holds the mirror of another computer ({owner['name'] or owner['id']}); "
                                     "pick a schema of this computer's own")
        if not owner and not found["can_create"]:
            raise mirror.MirrorError(f"user {params['user']} can't create the mirror's tables in {found['where']}")
        return found

    def action_mirror_test(self, body: dict) -> dict:
        from ...storage import mirror

        try:
            return {"ok": True, **self._mirror_check(self._mirror_params(body), self._mirror_schema(body))}
        except mirror.MirrorError as exc:
            return {"ok": False, "error": tr(str(exc))}

    def action_mirror_save(self, body: dict) -> dict:
        """Turn the mirror on with these settings (tested first: nothing is saved if they don't work) and write it
        now, or off. Off keeps the settings, so turning it on again needs no password, and leaves the copy as it is."""
        from ...core.config import MIRROR_INCLUDES, load_config, set_config_value
        from ...storage import mirror

        if not body.get("enabled"):
            set_config_value(self.cfg, "mirror", "to", '""')
            self.cfg = load_config(self.cfg.home)
            self._cfg_sig = self._config_sig()
            return {"ok": True, "mirror": self.mirror_info()}
        include = str(body.get("include") or self.cfg.mirror_include)
        if include not in MIRROR_INCLUDES:
            return {"error": tr("what the mirror holds must be \"knowledge\" or \"everything\"")}
        try:
            params, schema = self._mirror_params(body), self._mirror_schema(body)
            self._mirror_check(params, schema)
        except mirror.MirrorError as exc:
            return {"error": tr(str(exc))}
        mirror.write_settings(self.cfg, params)
        for key, value in (("include", include), ("schema", schema), ("to", "postgres")):
            set_config_value(self.cfg, "mirror", key, json.dumps(value))
        self.cfg = load_config(self.cfg.home)
        self._cfg_sig = self._config_sig()
        return {"ok": True, "mirror": self.mirror_info(), "started": self.action_mirror_sync()}

    def action_mirror_sync(self) -> bool:
        from ...storage import mirror

        cfg = self.cfg

        def job(progress):
            res = mirror.sync(cfg, progress=progress)
            if res.get("error"):
                raise RuntimeError(res["error"])
            return f"mirror: {res.summary()}"

        return mirror.enabled(cfg) and self.jobs.start("mirror", job)

    # ---- Devices › People (people.py): who may send to this hub and open its dashboard; admins only
    def people_info(self) -> dict:
        from ...sharing import people
        from ...sharing.projects import hub_projects

        return {"people": people.listing(self.conn), "shared_token": self.cfg.hub_shared_token,
                "address": self.cfg.hub_address or None, "audit": people.audit_log(self.conn, 50),
                "roles": list(people.ROLES), "projects": hub_projects(self.conn)}

    def _invite(self, person: dict, by: dict | None, address: str) -> dict:
        """A new invite for `person`, with the command and the link to pass on. The code is shown this once."""
        from ...sharing import people
        from ...sharing.protocol import invite_command, invite_link

        code = people.invite(self.conn, person["id"], by=by)
        expires = self.conn.execute("SELECT expires_at FROM people_codes WHERE person_id = ? AND kind = 'invite' "
                                    "ORDER BY rowid DESC LIMIT 1", (person["id"],)).fetchone()[0]
        out = {"person": person, "code": code, "expires_at": expires,
               "join": invite_command(address, code), "link": invite_link(address, code)}
        if not self.cfg.hub_address:  # guessed from this page's address, which others may not reach
            out["note"] = tr("this hub has no address set ([hub] address), so these use the address this page was "
                             "opened at; set it with `interlatch hub enable --url`")
        return out

    def _projects_body(self, body: dict) -> list[str] | None:
        """`projects` from the dashboard: "all" for every project, else a list of this hub's project paths."""
        from ...sharing import people
        from ...sharing.projects import hub_projects

        raw = body.get("projects")
        if raw == "all":
            return None
        if not isinstance(raw, list):
            raise people.PeopleError("say which projects they see, or every project")
        known = {p["path"] for p in hub_projects(self.conn)}
        unknown = [x for x in raw if x not in known]
        if unknown:
            raise people.PeopleError("{project} is not a project on this hub", project=str(unknown[0]))
        return people.clean_projects(raw)

    def action_people(self, verb: str, body: dict, by: dict | None, address: str) -> tuple[dict, int]:
        """add, invite, role, access, remove, revoke or shared-token, done by `by` (None: at the hub itself) for the
        audit."""
        from ...core.config import load_config, set_config_value
        from ...sharing import people

        try:
            if verb == "add":
                role = str(body.get("role") or "member")
                projects = [] if role == "admin" else self._projects_body(body)  # an admin sees all; if made a member, nothing yet
                person = people.add(self.conn, str(body.get("name") or ""), str(body.get("email") or ""), role, by=by,
                                    projects=projects)
                return self._invite(person, by, address), 200
            if verb == "shared-token":
                on = bool(body.get("on"))
                set_config_value(self.cfg, "hub", "shared_token", "true" if on else "false")
                self.cfg = load_config(self.cfg.home)
                self._cfg_sig = self._config_sig()
                people.audit(self.conn, people.actor_of(by), "shared-token", None, on=on)
                self.conn.commit()
                return {"ok": True, **self.people_info()}, 200
            try:
                pid = int(body.get("id"))
            except (TypeError, ValueError):
                return {"error": tr("no such person")}, 400
            if verb == "invite":
                person = people.get(self.conn, pid)
                if not person or person["removed_at"]:
                    return {"error": tr("no such person")}, 400
                return self._invite(person, by, address), 200
            if verb == "role":
                people.set_role(self.conn, pid, str(body.get("role") or ""), by=by)
            elif verb == "access":
                people.set_projects(self.conn, pid, self._projects_body(body), by=by)
            elif verb == "remove":
                people.remove(self.conn, pid, by=by)
            elif verb == "revoke":
                if not people.revoke(self.conn, pid, str(body.get("token") or ""), by=by):
                    return {"error": tr("no such computer or browser session")}, 400
            else:
                return {"error": tr("not found")}, 404
        except people.PeopleError as exc:
            return {"error": exc.shown()}, 400
        return {"ok": True, **self.people_info()}, 200

    def shared_projects(self) -> dict:
        """Settings › Devices › Shared projects: the projects set up on this hub (hub.add_project), and the ones that
        could be (this hub's projects that aren't, busiest first)."""
        from ...projects.routing import declared_projects, under
        from ...sharing.identity import read_token
        from ...sharing.projects import hub_projects, shared_projects

        shared = shared_projects(self.conn)
        set_up = declared_projects(self.conn)
        candidates = [p for p in hub_projects(self.conn, limit=300)
                      if not any(under(p["path"], d) or under(d, p["path"]) for d in set_up)][:200]
        return {"shared": shared, "candidates": candidates, "hub": bool(read_token(self.cfg)) and not self.cfg.is_spoke,
                "store": self.cfg.hub_store == "postgres", "dedicated": self.cfg.hub_dedicated}

    def action_shared_project(self, verb: str, body: dict, *, by: dict | None = None, here: bool = True) -> tuple[dict, int]:
        """Set up a project on this hub (its sessions and lessons then go to the team store and to the people given
        it), or stop: hub.add_project / remove_project, recorded in the people audit log. At the hub computer itself
        (`here`), any folder of it. Elsewhere, only on a dedicated hub and only for an admin (`by`; the handler checks):
        a new project by its name, never a folder (hub.named_project_folder), or removing one that is set up."""
        from ...sharing import people
        from ...sharing.projects import add_project, named_project_folder, remove_project
        from ...sharing.protocol import PROJECT_NAME_RULE, HubError

        try:
            if verb == "add" and (not here or (self.cfg.hub_dedicated and "name" in body)):
                folder = named_project_folder(self.cfg, str(body.get("name") or ""))
            else:
                folder = str(body.get("folder") or body.get("path") or "").strip()
                if not folder.startswith(("/", "~")):
                    return {"error": tr("pick a project or type its folder")}, 400
            got = (add_project(self.cfg, self.conn, folder, by=people.actor_of(by)) if verb == "add"
                   else remove_project(self.cfg, self.conn, folder))
        except HubError as exc:
            return {"error": tr(str(exc)) if str(exc) == PROJECT_NAME_RULE else str(exc)}, 400
        people.audit(self.conn, people.actor_of(by), "project-add" if verb == "add" else "project-remove", None,
                     path=got["path"])
        self.conn.commit()
        return {"ok": True, "result": got, **self.shared_projects()}, 200

    def action_share_mode(self, share: str) -> dict:
        """[hub] share on a computer that sends to a hub: its transcripts, or only what it learned."""
        from ...core.config import SHARE_MODES, load_config, set_config_value

        if not self.cfg.is_spoke:
            return {"error": tr("this computer has not joined a hub")}
        if share not in SHARE_MODES:
            return {"error": tr("unknown share mode {share!r}", share=share)}
        set_config_value(self.cfg, "hub", "share", json.dumps(share))
        self.cfg = load_config(self.cfg.home)
        self._cfg_sig = self._config_sig()
        return {"ok": True, "share": self.cfg.hub_share}

    def action_share_folders(self, all_folders) -> dict:
        """[hub] all_folders on a computer that shares knowledge: every folder's sessions, or only the hub's projects'."""
        from ...core.config import load_config, set_config_value

        if not self.cfg.is_spoke:
            return {"error": tr("this computer has not joined a hub")}
        if not isinstance(all_folders, bool):
            return {"error": tr("all_folders must be true or false")}
        set_config_value(self.cfg, "hub", "all_folders", "true" if all_folders else "false")
        self.cfg = load_config(self.cfg.home)
        self._cfg_sig = self._config_sig()
        return {"ok": True, "all_folders": self.cfg.hub_all_folders}

    def action_accept(self, accept: str, by: dict | None) -> tuple[dict, int]:
        """[hub] accept on the hub: transcripts too, or knowledge only from every computer. `by` (None: at the hub
        itself) goes in the audit."""
        from ...core.config import SHARE_MODES, load_config, set_config_value
        from ...sharing import people
        from ...sharing.identity import read_token

        if self.cfg.is_spoke or not read_token(self.cfg):
            return {"error": tr("this computer is not a hub")}, 400
        if accept not in SHARE_MODES:
            return {"error": tr("unknown share mode {share!r}", share=accept)}, 400
        set_config_value(self.cfg, "hub", "accept", json.dumps(accept))
        self.cfg = load_config(self.cfg.home)
        self._cfg_sig = self._config_sig()
        people.audit(self.conn, people.actor_of(by), "accept", None, accept=accept)
        self.conn.commit()
        return {"ok": True, "accept": self.cfg.hub_accept}, 200

    def action_push(self) -> bool:
        """Send to the hub now; a computer that shares knowledge also gets its teammates' lessons back."""
        from ...sharing.push import push

        return self.jobs.start("push", lambda progress: push(self.cfg, progress=progress).summary())

    def update_info(self, remote: bool) -> dict:
        from ...runtime.update import check, recall, remember

        recall(self.conn)
        info = check(remote, detail=True)
        if remote:
            remember(self.conn)
        return {**info, "check_daily": self.cfg.update_check_daily, "notify": self.cfg.update_notify}

    def _maybe_check_daily(self) -> None:
        """With [updates] check_daily on, ask PyPI once a day, in the background, while a dashboard polls."""
        from ...runtime.update import check_due, fetch_latest, remember

        if not self.cfg.update_check_daily or not check_due() or not self._update_check.acquire(blocking=False):
            return

        def run():
            conn = None
            try:
                fetch_latest()
                conn = connect(self.cfg.db_path)
                remember(conn)
            except Exception:
                log.exception("daily update check failed")
            finally:
                if conn is not None:
                    conn.close()
                self._update_check.release()

        threading.Thread(target=run, name="update-check", daemon=True).start()

    def action_update_setting(self, key: str, on: bool) -> dict:
        """Status › Updates switches: check_daily (while the dashboard is open) and notify (from the background)."""
        from ...core.config import load_config, set_config_value

        set_config_value(self.cfg, "updates", key, "true" if on else "false")
        self.cfg = load_config(self.cfg.home)
        self._cfg_sig = self._config_sig()
        self._maybe_check_daily()
        return self.update_info(remote=False)

    def _runner_for(self, backend: str):
        return make_runner(replace(self.cfg, analysis=replace(self.cfg.analysis, backend=backend)))

    def analysis_backends(self) -> dict:
        """What analyzes sessions, and every choice: the coding agents (installed or not) and the model providers
        (set up or not). Never an API key, only where one comes from."""
        return {"backend": make_runner(self.cfg).name,
                "choices": [self._runner_for(name).describe() for name in BACKENDS],
                "language": self.cfg.analysis.language,  # what Interlatch writes knowledge in, not the dashboard's language
                "languages": [{"code": code, "label": label} for code, label in LANGUAGES.items()]}

    def action_backend(self, backend: str) -> dict:
        from ...core.config import load_config, set_config_value

        if backend not in BACKENDS:
            return {"error": tr("unknown analysis backend {backend!r}", backend=backend)}
        set_config_value(self.cfg, "analysis", "backend", json.dumps(backend))
        self.cfg = load_config(self.cfg.home)
        self._cfg_sig = self._config_sig()
        return self.analysis_backends()

    def action_agent(self, body: dict) -> dict:
        """Change a coding agent's model and effort (AGENT_SETTINGS). An empty Codex model means Codex's default."""
        from ...core.config import load_config, set_config_value

        name = str(body.get("agent") or "")
        allowed = AGENT_SETTINGS.get(name)
        if not allowed:
            return {"error": tr("unknown analysis backend {backend!r}", backend=name)}
        values = body.get("settings") if isinstance(body.get("settings"), dict) else {}
        refused = []
        for k, v in values.items():
            v = str(v if v is not None else "").strip()
            ok = k in allowed and len(v) <= 120 and not any(c.isspace() for c in v)
            if k == "effort":
                ok = ok and v in EFFORTS
            elif not (name == "codex" and k == "model"):
                ok = ok and bool(v)  # Claude needs a model for each job
            if not ok:
                refused.append(k)
                continue
            set_config_value(self.cfg, *allowed[k], json.dumps(v))
        self.cfg = load_config(self.cfg.home)
        self._cfg_sig = self._config_sig()
        out = self.analysis_backends()
        if refused:
            out["error"] = tr("not saved: {keys}", keys=", ".join(refused))
        return out

    def action_provider(self, body: dict) -> dict:
        """Save a model provider's settings ([providers.<name>]) and, when given, its API key ("" forgets it). For
        IBM Bob, only the key."""
        from ...analysis.runners.settings import KEY_ENVS, PROVIDERS, save_settings, set_key
        from ...core.config import load_config

        name = str(body.get("provider") or "")
        if name not in KEY_ENVS:
            return {"error": tr("unknown analysis backend {backend!r}", backend=name)}
        values = body.get("settings") if isinstance(body.get("settings"), dict) and name in PROVIDERS else {}
        refused = save_settings(self.cfg, name, values)
        if "key" in body and body["key"] is not None:
            set_key(self.cfg, name, str(body["key"]))
        self.cfg = load_config(self.cfg.home)
        self._cfg_sig = self._config_sig()
        out = self.analysis_backends()
        if refused:
            out["error"] = tr("not saved: {keys}", keys=", ".join(refused))
        return out

    def action_provider_test(self, name: str) -> dict:
        """One small call to a provider or agent, with its main model: do the endpoint, the key and the model work?"""
        from ...analysis.runners.base import LLMError

        if name not in BACKENDS:
            return {"error": tr("unknown analysis backend {backend!r}", backend=name)}
        runner = self._runner_for(name)
        if not runner.available():
            return {"error": runner.unavailable_reason()}
        try:
            res = runner.run('Reply with {"ok": true}.', {"type": "object", "properties": {"ok": {"type": "boolean"}},
                                                          "required": ["ok"]},
                             system="You check that a connection works.", effort="low", timeout=120)
        except LLMError as exc:
            return {"error": str(exc)[:600]}
        return {"ok": res.data.get("ok") is True, "model": res.model, "ms": res.duration_ms,
                "tokens": res.input_tokens + res.output_tokens}

    def provider_models(self, name: str) -> dict:
        from ...analysis.runners.settings import PROVIDERS

        if name not in PROVIDERS:
            return {"models": []}
        return {"models": self._runner_for(name).list_models()}

    def action_language(self, lang: str) -> dict:
        """[analysis] language: what summaries, knowledge and proposed lines are written in from now on."""
        from ...core.config import load_config, set_config_value

        if lang not in LANGUAGES:
            return {"error": tr("unknown language {lang!r}", lang=lang)}
        set_config_value(self.cfg, "analysis", "language", json.dumps(lang))
        self.cfg = load_config(self.cfg.home)
        self._cfg_sig = self._config_sig()
        return self.analysis_backends()

    def imports(self) -> dict:
        from ...ingestion.chat_import import import_status

        return import_status(self.conn)

    def action_import(self, upload: Path, name: str | None = None) -> bool:
        from ...ingestion.chat_import import import_export, summary

        def job(progress):
            conn = connect(self.cfg.db_path)
            try:
                return summary(import_export(self.cfg, conn, upload, progress=progress, name=name))
            finally:
                conn.close()
                upload.unlink(missing_ok=True)  # the zip also holds the account files (name, email): not kept

        return self.jobs.start("import", job)

    def action_screen(self, source: str | None, redo: bool = False) -> bool:
        """Screen imported chats in the background: which are worth analyzing (screen.py)."""
        from ...analysis.screen import screen_chats

        def job(progress):
            conn = connect(self.cfg.db_path)
            try:
                report = screen_chats(self.cfg, conn, source=source, redo=redo, progress=progress)
            finally:
                conn.close()
            if report.left and not report.screened:
                raise RuntimeError(report.error or "nothing could be screened")
            return report.summary()

        return self.jobs.start("screen", job)

    def action_screen_queue(self, source: str | None, maybe: bool = False) -> dict:
        from ...analysis.screen import queue

        return {"queued": queue(self.conn, source=source, maybe=maybe), "per_run": self.cfg.analysis.max_per_run}

    def action_menu_bar(self, on: bool) -> dict:
        """Status › Recording's switch: the macOS menu-bar icon on or off, adding the `app` extra when it is missing."""
        from ...core.config import load_config
        from ...runtime.platforms import menubar

        info = menubar.setting_info(self.cfg)
        if not info["supported"]:
            return {"started": False, "error": tr("The menu-bar icon is for a command-line install on macOS")}
        if on and not info["app_extra"] and not info["can_install"]:
            return {"started": False, "error": tr("The menu-bar icon needs the app extra: {command}", command=info["command"])}
        restarts = menubar.restarts(on, info)
        busy = [k for k, j in self.jobs.snapshot().items() if j["state"] == "running" and k != "menu-bar"]
        if restarts and busy:  # the restart would cut the running job off
            return {"started": False, "error": tr("Wait for {job} to finish first", job=busy[0])}

        def job(progress):
            out = menubar.apply_setting(self.cfg, on, progress)
            self.cfg = load_config(self.cfg.home)
            self._cfg_sig = self._config_sig()
            return out

        return {"started": self.jobs.start("menu-bar", job), "restarts": restarts}

    def action_update(self) -> dict:
        from ...runtime.update import run_update

        busy = [k for k, j in self.jobs.snapshot().items() if j["state"] == "running" and k != "update"]
        if busy:  # updating restarts the dashboard, which would cut the running job off
            return {"started": False, "error": tr("Wait for {job} to finish first", job=busy[0])}
        return {"started": self.jobs.start("update", run_update)}

    def status(self) -> dict:
        from ...runtime.install import UI_LABEL, hooks_installed, launchd_status, mcp_registered, statusline_installed
        from ...runtime.statusline import plan_usage

        c = self.conn
        st = self.status_small()
        st["counts"] = {r["analysis_status"]: r["n"] for r in c.execute(
            "SELECT analysis_status, COUNT(*) n FROM sessions GROUP BY analysis_status")}
        st["analysis_cost"] = c.execute("SELECT COALESCE(SUM(cost_usd),0) FROM analyses").fetchone()[0]
        st["hooks"] = hooks_installed(self.cfg)
        st["launchd"] = launchd_status()
        st["ui_agent"] = launchd_status(UI_LABEL)
        st["mcp"] = mcp_registered()
        st["statusline"] = {"installed": statusline_installed(self.cfg), "plan": plan_usage(c)}
        from ...runtime.platforms.menubar import setting_info

        st["menu_bar"] = setting_info(self.cfg)
        st["db_size"] = self.cfg.db_path.stat().st_size if self.cfg.db_path.exists() else 0
        st["archive_dir"] = str(self.cfg.archive_dir)
        st["notes_dir"] = str(self.cfg.notes_dir)
        st["config"] = {"model": make_runner(self.cfg).model_label(), "auto": self.cfg.analysis.auto,
                        "backfill": self.cfg.analysis.backfill, "max_per_run": self.cfg.analysis.max_per_run}
        st["analysis"].update(self.analysis_backends())
        st["errors"] = [{**dict(r), "analysis_reason": analysis_reason_text(r["analysis_reason"])} for r in c.execute(
            "SELECT id, title, analysis_reason FROM sessions WHERE analysis_status = 'error' ORDER BY ended_at DESC LIMIT 10")]
        return st

    # ------------------------------------------------------------------ actions
    def action_sync(self) -> bool:
        from ...application.workflows import run_worker
        from ...ingestion.service import sync
        from ..exports.vault import export_markdown

        if self.cfg.sends_files:
            from ...sharing.push import push

            return self.jobs.start("sync", lambda progress: push(self.cfg, progress=progress).summary())

        def job(progress):
            progress("syncing transcripts…")
            conn = connect(self.cfg.db_path)
            try:
                report = sync(self.cfg, conn)
            finally:
                conn.rollback()
                conn.close()
            progress("processing…")
            work = run_worker(self.cfg, export=export_markdown, progress=progress)
            conn = connect(self.cfg.db_path)
            try:
                from ...sharing.leaderboard import send_safely

                send_safely(self.cfg, conn)  # once signed in to the leaderboard
            finally:
                conn.close()
            return f"{report.summary()} | {work.summary()}"

        return self.jobs.start("sync", job)

    def action_analyze(self, sid: str) -> bool:
        from ...application.workflows import run_worker
        from ..exports.vault import export_markdown

        real = resolve_session_id(self.conn, sid)
        if not real:
            return False

        def job(progress):
            report = run_worker(self.cfg, session_ids=[real], synthesize=True, export=export_markdown,
                                progress=progress, wait=True)
            if report.failed:
                raise RuntimeError(report.failed[0][1])
            return report.summary()

        return self.jobs.start(f"analyze:{real}", job)

    def _export_ids(self, ids: str) -> list[str]:
        from ..exports.sessions import MAX_SESSIONS, ExportError

        real = [r for r in (resolve_session_id(self.conn, x.strip()) for x in ids.split(",")[:MAX_SESSIONS] if x.strip()) if r]
        if not real:
            raise ExportError(tr("no such sessions"))
        return list(dict.fromkeys(real))

    def export(self, ids: str, fmt: str) -> tuple[str, str, bytes]:
        from ..exports.sessions import export_sessions

        return export_sessions(self.conn, self._export_ids(ids), fmt)

    def diagram_file(self, path: str) -> tuple[str, str, bytes] | None:
        """A project's architecture sketch as an .excalidraw file, to open and edit in Excalidraw."""
        from ...core.diagrams import normalize_diagram
        from ..exports.diagrams import to_excalidraw

        kb = self.conn.execute("SELECT project_name, kb_json FROM project_kb WHERE project_path = ?", (path,)).fetchone()
        d = normalize_diagram((loads(kb["kb_json"], {}) or {}).get("diagram")) if kb and path != GLOBAL else None
        if not d:
            return None
        name = project_labels(self.conn).get(path) or kb["project_name"] or Path(path).name
        scene = to_excalidraw(d, f"{name}: architecture")
        slug = re.sub(r"[^\w.-]+", "-", name).strip("-") or "project"
        return f"{slug}-architecture.excalidraw", "application/vnd.excalidraw+json", json.dumps(scene, ensure_ascii=False, indent=1).encode()

    def export_check(self, ids: str, fmt: str) -> dict:
        from ..exports.sessions import FORMATS, ExportError, has_original

        if fmt not in FORMATS:
            raise ExportError(tr("unknown format {fmt!r}", fmt=fmt))
        real = self._export_ids(ids)
        without = 0 if fmt != "raw" else sum(not has_original(self.conn, sid) for sid in real)
        if without == len(real):
            raise ExportError(tr("No original transcript to export: claude.ai chats share one export file and "
                                 "prompt-history sessions have none. Export as Markdown or JSON instead."))
        return {"ok": True, "count": len(real), "without_original": without}

    def action_analyze_many(self, refs: list) -> dict:
        """Analyze the sessions picked in the list, in one background job (history-only ones have nothing to send)."""
        from ...application.workflows import run_worker
        from ..exports.vault import export_markdown

        ids = []
        for ref in refs[:MAX_SELECTION]:
            real = resolve_session_id(self.conn, str(ref))
            row = self.conn.execute("SELECT source FROM sessions WHERE id = ?", (real,)).fetchone() if real else None
            if row and row["source"] != "history" and real not in ids:
                ids.append(real)
        if not ids:
            return {"started": False, "error": tr("Nothing in the selection can be analyzed")}

        def job(progress):
            report = run_worker(self.cfg, session_ids=ids, synthesize=True, export=export_markdown, progress=progress,
                                wait=True)
            if report.paused_until and report.failed:
                left = len(ids) - len(report.analyzed) - len(report.skipped)
                return f"{report.summary()}; hit the Claude usage limit: {left} not analyzed, select them again later"
            return report.summary()

        started = self.jobs.start("analyze:selection", job)
        return {"started": started, "count": len(ids), "dropped": len(refs) - len(ids),
                **({} if started else {"error": tr("A batch analysis is already running")})}

    def action_analyze_queue(self, which: str) -> dict:
        """The Queue's Analyze now: the sessions ready now, or ("active") the ones that may still be going, analyzed
        at once instead of on the background run. It runs with automatic analysis off and through a usage-limit
        pause (the limit may be over by now); a run that gets through lifts the pause for the background run too."""
        from ...analysis.queue import PAUSE_KEY, queued_ids
        from ...application.workflows import run_worker
        from ..exports.vault import export_markdown

        if which not in ("ready", "active"):
            return {"started": False, "error": tr("unknown queue")}
        if self.cfg.sends_files:
            return {"started": False, "error": tr("analysis runs on the hub ({hub})", hub=self.cfg.hub_url)}
        ids = queued_ids(self.conn, self.cfg, which, MAX_SELECTION)
        if not ids:
            return {"started": False, "error": tr("Nothing in the queue to analyze")}

        def job(progress):
            report = run_worker(self.cfg, session_ids=ids, synthesize=True, export=export_markdown, progress=progress,
                                wait=True)
            if report.analyzed and not report.paused_until:
                conn = connect(self.cfg.db_path)
                try:
                    kv_set(conn, PAUSE_KEY, None)
                    conn.commit()
                finally:
                    conn.close()
            if report.paused_until and report.failed:
                left = len(ids) - len(report.analyzed) - len(report.skipped)
                return f"{report.summary()}; hit the usage limit again: {left} not analyzed, they stay queued"
            return report.summary()

        started = self.jobs.start("analyze:queue", job)
        return {"started": started, "count": len(ids),
                **({} if started else {"error": tr("The queue is already being analyzed")})}

    def action_synthesize(self, path: str) -> bool:
        from ...knowledge.synthesize import synthesize_project

        def job(progress):
            conn = connect(self.cfg.db_path)
            try:
                progress("synthesizing…")
                synthesize_project(conn, self.cfg, path)
            finally:
                conn.rollback()
                conn.close()
            return "done"

        return self.jobs.start(f"synthesize:{path}", job)

    def action_knowledge(self, kid: int, body: dict) -> bool:
        sets, params = [], []
        if body.get("status") in ("active", "dismissed", "superseded"):
            sets.append("status = ?")
            params.append(body["status"])
            if body["status"] == "active":  # restored by hand: it no longer points at a successor
                sets.append("superseded_by = NULL, superseded_reason = NULL, superseded_at = NULL")
        if "pinned" in body:
            sets.append("pinned = ?")
            params.append(1 if body["pinned"] else 0)
        if not sets:
            return False
        self.conn.execute(f"UPDATE knowledge SET {', '.join(sets)}, updated_at = ? WHERE id = ?", [*params, to_iso(utcnow()), kid])
        refresh_stage(self.conn, [kid])  # a pin makes an item canonical; unpinning returns it to what its evidence earns
        self.conn.commit()
        return True


    # ------------------------------------------------------------------ suggestions / what goes wrong
    def suggestions(self, q: dict) -> dict:
        from ...knowledge import suggest

        status = q.get("status") if q.get("status") in suggest.STATUSES else None
        rows = suggest.list_suggestions(self.conn, status=status, project=q.get("project") or None)
        limit = int(q["limit"]) if str(q.get("limit") or "").isdigit() else None  # the Home card needs only the top few
        return {"suggestions": [suggest.display(r) for r in (rows[:limit] if limit else rows)], "total": len(rows),
                "counts": suggest.counts(self.conn)}

    def suggestion_preview(self, sid: int, q: dict) -> tuple[dict, int]:
        from ...knowledge import suggest

        if suggest.get(self.conn, sid) is None:
            return {"ok": False, "error": tr("no such suggestion")}, 404
        return suggest.preview(self.conn, self.cfg, sid, (q.get("text") or "").strip() or None), 200

    def action_suggestion(self, sid: int, verb: str, body: dict) -> tuple[dict, int]:
        """apply / unapply / dismiss / done / edit / move. A refusal is a 400 carrying the reason; an unknown id a 404."""
        from ...knowledge import suggest

        if suggest.get(self.conn, sid) is None:
            return {"ok": False, "error": tr("no such suggestion")}, 404
        text = body.get("text")
        text = text if isinstance(text, str) and text.strip() else None
        if verb == "apply":
            got = suggest.apply(self.conn, self.cfg, sid, text)
        elif verb == "unapply":
            got = suggest.unapply(self.conn, self.cfg, sid)
        elif verb == "dismiss":
            got = suggest.dismiss(self.conn, sid, str(body.get("reason") or "") or None)
        elif verb == "done":
            got = suggest.mark_done(self.conn, sid)
        elif verb == "move":  # to the user-level file, or back to the project files
            got = suggest.move(self.conn, self.cfg, sid, str(body.get("to") or ""))
        else:  # edit: keep the text you changed, so a refresh does not replace it
            got = suggest.edit_text(self.conn, sid, text or "")
        return got, (200 if got.get("ok") else 400)

    def friction(self, q: dict) -> dict:
        from ...knowledge.friction import display, report

        days = q.get("days")
        days = int(days) if days and days.isdigit() and int(days) > 0 else None
        got = report(self.conn, days=days, project=q.get("project") or None, noise=q.get("noise") in ("1", "true"))
        return {**got, "causes": [display(c) for c in got["causes"]]}
