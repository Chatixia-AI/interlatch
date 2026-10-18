# Design: Innerlatch, learning from your own past

[← Roadmap](../../ROADMAP.md) · 日本語: [ja/innerlatch.md](ja/innerlatch.md) · Related: [lessons people learn from](learning.md)

Status: built in 0.27.3 (2026-10-18), except the gate bosses (section 6). The user guide is
[Adventure](../party.md#adventure).

Innerlatch is the game behind the door at the bottom of the dashboard's rail: the coding agents as a party of pixel
characters, the Adventure, towns, formations and the arena. This page records what its Adventure is for, what a lesson
and a battle mean in it, and why each part works the way it does, including what was tried and dropped on the way.

## 1. The goal

**You learn from your own past work and move along your career path.**

Your agents learned these lessons in your sessions; the Adventure is where you make them yours. That sets three
meters apart, and nothing ever moves one from another:

| Meter | What it says | Where it comes from |
| --- | --- | --- |
| Your agents' levels | How much of each roadmap module their lessons cover | Lessons only (unchanged since the Party began) |
| Your readiness | How many of a module's lessons you have shown you understand | Study battles you answered right |
| Your level in a module | That you can do it | A gate passed with real work (section 6, planned) |

Gold, gear and the arena stay a game around these: they change battle numbers, never a level, a readiness or XP.

## 2. What a lesson and a battle mean

**A lesson is a worked example from your own past**, filed under the roadmap module it speaks to. Until you show you
understand it, it is study material your agents collected for you, not something you know.

**A battle is one study rep on that example.** It opens on the scene (the moment as it was met, with the answer left
out) and a question, with four answers to choose from. The lesson's title stays hidden until it is answered: titles
often state the answer. You choose one, then **Answer**:

- **Right** is the finishing blow: the monster is beaten, the first win pays gold, and the lesson counts toward your
  readiness in its module.
- **Wrong** lets it slip away. The end screen says why that answer is wrong, then shows the right one and the principle
  behind it, the part that holds beyond that project.

**Run** (or Esc) leaves at any time. There is no Attack, Skill, Item or Defend in a study battle: the knowledge is the
only move.

## 3. Decisions and why

### What you explore: one region at a time

The world map used to put every counted lesson on one continent as a monster: over a thousand of them on one archive.
A map covered in icons reads as a checklist to clear, not a place (games call it "chickenpoxing"). The Adventure now
opens on a **field**: one region, each roadmap module a zone joined by paths, a few monsters per zone, walked like a
handheld RPG. The world map stays, without creatures, as an overview.

### Which lessons become monsters: the ones worth studying

- **Your main quest first.** Zones of the main quest's track hold four monsters, other zones two.
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

### Where your answers are kept: on your computer

Per-person learning data stays on the person's own computer ([learning design](learning.md)). Your answers are kept in
your archive on this computer. Signed in to a hub, battles are the plain turn-based kind and nothing about your answers
is kept there. The questions themselves belong to the lessons, not to anyone, and are kept with them.

## 4. What the evidence says

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
  ([WaniKani](https://knowledge.wanikani.com/wanikani/srs-stages/)). Hence readiness from answers and levels only from
  gates.
- **Rewards for taking part lower the wish to do the task; feedback raises it**
  ([Deci, Koestner & Ryan, 1999](https://depts.washington.edu/techdocs/papers/deciExtrinsicRewardsAndIntrinsicMotivation99.pdf)).
  Points, levels and leaderboards raise how much people do, not how well
  ([Mekler et al., 2017](https://glassmanlab.seas.harvard.edu/annotated_works/mekler17.pdf)). Hence gold only on a
  right answer, and the end screen's feedback, answer and principle as the real reward.
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

## 5. What the parts are

- `src/interlatch/knowledge/study.py`: which lessons are worth studying, the written question and answers
  (`ask`), the first pick (`pick`), the record, readiness and when a lesson comes back (`schedule`).
- `src/interlatch/web/field.js`: the region's field (its layout is pure and tested), walking, the monsters, the HUD.
- `src/interlatch/web/battle.js`: the battle; a study battle's rules are pure and tested in Node.
- `GET /api/party/ask` sends the scene, the question and the answers' texts, never which is right;
  `POST /api/party/pick` checks the first pick on the server.

## 6. Next: gate bosses

The last meter: your level in a module, moved only by real work.

- **When a gate opens.** Five lessons understood in a module open its gate. The zone's sign becomes the gate, with its
  boss standing there.
- **What the gate asks.** The module's practice task and its pass check from the roadmap, such as "explain why the fix
  works and when it would not".
- **How you pass.** Link one of your own pull requests, commits or documents that Interlatch already found, and write
  two or three sentences on how it meets the check. The analysis model judges the explanation against the check's text:
  passed, or not yet with what is missing.
- **What passing does.** The module is marked as shown by you on your career map, kept on this computer, apart from
  your agents' levels. A "You" card on the Party page shows your readiness and the modules you have passed.

## Measuring it

On your own computer only: delayed recall (right answers when a lesson comes back after days), how often you run from
a question, how often you come back, and whether a lesson's trap stops recurring in your later sessions.

## Not doing

- Raising an agent's level from your answers, or your level from your agents' lessons.
- Streaks, overdue counts, or anything that punishes time away.
- Ranking people by what they understood, on a hub or anywhere else.
- Sending your answers to a hub.
