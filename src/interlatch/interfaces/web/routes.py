"""Every dashboard route: its path, who may call it, and what answers it, in the order they are matched (the first
that fits answers).

The handler (handler.py) has already checked the request by then: the host, the origin, the person signed in (on a
hub with people) and the projects they may see; for a POST, that the page sent it and that the person is an admin.
A route's Access adds what that route needs on top. An answer gets the handler (to reply, and to know who asks), the
request's Context, the path's match, and the query (GET) or the JSON body (POST). The hub API, which other computers
call with their token, is the handler's _hub_api.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote

from ...core import i18n
from ...core.i18n import tr
from ...knowledge.synthesize import GLOBAL
from ...sharing import news, people
from . import api

NOT_HERE = "change this on the computer itself, not from another device"
NOT_ADMIN = "only an admin of this hub can do this"
GAME_OFF = "game mode is off: turn it on in Settings › Appearance"


@dataclass(frozen=True)
class Access:
    allows: Callable  # (handler, ctx) -> bool
    refusal: str  # why not, as the page shows it (translated)
    game: bool = False  # Innerlatch's: refused to everyone while game mode is off ([game] enabled)

    def refused(self, h, ctx) -> str | None:
        """Why the request may not go on (untranslated), or None when it may."""
        if self.game and not ctx.cfg.game_enabled:
            return GAME_OFF
        return None if self.allows(h, ctx) else self.refusal


ANYONE = Access(lambda h, ctx: True, "")  # whoever passed the request's checks
ADMIN = Access(lambda h, ctx: h._can_admin(), NOT_ADMIN)  # at the hub itself, or an admin person
# a password, an API key, or where this archive goes: at the computer itself, or an admin of a hub with people
OWNER = Access(lambda h, ctx: h._can_admin(), NOT_HERE)
ADMIN_PERSON = Access(lambda h, ctx: h.viewer is None or h._can_admin(), NOT_ADMIN)  # refuses only people who aren't admins
HERE = Access(lambda h, ctx: h._from_here(), NOT_HERE)  # what leaves this computer, or changes it: only from here
LEADERBOARD = Access(lambda h, ctx: h._from_here(), "the leaderboard is on the dashboard of your own computer", game=True)
# the Party: this computer's own (behind its Google sign-in), or a hub person's own (api/party.py)
PARTY = Access(lambda h, ctx: h._from_here() or h.viewer is not None,
               "the Party opens on the dashboard of your own computer, or of a hub you're signed in to", game=True)
PARTY_SIGNIN = Access(lambda h, ctx: h._from_here(), NOT_HERE, game=True)  # a Google sign-in kept on this computer
LOCAL_FILES = Access(lambda h, ctx: h._local(), "files open only on the computer Interlatch runs on")
# a dedicated hub has no sessions of its own, so there its admins may set up (by name) and remove projects too
HERE_OR_HUB_ADMIN = Access(lambda h, ctx: h._from_here() or (ctx.cfg.hub_dedicated and h._can_admin()), NOT_HERE)


@dataclass(frozen=True)
class Route:
    path: str  # the path, a regular expression the whole path matches (when it has a group), or a prefix (`prefix`)
    access: Access
    answer: Callable  # (handler, ctx, match, data) -> what the handler sends
    prefix: bool = False

    def match(self, path: str):
        """None when the route doesn't take `path`; else the regex match, or True."""
        if self.prefix:
            return path.startswith(self.path) or None
        if "(" in self.path:
            return re.fullmatch(self.path, path)
        return path == self.path or None


def find(routes: list[Route], path: str):
    """(route, match) for the first route that takes `path`, or (None, None)."""
    for route in routes:
        m = route.match(path)
        if m:
            return route, m
    return None, None


def _body(data) -> dict:
    return data if isinstance(data, dict) else {}


def _not_found(h, ctx, m, data):
    return h._json({"error": tr("not found")}, 404)


# ------------------------------------------------------------------ GET
def _export(h, ctx, m, q):  # a download: one session's file, or a .zip of several
    from ..exports.sessions import ExportError

    try:
        if q.get("check"):  # asked first, so a refusal shows as a message, not a downloaded error
            return h._json(api.sessions.export_check(ctx, q.get("ids", ""), q.get("format", "md")))
        return h._download(*api.sessions.export(ctx, q.get("ids", ""), q.get("format", "md")))
    except ExportError as exc:
        return h._json({"error": str(exc)}, 400)


