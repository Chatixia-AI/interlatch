"""The hub's side of sharing: the hello a computer opens with, the files and sessions it sends (checked against what
that person may share), analyses and team lessons, invites, purging and withdrawing what was shared, and the trigger
that ingests what arrived."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import zlib
from pathlib import Path, PurePosixPath

from .. import __version__
from ..core.config import Config
from ..core.machine import MACHINE_RE, local_machine, platform_label
from ..core.paths import is_abs_path, normalize_project_path, path_variants
from ..core.util import utcnow_iso
from ..ingestion.archive import ROOT_RE
from ..projects.routing import (FOLDERS_KV, MAX_FOLDERS, ProjectResolver, clean_folders, declared_projects, folders_of,
                                normalize_remote, under)
from . import news
from .client import _walk
from .identity import read_token
from .projects import LEFT_KV, folders_report, hub_projects, left_of, refile
from .protocol import (ANALYSES_FILE, KNOWLEDGE_COLS, MAX_ANALYSES_JSON, MAX_FILE_BYTES, MAX_SESSIONS_JSON,
                       PERSONAL_ANALYSIS_COLS, PROTOCOL, SESSION_ID_RE, SHARE_BATCH, SHARE_KV, SHARED_AGENTS,
                       SHARED_ANALYSIS_COLS, SHARED_COLS, HubError, UnknownInvite, clean_left, safe_rel, shared_analysis,
                       shared_lesson)
from .push import _shared_records

log = logging.getLogger("interlatch.hub")


# ------------------------------------------------------------------ a computer's hello, joining and signing in
def machine_dir(cfg: Config, machine_id: str) -> Path:
    return cfg.machines_dir / machine_id


def check_machine(cfg: Config, machine_id: str) -> str:
    if not MACHINE_RE.match(machine_id or ""):
        raise HubError("bad machine id")
    if machine_id == local_machine(cfg)["id"]:
        raise HubError("that is this hub's own id: the spoke's Interlatch folder was copied from the hub; delete "
                       "machine.json on the spoke and join again")
    return machine_id


def inventory(cfg: Config, machine_id: str) -> dict[str, dict[str, list]]:
    base = machine_dir(cfg, machine_id)
    out: dict[str, dict[str, list]] = {}
    if not base.is_dir():
        return out
    for root in base.iterdir():
        if not root.is_dir() or not ROOT_RE.match(root.name):
            continue
        files = out.setdefault(root.name, {})
        for rel, path in _walk(root, PurePosixPath(".")):
            if ".tmp-" in path.name:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            files[rel] = [st.st_size, st.st_mtime]
    return out


def _store(cfg: Config):
    """The hub's team store (storage/team.py) when `[hub] store` names one, else None; its errors as HubError."""
    from ..storage.team import TeamStoreError, get

    try:
        return get(cfg)
    except TeamStoreError as exc:
        raise HubError(str(exc)) from None


def _in_store(call, *args):
    from ..storage.team import TeamStoreError

    try:
        return call(*args)
    except TeamStoreError as exc:
        raise HubError(str(exc)) from None


def limited_error(person: dict) -> HubError:
    return HubError(f"{person['name']} sees only some projects on this hub, so this computer may share knowledge only "
                    "(summaries and project lessons, not transcripts): run `interlatch config set hub.share knowledge`, "
                    "then `interlatch push`")


def transcripts_refused(cfg: Config, person: dict | None) -> HubError | None:
    """Why this hub won't take a computer's transcripts, or None if it will: the hub takes knowledge only
    (`[hub] accept`), or the computer's person sees only some projects (people.py)."""
    from .people import projects_of

    if cfg.hub_accept == "knowledge":
        return HubError("this hub takes knowledge only (summaries and project lessons, not transcripts), so this "
                        "computer may share knowledge only: run `interlatch config set hub.share knowledge`, then "
                        "`interlatch push`")
    if projects_of(person) is not None:
        return limited_error(person)
    return None


