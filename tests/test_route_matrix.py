"""Safety net: the dashboard's route matrix. Every route the dashboard serves, with the status code each kind of caller
gets, so splitting the dashboard (make_handler, a route table, an api/ package) can't quietly change who may do what.

- ROUTES lists every GET and POST route under /api/ (and /signin) with its access class. test_every_served_route_has_an_
  access_decision reads the routes the handler's code (interfaces/web/handler.py) actually matches (`p == "..."`, `p in (...)`, `p in NAME`,
  `startswith` and `re.fullmatch` patterns, alternations expanded) and fails when one is missing from ROUTES: a new
  route has to say who may call it. A route in ROUTES that the handler no longer serves fails too.
- The matrix tests send one representative request per route and caller and compare the whole matrix at once, so the
  assertion shows every cell that changed. Callers: anyone remote and signed out, a read-only person, a member, a
  member limited to one project, an admin person, the hub computer itself (localhost); on /api/hub/* also the hub's
  shared token and the computer tokens of a member and of a read-only person.
- Request bodies are minimal: this pins access, not behaviour. Actions that would reach outside the temporary home
  (start analysis or sync, call PyPI, the leaderboard, a database, open Finder, install the menu bar) are stubbed on
  the App, after the handler's own checks; a refused request must never reach one (checked in every matrix test).
"""

from __future__ import annotations

import ast
import itertools
import json
import re
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from interlatch.sharing import client, people
from interlatch.sharing.identity import new_token
from interlatch.sharing.protocol import PROTOCOL
from interlatch.sharing.receiver import IngestTrigger
from interlatch.interfaces.web import api
from interlatch.interfaces.web import handler as handler_mod
from interlatch.interfaces.web import routes as route_table
from interlatch.interfaces.web.handler import session_cookie_name

from conftest import CWD, SID
from test_hub import _serve

pytestmark = pytest.mark.usefixtures("game_on")  # Innerlatch: game mode is off by default

REMOTE = {"X-Forwarded-For": "203.0.113.5"}  # not this computer: test requests all come from 127.0.0.1
LIMITED_TO = CWD  # Sam, a member who sees only this project
SHARED_PC = "aaaaaaaa-0000-4000-8000-000000000001"  # a computer sending with the hub's shared token
BOB_PC = "bbbbbbbb-0000-4000-8000-000000000002"
VIC_PC = "cccccccc-0000-4000-8000-000000000003"

BROWSER = ("anon", "readonly", "member", "limited", "admin", "here")
HUB_CALLERS = (*BROWSER, "shared", "member_pc", "readonly_pc")


class Access:
    """Expected status per caller. `ok(code)`: allowed callers get `code` (a minimal body is refused as incomplete);
    `but(kind=code)`: one caller differs."""

    def __init__(self, name: str, codes: dict[str, int]):
        self.name, self.codes = name, codes

    def ok(self, code: int) -> Access:
        return Access(self.name, {k: code if v == 200 else v for k, v in self.codes.items()})

    def but(self, **codes: int) -> Access:
        return Access(self.name, {**self.codes, **codes})


def _row(anon, readonly, member, limited, admin, here) -> dict[str, int]:
    return dict(zip(BROWSER, (anon, readonly, member, limited, admin, here), strict=True))


# ---- GET: who may look
ANYONE = Access("anyone", _row(200, 200, 200, 200, 200, 200))  # no sign-in needed (the sign-in link itself)
LIMITED_OK = Access("signed in, limited people too", _row(401, 200, 200, 200, 200, 200))  # access.LIMITED_GET
SIGNED_IN = Access("signed in, not limited", _row(401, 200, 200, 403, 200, 200))
ADMIN = Access("admins", _row(401, 403, 403, 403, 200, 200))
HERE = Access("the hub computer only", _row(401, 403, 403, 403, 403, 200))
# ---- POST: who may change things (any non-admin person is refused before the route is looked at)
POST_ANYONE = Access("anyone", _row(200, 200, 200, 200, 200, 200))
POST_SIGNED_IN = Access("signed in", _row(401, 200, 200, 200, 200, 200))
POST_ADMIN = Access("admins", _row(401, 403, 403, 403, 200, 200))
POST_HERE = Access("the hub computer only", _row(401, 403, 403, 403, 403, 200))
# ---- /api/hub/*: a computer's token, never a browser session
HUB_JOIN = Access("an invite code", dict.fromkeys(HUB_CALLERS, 200))
HUB_READ = Access("any computer token", {**dict.fromkeys(BROWSER, 401), "shared": 200, "member_pc": 200, "readonly_pc": 200})
HUB_PUSH = Access("a computer that may send", {**dict.fromkeys(BROWSER, 401), "shared": 200, "member_pc": 200,
                                               "readonly_pc": 403})

