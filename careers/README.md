# The Party's market numbers

`pipeline.py` tells the Party which of the roadmap's classes (tracks, `src/interlatch/knowledge/roadmap.json`)
employers are hiring for. It reads the companies' own job boards, maps each new technical posting to a class with a
cheap model, and publishes the counts. Global is the main series. Japan is its own series, labelled with its sample
size: the boards this can read hold few Japanese employers (see "Limits").

It uses the standard library only. Run it from the repository: `python3 careers/pipeline.py --help`.

## What it stores

Only derived data. The boards' APIs are there for showing postings, not for keeping copies, so no posting's title,
link or description is written anywhere: not to disk, not to Firestore, not to the log. They exist in memory while the
model reads them.

- Each posting becomes a record in `postings.jsonl` (or `careers_postings/<id>` in Firestore) with exactly these
  fields:
  - `id`: sha256 of `provider:token:posting id`, first 24 hex characters
  - `company` and `region` (`global` | `japan`)
  - `first_seen` and `last_seen`, and `open`
  - the mapping: `cls`, `cls2`, `fit` (`good` | `partial` | `none`), `modules` (at most 6), `industry` and
    `seniority`
  - `mapped_by` and `catalog_version`
- `market.json` (or `market/current` and `market/<YYYY-MM>` in Firestore) is the aggregate the dashboard shows. With
  `--bundle` it is also written as the dashboard's fallback, `src/interlatch/knowledge/market.json`.

## One run

1. Fetch every board in `boards.json` through the provider's official public API (Greenhouse, Ashby or Lever). That is
   one request per board, one board after another, about a second apart, with the User-Agent `interlatch-careers`.
2. Normalize the postings and drop identical ones (same company, title and place). On a board marked
   `"copies": "en-ja"`, which posts each role in English and in Japanese, keep the English copy.
3. Strip the company's boilerplate: lines that appear on 40% or more of a board's postings.
4. Keep the technical postings, judged by title, in English and in Japanese. 設計, 技術 and 開発 count; 事業開発
   (business development), sales, recruiting, legal and finance titles don't.
5. Set the region. A posting whose location names a place in Japan (in either language) is `japan`. So is a Japan-based
   company's posting whose location names no place ("Hybrid", "Remote"), as PayPay's do.
6. Diff by posting id against the history, never by the boards' update times, which change on most postings every
   month. A posting is either new, still open or closed. A board that fails to load closes none of its postings.
   Closed records are kept for 211 days for the trend, then deleted.
7. Map the open postings that have no mapping yet, or one made with another catalog version. They go in batches of 90
   per model call; whatever a batch leaves out is asked again once, and what still fails waits for the next run. The
   run stops at `--max-calls` or `--max-cost`, or as soon as its first calls project past `--max-cost`.
8. Aggregate:
   - **Counts:** open postings that fit a class `good` or `partial`.
   - **Shares:** board-weighted. Each posting counts `1/sqrt(its company's postings in the region)`, so one company's
     hiring spree doesn't drown the rest; in the 2026-10 pilot, SpaceX and Anduril held 28% of the postings.
   - **New:** postings first seen in the last 90 days.
   - **Trend:** new postings over the previous window's, once the history covers both windows.
   - **Unmatched:** the weighted share of postings no class fits.

## Running it

A local test run, which writes `market.json`, `postings.jsonl` (the history the next run diffs against) and `run.json`:

    python3 careers/pipeline.py --out /tmp/careers --mapper claude --workers 4

- **Mappers:**
  - `claude` (the default) runs `claude -p --model haiku` with the dashboard's analysis runner's flags, from an empty
    folder, with `INTERLATCH_INTERNAL=1` so Interlatch's own hooks record nothing.
  - `api` calls the Messages API with `ANTHROPIC_API_KEY`, for CI.
  - `none` maps nothing.
- **Firestore:** `--firestore --auth <file>` publishes. The file is a JSON with the operator's Firebase
  `refresh_token`; it is never printed. The history then comes from `careers_postings`.
- **Dry run:** add `--dry-run` to print the writes instead of sending them. It needs no `--auth` and still writes the
  local files.
- **Rules:** they live in `leaderboard/firestore.rules`. Anyone may read `market/*`. Only the operator's account may
  write it, or read and write `careers_postings/*`. Deploy them from `leaderboard/` with
  `firebase deploy --only firestore:rules`.

## boards.json

Each entry:

- `provider`: `greenhouse` | `ashby` | `lever`
- `token`: the board's name in its URL
- `company`: the name stored on its postings. Keep it stable: the history scopes closing by company.
- `region`: where the company is based (`global` | `japan`)
- `copies`: optional, `"en-ja"`

To add a board, check its API URL answers with postings, and that the board belongs to the company you think. A
Greenhouse board called `wise` turned out to be another Wise.

## Limits

- **Japan.** Most Japanese tech employers use HERP, Talentio, HRMOS, Workday or their own sites, which this doesn't
  read. That includes SmartNews, Mercari, Money Forward, freee, LayerX, Ubie, Sakana AI and PFN. The Japan series is
  PayPay, Woven by Toyota and the Tokyo offices of global vendors: about 170 open technical postings and roughly 35 new
  ones a month. Show it with its sample size.
- **Boards move.** Mercari's Greenhouse board went empty and Confluent's Ashby board too. An `empty` board in
  `run.json` closes its postings, so check before believing it.
- **Run-to-run agreement.** In the pilot, the model's class matched a careful human check 95% of the time. Two
  separate runs agree on the best class about 77% of the time, and 92% counting the second class. Neighbouring classes
  (backend and full-stack) swap, so treat one-run moves of a few points as noise.
- **Cost.** About $0.0003 per posting at 90 a call, so a few cents to about a dollar a month mapping only new postings.
  Prices: Claude Haiku 5.5, $0.10 / $0.50 per million input / output tokens, as of 2026-10.