def hello(cfg: Config, conn, body: dict, person: dict | None = None) -> dict:
    """A computer says who it is and which folders it added to projects here; the hub answers with what it has of
    that computer's, and the projects it may add folders to. A person limited to projects (people.py) hears only of
    theirs. They, and every computer of a hub that takes knowledge only, must share knowledge (no transcripts)."""
    from . import people
    from .people import projects_of

    machine_id = check_machine(cfg, str(body.get("machine") or ""))
    if int(body.get("protocol", 0)) != PROTOCOL:
        return {"protocol": PROTOCOL, "version": __version__}
    scope = projects_of(person)
    if body.get("share") != "knowledge" and (refused := transcripts_refused(cfg, person)):
        raise refused
    # the team store holds what computers share as knowledge; one that sends transcripts never waits on it
    store = _store(cfg) if body.get("share") == "knowledge" else None
    if store:
        _in_store(store.computer_seen, machine_id, str(body.get("name") or "")[:120] or None,
                  str(body.get("platform") or "")[:40] or None, str(body.get("version") or "")[:40] or None)
    repos = body.get("repos") if isinstance(body.get("repos"), dict) else {}
    repos = {str(k): [str(v[0]), str(v[1])] for k, v in repos.items() if isinstance(v, list) and len(v) == 2}
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO machines(id, name, platform, version, role, first_seen, last_seen, repos_json) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name = excluded.name, platform = excluded.platform, version = excluded.version, "
        "role = excluded.role, last_seen = excluded.last_seen, repos_json = excluded.repos_json",
        (machine_id, str(body.get("name") or "")[:120] or None, str(body.get("platform") or "")[:40] or None,
         str(body.get("version") or "")[:40] or None, "spoke", now, now, json.dumps(repos)),
    )
    people.record_key(conn, machine_id, body.get("key"))  # the first one it shows: an invite for this id must show it
    conn.commit()
    from ..storage.kv import kv_get, kv_set

    folders, moved, refiled = folders_of(conn, machine_id), 0, False
    if "folders" in body:  # an older spoke doesn't send them: keep what it had
        sent = clean_folders(body.get("folders"))
        if scope is not None:  # a folder can go only to a project this person sees
            sent = {f: proj for f, proj in sent.items() if proj in scope}
        if sent != folders:
            kv_set(conn, FOLDERS_KV + machine_id, json.dumps(sent))
            conn.commit()
            moved = refile(cfg, conn, machine_id, set(folders) | set(sent))
            folders, refiled = sent, True
    if "left" in body:  # an older spoke doesn't send them
        from .people import actor_of, audit

        before, now_left = left_of(conn, machine_id), clean_left(body.get("left"))
        if now_left != before:
            kv_set(conn, LEFT_KV + machine_id, json.dumps(now_left))
            name = str(body.get("name") or "")[:120] or None
            for path in sorted(set(now_left) - set(before)):
                audit(conn, actor_of(person), "leave", person and person["id"], machine=machine_id, name=name, path=path)
            for path in sorted(set(before) - set(now_left)):
                audit(conn, actor_of(person), "rejoin", person and person["id"], machine=machine_id, name=name, path=path)
            conn.commit()
    remotes = {rem: proj for rem, proj in ProjectResolver(cfg, conn).project_remotes().items()
               if scope is None or proj in scope}
    report = folders_report(conn, machine_id, folders, repos, remotes)
    share = "knowledge" if body.get("share") == "knowledge" else "everything"
    kv_set(conn, SHARE_KV + machine_id, share)
    conn.commit()  # local_remotes caches what git said
    me = local_machine(cfg)
    if store:  # the store is the team's record: what it lacks is sent again, and folders that changed refile it all
        shared = {} if refiled else _in_store(store.shared, machine_id)
    else:
        shared = {r[0]: r[1] for r in conn.execute(
            "SELECT id, analyzed_at FROM sessions WHERE machine_id = ? AND source = 'remote'", (machine_id,))}
    return {"protocol": PROTOCOL, "version": __version__, "hub": cfg.hub_name or me["name"],
            "inventory": inventory(cfg, machine_id), "wants_analyses": not kv_get(conn, f"hub-analyses:{machine_id}"),
            "projects": hub_projects(conn, only=scope), "remotes": remotes, "folders": report, "moved": moved,
            "knowledge": shared, "team": bool(store), "scope": scope}