# Every route the dashboard serves, its access class and the status allowed callers get for the representative
# request in REQUESTS. {id} and {n} are path parameters.
ROUTES: dict[tuple[str, str], Access] = {
    ("GET", "/signin"): ANYONE.ok(400),  # an unknown code: refused for everyone alike
    ("POST", "/signin"): POST_ANYONE.ok(403),  # no Origin: another site's form, refused for everyone alike
    # signed in, also people limited to projects (access.LIMITED_GET and the session page)
    ("GET", "/api/me"): LIMITED_OK,
    ("GET", "/api/overview"): LIMITED_OK,
    ("GET", "/api/team"): LIMITED_OK,
    ("GET", "/api/team/who"): LIMITED_OK,
    ("GET", "/api/team/news"): LIMITED_OK,
    ("GET", "/api/sessions"): LIMITED_OK,
    ("GET", "/api/sessions/{id}"): LIMITED_OK.but(limited=404),  # not analyzed: hidden from a limited person
    ("GET", "/api/projects"): LIMITED_OK,
    ("GET", "/api/project"): LIMITED_OK.but(limited=404),  # nothing analyzed in it: nothing for a limited person
    ("GET", "/api/knowledge"): LIMITED_OK,
    ("GET", "/api/learning/interests"): LIMITED_OK,
    ("GET", "/api/jobs"): LIMITED_OK,
    ("GET", "/api/diagram"): LIMITED_OK.ok(404),  # no sketch for the project yet
    # signed in, not limited
    ("GET", "/api/export"): SIGNED_IN,
    ("GET", "/api/sessions/{id}/events"): SIGNED_IN,
    ("GET", "/api/sessions/{id}/matches"): SIGNED_IN,
    ("GET", "/api/project-groups"): SIGNED_IN,
    ("GET", "/api/group"): SIGNED_IN.ok(404),  # no such group
    ("GET", "/api/group/map"): SIGNED_IN.ok(404),
    ("GET", "/api/group/map.excalidraw"): SIGNED_IN.ok(404),
    ("GET", "/api/group/graph"): SIGNED_IN.ok(404),
    ("GET", "/api/reanalysis"): ADMIN,
    ("GET", "/api/artifacts"): SIGNED_IN,
    ("GET", "/api/artifacts/{n}/open"): SIGNED_IN.ok(404),
    ("GET", "/api/artifacts/{n}/file"): SIGNED_IN.ok(404),
    ("GET", "/api/file"): SIGNED_IN,
    ("GET", "/api/files"): SIGNED_IN,
    ("GET", "/api/learning/glance"): SIGNED_IN,
    ("GET", "/api/knowledge/hub"): SIGNED_IN,
    ("GET", "/api/search"): SIGNED_IN,
    ("GET", "/api/status"): SIGNED_IN,
    ("GET", "/api/analysis/queue"): SIGNED_IN,
    ("GET", "/api/reviews"): SIGNED_IN,
    ("GET", "/api/glossary"): SIGNED_IN,
    ("GET", "/api/glossary/terms"): SIGNED_IN,
    ("GET", "/api/connectors"): SIGNED_IN,
    ("GET", "/api/mcp"): SIGNED_IN,
    ("GET", "/api/mcp-clients"): SIGNED_IN,
    ("GET", "/api/map"): SIGNED_IN,
    ("GET", "/api/systems"): SIGNED_IN,
    ("GET", "/api/system"): SIGNED_IN.ok(404),
    ("GET", "/api/update"): SIGNED_IN,
    ("GET", "/api/feedback"): SIGNED_IN,
    ("GET", "/api/imports"): SIGNED_IN,
    ("GET", "/api/devices"): SIGNED_IN,  # non-admins get it trimmed: test_devices_is_trimmed_for_non_admins
    ("GET", "/api/hubs"): HERE,
    ("GET", "/api/team-store"): ADMIN,  # admin-only since #156; the matrix found it open
    ("GET", "/api/suggestions"): SIGNED_IN,
    ("GET", "/api/suggestions/unseen"): SIGNED_IN,
    ("GET", "/api/suggestions/{n}/preview"): SIGNED_IN.ok(404),
    ("GET", "/api/friction"): SIGNED_IN,
    # admins
    ("GET", "/api/people"): ADMIN,
    ("GET", "/api/mirror"): ADMIN,
    ("GET", "/api/mirror/status"): ADMIN,
    ("GET", "/api/team/settings"): ADMIN,
    ("GET", "/api/projects/shared"): ADMIN,
    # the computer itself: a person's own leaderboard
    ("GET", "/api/leaderboard"): HERE,
    ("GET", "/api/leaderboard/avatar/{id}"): HERE.ok(404),  # no picture
    ("GET", "/api/leaderboard/arena/battle"): HERE,
    # the Party: this computer, or any person signed in to the hub, limited people too (their projects' lessons);
    # behind a Google sign-in here, which the matrix's hub hasn't made: it answers only that it's locked
    ("GET", "/api/party"): LIMITED_OK,
    ("GET", "/api/party/skill"): LIMITED_OK.ok(404).but(here=403),  # no such module; here: not signed in
    ("GET", "/api/party/track"): LIMITED_OK.ok(404).but(here=403),  # no such track; here: not signed in
    ("GET", "/api/party/tracks"): LIMITED_OK,  # the catalog's own tracks, for picking a goal
    ("GET", "/api/party/map"): LIMITED_OK.but(here=403),  # the Adventure's map; here: not signed in
    ("GET", "/api/party/encounter"): LIMITED_OK.ok(404).but(here=403),  # no such lesson; here: not signed in
    ("GET", "/api/party/ask"): LIMITED_OK.ok(404).but(here=403),  # no such lesson; here: not signed in
    ("GET", "/api/party/gate"): LIMITED_OK.ok(403),  # gates are taken on your own computer; here: not signed in
    ("GET", "/api/party/jobs"): LIMITED_OK.ok(403),  # job goals are kept on your own computer; here: not signed in
    ("GET", "/api/party/interview"): LIMITED_OK.ok(403),  # interviews are taken on your own computer; here: not signed in
    ("GET", "/api/party/prep"): LIMITED_OK.ok(403),  # job monsters are fought on your own computer; here: not signed in
    ("GET", "/api/party/bag"): LIMITED_OK.but(here=403),  # the viewer's own bag; here: not signed in
    ("GET", "/api/party/shop"): LIMITED_OK.ok(404).but(here=403),  # no such region; here: not signed in
    # POST: anyone, and any signed-in person (their own news)
    ("POST", "/api/signout"): POST_ANYONE,
    ("POST", "/api/team/news/seen"): POST_SIGNED_IN,
    # POST: any signed-in person, for their own Party quest log (no such quest or track; here: not signed in)
    ("POST", "/api/party/quest"): POST_SIGNED_IN.ok(400).but(here=403),
    ("POST", "/api/party/goal"): POST_SIGNED_IN.ok(400).but(here=403),
    ("POST", "/api/party/hero"): POST_SIGNED_IN.ok(403),  # a hub person has no hero there; here: not signed in
    ("POST", "/api/party/gate"): POST_SIGNED_IN.ok(403),  # gates are taken on your own computer; here: not signed in
    ("POST", "/api/party/job"): POST_SIGNED_IN.ok(403),  # job goals are kept on your own computer; here: not signed in
    ("POST", "/api/party/interview"): POST_SIGNED_IN.ok(403),  # interviews are taken on your own computer
    ("POST", "/api/party/prep"): POST_SIGNED_IN.ok(403),  # job monsters are fought on your own computer
    ("POST", "/api/party/cleared"): POST_SIGNED_IN.ok(400).but(here=403),  # no such lesson
    ("POST", "/api/party/pick"): POST_SIGNED_IN.ok(400).but(here=403),  # no such lesson
    ("POST", "/api/party/buy"): POST_SIGNED_IN.ok(400).but(here=403),  # no such item
    ("POST", "/api/party/sell"): POST_SIGNED_IN.ok(400).but(here=403),
    ("POST", "/api/party/equip"): POST_SIGNED_IN.ok(400).but(here=403),
    ("POST", "/api/party/use"): POST_SIGNED_IN.ok(400).but(here=403),
    ("POST", "/api/party/deck"): POST_SIGNED_IN.ok(400).but(here=403),  # no such deck
    # POST: admins (the hub computer, or an admin person)
    ("POST", "/api/import"): POST_ADMIN,
    ("POST", "/api/sync"): POST_ADMIN,
    ("POST", "/api/team-store/test"): POST_ADMIN,
    ("POST", "/api/team-store/save"): POST_ADMIN,
    ("POST", "/api/mirror/test"): POST_ADMIN,
    ("POST", "/api/mirror/save"): POST_ADMIN,
    ("POST", "/api/mirror/sync"): POST_ADMIN,
    ("POST", "/api/team/settings"): POST_ADMIN,
    ("POST", "/api/team/accept"): POST_ADMIN.ok(400),  # no such setting
    ("POST", "/api/projects/shared/add"): POST_HERE.ok(400),  # admins too on a dedicated hub (test_dedicated_hub.py)
    ("POST", "/api/projects/shared/remove"): POST_HERE.ok(400),
    ("POST", "/api/project-groups/save"): POST_ADMIN.ok(400),
    ("POST", "/api/project-groups/delete"): POST_ADMIN,
    ("POST", "/api/project-groups/move"): POST_ADMIN.ok(400),
    ("POST", "/api/people/add"): POST_ADMIN.ok(400),
    ("POST", "/api/people/invite"): POST_ADMIN.ok(400),  # no such person
    ("POST", "/api/people/role"): POST_ADMIN.ok(400),  # no such person
    ("POST", "/api/people/access"): POST_ADMIN.ok(400),  # no such person
    ("POST", "/api/people/remove"): POST_ADMIN.ok(400),  # no such person
    ("POST", "/api/people/revoke"): POST_ADMIN.ok(400),  # no such person
    ("POST", "/api/people/shared-token"): POST_ADMIN,
    ("POST", "/api/devices/push"): POST_ADMIN,
    ("POST", "/api/update/check"): POST_ADMIN,
    ("POST", "/api/update/daily"): POST_ADMIN,
    ("POST", "/api/update/notify"): POST_ADMIN,
    ("POST", "/api/update"): POST_ADMIN,
    ("POST", "/api/analysis/backend"): POST_ADMIN,
    ("POST", "/api/analysis/agent"): POST_ADMIN,
    ("POST", "/api/analysis/provider"): POST_ADMIN,
    ("POST", "/api/analysis/provider/test"): POST_ADMIN,
    ("POST", "/api/analysis/provider/models"): POST_ADMIN,
    ("POST", "/api/analysis/language"): POST_ADMIN,
    ("POST", "/api/analysis/run"): POST_ADMIN,
    ("POST", "/api/sessions/analyze"): POST_ADMIN,
    ("POST", "/api/sessions/{id}/analyze"): POST_ADMIN,
    ("POST", "/api/screen"): POST_ADMIN,
    ("POST", "/api/screen/queue"): POST_ADMIN,
    ("POST", "/api/connectors/{id}/connect"): POST_ADMIN,
    ("POST", "/api/connectors/{id}/disconnect"): POST_ADMIN,
    ("POST", "/api/group/graph/analyze"): POST_ADMIN.ok(404),  # no such group
    ("POST", "/api/reanalyze"): POST_ADMIN.ok(404),  # no such group
    ("POST", "/api/reanalysis/day"): POST_ADMIN,
    ("POST", "/api/map/themes"): POST_ADMIN,
    ("POST", "/api/glossary/rebuild"): POST_ADMIN,
    ("POST", "/api/review"): POST_ADMIN,
    ("POST", "/api/synthesize"): POST_ADMIN,
    ("POST", "/api/knowledge/{n}"): POST_ADMIN,
    ("POST", "/api/suggestions/seen"): POST_ADMIN,
    ("POST", "/api/suggestions/refresh"): POST_ADMIN,
    ("POST", "/api/suggestions/{n}/apply"): POST_ADMIN.ok(404),
    ("POST", "/api/suggestions/{n}/unapply"): POST_ADMIN.ok(404),
    ("POST", "/api/suggestions/{n}/retire"): POST_ADMIN.ok(404),
    ("POST", "/api/suggestions/{n}/dismiss"): POST_ADMIN.ok(404),
    ("POST", "/api/suggestions/{n}/done"): POST_ADMIN.ok(404),
    ("POST", "/api/suggestions/{n}/edit"): POST_ADMIN.ok(404),
    ("POST", "/api/suggestions/{n}/move"): POST_ADMIN.ok(404),
    # POST: only the computer itself (what leaves it, its own sign-ins, software it installs, its files)
    ("POST", "/api/project-groups/share"): POST_HERE.ok(400),
    **{("POST", f"/api/hubs/{verb}"): POST_HERE.ok(409) for verb in ("add", "join", "remove", "connect", "choose", "push", "leave")},
    ("POST", "/api/devices/share"): POST_HERE,
    ("POST", "/api/devices/hub-signin"): POST_HERE,
    ("POST", "/api/devices/leave-hub"): POST_HERE,
    ("POST", "/api/devices/rejoin-hub"): POST_HERE.ok(400),
    ("POST", "/api/devices/projects/choices"): POST_HERE,
    ("POST", "/api/devices/projects/add"): POST_HERE,
    ("POST", "/api/devices/projects/remove"): POST_HERE,
    ("POST", "/api/devices/projects/leave"): POST_HERE,
    ("POST", "/api/devices/projects/rejoin"): POST_HERE,
    ("POST", "/api/menu-bar"): POST_HERE,
    ("POST", "/api/game"): POST_ADMIN,
    ("POST", "/api/artifacts/{n}/reveal"): POST_HERE,
    ("POST", "/api/leaderboard/signin"): POST_HERE,
    ("POST", "/api/leaderboard/finish"): POST_HERE,
    ("POST", "/api/leaderboard/cancel"): POST_HERE,
    ("POST", "/api/leaderboard/send"): POST_HERE,
    ("POST", "/api/leaderboard/name"): POST_HERE,
    ("POST", "/api/leaderboard/leave"): POST_HERE,
    ("POST", "/api/leaderboard/join"): POST_HERE,
    ("POST", "/api/leaderboard/arena/join"): POST_HERE,
    ("POST", "/api/leaderboard/arena/leave"): POST_HERE,
    ("POST", "/api/leaderboard/arena/deck"): POST_HERE,
    ("POST", "/api/party/signin"): POST_HERE,
    ("POST", "/api/party/signout"): POST_HERE,
    # the hub API: computers, by token
    ("POST", "/api/hub/join"): HUB_JOIN.ok(400),  # an unknown invite code
    ("POST", "/api/hub/signin"): HUB_READ.but(shared=400),  # a computer that is nobody's: no sign-in
    ("POST", "/api/hub/lessons"): HUB_READ,
    ("POST", "/api/hub/hello"): HUB_PUSH,
    ("POST", "/api/hub/done"): HUB_PUSH,
    ("POST", "/api/hub/withdraw"): HUB_PUSH,
    ("POST", "/api/hub/file"): HUB_PUSH.ok(400),  # nothing sent
    ("POST", "/api/hub/sessions"): HUB_PUSH.ok(400),
    ("POST", "/api/hub/analyses"): HUB_PUSH.ok(400),
}

