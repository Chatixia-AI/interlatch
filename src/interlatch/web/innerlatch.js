/* Innerlatch: the other side of the door at the bottom of the dashboard's rail. The coding agents as a party of
   pixel-art characters who level up on a career roadmap, the leaderboard of hours, sessions and tokens, and the arena
   where champions duel, with the Google sign-in they share. app.js loads this file (with innerlatch.css, sprites.js and
   scene.js) the first time someone walks through the door: until then the dashboard carries none of it. Everything here
   runs in app.js's scope (h, t, api, route, setCrumbs…) and hands the shell what it needs as window.Innerlatch. */

ICONS.party = ["M14.5 17.5 3 6V3h3l11.5 11.5", "m13 19 6-6", "m16 16 4 4", "m19 21 2-2", "M14 3.5 17.5 2 21 3.5V7c0 2.4-3.5 4-3.5 4S14 9.4 14 7z"]; // a sword and a shield
ICONS.arena = ["M14.5 17.5 3 6V3h3l11.5 11.5", "m13 19 6-6", "m16 16 4 4", "m19 21 2-2", "M14.5 6.5 18 3h3v3l-3.5 3.5", "m5 14 4 4", "m7 17-3 3", "m3 19 2 2"]; // two swords crossed
ICONS.ring = [C10]; // an open quest
ICONS.leaderboard = ["M6 9H4.5a2.5 2.5 0 0 1 0-5H6", "M18 9h1.5a2.5 2.5 0 0 0 0-5H18", "M4 22h16", "M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22",
  "M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22", "M18 2H6v7a6 6 0 0 0 12 0V2Z"]; // a trophy
ICONS.hiring = ["M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2", "M4 6h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z",
  "M2 12.5c6.3 2.6 13.7 2.6 20 0", "M12 13h.01"]; // a briefcase: who's hiring

// =====================================================================================
// Leaderboard: people who signed in with Google compare their hours, sessions and tokens (leaderboard.py)
// =====================================================================================
const LB_METRICS = [["hours", t("Hours"), "clock"], ["sessions", t("Sessions"), "sessions"], ["tokens", t("Tokens"), "tokens"]];
const LB_DAYS = [["7", t("7 days")], ["30", t("30 days")], ["90", t("90 days")]];
let lbPoll = null; // while a sign-in runs in another tab, this page looks again now and then
function lbHref(by, days) {
  const qs = new URLSearchParams(Object.entries({ by: by === "hours" ? "" : by, days: days === "30" ? "" : days }).filter(([, v]) => v)).toString();
  return `#/innerlatch/leaderboard${qs ? "?" + qs : ""}`;
}
function lbValue(by, v) { return by === "hours" ? fmtHours((v || 0) * 3600) : by === "tokens" ? fmtCompact(v || 0) : fmtNum(v || 0); }
function lbAvatar(uid, name, photo) { // the initial shows until the picture (fetched through this computer) arrives
  const box = h("span", { class: "lb-avatar", "aria-hidden": "true" }, h("span", null, (name || "?").slice(0, 1).toUpperCase()));
  if (uid && photo) {
    const img = h("img", { alt: "", loading: "lazy", src: `/api/leaderboard/avatar/${encodeURIComponent(uid)}` });
    img.addEventListener("error", () => img.remove());
    box.append(img);
  }
  return box;
}
async function lbPost(path, body) {
  try {
    const r = await post(path, body);
    if (r.error) { toast(r.error, 6000); return null; }
    return r;
  } catch (e) { if (!e.handled) toast(e.message, 6000); return null; }
}
function lbSignin() { return openSignin("/api/leaderboard/signin"); }
async function openSignin(path) { // Google's sign-in runs on the hosted page, in the browser; it comes back to this dashboard
  const r = await lbPost(path);
  if (!r || !safeUrl(r.url)) return;
  const a = h("a", { href: r.url, target: "_blank", rel: "noopener" });
  document.body.append(a);
  a.click();
  a.remove();
  render();
}
function lbRenameForm(me, line) { // the name line becomes a field, in place (no prompt(): the Mac app has none)
  const input = h("input", { type: "text", value: me.name || "", maxlength: 32, "aria-label": t("The name to show on the board"), class: "lb-name-input" });
  const save = async () => {
    const name = input.value.trim();
    if (!name || name === me.name) return render();
    if (await lbPost("/api/leaderboard/name", { name })) { toast(t("Sent")); render(); }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") render(); });
  line.replaceWith(h("div", { class: "lb-rename-form" }, input,
    h("button", { class: "btn small primary", type: "button", onclick: save }, t("Save")),
    h("button", { class: "btn small", type: "button", onclick: () => render() }, t("Cancel"))));
  input.focus();
  input.select();
}
function lbMeLine(me) {
  const line = h("div", { class: "lb-me" }, lbAvatar(me.uid, me.name, me.photo),
    h("div", null, h("b", null, me.name), h("div", { class: "muted" }, me.last_sent ? t("Sent {ago}", { ago: ago(me.last_sent) }) : t("Nothing sent yet"))),
    h("button", { class: "btn small lb-rename", type: "button", onclick: () => lbRenameForm(me, line) }, t("Rename")));
  return line;
}
function lbYouCard(lb) {
  const me = lb.me;
  const period = LB_DAYS.find(([v]) => v === String(lb.days))[1];
  const mine = h("div", { class: "lb-mine" }, LB_METRICS.map(([k, label]) => fact(label, lbValue(k, lb.mine[k]))));
  const head = cardHead(t("You"), { iconName: "person", hint: t("This computer, last {period}", { period }) });
  if (!me.joined && me.signed_in) { // signed in for the Party only: one click puts these on the board
    return h("section", { class: "card lb-you" }, head, mine,
      h("p", null, t("You're signed in with Google for the Party, but not on this board. Join to put these on it: only the totals go, with your first name and picture. Leave whenever you like and your row is deleted.")),
      h("div", { class: "lb-actions" }, h("button", { class: "btn primary", type: "button", onclick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        if (await lbPost("/api/leaderboard/join")) { toast(t("You're on the board.")); render(); } else btn.disabled = false;
      } }, t("Join the board as {name}", { name: me.name || "" }))));
  }
  if (!me.joined) {
    return h("section", { class: "card lb-you" }, head, mine,
      h("p", null, t("Sign in with Google to put these on the board. Only the totals go, with your first name and picture: no email, project, prompt or title. Leave whenever you like and your row is deleted.")),
      h("div", { class: "lb-actions" }, h("button", { class: "btn primary", type: "button", onclick: lbSignin }, t("Sign in with Google")),
        lb.signing_in ? h("button", { class: "btn", type: "button", onclick: async () => { if (await lbPost("/api/leaderboard/cancel")) render(); } }, t("Cancel")) : null),
      lb.signing_in ? h("div", { class: "muted lb-wait" }, t("Finish signing in on the page that opened. This page carries on by itself once you have.")) : null);
  }
  const problem = me.paused ? t("Another computer reports for this account now. Sign in again here to report from this one.") : me.error;
  return h("section", { class: "card lb-you" }, head,
    lbMeLine(me),
    mine,
    problem ? h("p", { class: "warn-line" }, problem) : null,
    h("div", { class: "lb-actions" },
      me.paused ? h("button", { class: "btn primary", type: "button", onclick: lbSignin }, t("Sign in again"))
        : h("button", { class: "btn", type: "button", onclick: async () => { if (await lbPost("/api/leaderboard/send")) { toast(t("Sent")); render(); } } }, t("Send now")),
      h("button", { class: "btn", type: "button", onclick: async () => {
        if (!confirm(t("Leave the leaderboard? Your row is deleted, and nothing more is sent."))) return;
        if (await lbPost("/api/leaderboard/leave")) { toast(t("You left the leaderboard.")); render(); }
      } }, t("Leave"))),
    h("p", { class: "muted lb-note" }, t("Each sync sends your totals again, every 15 minutes at most.")));
}
function lbBoard(lb) {
  const by = lb.by, label = LB_METRICS.find(([v]) => v === by)[1];
  if (lb.error) return h("section", { class: "card empty" }, t("Could not reach the leaderboard: {error}", { error: lb.error }));
  if (!lb.rows.length) {
    return h("section", { class: "card" }, emptyArt("flag", h("b", null, t("No one on this board yet")),
      h("div", { class: "muted" }, t("Sign in to be the first."))));
  }
  const top = Math.max(...lb.rows.map((r) => r[by] || 0), 1);
  // all three numbers on every row; the ranked one stands out and drives the bar. A column's head ranks by it.
  const nums = (r) => LB_METRICS.map(([k]) => h("span", { class: `lb-num${k === by ? " on" : ""}` }, lbValue(k, r[k])));
  const head = h("li", { class: "lb-row lb-head", "aria-hidden": "true" }, h("span"), h("span"), h("span"),
    LB_METRICS.map(([k, l]) => h("a", { class: `lb-num${k === by ? " on" : ""}`, href: lbHref(k, String(lb.days)) }, l)));
  const row = (r) => h("li", { class: `lb-row${r.me ? " me" : ""}${r.rank <= 3 ? ` podium p${r.rank}` : ""}`,
    title: LB_METRICS.map(([k, l]) => `${l}: ${lbValue(k, r[k])}`).join(" · ") },
    h("span", { class: "lb-rank" }, String(r.rank)),
    lbAvatar(r.uid, r.name, r.photo),
    h("div", { class: "lb-who" },
      h("div", { class: "lb-name" }, h("span", null, r.name), r.me ? h("span", { class: "lb-you-tag" }, t("you")) : null),
      h("div", { class: "lb-bar", "aria-hidden": "true" }, h("i", { style: { width: `${Math.max(2, ((r[by] || 0) / top) * 100)}%` } }))),
    nums(r));
  const meShown = lb.rows.some((r) => r.me);
  return h("section", { class: "card" },
    cardHead(t("Ranked by {metric}", { metric: label.toLowerCase() }), { iconName: "leaderboard", hint: tn(lb.rows.length, "{n} person", "{n} people", { n: fmtNum(lb.rows.length) }) }),
    h("ol", { class: "lb-list" }, head, lb.rows.map(row)),
    lb.me.joined && !meShown && !lb.me.paused ? h("p", { class: "muted" }, lb.me.last_sent ? t("You're not in the top {n} on this board.", { n: lb.rows.length }) : t("Your row appears after the first send.")) : null);
}
function lbWatch(lb, path) { // a sign-in runs in another tab: look again now and then
  if (!lb.signing_in || lb.me.joined) return;
  const seq = renderSeq;
  lbPoll = setTimeout(() => { if (seq === renderSeq && parseHash().path === path) render(); }, 3000);
}
route(/^\/innerlatch\/leaderboard$/, async (params) => {
  clearTimeout(lbPoll);
  const by = LB_METRICS.some(([v]) => v === params.by) ? params.by : "hours";
  const days = LB_DAYS.some(([v]) => v === params.days) ? params.days : "30";
  const lb = await api("/api/leaderboard", { by, days });
  lbWatch(lb, "/innerlatch/leaderboard");
  return h("div", { class: "narrow-page wide" },
    h("div", { class: "page-head" }, titled("flag", h("h1", null, t("Leaderboard")),
      h("div", { class: "sub" }, t("Who spends the most time with their coding agents. Sign in with Google to compete."))),
      h("div", { class: "lb-controls" },
        segControl(LB_METRICS.map(([v, l]) => [v, l]), by, (v) => go(lbHref(v, days))),
        segControl(LB_DAYS, days, (v) => go(lbHref(by, v))))),
    h("div", { class: "grid lb-grid" }, lbBoard(lb), lbYouCard(lb)),
    h("p", { class: "muted lb-note" }, t("Each person's Interlatch reports their own totals: hours of active agent time, sessions started and tokens used by that computer's coding agents. Nobody checks them: it's for fun.")));
});
route(/^\/innerlatch\/arena$/, async () => {
  clearTimeout(lbPoll);
  const lb = await api("/api/leaderboard", { by: "arena" }); // the arena ranks by this week's battles, not a total
  lbWatch(lb, "/innerlatch/arena");
  return h("div", { class: "narrow-page wide" },
    h("div", { class: "page-head" }, titled("flag", h("h1", null, t("Arena")),
      h("div", { class: "sub" }, t("Each player's champion duels every other once a week. Wins rank them.")))),
    h("div", { class: "grid lb-grid" }, arenaBoard(lb), h("div", { class: "lb-you arena-side" }, arenaYouCard(lb), arenaRivals(lb))),
    h("p", { class: "muted lb-note" }, t("Each party's sheet comes from its owner's own Interlatch, and every computer works the battles out the same way from the sheets. Nobody checks them: it's for fun.")));
});
// Leaderboard and Arena share a sidebar: what the board ranks by, and the arena
function competeSidebar(box) {
  box.replaceChildren(h("div", { class: "sb-head" }, h("h2", null, t("Leaderboard"))),
    h("div", { class: "sb-scroll" }, h("div", { class: "sb-group" }, t("Ranked by")),
      LB_METRICS.map(([v, label, ic]) => sbRow(label, lbHref(v, "30"), ic, null, ["/innerlatch/leaderboard", "by", v === "hours" ? "" : v])),
      sbRow(t("Arena"), "#/innerlatch/arena", "arena", null, ["/innerlatch/arena"])));
}

