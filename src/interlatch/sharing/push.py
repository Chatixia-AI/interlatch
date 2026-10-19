"""Sending the hub what this computer shares: raw session files (only the ones it lacks), or sessions' details
and lessons, plus the analyses this computer already did when it first joins."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from ..core.config import Config
from ..core.machine import local_machine
from ..core.paths import normalize_project_path
from ..core.util import parse_ts, utcnow, utcnow_iso
from ..projects import routing  # git_info through routing: one patch there reaches every caller
from .client import PUSH_WAIT_S, HubClient, _Exclusions, root_files, say_hello, sharing, spoke_roots
from .identity import read_token
from .projects import GroupRoutes, destination, group_routes
from .protocol import (ANALYSIS_COLS, KNOWLEDGE_COLS, SHARE_BATCH, SHARED_AGENTS, SHARED_ANALYSIS_COLS, SHARED_COLS,
                       HubError, HubUnauthorized, HubUnreachable, shared_analysis, shared_lesson)
from .pull import pull_team_lessons


@dataclass
class PushReport:
    sent: int = 0
    bytes: int = 0
    unchanged: int = 0
    skipped: int = 0
    analyses: int = 0
    errors: list[str] = field(default_factory=list)
    hub: str = ""
    seconds: float = 0.0
    kind: str = "files"  # or "knowledge": sessions this computer analyzed itself
    team: int | None = None  # teammates' lessons held here after the pull (a hub with a team store sends them)

    def summary(self) -> str:
        what = "session" if self.kind == "knowledge" else "file"
        verb = "shared" if self.kind == "knowledge" else "sent"
        parts = [f"{self.sent} {what}{'s' * (self.sent != 1)} {verb} ({self.bytes / 1e6:.1f} MB)", f"{self.unchanged} unchanged"]
        if self.skipped:  # sharing knowledge: sessions outside the hub's projects, or in excluded ones, stay here
            parts.append(f"{self.skipped} kept here" if self.kind == "knowledge" else f"{self.skipped} excluded")
        if self.analyses:
            parts.append(f"{self.analyses} earlier analyses handed over")
        if self.team is not None:
            parts.append(f"{self.team} team lesson{'s' * (self.team != 1)} here")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return f"to {self.hub}: " + ", ".join(parts) + f" in {self.seconds:.1f}s"


def _gzip_to_temp(path: Path | bytes) -> tuple[tempfile.SpooledTemporaryFile, int, str]:
    """(gzip of the file in a spooled temp file, raw size, raw sha256). Reads the file once, as it is now; given
    bytes (a file filtered for exclusions), those."""
    out = tempfile.SpooledTemporaryFile(max_size=16 << 20)
    h, size = hashlib.sha256(), 0
    with (io.BytesIO(path) if isinstance(path, bytes) else open(path, "rb")) as fin, \
            gzip.GzipFile(fileobj=out, mode="wb", compresslevel=6, mtime=0) as gz:
        while chunk := fin.read(1 << 20):
            h.update(chunk)
            size += len(chunk)
            gz.write(chunk)
    return out, size, h.hexdigest()


def _same(inv: list | None, size: int, mtime: float) -> bool:
    return bool(inv) and inv[0] == size and abs(float(inv[1]) - mtime) < 1.0


def push(cfg: Config, *, progress=None, client: HubClient | None = None) -> PushReport:
    """Send the hub every session file it lacks (or has an older copy of); with [hub] share = "knowledge", what
    this computer learned instead (push_knowledge). By the config as it is now (sharing)."""
    with sharing(cfg, PUSH_WAIT_S) as cfg:
        with _noting_failure(cfg):
            if cfg.shares_knowledge:
                return _push_knowledge(cfg, progress=progress, client=client)
            if cfg.profiles is not None:  # transcripts would carry every profile's sessions: never with profiles
                raise HubError("named profiles share knowledge only, and this computer is set to send transcripts: run "
                               "`interlatch config set hub.share knowledge`")
            return _push_files(cfg, progress=progress, client=client)


def push_knowledge(cfg: Config, *, progress=None, client: HubClient | None = None) -> PushReport:
    """[hub] share = "knowledge": send the hub each analyzed session's details, analysis and project lessons that it
    lacks or has an older analysis of. Transcripts never leave this computer."""
    with sharing(cfg, PUSH_WAIT_S) as cfg:
        if not cfg.shares_knowledge:  # changed since the caller read it
            raise HubError("this computer no longer shares knowledge only with the hub")
        with _noting_failure(cfg):
            return _push_knowledge(cfg, progress=progress, client=client)


def _push_files(cfg: Config, *, progress=None, client: HubClient | None = None) -> PushReport:
    token = read_token(cfg)
    if not cfg.hub_url or not token:
        raise HubError("this computer has not joined a hub (`interlatch hub join`)")
    t0 = time.monotonic()
    report = PushReport(hub=cfg.hub_url)
    roots = spoke_roots(cfg)
    client = client or HubClient(cfg.hub_url, token)
    me = local_machine(cfg)
    try:
        hello, _ = say_hello(cfg, client, roots, group_routes(cfg))
        inventory = hello.get("inventory") or {}
        if hello.get("wants_analyses"):
            blob, n = export_analyses(cfg)
            client.request("POST", "/api/hub/analyses", params={"machine": me["id"]}, body=blob,
                           headers={"Content-Type": "application/gzip"})
            report.analyses = n
        exclusions = _Exclusions(cfg)
        todo = []
        for root in roots:
            have = inventory.get(root.name) or {}
            for rel, path in root_files(root):
                try:
                    st = path.stat()
                except OSError:
                    continue
                # a file that mixes projects goes without an excluded one's lines: the hub holds that copy, so its
                # size is what tells whether the hub has it already
                data = exclusions.filtered(root, rel, path)
                if _same(have.get(rel), st.st_size if data is None else len(data), st.st_mtime):
                    report.unchanged += 1
                elif exclusions.excluded(root, rel, path) or (data == b"" and rel not in have):
                    report.skipped += 1
                else:
                    todo.append((root, rel, path, st, data))
        for i, (root, rel, path, st, data) in enumerate(todo, 1):
            if progress:
                progress(f"sending {i} of {len(todo)}: {rel}")
            try:
                body, size, sha = _gzip_to_temp(path if data is None else data)
            except OSError as exc:
                report.errors.append(f"{path}: {exc}")
                continue
            try:
                body.seek(0, os.SEEK_END)
                length = body.tell()
                client.request("POST", "/api/hub/file", body=body, length=length, headers={"Content-Type": "application/gzip"},
                               params={"machine": me["id"], "root": root.name, "path": rel, "size": size,
                                       "mtime": repr(st.st_mtime), "sha256": sha})
                report.sent += 1
                report.bytes += size
            except HubUnreachable:
                raise
            except HubError as exc:
                report.errors.append(f"{rel}: {exc}")
            finally:
                body.close()
        if report.sent or report.analyses:
            client.request("POST", "/api/hub/done", body={"machine": me["id"]})
    finally:
        client.close()
    report.seconds = time.monotonic() - t0
    _record_push(cfg, report)
    return report


def _shared_records(cfg: Config, have: dict, *, scope: list[str] | None = None, remotes: dict | None = None,
                    projects_only: bool = False, conn=None, where: str = "", params: tuple = (),
                    routes: GroupRoutes | None = None) -> tuple[list[dict], int, int]:
    """(records the hub lacks or has an older analysis of, unchanged, excluded) for push_knowledge.

    `scope`: the hub projects this computer's person is limited to (hello says so); then only sessions the hub files
    under them are sent: in a folder added to one of them, or in a repository whose remote the hub files there.
    `projects_only` ([hub] all_folders = false): the same for any of the hub's projects, so a folder nobody added and
    a repository the hub doesn't know stay here.
    Sessions the hub files under a project this computer left ([hub] left) always stay here.
    `routes`: the groups shared on the hub (GroupRoutes): a project in one goes to its hub project, whatever
    `projects_only` says; one in a shared group's folder but not in that group, or that left it, stays here.
    `conn`/`where`: the hub's own sessions in projects set up there (share_own)."""
    from ..storage.sqlite import connect

    out: list[dict] = []
    unchanged = excluded = 0
    own = conn is None
    if own and not cfg.db_path.exists():
        return out, 0, 0
    conn = conn or connect(cfg.db_path, readonly=True)
    found: dict[str, str | None] = {}
    left = set(cfg.hub_left)
    mine = owned_ids(cfg, conn)  # named profiles: only this profile's own sessions go to its hub
    routes = routes if routes is not None else GroupRoutes()
    try:
        # an allowlist: never a prompt-made title, the raw analysis, the person's mood or friction notes
        cols = ", ".join(("id", "project_path", "machine_path") + SHARED_COLS + SHARED_ANALYSIS_COLS)
        marks = ", ".join("?" for _ in SHARED_AGENTS)
        rows = conn.execute(f"SELECT {cols} FROM sessions WHERE analysis_status = 'done' "
                            f"AND source NOT IN ('history', 'remote') AND agent IN ({marks}){where}",
                            (*SHARED_AGENTS, *params)).fetchall()
        for r in rows:
            rec = dict(r)
            ran = rec.pop("machine_path") or rec["project_path"]  # where it ran: a set-up project may hold it
            if cfg.is_excluded(rec["project_path"]) or (mine is not None and (rec["id"] not in mine or cfg.off_hub(ran))):
                excluded += 1  # an excluded project's, or a named profile's (or waiting) once there are profiles
                continue
            limited = scope is not None or projects_only
            routed = bool(routes) and routes.decide(rec["project_path"], cfg.hub_folders) is not None
            if not limited and not left and not routed and have.get(rec["id"]) == rec["analyzed_at"]:
                unchanged += 1  # asked git nothing: it runs once per folder, only for what goes
                continue
            if ran and ran not in found:
                info = routing.git_info(ran)
                found[ran] = info[1] if info else None
            rec["remote"] = found.get(ran) if ran else None
            # a repository whose remote the hub files somewhere goes there, as destination() says; else a shared group
            via = None if not routed or (rec["remote"] and (remotes or {}).get(rec["remote"])) \
                else routes.decide(rec["project_path"], cfg.hub_folders)
            if via is not None:
                goes, ok = via
                goes = normalize_project_path(goes)  # [hub] left and a person's projects are spelled so
                if not ok or goes in left or (scope is not None and goes not in scope):
                    excluded += 1  # kept out of the group, out of it now, left, or a project this person doesn't share
                    continue
            elif limited or left:
                goes = normalize_project_path(destination(cfg, rec["project_path"], rec["remote"], remotes or {}))
                if goes in left or (limited and (goes is None or (scope is not None and goes not in scope))):
                    excluded += 1  # not one of the hub's projects, one this person doesn't share, or one it left
                    continue
            if have.get(rec["id"]) == rec["analyzed_at"]:
                unchanged += 1
                continue
            rec.update(shared_analysis(rec))  # its text redacted again, whatever wrote it
            rec["language"] = cfg.analysis.language  # what its summary and lessons are written in
            # lessons about the project only: ones about the person (global, preferences) stay on this computer
            rec["knowledge"] = [shared_lesson(dict(k)) for k in conn.execute(
                f"SELECT {', '.join(KNOWLEDGE_COLS)} FROM knowledge WHERE session_id = ? AND source = 'analysis' "
                "AND scope = 'project' AND kind != 'preference' AND status != 'dismissed'", (rec["id"],)).fetchall()]
            out.append(rec)
    finally:
        if own:
            conn.close()
    return out, unchanged, excluded