# Routes whose access today looks wrong (fails open): ROUTES holds the secure expectation, and test_suspected_fail_open
# runs them as strict xfails, so a fix turns them into passes that must move back into the main matrix.
FAIL_OPEN: dict[tuple[str, str], str] = {}  # none today: GET /api/team-store was one, fixed in #156

# the representative request: a query string for a GET, a body for a POST (default {}), and path parameters
QUERY = {
    "/signin": "?code=AAAA-BBBB-CCCC",
    "/api/project": f"?path={CWD}",
    "/api/diagram": f"?path={CWD}",
    "/api/group": "?id=999999",
    "/api/group/map": "?id=999999",
    "/api/group/map.excalidraw": "?id=999999",
    "/api/group/graph": "?id=999999",
    "/api/file": f"?path={CWD}/auth.py",
    "/api/files": f"?root={CWD}",
    "/api/export": f"?ids={SID}&format=md&check=1",
    "/api/search": "?q=login",
    "/api/system": "?id=nothing",
    "/api/party/skill": "?agent=claude&module=M-nothing",
    "/api/party/track": "?id=nothing",
    "/api/party/encounter": "?id=999999",
    "/api/party/shop": "?region=nowhere",
}
BODY = {
    "/api/group/graph/analyze": {"id": 999999},
    "/api/reanalyze": {"kind": "group", "target": 999999, "on": True},
    "/api/team-store/save": {"enabled": False},
    "/api/mirror/save": {"enabled": False},
    "/api/team/settings": {"name": "Matrix hub"},
    "/api/people/invite": {"id": 999999},
    "/api/people/role": {"id": 999999, "role": "member"},
    "/api/people/access": {"id": 999999, "projects": "all"},
    "/api/people/remove": {"id": 999999},
    "/api/people/revoke": {"id": 999999},
    "/api/people/shared-token": {"on": True},
    "/api/update/daily": {"on": False},
    "/api/update/notify": {"on": False},
    "/api/party/goal": {"track": "no-such-track"},
    "/api/party/hero": {"name": "", "look": {}},
    "/api/party/gate": {"module": "M-python", "artifacts": [], "text": ""},
    "/api/party/job": {"action": "activate", "id": None},
    "/api/party/interview": {"action": "start"},
    "/api/party/prep": {"action": "pick", "id": "p:x:M-python:0", "choice": 0},
    "/api/party/cleared": {"id": 999999, "cleared": True},
    "/api/party/pick": {"id": 999999, "choice": 0},
    "/api/party/buy": {"id": "c:nothing"},
    "/api/party/sell": {"id": "c:nothing"},
    "/api/party/equip": {"agent": "claude", "slot": "weapon", "id": "g:nothing"},
    "/api/party/use": {"id": "c:nothing"},
    "/api/party/deck": {"id": "d99", "name": "x"},
}
PARAMS = {"{id}": SID, "{n}": "999999"}
PARAMS_FOR = {"/api/connectors/{id}/connect": {"{id}": "claude"}, "/api/connectors/{id}/disconnect": {"{id}": "claude"},
              "/api/leaderboard/avatar/{id}": {"{id}": "nobody"}}