// =====================================================================================
// Arena: leaderboard players who opted in publish their party's character sheet (each agent's level, tier, class and
// attribute levels) and every arena party fights every other each week. Battles are worked out on this computer from the
// two sheets and a seed, so both players see the same result (sharing/arena.py); scene.js replays them.
// =====================================================================================
const ARENA_SPECIAL = { "Second wind": t("Second wind"), Focus: t("Focus"), Overdrive: t("Overdrive") };
function arenaPower(agents) { return Object.values(agents || {}).reduce((n, a) => n + (a?.level || 0), 0); }
function arenaRecord(r) { return t("{w}–{l}", { w: fmtNum(r.wins || 0), l: fmtNum(r.losses || 0) }); }
function arenaHref(uid) { return `#/innerlatch/arena/${encodeURIComponent(uid)}`; }
// What entering publishes, said exactly: the sheet (sharing/arena.py) and nothing else
function arenaShares() {
  return t("Entering the arena publishes your party's character sheet: for each of your coding agents, its level, tier, class and six attribute levels, and which agent is your champion, with the name and picture you show on the board and the code that tells your computers apart (a hash, not a name). Nothing else: no lessons, projects, sessions, titles or prompts. Each sync sends it again, and leaving the arena deletes it.");
}
// The arena is champion against champion: each player's chosen agent, by default the highest-level one
function arenaTop(agents) {
  let best = null;
  for (const a of PARTY_AGENTS) if (agents?.[a] && (!best || (agents[a].level || 0) > (agents[best].level || 0))) best = a;
  return best;
}
function arenaChamp(x) { // {agent, level, tier} of a board row or a sheet; `champion` is the agent's id, or the unit itself
  const c = x?.champion, agents = x?.agents || {};
  const agent = (typeof c === "string" ? c : c?.agent) || arenaTop(agents);
  if (!agent) return null;
  const u = (c && typeof c === "object" ? c : null) || agents[agent] || {};
  return { agent, level: u.level || 1, tier: u.tier || "novice" };
}
function champLabel(c) { return `${agentName(c.agent)} · ${t("Lv {n}", { n: c.level })} · ${tierLabel(c.tier)}`; }
function champCell(c) {
  if (!c) return h("span", { class: "arena-champ-cell muted" }, "–");
  return h("span", { class: "arena-champ-cell", title: champLabel(c) },
    partySprite(c.agent, { tier: c.tier, scale: 1, still: true, decorative: true }),
    h("span", { class: "acc-name" }, agentShort(c.agent)), h("span", { class: "acc-lv" }, t("Lv {n}", { n: c.level })));
}
function arenaPicker(me) { // the agents as tiles; the champion lit. The levels: the sheet's, else this computer's party
  const box = h("div", { class: "arena-pick" }, h("span", { class: "px-label" }, t("Your champion")));
  const fill = (agents) => {
    const cur = (typeof me.champion === "string" ? me.champion : me.champion?.agent) || arenaTop(agents);
    const tiles = h("div", { class: "ap-tiles", role: "group", "aria-label": t("Your champion") });
    tiles.append(...PARTY_AGENTS.filter((a) => agents[a]).map((a) => {
      const u = agents[a], on = a === cur;
      return h("button", { type: "button", class: `ap-tile${on ? " on" : ""}`, "aria-pressed": String(on), title: champLabel({ agent: a, ...u }),
        onclick: async () => {
          if (on) return;
          tiles.querySelectorAll("button").forEach((b) => { b.disabled = true; });
          if (await lbPost("/api/leaderboard/arena/champion", { agent: a })) { toast(t("{agent} is your champion now.", { agent: agentName(a) })); render(); }
          else tiles.querySelectorAll("button").forEach((b) => { b.disabled = false; });
        } },
        partySprite(a, { tier: u.tier, scale: 1, still: true, decorative: true }),
        h("span", { class: "apt-name" }, agentShort(a)), h("span", { class: "apt-lv" }, t("Lv {n}", { n: u.level || 1 })));
    }));
    box.append(tiles, h("p", { class: "muted small" }, t("Your champion duels every other player's champion once a week. Until you pick one, it's your highest-level agent.")));
  };
  if (me.agents) fill(me.agents);
  else {
    (partyCache?.signed_in && Date.now() - partyCacheAt < HOME_JOURNEY_FRESH ? Promise.resolve(partyCache) : partyLoad())
      .then((p) => { if (p?.agents) fill(Object.fromEntries(p.agents.map((a) => [a.agent, { level: a.level, tier: a.tier }]))); else box.remove(); })
      .catch(() => box.remove());
  }
  return box;
}
function arenaBoard(lb) {
  if (lb.error) return h("section", { class: "card empty" }, t("Could not reach the leaderboard: {error}", { error: lb.error }));
  if (!lb.rows?.length) {
    return h("section", { class: "card" }, emptyArt("flag", h("b", null, lb.me?.arena ? t("No rivals yet") : t("No one in the arena yet")),
      h("div", { class: "muted" }, lb.me?.arena ? t("Your party shows here after it syncs, and others as they enter.") : t("Enter it to be the first."))));
  }
  const top = Math.max(1, ...lb.rows.map((r) => r.wins || 0));
  const head = h("li", { class: "lb-row lb-head arena-row", "aria-hidden": "true" }, h("span"), h("span"), h("span"),
    h("span", { class: "lb-num on" }, t("Wins–losses")), h("span", { class: "lb-num" }, t("Champion")));
  // a rival's whole row opens your battle with it (the name is the link; CSS stretches it over the row). Your own
  // account is never a rival, also when another of your computers put it in the arena
  const row = (r) => arenaOwn(lb, r) ? arenaRow({ ...r, me: true }, top) : arenaRow(r, top);
  return h("section", { class: "card" },
    cardHead(t("Ranked by wins this week"), { iconName: "party", hint: tn(lb.rows.length, "{n} party", "{n} parties", { n: fmtNum(lb.rows.length) }) }),
    h("ol", { class: "lb-list arena-list" }, head, lb.rows.map(row)),
    h("p", { class: "muted lb-note" }, t("Click a party to watch your battle with it.")));
}
function arenaOwn(lb, r) { return !!r.me || (!!lb.me?.uid && r.uid === lb.me.uid); }
function arenaRow(r, top) {
  return h("li", { class: `lb-row arena-row${r.me ? " me" : " rival"}${r.rank <= 3 ? ` podium p${r.rank}` : ""}`,
    title: [`${t("Wins–losses")}: ${arenaRecord(r)}`, arenaChamp(r) ? `${t("Champion")}: ${champLabel(arenaChamp(r))}` : null].filter(Boolean).join(" · ") },
    h("span", { class: "lb-rank" }, String(r.rank)), lbAvatar(r.uid, r.name, r.photo),
    h("div", { class: "lb-who" },
      h("div", { class: "lb-name" }, r.me ? h("span", null, r.name)
        : h("a", { class: "arena-link", href: arenaHref(r.uid), title: t("Watch your battle with {name}", { name: r.name }) }, r.name),
        r.me ? h("span", { class: "lb-you-tag" }, t("you")) : null),
      h("div", { class: "lb-bar", "aria-hidden": "true" }, h("i", { style: { width: `${Math.max(2, ((r.wins || 0) / top) * 100)}%` } }))),
    h("span", { class: "lb-num on" }, arenaRecord(r)), champCell(arenaChamp(r)));
}
function arenaRivals(lb) { // two above you and two below
  const i = (lb.rows || []).findIndex((r) => arenaOwn(lb, r));
  if (i < 0) return null;
  const near = lb.rows.slice(Math.max(0, i - 2), i + 3).filter((r) => !arenaOwn(lb, r));
  if (!near.length) return null;
  return h("section", { class: "card arena-rivals" }, cardHead(t("Rivals near you"), { iconName: "team" }),
    h("ul", { class: "arena-near" }, near.map((r) => h("li", null,
      h("a", { href: arenaHref(r.uid) }, lbAvatar(r.uid, r.name, r.photo), h("span", { class: "an-name" }, r.name, arenaChamp(r) ? h("small", null, champLabel(arenaChamp(r))) : null),
        h("span", { class: "an-rec" }, arenaRecord(r)), h("span", { class: "an-go" }, t("Watch"), icon("arrow")))))));
}
function arenaYouCard(lb) {
  const me = lb.me || {};
  const head = cardHead(t("Your party"), { iconName: "party" });
  const shares = h("p", { class: "arena-shares" }, arenaShares());
  if (!me.joined) { // the arena is a part of the board: on it first
    const join = me.signed_in
      ? h("button", { class: "btn primary", type: "button", onclick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        if (await lbPost("/api/leaderboard/join")) { toast(t("You're on the board.")); render(); } else btn.disabled = false;
      } }, t("Join the board as {name}", { name: me.name || "" }))
      : h("button", { class: "btn primary", type: "button", onclick: lbSignin }, t("Sign in with Google"));
    return h("section", { class: "card lb-you arena-you" }, head,
      h("p", null, h("b", null, t("Join the board first.")), " ", t("The arena is for people on the leaderboard: join it, then enter the arena.")),
      shares, h("div", { class: "lb-actions" }, join));
  }
  const problem = me.paused ? t("Another computer reports for this account now. Sign in again here to report from this one.")
    : me.error ? t("Syncing with the board failed, so your party isn't updated there: {error}", { error: me.error }) : null;
  const paused = problem ? [h("p", { class: "warn-line" }, problem), // leaving still works from here
    h("div", { class: "lb-actions" }, h("button", { class: "btn primary", type: "button", onclick: lbSignin }, t("Sign in again")))] : null;
  if (me.paused && !me.arena) {
    return h("section", { class: "card lb-you arena-you" }, head,
      h("p", { class: "warn-line" }, t("Another computer reports for this account now. Sign in again here to report from this one.")),
      h("div", { class: "lb-actions" }, h("button", { class: "btn primary", type: "button", onclick: lbSignin }, t("Sign in again"))));
  }
  if (!me.arena) {
    return h("section", { class: "card lb-you arena-you" }, head,
      h("p", null, t("Enter the arena and your champion duels every other player's champion once a week. Wins rank you here.")),
      shares,
      h("div", { class: "lb-actions" }, h("button", { class: "btn primary", type: "button", onclick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        if (await lbPost("/api/leaderboard/arena/join")) { toast(t("Your party entered the arena.")); render(); } else btn.disabled = false;
      } }, t("Enter the arena"))));
  }
  const mine = (lb.rows || []).find((r) => r.me);
  return h("section", { class: "card lb-you arena-you" }, head,
    h("p", null, t("Your party is in the arena.")),
    paused,
    mine ? h("div", { class: "lb-mine arena-mine" }, fact(t("rank"), `#${mine.rank}`), fact(t("wins–losses"), arenaRecord(mine)))
      : problem ? null : h("p", { class: "muted" }, t("Your party appears here after the next sync.")),
    arenaPicker({ ...mine, ...me, champion: me.champion || mine?.champion }),
    h("details", { class: "arena-what" }, h("summary", null, t("What it shares")), shares),
    h("div", { class: "lb-actions" }, h("button", { class: "btn", type: "button", onclick: async () => {
      if (!confirm(t("Leave the arena? Your party's sheet is deleted and it stops fighting. You stay on the leaderboard."))) return;
      if (await lbPost("/api/leaderboard/arena/leave")) { toast(t("You left the arena.")); render(); }
    } }, t("Leave the arena"))));
}
// the log in words: the replay's text twin, and all there is without scene.js
function arenaUnitName(id, names) {
  const [side, agent] = String(id || "").split(":");
  return t("{agent} ({who})", { agent: agentShort(agent), who: names[side] || side });
}
function arenaLogLine(ev, names) {
  const src = arenaUnitName(ev.src, names), dst = arenaUnitName(ev.dst, names), n = fmtNum(ev.n || 0);
  if (ev.t === "hit") return t("{src} hits {dst} for {n}", { src, dst, n });
  if (ev.t === "crit") return t("{src} lands a critical hit on {dst} for {n}", { src, dst, n });
  if (ev.t === "heal") return t("{src} heals {dst} for {n}", { src, dst, n });
  if (ev.t === "special") return t("{src} uses {name}", { src, name: ARENA_SPECIAL[ev.name] || ev.name || "" });
  if (ev.t === "ko") return t("{dst} is down", { dst });
  if (ev.t === "end") return t("The battle ends");
  return ev.t;
}
function arenaLog(result, names, open) {
  const log = result?.log || [];
  return h("details", { class: "card arena-log", open }, h("summary", null, h("b", null, t("Battle log")), " ",
    h("span", { class: "muted" }, tn(log.length, "{n} event", "{n} events", { n: fmtNum(log.length) }))),
    h("ol", { class: "arena-log-list" }, log.map((ev) => h("li", { class: `al-${ev.t}` },
      h("span", { class: "al-r" }, t("Round {n}", { n: ev.r })), h("span", null, arenaLogLine(ev, names))))));
}
function arenaFighter(side, c, label, mine) { // one champion, large, with its player
  return h("div", { class: `arena-fighter${mine ? " mine" : ""}` },
    h("span", { class: "af-stage px-stage" }, c ? partySprite(c.agent, { tier: c.tier, scale: matchMedia("(max-width: 560px)").matches ? 2 : 3, still: true, label: champLabel(c) }) : null),
    h("div", { class: "af-text" }, h("span", { class: "px-label" }, label),
      c ? h("b", null, agentName(c.agent)) : null,
      c ? h("div", { class: "af-lv" }, lvBadge(c.level), tierBadge(c.tier)) : null,
      h("div", { class: "af-who" }, lbAvatar(side.uid, side.name, side.photo), h("span", null, side.name || "–"))));
}
function arenaTeam(side, label, mine) {
  const agents = side.agents || {};
  return h("div", { class: `arena-team small${mine ? " mine" : ""}` },
    h("div", { class: "at-head" }, lbAvatar(side.uid, side.name, side.photo),
      h("div", null, h("span", { class: "px-label" }, label), h("b", null, side.name)),
      h("span", { class: "at-power" }, h("small", null, t("Power")), h("b", null, fmtNum(arenaPower(agents))))),
    h("ul", { class: "at-units" }, PARTY_AGENTS.filter((a) => agents[a]).map((a) => {
      const u = agents[a];
      return h("li", { title: `${agentName(a)} · ${t("Lv {n}", { n: u.level })} · ${tierLabel(u.tier)}` },
        h("span", { class: "at-sprite" }, partySprite(a, { tier: u.tier, scale: 1, still: true, decorative: true })),
        h("span", { class: "at-name" }, agentShort(a)), h("span", { class: "at-lv" }, t("Lv {n}", { n: u.level })));
    })));
}
route(/^\/innerlatch\/arena\/([\w-]+)$/, async (params, uid) => {
  const token = renderSeq;
  let b;
  try { b = await api("/api/leaderboard/arena/battle", { uid }); } catch (e) {
    if (e.handled) throw e;
    return h("div", { class: "narrow-page wide" }, h("section", { class: "card" }, emptyArt("flag", h("b", null, t("No battle to show")),
      h("div", { class: "muted" }, e.status === 404 ? t("This party, or yours, isn't in the arena this week.")
        : t("Could not reach the arena: {error}", { error: e.message })), // 502: Firestore out of reach
      h("a", { class: "btn small", href: "#/innerlatch/arena" }, t("Back to the arena")))));
  }
  const me = b.me || {}, rival = b.rival || {}, result = b.result || {};
  setCrumbs([["Innerlatch", "#/innerlatch"], [t("Arena"), "#/innerlatch/arena"], [rival.name || "–"]], token);
  // the two sides are the uids in order (a = the smaller), the same on both players' computers; yours is on the left
  const mySide = result.a === me.uid ? "a" : result.b === me.uid ? "b" : String(me.uid) < String(rival.uid) ? "a" : "b";
  const theirSide = mySide === "a" ? "b" : "a";
  const won = result.winner === mySide;
  const names = { [mySide]: t("you"), [theirSide]: rival.name || "–" };
  const myC = arenaChamp(me), theirC = arenaChamp(rival);
  const mine = myC ? t("Your {agent}", { agent: agentShort(myC.agent) }) : t("you");
  const theirs = theirC ? t("{name}'s {agent}", { name: rival.name || "–", agent: agentShort(theirC.agent) }) : rival.name || "–";
  const outcome = t("{winner} won in {n} rounds", { winner: won ? mine : theirs, n: result.rounds || 0 });
  const only = (side, c) => (c && side.agents?.[c.agent] ? { [c.agent]: side.agents[c.agent] } : side.agents || {});
  const height = matchMedia("(max-width: 600px)").matches ? 200 : 240;
  const host = h("div", { class: "arena-stage", style: { height: `${height}px` } });
  const replay = h("button", { class: "btn small", type: "button", disabled: true }, icon("again"), t("Replay"));
  const S = window.Scene, live = S && typeof S.mountBattle === "function";
  const page = h("div", { class: "narrow-page wide arena-page" },
    h("div", { class: "page-head" }, h("div", null, h("h1", null, t("{a} vs {b}", { a: mine, b: theirs })),
      h("div", { class: "sub" }, b.week ? t("Arena, week {week}", { week: b.week }) : t("Arena"))),
      h("div", { class: "head-actions" }, h("a", { class: "btn small", href: "#/innerlatch/arena" }, icon("left"), t("Back to the arena")), live ? replay : null)),
    h("div", { class: "arena-duel" }, arenaFighter(me, myC, t("Your champion"), true), h("span", { class: "arena-vs", "aria-hidden": "true" }, "VS"),
      arenaFighter(rival, theirC, t("Rival's champion"), false)), // the name is on the card's own line: a long one won't wrap the label
    live ? h("section", { class: "card arena-battle px-frame" }, host) : null,
    h("p", { class: `arena-result ${won ? "won" : "lost"}`, role: "status", hidden: live && !stillMotion() }, h("b", null, won ? t("Victory") : t("Defeat")), " · ", outcome),
    arenaLog(result, names, !live),
    h("h2", { class: "arena-parties-head" }, t("The two parties")),
    h("div", { class: "arena-teams" }, arenaTeam(me, t("Your party"), true), h("span", { class: "arena-vs small", "aria-hidden": "true" }, "VS"),
      arenaTeam(rival, t("Rival"), false)),
    h("p", { class: "muted lb-note" }, t("Both players' computers work the battle out the same way from the two published sheets, so you both see this result. Self-reported numbers: it's a game, not a record.")));
  if (live) {
    whenShown(host, () => {
      try {
        const sc = S.mountBattle(host, {
          // the players' names: the scene adds each champion's own
          left: { name: me.name || t("you"), uid: me.uid, side: mySide, agents: only(me, myC), champion: myC?.agent },
          right: { name: rival.name || "–", uid: rival.uid, side: theirSide, agents: only(rival, theirC), champion: theirC?.agent },
          leftSide: mySide, result, height, reducedMotion: stillMotion(), // the scene fills the {placeholders}
          labels: { victory: t("Victory"), defeat: t("Defeat"), round: t("Round {n}"), crit: t("Critical!"), ko: t("Down!"),
            special: t("{agent}: {name}"), summary: outcome }, // "Your Claude won in 4 rounds", as the page says it
          onEnd: () => { replay.disabled = false; page.querySelector(".arena-result")?.removeAttribute("hidden"); }, // no spoilers before the end
        });
        replay.disabled = false;
        replay.addEventListener("click", () => { try { sc?.replay?.(); } catch (e) { /* the log has it all */ } });
      } catch (e) {
        host.closest(".arena-battle")?.remove();
        page.querySelector(".arena-log")?.setAttribute("open", "");
        page.querySelector(".arena-result")?.removeAttribute("hidden");
      }
    });
  }
  return page;
});

// =====================================================================================
// Party: each coding agent is a pixel-art character that levels up from the lessons its sessions produced, matched to
// the modules of the career roadmap (knowledge/party.py). It opens once the person signs in with Google (the
// leaderboard's account) or is a signed-in hub person. A level says what the lessons cover, never the person's own.
// The sprites and pixel icons come from sprites.js; without it, each agent shows its initial on its colour.
// =====================================================================================
const PARTY_AGENTS = ["claude", "codex", "copilot", "bob", "antigravity", "opencode"];
const PARTY_TIER = { novice: t("Novice"), adept: t("Adept"), expert: t("Expert"), master: t("Master") };
const PARTY_COLOR = { claude: "#d97757", codex: "#14b88c", copilot: "#8b6cf0", bob: "#0f62fe", antigravity: "#e0a100",
  opencode: "#d1497b" }; // until Sprites.color
