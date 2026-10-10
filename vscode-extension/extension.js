// Interlatch for VS Code: the coding-agent sessions that read or changed the open file, and the workspace's files that
// sessions touched, from the local dashboard's API (GET /api/file, /api/files). No dependencies and no build step;
// VS Code's own Node provides fetch.
const vscode = require("vscode");

const AGENTS = { claude: "Claude Code", codex: "Codex", copilot: "Copilot", bob: "IBM Bob", antigravity: "Antigravity", "claude-ai": "Claude.ai", chatgpt: "ChatGPT" };

// The extension was called Chronicle until 0.2.0: its settings (chronicle.*) still count where the interlatch.* one
// isn't set, and its id is how we spot it still installed.
const OLD_SECTION = "chronicle";
const OLD_ID = "chatixia.chronicle-sessions";

// A value you set for interlatch.<key>, else for chronicle.<key>; undefined when neither is set.
const configured = (key) => {
  for (const section of ["interlatch", OLD_SECTION]) {
    const i = vscode.workspace.getConfiguration(section).inspect(key);
    const v = i?.workspaceFolderValue ?? i?.workspaceValue ?? i?.globalValue;
    if (v !== undefined && v !== null && v !== "") return v;
  }
  return undefined;
};
const setting = (key, fallback) => configured(key) ?? fallback;
// Without a url of your own: the default port, then 8765, the port older installs keep in config.toml
const DEFAULT_URLS = ["http://127.0.0.1:11524", "http://127.0.0.1:8765"];
let answered = null; // the default address that answered last
const ownUrl = () => {
  const v = configured("url");
  return v ? String(v).replace(/\/+$/, "") : null;
};
const baseUrl = () => ownUrl() || answered || DEFAULT_URLS[0];
const includeReads = () => setting("includeReads", true);

// GET one API path: { data }, or { error } worded for the view's message line.
async function api(path) {
  const own = ownUrl();
  const urls = own ? [own] : [...new Set([answered, ...DEFAULT_URLS].filter(Boolean))];
  for (const url of urls) {
    let res;
    try {
      res = await fetch(`${url}${path}`, { signal: AbortSignal.timeout(5000) });
    } catch {
      continue; // nothing there: try the next address
    }
    if (!own) answered = url;
    if (res.status === 404) return { error: "This dashboard is too old for the extension: install Interlatch (uv tool install interlatch)." };
    if (!res.ok) return { error: `Interlatch answered ${res.status}: check interlatch.url in Settings.` };
    return { data: await res.json() };
  }
  return { error: `Interlatch isn't running at ${urls.join(" or ")}. Start it with: interlatch ui` };
}

function ago(iso) {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(s)) return "";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