def join_with_code(cfg: Config, conn, body: dict) -> dict:
    """POST /api/hub/join: a computer redeems an invite for a push token of its own. The code is the credential, so
    this is called without a token; the computer is recorded only once its code was good."""
    from . import people

    if not read_token(cfg) or cfg.is_spoke:
        raise HubError("this computer is not a hub")
    machine_id = check_machine(cfg, str(body.get("machine") or ""))
    code, name = str(body.get("code") or ""), str(body.get("name") or "")[:120] or None
    # a read-only person's computer could never send: refuse before the code is used, so it still opens a browser
    invited = people.peek_invite(conn, code)
    if invited and invited["role"] == "readonly":
        raise HubError(f"{invited['name']} is read-only on this hub: read-only people see its dashboard but don't send "
                       "to it. Open the invite link in a browser instead; the code still works there.")
    try:
        person, token = people.join_computer(conn, code, machine_id, name, body.get("key"))
    except people.UnknownCode as exc:
        raise UnknownInvite(str(exc)) from None
    except people.PeopleError as exc:
        raise HubError(str(exc)) from None
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO machines(id, name, platform, version, role, first_seen, last_seen, person_id) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET person_id = excluded.person_id",
        (machine_id, name, str(body.get("platform") or "")[:40] or None, str(body.get("version") or "")[:40] or None,
         "spoke", now, now, person["id"]),
    )
    people.record_key(conn, machine_id, body.get("key"))  # a computer new to the hub: its key from now on
    news.record(conn, machine_id, None, kind="joined")
    conn.commit()
    scope = people.projects_of(person)
    return {"token": token, "person": people.public(person), "hub": cfg.hub_name or local_machine(cfg)["name"],
            "projects": hub_projects(conn, only=scope) if scope is not None else None,
            "share": "knowledge" if scope is not None or cfg.hub_accept == "knowledge" else None}


def signin_code_for(cfg: Config, conn, person: dict | None) -> dict:
    """POST /api/hub/signin: a short-lived code that opens the dashboard as the person whose computer asks. A computer
    sending with the shared token is nobody in particular, so it can't."""
    from . import people

    if person is None:
        raise HubError("join with an invite to sign in")
    return {"code": people.signin_code(conn, person)}