def _session(h, ctx, m, q):
    if h.limited:
        s = api.sessions.session_summary(ctx, m.group(1))
        return h._json(s) if s else h._json({"error": tr("not found")}, 404)
    s = api.sessions.session(ctx, m.group(1))
    return h._json({**s, "local": h._local()}) if s else h._json({"error": tr("not found")}, 404)


def _avatar(h, ctx, m, q):
    from ...sharing.leaderboard import avatar

    got = avatar(m.group(1), ctx.cfg)
    return h._image(*got) if got else h._json({"error": "no avatar"}, 404)


def _team_news(h, ctx, m, q):
    after = q.get("after") if news.ISO_RE.fullmatch(q.get("after") or "") else None
    return h._json(api.team.team_news(ctx, h.viewer, scope=people.projects_of(h.viewer), admin=h._can_admin(),
                                      after=after))


def _artifact_open(h, ctx, m, q):  # the file itself, in a new tab: from disk, or as the agent wrote it once it is gone
    f = api.artifacts.artifact_open(ctx, int(m.group(1)))
    return h._open_file(f) if f else h._json({"error": tr("this file can no longer be opened")}, 404)


def _artifact_file(h, ctx, m, q):  # an image or diagram an agent made, still on disk: shown as a thumbnail
    f = api.artifacts.artifact_file(ctx, int(m.group(1)))
    return h._image(*f) if f else h._json({"error": tr("not available")}, 404)


def _project(h, ctx, m, q):
    proj = api.projects.project(ctx, unquote(q.get("path", "")), here=h._from_here(), limited=h.limited)
    return h._json({**proj, "local": h._local()}) if proj else h._json({"error": tr("not found")}, 404)


def _group(h, ctx, m, q):  # a group of projects read as one (groups.py): not for someone limited to projects
    gid = _group_id(q)
    if gid is None:
        return h._json({"error": tr("not found")}, 404)
    found = api.projects.group(ctx, gid, here=h._from_here(), limited=h.limited)
    return h._json({**found, "local": h._local()}) if found else h._json({"error": tr("not found")}, 404)


def _group_id(q) -> int | None:
    try:
        return int(q.get("id", ""))
    except ValueError:
        return None


def _group_map(h, ctx, m, q):  # the group's projects and the services they use, from the Systems map's evidence
    found = api.projects.group_map(ctx, _group_id(q)) if _group_id(q) is not None else None
    return h._json(found) if found else h._json({"error": tr("not found")}, 404)


def _group_map_file(h, ctx, m, q):  # a download: the group's map as an .excalidraw file
    f = api.projects.group_map_file(ctx, _group_id(q)) if _group_id(q) is not None else None
    return h._download(*f) if f else h._json({"error": tr("not found")}, 404)


def _group_graph(h, ctx, m, q):  # the group's Mindmap and Connections, once analyzed
    found = api.projects.group_graph(ctx, _group_id(q)) if _group_id(q) is not None else None
    return h._json(found) if found else h._json({"error": tr("not found")}, 404)


def _group_graph_analyze(h, ctx, m, b):  # the Analyze button: the analyzer checks the group's links
    try:
        started = api.projects.action_group_graph(ctx, int(b.get("id")))
    except (TypeError, ValueError):
        started = None
    return h._json({"started": started}) if started is not None else h._json({"error": tr("not found")}, 404)


def _reanalyze(h, ctx, m, b):  # a project's or group's weekly re-analysis, on or off
    found = api.projects.action_reanalyze(ctx, b.get("kind"), b.get("target"), bool(b.get("on")))
    return h._json(found) if found is not None else h._json({"error": tr("not found")}, 404)


def _diagram(h, ctx, m, q):  # a download: the project's architecture sketch as an .excalidraw file
    f = api.knowledge.diagram_file(ctx, q.get("path", ""))
    return h._download(*f) if f else h._json({"error": tr("no architecture sketch for this project")}, 404)


def _system(h, ctx, m, q):
    found = api.knowledge.system(ctx, q.get("id", ""))
    return h._json(found) if found else h._json({"error": tr("not found")}, 404)


