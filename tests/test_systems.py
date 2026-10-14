"""Systems map: systems and groups from project folders, parts from manifests and commands, links between systems,
each with its evidence. Built against a fake home with two repos, a worktree and a folder that holds both."""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from interlatch.projects import systems
from interlatch.storage.sqlite import connect


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture()
def world(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    acme = home / "Projects" / "Work" / "Acme"
    shop, worker = acme / "shop", acme / "worker"
    write(shop / ".git" / "config", '[core]\n\tbare = false\n[remote "origin"]\n\turl = https://me:tok3n@github.com/acme/shop.git\n')
    write(shop / "frontend" / "package.json", json.dumps({"name": "shop-web", "dependencies": {"react": "19"}, "devDependencies": {"vite": "7", "typescript": "5"}}))
    write(shop / "frontend" / "vite.config.ts", "export default { server: { port: 5173, proxy: {\n  '/api': {\n    target: process.env.API ?? 'http://127.0.0.1:8011',\n  },\n} } }\n")
    write(shop / "backend" / "pyproject.toml", '[project]\nname = "shop-api"\ndependencies = ["fastapi>=0.110", "openai", "psycopg[binary]"]\n')
    write(shop / "docker-compose.yml", "services:\n  db:\n    image: postgres:16  # the dev database\n    ports:\n      - \"5433:5432\"\n"
                                        "  api:\n    build:\n      context: .\n      dockerfile: backend/Dockerfile\n    environment:\n"
                                        "      DATABASE_URL: postgresql://db:5432/shop\n    depends_on:\n      db:\n        condition: service_healthy\n")
    write(shop / "backend" / "Dockerfile", "FROM python:3.13\nEXPOSE 8011\n")
    write(shop / "infra" / "main.tf", 'resource "azurerm_resource_group" "rg" {\n  name = "rg-shop"\n}\n'
                                      'resource "azurerm_linux_web_app" "app" {\n  name = "shop-app"\n}\n'
                                      'resource "azurerm_postgresql_flexible_server" "pg" {\n  name = "shop-pg"\n}\n')
    write(shop / ".github" / "workflows" / "deploy.yml", "name: Deploy\njobs:\n  d:\n    steps:\n      - uses: azure/webapps-deploy@v3\n")
    write(shop / "node_modules" / "x" / "package.json", json.dumps({"dependencies": {"express": "4"}}))  # never read
    feat = acme / "shop.worktrees" / "feat"
    write(shop / ".git" / "worktrees" / "feat" / "HEAD", "ref: refs/heads/feat\n")
    write(feat / ".git", f"gitdir: {shop}/.git/worktrees/feat\n")
    write(worker / ".git" / "config", "[core]\n")
    write(worker / "pyproject.toml", '[project]\nname = "acme-worker"\ndependencies = ["httpx"]\n')
    write(worker / "Dockerfile", "FROM python:3.13\n")

    conn = connect(env["cfg"].db_path)
    rows = [("s1", str(shop), "claude", "2026-09-20T10:00:00Z"), ("s2", str(worker), "codex", "2026-09-21T10:00:00Z"),
            ("s3", str(feat), "claude", "2026-09-22T10:00:00Z"), ("s4", str(acme), "claude", "2026-09-23T10:00:00Z"),
            ("chat", str(shop), "chatgpt", "2026-09-24T10:00:00Z")]
    for sid, path, agent, ts in rows:
        conn.execute("INSERT INTO sessions(id, source, agent, project_path, project_name, started_at, active_s) VALUES (?,?,?,?,?,?,60)",
                     (sid, "chatgpt-export" if sid == "chat" else "transcript", agent, path, Path(path).name, ts))
    cmds = [("s1", "cd backend && uv run uvicorn app.main:app --port 8011"),
            ("s1", "curl -s http://127.0.0.1:8011/api/health"),
            ("s1", "curl -s https://shop-app.azurewebsites.net/api/health"),
            ("s1", "az webapp log tail --name shop-app --resource-group rg-shop"),
            ("s1", "python -c \"print('https://fake-thing.azurewebsites.net')\""),  # named, never reached
            ("s1", f"cd {worker} && uv run python serve.py --port 9999"),  # another project's server
            ("s3", "pnpm --dir frontend dev --port 5175"),
            ("s2", "ssh -i ~/.ssh/key -o BatchMode=yes deploy@ops-vm 'docker ps; az vm list'"),
            ("s2", "ibmcloud ce app list"),
            ("chat", "ssh chat-only-host")]
    for i, (sid, cmd) in enumerate(cmds):
        conn.execute("INSERT INTO tool_calls(session_id, ts, name, command) VALUES (?,?,?,?)", (sid, f"2026-09-20T10:00:{i:02d}Z", "Bash", cmd))
    files = [("s1", f"{shop}/frontend/src/App.tsx", 1, 2), ("s1", f"{shop}/backend/app/main.py", 1, 1), ("s3", f"{shop}/frontend/src/api.ts", 1, 1),
             ("s2", f"{shop}/backend/app/schemas.py", 2, 0), ("s2", f"{worker}/worker.py", 1, 3)]
    for sid, path, reads, edits in files:
        conn.execute("INSERT INTO session_files(session_id, path, reads, edits) VALUES (?,?,?,?)", (sid, path, reads, edits))
    conn.execute("INSERT INTO glossary(id, term, norm, aliases_json, category) VALUES (1, 'shop', 'shop', '[]', 'system')")
    conn.execute("INSERT INTO glossary(id, term, norm, aliases_json, category) VALUES (2, 'SAP Ariba', 'sap ariba', '[\"worker\"]', 'service')")
    conn.execute("INSERT INTO glossary_usage(term_id, project_path, context, updated_at) VALUES (1, ?, 'The worker posts results to the shop API.', '2026-09-21')", (str(worker),))
    conn.execute("INSERT INTO glossary_usage(term_id, project_path, context, updated_at) VALUES (2, ?, 'Orders are pushed to Ariba.', '2026-09-21')", (str(shop),))
    conn.commit()
    yield {"conn": conn, "cfg": env["cfg"], "home": home, "shop": shop, "worker": worker, "feat": feat, "acme": acme}
    conn.close()


def by_label(sys_: dict) -> dict:
    return {p["label"]: p for p in sys_["parts"]}


def test_folders_become_systems_and_groups(world):
    data = systems.build(world["conn"], world["cfg"])
    ids = {s["id"] for s in data["systems"]}
    assert ids == {str(world["shop"]), str(world["worker"])}  # the worktree is the shop; Acme holds both, so it is a group
    shop = next(s for s in data["systems"] if s["label"] == "shop")
    assert shop["sessions"] == 2 and set(shop["project_paths"]) == {str(world["shop"]), str(world["feat"])}  # not the chat export
    assert shop["git"] == {"host": "github.com", "repo": "acme/shop", "url": "https://github.com/acme/shop"}  # no credentials
    groups = {g["id"]: g for g in data["groups"]}
    acme = next(g for g in groups.values() if str(world["shop"].name) in [Path(k).name for k in g["systems"]])
    assert acme["label"] == "Work › Acme" and acme["loose"] == 1  # a folder holding one folder reads as its path


def test_parts_and_connections_come_from_manifests_and_commands(world):
    shop = next(s for s in systems.build(world["conn"], world["cfg"])["systems"] if s["label"] == "shop")
    parts = by_label(shop)
    assert parts["frontend"]["kind"] == "ui" and parts["frontend"]["role"] == "way_in" and "React" in parts["frontend"]["stack"]
    assert parts["backend"]["kind"] == "api" and "FastAPI" in parts["backend"]["stack"] and "Docker" in parts["backend"]["stack"]
    assert {p["port"] for p in parts["backend"]["ports"]} >= {8011} and 9999 not in {p["port"] for p in parts["backend"]["ports"]}
    assert parts["backend"]["calls"] == [{"path": "/api", "n": 1}]
    assert 5175 in {p["port"] for p in parts["frontend"]["ports"]}  # pnpm --dir frontend, from the worktree's session
    assert [p for p in shop["parts"] if p["label"] == "PostgreSQL"] == [parts["PostgreSQL"]]  # dependency, compose and Terraform: one
    assert parts["PostgreSQL"].get("resource") == "azurerm_postgresql_flexible_server.pg"
    app = parts["shop-app"]  # the web app Terraform declares and the host commands reached are one node
    assert app["kind"] == "deployed" and app["resource"] == "azurerm_linux_web_app.app" and app["platform"] == "Azure"
    assert not any("fake-thing" in p["label"] for p in shop["parts"])  # a URL no command went to
    assert "rg-shop" not in parts and "Resource group" not in parts  # Terraform plumbing is left out
    assert parts["Azure"]["kind"] == "platform"  # the az CLI is its own evidence, not the web app's
    names = {p["id"]: p["label"] for p in shop["parts"]}
    edges = {(names[e["from"]], e["label"], names[e["to"]]) for e in shop["edges"]}
    assert ("frontend", "calls /api", "backend") in edges  # the vite proxy to the port the backend runs on
    assert ("backend", "stores in", "PostgreSQL") in edges  # one arrow per pair, its evidence pooled:
    db = next(e for e in shop["edges"] if names[e["from"]] == "backend" and names[e["to"]] == "PostgreSQL")
    assert any("api depends_on db" in ev["text"] for ev in db["evidence"])  # compose, built from backend/Dockerfile
    assert any("DATABASE_URL" in ev["text"] for ev in db["evidence"])  # and the service's environment
    assert ("backend", "uses", "OpenAI API") in edges
    assert ("infra (Terraform)", "provisions", "shop-app") in edges
    assert ("GitHub Actions", "deploys to", "Azure Web App") in edges
    ev = " ".join(e["text"] for e in app["evidence"])
    assert 'resource "azurerm_linux_web_app" "app"' in ev and "reached in 1 command" in ev
    folders = {f["folder"] for f in shop["folders"]}
    assert folders == {"frontend", "backend"}


def test_sessions_say_where_it_runs(world):
    worker = next(s for s in systems.build(world["conn"], world["cfg"])["systems"] if s["label"] == "worker")
    parts = by_label(worker)
    assert parts["worker"]["kind"] == "service"  # a package with no framework that ships as a container
    assert parts["ops-vm"]["kind"] == "server" and parts["IBM Cloud"]["kind"] == "platform"
    assert "Azure" not in parts  # `az` ran on ops-vm, inside the ssh command's quotes
    assert "chat-only-host" not in parts
    names = {p["id"]: p["label"] for p in worker["parts"]}
    assert ("worker", "runs on", "ops-vm") in {(names[e["from"]], e["label"], names[e["to"]]) for e in worker["edges"]}


def test_links_between_systems(world):
    data = systems.build(world["conn"], world["cfg"])
    shop, worker = str(world["shop"]), str(world["worker"])
    links = {(ln["from"], ln["to"], ln["kind"]): ln for ln in data["links"]}
    assert (worker, shop, "files") in links and links[(worker, shop, "files")]["files"] == 1
    assert links[(worker, shop, "mentions")]["evidence"][0]["text"] == "The worker posts results to the shop API."
    assert (shop, worker, "mentions") not in links  # an alias is not a name: "SAP Ariba" listing the worker links nothing
    one = systems.system(data, str(world["feat"]))  # any of its folders finds it
    assert one["id"] == shop and {ln["direction"] for ln in one["links"]} == {"in"}
    light = systems.landscape(data)
    s = next(x for x in light["systems"] if x["id"] == shop)
    assert "parts" not in s and s["n_parts"] == 2 and {"label": "shop-app", "platform": "Azure", "what": "App Service"} in s["deployed"]


def test_without_manifests_parts_come_from_sessions(world):
    shop = next(s for s in systems.build(world["conn"], world["cfg"], read_manifests_=False)["systems"] if s["label"] == "shop")
    parts = by_label(shop)
    assert parts["frontend"]["kind"] == "component" and "backend" in parts  # the folders sessions edited
    assert not any(p["id"].startswith("tf:") for p in shop["parts"])


def test_yaml_lite_reads_compose_shapes():
    data = systems._yaml_lite("services:\n  web:\n    build: ./web\n    ports: ['8080:80', \"9000\"]\n    depends_on: [db]\n"
                              "  db:\n    image: 'postgres:16'\n    environment:\n      - POSTGRES_DB=app # comment\n")
    assert data["services"]["web"] == {"build": "./web", "ports": ["8080:80", "9000"], "depends_on": ["db"]}
    assert data["services"]["db"] == {"image": "postgres:16", "environment": ["POSTGRES_DB=app"]}


def test_commands_split_outside_quotes():
    assert systems._segments("cd a && ssh h 'x; y' | tail -1; echo \"p;q\"") == ["cd a", "ssh h 'x; y'", "tail -1", 'echo "p;q"']
    assert systems._ssh_target("ssh", "ssh -i k -o BatchMode=yes root@10.0.0.1 'ls'") == "10.0.0.1"
    assert systems._ssh_target("scp", "scp ./a.txt deploy@box:/srv/") == "box"
    assert systems._host_info("app-x.scm.azurewebsites.net") == ("app-x", "Azure", "App Service")


def test_api(world):
    from interlatch.server import App, make_handler

    systems._CACHE.update(sig=None, data=None)
    app = App(world["cfg"])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    httpd.RequestHandlerClass = make_handler(app, httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/api/systems", timeout=10) as r:
            land = json.loads(r.read())
        assert {s["label"] for s in land["systems"]} == {"shop", "worker"} and all("parts" not in s for s in land["systems"])
        with urllib.request.urlopen(f"{base}/api/system?id={urllib.parse.quote(str(world['shop']))}", timeout=10) as r:
            one = json.loads(r.read())
        assert one["label"] == "shop" and one["parts"] and [r["id"] for r in one["recent"]] == ["s3", "s1"]
        try:
            urllib.request.urlopen(f"{base}/api/system?id=%2Fnowhere", timeout=10)
            raise AssertionError("an unknown system is a 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        httpd.shutdown()