# ------------------------------------------------------------------ what computers send
def receive_file(cfg: Config, conn, params: dict, rfile, length: int) -> dict:
    """Store one gzip-compressed file a spoke sent, verified against its size and sha256, atomically."""
    from ..ingestion.deletion import forgotten_ids

    machine_id = check_machine(cfg, params.get("machine", ""))
    root = params.get("root", "")
    rel = safe_rel(params.get("path", ""))
    try:
        size, mtime, sha = int(params.get("size", "")), float(params.get("mtime", "")), params.get("sha256", "")
    except ValueError:
        raise HubError("bad size or mtime") from None
    if not ROOT_RE.match(root) or rel is None or not re.fullmatch(r"[0-9a-f]{64}", sha) or not 0 <= size <= MAX_FILE_BYTES:
        raise HubError("bad file parameters")
    if any(sid in rel.name for sid in forgotten_ids(conn)):
        _drain(rfile, length)
        return {"ok": True, "skipped": "forgotten"}  # `interlatch forget` on the hub keeps it out for good
    target = machine_dir(cfg, machine_id) / root / Path(*rel.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    h, raw = hashlib.sha256(), 0
    d = zlib.decompressobj(wbits=31)
    left = length
    try:
        with open(tmp, "wb") as out:
            while left > 0:
                chunk = rfile.read(min(left, 1 << 20))
                if not chunk:
                    raise HubError("upload cut off")
                left -= len(chunk)
                data = d.decompress(chunk)
                raw += len(data)
                if raw > size:
                    raise HubError("file larger than announced")
                h.update(data)
                out.write(data)
            tail = d.flush()
            raw += len(tail)
            h.update(tail)
            out.write(tail)
        if raw != size or h.hexdigest() != sha:
            raise HubError("file damaged in transit (size or checksum mismatch)")
        old = target.stat().st_size if target.exists() else None
        os.replace(tmp, target)
        os.utime(target, (mtime, mtime))
    except zlib.error as exc:
        tmp.unlink(missing_ok=True)
        _drain(rfile, left)
        raise HubError(f"not gzip data: {exc}") from None
    except BaseException:
        tmp.unlink(missing_ok=True)
        _drain(rfile, left)
        raise
    conn.execute("UPDATE machines SET last_push = ?, files = files + ?, bytes = bytes + ? WHERE id = ?",
                 (utcnow_iso(), 0 if old is not None else 1, size - (old or 0), machine_id))
    conn.commit()
    return {"ok": True}


def _drain(rfile, left: int) -> None:
    while left > 0:
        chunk = rfile.read(min(left, 1 << 20))
        if not chunk:
            return
        left -= len(chunk)


def _gunzip(blob: bytes, limit: int, what: str) -> bytes:
    """Gzip data another computer sent, inflated only up to `limit` bytes: a small upload that inflates to gigabytes
    is refused before it fills the hub's memory."""
    d = zlib.decompressobj(wbits=31)
    out = d.decompress(blob, limit + 1)
    if len(out) > limit or d.unconsumed_tail:
        raise HubError(f"{what} too large once unpacked")
    if not d.eof:
        raise HubError(f"bad {what}: gzip data cut off")
    return out


def _cell(value):
    """A value another computer sent, as SQLite can store it."""
    if value is None or isinstance(value, (str, int, float)):
        return value
    return json.dumps(value) if isinstance(value, (dict, list)) else str(value)


def receive_sessions(cfg: Config, conn, params: dict, rfile, length: int, person: dict | None = None) -> dict:
    """Sessions another computer analyzed itself ([hub] share = "knowledge"): their details, analysis and project
    lessons, without a transcript. Stored with source 'remote', filed like any session of that computer, and never
    analyzed here. A session the hub has the transcript of keeps its own record."""
    from ..core.paths import project_name_for
    from ..core.sessions import best_title

    machine_id = check_machine(cfg, params.get("machine", ""))
    if length > 64 << 20:
        _drain(rfile, length)
        raise HubError("sessions too large")
    try:
        data = json.loads(_gunzip(rfile.read(length), MAX_SESSIONS_JSON, "sessions"))
    except (OSError, ValueError, EOFError, zlib.error) as exc:
        raise HubError(f"bad sessions: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
        raise HubError("bad sessions")
    from ..ingestion.deletion import forgotten_ids
    from .people import projects_of

    scope = projects_of(person)
    forgotten = forgotten_ids(conn)  # forgotten or purged here: never taken again
    r = ProjectResolver(cfg, conn)
    now = utcnow_iso()
    stored = kept = refused = 0
    accepted, fresh, got = [], set(), {}  # got: {project: [new sessions, new lessons]}
    for rec in data["sessions"][:SHARE_BATCH * 2]:
        sid = str(rec.get("id") or "") if isinstance(rec, dict) else ""
        if not SESSION_ID_RE.match(sid) or rec.get("agent") not in SHARED_AGENTS:
            continue
        if sid in forgotten:
            refused += 1
            continue
        have = conn.execute("SELECT source, machine_id FROM sessions WHERE id = ?", (sid,)).fetchone()
        if not have:
            fresh.add(sid)
        if have and have["source"] != "remote":
            kept += 1  # its transcript is here: the hub's own record wins
            continue
        if have and have["machine_id"] != machine_id:
            refused += 1  # another computer shared it: only that computer may change or take back its session
            log.warning("shared session %s from %s refused: it came from %s", sid, machine_id, have["machine_id"])
            continue
        recorded = str(rec.get("project_path") or "") or None
        remote = rec.get("remote") if isinstance(rec.get("remote"), str) else None
        project = r.resolve(machine_id, recorded, remote)
        if scope is not None and project not in scope:
            refused += 1  # a person limited to projects: only what belongs to theirs is kept
            continue
        name = project_name_for(project)
        # what is about the project only, its text redacted again: an older computer still sends its raw analysis,
        # mood and friction notes, which are cleared (None), here and in what it stored before
        row = {c: _cell(rec.get(c)) for c in SHARED_COLS + SHARED_ANALYSIS_COLS}
        row.update(shared_analysis(row))
        row.update(dict.fromkeys(PERSONAL_ANALYSIS_COLS))
        row.update(id=sid, source="remote", machine_id=machine_id, project_path=project, project_name=name,
                   machine_path=recorded if recorded != project else None, source_present=1, ingested_at=now,
                   analysis_status="done", title=best_title({"llm_title": row["llm_title"]}),
                   ended_flag=1 if rec.get("ended_flag") else 0)
        # lessons about the project only: one about the person should not have been sent, and is not kept
        lessons = [shared_lesson(k) for k in rec.get("knowledge") or [] if isinstance(k, dict) and k.get("title")
                   and k.get("kind") and k.get("scope") == "project" and k.get("kind") != "preference"]
        shared = {k: v for k, v in rec.items() if k not in PERSONAL_ANALYSIS_COLS}
        accepted.append((sid, {**shared, **{c: row[c] for c in SHARED_ANALYSIS_COLS}, "knowledge": lessons}, row, remote))
    store = _store(cfg)
    if store and accepted:  # the team's record first: if it fails, nothing is kept and the computer sends it again
        from ..storage.team import payload

        _in_store(store.put_sessions, machine_id, [
            payload(rec, project=row["project_path"], project_name=row["project_name"], remote=remote,
                    title=row["title"], details_cols=SHARED_COLS) for _, rec, row, remote in accepted])
    for sid, rec, row, _ in accepted:
        cols = list(row)
        try:
            conn.execute(f"INSERT INTO sessions({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
                         "ON CONFLICT(id) DO UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id"),
                         [row[c] for c in cols])
        except sqlite3.Error as exc:  # one bad record must not lose the rest of the batch
            log.warning("shared session %s from %s not stored: %s", sid, machine_id, exc)
            continue
        project, name = row["project_path"], row["project_name"]
        before = news.lesson_keys(conn, sid)
        conn.execute("DELETE FROM knowledge WHERE session_id = ? AND source = 'analysis'", (sid,))
        for k in rec["knowledge"]:
            krow = {c: _cell(k.get(c)) for c in KNOWLEDGE_COLS}
            krow.update(session_id=sid, project_path=project, project_name=name, source="analysis")
            conn.execute(f"INSERT OR IGNORE INTO knowledge({', '.join(krow)}) VALUES ({', '.join('?' for _ in krow)})",
                         list(krow.values()))
        tally = got.setdefault(project, [0, 0])
        tally[0] += sid in fresh
        tally[1] += news.learned(conn, sid, before)
        stored += 1
    for project, (sessions, lessons) in got.items():  # the Team page's news (news.py)
        news.record(conn, machine_id, project, sessions=sessions, lessons=lessons)
    if stored:
        conn.execute("UPDATE machines SET last_push = ? WHERE id = ?", (now, machine_id))
    conn.commit()
    return {"ok": True, "stored": stored, "kept": kept, "refused": refused}


def team_lessons(cfg: Config, body: dict, person: dict | None = None, conn=None) -> dict:
    """What teammates learned in the projects a computer works on, from the team store (pull_team_lessons asks).
    {"team": False} when this hub keeps no team store; {"unchanged": True} when the computer already has this answer.
    A person limited to projects gets lessons from those projects only, whatever the computer names."""
    from .people import projects_of

    machine_id = check_machine(cfg, str(body.get("machine") or ""))
    store = _store(cfg)
    if not store:
        return {"team": False}
    if conn is not None:  # the hub owner's own lessons in projects set up here, before teammates ask for them
        try:
            share_own(cfg, conn, store)
        except HubError as exc:
            log.warning("team store: this hub's own sessions not shared: %s", exc)
    remotes = [normalize_remote(x) or x for x in body.get("remotes") or [] if isinstance(x, str) and len(x) <= 300][:500]
    projects = [normalize_project_path(x) for x in body.get("projects") or []
                if isinstance(x, str) and is_abs_path(x) and len(x) <= 1024]
    left = clean_left(body.get("left"))  # projects the computer left: none of their lessons
    within = projects_of(person)

    def spelled(paths):  # a Windows project the store may hold as C:\x or c:/x (filed before it was normalized)
        return None if paths is None else list(dict.fromkeys(v for p in paths for v in path_variants(p)))

    data = _in_store(lambda: store.lessons_for(machine_id, remotes, spelled(projects[:MAX_FOLDERS]), within=spelled(within),
                                               left=spelled(left)))
    version = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:32]
    if body.get("version") == version:
        return {"team": True, "version": version, "unchanged": True}
    return {"team": True, "version": version, "hub": cfg.hub_name or local_machine(cfg)["name"], **data}


