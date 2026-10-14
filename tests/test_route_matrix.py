"""Safety-net test suite: Route matrix across HTTP endpoints and user access levels.

Guarantees that moving, refactoring, or splitting server.py (e.g. into an api/ package or route table)
cannot break route access rules or security boundaries.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from chronicle import hub, people

from test_hub import _serve

REMOTE = {"X-Forwarded-For": "203.0.113.5"}
PROJECT_A = "/Users/test/Projects/demo-app"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def _req(url: str, path: str, method: str = "GET", body: dict | None = None, headers: dict | None = None) -> tuple[int, dict[str, Any]]:
    req_headers = {"Content-Type": "application/json", "X-Interlatch": "1", **(headers or {})}
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url + path, data=data, method=method, headers=req_headers)
    try:
        with OPENER.open(req, timeout=10) as r:
            payload = r.read()
            try:
                return r.status, json.loads(payload)
            except Exception:
                return r.status, {"raw": payload}
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, {"raw": payload}


def _as_session(session_id: str, **extra):
    return {**REMOTE, "Cookie": f"interlatch_session={session_id}", **extra}


@pytest.fixture()
def matrix_env(synced, monkeypatch):
    """Hub dashboard with people (Ada=admin, Bob=member, Vic=readonly, Sam=restricted to PROJECT_A)."""
    monkeypatch.setattr(hub.IngestTrigger, "request", lambda self: None)
    cfg = synced["cfg"]
    token = hub.new_token(cfg)
    conn = synced["conn"]

    ada = people.add(conn, "Ada", "ada@example.com", "admin")
    bob = people.add(conn, "Bob", "bob@example.com", "member", by=ada)
    vic = people.add(conn, "Vic", "vic@example.com", "readonly", by=ada)
    sam = people.add(conn, "Sam", "sam@example.com", "member", by=ada, projects=[PROJECT_A])

    sessions = {
        p["name"]: people.open_browser(conn, people.invite(conn, p["id"]), "test")[1]
        for p in (ada, bob, vic, sam)
    }

    app, httpd, url = _serve(cfg)
    yield {
        "app": app,
        "httpd": httpd,
        "url": url,
        "token": token,
        "conn": conn,
        "cfg": cfg,
        "sessions": sessions,
    }
    httpd.shutdown()


def test_public_and_unauthenticated_matrix(matrix_env):
    url = matrix_env["url"]

    # Public routes allow remote anonymous access
    code, _ = _req(url, "/", method="GET", headers=REMOTE)
    assert code == 200

    code, _ = _req(url, "/signin", method="GET", headers=REMOTE)
    assert code == 400  # missing code, but reaches handler

    # Protected GET endpoints reject unauthenticated remote calls with 401
    protected_get_routes = [
        "/api/overview",
        "/api/sessions",
        "/api/knowledge",
        "/api/search",
        "/api/projects",
        "/api/team",
        "/api/team/news",
        "/api/me",
        "/api/artifacts",
        "/api/status",
    ]
    for route in protected_get_routes:
        code, body = _req(url, route, method="GET", headers=REMOTE)
        assert code == 401, f"{route} should reject remote caller with 401, got {code}"
        assert body.get("signin") is True


def test_admin_vs_member_access_matrix(matrix_env):
    url = matrix_env["url"]
    s = matrix_env["sessions"]

    admin_only_get = [
        "/api/people",
        "/api/team/settings",
        "/api/mirror",
    ]
    for route in admin_only_get:
        # Non-admin members receive 403
        for member_name in ("Bob", "Vic"):
            code, body = _req(url, route, method="GET", headers=_as_session(s[member_name]))
            assert code == 403, f"{route} should return 403 for {member_name}, got {code}"

        # Admin receives 200
        code, body = _req(url, route, method="GET", headers=_as_session(s["Ada"]))
        assert code == 200, f"{route} should return 200 for Ada, got {code}"

    # Admin-only POST endpoints
    admin_only_post = [
        ("/api/team-store/save", {"enabled": False}),
        ("/api/mirror/save", {"enabled": False}),
        ("/api/team/settings", {"name": "Hub"}),
        ("/api/suggestions/seen", {}),
    ]
    for route, payload in admin_only_post:
        # Bob / Vic get 403
        code, _ = _req(url, route, method="POST", body=payload, headers=_as_session(s["Bob"]))
        assert code == 403, f"POST {route} should return 403 for member, got {code}"

        # Ada gets 200
        code, _ = _req(url, route, method="POST", body=payload, headers=_as_session(s["Ada"]))
        assert code == 200, f"POST {route} should succeed for admin, got {code}"


def test_restricted_person_project_scope_matrix(matrix_env):
    url = matrix_env["url"]
    sam_session = matrix_env["sessions"]["Sam"]

    # Allowed endpoints for restricted person
    allowed_routes = [
        "/api/me",
        "/api/overview",
        "/api/team",
        "/api/team/who",
        "/api/team/news",
        "/api/sessions",
        "/api/projects",
        "/api/knowledge",
        "/api/learning/interests",
    ]
    for route in allowed_routes:
        code, body = _req(url, route, method="GET", headers=_as_session(sam_session))
        assert code == 200, f"{route} should be allowed for restricted Sam, got {code}"

    # Disallowed endpoints return 403 with limited=True
    disallowed_routes = [
        "/api/artifacts",
        "/api/file",
        "/api/files",
        "/api/search",
        "/api/friction",
        "/api/glossary",
        "/api/status",
        "/api/map",
    ]
    for route in disallowed_routes:
        code, body = _req(url, route, method="GET", headers=_as_session(sam_session))
        assert code == 403, f"{route} should be forbidden for restricted Sam, got {code}"
        assert body.get("limited") is True


def test_local_only_routes_matrix(matrix_env):
    url = matrix_env["url"]
    ada_session = matrix_env["sessions"]["Ada"]

    local_only_routes = [
        "/api/leaderboard",
    ]
    for route in local_only_routes:
        # Remote signed-in admin is rejected with 403 because it's not from_here
        code, _ = _req(url, route, method="GET", headers=_as_session(ada_session))
        assert code == 403, f"{route} should reject remote admin, got {code}"

        # Local caller (no proxy header, 127.0.0.1) is accepted
        code, _ = _req(url, route, method="GET")
        assert code == 200, f"{route} should accept localhost caller, got {code}"


def test_hub_spoke_token_matrix(matrix_env):
    url = matrix_env["url"]
    token = matrix_env["token"]
    spoke_body = {"machine": "11111111-2222-3333-4444-555555555555", "protocol": hub.PROTOCOL}

    # Hello endpoint with valid machine token in Authorization header
    code, body = _req(url, "/api/hub/hello", method="POST", body=spoke_body, headers={"Authorization": f"Bearer {token}"})
    assert code == 200
    assert bool(body.get("hub"))

    # Missing or invalid token is rejected with 401
    code, _ = _req(url, "/api/hub/hello", method="POST", body=spoke_body, headers={"Authorization": "Bearer invalid-token"})
    assert code == 401

    # Without any authorization header: rejected with 401
    code, _ = _req(url, "/api/hub/hello", method="POST", body=spoke_body)
    assert code == 401