// the line icon that stands in for each pixel icon while sprites.js is missing
const PX_STANDIN = { craft: "tool", data: "data", ops: "cloud", guard: "shield", arcana: "sparkles", lore: "knowledge", quest: "ring",
  "quest-done": "completed", "quest-evidence": "partial", star: "sparkles", xp: "zap", lock: "shield", party: "party", journey: "history" };
const PARTY_SHOWN_EVENTS = 40;
let partyCache = null, partyCacheAt = 0, partyInflight = null, partyPoll = null;
function sprites() { return window.Sprites && typeof window.Sprites.draw === "function" ? window.Sprites : null; }
function stillMotion() { return document.documentElement.dataset.motion === "off"; } // Appearance › Animations, or Reduce motion
function partyColor(a) {
  try { return sprites()?.color?.(a) || PARTY_COLOR[a] || "#8a94a3"; } catch (e) { return PARTY_COLOR[a] || "#8a94a3"; }
}
function partyDay(day) { return day ? `${String(day).slice(0, 10)}T12:00:00` : null; } // a YYYY-MM-DD at local noon: no time zone moves it
function tierLabel(tier) { return PARTY_TIER[tier] || PARTY_TIER.novice; }
function tierOf(level) { return level >= 25 ? "master" : level >= 12 ? "expert" : level >= 5 ? "adept" : "novice"; } // party.py's tiers
function pctOf(v) { const pc = (v || 0) * 100; return `${pc > 0 && pc < 10 ? (Math.round(pc * 10) / 10).toFixed(1) : Math.round(pc)}%`; } // 97%, 3.3%
function whenShown(node, fn, tries = 600) { // a page's nodes join the document once render() has all of them
  if (node.isConnected) { fn(); return; }
  if (tries > 0) requestAnimationFrame(() => whenShown(node, fn, tries - 1));
}
// An agent's sprite, idling unless animations are off. `label` names it for screen readers; `decorative` when the text
// beside it already does.
function partySprite(agent, { tier = "novice", scale = 4, locked = false, still = false, label = null, decorative = false } = {}) {
  const SP = sprites(), size = (SP?.SIZE || 32) * scale;
  const named = decorative ? { "aria-hidden": "true" } : { role: "img", "aria-label": label || agentName(agent) };
  const standIn = () => h("span", { class: `px-sprite px-standin${locked ? " locked" : ""}`, ...named,
    style: { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.46)}px`, "--ac": partyColor(agent) } },
    (AGENT_SHORT[agent] || agent || "?").slice(0, 1));
  if (!SP) return standIn();
  const c = h("canvas", { class: "px-sprite", width: size, height: size, ...named });
  try { SP.draw(c, { agent, tier, frame: 0, scale, locked }); } catch (e) { return standIn(); }
  if (!still && !stillMotion() && typeof SP.animate === "function") {
    whenShown(c, () => { try { SP.animate(c, { agent, tier, scale, locked }); } catch (e) { /* the first frame stays */ } });
  }
  return c;
}
function pxIcon(name, scale = 1) { // a 16×16 pixel icon from sprites.js, or the line icon of the same idea
  const SP = sprites();
  if (SP && typeof SP.icon === "function") {
    const c = h("canvas", { class: "px-icon", width: 16 * scale, height: 16 * scale, "aria-hidden": "true" });
    try { // an icon it doesn't know leaves the canvas empty: the line icon then
      SP.icon(c, name, scale);
      if (c.width && c.getContext("2d").getImageData(0, 0, c.width, c.height).data.some((v, i) => i % 4 === 3 && v)) return c;
    } catch (e) { /* the line icon */ }
  }
  const el = icon(PX_STANDIN[name] || "dot", "px-icon px-icon-line");
  setStyle(el, { width: `${16 * scale}px`, height: `${16 * scale}px` });
  return el;
}
function partySparkle(canvas) {
  if (!canvas || stillMotion() || canvas.tagName !== "CANVAS") return;
  try { sprites()?.sparkle?.(canvas); } catch (e) { /* only a flourish */ }
}

// ------------------------------------------------------------------ the payload: one request for the page and the sidebar
function partyLoad() {
  partyInflight ||= api("/api/party").then((p) => { partyKeep(p); return p; }).finally(() => { partyInflight = null; });
  return partyInflight;
}
function partyKeep(p) { partyCache = p; partyCacheAt = Date.now(); } // a POST answers with the fresh payload too
async function partyGet() { // a page's payload; the sidebar follows a sign-in or a sign-out
  const was = partyCache?.signed_in;
  let p;
  try { p = await partyLoad(); } catch (e) {
    if (e.status !== 403) throw e;
    p = { signed_in: false, signing_in: false, can_sign_in: false, error: e.message }; // refused here: say how it opens
  }
  if (was != null && was !== p.signed_in && sbSection === "party") buildSidebar("party");
  if (p.signed_in) clearTimeout(partyPoll);
  return p;
}
function partyRefresh(p) { // after a sign-out, a quest or a goal: the page and the sidebar from the new payload
  if (p) partyKeep(p); else partyCache = null;
  if (sbSection === "party") buildSidebar("party");
}

// ------------------------------------------------------------------ pieces
function partyShell(title, sub, ...body) {
  return h("div", { class: "party-page" },
    title ? h("div", { class: "page-head" }, h("div", { class: "head-title" }, pxIcon("party", 3),
      h("div", null, h("h1", null, title), sub ? h("div", { class: "sub" }, sub) : null))) : null,
    body,
    h("p", { class: "party-foot" }, icon("fact"),
      h("span", null, t("Levels show what your agents' lessons cover on the roadmap. They aren't your own level: the roadmap keeps that separate."))));
}
function lvBadge(level, big = false) {
  return h("span", { class: `px-lv${big ? " big" : ""}` }, h("small", null, t("Lv")), h("b", null, String(level)));
}
function tierBadge(tier) { return h("span", { class: `px-tier t-${PARTY_TIER[tier] ? tier : "novice"}` }, tierLabel(tier)); }
function partyLabel(a) { return t("{agent}, level {n} {tier}", { agent: a.name, n: a.level, tier: tierLabel(a.tier).toLowerCase() }); }
// XP within the current level, in gold segments
function xpBar(xp, from, to, level, { small = false } = {}) {
  const span = Math.max(1, to - from), into = Math.max(0, Math.min(span, xp - from));
  return h("div", { class: `xp${small ? " small" : ""}` },
    h("div", { class: "xp-bar", role: "progressbar", "aria-label": t("XP toward level {n}", { n: level + 1 }),
      "aria-valuemin": String(from), "aria-valuemax": String(to), "aria-valuenow": String(Math.min(Math.max(xp, from), to)) },
      h("i", { style: { width: `${(into / span) * 100}%` } })),
    small ? null : h("div", { class: "xp-text" }, h("span", null, t("{n} XP", { n: fmtNum(xp) })),
      h("span", null, t("{n} XP to Lv {level}", { n: fmtNum(Math.max(0, to - xp)), level: level + 1 }))));
}
// a level as a bar: its length is the level printed beside it, against the highest level in view
function lvBar(level, max) {
  return h("span", { class: "pa-bar", "aria-hidden": "true" }, h("i", { style: { width: `${level && max ? Math.max(3, (level / max) * 100) : 0}%` } }));
}
// the six attributes; `max`: the highest attribute level in the whole party, so the cards compare
// `big`: the character page's, with each attribute's XP printed and what it covers on hover
function attrBars(a, attrs, max, { big = false } = {}) {
  return h("ul", { class: `px-attrs${big ? " big" : ""}` }, attrs.map((at) => {
    const v = a.attrs?.[at.id] || 0, lv = a.attr_levels?.[at.id] || 0;
    const tip = [`${at.name}: ${lv ? t("Lv {n}", { n: lv }) : t("not covered yet")} · ${t("{n} XP", { n: fmtNum(v) })}`, at.about].filter(Boolean).join("\n");
    return h("li", { title: tip }, pxIcon(at.id, 1),
      h("span", { class: "pa-name" }, at.name),
      lvBar(lv, max),
      h("span", { class: "pa-lv" }, lv ? t("Lv {n}", { n: lv }) : "–", big ? h("small", null, t("{n} XP", { n: fmtNum(v) })) : null));
  }));
}
function partyMax(p) { return Math.max(1, ...p.agents.flatMap((a) => Object.values(a.attr_levels || {}))); }
function questTrack(qs) { // one square a quest: done, evidence found, open (the text beside it says the counts)
  return h("span", { class: "qtrack", "aria-hidden": "true" }, qs.map((q) => h("i", { class: `qt-${q.status}` })));
}
function questCounts(qs) {
  const done = qs.filter((q) => q.status === "done").length, found = qs.filter((q) => q.status === "evidence").length;
  return [t("{a} of {b} done", { a: done, b: qs.length }), found ? tn(found, "{n} with possible evidence", "{n} with possible evidence") : null].filter(Boolean).join(" · ");
}

// ------------------------------------------------------------------ who's hiring: job-market numbers beside the classes
// (sharing/market.py): open technical roles per roadmap track on the job boards careers/ reads, worldwide and in Japan.
// Public numbers, nothing about the person; each payload's `market` holds the tracks it shows, null when there are none.
const MARKET_SMALL = 500; // fewer Japanese postings than this in the sample: Japan's numbers say it is a small sample
function sharePct(v) { const pc = (v || 0) * 100; return pc >= 10 ? String(Math.round(pc)) : String(Math.round(pc * 10) / 10); } // 12, 3, 0.4
function hiringText(c) {
  return tn(c.global, "{n} open role · {share}% of demand", "{n} open roles · {share}% of demand", { n: fmtNum(c.global), share: sharePct(c.share_global) });
}
function hiringJapan(m, c) { // nothing while Japan's sample is too small to read (the market note says so)
  if (!m?.sample?.japan || m.sample.japan < MARKET_SMALL) return null;
  return t("Japan: {n}", { n: fmtNum(c.japan) });
}
// a class block's "Hiring" line; `compact`: one line, for a track in a list
function partyHiring(m, id, { compact = false } = {}) {
  const c = id ? m?.classes?.[id] : null;
  if (!c) return null;
  const ja = hiringJapan(m, c);
  if (compact) return h("div", { class: "pcl-hire" }, icon("hiring"), h("span", null, [hiringText(c), ja].filter(Boolean).join(" · ")));
  return h("div", { class: "pc-class pc-hire" }, h("span", { class: "px-label" }, t("Hiring")), h("span", null, hiringText(c)),
    ja ? h("span", { class: "muted" }, ja) : null);
}
// where the numbers come from, how fresh they are, and whether a test run made them
function marketNote(m) {
  if (!m) return null;
  const vars = { n: fmtNum(m.sample?.global), boards: fmtNum(m.sample?.boards), days: m.window_days, date: m.as_of ? fmtDateY(partyDay(m.as_of)) : "–" };
  const small = m.sample?.japan && m.sample.japan < MARKET_SMALL ? t("Japan's numbers stay hidden until its sample is bigger: {n} postings so far.", { n: fmtNum(m.sample.japan) }) : null;
  return h("p", { class: "party-market" }, icon("hiring"),
    m.status === "test" ? h("span", { class: "px-test", title: t("A test run of the pipeline made these numbers: don't rely on them yet.") }, t("Test data")) : null,
    h("span", null, [m.window_days ? t("Hiring numbers: {n} open technical roles on {boards} companies' job boards over the last {days} days, as of {date}.", vars)
      : t("Hiring numbers: {n} open technical roles on {boards} companies' job boards, as of {date}.", vars),
    t("A share of demand counts each company's postings with less weight the more it has, so one big hirer can't swamp it."),
    small, m.source === "bundled" ? t("From the copy that came with Interlatch.") : null].filter(Boolean).join(LANG === "ja" ? "" : " ")));
}

// ------------------------------------------------------------------ the sign-in gate
async function partySignin() { await openSignin("/api/party/signin"); }
function partyGate(p, seq = renderSeq) {
  clearTimeout(partyPoll);
  if (p.signing_in) { // the sign-in runs in another tab: look again now and then
    partyPoll = setTimeout(() => { if (seq === renderSeq && inRealm(parseHash().path)) render(); }, 3000);
  }
  return partyShell(t("Party"), t("Your coding agents, as a party of characters."),
    h("section", { class: "card party-gate px-frame" },
      h("div", { class: "pg-stage px-stage" },
        h("div", { class: "pg-sleepers", "aria-hidden": "true" }, PARTY_AGENTS.map((a) => partySprite(a, { scale: 3, locked: true, decorative: true }))),
        h("span", { class: "pg-lock" }, pxIcon("lock", 2))),
      h("div", { class: "pg-body" },
        h("h2", null, t("Sign in with Google to meet your party")),
        h("p", null, t("Each of your coding agents is a character here. It levels up from the lessons its sessions produced, matched to the modules of a career roadmap, and takes the class of the roadmap track it covers most.")),
        p.can_sign_in ? [
          h("p", { class: "muted" }, t("It's the leaderboard's Google account, but signing in here doesn't put you on the board.")),
          h("div", { class: "lb-actions" }, h("button", { class: "btn primary", type: "button", onclick: partySignin }, t("Sign in with Google")),
            p.signing_in ? h("button", { class: "btn", type: "button", onclick: async () => { if (await lbPost("/api/leaderboard/cancel")) render(); } }, t("Cancel")) : null),
          p.signing_in ? h("div", { class: "muted lb-wait" }, t("Finish signing in on the page that opened. This page carries on by itself once you have.")) : null,
        ] : h("p", { class: "warn-line" }, t("Signing in happens on the computer that runs Interlatch: open Innerlatch there and sign in with Google. On a team hub, sign in to the hub as one of its people.")),
        p.error ? h("p", { class: "muted small" }, p.error) : null)));
}

// ------------------------------------------------------------------ #/innerlatch: the leader and the characters
async function partySignout(p) {
  if (!confirm(p.who?.board ? t("Sign out of the Party? This also takes you off the leaderboard: your row there is deleted.")
    : t("Sign out of the Party? Your agents keep their levels: sign in again to see them."))) return;
  const r = await lbPost("/api/party/signout");
  if (!r) return;
  partyRefresh(r);
  toast(t("Signed out of the Party."));
  leaveRealm(); // Innerlatch opens to the signed in only
}
function partyLeader(p) {
  const who = p.who || {}, main = (p.quests || []).filter((q) => q.main);
  const out = who.kind === "google" && ME?.viewer?.here !== false
    ? h("button", { class: "btn small", type: "button", onclick: () => partySignout(p) }, t("Sign out")) : null;
  return h("section", { class: "card party-leader px-frame" },
    h("div", { class: "pl-who" }, lbAvatar(who.uid, who.name, who.photo),
      h("div", null, h("h2", null, t("{name}'s party", { name: who.name || "" })),
        h("div", { class: "muted small" }, who.kind === "hub" ? t("Signed in to this hub") : t("Signed in with Google")))),
    h("a", { class: "pl-quest", href: "#/innerlatch/quests" }, pxIcon("quest", 2),
      h("div", { class: "pl-quest-text" }, h("span", { class: "px-label" }, t("Main quest")),
        p.goal ? [h("b", null, p.goal.name), h("div", { class: "pl-progress" }, questTrack(main), h("span", { class: "muted small" }, questCounts(main)))]
          : h("b", null, t("No main quest yet: pick a goal track")))),
    out);
}
// the class, or while there is none (few lessons, or low coverage) "No class yet" and the track it leans towards
function partyClassLine(a, { category = false } = {}) {
  const lean = !a.class && a.classes?.length ? a.classes[0] : null;
  return h("div", { class: "pc-class" }, h("span", { class: "px-label" }, t("Class")),
    a.class ? [h("b", null, a.class.name), h("span", { class: "muted" }, [category ? a.class.category_name : null,
      t("track {pct} covered", { pct: pctOf(a.class.coverage) })].filter(Boolean).join(" · "))]
      : [h("b", null, t("No class yet")), lean ? h("span", { class: "muted" }, t("Leaning toward {track}", { track: lean.name })) : null]);
}
function partyRecruit(a) { return !a.counted; } // no lesson of its counts yet
function partyCard(a, attrs, max, market) {
  const recruit = partyRecruit(a);
  return h("a", { class: `party-card px-frame${recruit ? " recruit" : ""}`, href: `#/innerlatch/agent/${a.agent}`, style: { "--ac": partyColor(a.agent) } },
    h("div", { class: "pc-stage px-stage" }, partySprite(a.agent, { tier: a.tier, scale: 4, decorative: true }), tierBadge(a.tier)),
    h("div", { class: "pc-head" }, h("b", { class: "pc-name" }, a.name), lvBadge(a.level)),
    xpBar(a.xp, a.level_xp, a.next_xp, a.level),
    recruit ? h("div", { class: "pc-recruit" }, h("b", null, tc("party", "No lessons yet")), h("span", null, t("A recruit: it levels up once its sessions produce lessons.")))
      : [partyClassLine(a), partyHiring(market, a.class?.id),
        attrBars(a, attrs, max),
        a.skills?.length ? h("div", { class: "pc-top" }, h("span", { class: "px-label" }, t("Top skills")),
          h("ol", { class: "pc-skills" }, a.skills.slice(0, 3).map((sk) => h("li", null, h("span", { title: sk.name }, sk.name), h("b", null, t("Lv {n}", { n: sk.level })))))) : null]);
}
route(/^\/innerlatch$/, async () => {
  const token = renderSeq;
  const p = await partyGet();
  if (!p.signed_in) return partyGate(p, token);
  return partyShell(t("Party"), t("Your coding agents as characters. Each levels up from the lessons its sessions produced, matched to the roadmap."),
    partyLeader(p), marketNote(p.market), h("div", { class: "party-grid" }, p.agents.map((a) => partyCard(a, p.attrs, partyMax(p), p.market))),
    h("p", { class: "muted small party-levels" }, t("Three kinds of level: a character's comes from all its XP, an attribute's from the XP of the skills under it, and a skill's from one roadmap module.")));
});

