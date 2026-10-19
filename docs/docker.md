# A hub in Docker

[← Interlatch](../README.md) · [Docs index](README.md)

This guide sets up a team's [hub](devices.md#your-other-computers) on a server with Docker: from an empty server to
teammates sharing what they learn in a project. It's for whoever runs the hub. Teammates follow
[Joining your team's hub](join-a-hub.md) instead.

`docker/compose.yaml` runs three containers:

- **the hub**, from the image `ghcr.io/chatixia-ai/interlatch-hub`;
- **Caddy** in front of it, which gets and renews the HTTPS certificate;
- **Postgres** for the [team store](devices.md#teammates-lessons-and-a-team-store-in-postgres), which merges what
  each computer learned and sends teammates' lessons back.

The hub takes **knowledge only**. Each computer records and analyzes its own sessions with its own Claude Code,
Codex or model provider, then sends the hub each session's summary and project lessons. Transcripts, prompts and
file paths stay on the computers. The hub analyzes nothing, so it needs no model and no API key.

## What you need

- **A Linux server with Docker**, which you reach over SSH. Two CPUs and 4 GB of memory are plenty.
- **Ports 80 and 443 open** to the computers that will use the hub. Caddy also needs them to get a certificate from
  Let's Encrypt.
- **A name for the hub** whose DNS points at the server, such as `interlatch.example.com`. To try the hub before you
  have one, write the server's IP address with dashes and add `.sslip.io`: `172-207-25-249.sslip.io` is a free name
  that points at `172.207.25.249`. For a name only your company network reaches, see
  [Your own certificate](#your-own-certificate).

    Every hub needs a name of its own. A browser sends the cookies of one name to every port of it, so two hubs on
    one name, even on different ports, could sign in to each other as their people. For a second hub on the same
    server, put a word in front: `team-b.172-207-25-249.sslip.io` points at the same address
    ([One name per hub](devices.md#one-name-per-hub)).

To install Docker on the server:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
```

### On Azure

- **Use a virtual machine:** Ubuntu, size B2s or similar. Azure Container Apps and App Service won't do: they keep
  files on Azure Files, a network share, and the hub's SQLite database (in WAL mode) doesn't work reliably there.
- **Give it a key of your own.** Under **Administrator account**, choose **Use existing public key** and paste the
  public half of a key made for the hub:

    ```bash
    ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_interlatch_hub -C interlatch-hub
    pbcopy < ~/.ssh/id_ed25519_interlatch_hub.pub
    ```

    With **Generate new key pair** instead, creating the VM ends in a **Download private key** dialog. It isn't an
    error: the VM is only created once you download the key there.
- **Open the ports:** the VM's **Networking** › **Add inbound port rule**, once for port 80 and once for 443.
- **Name it:** the VM's public IP address › **Configuration** › **DNS name label** gives it
  `<label>.<region>.cloudapp.azure.com`. The sslip.io name above works too.

Then connect with `ssh -i ~/.ssh/id_ed25519_interlatch_hub azureuser@<the VM's IP address>`.

## Set up the hub

Steps 1 to 3 run on the server.

### 1. Download the files

```bash
mkdir ~/interlatch-hub && cd ~/interlatch-hub
base=https://raw.githubusercontent.com/Chatixia-AI/interlatch/main/docker
curl -fsSL "$base/compose.yaml" -o compose.yaml
curl -fsSL "$base/Caddyfile" -o Caddyfile
curl -fsSL "$base/.env.example" -o .env
```

### 2. Fill in the settings

Make a password for the team store's database, copy it, and open `.env`:

```bash
openssl rand -hex 24
nano .env
```

Set these four lines. In nano, Ctrl+O then Enter saves, and Ctrl+X quits.

```bash
INTERLATCH_DOMAIN=interlatch.example.com  # the hub's name
INTERLATCH_ADMIN_EMAIL=you@example.com    # its first admin: you
INTERLATCH_ADMIN_NAME=You
POSTGRES_PASSWORD=...                     # the password you copied
```

### 3. Start it

```bash
docker compose up -d
docker compose logs hub
```

The log shows your invite as the hub's first admin:

```text
Added You (you@example.com) as this hub's admin. The invite works once, for 7 days:

  In a browser, to open the hub's dashboard:  https://interlatch.example.com/signin?code=ABCD-EFGH-JKLM
  Or on their computer, to join it:           interlatch hub join https://interlatch.example.com --code ABCD-EFGH-JKLM --share knowledge
```

It's printed **only on the hub's first start**. A restart keeps everyone and prints no invite. If you miss it, or
it expires, make another:

```bash
docker compose exec hub interlatch hub invite you@example.com
```

The container won't serve anything until the hub has an admin. A hub with no people has no sign-in, and its
dashboard answers no one who comes through a proxy, so a start without `INTERLATCH_ADMIN_EMAIL` stops with an error
and serves nothing.

### 4. Sign in

Open the invite's **link** in your browser. You're the hub's admin: **Team** in the sidebar has its people,
projects, computers and team store. Every page says it's the hub, with a green band across the top (its name and
address) and a line in the status bar. Since it's a dedicated hub, the dashboard leaves out what only a person's own
computer needs, such as **Sync** and **Settings › Sources**.

## Add a project

In the dashboard, open **Team › Projects**, type the project's name under **New project**, such as `Website`, and
choose **Create project**.

An older image has no such button. There, a project is a folder on the hub's computer named after the project, which
you make on the server, in `~/interlatch-hub`:

```bash
docker compose exec hub mkdir -p /data/projects/Website
docker compose exec hub interlatch hub project add /data/projects/Website
docker compose exec hub interlatch hub project list
```

Each computer then adds its own folder for the project, as the next sections show. Once one computer has sent sessions from a git repository, other clones of that repository are
filed under the project on their own.

Members limited to some projects don't see the new one until you give it to them: their **Join a project** answers
"Nothing matches." Change their projects in **Team › People**, or on the server. The list is replaced, so name every
project they keep: for someone who sees `Mobile`,

```bash
docker compose exec hub interlatch hub people
docker compose exec hub interlatch hub access <email or id> --project Mobile --project Website
```

## Connect your own computer

Your computer joins the hub the way a teammate's does: with an invite of its own and the `interlatch hub join`
command that the invite prints. First choose what it shares:

- **Only the project.** Invite your computer as a person who sees only the project. Your browser keeps your admin
  sign-in.

    ```bash
    docker compose exec hub interlatch hub invite "Your Mac" --project Website
    ```

- **Everything it has analyzed.** Make a new code for yourself with
  `docker compose exec hub interlatch hub invite you@example.com`, and join with that. Your computer then sends the
  summary and project lessons of every analyzed coding-agent session, so all your projects appear on the hub.

Either way, chats imported from ChatGPT or claude.ai never leave the computer.

Every invite prints two things, and its code works **once, for one of them**:

- **the `interlatch hub join …` command** connects a computer: run it in a terminal;
- **the `https://…/signin?code=…` link** opens the dashboard: open it in a browser.

Opening the link uses up the code, and the computer then needs a new one.

On your computer:

```bash
interlatch hub disable    # only if this computer is a hub itself
interlatch hub leave      # only if it joined another hub
interlatch hub join https://interlatch.example.com --code XXXX-XXXX-XXXX --share knowledge --no-push
interlatch hub add-folder ~/Projects/Website --project Website
```

`add-folder` sends right away. A result like `3 sessions shared … 300 excluded` means the project's 3 analyzed
sessions went to the hub and the other 300 stayed on your computer. Refresh **Projects** in the dashboard to see
them.

## Invite your team

On the server, one invite per person:

```bash
docker compose exec hub interlatch hub invite "Yuma" --email yuma@example.com --project Website
```

Send them the `interlatch hub join …` line, not the link, with [Joining your team's hub](join-a-hub.md), which
walks them through the rest. The code works once, so don't send both. Once their computer has joined, they open the
dashboard from their own Interlatch: **Settings › Devices › Open the hub's dashboard**, or `interlatch hub signin`. For
a phone, or someone who only views the dashboard, make a new code with
`docker compose exec hub interlatch hub invite yuma@example.com` (or **New invite** on their row in **Team › People**)
and send its **link**.

**Team › People** in the dashboard invites people too, choosing their role and the projects they see.

## Everyday commands

On the server, in `~/interlatch-hub`:

| To | Run |
|---|---|
| List people and what each sees | `docker compose exec hub interlatch hub people` |
| See the computers that send, and when each last did | `docker compose exec hub interlatch hub status` |
| List the projects | `docker compose exec hub interlatch hub project list` |
| Make a new code for someone already on the hub | `docker compose exec hub interlatch hub invite <their email or id>` |
| Change which projects someone sees | `docker compose exec hub interlatch hub access <email or id> --project <name>…` (every project they keep: the list is replaced) |
| Remove someone | `docker compose exec hub interlatch hub remove <email or id>` |
| Read the hub's log | `docker compose logs hub` |

For a new code, use the person's email or the id that `hub people` shows. Typing their name again adds a second
person.

The hub in the container never runs a session in your repositories, so it doesn't know their git remotes. After
joining, each member adds their folder for each project, or their computer shares nothing:
`interlatch hub add-folder ~/work/demo-app --project demo-app`. Sessions in other folders stay on their computer
([Sharing knowledge only](devices.md#sharing-knowledge-only)). To take back what a computer sent:
`docker compose exec hub interlatch hub purge bob@example.com --project demo-app`
([Taking back what a computer sent](devices.md#taking-back-what-a-computer-sent)).

## What the hub takes

On its first start, the container sets two settings that an admin can change later:

| Setting | Value | Meaning |
|---|---|---|
| `[hub] accept` | `"knowledge"` | The hub turns away a computer that sends transcripts. |
| `[hub] shared_token` | `false` | Computers join with an invite of their own, not the hub's one token. |

To let the hub take transcripts and analyze them itself, it needs a model provider's API. The image doesn't include
Claude Code or Codex, so they can't be used here:

```bash
docker compose exec hub interlatch config set hub.accept everything
docker compose exec hub interlatch config set analysis.backend anthropic
docker compose exec hub interlatch config set-key anthropic   # asks for the key
docker compose restart hub
```

[Model providers](analysis.md#model-providers) lists the others.

## Settings

The container sets up `config.toml` from these variables on every start. A variable that is set wins over
`config.toml`; one that is unset or empty leaves it as it is. Other settings stay in `config.toml`. Change them with
`docker compose exec hub interlatch config set ...` and restart the hub. Each variable is also read under its name
from before the rename (`CHRONICLE_HUB_URL` for `INTERLATCH_HUB_URL`, …), so an older `.env` keeps working.

| Variable | Default | |
|---|---|---|
| `INTERLATCH_HUB_URL` | (required) | The address computers and browsers reach the hub at. It becomes `[hub] address`, and its host name joins `[server] allowed_hosts`. `compose.yaml` sets it from `INTERLATCH_DOMAIN`. |
| `INTERLATCH_ADMIN_EMAIL` | (required at first) | The first admin, added when the hub has no people. |
| `INTERLATCH_ADMIN_NAME` | the email's first part | Their name. |
| `INTERLATCH_HUB_NAME` | `Interlatch hub` | The name the dashboard shows (`[hub] name`). |
| `INTERLATCH_HOST` | `0.0.0.0` | The address the dashboard listens on. `compose.yaml` sets `127.0.0.1`: the hub shares Caddy's network, so only Caddy reaches it. |
| `INTERLATCH_PORT` | `11524` | The dashboard's port. |
| `INTERLATCH_ALLOWED_HOSTS` | | More names the dashboard answers to, separated by commas. |
| `INTERLATCH_TRUSTED_PROXIES` | `127.0.0.1, ::1` | Addresses of the proxies whose `X-Forwarded-Proto` the hub believes, separated by commas. Set this when your own proxy forwards to the container ([below](#with-your-own-proxy)). |
| `INTERLATCH_TEAM_STORE` | | `postgres` keeps the team store in Postgres, connecting with `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` and `PGSSLMODE`. `compose.yaml` sets all of them. |
| `INTERLATCH_WORK_MINUTES` | `15` | How often the hub reads what computers sent and runs its background work. `0` turns it off. |

Everything the hub keeps is in the `hub-data` volume, at `/data` in the container. The team store's data is in the
`postgres-data` volume.

Admins also find the hub's settings in its dashboard, under **Team › Hub settings**: its name, its address,
**Knowledge only**, the model that writes each project's knowledge base, updates, and the backup commands below.
The name and address show there but can't be changed there while `INTERLATCH_HUB_NAME` or `INTERLATCH_HUB_URL` sets
them, since each start writes them again: change the variable in `.env` and run `docker compose up -d`.

## Your own certificate

Caddy gets a certificate from Let's Encrypt, which must reach the server on port 80 or 443. For a name only your
company network reaches, give Caddy your company's certificate. Mount it in a `compose.override.yaml` next to
`compose.yaml`, which Compose reads with it (a new copy of `compose.yaml` when you [update](#update) leaves it as it
is):

```yaml
services:
  caddy:
    volumes:
      - ./certs:/certs:ro
```

and add a `tls` line to the `Caddyfile`:

```text
{$INTERLATCH_DOMAIN} {
	tls /certs/interlatch.crt /certs/interlatch.key
	reverse_proxy 127.0.0.1:11524
}
```

With `tls internal`, Caddy makes its own certificate instead. Every computer and browser that uses the hub must then
trust Caddy's root certificate.

## With your own proxy

Without `compose.yaml`, run the image alone and put your own HTTPS proxy (nginx, a load balancer) in front of it, as
in [Reaching the hub without Tailscale](devices.md#reaching-the-hub-without-tailscale):

```bash
docker run -d --name interlatch-hub --restart unless-stopped -p 11524:11524 -v interlatch-hub:/data \
  -e INTERLATCH_HUB_URL=https://interlatch.example.internal -e INTERLATCH_ADMIN_EMAIL=you@example.com \
  -e INTERLATCH_TRUSTED_PROXIES=10.0.4.12 ghcr.io/chatixia-ai/interlatch-hub
```

Set `INTERLATCH_TRUSTED_PROXIES` to the proxy's address as the container sees it, so the hub believes the proxy's
`X-Forwarded-Proto` and marks its sign-in cookies `Secure`. For a proxy on the same server reaching a published port,
that is usually Docker's bridge gateway, `172.17.0.1`. Let only the proxy reach port 11524. Without a team store, computers that share get no teammates'
lessons back. Add Postgres with `INTERLATCH_TEAM_STORE=postgres` and the `PG*` variables.

## Update

```bash
curl -fsSL https://raw.githubusercontent.com/Chatixia-AI/interlatch/main/docker/compose.yaml -o compose.yaml
docker compose pull
docker compose up -d
```

`compose.yaml` pins Caddy and Postgres to an exact version and digest, so their images can't change under you.
Newer ones reach this repository's copy as they are released, so download it again before pulling. Keep your own
changes in `compose.override.yaml` (as for [your own certificate](#your-own-certificate)), never in
`compose.yaml`. Postgres stays on 17: a new major version can't read the old one's data without moving it.

To stay on one version, set `INTERLATCH_VERSION` in `.env` to a release's version number. The dashboard can't update
a container: **Status** says to pull the new image instead.

**A hub set up as Chronicle** moves the same way: download the new `compose.yaml` (and the `Caddyfile`, if you
never changed it), keep your `.env`, then pull and start as above. `docker compose logs hub` then says
`Interlatch hub is up`. No data moves: the volumes, the Postgres database and user (`chronicle`) and the data in
`/data` stay as they are. If `.env` pins a version from before the rename (`CHRONICLE_VERSION=0.13.0`), remove that
line: the `interlatch-hub` image has no tags that old. A hub that only pulls, with its old `compose.yaml`, keeps
working too: `ghcr.io/chatixia-ai/chronicle-hub` still gets each release ([Moving from Chronicle](moving-from-chronicle.md)).

## Back up

Copy the hub's database while it runs, and dump the team store:

```bash
docker compose exec hub python -c "import sqlite3; sqlite3.connect('/data/chronicle.db').backup(sqlite3.connect('/data/backup.db'))"
docker compose cp hub:/data/backup.db ./interlatch-backup.db
docker compose exec -T postgres pg_dump -U chronicle chronicle > team-store.sql
```

Never copy `chronicle.db` itself while the hub is running: the copy can be corrupted.

## Troubleshooting

**The log shows no invite.** The first admin's invite is printed only on the hub's first start. Make a new one with
`docker compose exec hub interlatch hub invite you@example.com`.

**The page shows an error right after a start.** Caddy starts before the hub and answers with an error for the few
seconds the hub takes to start. Reload the page.

**A computer's code was opened in a browser, or it expired.** Make a new code with the person's email or id, such as
`docker compose exec hub interlatch hub invite 3`.

**`interlatch hub join` says the computer is a hub itself.** Run `interlatch hub disable` on it first. It keeps
everything it holds, but other computers can no longer send to it.

**Your own dashboard at `http://127.0.0.1:11524/` stopped answering** after you read the hub's log in VS Code
connected to the server over Remote-SSH. The 0.13.0 image prints `Chronicle dashboard: http://127.0.0.1:11524/`,
and VS Code forwards that port to your computer, in front of your own dashboard. In the VS Code window connected to
the server (its corner says **SSH: …**), open **Ports**, right-click 11524 and choose **Stop Forwarding Port**.
Adding `"remote.portsAttributes": { "11524": { "onAutoForward": "ignore" } }` to VS Code's settings keeps it from
happening again. Later images print `Interlatch hub is up: <the hub's address>` instead (`Chronicle hub is up` before the rename).

**HTTPS doesn't work.** Check that the name points at the server's IP address, that ports 80 and 443 are open, and
what `docker compose logs caddy` says. Let's Encrypt can't reach a server that only your company network reaches:
use [your own certificate](#your-own-certificate). Some company networks block sslip.io names; use a name of your
own there.
