/* Innerlatch's Adventure battles: a turn-based fight, classic JRPG style, against one lesson's monster (or a main
   quest's boss). The party is the agent that taught the lesson plus up to two allies; the player picks a command for
   each hero in turn, then the monster acts. Every number comes from the lesson and the party (levels, attribute
   levels, skill levels); the dice are seeded (lesson id × 1009 + attempt), so the same choices give the same fight.

   Honesty: a battle never changes a level or XP. Winning only marks the encounter cleared (the page does that in
   onWin); the victory text says the XP was earned when the agent learned the lesson. A boss can't be beaten here at
   all: it holds at 1 HP until the person marks the milestone done on the Quests page.

   Study: when the page has a question for the lesson (opts.study: its scene and question, at once or as a promise),
   the fight is a study battle: no Attack, Skill or the rest, only its options (in an order of the fight's own) to
   choose from and Answer to lock one in. The lesson's title stays hidden until it's answered. The page checks the
   answer (opts.onAnswer): "got it" is the finishing blow, anything else lets the monster slip away (phase "missed"),
   with why that one is wrong and the answer shown. One answer a fight. Without a question after all (none came), it's
   an ordinary fight.

   Art is drawn on a <canvas> (Sprites for the agents, Scene.art for the monsters, with a pixel blob if either is
   missing); the menu, the party's bars and the battle log are DOM. CSP: no inline styles (CSSOM only), no eval, no
   data:/blob: images.

   ---------------------------------------------------------------------------------------------------------------
   Battle.mount(host, opts) -> { destroy(), redraw() }
     opts.encounter   GET /api/party/encounter's payload, or for a boss {boss: <a /api/party/map boss>, party: [...]};
                      party[i].gear {atk, hp, mp, def, skills: {module: extra}} and encounter.items [{id, name, kind,
                      count}] (consumables and loot) when the person has them
     opts.attempt     int, 0 on the first try; the page bumps it on Retry (new dice, still reproducible)
     opts.labels      overrides for LABELS below (the page passes Japanese)
     opts.reducedMotion  no shaking, tweens or idle animation; every update is instant
     opts.height      css px for the battle stage (default: the stylesheet's clamp)
     opts.autofocus   focus the first command on mount (default true)
     opts.onWin(lessonId)      the monster is beaten (once; the page marks the encounter cleared). It may return a
                               Promise of {gold, loot: [item]} (the win screen shows them when it resolves), of null
                               (a lesson that paid out before: "no gold this time") or of false (saving the win failed:
                               "try it again from the map"); undefined, or no promise, shows no reward line
     opts.onUseItem(id)        an item was used (once per use; the page POSTs /api/party/use); the battle counts it down
     opts.study                {scene, question, options: [text]} | a Promise of it (or of null: no study battle now)
     opts.onAnswer(i)          the pick (an index into study.options) -> Promise of {verdict: "got_it"|"partly"|"not_yet",
                               feedback, principle, answer, back_in
                               (days until a lesson got right comes back), owned};
                               a rejection's message is said, and the person may answer again
     opts.onLose()             Retry pressed on the defeat screen (the page remounts with attempt + 1)
     opts.onRun()              Run chosen (no confirmation); the page goes back to the map
     opts.onExit()             "Back to the map" on any end screen
     opts.onReadLesson(id)     "Read the lesson" on the victory screen (the page opens the lesson modal)
     opts.onQuests()           "Open Quests" when a boss holds at 1 HP
     opts.sessionHref(id)      -> href for "Open the session" on the victory screen (left out without it)
     opts.formatDate(at)       -> how a lesson's date ("2026-10-14") reads in the victory text (default: as is)

   Battle.rules: pure, DOM-free (node runs them in the tests)
     rng(seed) -> () => [0, 1)             mulberry32, as scene.js
     seedOf(encounter, attempt) -> uint32  lesson id × 1009 + attempt (a boss: a hash of its id × 1009 + attempt)
     difficulty(encounter) -> d            1 + 0.05·min(xp, 18) + stage (0 / 0.15 / 0.3); a boss 1.5 + 0.15·n
     gearOf(member) -> {atk, hp, mp, def, skills}   party[i].gear, cleaned (def 0..0.6)
     heroStats(member, attr, {gear?}) -> {hp, mp, atk, def}   level and attribute level, then gear on top (def cuts damage
                                           taken by that fraction); gear's skill levels add to the module's skill level
                                           in battle only, shown apart ("+N gear"), never as the hero's level
     pickParty(encounter) -> members       the teacher first, then the two highest-level others with level > 0
                                           (a boss: the three strongest)
     stats(encounter, labels?) -> {attr, modules, heroes, foe}   the numbers a fight starts with
     setup(encounter, attempt, labels?) -> state   (state.events: the intro to play, state.log: every line said)
     skillOptions(state, heroIndex) -> [{id, name, level, own, gear, cost, usable}]   level = own + gear
     itemOptions(state) -> [{id, name, kind, count, target, usable}]   what the Item command offers the active hero
     ask(state, study) -> events           make it a study battle (its scene and question said), before it's answered
     heroAction(state, action, rng?) -> events      action {type: "attack"|"skill"|"item"|"answer"|"defend"|"run", module?,
                                                    id?, target?, verdict?, feedback?, principle?, answer?}; an answer
                                                    is the judged one; an item uses the hero's turn and counts down by one:
         c:potion +40% HP (the active hero), c:ether +50% MP (the active hero), c:phoenix revives the first knocked-out
         hero at 30% HP (not usable when nobody is down), c:smoke a sure escape, l:patch +25% HP everyone, l:sign 40% less
         damage for 2 monster turns, l:adr the monster loses its next turn, l:insight +30% MP everyone, l:template the
         next blow ×1.6, l:script 25% of the monster's max HP (a boss 10%, and it still holds at 1 HP)
     monsterTurn(state, rng?) -> events
     policy(state) -> action               a plain Attack/Skill player (the tests' bot)
   Balance: the monster is sized to the party without gear plus half of what gear adds, so an ungeared party meets the
   same fight as before gear existed and gear makes fights easier without making them instant.
     simulate(encounter, attempt, policy?, maxRounds?) -> state
     summary(state, labels?, formatDate?) -> {title, text, note, feedback, quoteTitle, quote, principle}   the end screen's
                                           words (note, feedback, principle: the study battle's, "" without one)
     rewardLines(result, labels?) -> [{kind: "gold"|"loot"|"none"|"failed", text}]   the win screen's reward lines
   Battle.LABELS, Battle.LABEL_VARS (the placeholders each label may use), Battle.format(template, vars)

   state.phase: "hero" (waiting for the active hero's command) → "monster" → "hero" … until "won", "lost", "ran",
   "missed" (a study battle answered wrong), "held" (a boss at 1 HP) or "done" (a boss whose milestone is already done).
   --------------------------------------------------------------------------------------------------------------- */