// ------------------------------------------------------------------ #/innerlatch/quests: the quest log and the goal
function partyEvidence(e) {
  const meta = h("span", { class: "muted" }, [e.agent ? agentShort(e.agent) : null, e.at ? fmtDate(partyDay(e.at)) : null].filter(Boolean).join(" · "));
  if (e.kind === "artifact") {
    const u = safeUrl(e.url); // only a web address opens: never a file path, a javascript: or a data: link
    return h("li", null, icon(ICONS[e.artifact_kind] ? e.artifact_kind : "artifacts"),
      u ? h("a", { href: u, target: "_blank", rel: "noopener", title: e.title || u }, e.title || u) : h("span", { title: e.title }, e.title), meta);
  }
  return h("li", null, icon("learning"),
    e.id ? lessonLink(e.id, e.title, { title: e.title }) : h("span", { title: e.title }, e.title), meta);
}
function partyQuest(q, draw, next = false) { // `next`: the main quest's first one with evidence, the one button in colour
  const [ic, label] = { done: ["quest-done", t("Done")], evidence: ["quest-evidence", t("Possible evidence")] }[q.status] || ["quest", tc("quest", "Open")];
  const btn = h("button", { class: `btn small${next ? " primary" : ""}`, type: "button", onclick: async () => {
    btn.disabled = true;
    const done = q.status !== "done";
    const r = await lbPost("/api/party/quest", { id: q.id, done });
    if (!r) { btn.disabled = false; return; }
    partyRefresh(r);
    if (done) toast(t("Quest complete: {text}", { text: q.text }));
    draw(r, { sparkle: done ? q.id : null, focus: q.id });
  } }, q.status === "done" ? t("Undo") : t("Mark done"));
  return h("li", { class: `quest qs-${q.status}`, "data-quest": q.id },
    h("span", { class: "q-icon" }, pxIcon(ic, 2)),
    h("div", { class: "q-body" },
      h("div", { class: "q-top" }, h("span", { class: "px-label" }, t("Milestone {n}", { n: q.n })), h("span", { class: `q-status s-${q.status}` }, label),
        q.done_at ? h("span", { class: "muted small" }, t("on {date}", { date: fmtDateY(q.done_at) })) : null),
      h("div", { class: "q-text" }, q.text),
      q.evidence?.length ? h("ul", { class: "q-evidence", "aria-label": t("Evidence") }, q.evidence.map(partyEvidence))
        : q.status === "open" ? h("div", { class: "muted small q-none" }, t("Nothing in your sessions matches it yet.")) : null),
    h("div", { class: "q-act" }, btn));
}
function partyGoal(p, draw, tracksP) {
  const sel = h("select", { "aria-label": t("Goal track"), disabled: true }, h("option", null, t("Loading…")));
  const choose = async (track, btn) => {
    btn.disabled = true;
    const r = await lbPost("/api/party/goal", { track });
    btn.disabled = false;
    if (!r) return;
    partyRefresh(r);
    toast(track ? t("Your main quest now follows {track}.", { track: r.goal?.name || track }) : t("Back to the default goal."));
    draw(r, { goal: true });
  };
  const set = h("button", { class: "btn small primary", type: "button", disabled: true, onclick: () => choose(sel.value, set) }, t("Set as goal"));
  const sync = () => { set.disabled = sel.disabled || !sel.value || sel.value === p.goal?.id; };
  sel.addEventListener("change", sync);
  // signed in, each track comes with its open roles: sorted by category (the roadmap's order) or by demand
  const sort = h("select", { class: "pq-sort", "aria-label": t("Sort tracks"), hidden: true },
    h("option", { value: "category" }, t("By category")), h("option", { value: "demand" }, t("By demand")));
  const label = (tr) => (tr.global == null ? tr.name
    : tn(tr.global, "{track} · {n} open role", "{track} · {n} open roles", { track: tr.name, n: fmtNum(tr.global) }));
  let filled = false;
  const fill = (r) => { // the choice so far stays chosen
    const keep = filled ? sel.value : p.goal?.id || "", option = (tr) => h("option", { value: tr.id, selected: tr.id === keep }, label(tr));
    const cats = r.categories || [];
    sel.replaceChildren(...(keep ? [] : [h("option", { value: "", selected: true }, t("Choose a track…"))]),
      ...(sort.value === "demand"
        ? cats.flatMap((c) => c.tracks).sort((a, b) => (b.global ?? -1) - (a.global ?? -1) || a.name.localeCompare(b.name, LOCALE)).map(option)
        : cats.map((c) => h("optgroup", { label: c.name }, c.tracks.map(option)))));
    filled = true;
  };
  tracksP.then((r) => {
    if (r.error) { sel.replaceChildren(h("option", null, t("Could not load: {error}", { error: r.error }))); return; }
    sort.hidden = !(r.categories || []).some((c) => c.tracks.some((tr) => tr.global != null));
    sort.addEventListener("change", () => { fill(r); sync(); });
    fill(r);
    sel.disabled = false;
    sync();
  });
  const lead = p.agents.find((a) => a.class); // the default: the class of the highest-level agent that has one
  const reset = p.goal?.chosen ? h("button", { class: "btn small", type: "button", onclick: () => choose(null, reset) },
    lead ? t("Use {agent}'s class ({track})", { agent: lead.short, track: lead.class.name }) : t("Back to the default")) : null;
  const row = h("div", { class: "pq-goal-row" }, sel, sort, set, reset);
  if (!p.goal) { // no main quest yet: the picker is the main quest's card
    return h("section", { class: "card pq-main pq-goal px-frame" },
      h("div", { class: "pq-head" }, pxIcon("quest", 2),
        h("div", { class: "pq-title" }, h("span", { class: "px-label" }, t("Main quest")), h("h2", null, t("No main quest yet: pick a goal track")))),
      h("p", { class: "muted" }, t("Pick the roadmap track you're working toward: its four milestones become your main quest, and Interlatch looks for evidence of each in your sessions.")),
      row, marketNote(p.market));
  }
  return h("section", { class: "card pq-goal" }, cardHead(t("Your goal"), { iconName: "pin" }),
    h("p", { class: "muted" }, p.goal.chosen ? t("You picked {track}: its four milestones are your main quest. Pick another track to change it.", { track: p.goal.name })
      : t("Pick the roadmap track you're working toward: its four milestones become your main quest. Until you pick one, it's the class of your highest-level agent.")),
    row, marketNote(p.market));
}
function partyQuests(p, draw, tracksP) {
  const quests = p.quests || [], main = quests.filter((q) => q.main), side = [];
  const next = main.find((q) => q.status === "evidence")?.id;
  for (const q of quests.filter((x) => !x.main)) {
    let g = side.find((x) => x.id === q.track);
    if (!g) side.push(g = { id: q.track, name: q.track_name, quests: [] });
    g.quests.push(q);
  }
  return [
    p.goal ? null : partyGoal(p, draw, tracksP),
    p.goal ? h("section", { class: "card pq-main px-frame" },
      h("div", { class: "pq-head" }, pxIcon("quest", 2),
        h("div", { class: "pq-title" }, h("span", { class: "px-label" }, t("Main quest")), h("h2", null, p.goal.name),
          h("div", { class: "muted small" }, p.goal?.chosen ? t("The goal you picked") : t("Your highest-level agent's class, until you pick a goal")),
          partyHiring(p.market, p.goal.id, { compact: true })),
        h("div", { class: "pq-progress" }, questTrack(main), h("span", null, questCounts(main)))),
      main.length ? h("ol", { class: "quests" }, main.map((q) => partyQuest(q, draw, q.id === next))) : h("div", { class: "empty" }, t("No quests yet"))) : null,
    side.length ? h("section", { class: "card pq-side" }, cardHead(t("Side quests"), { iconName: "branch", hint: t("From your agents' classes, other than your goal") }),
      side.map((g) => h("div", { class: "pq-track" }, h("h3", null, g.name,
        h("span", { class: "muted pq-whose" }, p.agents.filter((a) => a.class?.id === g.id).map((a) => a.short).join(", "))),
        partyHiring(p.market, g.id, { compact: true }),
        h("ol", { class: "quests" }, g.quests.map((q) => partyQuest(q, draw)))))) : null,
    p.goal ? partyGoal(p, draw, tracksP) : null,
  ];
}
route(/^\/innerlatch\/quests$/, async () => {
  const token = renderSeq;
  const p = await partyGet();
  if (!p.signed_in) return partyGate(p, token);
  const tracksP = api("/api/party/tracks").catch((e) => ({ error: e.message, categories: [] }));
  const body = h("div", { class: "pq" });
  const draw = (payload, { sparkle = null, focus = null, goal = false } = {}) => { // redrawn from a POST's answer: focus stays put
    body.replaceChildren(...partyQuests(payload, draw, tracksP).filter(Boolean));
    const quest = (id) => [...body.querySelectorAll("[data-quest]")].find((li) => li.dataset.quest === id);
    if (sparkle) partySparkle(quest(sparkle)?.querySelector(".q-icon canvas"));
    if (focus) quest(focus)?.querySelector(".q-act button")?.focus();
    if (goal) tracksP.then(() => body.querySelector(".pq-goal select")?.focus()); // after the picker has its tracks
  };
  draw(p);
  return partyShell(t("Quests"), t("The main quest follows your goal on the roadmap. Interlatch finds evidence in your sessions; only you mark a quest done."), body);
});

// ------------------------------------------------------------------ #/innerlatch/journey: the timeline and the levels over time
function weekOf(day) { const d0 = new Date(partyDay(day)); d0.setDate(d0.getDate() - ((d0.getDay() + 6) % 7)); return localDate(d0); }
function partyEvent(ev, a) {
  const who = h("b", null, a?.short || agentShort(ev.agent));
  let text, sub = null;
  if (ev.type === "join") text = tx("{agent} joined the party", { agent: who });
  else if (ev.type === "level") text = tx("{agent} reached level {n}", { agent: who, n: String(ev.level) });
  else if (ev.type === "skill" && ev.more?.length) { // one lesson that lifted several skills: one line
    const all = [ev, ...ev.more].map((x) => `${x.module_name || x.module || ""} ${t("Lv {n}", { n: x.level })}`).join(", ");
    text = tx("{agent} grew {n} skills: {skills}", { agent: who, n: String(all.split(", ").length), skills: all });
  } else if (ev.type === "skill") {
    text = tx("{agent} reached {skill} Lv {n}", { agent: who, skill: h("b", null, ev.module_name || ev.module || ""), n: String(ev.level) });
  } else if (ev.type === "class") text = tx("{agent}'s class is now {class}", { agent: who, class: h("b", null, ev.track_name || ev.track || "") });
  else if (ev.type === "quest") text = [t("Quest complete: {text}", { text: ev.text || ev.track_name || "" })];
  else text = [who];
  if (ev.lesson?.title && (ev.type === "skill" || ev.type === "class")) { // the lesson that tipped it, opening its session
    const title = ev.lesson.id ? lessonLink(ev.lesson.id, ev.lesson.title) : ev.lesson.title;
    sub = tx("Lesson: {title}", { title });
  }
  const mark = ev.type === "quest" || !PARTY_AGENTS.includes(ev.agent) ? pxIcon("quest-done", 2)
    : partySprite(ev.agent, { tier: ev.type === "level" ? tierOf(ev.level) : a?.tier, scale: 1, still: true, decorative: true });
  return h("li", { class: `pj-ev pj-${ev.type}` }, h("span", { class: "pj-who" }, mark),
    h("div", { class: "pj-text" }, h("div", null, text), sub ? h("div", { class: "pj-sub", title: ev.lesson.title }, sub) : null),
    h("span", { class: "pj-when" }, fmtDate(partyDay(ev.at))));
}
// a lesson that lifted several skills at once is one event, with the rest in `more`
function journeyRows(journey) {
  const out = [];
  for (const ev of journey) {
    const last = out[out.length - 1];
    if (ev.type === "skill" && last?.type === "skill" && last.agent === ev.agent && last.lesson?.id && last.lesson.id === ev.lesson?.id) {
      (last.more ||= []).push(ev);
    } else out.push({ ...ev });
  }
  return out;
}
function partyJourney(p) {
  const evs = journeyRows(p.journey || []);
  if (!evs.length) return h("section", { class: "card" }, emptyArt("clock", h("b", null, t("No journey yet")), h("div", { class: "muted" }, t("It starts with your agents' first lessons."))));
  const byAgent = Object.fromEntries(p.agents.map((a) => [a.agent, a]));
  const box = h("div", { class: "pj" });
  let week = null, list = null;
  const add = (from, to) => evs.slice(from, to).forEach((ev) => {
    const wk = weekOf(ev.at);
    if (wk !== week) {
      week = wk;
      list = h("ol", { class: "pj-list" });
      box.append(h("div", { class: "pj-week" }, h("div", { class: "tl-head" }, h("b", null, t("Week of {date}", { date: fmtDateY(partyDay(wk)) }))), list));
    }
    list.append(partyEvent(ev, byAgent[ev.agent]));
  });
  add(0, PARTY_SHOWN_EVENTS);
  const more = evs.length > PARTY_SHOWN_EVENTS ? h("button", { class: "btn small pj-more", type: "button", onclick: () => { add(PARTY_SHOWN_EVENTS, evs.length); more.remove(); } },
    t("Show {n} more", { n: evs.length - PARTY_SHOWN_EVENTS })) : null;
  return h("section", { class: "card" }, cardHead(t("Timeline"), { iconName: "history", hint: t("newest first") }), box, more);
}
function levelAt(series, week) { let pt = null; for (const x of series) { if (x[0] <= week) pt = x; else break; } return pt; }
// one step line per agent: its level at the end of each week with lessons, carried on to today
function partyStepChart(box, width, p, agents) {
  const narrow = width < 520, height = 230, m = { l: 32, r: narrow ? 12 : 104, t: 12, b: 26 };
  const iw = Math.max(40, width - m.l - m.r), ih = height - m.t - m.b;
  const firsts = agents.map((a) => new Date(partyDay(p.series[a.agent][0][0])).getTime());
  const t0 = Math.min(...firsts), t1 = Math.max(t0 + 14 * 86400000, Date.now());
  const xAt = (ms) => ((ms - t0) / (t1 - t0)) * iw;
  const x = (day) => xAt(Math.min(t1, new Date(partyDay(day)).getTime() + 6 * 86400000)); // a week's level: at its end (Sunday)
  const most = Math.max(2, ...agents.map((a) => a.level));
  const top = niceTicks(most, 4).at(-1) || 1, ticks = [0, 1, 2, 5, 10, 20, 30, 50, 75, 100, 150, 200].filter((v) => v < top).concat(top);
  const y = (lv) => ih - Math.sqrt(lv / top) * ih; // a square-root scale: the low levels don't sit on top of each other
  const svg = s("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": t("Each agent's level over time") });
  const g = s("g", { transform: `translate(${m.l},${m.t})` });
  svg.append(g);
  for (const v of ticks) {
    g.append(s("line", { class: v === 0 ? "baseline" : "gridline", x1: 0, x2: iw, y1: Math.round(y(v)) + 0.5, y2: Math.round(y(v)) + 0.5 }));
    g.append(s("text", { class: "chart-label tick-num", x: -8, y: y(v) + 3.5, "text-anchor": "end" }, String(v)));
  }
  const month = new Date(t0); month.setDate(1); month.setHours(12, 0, 0, 0); month.setMonth(month.getMonth() + 1);
  for (let lastX = -99; month.getTime() < t1; month.setMonth(month.getMonth() + 1)) {
    const mx = xAt(month.getTime());
    if (mx - lastX < 44) continue;
    lastX = mx;
    g.append(s("line", { class: "baseline", x1: mx, x2: mx, y1: ih, y2: ih + 4 }));
    g.append(s("text", { class: "chart-label", x: mx, y: ih + 17, "text-anchor": "middle" }, month.toLocaleString(LOCALE, { month: "short" })));
  }
  const ends = [], dots = [];
  for (const a of [...agents].reverse()) { // the lowest drawn last, on top: its line is the one that hides under others
    const pts = p.series[a.agent], col = partyColor(a.agent);
    let d = `M${x(pts[0][0]).toFixed(1)},${y(pts[0][1]).toFixed(1)}`;
    for (const pt of pts.slice(1)) d += `H${x(pt[0]).toFixed(1)}V${y(pt[1]).toFixed(1)}`;
    g.append(s("path", { class: "pchart-line", d: `${d}H${iw}`, stroke: col }));
    for (const pt of pts) {
      const dot = s("rect", { class: "pchart-dot", x: x(pt[0]) - 3, y: y(pt[1]) - 3, width: 6, height: 6, fill: col });
      hoverable(dot, () => [t("Lv {n}", { n: pt[1] }), `${a.name} · ${t("Week of {date}", { date: fmtDate(partyDay(pt[0])) })}`, t("{n} XP", { n: fmtNum(pt[2]) })], { focusable: false });
      dots.push(dot);
    }
    ends.push({ a, y: y(pts[pts.length - 1][1]) });
  }
  g.append(...dots); // over every line, so no week's dot hides under another agent's line
  if (!narrow) { // the names at the right end, nudged apart where levels meet
    ends.sort((e1, e2) => e1.y - e2.y);
    ends.forEach((e, i) => { if (i && e.y - ends[i - 1].y < 13) e.y = ends[i - 1].y + 13; });
    ends.forEach((e) => g.append(s("text", { class: "pchart-end", x: iw + 8, y: e.y + 4 }, `${e.a.short} ${e.a.level}`)));
  }
  box.append(svg);
  if (narrow) box.append(h("div", { class: "pchart-legend" }, agents.map((a) => h("span", null, h("i", { style: { background: partyColor(a.agent) } }),
    `${a.short} · ${t("Lv {n}", { n: a.level })}`)))); // wide, the names stand at the lines' ends
}
function partyLevelChart(p) {
  const agents = p.agents.filter((a) => (p.series?.[a.agent] || []).length);
  if (!agents.length) return null;
  const weeks = [...new Set(agents.flatMap((a) => p.series[a.agent].map((pt) => pt[0])))].sort().reverse();
  const table = () => ({ columns: [t("Week"), ...agents.map((a) => a.short)], num: [false, ...agents.map(() => true)],
    rows: weeks.map((w) => [fmtDateY(partyDay(w)), ...agents.map((a) => { const pt = levelAt(p.series[a.agent], w); return pt ? String(pt[1]) : "–"; })]) });
  return chartCard(t("Levels over time"), t("Each agent's level at the end of each week with lessons, on a square-root scale"), (box, w) => partyStepChart(box, w, p, agents), table, [], "status");
}
route(/^\/innerlatch\/journey$/, async () => {
  const token = renderSeq;
  const p = await partyGet();
  if (!p.signed_in) return partyGate(p, token);
  return partyShell(t("Journey"), t("How your party grew, newest first."), partyLevelChart(p), partyJourney(p));
});

