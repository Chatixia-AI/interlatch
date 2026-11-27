# Design: lessons people learn from

[← Roadmap](../../ROADMAP.md) · 日本語: [ja/learning.md](ja/learning.md)

Status: proposal, 2026-10-08. Built so far: steps 1 and 2 of the order of work (case files, without related cases;
see [Case files](../analysis.md#case-files)), step 4 (the hub), and on 2026-10-09
[Learn from your work](../dashboard.md#pages): every lesson in a library by topic, each told at once (a case file's
scene, question and answer, the explanation, the leads ruled out, and the principle, checks and diagram the analysis
found), with work notes and revisits kept in the browser. Asking before telling stays in All knowledge. The rest,
measurement included, is tracked as [issues](https://github.com/Chatixia-AI/interlatch/issues) with no dates.

Interlatch writes down what every session taught, and today almost all of it goes to the agents: the MCP tools and
the start-of-session notes. People meet the same lessons as lists. On a computer that shares with a team hub, its
teammates' lessons show up only as a count in **Settings › Devices**, while its agent gets all of them. The agent
remembers, so the person never has to.

This page describes how Interlatch could also help the people learn: as engineers, and as architects.

## What the research says

A study of the learning science, of AI assistance and skill formation, of how teams learn and of the products that
keep knowledge alive came to these conclusions:

- **Agents take away the struggle where learning happens.** In a controlled trial with 52 mostly junior
  developers, those who learned a new library with an AI assistant scored 50% on a later quiz against 67% for those
  who coded by hand, with the biggest gap on debugging ([Shen & Tamkin, 2026](https://arxiv.org/abs/2601.20245)).
  Those who asked the AI questions, rather than handing it the work, kept most of their learning.
- **Reading a lesson is not learning it.** Recalling an answer and then seeing the feedback beats rereading by
  about g = 0.73 ([Rowland, 2014](https://doi.org/10.1037/a0037559)). People who learned from AI summaries ended up
  with shallower knowledge than people who searched for themselves, even with the same facts (Melumad & Yun, PNAS
  Nexus, 2025). A lesson card is such a summary.
- **Stories and comparison carry over to new problems; a single rule rarely does.** Comparing two cases beats
  studying one by d = 0.50 ([Alfieri et al., 2013](https://files.eric.ed.gov/fulltext/ED528947.pdf)). Training
  that treats errors as information beats training that avoids them, most of all on unfamiliar tasks (Keith &
  Frese, 2008).
- **Teams learn when it is safe to share mistakes.** Per-person counts teach people to stop sharing. DORA's and the
  SPACE framework's authors warn against individual metrics; credit people as the source of a lesson, never as a
  number.
- **Small, regular, forgiving.** Products that keep people coming back ask for very little at a time and never let
  a backlog build up.

The caveat: no study has tested learning from coding-agent sessions. Most of the learning science comes from
students and lab tasks, so applying it to working developers is an extrapolation, which is why this design ends
with a way to measure it.

## Rules for every part below

1. **Ask before telling.** A lesson that matters opens with the situation and asks what you would do, before
   showing the answer.
2. **Always skippable.** One key skips the question. Someone who keeps skipping stops being asked.
3. **The agent always gets every lesson.** Nothing here holds knowledge back from the agent.
4. **Credit people as teachers.** "Learned in Ana's session", never a count per person.
5. **Per-person learning data stays on that person's computer.** Only team-level totals, if anything, reach the
   hub, and only by choice.
6. **Fixed vocabularies, real sources.** Principles, weakness IDs and skills come from fixed lists; reading links
   come from a curated library. The model picks from them; it never invents a principle or a source.

## 1. Case files

Gotchas, fixes and decisions are shown as case files:

| Part | What it holds | Where it comes from |
|---|---|---|
| The scene | The symptom: what was seen, the error | Needs a new field from the analysis |
| Leads ruled out | What was tried and turned out wrong | Needs a new field from the analysis |
| Your call | The question, with three answers to pick from, or skip | The real fix plus real dead ends |
| The verdict | The cause and the fix | The lesson's body |
| The rule | One line to remember | The lesson's title |
| Status | Solved (or Ruled, for a decision), seen once or corroborated ×N | The lesson's trust stage |
| Investigators | Who worked on it, and with which agent | The session; on a hub, whose sessions stated it |
| Related cases | Other cases with the same rule, side by side | Lessons that share a principle or a rule |

A decision reads as a case of its own: the question, the options weighed, the ruling, and its consequences (what
became easier, what became harder). Commands, facts and preferences stay plain reference cards.

The wrong answers must be real dead ends from the session, and the reveal marks them wrong. Answers a model made
up could teach something false. The extra fields extend the existing roadmap item for lessons with their reason
attached. The analysis runs on each computer, so a hub that takes knowledge only still gets them.

## 2. The architect's lens and track

Each case also says which design principle it shows, so the person learns the principle, not just the fix. The lens
under a case has the principle, how it applies beyond this bug, an open question to think through, and one to
three readings.

The **Architect's track** groups the cases by principle. Each principle has a summary, its cases, a kata (a design
question about the person's own project, with no single right answer) and a reading list. Progress on the track is
visible only to the person.

A first principle list, drawn from the cases in Interlatch's own archive:

| Principle | Example from Interlatch's own sessions | Reading |
|---|---|---|
| Know what's running | The dashboard served web files read once at startup | [The Twelve-Factor App: Build, release, run](https://12factor.net/build-release-run) |
| Cache invalidation keys | Python kept a stale `.pyc` after an edit and revert in the same second | [PEP 552](https://peps.python.org/pep-0552/) |
| Schema evolution | New tables never reached existing databases; Bob renamed a field | [Evolutionary Database Design](https://martinfowler.com/articles/evodb.html), [Tolerant Reader](https://martinfowler.com/bliki/TolerantReader.html), Kleppmann's *Designing Data-Intensive Applications* ch. 4 |
| Trust boundaries | Behind a proxy, a visitor's `Host` header looked local | [OWASP: Host header injection](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/07-Input_Validation_Testing/17-Testing_for_Host_Header_Injection), [NIST SP 800-207](https://csrc.nist.gov/pubs/sp/800/207/final) |
| One writer, many readers | The single-writer hub instead of a shared Postgres | [Leader and Followers](https://martinfowler.com/articles/patterns-of-distributed-systems/leader-follower.html), *Designing Data-Intensive Applications* ch. 5 |
| Decisions with their consequences | Recording why the hub stayed the only writer | [Documenting Architecture Decisions](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions) |

The full list should follow the quality attributes and tactics of *Software Architecture in Practice* (Bass,
Clements, Kazman), so it covers availability, modifiability, performance, security and the rest, not only what one
archive happened to hit. A team can add its own principles and readings.

Other tags from fixed lists:

- **Security lessons** get a [MITRE CWE](https://cwe.mitre.org/) ID: the `Host` header case is
  [CWE-807](https://cwe.mitre.org/data/definitions/807.html), Reliance on Untrusted Inputs in a Security Decision.
- **Lessons about the agents themselves** get a risk name from the
  [NIST AI 600-1 Generative AI Profile](https://doi.org/10.6028/NIST.AI.600-1). An agent that wrote figures from
  memory is a case of *confabulation*.
- **For a company,** the loop maps onto the [NIST SSDF](https://csrc.nist.gov/pubs/sp/800/218/final) (SP 800-218):
  task RV.3.1 analyzes root causes, RV.3.2 looks for patterns over time, and RV.3.3 reviews the software for
  similar problems "to eradicate a class of vulnerabilities". Related cases are RV.3.3.

## 3. Weekly review in recall mode

The weekly review each computer already writes gains five cases: two new ones from the week and three older ones
that are due again, in case-file form. Each ends with "knew it" or "didn't", and soon, later or someday, which
sets when it comes back. There is never an overdue count, and a missed week builds no backlog.

## 4. The team

- **Teammates' lessons where members see them.** A "from teammates" badge and filter on project pages and in the
  weekly review, credited to whose session it came from. When two people stated the same lesson, it shows both
  cases side by side.
- **No per-person counts on the hub.** The team's Home shows who has worked on what (projects and topics, no
  numbers) instead of lesson counts per person. A lesson count mostly counts what went wrong in someone's sessions.
- **Case of the week.** The team's Home shows one case, with decisions and successes as well as gotchas, as a
  regular thing to read together.
- **Members react.** Useful, outdated, or I knew this. Today only admins can pin or dismiss. "Outdated" also
  carries a computer's own check that a lesson's files have changed, since a hub that takes knowledge only never
  sees file paths.
- **A casebook for newcomers.** On a project you have no sessions in, a Start here page: the most corroborated
  cases, the decisions and why, how to run and deploy, and who to ask.

## 5. Your skills

A private page of what the person has worked on and what they have shown they know, built from their sessions.

It keeps two columns apart, because a session where a skill appeared is not evidence that the person has it:

- **Worked on with your agent:** projects, tools, principles met, lessons produced.
- **Shown you know it:** cases answered correctly weeks later, a cause named before the agent named it, decisions
  made and recorded.

The map follows fixed skill lists: the [SFIA 9](https://sfia-online.org/en/sfia-9) skills (such as Solution
architecture, Software design, Programming/software development), the task, knowledge and skill statements and
work roles of the [NIST NICE Framework](https://www.nist.gov/itl/applied-cybersecurity/nice) (such as Secure
Software Development), and the knowledge areas of [SWEBOK v4](https://www.computer.org/education/bodies-of-knowledge/software-engineering).
It also shows growth over time (principles covered, recurring mistakes going down) and the areas new to the
person, each with its next kata and reading.

It never shows levels. SFIA's levels describe responsibility, autonomy and influence, which sessions can't show.
The person can export it as a draft brag document, every claim linked to its sessions, for their own growth and
review conversations. Nothing from it reaches the hub.

## Measuring it

Asking people whether it helped flatters a passive list, so the measures are recall and behaviour:

| Measure | What it is | Where |
|---|---|---|
| Skip rate | Share of questions skipped, per person per week | The person's computer |
| Delayed recall | Right answers on a case 2 to 6 weeks after first seeing it | The person's computer |
| Recurrence | Whether a friction cause tied to a lesson the person has seen comes back in their later sessions | The person's computer (the friction catalog) |
| Human-first naming | In a later session that hits the same cause, whether the person names the cause or fix before the agent does | The person's computer |
| Sharing health | Share of sessions shared with the hub, before and after the hub changes | The hub, team totals only |

One trap: a gotcha can stop recurring because the agent's start-of-session notes prevent it, not because the
person learned anything. So the agent always gets every lesson, and only what the person sees varies: for each
eligible lesson, ask first, show plainly, or don't show. Comparing within each person works for teams of five to
twenty. Run it on Interlatch's own use for six to eight weeks before building past parts 1 and 3.

## Not doing

- Leaderboards, streaks you can lose, overdue counters, "most gotchas" views.
- Per-person numbers on the hub, or anything a manager could use to evaluate someone.
- More than one notification a week.
- Principles, skills or reading links the model makes up.
- Skill levels inferred from sessions.

## Licences of the vocabularies

NIST publications (SSDF, SP 800-207, AI 600-1, NICE) are works of the US government and can be quoted. CWE is free
to use under MITRE's terms of use. OWASP material is CC BY-SA. SFIA is free for individuals and for use inside an
organization; building it into a product needs a check of the SFIA Foundation's licence first. ISO/IEC 25010 is
paid, so use its quality-attribute names only.

## Order of work

1. Extract the scene and the ruled-out leads (the lessons with their reason attached).
2. Case files, with the ask-before-telling question.
3. Weekly review in recall mode, with the measures above built in.
4. On the hub: who has worked on what in place of per-person counts; teammates' lessons on members' dashboards.
5. The architect's lens and track.
6. Member reactions, case of the week, the newcomer casebook.
7. Your skills.