def owned_ids(cfg: Config, conn) -> set[str] | None:
    """The sessions this profile owns (cfg.owner: Personal, or a named profile sharing from Personal's archive), when
    named profiles exist (core.profiles); None when there are none. A session with no owner yet isn't in it: it waits
    for the next sync to be given one."""
    if cfg.profiles is None:
        return None if cfg.owner == "personal" else set()
    from ..projects.owners import has_table

    if not has_table(conn):
        return set()
    return {r[0] for r in conn.execute("SELECT session_id FROM session_owner WHERE owner = ?", (cfg.owner,))}


def _push_knowledge(cfg: Config, *, progress=None, client: HubClient | None = None) -> PushReport:
    token = read_token(cfg)
    if not cfg.hub_url or not token:
        raise HubError("this computer has not joined a hub (`interlatch hub join`)")
    t0 = time.monotonic()
    report = PushReport(hub=cfg.hub_url, kind="knowledge")
    client = client or HubClient(cfg.hub_url, token)
    me = local_machine(cfg)
    try:
        routes = group_routes(cfg)
        hello, info = say_hello(cfg, client, spoke_roots(cfg), routes)
        have = hello.get("knowledge")
        if not isinstance(have, dict):
            raise HubError(f"the hub runs version {hello.get('version')}, which can't take knowledge only; update it")
        scope = hello.get("scope") if isinstance(hello.get("scope"), list) else None  # the hub limits this person
        records, report.unchanged, report.skipped = _shared_records(cfg, have, scope=scope,
                                                                    remotes=hello.get("remotes") or {},
                                                                    projects_only=not cfg.hub_all_folders, routes=routes)
        for i in range(0, len(records), SHARE_BATCH):
            batch = records[i:i + SHARE_BATCH]
            if progress:
                progress(f"sharing {i + len(batch)} of {len(records)} sessions")
            body = gzip.compress(json.dumps({"version": 1, "sessions": batch}).encode(), mtime=0)
            try:
                client.request("POST", "/api/hub/sessions", params={"machine": me["id"]}, body=body,
                               headers={"Content-Type": "application/gzip"})
            except HubUnreachable:
                raise
            except HubError as exc:
                report.errors.append(str(exc))
                continue
            report.sent += len(batch)
            report.bytes += len(body)
        if report.sent:
            client.request("POST", "/api/hub/done", body={"machine": me["id"]})
        if hello.get("team"):  # the hub keeps a team store: bring back what teammates learned in these projects
            try:
                report.team = pull_team_lessons(cfg, client, info["repos"])
            except HubUnreachable:
                raise
            except HubError as exc:
                report.errors.append(f"team lessons: {exc}")
    finally:
        client.close()
    report.seconds = time.monotonic() - t0
    _record_push(cfg, report)
    return report