// ------------------------------------------------------------------ #/innerlatch/agent/<agent>: one character
function partySkillDetail(r) {
  const lesson = (l) => h("li", null, kindChip(l.kind),
    h("div", { class: "psl-main" }, l.id ? lessonLink(l.id, l.title) : h("span", null, l.title),
      h("div", { class: "psl-meta" }, [STAGE_LABEL[l.stage || "provisional"] || l.stage, t("+{n} XP", { n: l.xp }),
        l.created_at ? fmtDate(l.created_at) : null, l.project_name].filter(Boolean).join(" · "))));
  const lessons = r.lessons || [];
  return [
    r.level ? h("div", { class: "psd-block psd-xp" }, h("span", { class: "px-label" }, t("Skill XP")),
      xpBar(r.xp, r.level_xp, r.next_xp, r.level)) : null,
    r.about?.length ? h("div", { class: "psd-block" }, h("span", { class: "px-label" }, t("What the module covers")),
      h("ul", { class: "psd-study" }, r.about.map((x) => h("li", null, x)))) : null,
    r.practice || r.gate ? h("div", { class: "psd-block psd-you" }, h("span", { class: "px-label" }, t("On the roadmap, for you")),
      r.practice ? h("p", null, h("b", null, t("Practice")), " ", r.practice) : null,
      r.gate ? h("p", null, h("b", null, t("Pass check")), " ", r.gate) : null) : null,
    h("div", { class: "psd-block" }, h("span", { class: "px-label" }, tn(r.lessons_total ?? lessons.length, "The lesson that earned it", "The {n} lessons that earned it", { n: fmtNum(r.lessons_total ?? lessons.length) }),
      (r.lessons_total ?? 0) > lessons.length ? ` · ${t("the {n} that earned most listed", { n: fmtNum(lessons.length) })}` : null),
      lessons.length ? capped(lessons, lesson, 8, "psl-list") : h("div", { class: "muted small" }, t("No lessons"))),
  ];
}
function partySkillRow(a, sk, max) {
  const id = `psd-${a.agent}-${sk.module}`.replace(/[^\w-]/g, "-");
  const detail = h("div", { class: "ps-detail", id, hidden: true });
  let loaded = false;
  const btn = h("button", { type: "button", class: "ps-row", "aria-expanded": "false", "aria-controls": id, onclick: async () => {
    const open = btn.getAttribute("aria-expanded") !== "true";
    btn.setAttribute("aria-expanded", String(open));
    detail.hidden = !open;
    if (!open || loaded) return;
    loaded = true;
    detail.replaceChildren(h("div", { class: "muted small" }, t("Loading…")));
    try {
      const r = await api("/api/party/skill", { agent: a.agent, module: sk.module });
      detail.replaceChildren(...partySkillDetail(r).filter(Boolean));
    } catch (e) {
      loaded = false;
      if (!e.handled) detail.replaceChildren(h("div", { class: "warn-line" }, t("Could not load: {error}", { error: e.message })));
    }
  } },
    h("span", { class: "ps-name", title: sk.name }, sk.name),
    lvBar(sk.level, max),
    h("span", { class: "ps-lv" }, t("Lv {n}", { n: sk.level })),
    h("span", { class: "ps-n" }, tn(sk.lessons, "{n} lesson", "{n} lessons", { n: fmtNum(sk.lessons) })),
    icon("chevron", "ps-chev"));
  return h("li", null, btn, detail);
}
const PARTY_SKILLS_SHOWN = 5; // a group's strongest skills; the rest behind "+N more"
function partySkillMax(p) { return Math.max(1, ...p.agents.flatMap((a) => (a.skills || []).map((sk) => sk.level))); }
// `max`: the highest skill level in the whole party, so one agent's bars compare with another's
function partySkills(a, attrs, max) {
  const groups = attrs.map((at) => [at, (a.skills || []).filter((sk) => sk.attr === at.id)]).filter(([, list]) => list.length);
  return h("section", { class: "card" }, cardHead(t("Skills"), { iconName: "learning", hint: t("Open one to see the lessons that earned it. A lesson can count toward up to three skills.") }),
    groups.length ? h("div", { class: "ps-groups" }, groups.map(([at, list]) => h("div", { class: "ps-group" },
      h("div", { class: "ps-attr" }, pxIcon(at.id, 1), h("b", null, at.name), h("span", { class: "muted small" }, tn(list.length, "{n} skill", "{n} skills"))),
      capped(list, (sk) => partySkillRow(a, sk, max), PARTY_SKILLS_SHOWN, "ps-list", (n) => tn(n, "Show {n} more skill", "Show {n} more skills")))))
      : emptyArt("book", h("b", null, t("No skills yet")), h("div", { class: "muted" }, t("A skill appears once one of this agent's lessons matches a module of the roadmap."))));
}
// the track's modules in their suggested order, each lit by the agent's level there; `asked`: the modules employers ask for
function trackPath(tr, agent, asked = new Set()) {
  return h("ol", { class: "px-path", "aria-label": t("{track}: its modules in order", { track: tr.name }) }, (tr.modules || []).map((m) => {
    const lv = m.agents?.[agent]?.level || 0, ask = asked.has(m.module);
    const note = [m.name, lv ? t("Lv {n}", { n: lv }) : t("not covered yet"), m.target ? t("your roadmap target: level {n} of 5", { n: m.target }) : null,
      m.critical ? t("key module (counts double)") : null, ask ? t("employers ask for it") : null].filter(Boolean).join(" · ");
    return h("li", { class: `pp-node${lv >= 5 ? " lit-3" : lv >= 3 ? " lit-2" : lv ? " lit-1" : ""}${m.critical ? " critical" : ""}${ask ? " asked" : ""}`, title: note },
      h("span", { class: "pp-tile", "aria-hidden": "true" }, lv ? String(lv) : ""),
      h("span", { class: "pp-name", "aria-hidden": "true" }, m.name),
      h("span", { class: "sr-only" }, note));
  }));
}
// the modules employers ask for most in a track's postings, each with the agent's level there; one off the track's path
// says so (postings ask for what they ask for, not only the roadmap track's modules)
function partyAsks(tops, a, path) {
  const lv = (id) => a.skills?.find((sk) => sk.module === id)?.level || 0;
  return h("div", { class: "pcl-asks" }, h("span", { class: "px-label" }, t("Employers ask for")),
    h("ul", null, tops.map((m) => {
      const off = !path.has(m.module), n = lv(m.module);
      return h("li", { class: off ? "off-path" : null, title: off ? t("Not on this track's path") : null }, pxIcon(m.attr, 1), h("span", null, m.name),
        h("b", null, n ? t("Lv {n}", { n }) : "–"));
    })));
}
function partyClasses(a, market) {
  const row = (c, i) => {
    const path = h("div", { class: "pcl-path" }, h("span", { class: "muted small" }, t("Loading…")));
    api("/api/party/track", { id: c.id }).then((tr) => {
      const tops = tr.market?.classes?.[tr.id]?.top_modules || [];
      path.replaceChildren(trackPath(tr, a.agent, new Set(tops.map((m) => m.module))), tops.length ? partyAsks(tops, a, new Set((tr.modules || []).map((m) => m.module))) : "");
    }).catch((e) => { if (!e.handled) path.replaceChildren(h("span", { class: "muted small" }, t("Could not load: {error}", { error: e.message }))); });
    return h("div", { class: "pcl" },
      h("div", { class: "pcl-head" }, h("span", { class: "pcl-rank" }, String(i + 1)), h("b", null, c.name), h("span", { class: "pcl-cov" }, t("{pct} covered", { pct: pctOf(c.coverage) }))),
      h("span", { class: "pa-bar pcl-bar", "aria-hidden": "true" }, h("i", { style: { width: pctOf(c.coverage) } })),
      partyHiring(market, c.id, { compact: true }),
      path);
  };
  return h("section", { class: "card" }, cardHead(a.class ? t("Best-covered tracks") : t("Tracks it leans toward"), { iconName: "map", hint: t("The roadmap tracks its lessons cover most") }),
    a.classes?.length ? [h("div", { class: "pcl-list" }, a.classes.slice(0, 3).map(row)),
      h("p", { class: "muted small pcl-legend" }, t("Each square is one of the track's modules, in the order the roadmap suggests, lit by {agent}'s skill level there: full at Lv 5. A gold corner marks a key module: it counts double toward coverage.", { agent: a.short }),
        a.classes.slice(0, 3).some((c) => market?.classes?.[c.id]?.top_modules?.length) ? ` ${t("A green corner marks a module employers ask for.")}` : null),
      marketNote(market)]
      : h("div", { class: "empty" }, t("No class yet: its lessons don't cover a roadmap track.")));
}
route(/^\/innerlatch\/agent\/([\w-]+)$/, async (params, agent) => {
  const token = renderSeq; // this view's render: one that finishes after the person moved on names nothing
  const p = await partyGet();
  if (!p.signed_in) return partyGate(p, token);
  const a = p.agents.find((x) => x.agent === agent);
  if (!a) return partyShell(t("Party"), null, h("section", { class: "card empty" }, t("No such character in your party.")));
  setCrumbs([["Innerlatch", "#/innerlatch"], [t("Party"), "#/innerlatch"], [a.name]], token);
  const hero = h("section", { class: "card party-hero px-frame" },
    h("div", { class: "ph-stage px-stage" }, partySprite(a.agent, { tier: a.tier, scale: 8, label: partyLabel(a) })),
    h("div", { class: "ph-main" },
      h("div", { class: "ph-title" }, h("h1", null, a.name), tierBadge(a.tier)),
      h("div", { class: "ph-level" }, lvBadge(a.level, true), xpBar(a.xp, a.level_xp, a.next_xp, a.level)),
      partyRecruit(a) ? h("div", { class: "ph-class muted" }, t("No lessons yet: a recruit levels up once its sessions produce lessons."))
        : [partyClassLine(a, { category: true }), partyHiring(p.market, a.class?.id)],
      h("div", { class: "ph-facts" },
        fact(t("sessions"), fmtNum(a.sessions)), fact(t("lessons"), fmtNum(a.lessons)), fact(t("lessons that count"), fmtNum(a.counted)),
        fact(t("since"), a.since ? fmtDateY(partyDay(a.since)) : "–"), fact(t("latest"), a.last ? fmtDateY(partyDay(a.last)) : "–"))),
    h("div", { class: "ph-attrs" }, h("span", { class: "px-label" }, t("Attributes")), attrBars(a, p.attrs, partyMax(p), { big: true }),
      h("p", { class: "muted small ph-attrs-note" }, t("An attribute's level comes from all its skills' XP together, so it can pass its best skill."))));
  const [bag] = await Promise.all([bagLoad(true).catch(() => null), mapLoad().catch(() => null)]); // gear: battle numbers only (the map: the modules' names)
  const gearCard = bag ? h("section", { class: "card gear-card" }, cardHead(t("Gear"), { iconName: "tool",
    hint: t("Worn in Adventure battles only: gear never changes a level") }), gearSlots(bag, a.agent),
    h("a", { class: "link-arrow small", href: "#/innerlatch/adventure/bag" }, t("Open your bag"), icon("arrow"))) : null;
  const page = partyShell(null, null, hero, gearCard, partySkills(a, p.attrs, partySkillMax(p)), partyClasses(a, p.market));
  setStyle(page, { "--ac": partyColor(a.agent) });
  return page;
});
async function partySidebar(box) {
  const p = partyCache && Date.now() - partyCacheAt < 5000 ? partyCache : await partyLoad().catch(() => null);
  const on = !!p?.signed_in;
  const review = on ? (p.quests || []).filter((q) => q.main && q.status === "evidence").length : 0;
  const agentRow = (a) => h("a", { class: "sb-row sb-party", href: `#/innerlatch/agent/${a.agent}`, title: partyLabel(a), "data-route": `/innerlatch/agent/${a.agent}`, "data-key": "", "data-val": "" },
    agentIcon(a.agent), h("span", null, a.name), h("em", null, t("Lv {n}", { n: a.level })));
  box.replaceChildren(h("div", { class: "sb-head" }, h("h2", null, t("Party"))),
    h("div", { class: "sb-scroll" },
      sbRow(t("Party"), "#/innerlatch", "party", null, ["/innerlatch"]),
      Object.assign(sbRow(t("Quests"), "#/innerlatch/quests", "todo", review || null, ["/innerlatch/quests"]),
        review ? { title: tn(review, "Quests: {n} main-quest milestone with evidence to review", "Quests: {n} main-quest milestones with evidence to review") } : {}),
      sbRow(t("Journey"), "#/innerlatch/journey", "history", null, ["/innerlatch/journey"]),
      h("div", { class: "sb-group" }, t("Characters")),
      on ? p.agents.map(agentRow) : h("div", { class: "sb-empty" }, t("Sign in to meet your party."))));
}