def receive_analyses(cfg: Config, conn, params: dict, rfile, length: int) -> dict:
    from ..storage.kv import kv_set

    machine_id = check_machine(cfg, params.get("machine", ""))
    if length > 512 << 20:
        _drain(rfile, length)
        raise HubError("analyses too large")
    blob = rfile.read(length)
    try:
        data = json.loads(_gunzip(blob, MAX_ANALYSES_JSON, "analyses"))
    except (OSError, ValueError, EOFError, zlib.error) as exc:
        raise HubError(f"bad analyses: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
        raise HubError("bad analyses")
    dest = machine_dir(cfg, machine_id) / ANALYSES_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, dest)
    kv_set(conn, f"hub-analyses:{machine_id}", utcnow_iso())
    conn.commit()
    return {"ok": True, "sessions": len(data["sessions"])}


class IngestTrigger:
    """Runs `interlatch sync --work` after a push, one at a time; a push during a run queues exactly one more."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.running = False
        self.again = False

    def command(self) -> list[str]:
        from ..core.process import self_command

        return [*self_command(), "sync", "--work", "--quiet"]

    def request(self) -> None:
        with self.lock:
            if self.running:
                self.again = True
                return
            self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        from ..core.process import INTERNAL_ENV

        while True:
            with self.lock:
                self.again = False
            log_path = self.cfg.logs_dir / "hub.log"
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a") as fh:
                    subprocess.run(self.command(), stdin=subprocess.DEVNULL, stdout=fh, stderr=fh, start_new_session=True,
                                   env={k: v for k, v in os.environ.items() if k != INTERNAL_ENV})
            except OSError as exc:
                log.warning("hub ingest failed to start: %s", exc)
            with self.lock:
                if not self.again:
                    self.running = False
                    return


# ------------------------------------------------------------------ the hub's own sessions, for the team store
def share_own(cfg: Config, conn, store) -> int:
    """On a hub with a team store: send the store this computer's own analyzed sessions in projects set up here
    (add_project), the same details and project lessons a member's computer shares, so teammates get the hub
    owner's lessons too. Only what changed since the last time; returns how many sessions went."""
    declared = declared_projects(conn)
    if not declared:
        return 0
    me = local_machine(cfg)
    have = _in_store(store.shared, me["id"])
    marks = ", ".join("?" for _ in declared)
    records, _, _ = _shared_records(cfg, have, conn=conn, where=f" AND machine_id = ? AND project_path IN ({marks})",
                                    params=(me["id"], *declared))
    if not records:
        return 0
    from ..core.paths import project_name_for
    from ..storage.team import payload

    _in_store(store.computer_seen, me["id"], me["name"], platform_label(), __version__)
    for i in range(0, len(records), SHARE_BATCH):
        _in_store(store.put_sessions, me["id"], [
            payload(rec, project=rec["project_path"], project_name=project_name_for(rec["project_path"]),
                    remote=rec["remote"], title=rec.get("llm_title"), details_cols=SHARED_COLS)
            for rec in records[i:i + SHARE_BATCH]])
    return len(records)