const md = (text) => String(text).replace(/[\\`*_{}[\]()#+\-.!|<>]/g, "\\$&");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// What a session did to the file: "+3 −1 lines", "edited 4×" or "read 2×" (some agents record edits without line counts).
function fileChange(f) {
  if (f.changes) return f.added || f.removed ? `+${f.added || 0} −${f.removed || 0} lines` : `edited ${f.changes}×`;
  return `read ${f.reads || 0}×`;
}

// Breaks text into lines short enough for a narrow sidebar, since tree rows don't wrap.
function wrap(text, width = 44, max = 8) {
  const lines = [];
  let line = "";
  for (const word of String(text).split(/\s+/).filter(Boolean)) {
    if (line && line.length + 1 + word.length > width) {
      lines.push(line);
      line = word;
    } else line = line ? `${line} ${word}` : word;
  }
  if (line) lines.push(line);
  return lines.length > max ? [...lines.slice(0, max - 1), `${lines[max - 1]} …`] : lines;
}

// A session row: its title and when, expanding to its details. `scope` keeps ids unique where the same session
// appears under several files.
function sessionItem(s, scope) {
  const f = s.file || {};
  const item = new vscode.TreeItem(s.title || "(untitled session)", vscode.TreeItemCollapsibleState.Collapsed);
  item.id = `${scope}|${s.id}`;
  item.session = s;
  item.description = ago(s.started_at);
  item.iconPath = new vscode.ThemeIcon(f.changes ? "edit" : "eye");
  item.contextValue = "interlatchSession";
  const lines = f.added || f.removed ? `+${f.added || 0} −${f.removed || 0}` : "";
  const tip = new vscode.MarkdownString();
  tip.appendMarkdown(`**${md(item.label)}**\n\n`);
  tip.appendMarkdown(`${md(AGENTS[s.agent] || s.agent)} · ${md(s.project_name || s.project_path || "")}`
    + `${s.git_branch ? ` · \`${s.git_branch.replace(/`/g, "")}\`` : ""}\n\n`);
  tip.appendMarkdown(`${md(new Date(s.started_at).toLocaleString())}\n\n`);
  const parts = [];
  if (f.changes) parts.push(`changed ${f.changes}×${lines ? ` (${lines} lines)` : ""}`);
  if (f.reads) parts.push(`read ${f.reads}×`);
  if (parts.length) tip.appendMarkdown(`This file: ${md(parts.join(", "))}\n\n`);
  if (s.outcome) tip.appendMarkdown(`Outcome: ${md(s.outcome)}\n\n`);
  if (s.summary) tip.appendMarkdown(`${md(s.summary)}\n\n`);
  item.tooltip = tip;
  return item;
}

// The lines under an expanded session: one fact per row, the summary wrapped, and a link to the dashboard.
function sessionDetails(s) {
  const row = (label, icon, tooltip) => {
    const it = new vscode.TreeItem(label);
    it.iconPath = new vscode.ThemeIcon(icon);
    if (tooltip) it.tooltip = tooltip;
    return it;
  };
  const when = new Date(s.started_at);
  const rows = [
    row(`${ago(s.started_at)} · ${when.toLocaleDateString(undefined, { month: "short", day: "numeric" })} `
      + when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }), "clock", when.toLocaleString()),
    row([AGENTS[s.agent] || s.agent, s.project_name].filter(Boolean).join(" · "), "hubot", s.project_path || undefined),
  ];
  if (s.git_branch) rows.push(row(s.git_branch, "git-branch"));
  rows.push(row(fileChange(s.file || {}), s.file && s.file.changes ? "diff" : "eye"));
  const outcomeIcon = { completed: "pass", partial: "circle-large-outline", blocked: "error", abandoned: "circle-slash",
                        exploratory: "search" }[s.outcome] || "question";
  if (s.outcome) rows.push(row(`Outcome: ${s.outcome}`, outcomeIcon));
  wrap(s.summary || "").forEach((line, i) => rows.push(row(line, i ? "blank" : "note", s.summary)));
  const open = row("Open in Interlatch", "link-external");
  open.command = { command: "interlatch.openSession", title: "Open Session in Interlatch", arguments: [s.id] };
  rows.push(open);
  return rows;
}

// The sessions for one file, as the items under it (both views use this).
async function fileSessions(path) {
  const { data, error } = await api(`/api/file?path=${encodeURIComponent(path)}&limit=100`);
  if (error) return { error };
  const sessions = includeReads() ? data.sessions : data.sessions.filter((s) => s.file && s.file.changes);
  const more = data.total > data.sessions.length ? `Showing the newest ${data.sessions.length} of ${data.total}.` : undefined;
  return { items: sessions.map((s) => sessionItem(s, path)), more };
}

// "Interlatch: This File": the sessions for whichever file the editor shows.
class ThisFile {
  constructor() {
    this.changed = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.changed.event;
    this.items = [];
    this.uri = undefined;
    this.seq = 0;
  }

  attach(view) { this.view = view; }
  getTreeItem(item) { return item; }
  getChildren(item) { return !item ? this.items : item.session ? sessionDetails(item.session) : []; }

  show(items, message, description) {
    this.items = items;
    this.view.message = message;
    this.view.description = description;
    this.changed.fire();
  }

  // Keeps the last file when focus moves to a panel or the tree itself, so the list doesn't blank out.
  follow(editor) {
    if (editor) this.uri = editor.document.uri;
    if (this.view.visible) this.load();
  }

  async load() {
    const uri = this.uri;
    const seq = ++this.seq;
    if (!uri) return this.show([], "Open a file to see the sessions that read or changed it.", "");
    const name = uri.path.split("/").pop();
    if (uri.scheme !== "file") return this.show([], "Interlatch only knows files on this computer.", name);
    const { items, more, error } = await fileSessions(uri.fsPath);
    if (seq !== this.seq) return;
    if (error) return this.show([], error, name);
    this.show(items, items.length ? more : "No recorded session read or changed this file.", name);
  }
}

const byName = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" }).compare;

// Each folder's distinct sessions, latest touch and file count, from the files beneath it.
function summarize(node) {
  node.ids = new Set();
  node.last = "";
  node.count = node.files.length;
  for (const f of node.files) {
    f.ids.forEach((id) => node.ids.add(id));
    if (f.last > node.last) node.last = f.last;
  }
  for (const sub of node.folders.values()) {
    summarize(sub);
    sub.ids.forEach((id) => node.ids.add(id));
    if (sub.last > node.last) node.last = sub.last;
    node.count += sub.count;
  }
}

// "Interlatch: Files in Workspace": the files sessions touched, as a folder tree like the Explorer's; expand a file for its
// sessions.
class WorkspaceFiles {
  constructor() {
    this.changed = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.changed.event;
    this.trees = new Map();  // workspace folder -> its tree, fetched once per refresh
  }

  attach(view) { this.view = view; }
  refresh() { this.trees.clear(); this.changed.fire(); }
  getTreeItem(item) { return item; }

  async getChildren(item) {
    if (item && item.session) return sessionDetails(item.session);
    if (item && item.filePath) {
      const { items, error } = await fileSessions(item.filePath);
      return error ? [new vscode.TreeItem(error)] : items;
    }
    if (item && item.node) return this.items(item.node);
    if (item && item.root) {
      const t = await this.tree(item.root, false);
      return t.error ? [new vscode.TreeItem(t.error)] : this.items(t.node);
    }
    const folders = (vscode.workspace.workspaceFolders || []).filter((f) => f.uri.scheme === "file");
    this.view.message = folders.length ? undefined : "Open a folder to see the files agents worked on in it.";
    if (folders.length === 1) {
      const t = await this.tree(folders[0].uri.fsPath, true);
      return t.node ? this.items(t.node) : [];
    }
    return folders.map((f) => {
      const it = new vscode.TreeItem(f.name, vscode.TreeItemCollapsibleState.Expanded);
      it.id = `root:${f.uri.fsPath}`;
      it.root = f.uri.fsPath;
      it.iconPath = new vscode.ThemeIcon("root-folder");
      return it;
    });
  }

  // `own` when this folder is the whole view, so its notes can use the view's message line.
  async tree(root, own) {
    if (!this.trees.has(root)) this.trees.set(root, this.build(root));
    const t = await this.trees.get(root);
    if (own) this.view.message = t.error || t.note;
    return t;
  }

  async build(root) {
    const ignored = setting("showIgnoredFiles", false) ? 1 : 0;
    const { data, error } = await api(`/api/files?root=${encodeURIComponent(root)}&existing=1&ignored=${ignored}&limit=2000`);
    if (error) return { error };
    const reads = includeReads();
    const top = { dir: root, folders: new Map(), files: [] };
    for (const f of data.files) {
      const ids = reads ? f.session_ids : f.changed_ids;
      if (!ids.length) continue;
      const parts = f.rel.split("/");
      let node = top;
      for (const part of parts.slice(0, -1)) {
        if (!node.folders.has(part)) node.folders.set(part, { name: part, dir: `${node.dir}/${part}`, folders: new Map(), files: [] });
        node = node.folders.get(part);
      }
      node.files.push({ ...f, name: parts[parts.length - 1], ids });
    }
    summarize(top);
    const note = !top.count ? "No recorded session read or changed a file in this folder."
      : data.total > data.files.length ? `Showing the ${data.files.length} most recently touched of ${data.total} files.` : undefined;
    return { node: top, note };
  }

  // A folder's subfolders, then its files, by name as the Explorer sorts them.
  items(node) {
    const folders = [...node.folders.values()].map((sub) => {
      let n = sub, label = sub.name;
      while (!n.files.length && n.folders.size === 1) {  // compact a chain of single folders, like `src/chronicle`
        n = n.folders.values().next().value;
        label += `/${n.name}`;
      }
      const it = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.Collapsed);
      it.id = `dir:${n.dir}`;
      it.node = n;
      it.resourceUri = vscode.Uri.file(n.dir);
      it.iconPath = vscode.ThemeIcon.Folder;
      it.description = `${plural(n.ids.size, "session")} · ${ago(n.last)}`;
      it.tooltip = `${n.dir}\n${plural(n.count, "file")} that ${plural(n.ids.size, "session")} worked on`;
      return it;
    }).sort((a, b) => byName(a.label, b.label));
    const files = [...node.files].sort((a, b) => byName(a.name, b.name)).map((f) => {
      const it = new vscode.TreeItem(f.name, vscode.TreeItemCollapsibleState.Collapsed);
      it.id = `file:${f.path}`;
      it.filePath = f.path;
      it.resourceUri = vscode.Uri.file(f.path);
      it.iconPath = vscode.ThemeIcon.File;
      it.description = `${plural(f.ids.length, "session")} · ${ago(f.last)}`;
      it.tooltip = `${f.path}\n${plural(f.sessions, "session")}: ${f.changed} changed it, ${f.sessions - f.changed} only read it`;
      it.contextValue = "interlatchFile";
      return it;
    });
    return [...folders, ...files];
  }
}

// Once: the Chronicle extension, still installed, shows the same sections again under its old name. Offer to uninstall
// it.
async function suggestUninstallingChronicle(context) {
  const key = "suggestedUninstallingChronicle";
  if (!vscode.extensions.getExtension(OLD_ID) || context.globalState.get(key)) return;
  await context.globalState.update(key, true);
  const uninstall = "Uninstall Chronicle";
  const pick = await vscode.window.showInformationMessage(
    "Interlatch replaces the Chronicle extension, which is still installed. Uninstall it?", uninstall);
  if (pick !== uninstall) return;
  try {
    await vscode.commands.executeCommand("workbench.extensions.uninstallExtension", OLD_ID);
  } catch {
    vscode.commands.executeCommand("workbench.extensions.search", `@installed ${OLD_ID}`);
  }
}

function activate(context) {
  const thisFile = new ThisFile();
  const fileView = vscode.window.createTreeView("interlatch.fileSessions", { treeDataProvider: thisFile });
  thisFile.attach(fileView);
  thisFile.uri = vscode.window.activeTextEditor && vscode.window.activeTextEditor.document.uri;

  const workspace = new WorkspaceFiles();
  const filesView = vscode.window.createTreeView("interlatch.workspaceFiles", { treeDataProvider: workspace });
  workspace.attach(filesView);

  const refresh = () => { thisFile.load(); workspace.refresh(); };
  let timer;
  const debounced = (editor) => { clearTimeout(timer); timer = setTimeout(() => thisFile.follow(editor), 250); };
  context.subscriptions.push(
    fileView,
    filesView,
    vscode.window.onDidChangeActiveTextEditor(debounced),
    fileView.onDidChangeVisibility((e) => e.visible && thisFile.load()),
    filesView.onDidChangeVisibility((e) => e.visible && workspace.refresh()),
    vscode.workspace.onDidChangeWorkspaceFolders(() => workspace.refresh()),
    vscode.workspace.onDidChangeConfiguration((e) => (e.affectsConfiguration("interlatch") || e.affectsConfiguration(OLD_SECTION)) && refresh()),
    vscode.commands.registerCommand("interlatch.refresh", refresh),
    vscode.commands.registerCommand("interlatch.openFile", (item) => item && vscode.window.showTextDocument(vscode.Uri.file(item.filePath))),
    vscode.commands.registerCommand("interlatch.openDashboard", () => vscode.env.openExternal(vscode.Uri.parse(`${baseUrl()}/`))),
    // an id from a details row, or the session row itself from its inline button
    vscode.commands.registerCommand("interlatch.openSession", (arg) => {
      const id = typeof arg === "string" ? arg : arg && arg.session && arg.session.id;
      if (id) vscode.env.openExternal(vscode.Uri.parse(`${baseUrl()}/#/session/${encodeURIComponent(id)}`));
    }),
    { dispose: () => clearTimeout(timer) },
  );
  if (fileView.visible) thisFile.load();
  suggestUninstallingChronicle(context);
}

function deactivate() {}

module.exports = { activate, deactivate };