# Dashboard api functions that would reach outside the temporary home (processes, network, the desktop): stubbed,
# recording calls
STUBS = {
    "action_sync": False, "action_mirror_sync": False, "action_screen": False, "action_analyze": False,
    "action_themes": False, "action_glossary": False, "action_review": False, "action_synthesize": False,
    "action_import": False, "action_push": False, "action_leaderboard_cancel": None,
    "action_leaderboard_signin": {"stub": True}, "action_leaderboard_finish": {"stub": True},
    "action_leaderboard_send": {"stub": True}, "action_leaderboard_name": {"stub": True},
    "action_leaderboard_leave": {"stub": True}, "leaderboard": {"stub": True}, "action_leaderboard_join": {"stub": True},
    "action_party_signout": {"stub": True}, "action_arena": {"stub": True}, "action_arena_deck": {"stub": True}, "arena_battle": ({"stub": True}, 200),
    "action_team_store_test": {"stub": True}, "action_mirror_test": {"stub": True},
    "action_update": {"stub": True}, "action_menu_bar": {"stub": True},
    "action_provider_test": {"stub": True}, "provider_models": {"stub": True},
    "action_analyze_queue": {"stub": True}, "action_analyze_many": {"stub": True},
    "action_screen_queue": {"stub": True}, "action_connector": {"stub": True},
    "hub_project_choices": {"stub": True}, "action_hub_project": ({"stub": True}, 200),
    "action_leave_hub": {"stub": True}, "action_share_mode": {"stub": True}, "action_share_folders": {"stub": True},
    "artifact_reveal": None,
}


