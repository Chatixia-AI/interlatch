# Party

[← Interlatch](../README.md) · [Docs index](README.md)

**Party** turns your coding agents into pixel-art characters who grow as you work. Claude Code, Codex, GitHub
Copilot, IBM Bob, Google Antigravity and OpenCode each level up from the lessons their sessions taught, matched to the modules
of a technical career roadmap: 65 tracks, from AI product engineer to site reliability engineer, built from 70 learning
modules, with four milestones each. Chats imported from claude.ai or ChatGPT don't take part.

## Getting there

Innerlatch is part of **game mode**, which is off until you turn it on: `interlatch install` asks once (*Turn on game
mode?*, no unless you say yes), and **Settings › Appearance › Game mode** or `interlatch config set game.enabled true`
switches it any time. Off, there is no door, Innerlatch's addresses say game mode is off, and nothing is sent to the
leaderboard; what you played is kept for when you turn it back on. If you had signed in for the Party or the
leaderboard before game mode was optional, it stays on. On a hub, its admins turn it on for everyone signed in to it.

The Party lives in **Innerlatch**, an area of the dashboard of its own, drawn in violet. The door icon at the bottom of
the rail brings up a large door: click it to go through. Innerlatch opens only to someone signed in (see below): signed
out, the door is locked and asks you to sign in with Google, then opens by itself once you have. Once through, you stay
in until you leave, even if you reload the page.

Inside, the rail shows Innerlatch's sections: **Party**, **Adventure**, **Leaderboard** and **Arena**, and the door at the bottom of
the rail (**Back to Interlatch**) leads back to the page you came from, and the door shuts behind you. The Leaderboard
and the Arena show only on the computer that runs Interlatch: not on a dedicated hub, and not for hub people limited to
some projects.

The Party's address is `#/innerlatch`; old `#/party` and `#/leaderboard` links still work, and every address in
Innerlatch meets the door first. A lesson link anywhere in Innerlatch opens the lesson in a window over the page, with
its roadmap modules and, for a case file, the scene, question and answer; **Open the session in Interlatch** leaves for
the dashboard's session page.

## Signing in

Innerlatch's door stays locked until you sign in with Google, with the same account as the leaderboard
([Data and privacy](privacy.md)). Signing in for the Party doesn't put you on
the leaderboard and sends nothing: your characters are worked out on your computer, from your own archive. To join the
board later, use **Join the board** on the **Leaderboard** in Innerlatch. **Sign out** on the Party page forgets the sign-in, takes
your row off the leaderboard if you joined it, and takes you back out of Innerlatch.