// =====================================================================================
// Adventure: every lesson your agents learned as an encounter on a pixel overworld (adventure.js), one region per
// attribute and one territory per roadmap module, fogged where no lesson reached yet; the main quest's milestones guard
// the gates as bosses. An encounter opens a turn-based battle (battle.js). A win only marks it cleared: levels and XP
// come from the lessons alone. Without the engines, the encounters as a list.
// =====================================================================================
let mapCache = null, mapCacheAt = 0;
async function mapLoad(fresh = false) {
  if (!fresh && mapCache && Date.now() - mapCacheAt < 60000) return mapCache;
  mapCache = await api("/api/party/map");
  mapCacheAt = Date.now();
  return mapCache;
}
function mapCleared(ids) { if (mapCache) mapCache.cleared = ids; }
function advTally(m) {
  const done = new Set(m.cleared || []);
  const by = Object.fromEntries((m.regions || []).map((r) => [r.id, { total: 0, cleared: 0 }]));
  const territory = Object.fromEntries((m.regions || []).flatMap((r) => r.modules.map((x) => [x.id, r.id])));
  for (const l of m.lessons || []) {
    const r = by[territory[l.modules[0]]];
    if (!r) continue;
    r.total++;
    if (done.has(l.id)) r.cleared++;
  }
  return { by, total: (m.lessons || []).length, cleared: (m.lessons || []).filter((l) => done.has(l.id)).length };
}
const ADV_KIND_NAME = { fix: t("Glitch"), gotcha: t("Mal"), decision: t("Breaker"), learning: t("Wisp"), pattern: t("Gearling"), command: t("Scrollkin") };
function advLessonTip(l, where, cleared) {
  showTip(where, plainTitle(l.title), [ADV_KIND_NAME[l.kind] || kindLabel(l.kind), agentName(l.agent), l.at ? fmtDateY(partyDay(l.at)) : null].filter(Boolean).join(" · "),
    cleared ? t("Cleared · click to read it") : t("Click to battle it"));
}
async function adventureSidebar(box) {
  let m = null;
  try { m = await mapLoad(); } catch (e) { /* the page says why */ }
  const head = h("div", { class: "sb-head" }, h("h2", null, t("Adventure")));
  if (!m) { box.replaceChildren(head, h("div", { class: "sb-empty" }, t("Sign in to meet your party."))); return; }
  const tally = advTally(m);
  box.replaceChildren(head, h("div", { class: "sb-scroll" },
    sbRow(t("World map"), "#/innerlatch/adventure", "map", null, ["/innerlatch/adventure", "view", ""]),
    sbRow(t("Encounter list"), "#/innerlatch/adventure?view=list", "todo", tally.total - tally.cleared, ["/innerlatch/adventure", "view", "list"]),
    Object.assign(sbRow(t("Bag"), "#/innerlatch/adventure/bag", "artifacts", m.gold || 0, ["/innerlatch/adventure/bag"]),
      { title: t("{n} gold", { n: fmtNum(m.gold || 0) }) }),
    h("div", { class: "sb-group" }, t("Towns")),
    (m.regions || []).map((r) => sbRow(townName(m, r.id), `#/innerlatch/adventure/town/${r.id}`, "home", null, [`/innerlatch/adventure/town/${r.id}`])),
    h("div", { class: "sb-group" }, t("Regions")),
    (m.regions || []).map((r) => {
      const x = tally.by[r.id] || { total: 0, cleared: 0 };
      return h("a", { class: "sb-row sb-region", href: `#/innerlatch/adventure?view=map&region=${r.id}`, title: r.about || r.name,
        "data-route": "/innerlatch/adventure", "data-key": "region", "data-val": r.id },
        pxIcon(r.id, 1), h("span", null, r.name), h("em", null, t("{n}/{total}", { n: fmtNum(x.cleared), total: fmtNum(x.total) })));
    })));
}
function advList(m, params) {
  const done = new Set(m.cleared || []);
  const territory = Object.fromEntries((m.regions || []).flatMap((r) => r.modules.map((x) => [x.id, { region: r, module: x }])));
  const want = { region: params.region || "", kind: params.kind || "", agent: params.agent || "", show: params.show || "open" };
  const rows = (m.lessons || []).filter((l) => (!want.region || territory[l.modules[0]]?.region.id === want.region)
    && (!want.kind || l.kind === want.kind) && (!want.agent || l.agent === want.agent)
    && (want.show === "all" || (want.show === "cleared") === done.has(l.id))).slice().reverse();
  const set = (k, v) => go(`#/innerlatch/adventure?${new URLSearchParams(Object.entries({ ...want, view: "list", [k]: v }).filter(([, x]) => x)).toString()}`);
  const pick = (label, k, opts) => h("select", { "aria-label": label, onchange: (e) => set(k, e.target.value) },
    opts.map(([v, l]) => h("option", { value: v, selected: v === want[k] }, l)));
  const shown = rows.slice(0, 300);
  return h("section", { class: "card adv-list" },
    h("div", { class: "adv-filters" },
      pick(t("Region"), "region", [["", t("All regions")], ...(m.regions || []).map((r) => [r.id, r.name])]),
      pick(t("Kind"), "kind", [["", t("All kinds")], ...Object.keys(ADV_KIND_NAME).map((k) => [k, kindLabel(k)])]),
      pick(t("Agent"), "agent", [["", t("All agents")], ...PARTY_AGENTS.map((a) => [a, agentName(a)])]),
      segControl([["open", t("Not cleared")], ["cleared", t("Cleared")], ["all", t("All")]], want.show, (v) => set("show", v))),
    rows.length ? h("ul", { class: "adv-rows" }, shown.map((l) => {
      const where = territory[l.modules[0]];
      return h("li", { class: done.has(l.id) ? "cleared" : "" }, kindChip(l.kind),
        h("div", { class: "adv-row-main" }, lessonLink(l.id, l.title),
          h("div", { class: "muted small" }, [where?.region.name, where?.module.name, agentShort(l.agent), l.at ? fmtDate(partyDay(l.at)) : null].filter(Boolean).join(" · "))),
        done.has(l.id) ? h("span", { class: "adv-flag" }, pxIcon("quest-done", 1), t("Cleared"))
          : h("a", { class: "btn small", href: `#/innerlatch/adventure/battle/${l.id}` }, t("Battle")));
    })) : h("div", { class: "empty" }, t("No encounters match.")),
    rows.length > shown.length ? h("p", { class: "muted small" }, t("Showing the newest {n} of {total}.", { n: fmtNum(shown.length), total: fmtNum(rows.length) })) : null);
}
route(/^\/innerlatch\/adventure$/, async (params) => {
  const token = renderSeq;
  const p = await partyGet();
  if (!p.signed_in) return partyGate(p, token);
  const m = await mapLoad(true);
  const tally = advTally(m);
  const list = params.view === "list" || !window.Adventure || typeof window.Adventure.mountMap !== "function";
  const head = h("div", { class: "page-head adv-head" }, h("div", { class: "head-title" }, pxIcon("journey", 3),
    h("div", null, h("h1", null, t("Adventure")),
      h("div", { class: "sub" }, tally.total ? t("Every lesson your agents learned is out there. {n} of {total} cleared.", { n: fmtNum(tally.cleared), total: fmtNum(tally.total) })
        : t("No lessons on the roadmap yet: the world is still under fog.")))),
    h("div", { class: "head-actions" }, goldChip(m.gold), h("a", { class: "btn small", href: "#/innerlatch/adventure/bag" }, icon("artifacts"), t("Bag")),
      segControl([["map", t("Map")], ["list", t("List")]], list ? "list" : "map",
      (v) => go(v === "list" ? "#/innerlatch/adventure?view=list" : "#/innerlatch/adventure"))));
  if (list) return h("div", { class: "party-page adv-page" }, head, advList(m, params));
  const host = h("div", { class: "adv-world" }); // the engine names its own canvas (labels.map)
  const page = h("div", { class: "party-page adv-page map" }, head, host,
    h("p", { class: "muted small adv-note" }, t("Battles don't change levels: an agent's XP comes from its lessons. A win marks the encounter cleared.")));
  const done = () => new Set((mapCache || m).cleared || []);
  whenShown(host, () => {
    let world;
    try {
      world = Adventure.mountMap(host, { data: m, reducedMotion: stillMotion(), labels: advMapLabels(),
        onHover: (item, at) => {
          if (!item) { hideTip(); return; }
          const where = { clientX: at?.x ?? 0, clientY: at?.y ?? 0 };
          if (item.type === "lesson") advLessonTip(item.lesson, where, done().has(item.lesson.id));
          else if (item.type === "boss") showTip(where, item.boss.text, t("Main quest boss · {track}", { track: item.boss.track_name || "" }),
            item.boss.status === "done" ? t("Defeated: the quest is done") : t("Mark the quest done to defeat it"));
          else if (item.type === "region" && item.module) { // a territory: its module, and how much of it is cleared
            const mod = item.module;
            showTip(where, mod.name, item.region.name, mod.fog || !mod.total ? t("Unexplored")
              : t("{n} of {total} cleared", { n: fmtNum(mod.cleared || 0), total: fmtNum(mod.total) }));
          } else if (item.type === "region") showTip(where, item.region.name, item.region.about || null);
          else if (item.type === "party") showTip(where, t("Your party"), t("Where the newest lesson was learned"));
          else if (item.type === "town") showTip(where, item.town?.name || item.region.name, item.region.name, t("A shop: gear for this region's skills, and supplies"));
          else hideTip();
        },
        onPick: (item) => {
          hideTip();
          if (item?.type === "lesson") {
            if (done().has(item.lesson.id)) openLesson(item.lesson.id);
            else go(`#/innerlatch/adventure/battle/${item.lesson.id}`);
          } else if (item?.type === "boss") go(`#/innerlatch/adventure/boss/${item.boss.n}`);
          else if (item?.type === "town") go(`#/innerlatch/adventure/town/${item.region.id}`);
        } });
    } catch (e) { host.replaceChildren(h("div", { class: "card empty" }, t("Could not load: {error}", { error: e.message }))); return; }
    if (params.lesson) world.focusLesson?.(Number(params.lesson));
    else if (params.region) world.focusRegion?.(params.region);
    const gone = new MutationObserver(() => { if (!host.isConnected) { gone.disconnect(); try { world.destroy?.(); } catch (e) { /* gone */ } } });
    gone.observe(document.querySelector("#app"), { childList: true });
  });
  return page;
});
function advMapLabels() { // the map engine's words (adventure.js LABELS), in the page's language
  return {
    "map": t("Adventure map. Drag or use the arrow keys to look around; scroll, pinch or press + and − to zoom; N and P step through the encounters on screen and Enter opens one."),
    "regionStats": t("{cleared}/{lessons} cleared"), "fog": t("Unexplored"), "progress": t("{cleared}/{lessons}"),
    "boss": t("Boss: {text}"), "bossAsleep": t("asleep"), "bossAwake": t("awake: evidence found"),
    "bossDefeated": t("defeated"), "party": t("Your party"), "cleared": t("cleared"), "zoomIn": t("Zoom in"),
    "zoomOut": t("Zoom out"), "home": t("Back to the party"), "fit": t("Show the whole map"),
    "empty": t("No lessons yet: every lesson your agents learn will stand here as an encounter") };
}
function advBattleLabels() { // the battle engine's words (battle.js LABELS); the regions' names from the map's payload
  const attrs = Object.fromEntries((mapCache?.regions || []).map((r) => [`attr.${r.id}`, r.name]));
  return { ...attrs,
    "region": t("Battle: {monster}"), "scene": t("{monster} faces {heroes}"), "epithet": t("«{title}»"),
    "bossEpithet": t("Milestone {n}: {text}"), "round": t("Round {n}"), "level": t("Lv {n}"), "hp": t("HP"),
    "mp": t("MP"), "hpValue": t("{hp}/{max}"), "taught": t("Taught it"), "prompt": t("What will {hero} do?"),
    "log": t("Battle log"), "commands": t("Commands"), "attack": t("Attack"), "skill": t("Skill"),
    "defend": t("Defend"), "run": t("Run"), "back": t("Back"), "skillLevel": t("Lv {n}"),
    "skillNone": t("untrained"), "skillCost": t("{n} MP"), "attrSkill": t("{attr} lore"),
    "intro": t("A wild {monster} appears: «{title}»"), "introBoss": t("{monster} bars the way: «{text}»"),
    "lineAttack": t("{hero} attacks: {n} damage."), "lineCrit": t("{hero} lands a critical hit: {n} damage!"),
    "lineSkill": t("{hero} uses {skill} (Lv {lv}): {n} damage!"),
    "lineSkillWeak": t("{hero} tries {skill} untrained: {n} damage."),
    "lineDefend": t("{hero} defends and steadies (+{mp} MP)."), "lineDefendFull": t("{hero} defends."),
    "lineNoMp": t("{hero} doesn't have the MP for {skill}."), "lineRun": t("The party slips away."),
    "lineHit": t("{hero} takes {n} damage."), "lineGuarded": t("{hero} blocks: only {n} damage."),
    "lineDodge": t("{hero} dodges!"), "lineDrain": t("{hero} loses {mp} MP."),
    "lineHeal": t("{monster} recovers {n} HP."), "lineKo": t("{hero} is knocked out!"),
    "lineCharged": t("{monster} is gathering strength. Defend!"), "lineWin": t("{monster} is beaten!"),
    "lineLose": t("The party falls back."), "lineHold": t("{monster} holds at 1 HP: only you can defeat this boss."),
    "win": t("{monster} is beaten!"),
    "winText": t("{agent} learned this on {date}: +{xp} XP then. Battles don't change levels."),
    "winAnswer": t("The answer"), "winExcerpt": t("From the lesson"), "readLesson": t("Read the lesson"),
    "openSession": t("Open the session"), "backToMap": t("Back to the map"), "lose": t("The party falls back"),
    "loseText": t("Nothing is lost: battles don't change levels. Retry rolls new dice."), "retry": t("Retry"),
    "hold": t("{monster} holds at 1 HP"),
    "holdText": t("Only you can defeat this boss: mark the milestone done on the Quests page."),
    "openQuests": t("Open Quests"), "bossDone": t("{monster} is already defeated"),
    "bossDoneText": t("You marked this milestone done: «{text}»."), "monster.fix": t("Glitch"),
    "monster.gotcha": t("Mal"), "monster.decision": t("Breaker"), "monster.learning": t("Wisp"),
    "monster.pattern": t("Gearling"), "monster.command": t("Scrollkin"), "boss.1": t("Giant Glitch"),
    "boss.2": t("Mal the Masked"), "boss.3": t("Firewall Titan"), "boss.4": t("Great Breaker"),
    "move.fix.bite": t("{monster} chews through a test!"),
    "move.fix.swarm": t("{monster} multiplies across the codebase!"),
    "move.fix.heisen": t("{monster} flickers like a Heisenbug!"),
    "move.fix.charge": t("{monster} burrows into the stack…"), "move.fix.crash": t("{monster} crashes the build!"),
    "move.gotcha.trap": t("{monster} springs a trap!"),
    "move.gotcha.edge": t("{monster} finds an edge case nobody checked!"),
    "move.gotcha.silent": t("{monster} fails silently…"),
    "move.gotcha.charge": t("{monster} hides behind a default…"),
    "move.gotcha.default": t("{monster}'s hidden default goes off!"),
    "move.decision.fork": t("{monster} forks the road!"),
    "move.decision.tradeoff": t("{monster} forces a trade-off!"),
    "move.decision.bikeshed": t("{monster} starts a bikeshed and catches its breath."),
    "move.decision.charge": t("{monster} weighs its options…"),
    "move.decision.flip": t("{monster} flips the switch!"),
    "move.learning.glare": t("{monster} shines a blinding insight!"),
    "move.learning.rabbit": t("{monster} leads everyone down a rabbit hole!"),
    "move.learning.fog": t("{monster} spreads a fog of jargon…"),
    "move.learning.charge": t("{monster} gathers light…"),
    "move.learning.epiphany": t("{monster} bursts into an epiphany!"),
    "move.pattern.copy": t("{monster} copy-pastes itself!"), "move.pattern.grind": t("{monster} grinds its gears!"),
    "move.pattern.boiler": t("{monster} buries the party in boilerplate!"),
    "move.pattern.charge": t("{monster} spins up…"),
    "move.pattern.repeat": t("{monster} repeats itself at full speed!"),
    "move.command.flag": t("{monster} passes the wrong flag!"),
    "move.command.pipe": t("{monster} pipes everything into everything!"),
    "move.command.sudo": t("{monster} runs it again with sudo!"),
    "move.command.charge": t("{monster} types rm -rf…"), "move.command.enter": t("{monster} hits Enter!"),
    "move.boss.deadline": t("{monster} pulls the deadline closer!"),
    "move.boss.scope": t("{monster} creeps the scope!"),
    "move.boss.meeting": t("{monster} calls a meeting and recovers."),
    "move.boss.charge": t("{monster} gathers the whole milestone…"),
    "move.boss.unleash": t("{monster} unleashes the milestone!"),
    "item": t("Item"), "skillGear": t("+{n} gear"), "itemCount": t("×{n}"), "itemTarget": t("→ {hero}"),
    "itemNone": t("The bag is empty."), "itemNoTarget": t("Nobody is knocked out."),
    "hint.c:potion": t("Heals 40% HP"), "hint.c:ether": t("Restores 50% MP"),
    "hint.c:phoenix": t("Revives a fallen hero"), "hint.c:smoke": t("A sure escape"),
    "hint.l:patch": t("Heals everyone 25% HP"), "hint.l:sign": t("40% less damage, 2 turns"),
    "hint.l:adr": t("The monster loses a turn"), "hint.l:insight": t("Everyone recovers 30% MP"),
    "hint.l:template": t("Next blow ×1.6"), "hint.l:script": t("Hits for 25% of its HP"),
    "lineItem": t("{hero} uses {item}."), "lineItemHeal": t("{target} recovers {n} HP."),
    "lineItemMp": t("{target} recovers {n} MP."), "lineRevive": t("{target} is back on their feet ({n} HP)."),
    "lineSmoke": t("Smoke fills the field: the party escapes."),
    "lineWard": t("The party braces: less damage for {n} turns."),
    "lineSkipNext": t("{monster} will lose its next turn."),
    "lineSkipped": t("{monster} is still rethinking and loses its turn."),
    "lineTemplate": t("The next blow will hit ×1.6."), "lineScript": t("{item} hits {monster} for {n} damage!"),
    "lineNoItem": t("No {item} left."), "rewardWait": t("Counting the spoils…"), "rewardGold": t("+{n} gold"),
    "rewardLoot": t("Found: {item}"), "rewardPaid": t("Already cleared before: no gold this time."),
    "itemFullHp": t("Everyone is at full HP."), "itemFullMp": t("Everyone is at full MP."), "more": t("More below"), "hint.l:script.boss": t("Hits a boss for 10% of its HP"), "rewardFailed": t("The win wasn't saved: try it again from the map.") };
}
async function advBattlePage(build, back, crumbs, token) {
  setCrumbs(crumbs, token);
  const host = h("div", { class: "adv-battle" });
  const page = h("div", { class: "party-page adv-page battle" }, host);
  if (!window.Battle || typeof window.Battle.mount !== "function") {
    host.append(h("div", { class: "card empty" }, t("The battle screen didn't load."), " ", h("a", { href: back }, t("Back to the map"))));
    return page;
  }
  let fight = null, attempt = 0;
  const start = (enc) => {
    try { fight?.destroy?.(); } catch (e) { /* gone */ }
    host.replaceChildren();
    fight = Battle.mount(host, { ...enc, attempt, labels: advBattleLabels(), reducedMotion: stillMotion(),
      formatDate: (at) => fmtDateY(partyDay(at)) });
  };
  const cb = {
    onLose: async () => { attempt++; enc = await build(cb); if (enc) start(enc); }, // Retry: new dice, and the bag as it is now
    onRun: () => go(back), onExit: () => go(back),
    onReadLesson: (id) => openLesson(id),
    onQuests: () => go("#/innerlatch/quests"), // no sessionHref: the lesson opens in the modal, inside Innerlatch
    onUseItem: async (id) => { const r = await lbPost("/api/party/use", { id }); if (r?.bag) bagKeep(r.bag); },
  };
  let enc = await build(cb);
  if (!enc) return page;
  whenShown(host, () => start(enc));
  const gone = new MutationObserver(() => { if (!host.isConnected) { gone.disconnect(); try { fight?.destroy?.(); } catch (e) { /* gone */ } } });
  whenShown(host, () => gone.observe(document.querySelector("#app"), { childList: true }));
  return page;
}
route(/^\/innerlatch\/adventure\/battle\/(\d+)$/, async (params, id) => {
  const token = renderSeq;
  const p = await partyGet();
  if (!p.signed_in) return partyGate(p, token);
  let encounter;
  try { encounter = await api("/api/party/encounter", { id }); } catch (e) {
    if (e.handled || e.status !== 404) throw e;
    setCrumbs([["Innerlatch", "#/innerlatch"], [t("Adventure"), "#/innerlatch/adventure"], [t("Battle")]], token);
    return partyShell(t("Adventure"), null, h("section", { class: "card empty" }, t("This lesson isn't in your party's archive."), " ",
      h("a", { href: "#/innerlatch/adventure" }, t("Back to the map"))));
  }
  const back = `#/innerlatch/adventure?lesson=${id}`;
  const onWin = async () => { // the battle shows the reward: gold and loot on a first clear, nothing on a second
    const r = await lbPost("/api/party/cleared", { id: Number(id), cleared: true });
    if (r) { mapCleared(r.cleared); if (r.bag) bagKeep(r.bag); }
    if (sbSection === "adventure") buildSidebar("adventure");
    return r ? r.reward || null : false; // false: the win wasn't saved (the battle says so)
  };
  let first = encounter; // a retry asks again: the items used in the lost fight are gone
  return advBattlePage(async (cb) => {
    const enc = first || await api("/api/party/encounter", { id }).catch(() => ({ ...encounter, items: [] })); // never the old counts
    first = null;
    return { encounter: enc, ...cb, onWin };
  }, back, [["Innerlatch", "#/innerlatch"], [t("Adventure"), "#/innerlatch/adventure"], [plainTitle(encounter.lesson.title)]], token);
});
route(/^\/innerlatch\/adventure\/boss\/(\d)$/, async (params, n) => {
  const token = renderSeq;
  const p = await partyGet();
  if (!p.signed_in) return partyGate(p, token);
  const m = await mapLoad();
  const boss = (m.bosses || []).find((b) => String(b.n) === n);
  if (!boss) return partyShell(t("Adventure"), null, h("section", { class: "card empty" }, t("No such boss on your main quest."), " ", h("a", { href: "#/innerlatch/adventure" }, t("Back to the map"))));
  return advBattlePage(async (cb) => { // a boss fight brings the party's gear and supplies too, as they are at each try
    const bag = await bagLoad(true).catch(() => null);
    const party = (m.agents || []).map((a) => ({ ...a, gear: bag?.gear?.[a.agent] }));
    const items = (bag?.items || []).filter((x) => x.kind !== "gear" && x.count > 0);
    return { encounter: { boss, party, items }, ...cb, onWin: () => undefined };
  }, "#/innerlatch/adventure",
    [["Innerlatch", "#/innerlatch"], [t("Adventure"), "#/innerlatch/adventure"], [boss.text]], token);
});