def _as_hub(fn):
    """A Settings › Hubs action for the hub its body (or query) names, Personal's by default: every hub's page offers
    the same ones (api.hubs.for_hub)."""
    def run(h, ctx, m, body):
        key = (body or {}).get("hub") if isinstance(body, dict) else None
        if key and str(key).strip().casefold() != "personal" and not h._from_here():  # another hub: only from here
            return h._json({"error": tr("Change this on the computer itself, not from another device.")}, 403)
        view, problem = api.hubs.for_hub(ctx, key)
        return h._json({"error": problem}, 400) if problem else fn(h, view, m, body)
    return run


@_as_hub
def _devices(h, ctx, m, q):
    dv = api.devices.devices(ctx)
    if h.viewer is not None and not h._can_admin():  # computers, keys and the team store: admins'
        dv = {**dv, "this": {"name": dv["this"]["name"]}, "machines": [], "store": None,
              "allowed_hosts": [], "allowed_users": [], "path_map": {}, "folders": [], "projects": [],
              "team": None, "last_push": None}
    return h._json({**dv, "here": h._from_here(), **h._me(), "hub_key": None if ctx.cfg.owner == "personal" else (q or {}).get("hub")})


def _unseen_suggestions(h, ctx, m, q):
    from ...knowledge.suggest import count_unseen

    return h._json({"unseen": count_unseen(ctx.conn)})