def _record_push(cfg: Config, report: PushReport) -> None:
    """The spoke's own note of its last push, for `interlatch hub status` and its dashboard. A push that got through
    clears the failure an earlier one noted."""
    _write_last(cfg, {
        "at": utcnow_iso(), "summary": report.summary(), "errors": report.errors[:5], "kind": report.kind,
        "sent": report.sent, "unchanged": report.unchanged, "kept": report.skipped, "team": report.team})


def _write_last(cfg: Config, data: dict) -> None:
    path = cfg.home / LAST_PUSH
    tmp = path.with_name(f".{LAST_PUSH}.{os.getpid()}")
    try:  # whole or not at all: the dashboard and the menu bar read it while a push writes it
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def last_push(cfg: Config) -> dict | None:
    try:
        return json.loads((cfg.home / LAST_PUSH).read_text())
    except (OSError, ValueError):
        return None


# A push that never got through (the hub turned this computer's token away, or could not be reached) used to leave
# no trace but the log: last-push.json kept the last good push, so every place that shows it said all was well.
# It is noted beside that push now, as `failure`, until a push gets through again.
LAST_PUSH = "last-push.json"
UNREACHABLE_GRACE = timedelta(hours=2)  # a laptop offline for an hour or a hub restarting is no news


def failure_reason(exc: HubError) -> str:
    """'token' (the hub turned this computer's token away: only joining again helps), 'unreachable' or 'error'."""
    return "token" if isinstance(exc, HubUnauthorized) else "unreachable" if isinstance(exc, HubUnreachable) else "error"