# ------------------------------------------------------------------ purging and withdrawing what was shared
def purge_targets(conn, machine_ids: list[str], *, projects: list[str] | None = None,
                  keep: list[str] | None = None) -> list[dict]:
    """What `interlatch hub purge` would remove: sessions those computers sent, filed under `projects`, or (`keep`)
    under any project but those, sessions filed under no project included."""
    if not machine_ids or (projects is None) == (keep is None):
        return []
    marks = ", ".join("?" for _ in machine_ids)
    sql = f"SELECT id, project_path, project_name, title, source, transcript_path FROM sessions WHERE machine_id IN ({marks})"
    params: list = list(machine_ids)
    if projects is not None:
        sql += f" AND project_path IN ({', '.join('?' for _ in projects) or 'NULL'})"
        params += projects
    elif keep:
        sql += f" AND (project_path IS NULL OR project_path NOT IN ({', '.join('?' for _ in keep)}))"
        params += keep
    return [dict(r) for r in conn.execute(sql + " ORDER BY project_path, started_at", params)]


def purge(cfg: Config, conn, sessions: list[dict], *, by: str | None = None, person_id: int | None = None,
          action: str = "purge", keep_out: bool = True, owner: str | None = None, **detail) -> dict:
    """Remove sessions other computers sent from this hub for good (purge_targets picks them): their rows, lessons,
    notes and received transcripts, the team store's copy, and the knowledge bases built from them. Each is forgotten
    (ingestion.deletion.forget_session), so the computer that sent it can't send it again, unless `keep_out` is off
    (withdraw). `owner`: the only computer whose sessions may go (withdraw). `action` and `detail` go in the audit.
    Returns what was removed."""
    from ..ingestion.deletion import forget_session
    from .people import LOCAL, audit

    me = local_machine(cfg)["id"]
    ids = [x["id"] for x in sessions]
    if conn.execute(f"SELECT 1 FROM sessions WHERE machine_id = ? AND id IN ({', '.join('?' for _ in ids) or 'NULL'})",
                    (me, *ids)).fetchone():
        raise HubError("purge removes what other computers sent, not this hub's own sessions (`interlatch forget`)")
    if owner is not None and conn.execute(
            f"SELECT 1 FROM sessions WHERE machine_id IS NOT ? AND id IN ({', '.join('?' for _ in ids) or 'NULL'})",
            (owner, *ids)).fetchone():
        raise HubError("a computer takes back only the sessions it sent")
    store = _store(cfg)
    if store and ids:  # the team's record first: if it fails, nothing here changes and purge can run again
        _in_store(store.forget_sessions, ids, owner)
    received = cfg.machines_dir
    for x in sessions:  # forget_session also removes its note in the Markdown vault
        sent_here = bool(x["transcript_path"]) and under(x["transcript_path"], str(received))
        forget_session(conn, cfg, x["id"], delete_transcript=sent_here, remember=keep_out)
    projects = sorted({x["project_path"] for x in sessions if x["project_path"]})
    emptied = [p for p in projects if not conn.execute("SELECT 1 FROM sessions WHERE project_path = ? LIMIT 1",
                                                       (p,)).fetchone()]
    for p in projects:  # built from the lessons just removed: the next sync builds it again from what is left
        conn.execute("DELETE FROM project_kb WHERE project_path = ?", (p,))
    for p in emptied:
        conn.execute("DELETE FROM glossary_usage WHERE project_path = ?", (p,))
        conn.execute("DELETE FROM suggestions WHERE project_path = ? AND status IN ('new', 'stale')", (p,))
    conn.execute("DELETE FROM suggestions WHERE knowledge_id IS NOT NULL AND status IN ('new', 'stale') AND "
                 "knowledge_id NOT IN (SELECT id FROM knowledge)")
    audit(conn, by or LOCAL, action, person_id, sessions=len(ids), projects=projects, **detail)
    conn.commit()
    return {"sessions": len(ids), "projects": projects, "emptied": emptied, "store": bool(store)}