GET: list[Route] = [
    Route("/api/me", ANYONE, lambda h, ctx, m, q: h._json(h._me())),
    Route("/api/people", ADMIN, lambda h, ctx, m, q: h._json(api.hub_admin.people_info(ctx, h._session()))),
    Route("/api/overview", ANYONE, lambda h, ctx, m, q: h._json(api.status.overview(ctx, q, limited=h.limited))),
    # a person's own computer only
    Route("/api/leaderboard", LEADERBOARD, lambda h, ctx, m, q: h._json(api.leaderboard.leaderboard(ctx, q))),
    Route(r"/api/leaderboard/avatar/([\w-]+)", LEADERBOARD, _avatar),
    Route("/api/leaderboard/arena/battle", LEADERBOARD, lambda h, ctx, m, q: h._json(*api.leaderboard.arena_battle(ctx, q))),
    Route("/api/leaderboard/", LEADERBOARD, _not_found, prefix=True),
    # nothing about the agents before a sign-in; limited people see the party their projects' lessons make
    Route("/api/party", PARTY, lambda h, ctx, m, q: h._json(api.party.party(ctx, h.viewer, here=h._from_here()))),
    Route("/api/party/skill", PARTY, lambda h, ctx, m, q: h._json(*api.party.skill(ctx, h.viewer, q))),
    Route("/api/party/track", PARTY, lambda h, ctx, m, q: h._json(*api.party.track(ctx, h.viewer, q))),
    Route("/api/party/tracks", PARTY, lambda h, ctx, m, q: h._json(api.party.tracks(ctx, h.viewer))),
    # the Adventure: every counted lesson as an encounter on a map, and one lesson as a battle (behind the same sign-in)
    Route("/api/party/map", PARTY, lambda h, ctx, m, q: h._json(*api.party.adventure_map(ctx, h.viewer))),
    Route("/api/party/encounter", PARTY, lambda h, ctx, m, q: h._json(*api.party.encounter(ctx, h.viewer, q))),
    Route("/api/party/ask", PARTY, lambda h, ctx, m, q: h._json(*api.party.ask(ctx, h.viewer, q))),
    Route("/api/party/gate", PARTY, lambda h, ctx, m, q: h._json(*api.party.gate(ctx, h.viewer, q))),
    Route("/api/party/jobs", PARTY, lambda h, ctx, m, q: h._json(*api.party.jobs(ctx, h.viewer))),
    # the Town & gear: the viewer's own bag, and a region's shop (gold from wins; stock unlocked by real coverage)
    Route("/api/party/bag", PARTY, lambda h, ctx, m, q: h._json(*api.party.bag(ctx, h.viewer))),
    Route("/api/party/shop", PARTY, lambda h, ctx, m, q: h._json(*api.party.shop(ctx, h.viewer, q))),
    Route("/api/team/who", ANYONE, lambda h, ctx, m, q: h._json(api.team.who_options(ctx))),
    Route("/api/team", ANYONE, lambda h, ctx, m, q: h._json(api.team.team(
        ctx, q, scope=people.projects_of(h.viewer), admin=h._can_admin(), viewer=h.viewer))),
    Route("/api/team/news", ANYONE, _team_news),
    Route("/api/sessions", ANYONE, lambda h, ctx, m, q: h._json(api.sessions.sessions(ctx, q, limited=h.limited))),
    Route("/api/export", ANYONE, _export),
    Route(r"/api/sessions/([\w-]+)", ANYONE, _session),
    Route(r"/api/sessions/([\w-]+)/events", ANYONE,
          lambda h, ctx, m, q: h._json(api.sessions.events(ctx, m.group(1), q))),
    Route(r"/api/sessions/([\w-]+)/matches", ANYONE,
          lambda h, ctx, m, q: h._json(api.sessions.matches(ctx, m.group(1), q))),
    Route("/api/projects", ANYONE,
          lambda h, ctx, m, q: h._json(api.projects.projects(ctx, scope=people.projects_of(h.viewer)))),
    # can_share: what leaves this computer changes only from here
    Route("/api/project-groups", ANYONE, lambda h, ctx, m, q: h._json(
        {**api.projects.project_groups(ctx), "can_share": ctx.cfg.is_spoke and h._from_here()})),
    Route("/api/artifacts", ANYONE,
          lambda h, ctx, m, q: h._json({**api.artifacts.artifacts(ctx, q), "local": h._local()})),
    Route(r"/api/artifacts/(\d+)/open", ANYONE, _artifact_open),
    Route(r"/api/artifacts/(\d+)/file", ANYONE, _artifact_file),
    Route("/api/project", ANYONE, _project),
    Route("/api/group", ANYONE, _group),
    Route("/api/group/map", ANYONE, _group_map),
    Route("/api/group/map.excalidraw", ANYONE, _group_map_file),
    Route("/api/group/graph", ANYONE, _group_graph),
    Route("/api/reanalysis", ADMIN_PERSON, lambda h, ctx, m, q: h._json(api.projects.reanalysis(ctx))),
    Route("/api/diagram", ANYONE, _diagram),
    # parse_qs has decoded the path already: a second unquote would mangle a literal %
    Route("/api/file", ANYONE, lambda h, ctx, m, q: h._json(
        api.projects.file_sessions(ctx, q.get("path", ""), min(int(q.get("limit") or 50), 500)))),
    Route("/api/files", ANYONE, lambda h, ctx, m, q: h._json(api.projects.folder_files(
        ctx, q.get("root", ""), min(int(q.get("limit") or 200), 2000), existing=q.get("existing") == "1",
        ignored=q.get("ignored") != "0"))),
    Route("/api/knowledge", ANYONE, lambda h, ctx, m, q: h._json(api.knowledge.knowledge(ctx, q))),
    Route("/api/learning/interests", ANYONE,
          lambda h, ctx, m, q: h._json(api.knowledge.learning_interests(ctx, h.viewer, q))),
    Route("/api/learning/glance", ANYONE,
          lambda h, ctx, m, q: h._json(api.knowledge.learning_glance(ctx, h.viewer, q))),
    Route("/api/knowledge/hub", ANYONE, lambda h, ctx, m, q: h._json(api.knowledge.knowledge_hub(ctx))),
    Route("/api/search", ANYONE, lambda h, ctx, m, q: h._json(api.sessions.search(ctx, q))),
    Route("/api/status", ANYONE, lambda h, ctx, m, q: h._json(api.status.status(ctx))),
    Route("/api/reviews", ANYONE, lambda h, ctx, m, q: h._json(api.knowledge.reviews(ctx))),
    Route("/api/glossary", ANYONE, lambda h, ctx, m, q: h._json(api.knowledge.glossary(ctx, q))),
    Route("/api/connectors", ANYONE, lambda h, ctx, m, q: h._json(api.sources.connectors(ctx))),
    Route("/api/mcp", ANYONE, lambda h, ctx, m, q: h._json(api.sources.mcp_info())),
    Route("/api/mcp-clients", ANYONE, lambda h, ctx, m, q: h._json(api.sources.mcp_clients())),
    Route("/api/glossary/terms", ANYONE, lambda h, ctx, m, q: h._json(api.knowledge.glossary_terms(ctx))),
    Route("/api/map", ANYONE, lambda h, ctx, m, q: h._json(api.knowledge.map(ctx))),
    Route("/api/systems", ANYONE, lambda h, ctx, m, q: h._json(api.knowledge.systems(ctx))),
    Route("/api/system", ANYONE, _system),
    Route("/api/jobs", ANYONE, lambda h, ctx, m, q: h._json(api.status.status_small(ctx, limited=h.limited))),
    Route("/api/analysis/queue", ANYONE, lambda h, ctx, m, q: h._json(api.status.analysis_queue(ctx))),
    Route("/api/update", ANYONE, lambda h, ctx, m, q: h._json(api.settings.update_info(ctx, remote=False))),
    # the GitHub page for a report: the system's details only here or for an admin (api/settings.py)
    Route("/api/feedback", ANYONE, lambda h, ctx, m, q: h._json(api.settings.feedback(ctx, q, full=h._can_admin()))),
    Route("/api/imports", ANYONE, lambda h, ctx, m, q: h._json(api.sources.imports(ctx))),
    Route("/api/devices", ANYONE, _devices),
    # this computer's hubs, its folders that wait: only from here (docs/profiles.md)
    Route("/api/hubs", HERE, lambda h, ctx, m, q: h._json(api.hubs.hubs(ctx))),
    # its database, host and counts: admins'
    Route("/api/team-store", ADMIN_PERSON, lambda h, ctx, m, q: h._json(api.hub_admin.team_store_status(ctx))),
    # its address and user: at the computer itself, or an admin of a hub
    Route("/api/mirror", OWNER, lambda h, ctx, m, q: h._json(api.storage.mirror_info(ctx))),
    Route("/api/mirror/status", OWNER, lambda h, ctx, m, q: h._json(api.storage.mirror_status(ctx))),
    Route("/api/team/settings", ADMIN, lambda h, ctx, m, q: h._json(api.hub_admin.hub_settings(ctx))),
    Route("/api/projects/shared", ADMIN,
          lambda h, ctx, m, q: h._json({**api.devices.shared_projects(ctx), "here": h._from_here()})),
    Route("/api/suggestions", ANYONE, lambda h, ctx, m, q: h._json(api.knowledge.suggestions(ctx, q))),
    Route("/api/suggestions/unseen", ANYONE, _unseen_suggestions),
    Route(r"/api/suggestions/(\d+)/preview", ANYONE,
          lambda h, ctx, m, q: h._json(*api.knowledge.suggestion_preview(ctx, int(m.group(1)), q))),
    Route("/api/friction", ANYONE, lambda h, ctx, m, q: h._json(api.knowledge.friction(ctx, q))),
]