// ------------------------------------------------------------------ Town & gear: gold from first wins, a shop in each
// region's town whose gear unlocks with the party's real skill levels there, three slots per agent, supplies and loot
// for battles (knowledge/bag.py). It's a game economy: gear changes battle numbers only, never a level or XP.
let bagCache = null;
async function bagLoad(fresh = false) { if (!fresh && bagCache) return bagCache; return bagKeep(await api("/api/party/bag")); }
function bagKeep(b) { bagCache = b; if (mapCache && Number.isFinite(b?.gold)) mapCache.gold = b.gold; return b; }
function goldChip(n) { return h("span", { class: "adv-gold", title: t("Gold: earned by a lesson's first win, spent in towns") }, h("i", { "aria-hidden": "true" }), t("{n} gold", { n: fmtNum(n || 0) })); }
function townName(m, attr) { return (m?.towns || []).find((x) => x.attr === attr)?.name || (m?.regions || []).find((r) => r.id === attr)?.name || attr; }
const SLOT_ICON = { weapon: "tool", armor: "shield", charm: "sparkles" };
const SLOT_LABEL = { weapon: t("Weapon"), armor: t("Armor"), charm: t("Charm") };
const TIER_LABEL = { 1: "I", 2: "II", 3: "III" };
function itemStats(x) { // what it does in battle, in a line
  const st = x.stats || {}, mod = x.module ? (mapCache?.regions || []).flatMap((r) => r.modules).find((md) => md.id === x.module)?.name || x.module : null;
  const out = [st.atk ? t("+{n} attack", { n: st.atk }) : null, st.hp ? t("+{n} HP", { n: st.hp }) : null, st.mp ? t("+{n} MP", { n: st.mp }) : null,
    st.def ? t("−{n}% damage taken", { n: Math.round(st.def * 100) }) : null, st.skill && mod ? t("+{n} gear on {skill} (battles)", { n: st.skill, skill: mod }) : null];
  return out.filter(Boolean).join(" · ") || x.about || "";
}
function itemIcon(x) { return x.kind === "gear" ? icon(SLOT_ICON[x.slot] || "tool") : x.kind === "loot" ? pxIcon("star", 1) : pxIcon("xp", 1); }
function itemRow(x, ...actions) {
  return h("li", { class: `bag-row${x.locked ? " locked" : ""}` }, h("span", { class: "bag-ic" }, itemIcon(x)),
    h("div", { class: "bag-main" }, h("b", null, x.name, x.tier ? h("span", { class: "bag-tier" }, TIER_LABEL[x.tier]) : null,
      x.count > 1 ? h("span", { class: "bag-count" }, `×${x.count}`) : null),
      h("div", { class: "muted small" }, x.locked ? x.unlock : itemStats(x))),
    h("div", { class: "bag-acts" }, ...actions.filter(Boolean)));
}
async function bagAct(path, body, done, btn = null) {
  if (btn) btn.disabled = true; // one purchase per click, however fast the second comes
  const r = await lbPost(path, body);
  if (!r?.bag) { if (btn) btn.disabled = false; return; }
  bagKeep(r.bag);
  if (done) toast(done);
  if (sbSection === "adventure") buildSidebar("adventure");
  render();
}
route(/^\/innerlatch\/adventure\/town\/(\w+)$/, async (params, attr) => {
  const token = renderSeq;
  const p = await partyGet();
  if (!p.signed_in) return partyGate(p, token);
  const [shop, m] = await Promise.all([api("/api/party/shop", { region: attr }), mapLoad()]);
  const name = townName(m, attr);
  setCrumbs([["Innerlatch", "#/innerlatch"], [t("Adventure"), "#/innerlatch/adventure"], [name]], token);
  const bag = await bagLoad(true).catch(() => null);
  const owned = new Map((bag?.items || []).map((x) => [x.id, x.count]));
  const buy = (x) => x.locked ? h("span", { class: "muted small" }, icon("shield"), t("Locked"))
    : h("button", { class: "btn small primary", type: "button", disabled: (shop.gold ?? 0) < x.price,
      title: (shop.gold ?? 0) < x.price ? t("Not enough gold") : null,
      onclick: (e) => bagAct("/api/party/buy", { id: x.id }, t("Bought {item}", { item: x.name }), e.currentTarget) }, t("Buy · {n} gold", { n: fmtNum(x.price) }));
  const byModule = new Map();
  for (const x of shop.gear || []) { if (!byModule.has(x.module)) byModule.set(x.module, []); byModule.get(x.module).push(x); }
  const mods = new Map((m.regions || []).flatMap((r) => r.modules).map((md) => [md.id, md.name]));
  const open = (shop.gear || []).filter((x) => !x.locked).length;
  return partyShell(name, shop.region?.about || null,
    h("div", { class: "town-bar" }, goldChip(shop.gold), h("a", { class: "btn small", href: "#/innerlatch/adventure/bag" }, icon("artifacts"), t("Open your bag")),
      h("a", { class: "btn small", href: `#/innerlatch/adventure?view=map&region=${attr}` }, icon("map"), t("Back to the map"))),
    h("section", { class: "card town-card" }, cardHead(t("Supplies"), { iconName: "zap", hint: t("For battles: use them from the Item command") }),
      h("ul", { class: "bag-rows" }, (shop.consumables || []).map((x) => itemRow({ ...x, count: owned.get(x.id) || 0 },
        owned.get(x.id) ? h("span", { class: "adv-flag" }, t("In your bag ×{n}", { n: owned.get(x.id) })) : null, buy(x))))),
    h("section", { class: "card town-card" }, cardHead(t("Gear"), { iconName: "tool",
      hint: t("{n} of {total} pieces open. A piece opens when one of your agents reaches its level in that skill: Lv 1, 3 or 5.", { n: fmtNum(open), total: fmtNum((shop.gear || []).length) }) }),
      [...byModule].map(([mod, xs]) => h("details", { class: `town-mod${xs.every((x) => x.locked) ? " all-locked" : ""}`, open: xs.some((x) => !x.locked) },
        h("summary", { class: "px-label" }, mods.get(mod) || mod, h("span", { class: "muted" }, ` · ${t("{n}/{total} open", { n: xs.filter((x) => !x.locked).length, total: xs.length })}`)),
        h("ul", { class: "bag-rows" }, xs.map((x) => itemRow({ ...x, count: owned.get(x.id) || 0 },
          owned.get(x.id) ? h("span", { class: "adv-flag" }, t("In your bag")) : null, buy(x))))))),
    h("p", { class: "muted small" }, t("Gear only changes battle numbers. Levels and XP still come from lessons alone.")));
});
function gearSlots(bag, agent, { compact = false } = {}) { // the three slots of one agent, each with what it can wear
  const worn = bag?.equipped?.[agent] || {};
  const owned = (bag?.items || []).filter((x) => x.kind === "gear");
  const wearers = new Map(); // a piece's id: who wears its copies, so moving one says from whom
  for (const [who, s] of Object.entries(bag?.equipped || {})) for (const x of Object.values(s || {})) if (x) wearers.set(x.id, [...(wearers.get(x.id) || []), who]);
  const optLabel = (x) => {
    const others = (wearers.get(x.id) || []).filter((w) => w !== agent);
    const free = x.count - (wearers.get(x.id) || []).length;
    return others.length && free <= 0 && worn[x.slot]?.id !== x.id ? t("{item} (worn by {agent})", { item: x.name, agent: others.map(agentShort).join(", ") }) : x.name;
  };
  return h("div", { class: `gear-slots${compact ? " compact" : ""}` }, ["weapon", "armor", "charm"].map((slot) => {
    const cur = worn[slot];
    const pick = h("select", { "aria-label": t("{slot} for {agent}", { slot: SLOT_LABEL[slot], agent: agentShort(agent) }),
      onchange: (e) => bagAct("/api/party/equip", { agent, slot, id: e.target.value || null }) },
      h("option", { value: "" }, cur ? t("Take it off") : t("Empty")),
      owned.filter((x) => x.slot === slot).map((x) => h("option", { value: x.id, selected: cur?.id === x.id }, optLabel(x))));
    return h("div", { class: "gear-slot" }, h("span", { class: "gear-ic" }, icon(SLOT_ICON[slot])),
      h("div", { class: "gear-main" }, h("span", { class: "px-label" }, SLOT_LABEL[slot]),
        compact ? null : h("b", null, cur ? cur.name : t("Empty")), cur && !compact ? h("div", { class: "muted small" }, itemStats(cur)) : null),
      pick);
  }));
}
route(/^\/innerlatch\/adventure\/bag$/, async () => {
  const token = renderSeq;
  const p = await partyGet();
  if (!p.signed_in) return partyGate(p, token);
  const [bag, m] = await Promise.all([bagLoad(true), mapLoad()]);
  setCrumbs([["Innerlatch", "#/innerlatch"], [t("Adventure"), "#/innerlatch/adventure"], [t("Bag")]], token);
  const wornN = new Map(); // how many copies of each piece are worn: only a spare one can be sold
  for (const s of Object.values(bag.equipped || {})) for (const x of Object.values(s || {})) if (x) wornN.set(x.id, (wornN.get(x.id) || 0) + 1);
  const worn = new Set(wornN.keys());
  const sell = (x) => {
    const spare = x.count - (wornN.get(x.id) || 0) > 0;
    return h("button", { class: "btn small", type: "button", disabled: !spare, title: spare ? null : t("Take it off first"),
      onclick: (e) => bagAct("/api/party/sell", { id: x.id }, t("Sold {item} for {n} gold", { item: x.name, n: fmtNum(x.sell) }), e.currentTarget) },
      t("Sell · {n} gold", { n: fmtNum(x.sell) }));
  };
  const gear = (bag.items || []).filter((x) => x.kind === "gear"), rest = (bag.items || []).filter((x) => x.kind !== "gear");
  const agents = (p.agents || []).filter((a) => a.level > 0);
  return partyShell(t("Bag"), t("What your party carries and wears. Gear only changes battle numbers."),
    h("div", { class: "town-bar" }, goldChip(bag.gold),
      (m.regions || []).map((r) => h("a", { class: "btn small", href: `#/innerlatch/adventure/town/${r.id}` }, pxIcon(r.id, 1), townName(m, r.id)))),
    h("section", { class: "card" }, cardHead(t("Equipped"), { iconName: "party" }),
      agents.length ? h("div", { class: "gear-grid" }, agents.map((a) => h("div", { class: "gear-agent" },
        h("a", { class: "gear-who", href: `#/innerlatch/agent/${a.agent}` }, partySprite(a.agent, { tier: a.tier, scale: 2, decorative: true }), h("b", null, a.name)),
        gearSlots(bag, a.agent, { compact: true })))) : h("div", { class: "empty" }, t("No one in the party yet."))),
    h("section", { class: "card" }, cardHead(t("Gear"), { iconName: "tool" }),
      gear.length ? h("ul", { class: "bag-rows" }, gear.map((x) => itemRow(x, worn.has(x.id) ? h("span", { class: "adv-flag" }, t("Worn")) : null, sell(x))))
        : h("div", { class: "empty" }, t("No gear yet. Win battles for gold, then visit a town."))),
    h("section", { class: "card" }, cardHead(t("Supplies and loot"), { iconName: "zap" }),
      rest.length ? h("ul", { class: "bag-rows" }, rest.map((x) => itemRow(x, sell(x))))
        : h("div", { class: "empty" }, t("Nothing yet. Towns sell supplies, and lessons drop loot when you first beat them."))));
});