@contextmanager
def _noting_failure(cfg: Config):
    from ..core.profiles import RegistryError

    try:
        try:
            yield
        except RegistryError as exc:  # profiles.toml unreadable: nothing is sent, and it is a problem to tell
            raise HubError(f"nothing sent: {exc}") from None
    except HubError as exc:
        if cfg.hub_url and read_token(cfg):  # not joined at all is no failure to warn about
            note_failure(cfg, exc)
        raise


def note_failure(cfg: Config, exc: HubError) -> dict:
    """Note a push that didn't get through in last-push.json, and once it is a problem worth telling (send_problem),
    tell it in a desktop notification, once per run of failures. Returns the failure noted."""
    last = last_push(cfg) or {}
    before = last.get("failure") or {}
    reason, now = failure_reason(exc), utcnow_iso()
    failure = {"at": now, "since": before.get("since") or now, "reason": reason, "error": str(exc),
               "attempts": int(before.get("attempts") or 0) + 1, "hub": cfg.hub_url,
               "notified": bool(before.get("notified"))}
    last["failure"] = failure
    if not failure["notified"] and send_problem(last) and cfg.update_notify:
        failure["notified"] = _notify(cfg, failure)
    _write_last(cfg, last)
    return failure


def send_problem(last: dict | None, now=None) -> dict | None:
    """What to warn this computer's user about its pushes, or None: a token the hub turned away at once, any other
    failure once it has gone on for UNREACHABLE_GRACE. `last`: last_push(). The dict: reason, error, since (the
    first failed push), at (the latest), attempts, last_ok (the last push that got through, if any)."""
    failure = (last or {}).get("failure")
    if not failure:
        return None
    since = parse_ts(failure.get("since"))
    if failure.get("reason") != "token" and (since is None or (now or utcnow()) - since < UNREACHABLE_GRACE):
        return None
    return {k: failure.get(k) for k in ("reason", "error", "since", "at", "attempts", "hub")} | {"last_ok": last.get("at")}