(() => {
  "use strict";

  // =====================================================================================
  // Labels: every word the battle shows. {placeholders} are filled in; LABEL_VARS lists the ones each label may use.
  //   region       the battle's accessible name                          {monster}
  //   scene        the canvas's description                              {monster} {heroes}
  //   epithet      the lesson's title under the monster's name           {title}
  //   bossEpithet  a boss's milestone under its name                     {n} {text} {track}
  //   round        the round counter                                     {n}
  //   level / hp / mp / hpValue   the party window                      {n} / – / – / {hp} {max}
  //   taught       the tag on the hero who taught the lesson
  //   prompt       the menu's heading                                    {hero}
  //   attack / skill / item / answer / defend / run / back   the commands
  //   introStudy {monster}: the opening line of a study battle (no title: it would give the answer away; its scene and
  //     question show over the options); choose, preparing: under the options, or while they're written; checking, answerAgain
  //     {error}: while an answer is checked, or when it couldn't be; weakTag: under the finishing blow; lineGot {monster}
  //     {n}; lineNotYet, lineMiss {monster}, missTitle {monster}: a wrong answer, the monster slipping away
  //   studyGot {module}, studyBack {n}, studyOwned {module} {n}, studyPartly, studyNot, studyNone; principleTitle: the
  //     end screen's study lines
  //   skillLevel / skillGear / skillNone / skillCost   a skill's row: its level, what gear adds ({n}), its MP cost
  //   itemCount / itemTarget   an item's row: how many are left ({n}), who it will be used on ({hero})
  //   itemNone     the bag is empty
  //   itemNoTarget / itemFullHp / itemFullMp   why an item can't be used now (shown on its row in place of its hint,
  //                    and said in the log): nobody knocked out (Phoenix Down), nobody hurt (Potion, Patch), nobody short
  //                    of MP (Ether, Insight); nothing is spent
  //   more         the cue (for screen readers too) that a submenu has more rows below
  //   hint.<item id>   an item's effect, under its name (c:potion, c:ether, c:phoenix, c:smoke, l:patch, l:sign, l:adr,
  //                    l:insight, l:template, l:script); hint.l:script.boss the Script's in a boss fight (10%)
  //   attrSkill    a boss fight's one skill: the region's attribute      {attr}
  //   intro / introBoss   the opening line                              {monster} {title} / {monster} {text}
  //   lineAttack, lineCrit, lineSkill, lineSkillWeak, lineDefend, lineDefendFull, lineNoMp, lineRun: a hero's turn
  //   lineItem {hero} {item}, then lineItemHeal / lineItemMp / lineRevive {target} {n}, lineSmoke, lineWard {n},
  //     lineSkipNext {monster}, lineTemplate, lineScript {item} {monster} {n}; lineNoItem {item}: using an item
  //   lineHit, lineGuarded, lineDodge, lineDrain, lineHeal, lineKo, lineCharged, lineSkipped: the monster's turn
  //   lineWin / lineLose / lineHold: the end of a fight
  //   win, winText, winAnswer, winExcerpt, readLesson, openSession, backToMap: the victory screen
  //   rewardWait, rewardGold {n}, rewardLoot {item}, rewardPaid, rewardFailed: what onWin's promise brings (gold and
  //     loot; null: nothing, the lesson paid out before; false: the win couldn't be saved)
  //   lose, loseText, retry: the defeat screen; hold, holdText, openQuests: a boss at 1 HP
  //   bossDone, bossDoneText: a boss already defeated on the Quests page
  //   monster.<kind>   a lesson kind's monster name; boss.<1-4> the four bosses; attr.<id> a region's name
  //   move.<kind>.<id> a monster's move, said as it strikes                {monster}
  // =====================================================================================
  const LABELS = {
    region: "Battle: {monster}", scene: "{monster} faces {heroes}", epithet: "«{title}»",
    bossEpithet: "Milestone {n}: {text}", round: "Round {n}", level: "Lv {n}", hp: "HP", mp: "MP", hpValue: "{hp}/{max}",
    taught: "Taught it", prompt: "What will {hero} do?", log: "Battle log", commands: "Commands",
    attack: "Attack", skill: "Skill", item: "Item", answer: "Answer", defend: "Defend", run: "Run", back: "Back",
    skillLevel: "Lv {n}", skillGear: "+{n} gear", skillNone: "untrained", skillCost: "{n} MP", attrSkill: "{attr} lore",
    itemCount: "×{n}", itemTarget: "→ {hero}", itemNone: "The bag is empty.", itemNoTarget: "Nobody is knocked out.",
    itemFullHp: "Everyone is at full HP.", itemFullMp: "Everyone is at full MP.", more: "More below",
    "hint.c:potion": "Heals 40% HP", "hint.c:ether": "Restores 50% MP", "hint.c:phoenix": "Revives a fallen hero",
    "hint.c:smoke": "A sure escape", "hint.l:patch": "Heals everyone 25% HP", "hint.l:sign": "40% less damage, 2 turns",
    "hint.l:adr": "The monster loses a turn", "hint.l:insight": "Everyone recovers 30% MP", "hint.l:template": "Next blow ×1.6",
    "hint.l:script": "Hits for 25% of its HP", "hint.l:script.boss": "Hits a boss for 10% of its HP",
    intro: "A wild {monster} appears: «{title}»", introBoss: "{monster} bars the way: «{text}»",
    lineAttack: "{hero} attacks: {n} damage.", lineCrit: "{hero} lands a critical hit: {n} damage!",
    lineSkill: "{hero} uses {skill} (Lv {lv}): {n} damage!", lineSkillWeak: "{hero} tries {skill} untrained: {n} damage.",
    lineDefend: "{hero} defends and steadies (+{mp} MP).", lineDefendFull: "{hero} defends.", lineNoMp: "{hero} doesn't have the MP for {skill}.",
    lineRun: "The party slips away.",
    lineItem: "{hero} uses {item}.", lineItemHeal: "{target} recovers {n} HP.", lineItemMp: "{target} recovers {n} MP.",
    lineRevive: "{target} is back on their feet ({n} HP).", lineSmoke: "Smoke fills the field: the party escapes.",
    lineWard: "The party braces: less damage for {n} turns.", lineSkipNext: "{monster} will lose its next turn.",
    lineSkipped: "{monster} is still rethinking and loses its turn.", lineTemplate: "The next blow will hit ×1.6.",
    lineScript: "{item} hits {monster} for {n} damage!", lineNoItem: "No {item} left.",
    lineHit: "{hero} takes {n} damage.", lineGuarded: "{hero} blocks: only {n} damage.", lineDodge: "{hero} dodges!",
    lineDrain: "{hero} loses {mp} MP.", lineHeal: "{monster} recovers {n} HP.", lineKo: "{hero} is knocked out!",
    lineCharged: "{monster} is gathering strength. Defend!",
    introStudy: "A wild {monster} appears! It's a lesson from your own sessions.",
    checking: "Checking…", answerAgain: "Couldn't check it: {error}", weakTag: "weak spot",
    lineGot: "You got it! {monster}'s weak spot is exposed: {n} damage.",
    lineNotYet: "Not this one: the right answer is on the end screen.", lineMiss: "{monster} slips away.",
    missTitle: "{monster} slipped away", choose: "Choose an answer, then Answer.", preparing: "Preparing the question…",
    studyGot: "You understood it: it counts toward your readiness in {module}.",
    studyBack: "It comes back in {n} days for another look.",
    studyOwned: "Got it again on another day: it's yours now ({module}). Next look in {n} days.",
    studyPartly: "Partly there: it comes back tomorrow so you can try again.", studyNot: "Not yet: it comes back tomorrow.",
    studyNone: "You didn't answer this one: it stays open for you.", principleTitle: "The principle",
    lineWin: "{monster} is beaten!", lineLose: "The party falls back.",
    lineHold: "{monster} holds at 1 HP: only you can defeat this boss.",
    win: "{monster} is beaten!", winText: "{agent} learned this on {date}: +{xp} XP then. Battles don't change levels.",
    winAnswer: "The answer", winExcerpt: "From the lesson", readLesson: "Read the lesson", openSession: "Open the session",
    backToMap: "Back to the map",
    rewardWait: "Counting the spoils…", rewardGold: "+{n} gold", rewardLoot: "Found: {item}",
    rewardPaid: "Already cleared before: no gold this time.",
    rewardFailed: "The win wasn't saved: try it again from the map.",
    lose: "The party falls back", loseText: "Nothing is lost: battles don't change levels. Retry rolls new dice.",
    retry: "Retry",
    hold: "{monster} holds at 1 HP", holdText: "Only you can defeat this boss: mark the milestone done on the Quests page.",
    openQuests: "Open Quests",
    bossDone: "{monster} is already defeated", bossDoneText: "You marked this milestone done: «{text}».",
    "monster.fix": "Glitch", "monster.gotcha": "Mal", "monster.decision": "Breaker", "monster.learning": "Wisp",
    "monster.pattern": "Gearling", "monster.command": "Scrollkin",
    "boss.1": "Giant Glitch", "boss.2": "Mal the Masked", "boss.3": "Firewall Titan", "boss.4": "Great Breaker",
    "attr.craft": "Craft", "attr.data": "Data", "attr.ops": "Ops", "attr.guard": "Guard", "attr.arcana": "Arcana",
    "attr.lore": "Lore",
    "move.fix.bite": "{monster} chews through a test!", "move.fix.swarm": "{monster} multiplies across the codebase!",
    "move.fix.heisen": "{monster} flickers like a Heisenbug!", "move.fix.charge": "{monster} burrows into the stack…",
    "move.fix.crash": "{monster} crashes the build!",
    "move.gotcha.trap": "{monster} springs a trap!", "move.gotcha.edge": "{monster} finds an edge case nobody checked!",
    "move.gotcha.silent": "{monster} fails silently…", "move.gotcha.charge": "{monster} hides behind a default…",
    "move.gotcha.default": "{monster}'s hidden default goes off!",
    "move.decision.fork": "{monster} forks the road!", "move.decision.tradeoff": "{monster} forces a trade-off!",
    "move.decision.bikeshed": "{monster} starts a bikeshed and catches its breath.",
    "move.decision.charge": "{monster} weighs its options…", "move.decision.flip": "{monster} flips the switch!",
    "move.learning.glare": "{monster} shines a blinding insight!",
    "move.learning.rabbit": "{monster} leads everyone down a rabbit hole!",
    "move.learning.fog": "{monster} spreads a fog of jargon…", "move.learning.charge": "{monster} gathers light…",
    "move.learning.epiphany": "{monster} bursts into an epiphany!",
    "move.pattern.copy": "{monster} copy-pastes itself!", "move.pattern.grind": "{monster} grinds its gears!",
    "move.pattern.boiler": "{monster} buries the party in boilerplate!", "move.pattern.charge": "{monster} spins up…",
    "move.pattern.repeat": "{monster} repeats itself at full speed!",
    "move.command.flag": "{monster} passes the wrong flag!", "move.command.pipe": "{monster} pipes everything into everything!",
    "move.command.sudo": "{monster} runs it again with sudo!", "move.command.charge": "{monster} types rm -rf…",
    "move.command.enter": "{monster} hits Enter!",
    "move.boss.deadline": "{monster} pulls the deadline closer!", "move.boss.scope": "{monster} creeps the scope!",
    "move.boss.meeting": "{monster} calls a meeting and recovers.", "move.boss.charge": "{monster} gathers the whole milestone…",
    "move.boss.unleash": "{monster} unleashes the milestone!",
  };
  const LABEL_VARS = {
    region: ["monster"], scene: ["monster", "heroes"], epithet: ["title"], bossEpithet: ["n", "text", "track"], round: ["n"],
    level: ["n"], hpValue: ["hp", "max"], prompt: ["hero"], skillLevel: ["n"], skillCost: ["n"], attrSkill: ["attr"],
    intro: ["monster", "title"], introBoss: ["monster", "text"],
    lineAttack: ["hero", "n"], lineCrit: ["hero", "n"], lineSkill: ["hero", "skill", "lv", "n"], lineSkillWeak: ["hero", "skill", "n"],
    lineDefend: ["hero", "mp"], lineDefendFull: ["hero"], skillGear: ["n"], itemCount: ["n"], itemTarget: ["hero"],
    lineItem: ["hero", "item"], lineItemHeal: ["target", "n"], lineItemMp: ["target", "n"], lineRevive: ["target", "n"],
    lineWard: ["n"], lineSkipNext: ["monster"], lineSkipped: ["monster"], lineScript: ["item", "monster", "n"], lineNoItem: ["item"],
    rewardGold: ["n"], rewardLoot: ["item"], lineNoMp: ["hero", "skill"], lineHit: ["hero", "n"], lineGuarded: ["hero", "n"],
    lineDodge: ["hero"], lineDrain: ["hero", "mp"], lineHeal: ["monster", "n"], lineKo: ["hero"], lineCharged: ["monster"],
    lineWin: ["monster"], lineHold: ["monster"], win: ["monster"], winText: ["agent", "date", "xp"], hold: ["monster"],
    bossDone: ["monster"], bossDoneText: ["text"], introStudy: ["monster"], answerAgain: ["error"],
    lineGot: ["monster", "n"], studyGot: ["module"],
    lineMiss: ["monster"], missTitle: ["monster"],
    studyBack: ["n"], studyOwned: ["module", "n"],
  };
  for (const k of Object.keys(LABELS)) if (k.startsWith("move.")) LABEL_VARS[k] = ["monster"];
  for (const k of Object.keys(LABELS)) if (!LABEL_VARS[k]) LABEL_VARS[k] = [];

  const fill = (tpl, vars) => String(tpl == null ? "" : tpl).replace(/\{(\w+)\}/g, (m, k) => (vars && k in vars ? String(vars[k]) : m));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const num = (v, d = 0) => { const n = Number(v); return Number.isFinite(n) ? n : d; };
  const AGENT_ORDER = ["claude", "codex", "copilot", "bob", "antigravity", "opencode"];
  const KINDS = ["fix", "gotcha", "decision", "learning", "pattern", "command"];
  const ATTRS = ["craft", "data", "ops", "guard", "arcana", "lore"];
  const KIND_TYPE = { fix: "glitch", gotcha: "mal", decision: "breaker", learning: "wisp", pattern: "gears", command: "scroll" };
  const KIND_COLOR = { fix: "#f2666c", gotcha: "#8d93a8", decision: "#c9d8ee", learning: "#e4dcff", pattern: "#b9c7da",
    command: "#f4efe4", boss: "#e07a55" };

  // =====================================================================================
  // Rules (pure)
  // =====================================================================================
  const STAGE_D = { provisional: 0, established: 0.15, canonical: 0.3 };
  const FOE_ROUNDS = 4;     // a lesson's monster takes ~4·d rounds of the party's plain attacks
  const BOSS_ROUNDS = 3.4;
  const WIPE_ROUNDS = 10;   // the monster deals the party's whole HP in ~10/d^0.7 rounds (a boss: 10/d^0.5)
  const SKILL_COST = 6, CRIT = 1 / 16, GUARD = 0.45;
  const GEAR_SHARE = 0.5;   // the part of the party's gear the monster is sized to
  const VERDICTS = ["got_it", "partly", "not_yet"]; // a checked answer: only "got it" beats the monster

  function rng(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function hashStr(s) {
    let h = 2166136261;
    for (const ch of String(s)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0;
    return h >>> 0;
  }
  function seedOf(encounter, attempt) {
    const enc = encounter || {}, att = Math.max(0, Math.floor(num(attempt)));
    const base = enc.boss ? hashStr(enc.boss.id || enc.boss.text || "boss") % 4000000 : Math.floor(num(enc.lesson && enc.lesson.id));
    return (Math.imul(base, 1009) + att) >>> 0;
  }
  function difficulty(encounter) {
    const enc = encounter || {};
    if (enc.boss) return 1.5 + 0.15 * clamp(Math.floor(num(enc.boss.n, 1)), 1, 4);
    const l = enc.lesson || {};
    return 1 + 0.05 * clamp(num(l.xp), 0, 18) + (STAGE_D[l.stage] || 0);
  }
  // what an agent wears, summed (party[i].gear): {atk, hp, mp, def (a damage cut, 0..0.6), skills: {module: extra}}
  function gearOf(member) {
    const g = (member && member.gear) || {}, skills = {};
    for (const [k, v] of Object.entries(g.skills && typeof g.skills === "object" ? g.skills : {})) skills[k] = clamp(Math.floor(num(v)), 0, 30);
    return { atk: clamp(Math.round(num(g.atk)), 0, 999), hp: clamp(Math.round(num(g.hp)), 0, 9999), mp: clamp(Math.round(num(g.mp)), 0, 999),
      def: clamp(num(g.def), 0, 0.6), skills };
  }
  // a hero's numbers: level and the attribute level for the lesson's region, then gear on top (gear changes battle
  // numbers only, never a level)
  function heroStats(member, attr, { gear = true } = {}) {
    const m = member || {}, L = clamp(num(m.level), 0, 99), a = clamp(num((m.attr_levels || {})[attr]), 0, 99);
    const g = gear ? gearOf(m) : { atk: 0, hp: 0, mp: 0, def: 0 };
    return { hp: Math.round(36 + 3 * L + 5 * a) + g.hp, mp: Math.round(14 + 0.5 * L + 1.5 * a) + g.mp,
      atk: Math.round(8 + 0.6 * L + 1.2 * a) + g.atk, def: g.def };
  }
  const byLevel = (p, q) => num(q.level) - num(p.level) || AGENT_ORDER.indexOf(p.agent) - AGENT_ORDER.indexOf(q.agent);
  function pickParty(encounter) {
    const enc = encounter || {}, all = (Array.isArray(enc.party) ? enc.party : []).filter((m) => m && m.agent);
    if (enc.boss) {
      const strong = all.filter((m) => num(m.level) > 0).sort(byLevel);
      const out = (strong.length ? strong : all.slice().sort(byLevel)).slice(0, 3);
      return out.length ? out : [{ agent: "claude", level: 1, tier: "novice" }];
    }
    const lesson = enc.lesson || {}, who = lesson.agent || (all[0] && all[0].agent) || "claude";
    const you = all.find((m) => m.hero); // the person's hero (party[i].hero): in front, the lesson's agent beside them
    if (you) {
      const rest = all.filter((m) => !m.hero), finder = rest.find((m) => m.agent === who);
      return [you, ...[finder, ...rest.filter((m) => m !== finder && num(m.level) > 0).sort(byLevel)].filter(Boolean).slice(0, 2)];
    }
    const lead = all.find((m) => m.agent === who) || { agent: who, level: 1, tier: "novice" };
    const allies = all.filter((m) => m.agent !== lead.agent && num(m.level) > 0).sort(byLevel).slice(0, 2);
    return [lead, ...allies];
  }
  const nameOf = (m) => String(m.short || m.name || (m.agent ? m.agent.charAt(0).toUpperCase() + m.agent.slice(1) : "?"));

  function stats(encounter, labels = LABELS) {
    const enc = encounter || {}, boss = !!enc.boss, lesson = enc.lesson || {};
    const modules = boss ? [] : (Array.isArray(lesson.modules) ? lesson.modules : []).filter((m) => m && m.id).slice(0, 3)
      .map((m) => ({ id: String(m.id), name: String(m.name || m.id), attr: m.attr || null }));
    const attr = boss ? (ATTRS.includes(enc.boss.attr) ? enc.boss.attr : "craft")
      : (modules[0] && ATTRS.includes(modules[0].attr) ? modules[0].attr : "craft");
    const lab = (k) => (labels && labels[k] != null ? labels[k] : LABELS[k]);
    const members = pickParty(enc);
    const heroes = members.map((m, i) => {
      const s = heroStats(m, attr), g = gearOf(m), skills = {}, gearSkills = {};
      for (const mod of modules) {
        skills[mod.id] = Math.max(0, Math.floor(num((m.skills || {})[mod.id])));
        gearSkills[mod.id] = g.skills[mod.id] || 0;
      }
      return { i, agent: String(m.agent), name: nameOf(m), level: Math.max(0, Math.floor(num(m.level))),
        tier: m.tier || "novice", lead: !boss && i === 0, attrLevel: Math.max(0, Math.floor(num((m.attr_levels || {})[attr]))),
        maxHp: s.hp, hp: s.hp, maxMp: s.mp, mp: s.mp, atk: s.atk, def: s.def, skills, gearSkills, guard: false, ko: false };
    });
    // the monster is sized to the party without its gear, plus half of what the gear adds: gear makes a fight easier,
    // and an ungeared party meets exactly the monster it always did
    const bare = members.map((m) => heroStats(m, attr, { gear: false }));
    const d = difficulty(enc);
    let atkSum = 0, hpSum = 0;
    heroes.forEach((h, i) => { atkSum += bare[i].atk + GEAR_SHARE * (h.atk - bare[i].atk); hpSum += bare[i].hp + GEAR_SHARE * (h.maxHp - bare[i].hp); });
    const kind = boss ? "boss" : KINDS.includes(lesson.kind) ? lesson.kind : "fix";
    const n = boss ? clamp(Math.floor(num(enc.boss.n, 1)), 1, 4) : 0;
    const maxHp = Math.max(12, Math.round((boss ? BOSS_ROUNDS : FOE_ROUNDS) * d * atkSum));
    const power = hpSum / (boss ? 10 / Math.pow(d, 0.5) : WIPE_ROUNDS / Math.pow(d, 0.7));
    const foe = { kind, n, boss, d: Math.round(d * 100) / 100, maxHp, hp: maxHp, power: Math.round(power * 10) / 10,
      charged: false, stage: lesson.stage || "provisional",
      name: String(boss ? lab("boss." + (((n - 1) % 4) + 1)) : lab("monster." + kind)),
      title: String(boss ? (enc.boss.text || "") : (lesson.title || "")) };
    return { attr, modules, heroes, foe };
  }

  const MOVES = {
    fix: { list: [{ id: "bite", type: "hit", pow: 1, w: 4 }, { id: "swarm", type: "all", pow: 0.85, w: 2 },
      { id: "heisen", type: "hit", pow: 1.35, miss: 0.3, w: 2 }, { id: "charge", type: "charge", w: 1 }],
      big: { id: "crash", type: "big", pow: 2 } },
    gotcha: { list: [{ id: "trap", type: "hit", pow: 1.15, w: 4 }, { id: "edge", type: "all", pow: 0.85, w: 2 },
      { id: "silent", type: "drain", pow: 0.6, w: 2 }, { id: "charge", type: "charge", w: 1 }],
      big: { id: "default", type: "big", pow: 2 } },
    decision: { list: [{ id: "fork", type: "all", pow: 0.9, w: 3 }, { id: "tradeoff", type: "hit", pow: 1.1, w: 3 },
      { id: "bikeshed", type: "heal", pow: 0.1, w: 1 }, { id: "charge", type: "charge", w: 1 }],
      big: { id: "flip", type: "big", pow: 2 } },
    learning: { list: [{ id: "glare", type: "hit", pow: 1.1, w: 4 }, { id: "rabbit", type: "all", pow: 0.85, w: 2 },
      { id: "fog", type: "drain", pow: 0.6, w: 2 }, { id: "charge", type: "charge", w: 1 }],
      big: { id: "epiphany", type: "big", pow: 2 } },
    pattern: { list: [{ id: "copy", type: "twice", pow: 0.6, w: 3 }, { id: "grind", type: "hit", pow: 1.1, w: 3 },
      { id: "boiler", type: "all", pow: 0.85, w: 2 }, { id: "charge", type: "charge", w: 1 }],
      big: { id: "repeat", type: "big", pow: 2 } },
    command: { list: [{ id: "flag", type: "hit", pow: 1.15, w: 4 }, { id: "pipe", type: "all", pow: 0.85, w: 2 },
      { id: "sudo", type: "hit", pow: 1.4, miss: 0.3, w: 2 }, { id: "charge", type: "charge", w: 1 }],
      big: { id: "enter", type: "big", pow: 2 } },
    boss: { list: [{ id: "deadline", type: "all", pow: 0.9, w: 3 }, { id: "scope", type: "hit", pow: 1.2, w: 3 },
      { id: "meeting", type: "heal", pow: 0.06, w: 1 }, { id: "charge", type: "charge", w: 2 }],
      big: { id: "unleash", type: "big", pow: 2.2 } },
  };

  function setup(encounter, attempt = 0, labels = LABELS) {
    const enc = encounter || {}, s = stats(enc, labels), seed = seedOf(enc, attempt);
    const state = { seed, attempt: Math.max(0, Math.floor(num(attempt))), boss: !!enc.boss, attr: s.attr, modules: s.modules,
      heroes: s.heroes, foe: s.foe, round: 1, phase: "hero", active: 0, log: [], events: [],
      lessonId: enc.lesson ? enc.lesson.id : null, rng: rng(seed), labels, items: bagOf(enc), ward: 0, template: false,
      study: null };
    const ev = state.events, foe = state.foe;
    if (enc.boss && enc.boss.status === "done") {
      state.phase = "done";
      foe.hp = 0;
      return state;
    }
    const scene = !enc.boss && enc.lesson && enc.lesson.case && typeof enc.lesson.case.scene === "string" ? enc.lesson.case.scene.trim() : "";
    const study = studyOf(enc.study);
    if (scene && !study && !enc.studyPending) say(state, ev, null, { text: scene }); // a question brings its own
    if (enc.boss) say(state, ev, "introBoss", { monster: foe.name, text: foe.title });
    else if (study || enc.studyPending) say(state, ev, "introStudy", { monster: foe.name });
    else say(state, ev, "intro", { monster: foe.name, title: foe.title });
    if (study) ev.push(...ask(state, study));
    ev.push({ t: "round", n: 1 });
    return state;
  }
  // a study battle's question: {scene, question}, both text; null otherwise (a boss is never one)
  function studyOf(s) {
    const str = (v) => (typeof v === "string" && v.trim() ? v.trim() : null);
    const options = s && Array.isArray(s.options) ? s.options.map(str) : [];
    return s && typeof s === "object" && str(s.question) && options.length >= 2 && options.every(Boolean)
      ? { scene: str(s.scene), question: str(s.question), options } : null;
  }
  // make it a study battle (once, before it's answered): its scene and question are shown over its options, in the
  // menu, not said in the log
  function ask(state, s) {
    const ev = [], study = studyOf(s);
    if (!study || state.boss || state.study) return ev;
    // the options in an order of this fight's own (a stream apart from its dice): another try, another order
    const order = study.options.map((_, i) => i), r = rng((state.seed ^ 0x9e3779b9) >>> 0);
    for (let i = order.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; }
    state.study = { ...study, order, verdict: null, feedback: "", principle: "", answer: "" };
    ev.push({ t: "ask" });
    return ev;
  }
  // the consumables and loot the person owns (encounter.items), those the battle knows how to use, with counts
  const ITEMS = ["c:potion", "c:ether", "c:phoenix", "c:smoke", "l:patch", "l:sign", "l:adr", "l:insight", "l:template", "l:script"];
  function bagOf(encounter) {
    const out = [], seen = new Set();
    for (const it of Array.isArray(encounter && encounter.items) ? encounter.items : []) {
      const id = it && String(it.id || "");
      if (!ITEMS.includes(id) || seen.has(id)) continue;
      const count = clamp(Math.floor(num(it.count)), 0, 99);
      if (count <= 0) continue;
      seen.add(id);
      out.push({ id, name: String(it.name || id), kind: it.kind === "loot" ? "loot" : "consumable", count });
    }
    return out.sort((p, q) => ITEMS.indexOf(p.id) - ITEMS.indexOf(q.id));
  }
  // a line of narration: a label key and its values (or, with no key, a text of the lesson's own, like its case scene)
  function say(state, events, key, vars) {
    const line = { key, vars: vars || {} };
    state.log.push(line);
    events.push({ t: "say", ...line });
  }

  function skillOptions(state, i) {
    const h = state.heroes[i];
    if (!h) return [];
    const lab = (k) => (state.labels && state.labels[k] != null ? state.labels[k] : LABELS[k]);
    const opts = state.boss
      ? [{ id: "attr:" + state.attr, name: fill(lab("attrSkill"), { attr: lab("attr." + state.attr) }), level: h.attrLevel }]
      : state.modules.map((m) => ({ id: m.id, name: m.name, level: (h.skills[m.id] || 0) + (h.gearSkills[m.id] || 0),
        own: h.skills[m.id] || 0, gear: h.gearSkills[m.id] || 0 }));
    return opts.map((o) => ({ own: o.level, gear: 0, ...o, cost: SKILL_COST, usable: h.mp >= SKILL_COST }));
  }
  // what the Item submenu offers the active hero: each item with its count, who it would be used on, and whether it can be
  // An item is only offered when it would do something: a Potion goes to the standing hero lowest on HP (by share of
  // their max), an Ether to the one lowest on MP, a Phoenix Down to the first one knocked out; Patch and Insight need
  // somebody short of HP or MP. `why` is the label that says why one can't be used now.
  function needy(state, what) {
    let best = null, low = 1;
    for (const x of alive(state)) {
      const frac = what === "hp" ? x.hp / x.maxHp : x.mp / Math.max(1, x.maxMp);
      if (frac < low) { low = frac; best = x; }
    }
    return best;
  }
  function itemOptions(state) {
    const fallen = state.heroes.find((x) => x.ko), hurt = needy(state, "hp"), drained = needy(state, "mp");
    return state.items.filter((it) => it.count > 0).map((it) => {
      let target = null, why = null;
      if (it.id === "c:phoenix") { target = fallen || null; if (!fallen) why = "itemNoTarget"; }
      else if (it.id === "c:potion") { target = hurt; if (!hurt) why = "itemFullHp"; }
      else if (it.id === "c:ether") { target = drained; if (!drained) why = "itemFullMp"; }
      else if (it.id === "l:patch" && !hurt) why = "itemFullHp";
      else if (it.id === "l:insight" && !drained) why = "itemFullMp";
      return { ...it, target: target ? target.i : null, usable: !why, why };
    });
  }
  const skillMult = (level) => (level > 0 ? Math.min(3.4, 1 + 0.24 * level) : 0.7);
  const alive = (state) => state.heroes.filter((h) => !h.ko);

  function heroAction(state, action, r) {
    const roll = r || state.rng, ev = [];
    if (!state || state.phase !== "hero") return ev;
    const h = state.heroes[state.active], foe = state.foe, a = action || { type: "attack" };
    if (!h || h.ko) { advance(state, ev); return ev; }
    if (a.type === "run") {
      state.phase = "ran";
      say(state, ev, "lineRun", {});
      ev.push({ t: "end", phase: "ran" });
      return ev;
    }
    if (a.type === "defend") {
      h.guard = true;
      const gain = Math.min(h.maxMp - h.mp, Math.ceil(h.maxMp * 0.3));
      h.mp += gain;
      ev.push({ t: "guard", hero: h.i, mp: h.mp });
      if (gain > 0) say(state, ev, "lineDefend", { hero: h.name, mp: gain });
      else say(state, ev, "lineDefendFull", { hero: h.name });
      advance(state, ev);
      return ev;
    }
    if (a.type === "item") return useItem(state, h, a, ev);
    if (a.type === "answer") return answer(state, h, a, ev);
    let mult = 1, skill = null;
    if (a.type === "skill") {
      skill = skillOptions(state, h.i).find((o) => o.id === a.module) || null;
      if (!skill) return ev;
      if (h.mp < skill.cost) { say(state, ev, "lineNoMp", { hero: h.name, skill: skill.name }); return ev; }
      h.mp -= skill.cost;
      mult = skillMult(skill.level);
    }
    h.guard = false;
    const boost = state.template ? 1.6 : 1;
    state.template = false;
    let n = Math.max(1, Math.round(h.atk * mult * boost * (0.85 + 0.3 * roll())));
    const crit = roll() < CRIT;
    if (crit) n = Math.round(n * 1.6);
    const before = foe.hp;
    foe.hp = Math.max(0, foe.hp - n);
    ev.push({ t: "strike", hero: h.i, n, crit, skill: skill ? skill.id : null, lv: skill ? skill.level : 0, hp: state.boss ? Math.max(1, foe.hp) : foe.hp, mp: h.mp, was: before });
    if (skill) say(state, ev, skill.level > 0 ? "lineSkill" : "lineSkillWeak", { hero: h.name, skill: skill.name, lv: skill.level, n });
    else say(state, ev, crit ? "lineCrit" : "lineAttack", { hero: h.name, n });
    settle(state, ev);
    return ev;
  }
  // after a blow: a boss holds at 1 HP (only the person beats it, on the Quests page); a monster at 0 is beaten
  function settle(state, ev) {
    const foe = state.foe, hold = state.boss && foe.hp <= 0;
    if (hold) foe.hp = 1;
    if (hold) {
      state.phase = "held";
      ev.push({ t: "hold" });
      say(state, ev, "lineHold", { monster: foe.name });
      ev.push({ t: "end", phase: "held" });
    } else if (foe.hp <= 0) {
      state.phase = "won";
      ev.push({ t: "down" });
      say(state, ev, "lineWin", { monster: foe.name });
      ev.push({ t: "end", phase: "won" });
    } else advance(state, ev);
  }
  // the person's judged answer, as the active hero's move: once a fight. "got it" and "partly" take a share of the
  // monster's HP, break a charge and make later blows harder; "not yet" uses the turn and nothing else
  // the person's checked answer ends a study battle: "got it" is the finishing blow (the monster is beaten), anything
  // else lets it slip away ("missed": nothing beaten, the answer shown on the end screen)
  function answer(state, h, a, ev) {
    const st = state.study, verdict = a && VERDICTS.includes(a.verdict) ? a.verdict : null, foe = state.foe;
    if (!st || st.verdict || !verdict) return ev;
    const text = (v) => (typeof v === "string" ? v.trim() : "");
    Object.assign(st, { verdict, feedback: text(a.feedback), principle: text(a.principle), answer: text(a.answer),
      backIn: Math.max(0, Math.floor(num(a.back_in))), owned: a.owned === true });
    ev.push({ t: "answered", verdict });
    if (verdict !== "got_it") {
      say(state, ev, "lineNotYet", {});
      if (st.feedback) say(state, ev, null, { text: st.feedback });
      state.phase = "missed";
      ev.push({ t: "miss" });
      say(state, ev, "lineMiss", { monster: foe.name });
      ev.push({ t: "end", phase: "missed" });
      return ev;
    }
    const n = foe.hp, before = foe.hp;
    foe.hp = 0;
    ev.push({ t: "strike", hero: h.i, n, crit: false, skill: null, lv: 0, study: true, hp: 0, mp: h.mp, was: before });
    say(state, ev, "lineGot", { monster: foe.name, n });
    if (st.feedback) say(state, ev, null, { text: st.feedback });
    settle(state, ev);
    return ev;
  }
  // an item uses the hero's turn; its count goes down by one (the page is told through onUseItem)
  function useItem(state, h, a, ev) {
    const it = state.items.find((x) => x.id === a.id), foe = state.foe;
    if (!it) return ev;
    if (it.count <= 0) { say(state, ev, "lineNoItem", { item: it.name }); return ev; }
    const opt = itemOptions(state).find((x) => x.id === it.id);
    // nothing to do (everyone full, nobody down): said, and nothing is spent
    if (!opt || !opt.usable) { say(state, ev, (opt && opt.why) || "itemNoTarget", {}); return ev; }
    // a chosen target must be one the item can help; otherwise the one who needs it most
    const pick = a.target != null ? state.heroes[a.target] : null;
    const fits = (x) => x && (it.id === "c:phoenix" ? x.ko : !x.ko && (it.id === "c:potion" ? x.hp < x.maxHp : it.id === "c:ether" ? x.mp < x.maxMp : true));
    const target = fits(pick) ? pick : opt.target != null ? state.heroes[opt.target] : h;
    it.count -= 1;
    h.guard = false;
    const hits = [];
    say(state, ev, "lineItem", { hero: h.name, item: it.name });
    const heal = (x, frac) => { const n = Math.min(x.maxHp - x.hp, Math.ceil(x.maxHp * frac)); x.hp += n; hits.push({ hero: x.i, hp: x.hp, mp: x.mp, n, what: "hp" }); return n; };
    const ether = (x, frac) => { const n = Math.min(x.maxMp - x.mp, Math.ceil(x.maxMp * frac)); x.mp += n; hits.push({ hero: x.i, hp: x.hp, mp: x.mp, n, what: "mp" }); return n; };
    const event = { t: "item", id: it.id, hero: h.i, left: it.count, hits };
    if (it.id === "c:potion") { const n = heal(target, 0.4); ev.push(event); say(state, ev, "lineItemHeal", { target: target.name, n }); }
    else if (it.id === "c:ether") { const n = ether(target, 0.5); ev.push(event); say(state, ev, "lineItemMp", { target: target.name, n }); }
    else if (it.id === "c:phoenix") {
      target.ko = false;
      target.hp = Math.max(1, Math.ceil(target.maxHp * 0.3));
      hits.push({ hero: target.i, hp: target.hp, mp: target.mp, n: target.hp, what: "revive" });
      ev.push(event);
      say(state, ev, "lineRevive", { target: target.name, n: target.hp });
    } else if (it.id === "c:smoke") {
      ev.push(event);
      state.phase = "ran";
      say(state, ev, "lineSmoke", {});
      ev.push({ t: "end", phase: "ran" });
      return ev;
    } else if (it.id === "l:patch") {
      const got = alive(state).map((x) => [x, heal(x, 0.25)]);
      ev.push(event);
      for (const [x, n] of got) say(state, ev, "lineItemHeal", { target: x.name, n });
    } else if (it.id === "l:insight") {
      const got = alive(state).map((x) => [x, ether(x, 0.3)]);
      ev.push(event);
      for (const [x, n] of got) say(state, ev, "lineItemMp", { target: x.name, n });
    } else if (it.id === "l:sign") { state.ward = 2; ev.push(event); say(state, ev, "lineWard", { n: 2 }); }
    else if (it.id === "l:adr") { foe.skip = true; ev.push(event); say(state, ev, "lineSkipNext", { monster: foe.name }); }
    else if (it.id === "l:template") { state.template = true; ev.push(event); say(state, ev, "lineTemplate", {}); }
    else if (it.id === "l:script") {
      const n = Math.max(1, Math.round(foe.maxHp * (state.boss ? 0.1 : 0.25)));
      const before = foe.hp;
      foe.hp = Math.max(0, foe.hp - n);
      event.foe = { n, hp: state.boss ? Math.max(1, foe.hp) : foe.hp, was: before };
      ev.push(event);
      say(state, ev, "lineScript", { item: it.name, monster: foe.name, n });
      settle(state, ev);
      return ev;
    }
    advance(state, ev);
    return ev;
  }
  function advance(state, ev) {
    const next = state.heroes.findIndex((h, i) => i > state.active && !h.ko);
    if (next >= 0) { state.active = next; ev.push({ t: "turn", hero: next }); } else state.phase = "monster";
  }

  function pickMove(state, roll) {
    const foe = state.foe, set = MOVES[foe.kind] || MOVES.fix;
    if (foe.charged) { foe.charged = false; return set.big; }
    const list = set.list.filter((m) => (m.type !== "charge" || state.round >= 2) && (m.type !== "heal" || foe.hp < foe.maxHp * 0.7));
    const total = list.reduce((t, m) => t + m.w, 0);
    let x = roll() * total;
    for (const m of list) { x -= m.w; if (x < 0) return m; }
    return list[list.length - 1];
  }
  function monsterTurn(state, r) {
    const roll = r || state.rng, ev = [];
    if (!state || state.phase !== "monster") return ev;
    const foe = state.foe, size = state.heroes.length;
    if (foe.skip) { // an ADR Scroll: it loses this turn (a charge it gathered waits)
      foe.skip = false;
      ev.push({ t: "skip" });
      say(state, ev, "lineSkipped", { monster: foe.name });
      return nextRound(state, ev);
    }
    const mv = pickMove(state, roll), ward = state.ward > 0 ? 0.6 : 1;
    if (state.ward > 0) state.ward -= 1;
    say(state, ev, `move.${foe.kind}.${mv.id}`, { monster: foe.name });
    const hits = [];
    const strike = (h, pow, big) => {
      if (mv.miss && roll() < mv.miss) { hits.push({ hero: h.i, miss: true }); return; }
      let n = foe.power * pow * (0.85 + 0.3 * roll());
      if (h.guard) n *= GUARD;
      n *= (1 - (h.def || 0)) * ward; // armour, then a Warning Sign
      if (!big) n = Math.min(n, h.maxHp * 0.75);
      n = Math.max(1, Math.round(n));
      h.hp = Math.max(0, h.hp - n);
      const hit = { hero: h.i, n, hp: h.hp, guard: h.guard };
      if (mv.type === "drain") { const lost = Math.min(h.mp, Math.round(h.maxMp * 0.25)); h.mp -= lost; hit.drain = lost; hit.mp = h.mp; }
      if (h.hp <= 0) { h.ko = true; h.guard = false; hit.ko = true; }
      hits.push(hit);
    };
    const anyone = () => { const a = alive(state); return a[Math.floor(roll() * a.length)]; };
    if (mv.type === "charge") {
      foe.charged = true;
      ev.push({ t: "charge" });
      say(state, ev, "lineCharged", { monster: foe.name });
    } else if (mv.type === "heal") {
      const n = Math.max(1, Math.round(foe.maxHp * mv.pow * (0.85 + 0.3 * roll())));
      foe.hp = Math.min(foe.maxHp, foe.hp + n);
      ev.push({ t: "heal", n, hp: foe.hp });
      say(state, ev, "lineHeal", { monster: foe.name, n });
    } else {
      // on everyone: the move's power shared across the party it started with (so it does less as heroes fall)
      if (mv.type === "all") for (const h of alive(state)) strike(h, mv.pow / size, false);
      else if (mv.type === "twice") { strike(anyone(), mv.pow, false); if (alive(state).length) strike(anyone(), mv.pow, false); }
      else strike(anyone(), mv.pow, mv.type === "big");
      ev.push({ t: "claw", move: mv.id, all: mv.type === "all", big: mv.type === "big", hits });
      for (const x of hits) {
        const h = state.heroes[x.hero];
        if (x.miss) { say(state, ev, "lineDodge", { hero: h.name }); continue; }
        say(state, ev, x.guard ? "lineGuarded" : "lineHit", { hero: h.name, n: x.n });
        if (x.drain) say(state, ev, "lineDrain", { hero: h.name, mp: x.drain });
        if (x.ko) { ev.push({ t: "ko", hero: x.hero }); say(state, ev, "lineKo", { hero: h.name }); }
      }
    }
    if (!alive(state).length) {
      state.phase = "lost";
      say(state, ev, "lineLose", {});
      ev.push({ t: "end", phase: "lost" });
      return ev;
    }
    return nextRound(state, ev);
  }
  function nextRound(state, ev) {
    state.round += 1;
    state.phase = "hero";
    state.active = state.heroes.findIndex((h) => !h.ko);
    ev.push({ t: "round", n: state.round });
    ev.push({ t: "turn", hero: state.active });
    return ev;
  }

  // the tests' player: the hero's best trained skill while the MP lasts, else Attack
  function policy(state) {
    const opts = skillOptions(state, state.active).filter((o) => o.usable && o.level > 0).sort((p, q) => q.level - p.level);
    return opts.length ? { type: "skill", module: opts[0].id } : { type: "attack" };
  }
  function simulate(encounter, attempt = 0, play = policy, maxRounds = 40) {
    const state = setup(encounter, attempt);
    let guard = 0;
    while (guard++ < 1000 && state.round <= maxRounds) {
      if (state.phase === "hero") heroAction(state, play(state));
      else if (state.phase === "monster") monsterTurn(state);
      else break;
    }
    return state;
  }

  // the end screen's words
  // a lesson's body as a short plain excerpt: markdown's markup goes (fences, heading and list marks, link syntax,
  // emphasis delimiters around words), the text stays, inline code's content and snake_case identifiers included
  function excerpt(body, max = 280) {
    const codes = [];
    let s = String(body || "").replace(/\r/g, "");
    s = s.replace(/^[ \t]*(```|~~~)[^\n]*$/gm, "");                                   // fence lines go, their contents stay
    s = s.replace(/`([^`\n]+)`/g, (m, c) => `\u0000${codes.push(c) - 1}\u0000`);       // inline code: kept as it is
    s = s.replace(/!\[[^\]]*\]\([^)]*\)/g, "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/<(https?:\/\/[^>\s]+)>/g, "$1");
    const cjk = (t) => /[　-ヿ㐀-鿿＀-￯]$/.test(t);
    s = s.split("\n").map((line) => {
      let t = line.replace(/^[ \t]{0,3}>[ \t]?/, "");
      if (/^[ \t]*([-*_][ \t]*){3,}$/.test(t)) return "";                              // a rule
      const head = /^[ \t]{0,3}#{1,6}[ \t]+/.test(t), item = /^[ \t]*([-*+]|\d+[.)])[ \t]+/.test(t);
      t = t.replace(/^[ \t]{0,3}#{1,6}[ \t]+/, "").replace(/[ \t]+#+[ \t]*$/, "").replace(/^[ \t]*([-*+]|\d+[.)])[ \t]+/, "").trim();
      // a heading or a list item runs into the next line: end it so the sentence reads
      if ((head || item) && t && !/[.!?:;。！？：；]$/.test(t)) t += head ? (cjk(t) ? "：" : ":") : (cjk(t) ? "。" : ".");
      return t;
    }).filter(Boolean).join(" ");
    for (let i = 0; i < 2; i++) { // emphasis wraps words; a delimiter inside a word (doc_int_text, a*b) is text
      s = s.replace(/(^|[^\w*])(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\2(?![\w*]|\.\w)/g, "$1$3") // __init__.py stays
        .replace(/(^|[^\w~])~~(?=\S)(.+?)(?<=\S)~~(?![\w~])/g, "$1$2");
    }
    s = s.replace(/\u0000(\d+)\u0000/g, (m, i) => codes[Number(i)]).replace(/\s+/g, " ").trim();
    if (s.length > max) s = s.slice(0, s.lastIndexOf(" ", max) > max * 0.6 ? s.lastIndexOf(" ", max) : max).trimEnd() + "…";
    return s;
  }
  // the win screen's reward lines for what onWin's promise resolved to: {gold, loot} → "+n gold" and each loot; null → the
  // lesson paid out before; false → the win wasn't saved; anything else → no line
  function rewardLines(result, labels = LABELS) {
    const lab = (k) => (labels && labels[k] != null ? labels[k] : LABELS[k]);
    if (result === null) return [{ kind: "none", text: lab("rewardPaid") }];
    if (result === false) return [{ kind: "failed", text: lab("rewardFailed") }];
    if (!result || typeof result !== "object") return [];
    const out = [], gold = Math.max(0, Math.round(num(result.gold)));
    if (gold > 0) out.push({ kind: "gold", text: fill(lab("rewardGold"), { n: gold }) });
    for (const it of Array.isArray(result.loot) ? result.loot : []) if (it) out.push({ kind: "loot", text: fill(lab("rewardLoot"), { item: String(it.name || it.id || "") }) });
    return out;
  }
  function summary(state, labels = LABELS, formatDate = null, encounter = null) {
    const lab = (k) => (labels && labels[k] != null ? labels[k] : LABELS[k]);
    const foe = state.foe, lesson = (encounter && encounter.lesson) || {}, st = state.study;
    const studied = st && ["won", "lost", "ran", "missed"].includes(state.phase);
    const module = (state.modules[0] && state.modules[0].name) || "";
    const note = !studied ? "" : st.verdict === "got_it" ? (st.owned ? fill(lab("studyOwned"), { module, n: st.backIn })
      : fill(lab("studyGot"), { module }) + (st.backIn ? " " + fill(lab("studyBack"), { n: st.backIn }) : ""))
      : st.verdict === "partly" ? lab("studyPartly") : st.verdict === "not_yet" ? lab("studyNot") : lab("studyNone");
    const extra = { note, feedback: studied && st.verdict ? st.feedback : "", principle: studied && st.verdict ? st.principle : "" };
    if (state.phase === "won") {
      const lead = state.heroes[0] || { name: "" };
      const at = String(lesson.at || "").slice(0, 10);
      const date = typeof formatDate === "function" ? formatDate(at) : at;
      const c = lesson.case && typeof lesson.case.answer === "string" && lesson.case.answer.trim() ? lesson.case.answer.trim() : "";
      const said = st && st.verdict && st.answer ? st.answer : c; // the answer the judge held the person's against
      return { title: fill(lab("win"), { monster: foe.name }),
        text: fill(lab("winText"), { agent: lead.name, date, xp: Math.max(0, Math.round(num(lesson.xp))) }), ...extra,
        quoteTitle: said ? lab("winAnswer") : lab("winExcerpt"), quote: said || excerpt(lesson.body) };
    }
    if (state.phase === "missed") return { title: fill(lab("missTitle"), { monster: foe.name }), text: "", ...extra,
      quoteTitle: st && st.answer ? lab("winAnswer") : "", quote: (st && st.answer) || "" };
    if (state.phase === "lost") return { title: lab("lose"), text: lab("loseText"), ...extra, quoteTitle: "", quote: "" };
    if (state.phase === "held") return { title: fill(lab("hold"), { monster: foe.name }), text: lab("holdText"), ...extra, quoteTitle: "", quote: "" };
    if (state.phase === "done") return { title: fill(lab("bossDone"), { monster: foe.name }), text: fill(lab("bossDoneText"), { text: foe.title }), ...extra, quoteTitle: "", quote: "" };
    return { title: "", text: "", ...extra, quoteTitle: "", quote: "" };
  }

  const RULES = Object.freeze({ rng, seedOf, difficulty, gearOf, heroStats, pickParty, stats, setup, ask, skillOptions, itemOptions,
    heroAction, monsterTurn, policy, simulate, summary, rewardLines, excerpt, MOVES, SKILL_COST, ITEMS });

  // =====================================================================================
  // Colour and theme: read from the page's tokens, like scene.js, tinted toward the region's hue
  // =====================================================================================
  const rgbOf = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const hexOf = (rgb) => "#" + rgb.map((v) => clamp(Math.round(v), 0, 255).toString(16).padStart(2, "0")).join("");
  const mix = (a, b, u) => { const x = rgbOf(a), y = rgbOf(b); return hexOf(x.map((v, i) => v + (y[i] - v) * u)); };
  const rgba = (hex, al) => { const [r, g, b] = rgbOf(hex); return `rgba(${r}, ${g}, ${b}, ${al})`; };
  const isHex = (v) => /^#[0-9a-f]{6}$/i.test(v);
  const HUE = { craft: "#3a7be0", data: "#1fae96", ops: "#8a96a8", guard: "#d0605a", arcana: "#9a6ae0", lore: "#e8a040" };
  const FONT = '"IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace';

  function themeDark() {
    const t = document.documentElement.getAttribute("data-theme");
    if (t === "dark" || t === "light") return t === "dark";
    return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function readTheme(attr) {
    const dark = themeDark(), css = getComputedStyle(document.documentElement);
    const tok = (name, fallback) => { const v = css.getPropertyValue(name).trim(); return isHex(v) ? v : fallback; };
    const surface = tok("--surface", dark ? "#0e2c51" : "#ffffff"), surface2 = tok("--surface-2", dark ? "#10345e" : "#e9eff7");
    const page = tok("--page", dark ? "#071729" : "#eef3f9");
    const ink = tok("--ink", dark ? "#f3f8ff" : "#071629"), accent = tok("--accent-ink", dark ? "#8ccfff" : "#164c80");
    const base = tok("--baseline", dark ? "#4a74a6" : "#7f9cc0"), grid = tok("--grid", dark ? "#24507f" : "#d0dceb");
    const cta = tok("--cta", "#ffb45e"), gold = tok("--gold", dark ? "#ffb45e" : "#8a4f06");
    const good = tok("--good-ink", dark ? "#6fe3c1" : "#0c7c60"), bad = tok("--critical-ink", dark ? "#ff8a8a" : "#b42f2f");
    const hue = HUE[attr] || accent, k = dark ? 1 : 0.8;
    const tint = (c, u) => mix(c, hue, Math.min(0.9, u * k * (attr === "ops" || attr === "lore" ? 1.4 : 1)));
    const skyTop = tint(dark ? mix(page, "#000000", 0.2) : surface2, 0.22), skyLow = tint(dark ? surface : mix(surface2, surface, 0.6), 0.1);
    const floorTop = tint(dark ? mix(surface, base, 0.22) : mix(surface2, base, 0.22), 0.16);
    const floorLow = tint(dark ? mix(page, "#000000", 0.1) : mix(surface2, base, 0.1), 0.12);
    const far = mix(skyLow, accent, dark ? 0.17 : 0.17), near = mix(skyLow, accent, dark ? 0.3 : 0.32);
    return { dark, ink, accent, cta, gold: dark ? cta : gold, good, bad, hue, skyTop, skyLow, floorTop, floorLow, far, near,
      farHi: mix(far, accent, 0.35), nearHi: mix(near, accent, 0.45), grid: rgba(dark ? grid : mix(grid, base, 0.3), dark ? 0.55 : 0.6),
      line: mix(floorTop, accent, dark ? 0.26 : 0.22), horizon: mix(base, accent, 0.3), lamp: cta, lamp2: mix(hue, "#ffffff", dark ? 0.35 : 0),
      shadow: dark ? "rgba(0, 0, 0, 0.35)" : "rgba(20, 40, 80, 0.18)", stroke: dark ? "rgba(6, 10, 26, 0.92)" : "rgba(255, 255, 255, 0.95)" };
  }

  // =====================================================================================
  // The battle screen
  // =====================================================================================
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  const CODE = /`([^`\n]+)`/g;
  const plain = (text) => String(text == null ? "" : text).replace(CODE, "$1");
  // text with its `code` spans shown as code (a lesson's title often has them)
  function rich(node, text) {
    node.textContent = "";
    for (const part of String(text == null ? "" : text).split(/(`[^`\n]+`)/)) {
      if (!part) continue;
      if (/^`[^`\n]+`$/.test(part)) node.append(el("code", "bt-code", part.slice(1, -1)));
      else node.append(document.createTextNode(part));
    }
    return node;
  }
  function blank(w, h) {
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(w));
    c.height = Math.max(1, Math.round(h));
    return c;
  }
  const ease = (u) => 1 - (1 - clamp(u, 0, 1)) ** 2;
  const bump = (u) => (u < 0.5 ? ease(u * 2) : ease((1 - u) * 2)); // out and back

  // a pixel blob in the kind's colour: the monster when Scene.art isn't there
  function blobPieces(frame, boss) {
    const body = boss
      ? ["....bbbbbbbb....", "..bbbbbbbbbbbb..", ".bbbbbbbbbbbbbb.", "bbbbbbbbbbbbbbbb", "bbbbbbbbbbbbbbbb", "bbbbbbbbbbbbbbbb",
        "bbbbbbbbbbbbbbbb", "bbbbbbbbbbbbbbbb", "bbbbbbbbbbbbbbbb", ".bbbbbbbbbbbbbb.", "..bbbbbbbbbbbb..", ".dd..dd..dd..dd."]
      : [".....bbbbbb.....", "...bbbbbbbbbb...", "..bbbbbbbbbbbb..", ".bbbbbbbbbbbbbb.", "bbbbbbbbbbbbbbbb", "bbbbbbbbbbbbbbbb",
        "bbbbbbbbbbbbbbbb", "bbbbbbbbbbbbbbbb", ".bbbbbbbbbbbbbb.", "..bbbbbbbbbbbb..", frame ? "..dd..dd..dd...." : "...dd..dd..dd..."];
    const pieces = [[0, boss ? 3 : 2, body], [2, (boss ? 3 : 2) + 3, [".ww...ww.", "wkww.wkww", "wkkw.wkkw", ".ww...ww."]],
      [1, (boss ? 3 : 2) + 7, ["cc.....cc"]], [4, (boss ? 3 : 2) + 8, ["m..m", ".mm."]]];
    if (boss) pieces.push([3, 0, ["y.y.y.y.y", "yyyyyyyyy", "yyyyyyyyy"]]);
    return pieces;
  }
  function rawPixels(pieces, pal) {
    let w = 0, h = 0;
    for (const [x, y, rows] of pieces) { w = Math.max(w, x + Math.max(...rows.map((r) => r.length))); h = Math.max(h, y + rows.length); }
    const c = blank(w, h), g = c.getContext("2d");
    for (const [x, y, rows] of pieces) rows.forEach((row, dy) => {
      for (let dx = 0; dx < row.length; dx++) {
        const v = pal[row[dx]];
        if (!v) continue;
        g.fillStyle = v.replace(/^[*=]/, "");
        g.fillRect(x + dx, y + dy, 1, 1);
      }
    });
    return c;
  }

  function mount(host, opts = {}) {
    const labels = { ...LABELS, ...(opts.labels || {}) };
    const lab = (k, vars) => fill(labels[k] != null ? labels[k] : LABELS[k] != null ? LABELS[k] : k, vars || {});
    const still = !!opts.reducedMotion;
    const enc = opts.encounter || {};
    // a study battle: its question now, or on its way (the page asks the model for one when the lesson has no case file)
    const studyLater = !!opts.study && typeof opts.study.then === "function";
    const st = setup({ ...enc, study: studyLater ? null : opts.study, studyPending: studyLater }, opts.attempt, labels);
    const hideTitle = () => !!st.study && !st.study.verdict || (studyLater && !st.study && !studyDone);
    let studyDone = !studyLater;
    const call = (k, ...args) => {
      if (typeof opts[k] !== "function") return undefined;
      try { return opts[k](...args); } catch (e) { setTimeout(() => { throw e; }); return undefined; }
    };
    const S = window.Sprites, SA = window.Scene && window.Scene.art;

    // ---- the DOM: the stage (canvas, the monster's window, the round), the message window, the party and the menu
    const root = el("section", "bt" + (still ? " bt-still" : "") + (st.boss ? " bt-boss" : ""));
    root.setAttribute("aria-label", plain(lab("region", { monster: st.foe.name })));
    const stage = el("div", "bt-stage");
    if (Number(opts.height) > 0) stage.style.height = Math.round(Number(opts.height)) + "px";
    const canvas = el("canvas", "bt-canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", plain(lab("scene", { monster: st.foe.name, heroes: st.heroes.map((x) => x.name).join(", ") })));
    const foeWin = el("div", "bt-win bt-foe");
    const foeName = el("div", "bt-foe-name", st.foe.name);
    const foeTitle = el("div", "bt-foe-title");
    // the lesson's title would give a study battle's answer away: its roadmap module until it's answered
    function paintEpithet() {
      const hidden = hideTitle(), title = hidden ? ((st.modules[0] && st.modules[0].name) || "?") : st.foe.title;
      rich(foeTitle, st.boss ? lab("bossEpithet", { n: st.foe.n, text: st.foe.title, track: (enc.boss && enc.boss.track_name) || "" })
        : lab("epithet", { title }));
      foeTitle.title = plain(title);
    }
    paintEpithet();
    const foeBar = bar("hp");
    foeWin.append(foeName, foeTitle, foeBar.el);
    const roundTag = el("div", "bt-round");
    stage.append(canvas, foeWin, roundTag);

    const msgWin = el("div", "bt-win bt-msg");
    const logEl = el("ol", "bt-log");
    logEl.setAttribute("role", "log");
    logEl.setAttribute("aria-live", "polite");
    logEl.setAttribute("aria-label", lab("log"));
    logEl.tabIndex = 0;
    msgWin.append(logEl);

    const deck = el("div", "bt-deck");
    const partyWin = el("div", "bt-win bt-party");
    const rows = st.heroes.map((hr) => {
      const row = el("div", "bt-hero");
      const top = el("div", "bt-hero-top");
      const nm = el("span", "bt-hero-name", hr.name);
      nm.style.setProperty("--bt-ac", S && S.color ? S.color(hr.agent) : "#8a94a3");
      top.append(nm);
      if (hr.lead) top.append(el("span", "bt-tag", lab("taught")));
      top.append(el("span", "bt-hero-lv", lab("level", { n: hr.level })));
      const hp = statLine(lab("hp"), "hp"), mp = statLine(lab("mp"), "mp");
      row.append(top, hp.el, mp.el);
      partyWin.append(row);
      return { row, hp, mp };
    });
    const menuWin = el("div", "bt-win bt-menu");
    const menuHead = el("div", "bt-menu-head");
    const cmds = el("div", "bt-cmds");
    cmds.setAttribute("role", "group");
    cmds.setAttribute("aria-label", lab("commands"));
    menuWin.append(menuHead, cmds);
    deck.append(partyWin, menuWin);
    const resultEl = el("div", "bt-result");
    resultEl.hidden = true;
    root.append(stage, msgWin, deck, resultEl);
    host.appendChild(root);

    function bar(kind) {
      const e = el("div", "bt-bar bt-" + kind), ghost = el("b"), fillEl = el("i");
      e.append(ghost, fillEl);
      e.setAttribute("aria-hidden", "true");
      return { el: e, set(frac) {
        const p = Math.round(clamp(frac, 0, 1) * 1000) / 10 + "%";
        fillEl.style.width = p;
        ghost.style.width = p;
        e.classList.toggle("bt-mid", kind === "hp" && frac <= 0.5 && frac > 0.25);
        e.classList.toggle("bt-low", kind === "hp" && frac <= 0.25);
      } };
    }
    function statLine(name, kind) {
      const e = el("div", "bt-stat"), b = bar(kind), v = el("span", "bt-stat-v");
      e.append(el("span", "bt-stat-k", name), b.el, v);
      return { el: e, set(cur, max) { b.set(max ? cur / max : 0); v.textContent = lab("hpValue", { hp: cur, max }); } };
    }

    // ---- what is shown: the rules run ahead, the screen catches up event by event
    const shown = { foe: { hp: st.foe.hp, charged: false, down: st.phase === "done", held: false },
      heroes: st.heroes.map((x) => ({ hp: x.hp, mp: x.mp, ko: false, guard: false })), active: -1, round: 1, cheer: st.phase === "done" };
    function paintStatus() {
      foeBar.set(shown.foe.hp / st.foe.maxHp);
      foeWin.classList.toggle("bt-gone", !!shown.foe.down);
      st.heroes.forEach((x, i) => {
        const s = shown.heroes[i];
        rows[i].hp.set(s.hp, x.maxHp);
        rows[i].mp.set(s.mp, x.maxMp);
        rows[i].row.classList.toggle("bt-on", shown.active === i);
        rows[i].row.classList.toggle("bt-ko", s.ko);
        rows[i].row.classList.toggle("bt-guard", s.guard && !s.ko);
      });
      roundTag.textContent = lab("round", { n: shown.round });
      roundTag.hidden = st.phase === "done";
    }
    // a line of the log; the log scrolls to show the newest line from its start (a long one, like the intro with a long
    // title, isn't cut at the top), and the older lines stay above it
    function addLine(text, cls) {
      const li = rich(el("li", cls || ""), text);
      logEl.append(li);
      while (logEl.children.length > 60) logEl.firstChild.remove();
      logEl.scrollTop = Math.min(logEl.scrollHeight - logEl.clientHeight, li.offsetTop);
    }

    // ---- the menu: Answer (a study battle, until it's answered), Attack, Skill, Item, Defend, Run; Answer, Skill and
    // Item open their own panels; arrows move, Esc goes back
    let mode = "main", busy = true, judging = false, judgeError = "", chosen = null;
    // a study battle has nothing but its question: choose an option, then Answer; the page checks it (opts.onAnswer, with
    // the option's index as the page sent it) and the verdict ends the fight; a failed check says so, and the person
    // may answer again
    const studying = () => (!!st.study && !st.study.verdict) || (studyLater && !studyDone && !st.study);
    function choose(i) {
      if (judging || busy || !st.study || st.study.verdict || i == null) return;
      judging = true; judgeError = "";
      renderMenu();
      Promise.resolve(call("onAnswer", i)).then((r) => {
        judging = false;
        if (!alive_) return;
        if (!r || !r.verdict) throw new Error("no verdict");
        command({ type: "answer", verdict: r.verdict, feedback: r.feedback, principle: r.principle, answer: r.answer,
          back_in: r.back_in, owned: r.owned });
      }).catch((e) => {
        judging = false;
        if (!alive_) return;
        judgeError = fill(lab("answerAgain"), { error: (e && e.message) || "?" });
        renderMenu();
      });
    }
    const main = () => [["attack", lab("attack")], ["skill", lab("skill")], ["item", lab("item")], ["defend", lab("defend")],
      ["run", lab("run")]];
    function button(label, onClick, extra) {
      const b = el("button", "bt-cmd");
      b.type = "button";
      if (extra) { b.append(el("span", "bt-cmd-name", label), extra); } else b.textContent = label;
      b.addEventListener("click", () => { if (!busy && b.getAttribute("aria-disabled") !== "true") onClick(); else if (!busy) onClick(true); });
      return b;
    }
    function renderMenu() {
      cmds.textContent = "";
      cmds.classList.toggle("bt-sub", mode !== "main");
      mode = studying() ? "answer" : mode === "answer" ? "main" : mode;
      menuHead.classList.toggle("bt-ask", mode === "answer");
      root.classList.toggle("bt-asking", mode === "answer");
      const hero = st.heroes[st.active];
      // a submenu: its rows in a list that scrolls by whole rows, Back pinned under it
      const sub = () => {
        const wrap = el("div", "bt-subwrap"), list = el("div", "bt-sublist"), cue = el("span", "bt-more-cue", "▼ " + lab("more"));
        cue.setAttribute("aria-hidden", "true");
        wrap.append(list, cue);
        return { wrap, list };
      };
      // Run (or Esc) leaves a study battle at any time, its question still being written included
      const flee = () => {
        const b = button(lab("run"), () => { if (!judging) command({ type: "run" }); }, el("span", "bt-cmd-meta", "Esc"));
        b.classList.add("bt-back", "bt-flee");
        b.dataset.cmd = "run";
        if (judging) b.setAttribute("aria-disabled", "true");
        return b;
      };
      if (mode === "answer" && !st.study) { // its question is still being written
        menuHead.textContent = lab("preparing");
        cmds.append(flee());
      } else if (mode === "answer") {
        // the case: its scene, then its question, over the options, each whole (they wrap)
        menuHead.replaceChildren(...(st.study.scene ? [rich(el("p", "bt-ask-scene"), st.study.scene)] : []),
          rich(el("p", "bt-ask-q"), st.study.question));
        const { wrap, list } = sub();
        st.study.order.forEach((i, k) => {
          const b = button(st.study.options[i], () => { if (!judging) { chosen = i; renderMenu(); focusChoice(); } },
            el("span", "bt-cmd-meta", String(k + 1)));
          rich(b.querySelector(".bt-cmd-name"), st.study.options[i]); // `code` in an option shows as code
          b.classList.add("bt-skill", "bt-choice");
          b.classList.toggle("bt-chosen", chosen === i);
          b.setAttribute("aria-pressed", chosen === i ? "true" : "false");
          b.dataset.choice = String(i);
          if (judging) b.setAttribute("aria-disabled", "true");
          list.append(b);
        });
        const go_ = button(judging ? lab("checking") : lab("answer"), () => choose(chosen));
        go_.classList.add("bt-back", "bt-answer-go");
        go_.dataset.cmd = "answer";
        if (chosen == null || judging) go_.setAttribute("aria-disabled", "true");
        if (judgeError) wrap.append(el("p", "bt-answer-error", judgeError));
        else if (chosen == null) wrap.append(el("p", "bt-answer-hint", lab("choose")));
        const row = el("div", "bt-answer-row");
        row.append(go_, flee());
        cmds.append(wrap, row); // every option in full: the panel has the deck's whole width and height
      } else if (mode === "item" && hero) {
        menuHead.textContent = lab("item");
        const { wrap, list } = sub();
        for (const o of itemOptions(st)) {
          const name = el("span", "bt-cmd-name");
          const hint = o.why ? lab(o.why) : lab(o.id === "l:script" && st.boss ? "hint.l:script.boss" : "hint." + o.id);
          name.append(el("span", "bt-item-name", o.name), el("small", "bt-item-hint" + (o.why ? " bt-why" : ""), hint));
          const meta = el("span", "bt-cmd-meta");
          if (o.target != null) meta.append(el("span", "bt-cmd-target", lab("itemTarget", { hero: st.heroes[o.target].name })));
          meta.append(el("span", "bt-cmd-count", lab("itemCount", { n: o.count })));
          const b = el("button", "bt-cmd bt-skill bt-itemrow");
          b.type = "button";
          b.dataset.item = o.id;
          b.append(name, meta);
          if (!o.usable) b.setAttribute("aria-disabled", "true");
          b.addEventListener("click", () => {
            if (busy) return;
            if (b.getAttribute("aria-disabled") === "true") { addLine(lab(o.why || "itemNoTarget")); return; } // nothing spent
            command({ type: "item", id: o.id });
          });
          list.append(b);
        }
        if (!itemOptions(st).length) list.append(el("p", "bt-empty", lab("itemNone")));
        const back = button(lab("back"), () => { mode = "main"; renderMenu(); focusFirst(true, "item"); });
        back.classList.add("bt-back");
        cmds.append(wrap, back);
        fitSub(wrap, list, back);
      } else if (mode === "skill" && hero) {
        menuHead.textContent = lab("skill");
        const { wrap, list } = sub();
        for (const o of skillOptions(st, st.active)) {
          const meta = el("span", "bt-cmd-meta");
          meta.append(el("span", "bt-cmd-lv" + (o.own > 0 ? "" : " bt-none"), o.own > 0 ? lab("skillLevel", { n: o.own }) : lab("skillNone")));
          if (o.gear > 0) meta.append(el("span", "bt-cmd-gear", lab("skillGear", { n: o.gear })));
          meta.append(el("span", "bt-cmd-cost", lab("skillCost", { n: o.cost })));
          const b = button(o.name, (blocked) => {
            if (blocked) { addLine(lab("lineNoMp", { hero: hero.name, skill: o.name })); return; }
            command({ type: "skill", module: o.id });
          }, meta);
          b.classList.add("bt-skill");
          if (!o.usable) b.setAttribute("aria-disabled", "true");
          list.append(b);
        }
        const back = button(lab("back"), () => { mode = "main"; renderMenu(); focusFirst(true, "skill"); });
        back.classList.add("bt-back");
        cmds.append(wrap, back);
        fitSub(wrap, list, back);
      } else {
        mode = "main";
        menuHead.textContent = hero ? lab("prompt", { hero: hero.name }) : "";
        for (const [id, text] of main()) {
          const b = button(text, () => {
            if (id === "answer" && !(st.study && !st.study.verdict)) return; // its question is still on its way
            if (id === "skill" || id === "item" || id === "answer") { mode = id; renderMenu(); focusFirst(true); return; }
            command({ type: id });
          });
          b.dataset.cmd = id;
          if (id === "item" && !itemOptions(st).length) b.classList.add("bt-dim");
          if (id === "answer") { b.classList.add("bt-answer-cmd"); if (!st.study) b.setAttribute("aria-disabled", "true"); }
          cmds.append(b);
        }
        const hgt = cmds.getBoundingClientRect().height;
        if (hgt > 0) mainH = hgt;
      }
      roving();
      setBusy(busy);
    }
    // a submenu is as tall as the main menu (taller on a phone, where the page scrolls anyway): as many whole rows as fit
    // above the pinned Back, and a "▼ more" fade while rows are hidden below (or above)
    let mainH = 0;
    function fitSub(wrap, list, back) {
      const rowsEl = [...list.children], gap = 2;
      if (!rowsEl.length || !rowsEl[0].offsetHeight) return;
      const cueEl = wrap.querySelector(".bt-more-cue");
      const budget = Math.max(mainH || 188, root.clientWidth <= 560 ? 280 : 0) - back.offsetHeight - (cueEl ? cueEl.offsetHeight : 0) - gap;
      let used = 0, k = 0;
      for (const r of rowsEl) { const hgt = r.offsetHeight + (k ? gap : 0); if (k && used + hgt > budget) break; used += hgt; k++; }
      if (k < rowsEl.length) list.style.maxHeight = Math.max(rowsEl[0].offsetHeight, used) + "px";
      const cue = () => {
        wrap.classList.toggle("bt-more", list.scrollTop + list.clientHeight < list.scrollHeight - 2);
        wrap.classList.toggle("bt-less", list.scrollTop > 2);
      };
      list.addEventListener("scroll", cue, { passive: true });
      cue();
    }
    function items() { return [...cmds.querySelectorAll(".bt-cmd")]; }
    // focus moves where it can be seen: the nearest scroll that shows the whole element
    function reveal(node) {
      if (node && typeof node.scrollIntoView === "function") node.scrollIntoView({ block: "nearest", inline: "nearest", behavior: still ? "auto" : "smooth" });
    }
    function roving(target) {
      const list = items();
      const at = target ? list.indexOf(target) : 0;
      list.forEach((b, i) => { b.tabIndex = i === Math.max(0, at) ? 0 : -1; });
    }
    // after choosing: the focus stays on the option chosen (the menu was drawn again)
    function focusChoice() {
      const b = cmds.querySelector(`.bt-choice[data-choice="${chosen}"]`);
      if (b) { roving(b); b.focus({ preventScroll: true }); }
    }
    function focusFirst(force, cmd) {
      const list = items();
      const b = (cmd && list.find((x) => x.dataset.cmd === cmd)) || list[0];
      if (!b) return;
      roving(b);
      const inside = root.contains(document.activeElement) || document.activeElement === document.body || !document.activeElement;
      if (force || inside) { reveal(menuWin); b.focus({ preventScroll: true }); }
    }
    // the keys work wherever the focus is while the battle shows (pageKey, below), not only inside the menu
    const typing = (t) => !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || ""));
    function pageKey(e) {
      if (!alive_ || !root.isConnected || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return;
      if (e.key === "Escape" && !menuWin.hidden && mode === "answer" && !judging && st.phase === "hero") { // run away
        e.preventDefault();
        command({ type: "run" });
        return;
      }
      if (!menuWin.hidden && mode === "answer" && st.study && !st.study.verdict) { // a question: choose, move, answer
        const order = st.study.order, at = chosen == null ? -1 : order.indexOf(chosen);
        let pick = null;
        if (/^[1-9]$/.test(e.key)) pick = order[Number(e.key) - 1];
        else if (e.key === "ArrowDown" || e.key === "ArrowRight") pick = order[(at + 1) % order.length];
        else if (e.key === "ArrowUp" || e.key === "ArrowLeft") pick = order[at < 0 ? order.length - 1 : (at - 1 + order.length) % order.length];
        else if (e.key === "Home") pick = order[0];
        else if (e.key === "End") pick = order[order.length - 1];
        else if (e.key === "Enter" || e.key === " ") { // Answer, once one is chosen (and not a click on the focused row too)
          e.preventDefault();
          if (chosen != null) choose(chosen);
          return;
        }
        if (pick == null) return;
        e.preventDefault();
        if (!judging) { chosen = pick; renderMenu(); focusChoice(); }
        return;
      }
      // the commands: the arrows bring the focus into the menu when it's elsewhere (the menu's own keys take it from there)
      if (!menuWin.hidden && !root.contains(document.activeElement) && /^Arrow/.test(e.key)) { e.preventDefault(); focusFirst(true); }
    }
    document.addEventListener("keydown", pageKey);
    cmds.addEventListener("keydown", (e) => {
      if (mode === "answer") return; // a question's keys are the page's (pageKey)
      const list = items(), i = list.indexOf(document.activeElement);
      let to = -1;
      if (e.key === "ArrowDown" || e.key === "ArrowRight") to = (i + 1) % list.length;
      else if (e.key === "ArrowUp" || e.key === "ArrowLeft") to = (i - 1 + list.length) % list.length;
      else if (e.key === "Home") to = 0;
      else if (e.key === "End") to = list.length - 1;
      else if (e.key === "Escape" && mode !== "main" && !judging) { e.preventDefault(); const was = mode; mode = "main"; renderMenu(); focusFirst(true, was); return; }
      if (to < 0 || !list.length) return;
      e.preventDefault();
      roving(list[to]);
      list[to].focus({ preventScroll: true });
      reveal(list[to]);
    });
    function setBusy(on) {
      busy = on;
      menuWin.classList.toggle("bt-wait", on);
      for (const b of items()) {
        if (on) b.setAttribute("aria-busy", "true"); else b.removeAttribute("aria-busy");
      }
    }

    // ---- turns
    function command(action) {
      if (busy || st.phase !== "hero") return;
      const evs = heroAction(st, action);
      if (!evs.length) return;
      const used = evs.find((e) => e.t === "item");
      if (used) call("onUseItem", used.id); // the page spends it (POST /api/party/use); the battle counts it down itself
      mode = "main";
      if (action.type === "answer") { // the answer ends the fight: the menu goes, the party is back in view for it
        menuWin.hidden = true;
        root.classList.remove("bt-asking");
      }
      if (st.phase === "hero" && evs.every((e) => e.t === "say")) { // nothing happened (no MP): stay on this hero
        play(evs, () => {});
        return;
      }
      setBusy(true);
      shown.active = -1;
      play(evs, afterHero);
    }
    function afterHero() {
      if (st.phase === "monster") play([{ t: "pause", ms: 380 }, ...monsterTurn(st)], afterMonster);
      else afterMonster();
    }
    function afterMonster() {
      if (st.phase === "hero") ready();
      else finish();
    }
    function ready() {
      shown.active = st.active;
      mode = "main";
      renderMenu();
      setBusy(false);
      paintStatus();
      render();
      focusFirst(false);
    }
    function finish() {
      setBusy(true);
      menuWin.hidden = true;
      if (st.phase === "ran") { call("onRun"); return; }
      showResult();
    }

    // ---- the end screens
    function showResult() {
      const sum = summary(st, labels, opts.formatDate, enc);
      resultEl.textContent = "";
      const card = el("div", "bt-win bt-card bt-" + st.phase);
      const head = rich(el("h2", "bt-card-title"), sum.title);
      head.tabIndex = -1;
      card.append(head);
      if (sum.text) card.append(rich(el("p", "bt-card-text"), sum.text));
      if (sum.note) card.append(el("p", "bt-card-note" + (st.study && st.study.verdict === "got_it" ? " bt-solved" : ""), sum.note));
      if (sum.feedback) card.append(rich(el("p", "bt-card-feedback"), sum.feedback));
      if (st.phase === "won" && reward !== undefined) card.append(rewardBox());
      if (sum.quote) {
        const fig = el("figure", "bt-quote");
        fig.append(el("figcaption", "", sum.quoteTitle), el("blockquote", "", sum.quote));
        card.append(fig);
      }
      if (sum.principle) {
        const fig = el("figure", "bt-quote bt-principle");
        fig.append(el("figcaption", "", lab("principleTitle")), el("blockquote", "", sum.principle));
        card.append(fig);
      }
      const acts = el("div", "bt-actions");
      const btn = (text, cls, fn) => { const b = el("button", "bt-btn" + (cls ? " " + cls : ""), text); b.type = "button"; b.addEventListener("click", fn); acts.append(b); return b; };
      const lesson = enc.lesson || {};
      if (st.phase === "won") {
        btn(lab("readLesson"), "bt-primary", () => call("onReadLesson", lesson.id));
        if (lesson.session_id && typeof opts.sessionHref === "function") {
          let href = "";
          try { href = opts.sessionHref(lesson.session_id) || ""; } catch { href = ""; }
          if (href) { const a = el("a", "bt-btn", lab("openSession")); a.href = href; acts.append(a); }
        }
      } else if (st.phase === "missed") btn(lab("readLesson"), "bt-primary", () => call("onReadLesson", lesson.id));
      else if (st.phase === "lost") btn(lab("retry"), "bt-primary", () => call("onLose"));
      else if (st.phase === "held") btn(lab("openQuests"), "bt-primary", () => call("onQuests"));
      btn(lab("backToMap"), "", () => call("onExit"));
      card.append(acts);
      resultEl.append(card);
      resultEl.hidden = false;
      root.classList.add("bt-over");
      const first = acts.querySelector(".bt-btn");
      const inside = root.contains(document.activeElement) || document.activeElement === document.body;
      if (first && (inside || opts.autofocus !== false)) { first.focus({ preventScroll: true }); reveal(card); }
    }

    // what onWin brought: a promise of {gold, loot: [item]}, or null for a lesson that paid out before (undefined: the
    // page says nothing about rewards, so the screen doesn't either)
    let reward;
    function rewardBox() {
      const box = el("div", "bt-reward");
      box.setAttribute("aria-live", "polite");
      const show = (r) => {
        box.textContent = "";
        box.classList.remove("bt-wait");
        const lines = rewardLines(r, labels);
        for (const x of lines) box.append(el("p", "bt-reward-" + x.kind + (x.kind === "failed" ? " bt-reward-none" : ""), x.text));
        box.hidden = !lines.length;
      };
      if (reward && typeof reward.then === "function") {
        box.classList.add("bt-wait");
        box.append(el("p", "", lab("rewardWait")));
        reward.then((r) => { if (alive_) show(r === undefined ? {} : r); }, () => { if (alive_) show(false); });
      } else show(reward);
      return box;
    }

    // ---- events: each one changes what is shown and starts its effects; the next waits for it
    const queue = [];
    let waitTimer = 0, playing = false;
    function play(evs, done) {
      queue.push(...evs, { t: "_done", fn: done });
      if (!playing) next();
    }
    // one loop drains the queue; an event's callback that queues more (the monster's turn) just extends it
    function next() {
      waitTimer = 0;
      playing = true;
      while (alive_ && queue.length) {
        const ev = queue.shift();
        const ms = apply(ev);
        paintStatus();
        render();
        if (!still && ms > 0) { waitTimer = setTimeout(next, ms); return; }
      }
      playing = false;
    }
    const heroName = (i) => (st.heroes[i] ? st.heroes[i].name : "");
    function apply(ev) {
      const t0 = now();
      switch (ev.t) {
        case "_done": ev.fn(); return 0;
        case "pause": return ev.ms;
        case "say": addLine(ev.key ? lab(ev.key, ev.vars) : ev.vars.text, ev.key ? "" : "bt-case"); return ev.key ? 300 : 900;
        case "round": shown.round = ev.n; return 0;
        case "turn": return 0;
        case "guard": {
          const s = shown.heroes[ev.hero];
          s.guard = true; s.mp = ev.mp;
          spawn({ kind: "shield", who: ev.hero, t0, dur: 450 });
          return 520;
        }
        case "strike": {
          const s = shown.heroes[ev.hero];
          s.guard = false; s.mp = ev.mp;
          const skill = !!ev.skill || !!ev.study, hitAt = skill ? 330 : 170, big = ev.crit || !!ev.study;
          spawn({ kind: "lunge", who: ev.hero, t0, dur: skill ? 520 : 360, dx: skill ? -10 : -18 });
          if (skill) spawn({ kind: "pillar", who: ev.hero, t0, dur: 420 });
          spawn({ kind: skill ? "burst" : "slash", t0: t0 + hitAt, dur: skill ? 520 : 260, big: big || ev.lv >= 3, seed: ev.n + ev.hero });
          spawn({ kind: "flash", who: "foe", t0: t0 + hitAt, dur: 110 });
          spawn({ kind: "shake", who: "foe", t0: t0 + hitAt, dur: big ? 380 : 260, amp: big ? 4 : 2 });
          if (big || (skill && ev.lv >= 3)) spawn({ kind: "quake", t0: t0 + hitAt, dur: 260, amp: 2 });
          spawn({ kind: "num", who: "foe", t0: t0 + hitAt, dur: 1000, text: String(ev.n), color: big ? "gold" : skill ? "accent" : "ink",
            size: big ? 27 : skill ? 24 : 20, sub: ev.study ? lab("weakTag") : skill && ev.lv > 0 ? lab("skillLevel", { n: ev.lv }) : ev.crit ? "!" : "" });
          later(t0 + hitAt, () => { shown.foe.hp = ev.hp; paintStatus(); });
          if (still) shown.foe.hp = ev.hp;
          return hitAt + 520;
        }
        case "claw": {
          const hitAt = ev.big ? 380 : 220;
          spawn({ kind: "lunge", who: "foe", t0, dur: ev.big ? 600 : 420, dx: ev.big ? 22 : 14 });
          if (ev.all || ev.big) spawn({ kind: "quake", t0: t0 + hitAt, dur: ev.big ? 420 : 300, amp: ev.big ? 4 : 2 });
          if (ev.all) spawn({ kind: "wave", t0, dur: hitAt + 200 });
          ev.hits.forEach((x, k) => {
            const at = t0 + hitAt + (ev.all ? 0 : k * 260);
            if (x.miss) { spawn({ kind: "num", who: x.hero, t0: at, dur: 900, text: "MISS", color: "muted", size: 15 }); return; }
            spawn({ kind: "flash", who: x.hero, t0: at, dur: 110 });
            spawn({ kind: "shake", who: x.hero, t0: at, dur: 300, amp: ev.big ? 4 : 2 });
            spawn({ kind: "claw", who: x.hero, t0: at, dur: 240 });
            if (x.guard) spawn({ kind: "shield", who: x.hero, t0: at, dur: 380 });
            spawn({ kind: "num", who: x.hero, t0: at, dur: 1000, text: String(x.n), color: "bad", size: ev.big ? 26 : 20 });
            if (x.drain) spawn({ kind: "num", who: x.hero, t0: at + 260, dur: 900, text: "-" + x.drain + " " + lab("mp"), color: "accent", size: 13, lift: 14 });
            const apply2 = () => { const s = shown.heroes[x.hero]; s.hp = x.hp; if (x.mp != null) s.mp = x.mp; if (x.ko) s.guard = false; paintStatus(); };
            if (still) apply2(); else later(at, apply2);
          });
          return hitAt + (ev.all ? 0 : (ev.hits.length - 1) * 260) + 560;
        }
        case "item": {
          const hb = shown.heroes[ev.hero];
          hb.guard = false;
          spawn({ kind: "pillar", who: ev.hero, t0, dur: 380 });
          ev.hits.forEach((x, k) => {
            const s2 = shown.heroes[x.hero], at = t0 + 260 + k * 90;
            const fix = () => { s2.hp = x.hp; s2.mp = x.mp; if (x.what === "revive") s2.ko = false; paintStatus(); };
            spawn({ kind: "crosses", who: x.hero, t0: at, dur: 700, color: x.what === "mp" ? "accent" : "good" });
            if (x.what === "revive") spawn({ kind: "burst", who: x.hero, t0: at, dur: 520, seed: 7 });
            spawn({ kind: "num", who: x.hero, t0: at, dur: 1000, text: "+" + x.n + (x.what === "mp" ? " " + lab("mp") : ""), color: x.what === "mp" ? "accent" : "good", size: 18 });
            if (still) fix(); else later(at, fix);
          });
          if (ev.id === "l:sign") st.heroes.forEach((h, i) => { if (!shown.heroes[i].ko) spawn({ kind: "shield", who: i, t0: t0 + 200, dur: 700 }); });
          if (ev.id === "l:adr") spawn({ kind: "ring", t0: t0 + 200, dur: 700 });
          if (ev.id === "c:smoke") spawn({ kind: "smoke", t0: t0 + 150, dur: 900 });
          if (ev.foe) {
            const at = t0 + 300;
            spawn({ kind: "burst", t0: at, dur: 520, big: true, seed: ev.foe.n });
            spawn({ kind: "flash", who: "foe", t0: at, dur: 110 });
            spawn({ kind: "shake", who: "foe", t0: at, dur: 300, amp: 3 });
            spawn({ kind: "num", who: "foe", t0: at, dur: 1000, text: String(ev.foe.n), color: "gold", size: 24 });
            if (still) shown.foe.hp = ev.foe.hp; else later(at, () => { shown.foe.hp = ev.foe.hp; paintStatus(); });
          }
          return ev.id === "c:smoke" ? 900 : 820;
        }
        case "skip": spawn({ kind: "ring", t0, dur: 600 }); spawn({ kind: "shake", who: "foe", t0, dur: 260, amp: 1 }); return 650;
        case "charge": shown.foe.charged = true; spawn({ kind: "ring", t0, dur: 700 }); return 720;
        case "uncharge": shown.foe.charged = false; return 0;
        case "answered": paintEpithet(); return 0; // the title shows once it can't give the answer away
        case "miss": shown.foe.down = true; shown.foe.charged = false; spawn({ kind: "dissolve", t0, dur: 1000 }); return 1050; // it slips away
        case "ask": if (!busy) renderMenu(); return 0;
        case "heal": {
          spawn({ kind: "crosses", t0, dur: 800 });
          spawn({ kind: "num", who: "foe", t0: t0 + 120, dur: 1000, text: "+" + ev.n, color: "good", size: 20 });
          shown.foe.hp = ev.hp;
          return 760;
        }
        case "ko": shown.heroes[ev.hero].ko = true; shown.heroes[ev.hero].guard = false; spawn({ kind: "fall", who: ev.hero, t0, dur: 450 }); return 480;
        case "down": shown.foe.down = true; shown.foe.charged = false; spawn({ kind: "dissolve", t0, dur: 1000 }); reward = call("onWin", st.lessonId); return 1050;
        case "hold": shown.foe.held = true; shown.foe.charged = false; spawn({ kind: "quake", t0, dur: 500, amp: 5 }); spawn({ kind: "flash", who: "foe", t0, dur: 300 });
          spawn({ kind: "num", who: "foe", t0: t0 + 200, dur: 1600, text: "1 " + lab("hp"), color: "gold", size: 24, lift: 20 }); return 1000;
        case "end": if (ev.phase === "won" || ev.phase === "held") shown.cheer = ev.phase === "won"; shown.foe.charged = false; return 350;
        default: return 0;
      }
    }

    // ---- the canvas
    const ctx = canvas.getContext("2d");
    let W = 0, L = 0, P = 1, dpr = 1, horizon = 0, theme = null, backdrop = null;
    let alive_ = true, raf = 0, lastDraw = 0;
    const art = new Map(), fx = [], timers = new Set();
    const slots = [];
    let foeSlot = null;
    const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());
    function spawn(f) { if (still) return; fx.push(f); wake(); }
    function later(at, fn) { const id = setTimeout(() => { timers.delete(id); if (alive_) fn(); }, Math.max(0, at - now())); timers.add(id); }

    function measure() {
      dpr = window.devicePixelRatio || 1;
      const cw = Math.max(200, stage.clientWidth || host.clientWidth || 600), ch = Math.max(160, stage.clientHeight || 300);
      const unit = ch >= 300 && cw >= 620 ? 3 : 2;
      P = Math.max(1, Math.round(unit * dpr));
      canvas.width = Math.round(cw * dpr);
      canvas.height = Math.round(ch * dpr);
      W = Math.ceil(canvas.width / P);
      L = Math.ceil(canvas.height / P);
      horizon = Math.round(L * 0.44);
      theme = readTheme(st.attr);
      art.clear();
      layout();
      foeFit();
      backdrop = paintBackdrop();
    }
    function layout() {
      const n = st.heroes.length;
      const narrow = W < 240;
      const spots = n === 1 ? [[0.7, 0.8]] : n === 2 ? [[0.66, 0.7], [0.8, 0.9]]
        : narrow ? [[0.6, 0.76], [0.8, 0.58], [0.83, 0.95]] : [[0.64, 0.75], [0.76, 0.58], [0.79, 0.93]];
      slots.length = 0;
      spots.forEach(([fx_, fy], i) => {
        const x = Math.round(W * fx_), feet = Math.round(L * fy);
        slots.push({ cx: x, feet: Math.min(L - 2, feet) });
      });
      // the monster stands left of the party with a gap; its size is set by foeFit once its art is known
      const heroLeft = Math.min(...slots.map((sl) => sl.cx)) - 16 - 8;
      foeSlot = { cx: Math.round(W * (narrow ? 0.25 : 0.28)), feet: Math.round(L * 0.86), right: heroLeft - (narrow ? 8 : 14), R: 1 };
    }

    // ---- the backdrop: the region's blueprint skyline over a floor in perspective, drawn once per size and theme
    function paintBackdrop() {
      const cv = blank(W * P, L * P), g = cv.getContext("2d"), t = theme, r = rng(97 + ATTRS.indexOf(st.attr) * 131);
      const R = (x, y, w, h, c) => { if (w <= 0 || h <= 0) return; g.fillStyle = c; g.fillRect(Math.round(x) * P, Math.round(y) * P, Math.round(w) * P, Math.round(h) * P); };
      const hz = horizon;
      // the sky in stepped bands, a blueprint grid over it
      const bands = 7;
      for (let i = 0; i < bands; i++) R(0, Math.floor((hz * i) / bands), W, Math.ceil(hz / bands) + 1, mix(t.skyTop, t.skyLow, i / (bands - 1)));
      for (let x = 0; x < W; x += 12) R(x, 0, 1, hz, t.grid);
      for (let y = hz - 12; y > 0; y -= 12) R(0, y, W, 1, t.grid);
      // things in the sky
      const disc = (cx, cy, rad, c) => { for (let y = -rad; y <= rad; y++) { const w = Math.round(Math.sqrt(rad * rad - y * y)); R(cx - w, cy + y, w * 2 + 1, 1, c); } };
      if (st.attr === "arcana" || st.attr === "lore") for (let i = 0; i < W / 9; i++) {
        const x = Math.floor(r() * W), y = Math.floor(r() * hz * 0.7);
        R(x, y, 1, 1, i % 3 ? t.farHi : t.lamp);
        if (i % 7 === 0) { R(x - 1, y, 3, 1, t.farHi); R(x, y - 1, 1, 3, t.farHi); }
      }
      if (st.attr === "ops" || st.attr === "data") for (let i = 0; i < 3; i++) {
        const cx = Math.round(W * (0.15 + i * 0.33 + r() * 0.1)), cy = Math.round(hz * (0.18 + r() * 0.2));
        for (const [dx, dy, rr] of [[0, 0, 5], [6, -2, 6], [12, 0, 5], [5, 2, 4]]) disc(cx + dx, cy + dy, rr, mix(t.skyLow, t.far, 0.5));
      }
      if (st.attr === "guard" || st.attr === "craft") disc(Math.round(W * 0.84), Math.round(hz * 0.28), 7, mix(t.skyLow, t.lamp, t.dark ? 0.35 : 0.45));
      // the skyline, far then near
      const block = (x, w, h, c, hi) => { R(x, hz - h, w, h, c); R(x, hz - h, w, 1, hi); };
      const layer = (far) => {
        const c = far ? t.far : t.near, hi = far ? t.farHi : t.nearHi, lift = far ? 0 : 0, scale = far ? 1 : 0.7;
        let x = far ? -6 : -2 - Math.floor(r() * 10);
        while (x < W) {
          const w0 = Math.round((far ? 16 : 12) + r() * (far ? 22 : 16)), h0 = Math.round(((far ? 18 : 10) + r() * (far ? 22 : 14)) * (hz / 50));
          const h = Math.min(hz - 4, Math.round(h0 * scale) + lift);
          switch (st.attr) {
            case "craft": // workshops with sawtooth roofs and chimneys, a gear on the far ones
              block(x, w0, h, c, hi);
              for (let k = 0; k + 8 <= w0; k += 8) for (let j = 0; j < 6; j++) R(x + k, hz - h - j - 1, Math.max(1, 8 - Math.round(j * 1.4)), 1, j === 5 ? hi : c);
              if (r() < 0.6) { R(x + w0 - 5, hz - h - 10, 3, 10, c); R(x + w0 - 6, hz - h - 11, 5, 1, hi); }
              if (far && r() < 0.5) { const gx = x + Math.round(w0 / 2), gy = hz - h - 9; disc(gx, gy, 5, c); for (let a = 0; a < 8; a++) R(gx + Math.round(Math.cos(a * 0.785) * 6) - 1, gy + Math.round(Math.sin(a * 0.785) * 6) - 1, 2, 2, c); disc(gx, gy, 1, t.skyLow); }
              if (!far) for (let k = 2; k < w0 - 2; k += 4) if (r() < 0.4) R(x + k, hz - Math.round(h * 0.5), 2, 2, t.lamp);
              break;
            case "data": { // database cylinders and bar-chart towers
              const cw2 = Math.max(8, Math.round(w0 * 0.7));
              R(x + 1, hz - h - 1, cw2 - 2, 1, hi); block(x, cw2, h, c, hi);
              for (let y = hz - h + 4; y < hz - 2; y += 5) R(x, y, cw2, 1, hi);
              if (!far) for (let k = 0; k < 3; k++) R(x + cw2 + 2 + k * 4, hz - Math.round(h * (0.3 + 0.25 * k)), 3, Math.round(h * (0.3 + 0.25 * k)), c);
              break;
            }
            case "ops": // server racks with lights
              block(x, Math.max(9, w0 - 6), h, c, hi);
              for (let y = hz - h + 3; y < hz - 2; y += 4) { R(x + 2, y, Math.max(5, w0 - 10), 1, hi); if (r() < 0.55) R(x + Math.max(6, w0 - 9), y + 1, 1, 1, r() < 0.5 ? t.lamp : t.lamp2); }
              break;
            case "guard": { // a castle wall with crenellations and towers
              const tower = r() < 0.4;
              const hh = tower ? h + 8 : Math.round(h * 0.7);
              block(x, w0, hh, c, hi);
              for (let k = 0; k < w0; k += 4) R(x + k, hz - hh - 3, 2, 3, c);
              if (tower) { for (let j = 0; j < 7; j++) R(x + Math.round(w0 / 2) - j, hz - hh - 3 - 7 + j, j * 2 + 1, 1, j === 0 ? hi : c); }
              if (!far) R(x + Math.round(w0 / 2) - 2, hz - Math.round(hh * 0.5), 3, 4, t.lamp);
              break;
            }
            case "arcana": { // crystal spires and floating orbs
              const sw = Math.max(5, Math.round(w0 * 0.5));
              for (let j = 0; j < h; j++) { const ww = Math.max(1, Math.round((sw * j) / h)); R(x + sw - Math.round(ww / 2), hz - h + j, ww, 1, j < 2 ? hi : c); }
              if (r() < 0.6) disc(x + sw + 8, hz - h - 6 + Math.round(r() * 8), 2, far ? hi : t.lamp2);
              break;
            }
            default: { // lore: bookshelves and pillars
              block(x, w0, h, c, hi);
              for (let k = 2; k < w0 - 2; k += 2) { const bh = Math.round(4 + r() * 4); R(x + k, hz - Math.round(h * 0.55) - bh, 1, bh, k % 6 === 0 ? t.lamp : hi); }
              R(x + 1, hz - Math.round(h * 0.55), w0 - 2, 1, hi);
              if (far && r() < 0.5) { R(x + w0 + 2, hz - h - 6, 4, h + 6, c); R(x + w0 + 1, hz - h - 7, 6, 1, hi); }
            }
          }
          x += w0 + (far ? 4 : 10) + Math.round(r() * (far ? 8 : 26));
        }
      };
      layer(true);
      layer(false);
      R(0, hz, W, 1, t.horizon);
      // the floor: stepped bands that darken toward us, lines to a vanishing point, a gold ring under the monster
      const fh = L - hz - 1;
      for (let i = 0; i < 5; i++) R(0, hz + 1 + Math.floor((fh * i) / 5), W, Math.ceil(fh / 5) + 1, mix(t.floorTop, t.floorLow, i / 4));
      for (let k = 1, y = hz + 3; y < L; k++, y += k + 1) R(0, y, W, 1, rgba(t.line, 0.55));
      const vx = W / 2, vy = hz - L;
      g.fillStyle = rgba(t.line, 0.8);
      for (let xf = -W; xf < 2 * W; xf += 16) for (let y = hz + 1; y < L; y++) {
        const x = Math.round(vx + ((xf - vx) * (y - vy)) / (L - vy));
        if (x >= 0 && x < W) g.fillRect(x * P, y * P, P, P);
      }
      return cv;
    }

    // ---- art, cached per device scale: heroes mirrored to face left, the monster mirrored to face right
    function finishArt(src, w, h, flip, variant) {
      const c = blank(w, h), g = c.getContext("2d");
      g.imageSmoothingEnabled = false;
      if (flip) { g.translate(w, 0); g.scale(-1, 1); }
      g.drawImage(src, 0, 0, w, h);
      g.setTransform(1, 0, 0, 1, 0, 0);
      if (variant === "flash") { g.globalCompositeOperation = "source-atop"; g.fillStyle = "#ffffff"; g.fillRect(0, 0, w, h); }
      if (variant === "grey") {
        try {
          const d = g.getImageData(0, 0, w, h);
          for (let i = 0; i < d.data.length; i += 4) {
            const v = d.data[i] * 0.3 + d.data[i + 1] * 0.55 + d.data[i + 2] * 0.15;
            d.data[i] = v * 0.78 + 30; d.data[i + 1] = v * 0.8 + 32; d.data[i + 2] = v * 0.88 + 46;
          }
          g.putImageData(d, 0, 0);
        } catch { /* a tainted canvas can't be greyed: leave it */ }
      }
      return c;
    }
    function heroArt(i, frame, variant) {
      const hr = st.heroes[i], key = `h|${i}|${frame}|${variant}`;
      if (art.has(key)) return art.get(key);
      let src = null;
      if (S && typeof S.draw === "function" && (!S.AGENTS || S.AGENTS.includes(hr.agent) || String(hr.agent).startsWith("hero-"))) {
        src = blank(1, 1);
        try { S.draw(src, { agent: hr.agent, tier: hr.tier, frame, scale: P }); } catch { src = null; }
      }
      if (!src || src.width < 4) { // no sprite for this agent: a blob in its colour
        const col = (S && S.color ? S.color(hr.agent) : "#8a94a3");
        const px = rawPixels(blobPieces(frame % 2, false), { b: col, d: mix(isHex(col) ? col : "#8a94a3", "#000000", 0.3), w: "#ffffff", k: "#1b1d33", c: "#ff9db4", m: "#3b1f2e" });
        src = blank(32 * P, 32 * P);
        const g = src.getContext("2d");
        g.imageSmoothingEnabled = false;
        g.drawImage(px, 8 * P, 14 * P, px.width * P, px.height * P);
      }
      const c = finishArt(src, src.width, src.height, true, variant);
      art.set(key, c);
      return c;
    }
    const foeType = () => (SA && SA.kinds && SA.kinds[st.foe.kind]) || KIND_TYPE[st.foe.kind] || "glitch";
    function foeSource(frame, variant) {
      let src = null;
      try { // one canvas pixel an art pixel (scale 1); a boss already beaten on the Quests page drawn felled
        if (SA && st.boss && typeof SA.boss === "function") src = SA.boss(st.foe.n, frame, { scale: 1, felled: st.phase === "done" && variant === "grey" });
        else if (SA && typeof SA.monster === "function") src = SA.monster(foeType(), st.foe.stage, frame, { scale: 1 });
      } catch { src = null; }
      src = src && src.cv ? src.cv : src;
      if (src && src.width > 0 && src.height > 0 && typeof src.getContext === "function") return src;
      const col = KIND_COLOR[st.foe.kind] || KIND_COLOR.fix;
      const pal = { b: "*" + col, d: mix(col, "#1c2456", 0.4), w: "=#ffffff", k: "=#1b1d33", c: "=#ff9db4", m: "=#3b1f2e", y: "*#f6c445" };
      const pieces = blobPieces(frame % 2, st.boss);
      if (S && typeof S.art === "function") { try { return S.art(pieces, pal, 1).cv; } catch { /* fall through */ } }
      return rawPixels(pieces, pal);
    }
    // a monster's pixel is a whole multiple of a hero's: 2 (as in the Journey strip), 3 for a canonical lesson, 4 for a
    // boss; one step smaller at a time while it would crowd the party or outgrow the stage
    const FOE_RATIO = () => (st.boss ? 4 : st.foe.stage === "canonical" ? 3 : 2);
    function foeFit() {
      const src = foeSource(0), room = foeSlot.right - 4, maxH = L * (st.boss ? 0.74 : 0.62);
      let R = FOE_RATIO();
      while (R > 1 && (src.width * R > room || src.height * R > maxH)) R--;
      foeSlot.R = R;
      foeSlot.w = src.width * R;
      foeSlot.h = src.height * R;
      foeSlot.cx = Math.round(clamp(foeSlot.cx, 3 + foeSlot.w / 2, foeSlot.right - foeSlot.w / 2));
    }
    function foeArt(frame, variant) {
      const key = `f|${frame}|${variant}`;
      if (art.has(key)) return art.get(key);
      const src = foeSource(frame, variant);
      const k = foeSlot.R * P;
      const c = finishArt(src, Math.round(src.width * k), Math.round(src.height * k), true, SA && st.phase === "done" && variant === "grey" ? "base" : variant);
      art.set(key, c);
      return c;
    }

    // ---- drawing
    function active(kind, who, t) { return fx.filter((f) => f.kind === kind && (who === undefined || f.who === who) && t >= f.t0 && t < f.t0 + f.dur); }
    function shakeOf(who, t) {
      let dx = 0;
      for (const f of active("shake", who, t)) dx += (Math.floor((t - f.t0) / 40) % 2 ? 1 : -1) * f.amp * (1 - (t - f.t0) / f.dur);
      return Math.round(dx);
    }
    function lungeOf(who, t) {
      let dx = 0;
      for (const f of active("lunge", who, t)) dx += f.dx * bump((t - f.t0) / f.dur);
      return Math.round(dx);
    }
    const put = (img, x, y) => ctx.drawImage(img, Math.round(x * P), Math.round(y * P));
    function text(s, x, y, { size = 14, color, align = "center", alpha = 1, weight = 700 } = {}) {
      ctx.save();
      ctx.globalAlpha = clamp(alpha, 0, 1);
      ctx.font = `${weight} ${Math.round(size * dpr)}px ${FONT}`;
      ctx.textAlign = align;
      ctx.textBaseline = "middle";
      ctx.lineJoin = "round";
      ctx.lineWidth = Math.max(3, Math.round(4 * dpr));
      ctx.strokeStyle = theme.stroke;
      ctx.strokeText(s, x * P, y * P);
      ctx.fillStyle = color;
      ctx.fillText(s, x * P, y * P);
      ctx.restore();
    }
    const colorOf = (name) => ({ gold: theme.gold, accent: theme.accent, bad: theme.bad, good: theme.good, muted: mix(theme.ink, theme.skyLow, 0.4) }[name] || theme.ink);
    function heroBox(i, t) {
      const s = slots[i], k = 1, stepIn = shown.active === i && !shown.heroes[i].ko ? -8 : 0;
      const hop = shown.cheer && !shown.heroes[i].ko && !still ? -Math.round(Math.abs(Math.sin(t / 160 + i)) * 4) : 0;
      const x = s.cx - 16 * k + stepIn + lungeOf(i, t) + shakeOf(i, t), y = s.feet - 31 * k + hop;
      return { x, y, w: 32 * k, h: 32 * k };
    }
    function foeBox(t, frame) {
      const img = foeArt(frame, "base"), w = img.width / P, h = img.height / P;
      const bob = still || shown.foe.down ? 0 : Math.round(Math.sin(t / 420) * 1.5);
      const fly = SA && typeof SA.fly === "function" ? (SA.fly(foeType()) || 0) * foeSlot.R * 0.6 : st.foe.kind === "learning" ? 6 : 0;
      return { x: foeSlot.cx - w / 2 + lungeOf("foe", t) + shakeOf("foe", t), y: foeSlot.feet - h - fly + bob, w, h };
    }
    function draw(t) {
      if (!theme) return;
      ctx.imageSmoothingEnabled = false;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      let qx = 0, qy = 0;
      for (const f of active("quake", undefined, t)) { const u = (t - f.t0) / f.dur, s = Math.floor((t - f.t0) / 35) % 4; qx += [1, -1, 1, 0][s] * f.amp * (1 - u); qy += [0, 1, -1, 1][s] * f.amp * 0.5 * (1 - u); }
      ctx.save();
      ctx.translate(Math.round(qx) * P, Math.round(qy) * P);
      ctx.drawImage(backdrop, 0, 0);
      const sec = t / 1000;
      // the monster: its shadow, an aura while it gathers strength, itself (flashing when hit), dissolving when beaten
      const ff = still ? 0 : Math.floor(sec * 2.2) % 2, fb = foeBox(t, ff);
      const dis = active("dissolve", undefined, t)[0];
      if (!shown.foe.down || dis || st.phase === "done") {
        ctx.fillStyle = theme.shadow;
        const sw = Math.round(fb.w * 0.7);
        ctx.fillRect(Math.round((foeSlot.cx - sw / 2) * P), (foeSlot.feet - 1) * P, sw * P, 2 * P);
        // the gold ring it stands in, centred under its art
        const ring = (rx, ry, c, step) => { ctx.fillStyle = c; for (let i = 0; i < 160; i += step) { const a = (i / 160) * Math.PI * 2; ctx.fillRect(Math.round(foeSlot.cx + Math.cos(a) * rx) * P, Math.round(foeSlot.feet - 1 + Math.sin(a) * ry) * P, P, P); } };
        ring(fb.w * 0.6, Math.max(3, fb.w * 0.13), rgba(theme.gold, 0.75), 2);
        ring(fb.w * 0.76, Math.max(4, fb.w * 0.17), rgba(theme.gold, 0.35), 4);
        if (shown.foe.charged) aura(fb, sec);
        const flashing = active("flash", "foe", t).length > 0;
        let img = foeArt(ff, flashing ? "flash" : st.phase === "done" ? "grey" : "base");
        if (dis) img = dissolve(img, (t - dis.t0) / dis.dur);
        put(img, fb.x, fb.y);
      }
      if (st.phase === "done") doneFlag(fb);
      // the party, back to front
      const order = st.heroes.map((_, i) => i).sort((a, b) => slots[a].feet - slots[b].feet);
      for (const i of order) drawHero(i, t, sec);
      // effects over everything
      for (const f of fx) {
        if (t < f.t0 || t >= f.t0 + f.dur) continue;
        const u = (t - f.t0) / f.dur;
        if (f.kind === "slash") slash(fb, u, f.big);
        else if (f.kind === "burst") burst(f.who != null ? heroBox(f.who, t) : fb, u, f);
        else if (f.kind === "pillar") pillar(heroBox(f.who, t), u);
        else if (f.kind === "claw") clawMark(heroBox(f.who, t), u);
        else if (f.kind === "ring") ringFx(fb, u);
        else if (f.kind === "crosses") crosses(f.who != null && f.who !== "foe" ? heroBox(f.who, t) : fb, u, f.color);
        else if (f.kind === "smoke") smoke(u);
        else if (f.kind === "wave") wave(u);
      }
      for (const f of fx) {
        if (f.kind !== "num" || t < f.t0 || t >= f.t0 + f.dur) continue;
        const u = (t - f.t0) / f.dur, box = f.who === "foe" ? fb : heroBox(f.who, t);
        const rise = ease(Math.min(1, u * 2.5)) * (10 + (f.lift || 0)), hop = u < 0.15 ? Math.sin((u / 0.15) * Math.PI) * 3 : 0;
        const y = Math.max(8, box.y + 2 - rise - hop), x = box.x + box.w / 2;
        const alpha = u > 0.8 ? 1 - (u - 0.8) / 0.2 : 1;
        text(f.text, x, y, { size: f.size, color: colorOf(f.color), alpha });
        if (f.sub) text(f.sub, x, y + (f.size * 0.8 * dpr) / P, { size: 12, color: colorOf("gold"), alpha });
      }
      ctx.restore();
    }
    function drawHero(i, t, sec) {
      const s = shown.heroes[i], box = heroBox(i, t), slot = slots[i];
      ctx.fillStyle = theme.shadow;
      ctx.fillRect((slot.cx - 10) * P, (slot.feet - 1) * P, 20 * P, P);
      ctx.fillRect((slot.cx - 8) * P, slot.feet * P, 16 * P, P);
      if (s.ko) {
        const f = active("fall", i, t)[0], sink = f ? Math.round(ease((t - f.t0) / f.dur) * 6) : 6;
        ctx.save();
        ctx.beginPath();
        ctx.rect(0, 0, canvas.width, (slot.feet + 1) * P);
        ctx.clip();
        put(heroArt(i, 0, "grey"), box.x, box.y + sink);
        ctx.restore();
        return;
      }
      const frame = still ? 0 : Math.floor(sec * 4 + i * 1.7) % 4;
      const flashing = active("flash", i, t).length > 0;
      put(heroArt(i, frame, flashing ? "flash" : "base"), box.x, box.y);
      // defending: a little shield in front
      if (s.guard || active("shield", i, t).length) shield(box.x + 2, box.y + 14, active("shield", i, t).length > 0);
      // the active hero: a bobbing gold cursor over its head
      if (shown.active === i && !busy) {
        const by = box.y - 4 + (still ? 0 : Math.round(Math.abs(Math.sin(sec * 5)) * -2));
        const cx = Math.round(box.x + 16);
        for (let j = 0; j < 4; j++) { ctx.fillStyle = j === 0 ? theme.stroke : theme.gold; ctx.fillRect((cx - 3 + j) * P, (by + j) * P, (7 - 2 * j) * P, P); }
      }
    }
    function shield(x, y, bright) {
      const rows = [".aaaaa.", "abbbbba", "abbwbba", "abbbbba", ".abbba.", "..aba..", "...a..."];
      const pal = { a: theme.stroke, b: bright ? theme.gold : theme.accent, w: theme.ink };
      rows.forEach((row, dy) => { for (let dx = 0; dx < row.length; dx++) if (pal[row[dx]]) { ctx.fillStyle = pal[row[dx]]; ctx.fillRect((x + dx) * P, (y + dy) * P, P, P); } });
    }
    function aura(fb, sec) {
      const n = 14, cx = fb.x + fb.w / 2, cy = fb.y + fb.h / 2, rx = fb.w / 2 + 5, ry = fb.h / 2 + 4;
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2 + sec * 2.4, pulse = (Math.floor(sec * 6) + i) % 3 === 0;
        ctx.fillStyle = pulse ? theme.gold : rgba(theme.gold, 0.55);
        const s = pulse ? 2 : 1;
        ctx.fillRect(Math.round(cx + Math.cos(a) * rx) * P, Math.round(cy + Math.sin(a) * ry) * P, s * P, s * P);
      }
    }
    function slash(fb, u, big) {
      // three diagonal cuts across the monster, drawn in steps
      const step = Math.floor(u * 6) / 6, len = Math.round(fb.w * 0.8 * Math.min(1, step * 2));
      ctx.globalAlpha = u > 0.6 ? 1 - (u - 0.6) / 0.4 : 1;
      for (let c = 0; c < (big ? 3 : 2); c++) {
        const x0 = fb.x + fb.w * 0.15 + c * 5, y0 = fb.y + fb.h * 0.15 - c * 2;
        for (let k = 0; k < len; k += 1) {
          ctx.fillStyle = k > len - 3 ? theme.gold : "#ffffff";
          ctx.fillRect(Math.round(x0 + k) * P, Math.round(y0 + k * (fb.h / fb.w) * 0.9) * P, P * (big ? 2 : 1), P);
        }
      }
      ctx.globalAlpha = 1;
    }
    function burst(fb, u, f) {
      const cx = fb.x + fb.w / 2, cy = fb.y + fb.h / 2, r = rng(f.seed || 1), step = Math.floor(u * 10) / 10;
      const reach = (f.big ? 0.75 : 0.55) * Math.max(fb.w, fb.h) * ease(step);
      const cols = ["#ffffff", theme.gold, theme.lamp2, theme.accent];
      for (let i = 0; i < (f.big ? 22 : 14); i++) {
        const a = r() * Math.PI * 2, d = reach * (0.5 + r() * 0.5);
        if (step > 0.7 && i % 2) continue;
        const x = Math.round(cx + Math.cos(a) * d), y = Math.round(cy + Math.sin(a) * d), c = cols[i % cols.length];
        ctx.fillStyle = c;
        ctx.fillRect(x * P, y * P, P, P);
        if (i % 3 === 0 && step < 0.6) { ctx.fillRect((x - 1) * P, y * P, 3 * P, P); ctx.fillRect(x * P, (y - 1) * P, P, 3 * P); }
      }
      if (step < 0.3) { // the ring that opens first
        const rr = 3 + step * 40;
        ctx.fillStyle = rgba(theme.gold, 0.8);
        for (let i = 0; i < 24; i++) { const a = (i / 24) * Math.PI * 2; ctx.fillRect(Math.round(cx + Math.cos(a) * rr) * P, Math.round(cy + Math.sin(a) * rr) * P, P, P); }
      }
    }
    function pillar(hb, u) {
      const cx = hb.x + 16, w = Math.round(10 * (1 - u * 0.6));
      ctx.fillStyle = rgba(theme.cta, 0.35 * (1 - u));
      ctx.fillRect((cx - w) * P, 0, w * 2 * P, (hb.y + 31) * P);
      ctx.fillStyle = rgba("#ffffff", 0.5 * (1 - u));
      ctx.fillRect((cx - 1) * P, 0, 2 * P, (hb.y + 31) * P);
    }
    function clawMark(hb, u) {
      ctx.globalAlpha = 1 - u;
      ctx.fillStyle = theme.bad;
      for (let c = 0; c < 3; c++) for (let k = 0; k < 12; k++) ctx.fillRect((hb.x + 8 + c * 5 + Math.round(k * 0.4)) * P, (hb.y + 8 + k) * P, P, P);
      ctx.globalAlpha = 1;
    }
    function ringFx(fb, u) {
      const cx = fb.x + fb.w / 2, cy = fb.y + fb.h / 2, rr = Math.max(fb.w, fb.h) * (1 - ease(u)) * 0.9 + 4;
      ctx.fillStyle = rgba(theme.gold, 0.9);
      for (let i = 0; i < 32; i++) { const a = (i / 32) * Math.PI * 2; ctx.fillRect(Math.round(cx + Math.cos(a) * rr) * P, Math.round(cy + Math.sin(a) * rr * 0.8) * P, P, P); }
    }
    function crosses(fb, u, color) {
      ctx.fillStyle = color ? colorOf(color) : theme.good;
      for (let k = 0; k < 7; k++) {
        const x = Math.round(fb.x + 4 + ((k * 11) % Math.max(8, fb.w - 8))), y = Math.round(fb.y + fb.h - ((u * 50 + k * 7) % (fb.h + 6)));
        ctx.fillRect(x * P, (y - 1) * P, P, 3 * P);
        ctx.fillRect((x - 1) * P, y * P, 3 * P, P);
      }
    }
    function smoke(u) { // a Smoke Bomb: grey puffs swell over the party, then thin out
      const r = rng(41), step = Math.floor(u * 8) / 8;
      ctx.fillStyle = rgba(mix(theme.skyLow, theme.ink, 0.35), 0.55 * (1 - step));
      for (let i = 0; i < 14; i++) {
        const cx = Math.round(W * (0.55 + r() * 0.4)), cy = Math.round(horizon + (L - horizon) * (0.1 + r() * 0.8)), rad = Math.round(3 + step * (6 + r() * 8));
        for (let y = -rad; y <= rad; y++) { const w = Math.round(Math.sqrt(rad * rad - y * y)); ctx.fillRect((cx - w) * P, (cy + y) * P, (w * 2 + 1) * P, P); }
      }
    }
    function wave(u) { // an attack on everyone: a band sweeping right across the floor
      const x = Math.round(W * (0.3 + 0.7 * ease(u)));
      ctx.fillStyle = rgba(theme.bad, 0.18 * (1 - u));
      ctx.fillRect((x - 16) * P, horizon * P, 16 * P, (L - horizon) * P);
      ctx.fillStyle = rgba(theme.bad, 0.4 * (1 - u));
      ctx.fillRect(x * P, horizon * P, P * 2, (L - horizon) * P);
    }
    function doneFlag(fb) {
      const x = Math.round(fb.x + fb.w + 3), y = Math.round(foeSlot.feet - 22);
      ctx.fillStyle = theme.ink; ctx.fillRect(x * P, y * P, P, 22 * P);
      ctx.fillStyle = theme.good; ctx.fillRect((x + 1) * P, y * P, 9 * P, 6 * P);
      ctx.fillStyle = "#ffffff"; ctx.fillRect((x + 3) * P, (y + 2) * P, 4 * P, P);
    }
    // the classic dissolve: the monster's pixels drop out in blocks, from the top down, then it's gone
    function dissolve(img, u) {
      const c = blank(img.width, img.height), g = c.getContext("2d");
      g.drawImage(img, 0, 0);
      if (u < 0.12) { g.globalCompositeOperation = "source-atop"; g.fillStyle = "#ffffff"; g.fillRect(0, 0, c.width, c.height); g.globalCompositeOperation = "source-over"; }
      const bs = P * 2, cols = Math.ceil(c.width / bs), rowsN = Math.ceil(c.height / bs);
      for (let y = 0; y < rowsN; y++) for (let x = 0; x < cols; x++) {
        const hsh = ((Math.imul(x + 1, 73856093) ^ Math.imul(y + 1, 19349663)) >>> 0) % 1000 / 1000;
        if (hsh * 0.7 + (y / rowsN) * 0.3 < u * 1.05) g.clearRect(x * bs, y * bs, bs, bs);
      }
      return c;
    }

    function render() { if (alive_ && theme) draw(now()); }
    function loop(ts) {
      raf = 0;
      if (!alive_) return;
      if (!root.isConnected) { destroy(); return; }
      const t = ts || now();
      if (t - lastDraw >= 30) { lastDraw = t; draw(t); }
      for (let i = fx.length - 1; i >= 0; i--) if (t >= fx[i].t0 + fx[i].dur) fx.splice(i, 1);
      if (!document.hidden) raf = requestAnimationFrame(loop);
    }
    function wake() { if (!still && alive_ && !raf && typeof requestAnimationFrame === "function") raf = requestAnimationFrame(loop); }
    document.addEventListener("visibilitychange", wake);

    // ---- theme and size
    function redraw() { if (!alive_) return; measure(); render(); }
    const mo = typeof MutationObserver === "function" ? new MutationObserver(redraw) : null;
    mo && mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
    const scheme = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;
    scheme && scheme.addEventListener && scheme.addEventListener("change", redraw);
    let size = "";
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => {
      const s = stage.clientWidth + "x" + stage.clientHeight;
      if (s !== size) { size = s; redraw(); }
    }) : null;
    ro && ro.observe(stage);

    function destroy() {
      if (!alive_) return;
      alive_ = false;
      if (raf && typeof cancelAnimationFrame === "function") cancelAnimationFrame(raf);
      raf = 0;
      clearTimeout(waitTimer);
      for (const id of timers) clearTimeout(id);
      timers.clear();
      queue.length = 0;
      mo && mo.disconnect();
      ro && ro.disconnect();
      scheme && scheme.removeEventListener && scheme.removeEventListener("change", redraw);
      document.removeEventListener("visibilitychange", wake);
      document.removeEventListener("keydown", pageKey);
      root.remove();
      art.clear();
    }

    // ---- start
    measure();
    renderMenu();
    paintStatus();
    render();
    if (document.fonts && document.fonts.load) document.fonts.load(`700 14px ${FONT}`).then(render, () => {});
    if (st.phase === "done") {
      menuWin.hidden = true;
      addLine(lab("bossDone", { monster: st.foe.name }));
      showResult();
    } else {
      play(st.events, () => { ready(); if (opts.autofocus !== false) focusFirst(true); });
    }
    // a question on its way: said when it comes (Answer turns on); none after all, and the title shows as usual
    if (studyLater) {
      const arrive = (sv) => {
        studyDone = true;
        if (!alive_ || st.phase !== "hero" && st.phase !== "monster") return;
        const evs = ask(st, sv);
        if (!evs.length) evs.push({ t: "say", key: "intro", vars: { monster: st.foe.name, title: st.foe.title } });
        paintEpithet();
        play(evs, () => { if (!busy) renderMenu(); });
      };
      opts.study.then(arrive, () => arrive(null));
    }
    wake();
    return { destroy, redraw };
  }

  window.Battle = Object.freeze({ mount, rules: RULES, LABELS: Object.freeze({ ...LABELS }), LABEL_VARS: Object.freeze(LABEL_VARS), format: fill });
})();