# ------------------------------------------------------------------ POST
def _leaderboard(h, ctx, m, body):  # signing in keeps a credential on this computer: only from here
    from ...sharing.leaderboard import LeaderboardError

    body = _body(body)
    try:
        if m.group(1) == "signin":  # the hosted page comes back to this dashboard, as it was opened
            return h._json(api.leaderboard.action_leaderboard_signin(ctx, f"http://{h._host()}/", i18n.lang.get()))
        if m.group(1) == "finish":
            return h._json(api.leaderboard.action_leaderboard_finish(ctx, body))
        if m.group(1) == "cancel":
            api.leaderboard.action_leaderboard_cancel(ctx)
            return h._json({"ok": True})
        if m.group(1) == "name":
            return h._json(api.leaderboard.action_leaderboard_name(ctx, body))
        if m.group(1) == "send":
            return h._json(api.leaderboard.action_leaderboard_send(ctx))
        if m.group(1) == "join":
            return h._json(api.leaderboard.action_leaderboard_join(ctx))
        return h._json(api.leaderboard.action_leaderboard_leave(ctx))
    except LeaderboardError as e:
        return h._json({"error": str(e)}, 502)


def _arena(h, ctx, m, body):  # what this computer publishes about its party: only from here
    from ...sharing.leaderboard import LeaderboardError

    try:
        if m.group(1) == "deck":
            return h._json(api.leaderboard.action_arena_deck(ctx, _body(body)))
        return h._json(api.leaderboard.action_arena(ctx, m.group(1)))
    except LeaderboardError as e:
        return h._json({"error": str(e)}, 502)


