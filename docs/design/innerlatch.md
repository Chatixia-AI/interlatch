# Design: Innerlatch, learning from your own past

[← Roadmap](../../ROADMAP.md) · 日本語: [ja/innerlatch.md](ja/innerlatch.md) · Related: [lessons people learn from](learning.md)

Status: study battles built in 0.27.3 (2026-10-18); the hero (section 2) and gate bosses (section 7) built in October
2026. The job goal (section 8) is planned, next in the order of section 10. The user guide is [Adventure](../party.md#adventure).

Innerlatch is the game behind the door at the bottom of the dashboard's rail. **You are the hero:** a character you
make, on a career path you choose, who beats the lessons of your own past work and levels up toward a real job. Your
coding agents are your companions, the ones who brought those lessons back. This page records what each part is for,
what a lesson and a battle mean, and why each part works the way it does, including what was tried and dropped on the
way.

## 1. The goal

**You learn from your own past work and move along your career path, toward a job you choose.**

Your agents learned these lessons in your sessions; the Adventure is where you make them yours. That sets three
meters apart, and nothing ever moves one from another:

| Meter | What it says | Where it comes from |
| --- | --- | --- |
| Your companions' levels | How much of each roadmap module your agents' lessons cover | Lessons only (unchanged since the Party began) |
| Your readiness | How many of a module's lessons you have shown you understand | Study battles you answered right |
| Your rank in a module | What you have shown you can do, on the roadmap's 0–4 scale | Gates passed with real work (section 7) |

**Your hero's level** sums up the last two (section 2.4). It is there to feel progress often; the job goal reads ranks
only, never the level.

Gold, gear and the arena stay a game around these: they change battle numbers, never a level, a readiness, a rank or
XP.

## 2. The hero

### 2.1 Making your character

The first time you open the door you make your hero, once: a name and a pixel look (body, skin, hair and its colour,
outfit colour, one accessory), drawn from the same parts as the companions' sprites. You can change the look later.

**Everyone starts at level 1 with every rank at 0.** Nothing is granted for past work, however much there is: an old
pull request counts the day you take it through a gate (section 7). The making screen may say which tracks your past
sessions touch most, as a hint for the next step, labelled as what your agents' lessons cover, not as your level.

### 2.2 Choosing a path

A path is one of the roadmap's 65 tracks (grouped in 12 categories), or the Generalist path. Your path sets:

- **the main quest:** the track's milestones G1–G4, which you mark done yourself, as today;
- **the target rank of each module**, from the track's targets (2 to 4);
- **which zones come first** on the field: the path's modules hold four monsters, other zones two.

You can change path at any time. Readiness and ranks belong to modules, not paths, so they carry over: changing path
only changes which targets you are measured against.

### 2.3 The Generalist path

The Generalist path is built from the catalog, not picked by hand: **the modules at least half of all tracks include,
plus those at least five tracks mark as critical**, each with a target rank of 2. On the current catalog that is eleven
modules: Python and debugging, software engineering, SQL and data modelling, system design, statistics and
measurement, product discovery, cloud and identity, AI evaluation, customer delivery, enterprise architecture, and
Linux. It has no milestones of its own; its main quest is reaching rank 2 in all eleven. A catalog change updates it with no extra rule.

### 2.4 How the hero levels up

The hero earns XP from the two meters, computed from the records each time (nothing extra is stored, so past study
counts from day one and nothing can drift):

| What happened | XP |
| --- | --- |
| A lesson answered right for the first time | 10 |
| A right answer when it comes back | 5 |
| A lesson becomes owned | 20 |
| A gate passed | 50 × the rank it gives |

The level follows a slower curve than the companions' (starting proposal: level L at 10·(L−1)² XP, tuned in step 1).
Wrong answers and time away take nothing away.

### 2.5 Your companions

The coding agents stay as they are: pixel characters with levels from lesson coverage, their class, their journey,
the formations and the arena. They are your companions, listed under the hero on the Party page. In a study battle
the hero stands in front and answers; the companions stand behind, as the ones who found the lesson.

The arena stays companions only (formations, sheet v3): your answers, ranks and job goal never leave your computer,
so the hero has nothing to send.

## 3. What a lesson and a battle mean

**A lesson is a worked example from your own past**, filed under the roadmap module it speaks to. Until you show you
understand it, it is study material your companions collected for you, not something you know.

**A battle is one study rep on that example.** It opens on the scene (the moment as it was met, with the answer left
out) and a question, with four answers to choose from. The lesson's title stays hidden until it is answered: titles
often state the answer. You choose one, then **Answer**:

- **Right** is the finishing blow: the monster is beaten, the hero gains XP, the first win pays gold, and the lesson
  counts toward your readiness in its module.
- **Wrong** lets it slip away. The end screen says why that answer is wrong, then shows the right one and the principle
  behind it, the part that holds beyond that project.

**Run** (or Esc) leaves at any time. There is no Attack, Skill, Item or Defend in a study battle: the knowledge is the
only move.

## 4. Decisions and why

### Who levels up: you, not your agents

The Party began with the agents as the characters, levelled by their lessons. That celebrated the agents' work, but
the goal is yours: an agent's level says what its lessons cover, never what you can do. So the hero is a character of
its own, levelled only by what you answer and what you pass, and the agents become companions with their levels
unchanged.

### What a rank means: the roadmap's scale

Ranks use the career roadmap's own scale, so a rank says the same as the roadmap's evidence label:

| Rank | Means | Evidence the gate asks for |
| --- | --- | --- |
| 0 | Not yet shown | — |
| 1 | Can explain the concept and its limits | An accurate explanation |
| 2 | Can do a bounded task and debug or explain it | One of your artifacts and an explanation of it |
| 3 | Can design, build, diagnose and run representative work on your own | A practical result and what came of it |
| 4 | Can set standards, weigh tradeoffs across teams and teach | Several adopted decisions, across projects |

### What you explore: one region at a time

The world map used to put every counted lesson on one continent as a monster: over a thousand of them on one archive.
A map covered in icons reads as a checklist to clear, not a place (games call it "chickenpoxing"). The Adventure now
opens on a **field**: one region, each roadmap module a zone joined by paths, a few monsters per zone, walked like a
handheld RPG. The world map stays, without creatures, as an overview, and will hold the job goal's castle.

### Which lessons become monsters: the ones worth studying

- **Your job goal's modules first, then your path's.** Those zones hold four monsters, other zones two.
- **Study material first.** Lessons with a case file's question or a principle come before the rest.
- **Never your agents' own friction.** A gotcha that applies everywhere and keeps recurring, or one tagged as shell or
  agent tooling (an aliased `rm`, zsh word splitting, where screenshots land), is a trap for the agents' rules, not
  something for your career. On one archive that kept 97 lessons off the field.

### How understanding is checked: multiple choice written for each lesson

This took four tries:

1. **Number fights.** The first battles were stat and dice fights with the lesson as decoration. Replacing every
   lesson's text in 300 simulated fights changed none of them: nothing the person knew went in, nothing came out.
2. **A pick from the case file with borrowed wrong answers** (the Diagnose experiment, closed unmerged). Only about one
   lesson in ten had a case file, and 63% of the playable ones had no recorded dead ends, so their wrong answers were other
   lessons' answers: easy to rule out. In about one case in five the title, shown first, held most of the answer, and a
   wrong pick could simply be followed by the next one.
3. **A typed answer judged by the model.** Honest and flexible, but it asked for writing in the middle of a game, and
   it didn't land.
4. **Multiple choice written for the lesson** (what shipped). The analysis model writes the scene, the question and
   four answers once per lesson, from the lesson itself: the leads its session ruled out first, then likely
   misconceptions, each with why it is wrong. The set is checked (exactly one right, no two alike, the right one never
   the longest) and kept with the lesson. The first pick of a fight counts, and the server checks it, so no model call
   is needed to answer.

### When a lesson comes back: spaced, never overdue

- **Right:** it rests and comes back for another look as you would start to forget it: 3 days, then 10, 30 and 90 as
  you keep getting it right, marked ↻ on the field. Right on two separate days in a row, it is **owned**.
- **Not right:** it stays off the field until tomorrow, whatever the result (a wrong answer, a win without one, Run).
  Trying again right after seeing the answer is copying, not remembering.
- **Never overdue.** No counts of what is due; a lesson that is back just waits on the field, and time away builds no
  backlog ([learning design, rules](learning.md#rules-for-every-part-below)).

### How a job comes in: pasted text

A job description is pasted, not fetched from a link: job sites often block fetching or need a sign-in, and pasted
text needs nothing sent anywhere but your own analysis model.

### Where your answers are kept: on your computer

Per-person learning data stays on the person's own computer ([learning design](learning.md)). Your hero, answers,
gates and job goals are kept in your archive on this computer. Signed in to a hub, there is no hero: battles are the
plain turn-based kind and nothing about you is kept there. The questions themselves belong to the lessons, not to
anyone, and are kept with them.

## 5. What the evidence says

A research pass in October 2026 looked at boot.dev and two dozen other learning products, games laid over real work and
AI-agent activity, game patterns that make knowledge part of the world, and the learning research. What carried the
decisions above:

- **The knowledge has to be the move that decides the encounter.** Children learned more, and chose to play about
  seven times longer, when the maths was the combat itself rather than quizzes between levels
  ([Habgood & Ainsworth, 2011](https://shura.shu.ac.uk/3556/)). It is not a blanket rule: separating the exercise from
  play did better in one study with adults ([Nidd & Muldner, 2019](https://escholarship.org/content/qt8453p0gf/qt8453p0gf.pdf)).
  The reading that holds: integration pays when the winning move forces the relevant thinking.
- **Recalling beats rereading,** and the rereaders feel more confident: 56% against 42% remembered after a week
  ([Roediger & Karpicke, 2006](https://psychology.ecu.edu/wp-content/pv-uploads/sites/216/2019/03/Roediger-Karpicke-2006.pdf)).
  Multiple-choice practice with feedback works well
  ([Adesope et al., 2017](https://journals.sagepub.com/doi/10.3102/0034654316689306)), and spacing helps it stick;
  Duolingo's spaced review raised daily engagement 12% ([Settles & Meeder, 2016](https://aclanthology.org/P16-1174/)).
- **A level means something only when a check stands behind it.** Boot.dev pays out only for work a machine has checked,
  and in April 2026 replaced even its self-marked lessons with multiple-choice checks
  ([Boot.dev, April 2026](https://www.boot.dev/blog/news/bootdev-beat-2026-04)). Codewars raises rank only for problems
  never solved before ([Codewars](https://docs.codewars.com/gamification/ranks/)); WaniKani unlocks items only once
  their prerequisites are remembered, and lets them fall back
  ([WaniKani](https://knowledge.wanikani.com/wanikani/srs-stages/)). Hence readiness from answers, ranks only from
  gates, and the job goal reading ranks, not the hero's level.
- **Rewards for taking part lower the wish to do the task; feedback raises it**
  ([Deci, Koestner & Ryan, 1999](https://depts.washington.edu/techdocs/papers/deciExtrinsicRewardsAndIntrinsicMotivation99.pdf)).
  Points, levels and leaderboards raise how much people do, not how well
  ([Mekler et al., 2017](https://glassmanlab.seas.harvard.edu/annotated_works/mekler17.pdf)). Hence XP and gold only on
  a right answer or a passed gate, and the end screen's feedback, answer and principle as the real reward.
- **Counters steer behaviour.** When GitHub removed its streak counters, weekend work and keep-alive commits fell
  ([Moldon et al., 2021](https://ar5iv.labs.arxiv.org/html/2006.02371)), and a broken streak lowers later engagement
  even when the work behind it is the same
  ([Silverman & Barasch, 2023](https://www.colorado.edu/business/faculty-research/2023/04/19/or-track-how-broken-streaks-affect-consumer-decisions)).
  Hence no streaks and no overdue counts.
- **Gamified learning helps moderately on average and varies a lot with the design.** Only its effect on learning held
  up in the most rigorous studies, not the one on motivation
  ([Sailer & Homner, 2020](https://link.springer.com/article/10.1007/s10648-019-09498-w)), and most of the evidence
  comes from classrooms, often with children. So each part ships as a small experiment, judged on what is remembered later
  and on whether you come back, not on how effective it feels.

## 6. What the parts are

Built:

- `src/interlatch/knowledge/study.py`: which lessons are worth studying, the written question and answers
  (`ask`), the first pick (`pick`), the record, readiness and when a lesson comes back (`schedule`).
- `src/interlatch/web/field.js`: the region's field (its layout is pure and tested), walking, the monsters, the HUD.
- `src/interlatch/web/battle.js`: the battle; a study battle's rules are pure and tested in Node.
- `GET /api/party/ask` sends the scene, the question and the answers' texts, never which is right;
  `POST /api/party/pick` checks the first pick on the server.
- `src/interlatch/knowledge/hero.py`: the hero's sheet (name and look; the path is the Party's goal, a track or
  `generalist`), the Generalist path, XP and level from the study record and the gates. The `hero` block of
  `GET /api/party`; `POST /api/party/hero`. `Sprites.heroKey(look)` draws the hero like a companion.
- `src/interlatch/knowledge/gates.py`: which gates are open, what each rank asks, the work to link, the attempt and
  the model's verdict. `GET`/`POST /api/party/gate`; the map's `gates` puts them on the field.

Planned:

- `knowledge/jobs.py`: the job goals, the model's mapping, the match. `GET`/`POST /api/party/jobs`.

## 7. Gate bosses

The rank meter: what you can do in a module, moved only by real work.

- **When a gate opens.** Five lessons understood in a module open its rank 1 gate. The zone's sign becomes the gate,
  with its boss standing there. Each pass opens the next rank's gate, up to 4.
- **What a gate asks.** The rank's evidence from the scale in section 4, held against the module's practice task and
  pass check from the roadmap (such as "explain why the fix works and when it would not"):
  - rank 1: explain the concept and its limits in a few sentences, no artifact needed;
  - rank 2: link one of your own pull requests, commits or documents that Interlatch already found, and say in two or
    three sentences how it meets the check;
  - rank 3: the same, plus what came of it (tests that now pass, a measured result, an incident closed);
  - rank 4: several decisions of yours that others adopted, from more than one project.
- **How it is judged.** The analysis model judges the explanation and the linked work against the check's text:
  passed, or not yet with what is missing. A gate not passed can be tried again tomorrow, as with lessons.
- **What passing does.** The module's rank rises, on this computer, apart from your companions' levels. The hero gains
  XP, and the hero card shows each module's rank against your path's target. Ranks never fall with time away.

## 8. Next: the job goal

The goal at the end of the path: a real job you want.

- **Adding a job.** Paste a job description, with the role and company if you like. It is kept in your archive, and
  sent only to your own analysis model, once, to map it.
- **What the mapping gives.** Each requirement the description states, as a roadmap module and the rank it needs
  (1–4), marked must-have or nice-to-have, with the line of the description it came from. The set is checked: modules
  that exist, quoted lines that are in the text, at most twelve. What the roadmap doesn't cover (years of experience, a
  language, a degree, a visa) is listed as outside the game, not dropped. With no analysis model, you pick the
  requirements from the module list yourself.
- **The castle.** The active job is a castle at the edge of the world map, one tower per requirement. A tower lights
  when your rank reaches what it needs. The match is the must-haves lit out of all must-haves, with the ranks still to
  go; readiness shows apart from it, as gates you could take next.
- **Steering the path.** The job suggests the track whose targets fit its requirements best; switching is your call.
  The job's modules lead the field (section 4).
- **Several jobs.** Keep as many as you like; one is active at a time.
- **Later: the final boss.** Interview questions written from the job description and grounded in your own past
  work, answered like a gate.

## 9. Measuring it

On your own computer only: delayed recall (right answers when a lesson comes back after days), how often you run from
a question, how often you come back, gates tried and passed, and whether a lesson's trap stops recurring in your later
sessions.

## 10. Build order

One pull request each, judged before the next:

1. **The hero.** Character making, the path (with Generalist), XP and level from the existing study record, the hero
   card at the top of the Party page, companions under it.
2. **Gate bosses** (section 7), which move the ranks.
3. **The job goal** (section 8): paste, mapping, the castle, the match, the field led by its modules.
4. **The final boss**, later.

## Not doing

- Raising a companion's level from your answers, or your readiness or rank from your companions' lessons.
- Granting ranks or levels for past work that hasn't been through a gate.
- Streaks, overdue counts, falling ranks, or anything that punishes time away.
- Ranking people by what they understood or passed, on a hub, the leaderboard, the arena or anywhere else.
- Sending your answers, ranks or job goals to a hub.
- Fetching job descriptions from links.