def problem_text(problem: dict) -> tuple[str, str]:
    """(headline, what to do) for the CLI, the menu bar and the notification; the dashboard words its own."""
    if problem["reason"] == "token":
        return ("The hub no longer accepts this computer",
                "Ask the hub's admin for a new invite for this computer (Team › People › New invite), then join again: "
                f"interlatch hub join {problem.get('hub') or '<hub address>'} --code <code>")
    if problem["reason"] == "unreachable":
        return "Can't reach the hub", "Check the network and that the hub is running; sending resumes by itself."
    return "The hub refuses this computer's sends", problem.get("error") or ""


def _notify(cfg: Config, failure: dict) -> bool:
    from ..core.util import local_str
    from ..runtime.notify import post

    headline, fix = problem_text(failure)
    lead = ("Nothing reaches the hub until this computer joins again." if failure["reason"] == "token"
            else f"Nothing has reached the hub since {local_str(failure['since'])}.")
    return post(f"Interlatch: {headline}", f"{lead} {fix}")


def export_analyses(cfg: Config) -> tuple[bytes, int]:
    """gzip JSON of this computer's finished analyses and the knowledge they produced (not memory notes), but none
    of an excluded project's."""
    from ..storage.sqlite import connect

    sessions: list[dict] = []
    if cfg.db_path.exists():
        conn = connect(cfg.db_path, readonly=True)
        try:
            mine = owned_ids(cfg, conn)
            cols = ", ".join(("id", "project_path AS _project", "machine_path AS _ran") + ANALYSIS_COLS)
            for r in conn.execute(f"SELECT {cols} FROM sessions WHERE analysis_status = 'done'").fetchall():
                rec = dict(r)
                ran = (rec.pop("_project"), rec.pop("_ran"))
                if any(cfg.off_hub(p) for p in ran) or (mine is not None and rec["id"] not in mine):
                    continue  # an excluded project's analysis stays here, as its transcript does; a profile's too
                rec["knowledge"] = [dict(k) for k in conn.execute(
                    f"SELECT {', '.join(KNOWLEDGE_COLS)} FROM knowledge WHERE session_id = ? AND source = 'analysis'",
                    (rec["id"],)).fetchall()]
                sessions.append(rec)
        finally:
            conn.close()
    blob = gzip.compress(json.dumps({"version": 1, "sessions": sessions}).encode(), mtime=0)
    return blob, len(sessions)