def _party_sign(h, ctx, m, body):  # the Party's Google sign-in: the leaderboard's, kept off the board
    from ...sharing.leaderboard import LeaderboardError

    try:
        if m.group(1) == "signin":  # back to this dashboard, as it was opened (the Leaderboard page forwards)
            return h._json(api.party.action_party_signin(ctx, f"http://{h._host()}/", i18n.lang.get()))
        return h._json(api.party.action_party_signout(ctx))
    except LeaderboardError as e:
        return h._json({"error": str(e)}, 502)


def _party_own(h, ctx, m, body):  # the viewer's own quest log, goal, hero, cleared encounters, bag and decks
    if m.group(1) == "deck":
        return h._json(*api.party.action_party_deck(ctx, h.viewer, _body(body), here=h._from_here()))
    if m.group(1) in ("buy", "sell", "equip", "use"):
        return h._json(*api.party.action_party_bag(ctx, h.viewer, m.group(1), _body(body), here=h._from_here()))
    act = {"quest": api.party.action_party_quest, "goal": api.party.action_party_goal, "hero": api.party.action_party_hero, "gate": api.party.action_party_gate, "job": api.party.action_party_job,
           "cleared": api.party.action_party_cleared, "pick": api.party.action_party_pick}[m.group(1)]
    return h._json(*act(ctx, h.viewer, _body(body), here=h._from_here()))


def _reveal(h, ctx, m, body):  # open the file in its own app, or show it in Finder: only from this computer
    err = api.artifacts.artifact_reveal(ctx, int(m.group(1)), "reveal" if body.get("how") == "reveal" else "open")
    return h._json({"error": err}, 400) if err else h._json({"ok": True})


def _hub_signin(h, ctx, m, body):
    from ...sharing.client import dashboard_signin
    from ...sharing.protocol import HubError

    cfg = ctx.cfg
    if str(body.get("hub") or "personal").strip().casefold() != "personal":  # another hub: signed in as its own link
        from ...application.profiles import ProfileError, find_profile
        from ...core.config import load_config

        try:
            cfg = load_config(find_profile(ctx.cfg.home, str(body["hub"])).home, create=False)
        except ProfileError as exc:
            return h._json({"error": str(exc)}, 400)
    try:
        return h._json({"url": dashboard_signin(cfg, page=body.get("next"))})
    except HubError as exc:
        return h._json({"error": tr(str(exc))}, 400)


@_as_hub
def _share(h, ctx, m, body):
    if "all_folders" in body:
        return h._json(api.devices.action_share_folders(ctx, body.get("all_folders")))
    return h._json(api.devices.action_share_mode(ctx, str(body.get("share") or "")))


@_as_hub
def _hub_project(h, ctx, m, body):
    if m.group(1) == "choices":
        return h._json(api.devices.hub_project_choices(ctx))
    return h._json(*api.devices.action_hub_project(ctx, m.group(1), _body(body)))


def _connector(h, ctx, m, body):
    try:
        return h._json(api.sources.action_connector(ctx, m.group(1), m.group(2)))
    except KeyError:
        return h._json({"error": tr("unknown connector")}, 404)


def _suggestions_seen(h, ctx, m, body):
    from ...knowledge.suggest import mark_seen

    mark_seen(ctx.conn)
    return h._json({"ok": True})


def _suggestions_refresh(h, ctx, m, body):
    from ...knowledge.suggest import refresh

    return h._json(refresh(ctx.conn, ctx.cfg))