// =====================================================================================
// A lesson, in a modal over the page: every lesson link in Innerlatch opens here instead of leaving for the dashboard's
// session page (GET /api/party/encounter: the lesson, its case and its roadmap modules). The session stays one link away.
// #/innerlatch/lesson/<id> opens it too (a link opened in a new tab), over the Party.
// =====================================================================================
function plainTitle(title) { return String(title || "").replace(/`([^`\n]+)`/g, "$1"); } // `code` in a title, as plain text
function lessonLink(id, title, attrs = {}) {
  return h("a", { ...attrs, href: `#/innerlatch/lesson/${id}`, html: codeSpans(title), onclick: (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return; // a new tab: the address opens it there
    e.preventDefault();
    openLesson(id);
  } });
}
let lessonDialog = null;
async function openLesson(id) {
  lessonDialog?.close();
  const body = h("div", { class: "lm-body" }, h("div", { class: "lm-loading muted" }, t("Loading…")));
  const close = h("button", { class: "icon-btn lm-close", type: "button", "aria-label": t("Close"), title: t("Close") }, icon("x"));
  const dlg = h("dialog", { class: "lesson-modal px-frame", "aria-labelledby": "lm-title" }, close, body);
  close.addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); }); // the backdrop
  dlg.addEventListener("close", () => { dlg.remove(); if (lessonDialog === dlg) lessonDialog = null; });
  lessonDialog = dlg;
  document.body.append(dlg);
  dlg.showModal();
  let r;
  try { r = await api("/api/party/encounter", { id }); } catch (e) {
    if (e.handled || !dlg.open) return;
    body.replaceChildren(h("h2", { id: "lm-title" }, t("Lesson")), h("p", { class: "muted" }, e.status === 404 ? t("This lesson isn't in your party's archive.") : t("Could not load: {error}", { error: e.message })));
    return;
  }
  if (!dlg.open) return;
  const l = r.lesson, a = (r.party || []).find((x) => x.agent === l.agent);
  const c = l.case && l.case.scene && l.case.question && l.case.answer ? l.case : null;
  const meta = [agentName(l.agent), l.at ? fmtDateY(partyDay(l.at)) : null, STAGE_LABEL[l.stage || "provisional"] || l.stage,
    l.xp ? t("+{n} XP", { n: l.xp }) : null, l.project].filter(Boolean).join(" · ");
  const counted = (l.modules || []).length > 0;
  body.replaceChildren(...[ // replaceChildren prints a null: the parts a lesson doesn't have are left out
    h("div", { class: "lm-head" }, PARTY_AGENTS.includes(l.agent) ? partySprite(l.agent, { tier: a?.tier, scale: 2, decorative: true }) : agentIcon(l.agent),
      h("div", null, kindChip(l.kind), h("h2", { id: "lm-title", html: codeSpans(l.title) }), h("div", { class: "muted small" }, meta))),
    counted ? h("div", { class: "lm-modules" }, h("span", { class: "px-label" }, t("On the roadmap")),
      l.modules.map((m) => h("span", { class: "lm-module" }, pxIcon(m.attr, 1), m.name,
        a?.skills?.[m.id] ? h("b", null, t("Lv {n}", { n: a.skills[m.id] })) : null)))
      : h("p", { class: "muted small" }, t("This lesson doesn't count toward the roadmap: it matched none of its modules.")),
    c ? h("div", { class: "lm-case" }, h("span", { class: "px-label" }, t("The scene")), mdEl(c.scene),
      h("span", { class: "px-label" }, t("The question")), mdEl(c.question),
      h("details", null, h("summary", null, t("The answer")), mdEl(c.answer))) : null,
    l.body ? mdEl(l.body, "lm-text") : null,
    h("div", { class: "lm-actions" },
      counted ? h("a", { class: "btn small primary", href: r.cleared ? `#/innerlatch/adventure?lesson=${l.id}` : `#/innerlatch/adventure/battle/${l.id}`,
        onclick: () => dlg.close() }, icon("map"), r.cleared ? t("Find it on the map") : t("Battle it")) : null,
      l.session_id ? h("a", { class: "btn small", href: `#/session/${l.session_id}`, onclick: () => dlg.close(), title: t("Leaves Innerlatch for the dashboard's session page") },
        icon("door"), t("Open the session in Interlatch")) : null)].filter(Boolean));
  body.querySelectorAll(".gloss").forEach((el) => glossify(el));
}
route(/^\/innerlatch\/lesson\/(\d+)$/, async (params, id) => {
  history.replaceState(null, "", "#/innerlatch");
  lastHash = location.hash;
  setTimeout(() => openLesson(Number(id)), 0);
  return routes.find(([rx]) => rx.test("/innerlatch"))[1](params); // the Party, with the lesson over it
});

// =====================================================================================
// The door: Innerlatch opens to someone who walks through it signed in, with Google on the computer that runs
// Interlatch or with a hub's own sign-in. app.js shows it over the page that's showing, whenever someone comes in from
// the dashboard or an address; `to` is the page they were going to. Click it: signed in, it swings open and the page
// goes through; signed out, it's locked and asks for the sign-in, then opens by itself once that comes back. The
// hosted sign-in page returns in a new tab (#/leaderboard?lb_token=…), which meets the door too and finishes there.
// =====================================================================================
let doorScene = null;
function doorLeaf() {
  return h("span", { class: "ds-leaf" },
    h("span", { class: "ds-panel" }), h("span", { class: "ds-panel" }), h("span", { class: "ds-panel" }), h("span", { class: "ds-panel" }),
    h("span", { class: "ds-knob" }), h("span", { class: "ds-keyhole" }));
}
// Leaving: the door you came through stands open behind you, swings shut, and the whole scene shrinks back into the
// rail's door icon while the dashboard comes back. app.js calls it once the dashboard's rail is drawn again; it never
// blocks a click (the page underneath is already the dashboard). Not with animations off.
function farewell() {
  if (stillMotion() || doorScene) return;
  const icon = document.querySelector("#rail a.rail-door")?.getBoundingClientRect();
  const scene = h("div", { class: "door-scene door-exit", "aria-hidden": "true", "data-state": "opening" },
    h("div", { class: "ds-sky" }),
    h("div", { class: "ds-stage" }, h("span", { class: "ds-door" }, h("span", { class: "ds-arch" }, h("span", { class: "ds-light" }), doorLeaf()),
      h("span", { class: "ds-spill" }))));
  // it shrinks into the door icon (gone: the middle of the bottom of the screen)
  setStyle(scene, { "--exit-x": `${icon?.width ? icon.left + icon.width / 2 : innerWidth / 2}px`, "--exit-y": `${icon?.height ? icon.top + icon.height / 2 : innerHeight}px` });
  document.body.append(scene);
  void scene.offsetWidth; // drawn open first, so the swing shut runs
  scene.dataset.state = "shut";
  setTimeout(() => scene.classList.add("gone"), 700);
  setTimeout(() => {
    scene.remove();
    const door = document.querySelector("#rail a.rail-door");
    door?.classList.add("landed");
    setTimeout(() => door?.classList.remove("landed"), 900);
  }, 1250);
}
function doorSigninLine(p) {
  return p.can_sign_in ? t("Sign in with Google to open it. It's the leaderboard's account, but signing in here doesn't put you on the board.")
    : p.error || t("Signing in happens on the computer that runs Interlatch: open Innerlatch there and sign in with Google. On a team hub, sign in to the hub as one of its people.");
}
async function threshold(to) {
  if (doorScene) return;
  const [path, qs] = to.replace(/^#/, "").split("?");
  const params = Object.fromEntries(new URLSearchParams(qs || ""));
  let dest = `#${path}`; // the sign-in's session never stays in an address
  delete params.lb_token; delete params.lb_state;
  const rest = new URLSearchParams(params).toString();
  if (rest) dest += `?${rest}`;
  const back = to.match(/[?&]lb_token=([^&]*)/), state = to.match(/[?&]lb_state=([^&]*)/);
  let p = null, poll = 0, opening = false;
  const still = stillMotion(), returnFocus = document.activeElement;

  const hint = h("p", { class: "ds-hint" }, t("Click the door to go in"));
  const lock = h("div", { class: "ds-lock", hidden: true });
  const motes = h("div", { class: "ds-motes", "aria-hidden": "true" }, Array.from({ length: 18 }, (_, i) =>
    h("i", { style: { left: `${(i * 53) % 100}%`, animationDelay: `${-((i * 7) % 11)}s`, animationDuration: `${9 + (i % 5) * 2}s` } })));
  const leaf = doorLeaf();
  const padlock = h("span", { class: "ds-padlock", "aria-hidden": "true" }, pxIcon("lock", 4));
  const door = h("button", { class: "ds-door", type: "button", "aria-label": t("Open the door"), "aria-describedby": "ds-hint-text",
    onclick: () => knock() },
    h("span", { class: "ds-arch" }, h("span", { class: "ds-light" }), leaf, padlock), h("span", { class: "ds-spill", "aria-hidden": "true" }));
  hint.id = "ds-hint-text";
  const close = () => {
    if (opening) return;
    clearTimeout(poll);
    document.removeEventListener("keydown", onKey, true);
    scene.classList.add("leaving");
    setTimeout(() => { scene.remove(); doorScene = null; }, still ? 0 : 260);
    if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
  };
  const onKey = (e) => { if (e.key === "Escape" && !pal.open) { e.preventDefault(); close(); } };
  const scene = h("div", { class: "door-scene", id: "door-scene", role: "dialog", "aria-modal": "true", "aria-labelledby": "ds-title", "data-state": "closed" },
    h("div", { class: "ds-sky", "aria-hidden": "true" }), motes,
    h("div", { class: "ds-stage" },
      h("h1", { class: "ds-title", id: "ds-title" }, "Innerlatch"),
      door, hint, lock),
    h("button", { class: "btn small ds-close", type: "button", onclick: close }, t("Not now")),
    h("div", { class: "ds-flash", "aria-hidden": "true" }));
  doorScene = scene;
  document.body.append(scene);
  document.addEventListener("keydown", onKey, true);
  door.focus({ preventScroll: true });

  const setState = (st) => { scene.dataset.state = st; };
  const open = () => { // through the door: it swings in, the light takes the screen, and Innerlatch is on the other side
    if (opening) return;
    opening = true;
    clearTimeout(poll);
    setState("opening");
    lock.hidden = true;
    hint.textContent = t("The door is open");
    const through = () => {
      document.removeEventListener("keydown", onKey, true);
      enterRealm(dest);
      scene.classList.add("through");
      setTimeout(() => { scene.remove(); doorScene = null; }, still ? 0 : 700);
    };
    if (still) through(); else setTimeout(through, 1500);
  };
  const locked = (fresh) => { // signed out: the door rattles, and says how to open it
    setState("locked");
    if (fresh && !still) { leaf.classList.remove("rattle"); void leaf.offsetWidth; leaf.classList.add("rattle"); }
    hint.textContent = t("The door is locked");
    const waiting = p.signing_in;
    lock.replaceChildren(...[h("p", null, doorSigninLine(p)),
      p.can_sign_in ? h("div", { class: "lb-actions" },
        h("button", { class: "btn primary", type: "button", onclick: async () => { await openSignin("/api/party/signin"); await refresh(); } }, t("Sign in with Google")),
        waiting ? h("button", { class: "btn", type: "button", onclick: async () => { if (await lbPost("/api/leaderboard/cancel")) await refresh(); } }, t("Cancel")) : null) : null,
      waiting ? h("p", { class: "muted small" }, t("Finish signing in on the page that opened. The door opens by itself once you have.")) : null].filter(Boolean)); // replaceChildren prints a null
    lock.hidden = false;
    clearTimeout(poll);
    if (waiting) poll = setTimeout(refresh, 3000); // the sign-in runs in another tab
  };
  const load = async () => {
    partyCache = null;
    try { p = await partyLoad(); } catch (e) { p = { signed_in: false, signing_in: false, can_sign_in: false, error: e.message }; }
    return p;
  };
  const refresh = async () => { // after a sign-in started, or while it runs: unlocked, the door opens by itself
    if (!scene.isConnected) return;
    await load();
    if (!scene.isConnected) return;
    if (p.signed_in) { hint.textContent = t("Unlocked"); setTimeout(open, still ? 0 : 500); } else locked(false);
  };
  const knock = async () => {
    if (opening) return;
    if (!p) await load();
    if (p.signed_in) open(); else locked(true);
  };

  if (back && state) { // back from the hosted sign-in page: finish it here, then the door unlocks
    hint.textContent = t("Unlocking…");
    const r = await lbPost("/api/leaderboard/finish", { state: decodeURIComponent(state[1]), token: decodeURIComponent(back[1]) });
    if (r?.name) toast(r.next === "party" ? t("Signed in as {name}", { name: r.name }) : t("Signed in as {name}. Your totals are on the board.", { name: r.name }), 6000);
    if (r?.next === "party") dest = "#/innerlatch"; // the Party started it; the Leaderboard's own comes back there
    await refresh();
    return;
  }
  load(); // ready before the click
}

// =====================================================================================
// The shell's side: Innerlatch's rail, sidebars, crumbs and palette, which app.js shows in place of its own in here
// =====================================================================================
// the leaderboard and the arena keep a Google credential on this computer: only from here, and not on a dedicated hub
// or for someone a hub limits to some of its projects (the server refuses them anyway)
function realmHere() { return ME?.viewer?.here !== false && !dedicated() && !limited(); }
const REALM_SECTIONS = [
  { key: "party", label: t("Party"), href: "#/innerlatch", icon: "party" }, // a hub's people have theirs too
  { key: "adventure", label: t("Adventure"), href: "#/innerlatch/adventure", icon: "map" },
  { key: "leaderboard", label: t("Leaderboard"), href: "#/innerlatch/leaderboard", icon: "leaderboard", here: true },
  { key: "arena", label: t("Arena"), href: "#/innerlatch/arena", icon: "arena", here: true },
];
function realmSectionOf(path) {
  if (path.startsWith("/innerlatch/adventure")) return "adventure";
  if (path.startsWith("/innerlatch/leaderboard")) return "leaderboard";
  if (path.startsWith("/innerlatch/arena")) return "arena";
  return "party";
}
function realmCrumbs(path) {
  const home = ["Innerlatch", "#/innerlatch"], party = [t("Party"), "#/innerlatch"];
  if (path === "/innerlatch/quests") return [home, party, [t("Quests")]];
  if (path === "/innerlatch/journey") return [home, party, [t("Journey")]];
  if (path === "/innerlatch/adventure") return [home, [t("Adventure")]];
  if (path === "/innerlatch/leaderboard") return [home, [t("Leaderboard")]];
  if (path === "/innerlatch/arena") return [home, [t("Arena")]];
  if (path === "/innerlatch") return [home, [t("Party")]];
  return [home]; // a character or a battle names itself
}
window.Innerlatch = Object.freeze({
  threshold,
  farewell,
  sections: () => REALM_SECTIONS.filter((x) => !x.here || realmHere()),
  sectionOf: realmSectionOf,
  crumbs: realmCrumbs,
  owns: (section) => REALM_SECTIONS.some((x) => x.key === section),
  sidebar: (section, box) => (section === "party" ? partySidebar(box) : section === "adventure" ? adventureSidebar(box) : competeSidebar(box)),
  commands: (nav) => [nav(t("Party"), "#/innerlatch", "party", t("your agents as characters")),
    nav(t("Quests"), "#/innerlatch/quests", "todo", t("Party")), nav(t("Journey"), "#/innerlatch/journey", "history", t("Party")),
    nav(t("Adventure"), "#/innerlatch/adventure", "map", t("every lesson as a world to explore")),
    ...(realmHere() ? [nav(t("Leaderboard"), "#/innerlatch/leaderboard", "leaderboard"), nav(t("Arena"), "#/innerlatch/arena", "arena", t("Leaderboard"))] : [])],
});