def _api_module(name: str):
    """The api module that defines `name`, where the handler looks it up (api.<feature>.<name>)."""
    return next(mod for mod in vars(api).values() if isinstance(mod, types.ModuleType)
                and getattr(getattr(mod, name, None), "__module__", None) == mod.__name__)


# ------------------------------------------------------------------ the routes the handler serves
def _expand(pattern: str) -> list[str]:
    """A re.fullmatch route as the paths it matches: alternations expanded, parameters as {id} and {n}."""
    path = pattern.replace(r"([\w-]+)", "{id}").replace(r"(\d+)", "{n}")
    alts = re.findall(r"\(([\w|-]+)\)", path)
    template = re.sub(r"\(([\w|-]+)\)", "\0", path)
    assert not re.search(r"[()\[\]\\*+?^$]", template), f"teach _expand this route pattern: {pattern}"
    out = []
    for choice in itertools.product(*(a.split("|") for a in alts)):
        parts = template.split("\0")
        out.append(parts[0] + "".join(c + rest for c, rest in zip(choice, parts[1:], strict=True)))
    return out


def _is_path(node: ast.AST) -> bool:
    """`p`, or `urlparse(self.path).path`: what the handler compares routes against."""
    return (isinstance(node, ast.Name) and node.id == "p") or (isinstance(node, ast.Attribute) and node.attr == "path"
                                                               and isinstance(node.value, ast.Call))


