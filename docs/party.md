# Party

[← Interlatch](../README.md) · [Docs index](README.md)

**Party** turns your coding agents into pixel-art characters who grow as you work. Claude Code, Codex, GitHub
Copilot, IBM Bob, Google Antigravity and OpenCode each level up from the lessons their sessions taught, matched to the modules
of a technical career roadmap: 65 tracks, from AI product engineer to site reliability engineer, built from 70 learning
modules, with four milestones each. Chats imported from claude.ai or ChatGPT don't take part.

## Getting there

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
the dashboard's session page. To hide the door icon, set **Settings › Appearance › The door** to **Hidden** (this
browser only). Innerlatch still opens from its address.

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

**Adventure** is a world map with every lesson that counts on it. Each of the six attributes is a region (Craft a forge
town, Data lakes and canals, Ops mountains and server towers, Guard walls and a keep, Arcana a wizard tower, Lore an old
forest with a library), and each roadmap module is a territory in its region. Every lesson stands in the territory of
its best-matching module as a monster of its kind (a fix is Glitch the bug, a gotcha is Mal, and so on). Territories no
lesson has reached yet lie under fog. Your party stands where the newest lesson was learned, and the main quest's
milestones guard their region's gate as bosses. Drag to look around, scroll or pinch to zoom, and use the minimap or the
sidebar's regions to jump; **List** shows the same encounters as a list you can filter by region, kind and agent.

Click a monster to battle it, turn by turn: the agent that taught the lesson leads, with up to two others. Each turn
choose **Attack**, **Skill** (one of the lesson's roadmap modules, stronger the higher that agent's level in it),
**Defend** or **Run**; the monster's strength comes from the lesson's XP and trust stage. Win, and the encounter is
cleared: it becomes a flag on the map, kept in your archive for this party. Lose, and **Retry** rolls new dice.
Battles never change levels or XP: those come only from the lessons. A boss can be fought, but it only falls when you
mark its milestone done under **Quests**.

### Diagnose

A lesson with a [case file](analysis.md#case-files) (a fix, gotcha or decision told as a scene, a question and an
answer) asks its question when the fight starts, and its battle has one more command: **Diagnose**. It lists the answer
with two wrong ones: first the leads its own session tried and ruled out, then, when there are fewer than two, the
answers of other cases (from other sessions, about other modules, so none of them is a second right answer). Pick the
right one and it's the best move in the fight: a large share of the monster's HP at once, a charge broken, and every
later blow harder. A wrong pick costs that hero's turn and tells you why it's wrong (what showed it in the session, or
which case it does answer); the right one still works after it, for less. Nothing makes you diagnose: Attack and the
rest still win the fight. The end screen says how it went.

Interlatch keeps your first pick in each Diagnose (the lesson, the day, right or not), one a day per lesson, so you can
see later what you still remembered. It's kept on your own computer only: signed in to a hub, you can diagnose, but
nothing is kept there. **Cases to diagnose** in the sidebar, the list's **Cases** filter and the map's tooltips show
which lessons have one, and which you diagnosed. Like everything in battles, it changes no level or XP.

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