def withdraw(cfg: Config, conn, body: dict, person: dict | None = None) -> dict:
    """POST /api/hub/withdraw: a computer that shares knowledge takes back what it shared from a folder it added to a
    project here (`interlatch hub remove-folder`: the wrong folder). The sessions it sent from that folder that are
    filed under the folder's project go, with their lessons, here and in the team store. Unlike purge they aren't
    kept out: once their folder is added to the right project, they are shared again.

    With `sessions` (ids) instead of a folder: those of them it sent go, wherever they are filed. A computer with
    several hubs does this when a folder moves to another hub (docs/profiles.md). The answer's `withdrawn` lists the
    ones that went; an older hub doesn't send it."""
    from .people import actor_of

    machine_id = check_machine(cfg, str(body.get("machine") or ""))
    if "sessions" in body:
        return _withdraw_sessions(cfg, conn, machine_id, body.get("sessions"), person)
    folder = str(body.get("folder") or "").strip()
    folder = folder.rstrip("/") or "/"
    project = folders_of(conn, machine_id).get(folder)
    if not project:  # never added here (or it never said so): nothing of it was filed by that folder
        return {"sessions": 0, "project": None}
    rows = [dict(r) for r in conn.execute(
        "SELECT id, project_path, project_name, title, source, transcript_path, machine_path FROM sessions "
        "WHERE machine_id = ? AND project_path = ? AND source = 'remote'", (machine_id, project))]
    targets = [r for r in rows if under(r["machine_path"] or r["project_path"], folder)]
    if not targets:
        return {"sessions": 0, "project": project}
    name = conn.execute("SELECT name FROM machines WHERE id = ?", (machine_id,)).fetchone()
    try:
        done = purge(cfg, conn, targets, by=actor_of(person), person_id=person and person["id"], action="withdraw",
                     keep_out=False, owner=machine_id, machine=machine_id, name=name[0] if name else None, path=project)
    except RuntimeError as exc:  # the hub is syncing: the computer asks again
        raise HubError(str(exc)) from None
    return {"sessions": done["sessions"], "project": project}