On a team hub, sign in to the hub as usual ([Joining your team's hub](join-a-hub.md)). You see a party made from your
own computers' sessions only. A teammate's sessions never level up your agents.

## How characters grow

- **Experience.** A lesson counts once it is active and not a work in progress, and only if it is a fix, gotcha,
  decision, learning, pattern or command. Its trust stage sets what it earns: seen once 1 XP, established 3,
  canonical 6. A module counts only when the lesson's title or tags name it, or its text mentions it at least
  twice: a word in passing doesn't. Each lesson counts toward its three best-matching modules at most, so one
  lesson can't level everything.
- **Skills.** Each roadmap module is a skill with its own level: Lv 1 at 1 XP, Lv 2 at 6, Lv 3 at 16, Lv 5 at 51,
  Lv 10 at 226. Click a skill to see the lessons that earned it.
- **Level and tier.** A character's level comes from all its XP. Its gear changes with its tier: novice (levels 1–4),
  adept (5–11), expert (12–24) and master (25 and up).
- **Attributes.** Six attributes sum the skills: Craft (software), Data, Ops (cloud, delivery, reliability), Guard
  (security, evaluation, governance), Arcana (models and agents) and Lore (product, customers, leadership).
- **Class.** An agent's class is the roadmap track its skills cover best. Critical modules count twice. An agent
  earns a class once at least 10 of its lessons count and its best track is at least 5% covered; until then the
  page shows the track it leans towards.

## Quests and the journey

The **main quest** is the four milestones of your goal track. It starts as your strongest agent's class, and you can
pick another under **Quests**. Your agents' other classes add side quests. Interlatch marks a quest *possible evidence*
when a pull request, commit, document or lesson matches it, but only you mark it done. The roadmap asks for a
person's review before a milestone counts.

**Journey** lists level-ups week by week: when each agent joined, reached a level, learned a skill or changed class,
and when you completed a quest, with a chart of each agent's level over time. A lesson that lifted several skills at
once is one line. Every join, level, class and quest stays on the list; of the skill-ups, the newest 120 do.

## Adventure

**Adventure** opens on a field to explore: one region at a time, seen from above like a handheld RPG. It's the region
you explored last (or, the first time, the one where your party stands); the sidebar's **Regions** open the others.
Each roadmap module of the region is a zone with its sign, the zones joined by dirt paths and cut apart by lines of
trees, with the region's town in the middle. Click or tap where to walk, or use the arrow keys or WASD; your party
follows the leader. The zone's banner at the top says which module you're in, how many of its lessons are left to
battle, and how many you've understood; ★ marks the zones of your main quest. The minimap in the corner shows where you
are and the monsters around.

The monsters are your lessons worth studying: in each zone, the ones with the most to learn from first (a case file or
a principle), four at a time in your main quest's zones and two elsewhere. The traps your agents keep hitting in your
setup (an aliased `rm`, zsh, where screenshots land) never show up: they belong in your agents' rules, not your career.
Walk into a monster to battle it, or into the town's gate to shop.

**World map** in the sidebar shows the whole continent: every region and its territories, fog where no lesson has
reached yet. **Encounter list** shows the lessons as a list you can filter by region, kind and agent.

### Study battles

A battle is one lesson from your own sessions as a question. It opens on the scene and the question, with four answers
to choose from; the lesson's title stays hidden, since it often gives the answer away. Choose one (click it, or press 1
to 4; the arrow keys move your choice), then **Answer** (or Enter). **Run** (or Esc) leaves at any time.

- **Right**: your answer is the finishing blow. The monster is beaten, the first win pays gold, and the lesson counts
  toward your readiness in its module.
- **Wrong**: the monster slips away. The end screen says why that answer is wrong, then shows the right one and the
  principle behind it.

The question and its answers are written once for each lesson by your analysis model, from the lesson itself: the
wrong answers are the leads your session ruled out, then likely mistakes. The first time you meet a lesson that takes
a few seconds; after that it's kept.

A lesson you fought today and didn't get right stays off the field until tomorrow, so trying again means remembering,
not copying the answer you just saw. A lesson you got right rests, then comes back for another look as you'd start to
forget it: 3 days later, then 10, 30 and 90 as you keep getting it right, marked ↻ on the field. Got right on two
separate days, it's yours. Nothing is ever overdue: a lesson that's back just waits on the field.

Your answers count toward your own readiness, never your agents' levels, which come only from their lessons. They're
kept on your own computer: signed in to a hub, battles are the plain turn-based kind (**Attack**, **Skill**, **Item**,
**Defend**, **Run**), and nothing about your answers is kept there. The same plain battles stand in when no question
can be written (the analysis model isn't reachable). A boss can be fought, but it only falls when you mark its
milestone done under **Quests**.

## Towns and gear

Each region has a **town** on the map (also listed in the Adventure sidebar). Its shop sells **gear** for that region's
roadmap modules: a weapon, an armor and a charm for each module, in three tiers. A piece opens in the shop when one of
your agents reaches its level in that skill: Lv 1 for tier I, Lv 3 for tier II, Lv 5 for tier III. So what you can buy
follows what your agents' lessons actually cover. Every town also sells **supplies** for battles: Potion, Ether,
Phoenix Down and Smoke Bomb.

**Gold** comes from the first win over each lesson (more for an established or canonical one; never again for the same
lesson, and bosses pay none). A first win can also drop **loot** of the lesson's kind, such as a Patch from a fix or an
ADR Scroll from a decision. Use supplies and loot from the **Item** command in a battle. Each agent wears three slots
(weapon, armor, charm): change them on the agent's page or in the **Bag**, where you can also sell what you don't need.
Gear only changes battle numbers: levels and XP still come from lessons alone.

## Formations

On the Party page, **Formation** builds **decks**: teams of one to three of your agents, in the order they fight
(lead, middle, anchor). Keep up to five, each with a name of your choice. Add an agent from the **Bench** below the
slots, reorder with the arrows or by dragging, and drag a bench agent onto a slot to swap it in. Until you change
anything, your party has one deck: your three highest-level agents (or, if you picked a champion before decks
existed, that agent and then the two highest). One deck is the one the Arena fields: pick it with **Field in the
Arena**, here or on the Arena page. A deck changes who fights and in what order, never a level.

## The arena

People on the leaderboard can also put their parties in the **Arena**, a page of its own in Innerlatch. Open
**Arena** in Innerlatch's rail and choose **Enter the arena** (you join the board first). From then on each sync publishes your party's
character sheet: for each of your coding agents its level, tier, class and six attribute levels, and the team you
field (which agents, in what order), with the name and picture you show on the board and the code that tells your computers apart (a
hash, not a name). No lessons, projects, sessions, titles or prompts go.

The arena is team against team, in a relay. Your team is the deck you field ([Formations](#formations)). Every
week it fights every other player's team once: the two leads fight one on one, round by round; whoever wins the bout
stays on, with the HP it has left, against the other side's next agent; a bout still going after 40 rounds puts out
the one with less of its HP left. The team that runs out of agents loses. A deck of one against a deck of one is a
plain duel. The Arena page ranks you by wins, with each player's team. Click a rival to watch the battle: your team on
the left, theirs on the right, with the same fight written out as a log underneath and both full parties below. Each battle is worked out on your own
computer from the two published sheets and the week, so both players see the same result. The numbers are
self-reported: it's a game, not a record. **Leave the arena** deletes your sheet; you stay on the board. Leaving the
board leaves the arena too.

## Who's hiring

Each class shows who's hiring for it. **Hiring** gives the open technical roles that fit the track, worldwide, and
their share of demand: each company's postings count with less weight the more it has, so one big hirer can't swamp the
share. The count in Japan shows once Japan has at least 500 postings in the sample; until then the note under the
numbers says how many it has. Under **Quests** you can sort the goal tracks by demand, each with its open roles. A character's top classes list
the modules employers ask for most, and a green corner marks them on the track.

The numbers come from the public job boards of dozens of companies, read through the boards' official APIs. Only
counts are kept: no posting's title, link or text. They are a sample of postings, not the whole market. Once you're
signed in to the Party, Interlatch reads them from its Firebase project, at most every six hours, and until then, or
offline, shows the copy that came with your version. The date under them says when they were counted, and a
**Test data** badge marks numbers from a test run. Reading them sends nothing about you ([Data and privacy](privacy.md)).

!!! note "Your agents' level, not yours"
    Levels show what your agents' lessons cover on the roadmap. They aren't your own level: the roadmap keeps that
    separate, and only a check of your own work can show it.