POST: list[Route] = [
    Route("/api/sync", ANYONE, lambda h, ctx, m, b: h._json({"started": api.status.action_sync(ctx)})),
    Route(r"/api/leaderboard/(signin|finish|cancel|join|send|name|leave)", LEADERBOARD, _leaderboard),
    Route(r"/api/leaderboard/arena/(join|leave|deck)", LEADERBOARD, _arena),
    # a Google sign-in kept on this computer: only from here
    Route(r"/api/party/(signin|signout)", PARTY_SIGNIN, _party_sign),
    # the viewer's own quest log: from here, or any person signed in to a hub, not only admins (handler.PARTY_OWN)
    Route(r"/api/party/(quest|goal|hero|gate|job|cleared|pick|buy|sell|equip|use|deck)", PARTY, _party_own),
    Route(r"/api/artifacts/(\d+)/reveal", LOCAL_FILES, _reveal),
    # a password: at the hub itself, or an admin of a hub with people
    Route("/api/team-store/test", OWNER, lambda h, ctx, m, b: h._json(api.hub_admin.action_team_store_test(ctx, b))),
    Route("/api/team-store/save", OWNER, lambda h, ctx, m, b: h._json(api.hub_admin.action_team_store_save(ctx, b))),
    # a password, and where this archive goes: as for the team store
    Route("/api/mirror/test", OWNER, lambda h, ctx, m, b: h._json(api.storage.action_mirror_test(ctx, b))),
    Route("/api/mirror/save", OWNER, lambda h, ctx, m, b: h._json(api.storage.action_mirror_save(ctx, b))),
    Route("/api/mirror/sync", OWNER, lambda h, ctx, m, b: h._json({"started": api.storage.action_mirror_sync(ctx)})),
    # what leaves this computer, or its own sign-in: only from here
    Route("/api/devices/share", HERE, _share),
    Route("/api/devices/hub-signin", HERE, _hub_signin),
    # what leaves this computer, and for which projects: only from here
    Route(r"/api/devices/projects/(choices|add|remove|leave|rejoin)", HERE, _hub_project),
    Route("/api/devices/leave-hub", HERE, _as_hub(lambda h, ctx, m, b: h._json(api.devices.action_leave_hub(ctx)))),
    Route("/api/devices/rejoin-hub", HERE, _as_hub(lambda h, ctx, m, b: h._json(*api.devices.action_rejoin_hub(ctx, b)))),
    Route("/api/team/settings", ADMIN,
          lambda h, ctx, m, b: h._json(*api.hub_admin.action_hub_settings(ctx, _body(b), h.viewer))),
    # what leaves this computer (its sessions go to the team store): only from here, or a dedicated hub's admin
    Route(r"/api/projects/shared/(add|remove)", HERE_OR_HUB_ADMIN, lambda h, ctx, m, b: h._json(
        *api.devices.action_shared_project(ctx, m.group(1), _body(b), by=h.viewer, here=h._from_here()))),
    # which hub each group goes to, and joining one: what leaves this computer, only from here
    Route(r"/api/hubs/(add|join|remove|connect|choose|push|leave)", HERE,
          lambda h, ctx, m, b: h._json(*api.hubs.action_hubs(ctx, m.group(1), _body(b)))),
    Route("/api/project-groups/share", HERE,  # what leaves this computer: only from here
          lambda h, ctx, m, b: h._json(*api.projects.action_project_groups(ctx, "share", _body(b)))),
    Route(r"/api/project-groups/(save|delete|move)", ANYONE,
          lambda h, ctx, m, b: h._json(*api.projects.action_project_groups(ctx, m.group(1), _body(b)))),
    Route(r"/api/people/(add|invite|role|access|remove|revoke|shared-token)", ADMIN, lambda h, ctx, m, b: h._json(
        *api.hub_admin.action_people(ctx, m.group(1), _body(b), h.viewer, h._address()))),
    # what this hub takes from the computers that send to it
    Route("/api/team/accept", ADMIN,
          lambda h, ctx, m, b: h._json(*api.devices.action_accept(ctx, str(b.get("accept") or ""), h.viewer))),
    Route("/api/devices/push", ANYONE, _as_hub(lambda h, ctx, m, b: h._json({"started": api.devices.action_push(ctx)}))),
    # a POST: with the daily check off, the dashboard's only call to PyPI
    Route("/api/update/check", ANYONE, lambda h, ctx, m, b: h._json(api.settings.update_info(ctx, remote=True))),
    Route("/api/analysis/backend", ANYONE,
          lambda h, ctx, m, b: h._json(api.settings.action_backend(ctx, str(b.get("backend") or "")))),
    Route("/api/analysis/agent", ANYONE, lambda h, ctx, m, b: h._json(api.settings.action_agent(ctx, _body(b)))),
    # an API key, and where transcripts are sent
    Route("/api/analysis/provider", OWNER, lambda h, ctx, m, b: h._json(api.settings.action_provider(ctx, _body(b)))),
    Route("/api/analysis/provider/test", OWNER, lambda h, ctx, m, b: h._json(
        api.settings.action_provider_test(ctx, str(b.get("provider") or "")))),
    Route("/api/analysis/provider/models", OWNER, lambda h, ctx, m, b: h._json(
        api.settings.provider_models(ctx, str(b.get("provider") or "")))),
    Route("/api/analysis/language", ANYONE,
          lambda h, ctx, m, b: h._json(api.settings.action_language(ctx, str(b.get("language") or "")))),
    Route("/api/update/daily", ANYONE, lambda h, ctx, m, b: h._json(
        api.settings.action_update_setting(ctx, "check_daily", bool(b.get("on"))))),
    Route("/api/update/notify", ANYONE,
          lambda h, ctx, m, b: h._json(api.settings.action_update_setting(ctx, "notify", bool(b.get("on"))))),
    Route("/api/update", ANYONE, lambda h, ctx, m, b: h._json(api.settings.action_update(ctx))),
    # installs software and restarts the dashboard: only from this computer
    Route("/api/menu-bar", HERE, lambda h, ctx, m, b: h._json(api.settings.action_menu_bar(ctx, bool(b.get("on"))))),
    # game mode: on a hub, for everyone signed in to it
    Route("/api/game", ADMIN, lambda h, ctx, m, b: h._json(api.settings.action_game(ctx, b.get("on") is True))),
    # the Queue's Analyze now
    Route("/api/analysis/run", ANYONE,
          lambda h, ctx, m, b: h._json(api.status.action_analyze_queue(ctx, str(b.get("which") or "ready")))),
    Route("/api/sessions/analyze", ANYONE,
          lambda h, ctx, m, b: h._json(api.status.action_analyze_many(ctx, list(b.get("ids") or [])))),
    Route("/api/screen", ANYONE, lambda h, ctx, m, b: h._json(
        {"started": api.sources.action_screen(ctx, b.get("source") or None, bool(b.get("redo")))})),
    Route("/api/screen/queue", ANYONE, lambda h, ctx, m, b: h._json(
        api.sources.action_screen_queue(ctx, b.get("source") or None, bool(b.get("maybe"))))),
    Route(r"/api/sessions/([\w-]+)/analyze", ANYONE,
          lambda h, ctx, m, b: h._json({"started": api.status.action_analyze(ctx, m.group(1))})),
    Route(r"/api/connectors/([\w-]+)/(connect|disconnect)", ANYONE, _connector),
    Route("/api/group/graph/analyze", ANYONE, _group_graph_analyze),
    Route("/api/reanalyze", ANYONE, _reanalyze),
    Route("/api/reanalysis/day", ANYONE,
          lambda h, ctx, m, b: h._json(api.settings.action_weekly_day(ctx, str(b.get("day") or "")))),
    Route("/api/map/themes", ANYONE,
          lambda h, ctx, m, b: h._json({"started": api.knowledge.action_themes(ctx, bool(b.get("force")))})),
    Route("/api/glossary/rebuild", ANYONE,
          lambda h, ctx, m, b: h._json({"started": api.knowledge.action_glossary(ctx, b.get("path") or None)})),
    Route("/api/review", ANYONE,
          lambda h, ctx, m, b: h._json({"started": api.knowledge.action_review(ctx, b.get("week"))})),
    Route("/api/synthesize", ANYONE,
          lambda h, ctx, m, b: h._json({"started": api.knowledge.action_synthesize(ctx, b.get("path") or GLOBAL)})),
    Route(r"/api/knowledge/(\d+)", ANYONE,
          lambda h, ctx, m, b: h._json({"ok": api.knowledge.action_knowledge(ctx, int(m.group(1)), b)})),
    Route("/api/suggestions/seen", ANYONE, _suggestions_seen),
    Route("/api/suggestions/refresh", ANYONE, _suggestions_refresh),
    Route(r"/api/suggestions/(\d+)/(apply|unapply|retire|dismiss|done|edit|move)", ANYONE, lambda h, ctx, m, b: h._json(
        *api.knowledge.action_suggestion(ctx, int(m.group(1)), m.group(2), _body(b)))),
]
