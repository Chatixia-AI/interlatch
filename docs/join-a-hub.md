# Joining your team's hub

[← Interlatch](../README.md) · [Docs index](README.md)

Your team runs an Interlatch hub, and its admin sent you a command like this one:

```bash
interlatch hub join https://interlatch.example.com --code XXXX-XXXX-XXXX --share knowledge
```

This page takes you from that command to sharing what your coding agents learn in your team's project, and getting
your teammates' lessons back. It takes about five minutes. Whoever runs the hub follows
[A hub in Docker](docker.md).

## What is shared

- **Sent to the hub:** for the projects the admin gave you, each analyzed session's title, summary and project
  lessons, with its details (times, counts, models). Secrets in them (keys, tokens, passwords) are redacted first.
- **Never sent:** your prompts, transcripts, files and commands; lessons about you, such as your preferences;
  how a session went for you (its mood and what slowed it down); sessions in any other project; chats you imported
  from ChatGPT or claude.ai.
- **Your own computer still analyzes your sessions,** with your own Claude Code, Codex or model provider, as it did
  before. The hub analyzes nothing.

## 1. Install Interlatch

Skip this if Interlatch is already installed. With Chronicle, its earlier name, see
[Moving from Chronicle](moving-from-chronicle.md) instead.

```bash
uv tool install --python 3.13 interlatch
interlatch install
```

[Install](install.md) explains the choices `interlatch install` offers.

## 2. Join the hub

If your computer already sends to another hub, leave it first:

```bash
interlatch hub leave
```

`interlatch hub join` refuses a second hub until you do, before it contacts the new one: joining sends your
computer's key, which is how a hub recognises it. Joining the hub you already send to again, with a new code, only
gives your computer a new token there and keeps everything else.

Then run, in a terminal, the `interlatch hub join …` command your admin sent you. It lists the projects you share:

```text
Projects you share with it: Website. Add your folder for each one: …
```

A code works once, for 7 days. Opening it as a link in your browser also uses it up, so for this step ask your
admin for the `interlatch hub join` command, not the link.

## 3. Add your project folder

Tell the hub where the project is on your computer:

```bash
interlatch hub add-folder ~/code/website --project Website
```

Use your folder for the project and the project's name from step 2. This sends what your computer has already
analyzed there. A clone of a git repository the hub already knows is filed under its project without this step,
but running it does no harm.

Your dashboard can do the same: **Settings › Devices › Projects on the hub › Join a project**, then pick the project
and your folder.

If the hub says it has no such project, or **Join a project** answers "Nothing matches.", you don't see that
project yet: a project created after you joined isn't yours until the hub's admin gives it to you. Ask them, then
try again.

## 4. Check

```bash
interlatch hub status
```

It shows the hub's address, when your computer last sent, and how many of your teammates' lessons it holds. Your
dashboard's **Settings › Devices** shows the same. **Open the hub's dashboard** there signs you in to the hub's
dashboard, where you see your projects.

## From then on

- **After each analysis,** your computer sends the project's new summaries and lessons. `interlatch push`, or
  **Share now** in **Settings › Devices**, sends right away.
- **Each time it sends,** it gets back what your teammates learned in the project. Your agents find those lessons
  through Interlatch's MCP tools, marked as teammates'. With start-of-session notes on (`interlatch install
  --inject-context`), new sessions also list them under "From teammates' sessions".
- **Your own dashboard** keeps showing your own sessions, as before. Your teammates' lessons appear on its project
  pages and in **All knowledge**, marked with the computers they came from; **Read them** in **Settings › Devices**
  lists only those.

To stop sharing to one project, choose **Leave** next to it in **Settings › Devices** (or run `interlatch hub leave
--project <name>`). What you shared there stays on the hub. Added the wrong folder? **Remove** next to it takes the
folder back, and the hub deletes what your computer shared from it. To stop sending altogether, choose **Leave the
hub…** (or run `interlatch hub leave`). What you already sent stays on the hub. Your teammates' lessons from it are
removed from your computer, and the folders, left projects and shared groups you had for that hub are put aside in
`~/.interlatch/hub-left/`: another hub you join later never gets them. If you join the same hub again, `interlatch hub
join` asks whether to bring them back (`--restore` or `--no-restore` answers without asking).

## If something goes wrong

- **The code doesn't work.** It was used already, or it is more than 7 days old. Ask your admin for a new one.
- **"this hub takes knowledge only".** Your computer was set to send transcripts. Run
  `interlatch config set hub.share knowledge`, then `interlatch push`.
- **"This computer is a hub itself".** Your computer is a hub that other computers may send to. Check with
  whoever set it up, then run `interlatch hub disable` before you join.
- **A certificate error.** Your computer doesn't trust the hub's HTTPS certificate. Tell your admin.
- **"This computer sends to the hub at …".** It is still in another hub. Run `interlatch hub leave` first, then join.
- **The hub moved to a new address.** Run `interlatch hub set-url <new address>`: your computer keeps its token,
  folders and groups, and sends to the new address from the next push. Only for the same hub: it sends your token and
  your computer's key there.