WITHDRAW_MAX = 1000  # session ids in one request: the computer sends more in batches


def _withdraw_sessions(cfg: Config, conn, machine_id: str, ids, person: dict | None) -> dict:
    """withdraw() by session id: the ones this computer sent, here or only in the team store."""
    from .people import actor_of

    if not isinstance(ids, list) or len(ids) > WITHDRAW_MAX or not all(isinstance(x, str) and SESSION_ID_RE.match(x)
                                                                       for x in ids):
        raise HubError(f"bad sessions: a list of at most {WITHDRAW_MAX} session ids")
    ids = list(dict.fromkeys(ids))
    marks = ", ".join("?" for _ in ids) or "NULL"
    here = {r["id"]: dict(r) for r in conn.execute(
        f"SELECT id, machine_id, source, project_path, project_name, title, transcript_path FROM sessions "
        f"WHERE id IN ({marks})", ids)}
    targets = [r for r in here.values() if r["machine_id"] == machine_id and r["source"] == "remote"]
    store = _store(cfg)
    if store:  # shared as knowledge, the team store may hold it with no row here
        shared = _in_store(store.shared, machine_id)
        targets += [{"id": x, "project_path": None, "project_name": None, "title": None, "transcript_path": None}
                    for x in ids if x not in here and x in shared]
    if not targets:
        return {"sessions": 0, "withdrawn": []}
    name = conn.execute("SELECT name FROM machines WHERE id = ?", (machine_id,)).fetchone()
    try:
        purge(cfg, conn, targets, by=actor_of(person), person_id=person and person["id"], action="withdraw",
              keep_out=False, owner=machine_id, machine=machine_id, name=name[0] if name else None, moved=True)
    except RuntimeError as exc:  # the hub is syncing: the computer asks again
        raise HubError(str(exc)) from None
    return {"sessions": len(targets), "withdrawn": [t["id"] for t in targets]}