def served_routes() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """(routes, prefixes) the dashboard serves: its route table (routes.py), and what the handler matches itself (the
    page, signing in and out, the hub API), read from its syntax tree."""
    tree = ast.parse(Path(handler_mod.__file__).read_text(encoding="utf-8"))
    consts = {t.id: node.value for node in tree.body if isinstance(node, ast.Assign)
              for t in node.targets if isinstance(t, ast.Name)}
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Handler")
    methods = {"_get": "GET", "_post": "POST", "_hub_api": "POST"}
    routes, prefixes = set(), set()
    for fn in handler.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name not in methods:
            continue
        verb = methods[fn.name]
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare) and _is_path(node.left):
                for op, right in zip(node.ops, node.comparators, strict=True):
                    if isinstance(right, ast.Name) and right.id in consts:
                        right = consts[right.id]
                    if isinstance(op, ast.Eq) and isinstance(right, ast.Constant):
                        routes.add((verb, right.value))
                    elif isinstance(op, ast.In) and isinstance(right, (ast.Tuple, ast.List, ast.Set)):
                        routes |= {(verb, e.value) for e in right.elts if isinstance(e, ast.Constant)}
                    else:
                        raise AssertionError(f"teach served_routes this comparison: {ast.unparse(node)}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "startswith" and _is_path(node.func.value):
                    prefixes.add((verb, node.args[0].value))
                elif (node.func.attr == "fullmatch" and isinstance(node.func.value, ast.Name)
                      and node.func.value.id == "re" and len(node.args) == 2 and _is_path(node.args[1])):
                    routes |= {(verb, r) for r in _expand(node.args[0].value)}
    for verb, table in (("GET", route_table.GET), ("POST", route_table.POST)):
        for route in table:
            if route.prefix:
                prefixes.add((verb, route.path))
            elif "(" in route.path:
                routes |= {(verb, r) for r in _expand(route.path)}
            else:
                routes.add((verb, route.path))
    return routes, prefixes


def test_every_served_route_has_an_access_decision():
    routes, prefixes = served_routes()
    assert {("GET", "/api/mirror"), ("GET", "/api/mirror/status"), ("GET", "/api/devices"), ("POST", "/api/signout"),
            ("GET", "/api/sessions/{id}/events"), ("POST", "/api/people/shared-token"), ("POST", "/api/hub/withdraw"),
            ("POST", "/api/hub/join"), ("POST", "/api/leaderboard/leave")} <= routes  # the reader still reads
    missing = sorted(routes - set(ROUTES))
    assert not missing, f"served but not in ROUTES (decide who may call each): {missing}"
    stale = sorted(set(ROUTES) - routes)
    assert not stale, f"in ROUTES but no longer served: {stale}"
    for verb, prefix in prefixes:  # a prefix the handler matches leads to routes in the table
        assert prefix == "/api/" or any(v == verb and r.startswith(prefix) for v, r in ROUTES), (verb, prefix)


# ------------------------------------------------------------------ calling them
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def _request(url: str, verb: str, path: str, body: dict | None, headers: dict) -> tuple[int, bytes]:
    data = json.dumps(body if body is not None else {}).encode() if verb == "POST" else None
    req = urllib.request.Request(url + path, data=data, method=verb,
                                 headers={"Content-Type": "application/json", "X-Interlatch": "1", **headers})
    try:
        with OPENER.open(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class _NoWaiting:
    """Unknown codes never make this address wait here (people.CodeAttempts has its own tests): the matrix sends
    several, all from 127.0.0.1."""

    def wait(self, who):
        return 0

    def wrong(self, who):
        pass


@pytest.fixture()
def matrix(synced, monkeypatch):
    """A hub with people (Ada admin, Bob member, Vic read-only, Sam a member limited to one project), each signed in in
    a browser; Bob's and Vic's computers joined with their own tokens."""
    monkeypatch.setattr(IngestTrigger, "request", lambda self: None)
    monkeypatch.setattr(client, "dashboard_signin", lambda *a, **k: "http://127.0.0.1/stub")
    monkeypatch.setattr("interlatch.knowledge.suggest.refresh", lambda *a, **k: {"stub": True})
    monkeypatch.setattr("interlatch.sharing.leaderboard.avatar", lambda *a, **k: None)
    cfg, conn = synced["cfg"], synced["conn"]
    shared = new_token(cfg)
    ada = people.add(conn, "Ada", "ada@example.com", "admin")
    bob = people.add(conn, "Bob", "bob@example.com", "member", by=ada)
    vic = people.add(conn, "Vic", "vic@example.com", "member", by=ada)  # read-only once their computer joined
    sam = people.add(conn, "Sam", "sam@example.com", "member", by=ada, projects=[LIMITED_TO])
    browser = {kind: people.open_browser(conn, people.invite(conn, who["id"]), "test")[1]
               for kind, who in (("admin", ada), ("member", bob), ("readonly", vic), ("limited", sam))}
    app, httpd, url = _serve(cfg)
    app.code_attempts = _NoWaiting()
    reached: list[str] = []

    def stub(name, value):
        def fn(*args, **kwargs):
            reached.append(name)
            return value
        return fn

    for name, value in STUBS.items():
        monkeypatch.setattr(_api_module(name), name, stub(name, value))
    real_update_info = api.settings.update_info
    monkeypatch.setattr(api.settings, "update_info", lambda ctx, remote: stub("update_info(remote)", {"stub": True})()
                        if remote else real_update_info(ctx, remote))

    tokens = {}
    for kind, who, machine in (("member_pc", bob, BOB_PC), ("readonly_pc", vic, VIC_PC)):
        status, body = _request(url, "POST", "/api/hub/join", {"code": people.invite(conn, who["id"]),
                                                               "machine": machine, "name": kind}, {})
        assert status == 200, body
        tokens[kind] = json.loads(body)["token"]
    people.set_role(conn, vic["id"], "readonly")

    cookie = session_cookie_name(conn, https=False, create=True)  # the hub's own session cookie name

    def headers(kind: str) -> dict:
        if kind == "here":
            return {}
        if kind == "anon":
            return dict(REMOTE)
        if kind in browser:
            return {**REMOTE, "Cookie": f"{cookie}={browser[kind]}"}
        return {**REMOTE, "Authorization": f"Bearer {shared if kind == 'shared' else tokens[kind]}"}

    machines = {"shared": SHARED_PC, "member_pc": BOB_PC, "readonly_pc": VIC_PC}
    yield {"url": url, "app": app, "cfg": cfg, "conn": conn, "headers": headers, "reached": reached,
           "machines": machines, "browser": browser}
    httpd.shutdown()


def _path(route: str, kind: str, machines: dict) -> str:
    path = route
    for k, v in {**PARAMS, **PARAMS_FOR.get(route, {})}.items():
        path = path.replace(k, v)
    if route.startswith("/api/hub/"):
        return f"{path}?machine={machines.get(kind, SHARED_PC)}"
    return path + QUERY.get(route, "")


def _body(route: str, kind: str, machines: dict) -> dict:
    if route.startswith("/api/hub/"):
        return {"protocol": PROTOCOL, "machine": machines.get(kind, SHARED_PC), "name": "matrix", "code": "AAAA-BBBB-CCCC"}
    return BODY.get(route, {})


def _run(env, verb: str, routes: list[str]) -> tuple[dict, dict, list]:
    """(actual, expected, refused-but-reached) for these routes, every caller kind its Access names."""
    actual, expected, leaks = {}, {}, []
    for route in routes:
        access = ROUTES[(verb, route)]
        for kind, want in access.codes.items():
            env["reached"].clear()
            status, _ = _request(env["url"], verb, _path(route, kind, env["machines"]),
                                 _body(route, kind, env["machines"]) if verb == "POST" else None, env["headers"](kind))
            actual[f"{verb} {route} [{kind}]"] = status
            expected[f"{verb} {route} [{kind}]"] = want
            if status in (401, 403) and env["reached"]:
                leaks.append((verb, route, kind, list(env["reached"])))
    return actual, expected, leaks


def _routes(verb: str, *, hub_api: bool = False) -> list[str]:
    last = ["/api/signout"]  # signs the browser sessions out: called last
    rs = [r for v, r in ROUTES if v == verb and r.startswith("/api/hub/") == hub_api and r not in last
          and (v, r) not in FAIL_OPEN]
    return rs + [r for r in last if (verb, r) in ROUTES and not hub_api]


def _check(actual: dict, expected: dict, leaks: list) -> None:
    changed = {cell: f"expected {expected[cell]}, got {actual[cell]}" for cell in expected if actual[cell] != expected[cell]}
    assert not changed, "route matrix changed:\n" + "\n".join(f"  {c}: {w}" for c, w in changed.items())
    assert not leaks, f"refused, yet the action ran: {leaks}"


def test_get_matrix(matrix):
    _check(*_run(matrix, "GET", _routes("GET")))


def test_post_matrix(matrix):
    _check(*_run(matrix, "POST", _routes("POST")))


def test_hub_api_matrix(matrix):
    _check(*_run(matrix, "POST", _routes("POST", hub_api=True)))


@pytest.mark.parametrize("route", [pytest.param(r, marks=pytest.mark.xfail(strict=True, reason=why), id=f"{r[0]} {r[1]}")
                                   for r, why in sorted(FAIL_OPEN.items())])
def test_suspected_fail_open(matrix, route):
    """ROUTES' secure expectation for a route that fails open today: fails (xfail) until the handler is fixed."""
    _check(*_run(matrix, route[0], [route[1]]))


TRIMMED = {"machines": [], "store": None, "allowed_users": [], "allowed_hosts": [], "path_map": {}, "folders": [],
           "projects": [], "team": None, "last_push": None}


def test_devices_is_trimmed_for_non_admins(matrix):
    """Signed-in people who aren't admins get /api/devices with the hub's computers, keys, team store and sign-ins
    emptied, and this computer by name only (#156); admins and the hub computer get all of it."""
    url, headers = matrix["url"], matrix["headers"]
    for kind in ("readonly", "member"):
        status, body = _request(url, "GET", "/api/devices", None, headers(kind))
        dv = json.loads(body)
        assert status == 200 and not dv["can_admin"] and dv["role"] == "hub", kind
        assert {k: dv[k] for k in TRIMMED} == TRIMMED, kind
        assert dv["this"] == {"name": dv["this"]["name"]}, kind
    for kind in ("admin", "here"):
        status, body = _request(url, "GET", "/api/devices", None, headers(kind))
        dv = json.loads(body)
        assert status == 200 and dv["can_admin"], kind
        assert len(dv["machines"]) >= 2 and dv["store"] is not None and "id" in dv["this"], kind
        assert all("key_hash" in m for m in dv["machines"]), kind
