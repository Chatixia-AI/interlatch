"""Build a demo Interlatch home from made-up sessions, for screenshots and trying the dashboard without your own data.

    uv run python docs/demo/make_demo.py /tmp/interlatch-demo
    INTERLATCH_HOME=/tmp/interlatch-demo/home uv run python -m interlatch ui --port 8898 --open

With --hub, the demo computer is then a team hub with people: Ada at the hub, Yuma and Ueno sending from their own
computers, each with some of the sessions, so a project's page shows the team's side (whose sessions taught what).

It writes synthetic Claude Code transcripts for a fictional developer (five projects, about six weeks), then runs the
real pipeline over them: sync, analysis, knowledge bases, glossary and weekly reviews. Then a project group with its
Mindmap analyzed, and game mode on with a hero in Innerlatch: a path, lessons studied, gates passed and a job goal.
The only stand-in is `claude`: a small script that answers each analysis with the hand-written summary and knowledge
below, so the demo costs nothing and never calls a model. Dates are relative to today, so "Today" in the dashboard is
always populated.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = "/Users/demo/Projects"
PROJECTS = {
    "billing-api": "Python FastAPI service for subscriptions and invoices: Postgres, Alembic migrations, Celery workers on Redis, and Stripe for payments.",
    "storefront": "The shop's Next.js app: React Server Components, Tailwind CSS, Zod-validated forms and Playwright end-to-end tests.",
    "infra": "Terraform for the AWS stack: ECS Fargate services behind an ALB, CloudFront, and deploys from GitHub Actions with OIDC.",
    "recommender": "Similar-item recommendations: embeddings stored in Postgres with pgvector, refreshed by a nightly job.",
    "dotfiles": "Shell, editor and Homebrew setup for this Mac.",
}

# ---------------------------------------------------------------------------------------------------------------
# Sessions. A "turn" is a prompt and what the agent did: ("say", text) or ("tool", name, input, result, is_error).
# ---------------------------------------------------------------------------------------------------------------


def T(name, inp, result, err=False, patch=None):
    return ("tool", name, inp, result, err, patch)


RICH = [
    {
        "project": "billing-api", "days": 0, "at": "-5h", "branch": "fix/webhook-idempotency",
        "turns": [
            ("Customers are getting charged twice when Stripe retries the invoice.paid webhook. Can you find out why and fix it?", [
                ("say", "I'll look at how the webhook handler records processed events."),
                T("Grep", {"pattern": "invoice.paid", "path": "app"}, "app/webhooks/stripe.py:41\napp/billing/invoices.py:88"),
                T("Read", {"file_path": "app/webhooks/stripe.py"}, "async def stripe_webhook(request):\n    event = stripe.Webhook.construct_event(...)\n    if event.type == 'invoice.paid':\n        await mark_invoice_paid(event.data.object)"),
                ("say", "The handler never checks whether it has seen an event before. Stripe retries any webhook that doesn't get a 2xx within 10 seconds, and `mark_invoice_paid` triggers a charge for the next period, so a slow first attempt followed by a retry charges twice."),
                T("Bash", {"command": "uv run pytest tests/test_webhooks.py -q"}, "3 passed in 0.84s"),
                T("Write", {"file_path": "migrations/versions/2026_add_processed_events.py"}, "File created", patch=["+op.create_table('processed_events',", "+    sa.Column('event_id', sa.Text, primary_key=True),", "+    sa.Column('received_at', sa.DateTime(timezone=True)))"]),
                T("Edit", {"file_path": "app/webhooks/stripe.py", "old_string": "if event.type", "new_string": "if await already_processed(event.id): return"}, "The file was updated",
                  patch=["+    if await already_processed(session, event.id):", "+        return Response(status_code=200)", "+    await record_event(session, event.id)", " ", "     if event.type == 'invoice.paid':"]),
                T("Bash", {"command": "uv run alembic upgrade head"}, "INFO  [alembic.runtime.migration] Running upgrade 4c1e -> 9ab2, add processed_events"),
                ("say", "Each event is now recorded in `processed_events` inside the same transaction as the invoice update, and a repeat of the same `event.id` returns 200 without doing anything."),
            ]),
            ("add a test that replays the same event twice", [
                T("Edit", {"file_path": "tests/test_webhooks.py", "old_string": "", "new_string": "def test_replayed_event_charges_once"}, "The file was updated",
                  patch=["+async def test_replayed_event_charges_once(client, stripe_event):", "+    for _ in range(2):", "+        r = await client.post('/webhooks/stripe', content=stripe_event.raw, headers=stripe_event.headers)", "+        assert r.status_code == 200", "+    assert await count_charges() == 1"]),
                T("Bash", {"command": "uv run pytest tests/test_webhooks.py -q"}, "1 failed: SignatureVerificationError: No signatures found matching the expected signature", True),
                ("say", "The test posted the event as parsed JSON. Stripe signs the raw bytes, so the body has to be sent exactly as signed."),
                T("Edit", {"file_path": "tests/conftest.py", "old_string": "json=", "new_string": "content="}, "The file was updated", patch=["-    return client.post(url, json=payload)", "+    return client.post(url, content=raw_body, headers=signed_headers)"]),
                T("Bash", {"command": "uv run pytest tests/test_webhooks.py -q"}, "4 passed in 0.91s"),
            ]),
            ("document how to test webhooks locally", [
                T("Edit", {"file_path": "docs/webhooks.md", "old_string": "", "new_string": "stripe listen"}, "The file was updated",
                  patch=["+## Testing locally", "+", "+    stripe listen --forward-to localhost:8000/webhooks/stripe", "+    stripe trigger invoice.paid"]),
                ("say", "Added a *Testing locally* section with the Stripe CLI commands."),
            ]),
            ("open a PR", [
                T("Bash", {"command": 'gh pr create --title "Stop double charges when Stripe retries invoice.paid" --fill'},
                  "https://github.com/acme/billing-api/pull/214"),
            ]),
        ],
        "analysis": {
            "title": "Stop double charges when Stripe retries invoice.paid",
            "summary": "Customers were charged twice because the Stripe webhook handler processed retried events again. The developer and agent added a processed_events table (Alembic migration) and an idempotency check on event.id inside the invoice transaction, added a replay test (which first failed on the webhook signature until the test sent the raw body), and documented local testing with the Stripe CLI.",
            "goal": "Find and fix duplicate charges on webhook retries", "outcome": "completed", "outcome_note": "Replay test passes; migration applied.",
            "work_types": ["bugfix", "testing", "docs"], "tags": ["stripe", "webhooks", "idempotency", "alembic"],
            "highlights": ["Root cause: no dedupe on Stripe event.id", "Replay test covers retries"],
            "open_threads": ["Backfill refunds for customers charged twice last week"],
            "knowledge": [
                ("fix", "Stripe retries webhooks: dedupe on event.id", "Stripe retries a webhook until it gets a 2xx within 10 s. The invoice.paid handler charged again on each retry. Record each event.id in processed_events in the same transaction as the side effect, and return 200 for repeats."),
                ("gotcha", "Stripe webhook signatures need the raw request body", "Webhook.construct_event verifies the signature over the exact bytes Stripe sent. Parsing to JSON first, or posting json= in tests, fails with SignatureVerificationError. Read `await request.body()` and pass that."),
                ("decision", "Idempotency in Postgres, not Redis", "processed_events lives in Postgres so the dedupe row commits atomically with the invoice update; a Redis key could be written while the transaction rolls back."),
                ("command", "Forward Stripe webhooks to the local API", "`stripe listen --forward-to localhost:8000/webhooks/stripe`, then `stripe trigger invoice.paid` to send a test event."),
            ],
        },
    },
    {
        "project": "storefront", "days": 0, "at": "-2h", "branch": "fix/checkout-hydration",
        "turns": [
            ("The checkout page logs a hydration mismatch in production and the delivery date flickers. What's going on?", [
                T("Read", {"file_path": "app/checkout/DeliveryEstimate.tsx"}, "export function DeliveryEstimate({ date }) {\n  return <p>Arrives {new Date(date).toLocaleDateString()}</p>\n}"),
                ("say", "`toLocaleDateString()` runs twice with different results: on the server in UTC with the server's locale, then in the browser with the visitor's timezone and locale. React sees different text and throws a hydration mismatch."),
                T("Edit", {"file_path": "app/checkout/DeliveryEstimate.tsx", "old_string": "toLocaleDateString()", "new_string": "formatDate(date, locale, tz)"}, "The file was updated",
                  patch=["-  return <p>Arrives {new Date(date).toLocaleDateString()}</p>", "+  const text = formatDate(date, { locale, timeZone: shopTimeZone })", "+  return <p>Arrives {text}</p>"]),
                T("Bash", {"command": "pnpm test -- DeliveryEstimate"}, " ✓ DeliveryEstimate renders the same text on server and client (12 ms)"),
            ]),
            ("add an e2e check for it", [
                T("Write", {"file_path": "e2e/checkout.spec.ts"}, "File created", patch=["+test('checkout has no hydration errors', async ({ page }) => {", "+  const errors: string[] = []", "+  page.on('console', m => m.type() === 'error' && errors.push(m.text()))", "+  await page.goto('/checkout')", "+  await expect(page.getByText('Arrives')).toBeVisible()", "+  expect(errors).toEqual([])", "+})"]),
                T("Bash", {"command": "pnpm exec playwright test e2e/checkout.spec.ts"}, "  1 passed (4.2s)"),
            ]),
        ],
        "analysis": {
            "title": "Fix the checkout hydration mismatch on delivery dates",
            "summary": "The checkout's delivery estimate rendered different text on the server and in the browser because toLocaleDateString used different time zones and locales, causing a React hydration mismatch. Dates are now formatted with an explicit locale and the shop's time zone, with a unit test and a Playwright check that fails on console errors.",
            "goal": "Remove the hydration error on checkout", "outcome": "completed", "outcome_note": "Unit and e2e tests pass.",
            "work_types": ["bugfix", "testing"], "tags": ["nextjs", "react", "hydration", "playwright"],
            "highlights": ["Explicit time zone for server and client"], "open_threads": [],
            "knowledge": [
                ("gotcha", "toLocaleDateString causes hydration mismatches", "In React Server Components the server formats in its own time zone and locale, the browser in the visitor's. Format with an explicit locale and time zone (the shop's) so both render the same text."),
                ("pattern", "Fail Playwright tests on console errors", "Collect `page.on('console')` errors in the test and assert the list is empty; it catches hydration mismatches that don't break the page visibly."),
            ],
        },
    },
    {
        "project": "infra", "days": 1, "at": "16:30", "branch": "ci/oidc-deploys",
        "turns": [
            ("Replace the AWS access keys in our GitHub Actions deploy with OIDC.", [
                T("Read", {"file_path": ".github/workflows/deploy.yml"}, "env:\n  AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}"),
                T("Write", {"file_path": "terraform/github_oidc.tf"}, "File created", patch=["+resource \"aws_iam_openid_connect_provider\" \"github\" {", "+  url = \"https://token.actions.githubusercontent.com\"", "+}", "+data \"aws_iam_policy_document\" \"deploy_trust\" {", "+  condition { test = \"StringEquals\", variable = \"token.actions.githubusercontent.com:sub\"", "+    values = [\"repo:acme/storefront:environment:production\"] }", "+}"]),
                T("Bash", {"command": "terraform plan -out plan.tfplan"}, "Plan: 3 to add, 0 to change, 0 to destroy."),
                T("Edit", {"file_path": ".github/workflows/deploy.yml", "old_string": "AWS_ACCESS_KEY_ID", "new_string": "role-to-assume"}, "The file was updated",
                  patch=["+permissions:", "+  id-token: write", "+  contents: read", "-      AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}", "+      - uses: aws-actions/configure-aws-credentials@v4", "+        with: { role-to-assume: ${{ vars.DEPLOY_ROLE_ARN }}, aws-region: eu-west-1 }"]),
                T("Bash", {"command": "gh workflow run deploy.yml --ref ci/oidc-deploys"}, "Not authorized to perform sts:AssumeRoleWithWebIdentity", True),
                ("say", "The job runs in the `production` environment, so the token's `sub` claim is `repo:acme/storefront:environment:production`, not the branch. The trust policy matched the branch; I've switched it to the environment and applied."),
                T("Bash", {"command": "terraform apply plan.tfplan && gh workflow run deploy.yml"}, "Apply complete! Resources: 3 added.\n✓ deploy completed in 3m12s"),
            ]),
        ],
        "analysis": {
            "title": "Deploy from GitHub Actions with OIDC instead of access keys",
            "summary": "Replaced long-lived AWS keys in the deploy workflow with GitHub OIDC: an IAM OIDC provider and deploy role in Terraform, and configure-aws-credentials in the workflow. The first run failed because the trust policy matched the branch while jobs in an environment carry the environment in the sub claim.",
            "goal": "Remove static AWS keys from CI", "outcome": "completed", "outcome_note": "Deploy runs with the assumed role; old keys can be deleted.",
            "work_types": ["infra", "security"], "tags": ["github-actions", "oidc", "terraform", "aws"],
            "highlights": ["No more AWS secrets in GitHub"], "open_threads": ["Delete the old IAM user's access keys"],
            "knowledge": [
                ("gotcha", "OIDC sub claim uses the environment, not the branch", "For jobs with `environment:`, the GitHub OIDC token's sub is `repo:ORG/REPO:environment:NAME`. A trust policy that matches `ref:refs/heads/...` fails with 'Not authorized to perform sts:AssumeRoleWithWebIdentity'."),
                ("fact", "Deploys assume DEPLOY_ROLE_ARN via GitHub OIDC", "The deploy workflow needs `permissions: id-token: write` and assumes the Terraform-managed role in vars.DEPLOY_ROLE_ARN; no AWS secrets are stored in GitHub."),
                ("decision", "One deploy role per environment", "Separate roles for staging and production keep a staging job from assuming production permissions; the trust policy pins the environment."),
            ],
        },
    },
    {
        "project": "recommender", "days": 2, "at": "11:15", "branch": "perf/ann-index",
        "turns": [
            ("The similar-items endpoint takes 4 seconds at p95. Can you make it fast?", [
                T("Bash", {"command": "psql $DATABASE_URL -c \"EXPLAIN ANALYZE SELECT id FROM items ORDER BY embedding <=> $1 LIMIT 20\""}, "Seq Scan on items  (cost=0.00..48211.00 rows=412000) (actual time=3812.4..3812.9 rows=20)"),
                ("say", "It's a sequential scan over 412k vectors: there's no approximate-nearest-neighbour index on `embedding`."),
                T("Write", {"file_path": "migrations/0042_hnsw_index.sql"}, "File created", patch=["+CREATE INDEX CONCURRENTLY items_embedding_hnsw", "+  ON items USING hnsw (embedding vector_cosine_ops)", "+  WITH (m = 16, ef_construction = 64);"]),
                T("Bash", {"command": "psql $DATABASE_URL -f migrations/0042_hnsw_index.sql && psql -c 'SET hnsw.ef_search = 80; EXPLAIN ANALYZE ...'"}, "Index Scan using items_embedding_hnsw on items (actual time=4.1..6.8 rows=20)"),
                ("say", "p95 is now about 7 ms. Recall@20 against the exact scan is 0.97 with `ef_search = 80`."),
            ]),
        ],
        "analysis": {
            "title": "Cut similar-items latency from 4 s to 7 ms with an HNSW index",
            "summary": "The similar-items query did a sequential scan over 412k pgvector embeddings. Adding an HNSW index with cosine ops (created concurrently) and ef_search = 80 brought p95 from about 4 s to 7 ms with recall@20 of 0.97.",
            "goal": "Make similar-items fast", "outcome": "completed", "outcome_note": "p95 7 ms.",
            "work_types": ["performance"], "tags": ["pgvector", "postgres", "hnsw"],
            "highlights": ["570x faster"], "open_threads": ["Tune ef_search per endpoint"],
            "knowledge": [
                ("fix", "pgvector similarity search needs an HNSW index", "Without an index, ORDER BY embedding <=> $1 scans every row. `CREATE INDEX CONCURRENTLY ... USING hnsw (embedding vector_cosine_ops)` turned a 4 s seq scan into a 7 ms index scan."),
                ("gotcha", "The index operator class must match the distance operator", "An HNSW index built with vector_l2_ops is ignored by queries that order by the cosine operator <=>. Use vector_cosine_ops for <=>."),
                ("fact", "Recommender uses hnsw.ef_search = 80", "At ef_search 80 recall@20 is 0.97 against the exact scan; raise it for batch jobs that need exact neighbours."),
            ],
        },
    },
    {
        "project": "billing-api", "days": 3, "at": "15:05", "branch": "fix/celery-duplicates",
        "turns": [
            ("Some Celery tasks run twice after every deploy. The dunning emails went out twice to a few customers.", [
                T("Read", {"file_path": "app/worker.py"}, "app = Celery('billing', broker=REDIS_URL)\napp.conf.task_acks_late = True"),
                ("say", "With `task_acks_late` and the Redis broker, a task that isn't acknowledged within `visibility_timeout` (1 hour by default) is redelivered. The deploy stops workers mid-task, so the task runs again on the new worker."),
                T("Edit", {"file_path": "app/worker.py", "old_string": "task_acks_late = True", "new_string": "visibility_timeout"}, "The file was updated",
                  patch=["+app.conf.broker_transport_options = {'visibility_timeout': 4 * 3600}", "+app.conf.worker_cancel_long_running_tasks_on_connection_loss = True"]),
                T("Edit", {"file_path": "app/dunning/tasks.py", "old_string": "send_email(", "new_string": "send_once("}, "The file was updated", patch=["-    send_email(customer, template)", "+    send_once(customer, template, key=f'dunning:{invoice.id}:{attempt}')"]),
                T("Bash", {"command": "uv run pytest tests/test_dunning.py -q"}, "6 passed in 1.12s"),
            ]),
        ],
        "analysis": {
            "title": "Stop duplicate dunning emails after deploys",
            "summary": "Late-acked Celery tasks on the Redis broker were redelivered when deploys stopped workers, so dunning emails went out twice. Raised the visibility timeout above the longest task and made the dunning email send idempotent with a per-invoice, per-attempt key.",
            "goal": "Prevent duplicate task runs", "outcome": "completed", "outcome_note": "Tests pass; watch the next deploy.",
            "work_types": ["bugfix"], "tags": ["celery", "redis", "dunning"],
            "highlights": ["Idempotent email sends"], "open_threads": ["Confirm no duplicates on next deploy"],
            "knowledge": [
                ("gotcha", "Celery on Redis redelivers late-acked tasks", "With task_acks_late, a task not acked within the Redis broker's visibility_timeout is redelivered, and deploys that stop workers mid-task trigger it. Keep visibility_timeout above the longest task and make side effects idempotent."),
                ("pattern", "Idempotency keys for outgoing emails", "Dunning emails send through send_once with a key like dunning:{invoice}:{attempt}; a repeat with the same key is skipped."),
            ],
        },
    },
    {
        "project": "storefront", "days": 5, "at": "10:20", "branch": "test/flaky-cart",
        "turns": [
            ("The cart badge Playwright test fails about one run in ten on CI. Make it reliable.", [
                T("Read", {"file_path": "e2e/cart.spec.ts"}, "await page.click('text=Add to cart')\nconst count = await page.textContent('.cart-badge')\nexpect(count).toBe('1')"),
                ("say", "`textContent` reads the badge once, right after the click, before the optimistic update renders. Web-first assertions retry until they pass or time out."),
                T("Edit", {"file_path": "e2e/cart.spec.ts", "old_string": "textContent", "new_string": "toHaveText"}, "The file was updated",
                  patch=["-const count = await page.textContent('.cart-badge')", "-expect(count).toBe('1')", "+await expect(page.getByTestId('cart-badge')).toHaveText('1')"]),
                T("Bash", {"command": "pnpm exec playwright test e2e/cart.spec.ts --repeat-each 30"}, "  30 passed (41.7s)"),
            ]),
        ],
        "analysis": {
            "title": "Fix the flaky cart badge Playwright test",
            "summary": "The cart badge test read textContent once instead of waiting, so it failed whenever the optimistic update was slow. Replaced it with a web-first toHaveText assertion on a test id; 30 repeated runs pass.",
            "goal": "Make the cart e2e test reliable", "outcome": "completed", "outcome_note": "30/30 runs pass.",
            "work_types": ["testing"], "tags": ["playwright", "flaky-tests"],
            "highlights": ["--repeat-each 30 to prove the fix"], "open_threads": [],
            "knowledge": [
                ("learning", "Use Playwright web-first assertions, not textContent", "expect(locator).toHaveText() retries until it passes; reading textContent and asserting on the value checks once and is flaky under load."),
                ("command", "Prove a flaky Playwright test is fixed", "`pnpm exec playwright test <file> --repeat-each 30` runs it 30 times in one go."),
            ],
        },
    },
]

# Smaller sessions: (project, days ago, time, title, first prompt, [files], test command, outcome, tags, [(kind, title, body)])
FILLERS = [
    ("billing-api", 1, "10:00", "Add proration preview endpoint", "Add an endpoint that previews the prorated amount when a customer changes plan mid-cycle.",
     ["app/billing/proration.py", "app/api/plans.py"], "uv run pytest tests/test_proration.py -q", "completed", ["stripe", "proration"],
     [("fact", "Proration previews use Stripe's upcoming invoice", "The preview endpoint calls Invoice.upcoming with subscription_proration_date so the amount matches what Stripe will charge."),
      ("decision", "Round proration in the customer's currency", "Amounts are rounded with the currency's minor units, never to two decimals, so JPY previews are exact.")]),
    ("storefront", 2, "15:40", "Validate checkout forms with Zod", "Move the checkout form validation to Zod so the server action and the client share one schema.",
     ["app/checkout/schema.ts", "app/checkout/actions.ts"], "pnpm test -- checkout", "completed", ["zod", "forms", "nextjs"],
     [("pattern", "Share one Zod schema between client and server action", "checkout/schema.ts exports the schema; the form uses it for inline errors and the server action parses with it again, since client validation can be bypassed.")]),
    ("infra", 3, "09:30", "Terraform state lock stuck after cancelled apply", "terraform plan says the state is locked by a run I cancelled an hour ago.",
     ["terraform/backend.tf"], "terraform plan", "completed", ["terraform", "dynamodb"],
     [("gotcha", "Cancelled Terraform runs can leave the DynamoDB state lock behind", "If an apply is killed, the lock row stays in the DynamoDB lock table. Check nobody is running, then `terraform force-unlock <LOCK_ID>`."),
      ("command", "Release a stale Terraform state lock", "`terraform force-unlock <LOCK_ID>` with the id from the lock error; only after confirming no apply is running.")]),
    ("recommender", 4, "13:00", "Nightly embedding refresh job", "Write the nightly job that re-embeds items whose description changed.",
     ["jobs/refresh_embeddings.py"], "uv run pytest tests/test_refresh.py -q", "completed", ["embeddings", "pgvector"],
     [("fact", "Embeddings are refreshed nightly for changed items only", "jobs/refresh_embeddings.py re-embeds rows whose content hash changed since the last run and writes them in batches of 500.")]),
    ("billing-api", 6, "11:20", "N+1 queries on the invoices list", "The invoices list endpoint got slow; the logs show hundreds of queries per request.",
     ["app/api/invoices.py", "app/models/invoice.py"], "uv run pytest tests/test_invoices.py -q", "completed", ["postgres", "sqlalchemy", "performance"],
     [("fix", "N+1 on invoices: eager-load line items", "The list endpoint loaded line_items lazily per invoice. selectinload(Invoice.line_items) turned 301 queries into 2.")]),
    ("storefront", 7, "16:10", "Image optimisation for product pages", "Product pages score badly on LCP. Look at the hero image.",
     ["app/products/[slug]/page.tsx"], "pnpm build", "completed", ["nextjs", "performance"],
     [("learning", "Mark the LCP image as priority in next/image", "The product hero image was lazy-loaded; `priority` on next/image preloads it and cut LCP from 3.9 s to 1.8 s.")]),
    ("infra", 8, "14:45", "CloudFront caching for static assets", "Static assets are served from the ALB every time. Put CloudFront in front with long caching.",
     ["terraform/cdn.tf"], "terraform plan", "completed", ["cloudfront", "terraform"],
     [("decision", "Hashed assets get a one-year CloudFront TTL", "Next.js build output under /_next/static is content-hashed, so it is cached immutable for a year; HTML is never cached at the edge.")]),
    ("billing-api", 9, "10:05", "Dunning schedule for failed payments", "Implement the retry schedule for failed renewals: 1, 3 and 7 days.",
     ["app/dunning/schedule.py", "app/dunning/tasks.py"], "uv run pytest tests/test_dunning.py -q", "completed", ["dunning", "celery"],
     [("fact", "Dunning retries on days 1, 3 and 7", "Failed renewals are retried by Celery beat on days 1, 3 and 7; after the third failure the subscription is marked past_due and the customer emailed.")]),
    ("dotfiles", 10, "20:30", "Speed up zsh startup", "My terminal takes almost two seconds to open. Profile zsh startup.",
     [".zshrc"], "zsh -i -c exit", "completed", ["zsh", "shell"],
     [("fix", "nvm made zsh start in 1.8 s", "Loading nvm eagerly cost 1.4 s. Replaced it with fnm (`eval \"$(fnm env)\"`); startup is now 0.2 s."),
      ("preference", "Use uv for every Python project", "New Python projects use uv (uv init, uv add, uv run); no bare pip or virtualenv.")]),
    ("recommender", 11, "09:50", "Evaluate embedding models", "Compare the current embedding model with two newer ones on our click data.",
     ["notebooks/eval_models.py"], "uv run python notebooks/eval_models.py", "partial", ["embeddings", "evaluation"],
     [("learning", "Offline recall tracks click-through only loosely", "The model with the best recall@20 on click pairs was third on the A/B test; use offline recall to shortlist, not to decide.")]),
    ("storefront", 12, "11:35", "Feature flag for the new search", "Put the new search page behind a feature flag so we can roll it out gradually.",
     ["lib/flags.ts", "app/search/page.tsx"], "pnpm test -- flags", "completed", ["feature-flags", "nextjs"],
     [("pattern", "Read feature flags on the server", "Flags are evaluated in server components and passed down as props, so the page never flashes the old UI before the client loads flags.")]),
    ("infra", 14, "10:10", "ECS service keeps restarting", "The billing worker service on ECS Fargate keeps restarting every few minutes.",
     ["terraform/ecs_billing.tf"], "aws ecs describe-services --cluster prod --services billing-worker", "completed", ["ecs", "fargate"],
     [("fix", "ECS health check killed the Celery worker", "The worker had an ALB-style HTTP health check but serves no HTTP. Replaced it with a container health check (`celery inspect ping`) and removed the target group.")]),
    ("billing-api", 16, "14:00", "Upgrade to SQLAlchemy 2.0 style", "Migrate the remaining queries to SQLAlchemy 2.0 select() style.",
     ["app/models/", "app/api/"], "uv run pytest -q", "completed", ["sqlalchemy", "refactor"],
     [("learning", "SQLAlchemy 2.0 needs .scalars() for ORM rows", "session.execute(select(Invoice)) returns Row tuples; use session.scalars(...) to get Invoice objects.")]),
    ("storefront", 18, "15:15", "Tailwind dark mode", "Add dark mode that follows the system setting.",
     ["tailwind.config.ts", "app/globals.css"], "pnpm build", "completed", ["tailwind", "dark-mode"],
     [("gotcha", "Tailwind darkMode 'media' ignores a manual toggle", "With darkMode: 'media' the dark: variants follow the OS only; a user toggle needs darkMode: 'class' and a class on <html>.")]),
    ("recommender", 20, "16:00", "Cold start for new items", "New items never get recommended because they have no interactions.",
     ["app/recommend.py"], "uv run pytest tests/test_recommend.py -q", "partial", ["cold-start", "embeddings"],
     [("decision", "Blend content similarity for items under 7 days old", "New items rank by embedding similarity alone until they have 7 days of interactions, then blend in collaborative scores.")]),
    ("infra", 22, "11:00", "Datadog APM for the billing API", "Set up Datadog tracing for billing-api on ECS.",
     ["terraform/datadog.tf", "billing-api/Dockerfile"], "terraform plan", "completed", ["datadog", "observability"],
     [("fact", "Datadog agent runs as an ECS sidecar", "Each billing-api task has a datadog-agent sidecar; ddtrace is enabled with DD_SERVICE=billing-api and DD_ENV from the task definition.")]),
    ("billing-api", 25, "09:45", "Sentry noise from 404s", "Sentry is full of NotFound errors from bots. Filter them.",
     ["app/observability.py"], "uv run pytest -q", "completed", ["sentry", "observability"],
     [("pattern", "Drop expected 4xx in Sentry's before_send", "before_send returns None for HTTPException with status < 500, so Sentry only receives real failures.")]),
    ("storefront", 28, "13:20", "Server actions timing out on Vercel", "The place-order server action times out on large carts.",
     ["app/checkout/actions.ts"], "pnpm test -- actions", "abandoned", ["nextjs", "performance"],
     [("todo", "Move order placement to a background job", "Large carts exceed the server action time limit; place the order via billing-api and poll for the result instead.")]),
    ("dotfiles", 31, "21:10", "Brewfile cleanup", "Clean up my Brewfile and remove things I don't use.",
     ["Brewfile"], "brew bundle check", "completed", ["homebrew"],
     [("command", "Find Homebrew packages not in the Brewfile", "`brew bundle cleanup --file=~/dotfiles/Brewfile` lists installed formulae the Brewfile doesn't mention; add --force to remove them.")]),
    ("billing-api", 34, "10:30", "PgBouncer connection errors", "We see 'too many connections' from Postgres at peak.",
     ["app/db.py", "infra/pgbouncer.ini"], "uv run pytest -q", "completed", ["postgres", "pgbouncer"],
     [("gotcha", "PgBouncer transaction mode breaks prepared statements", "asyncpg prepares statements by default; behind PgBouncer in transaction mode that fails with 'prepared statement does not exist'. Set statement_cache_size=0.")]),
    ("infra", 37, "15:50", "Blue/green deploys for storefront", "Can we do blue/green deploys for the storefront on ECS?",
     ["terraform/ecs_storefront.tf"], "terraform plan", "exploratory", ["ecs", "deploys"],
     [("reference", "ECS blue/green needs CodeDeploy", "ECS native rolling updates can't shift traffic gradually; blue/green uses a CodeDeploy deployment group with two ALB target groups.")]),
    ("recommender", 40, "11:10", "Set up the recommender repo", "Set up a new Python project for the recommender with FastAPI and pgvector.",
     ["pyproject.toml", "app/main.py"], "uv run pytest -q", "completed", ["setup", "fastapi", "pgvector"],
     [("fact", "Recommender runs on FastAPI with psycopg 3", "app/main.py serves /similar/{item_id}; pgvector's Python adapter is registered on each psycopg connection.")]),
    ("billing-api", 13, "10:40", "One active subscription per customer", "Two checkout tabs gave a customer two active subscriptions. Stop that in the database.",
     ["migrations/versions/2026_one_active_subscription.py"], "uv run pytest tests/test_subscriptions.py -q", "completed", ["postgres", "sql"],
     [("decision", "A partial unique index allows one active subscription", "`CREATE UNIQUE INDEX ON subscriptions (customer_id) WHERE status = 'active'` in Postgres: the second insert fails with a unique constraint violation inside its transaction, and the API answers 409.")]),
]

# Glossary: the terms a pass over each project's knowledge "finds"; `match` words decide which knowledge items cite a term.
TERMS = [
    ("Stripe", "platform", "Payment platform the billing API charges through; webhooks report invoice and payment events.", ["stripe"], ["Stripe", "Stripe CLI"]),
    ("Idempotency key", "concept", "A key that makes repeating an operation safe: the second request with the same key does nothing.", ["idempot", "dedupe", "send_once"], ["Stripe", "processed_events"]),
    ("processed_events", "data", "Postgres table of Stripe event ids already handled; written in the same transaction as the side effect.", ["processed_events"], ["Idempotency key"]),
    ("Webhook signature", "concept", "Stripe's HMAC over the raw request body, checked with Webhook.construct_event.", ["signature"], ["Stripe"]),
    ("Stripe CLI", "tool", "Command-line tool to forward and trigger webhooks locally.", ["stripe listen", "stripe trigger"], ["Stripe"]),
    ("Alembic", "tool", "Database migration tool for SQLAlchemy.", ["alembic"], ["SQLAlchemy", "Postgres"]),
    ("SQLAlchemy", "library", "Python ORM used by billing-api (2.0 style).", ["sqlalchemy", "selectinload", "scalars"], ["Alembic"]),
    ("Postgres", "system", "Primary database for billing-api and the recommender.", ["postgres", "psql"], ["pgvector", "PgBouncer"]),
    ("PgBouncer", "tool", "Connection pooler in front of Postgres; runs in transaction mode.", ["pgbouncer"], ["Postgres", "asyncpg"]),
    ("asyncpg", "library", "Async Postgres driver; needs statement_cache_size=0 behind PgBouncer.", ["asyncpg"], ["PgBouncer"]),
    ("Celery", "library", "Task queue for billing jobs such as dunning, with Redis as broker.", ["celery"], ["Redis", "Dunning"]),
    ("Redis", "system", "Broker for Celery tasks.", ["redis"], ["Celery"]),
    ("visibility_timeout", "concept", "How long the Redis broker waits for an ack before redelivering a Celery task.", ["visibility_timeout"], ["Celery"]),
    ("Dunning", "domain", "Retrying failed renewal payments and emailing the customer (days 1, 3, 7).", ["dunning"], ["Celery"]),
    ("Proration", "domain", "Charging the difference when a plan changes mid-cycle.", ["prorat"], ["Stripe"]),
    ("Next.js", "library", "React framework of the storefront.", ["next.js", "next/image", "_next", "server action"], ["React Server Components"]),
    ("React Server Components", "concept", "Components rendered on the server; their output must match the client's first render.", ["server component", "hydration"], ["Next.js", "Hydration mismatch"]),
    ("Hydration mismatch", "concept", "React error when server-rendered HTML differs from the first client render.", ["hydration"], ["React Server Components"]),
    ("Playwright", "tool", "End-to-end browser tests for the storefront.", ["playwright"], ["Web-first assertion"]),
    ("Web-first assertion", "concept", "A Playwright assertion that retries until it passes, such as toHaveText.", ["tohavetext", "web-first"], ["Playwright"]),
    ("Zod", "library", "Schema validation shared by forms and server actions.", ["zod"], ["Next.js"]),
    ("Tailwind CSS", "library", "Utility CSS used by the storefront.", ["tailwind"], ["Next.js"]),
    ("Feature flag", "concept", "A switch that turns a feature on per user or percentage, evaluated on the server.", ["feature flag", "flags"], ["Next.js"]),
    ("LCP", "concept", "Largest Contentful Paint, the page-speed metric for the main image or text.", ["lcp"], ["Next.js"]),
    ("Terraform", "tool", "Infrastructure as code for the AWS stack.", ["terraform"], ["DynamoDB lock table"]),
    ("DynamoDB lock table", "data", "Table that holds Terraform's state lock.", ["dynamodb", "state lock", "lock row"], ["Terraform"]),
    ("ECS Fargate", "platform", "Serverless containers running billing-api, workers and the storefront.", ["ecs", "fargate"], ["ALB"]),
    ("ALB", "platform", "Application Load Balancer in front of the ECS services.", ["alb", "target group"], ["ECS Fargate"]),
    ("CloudFront", "platform", "CDN caching the storefront's static assets.", ["cloudfront"], ["Next.js"]),
    ("GitHub Actions", "platform", "CI and deploys.", ["github", "workflow"], ["OIDC"]),
    ("OIDC", "concept", "Short-lived credentials GitHub Actions exchanges for an AWS role; no stored keys.", ["oidc", "assumerolewithwebidentity"], ["GitHub Actions"]),
    ("Datadog", "platform", "APM and metrics; the agent runs as an ECS sidecar.", ["datadog", "ddtrace"], ["ECS Fargate"]),
    ("Sentry", "platform", "Error tracking for billing-api.", ["sentry"], ["Datadog"]),
    ("pgvector", "library", "Postgres extension for vector similarity search.", ["pgvector", "vector_cosine_ops", "<=>"], ["HNSW", "Postgres"]),
    ("HNSW", "concept", "Approximate-nearest-neighbour index type in pgvector; tuned with ef_search.", ["hnsw", "ef_search"], ["pgvector"]),
    ("Embedding", "concept", "Vector representation of an item's description used for similarity.", ["embedding", "re-embed"], ["pgvector"]),
    ("Cold start", "concept", "New items with no interactions yet.", ["cold start", "new items"], ["Embedding"]),
    ("uv", "tool", "Python package and project manager used everywhere.", ["uv run", "uv init", "uv "], []),
    ("fnm", "tool", "Fast Node version manager that replaced nvm.", ["fnm", "nvm"], []),
    ("Homebrew", "tool", "Package manager for this Mac, managed from a Brewfile.", ["brew"], []),
]

WEEKS_REVIEWED = 3

# A project group, analyzed: the stand-in keeps the glossary links named here, with this relation (read from the
# first term), and drops the rest, as the analyzer drops terms that were only discussed together.
GROUP = ("Shop", ["billing-api", "storefront", "infra", "recommender"])
RELATIONS = {
    ("Terraform", "DynamoDB lock table"): "locks state in", ("Celery", "Dunning"): "runs", ("Celery", "Redis"): "uses as broker",
    ("visibility_timeout", "Celery"): "redelivers tasks of", ("ALB", "ECS Fargate"): "routes to", ("pgvector", "Embedding"): "stores",
    ("Stripe CLI", "Stripe"): "forwards webhooks of", ("Proration", "Stripe"): "billed through", ("PgBouncer", "Postgres"): "pools connections to",
    ("pgvector", "Postgres"): "extension of", ("Playwright", "Web-first assertion"): "retries with", ("Idempotency key", "Stripe"): "dedupes retries of",
    ("Webhook signature", "Stripe"): "signed by", ("asyncpg", "PgBouncer"): "connects through", ("CloudFront", "Next.js"): "caches assets of",
    ("Zod", "Next.js"): "validates forms in", ("Feature flag", "Next.js"): "evaluated in", ("Datadog", "ECS Fargate"): "sidecar on",
    ("GitHub Actions", "OIDC"): "deploys with", ("HNSW", "pgvector"): "index type in", ("processed_events", "Idempotency key"): "stores",
    ("React Server Components", "Next.js"): "part of", ("Hydration mismatch", "React Server Components"): "error in",
}

# Innerlatch (game mode): a hero on the Backend engineer path, the demo's strongest class. It has studied the lessons
# in STUDIED (by title: understood on these days ago, newest last), passed the SQL gate twice, and set a job goal.
HERO = {"name": "Alex", "look": {"body": 1, "skin": 2, "hair": 3, "hair_color": 1, "outfit": 4, "accessory": 2}, "path": "backend"}
STUDIED = {  # the five SQL lessons open its gate
    "Stripe webhook signatures need the raw request body": [11, 10], "Idempotency in Postgres, not Redis": [10, 9],
    "PgBouncer transaction mode breaks prepared statements": [9, 8], "A partial unique index allows one active subscription": [8],
    "pgvector similarity search needs an HNSW index": [8], "The index operator class must match the distance operator": [7],
    "Stripe retries webhooks: dedupe on event.id": [5], "Forward Stripe webhooks to the local API": [4],
    "Fail Playwright tests on console errors": [3],
}
MISSED = ["Use Playwright web-first assertions, not textContent"]  # answered wrong: back on the field
GATES = [  # (module, days ago, explanation); rank 2 links the webhook PR
    ("M-sql", 6, "A query plan shows how Postgres reads the tables: an N+1 shows up as one plan run per row, so the "
                 "fix is one query with a join or selectinload. Indexes help reads and slow writes, and a unique index is "
                 "the only check that holds under concurrent inserts."),
    ("M-sql", 2, "The PR records each Stripe event id in processed_events, in the same transaction as the invoice "
                 "update. A retried webhook hits the primary key and returns 200 without charging. I wrote the replay "
                 "test that sends the same event twice and checked it fails without the table."),
]
JOB = {
    "title": "Backend Engineer, Payments", "company": "Northwind Commerce",
    "text": "Backend Engineer, Payments\nNorthwind Commerce is looking for a backend engineer to own our payments and "
            "subscription services.\n\nWhat you'll do\n- Design and run REST APIs in Python that other teams build on.\n"
            "- Model data in Postgres and write the SQL behind billing and reporting.\n- Build reliable systems: "
            "retries, idempotent jobs and queues that recover cleanly.\n- Keep quality high with automated tests and "
            "code review.\n\nNice to have\n- Experience with OAuth and service-to-service authentication.\n- "
            "Infrastructure as code with Terraform.\n\nRequirements\n3+ years building production backend services. "
            "Fluent English.",
    "mapping": {"title": "Backend Engineer, Payments", "requirements": [
        {"module": "M-sql", "rank": 2, "must": True, "quote": "Model data in Postgres and write the SQL behind billing and reporting."},
        {"module": "M-g-webapi", "rank": 2, "must": True, "quote": "Design and run REST APIs in Python that other teams build on."},
        {"module": "M-python", "rank": 2, "must": True, "quote": "REST APIs in Python"},
        {"module": "M-systems", "rank": 2, "must": True, "quote": "retries, idempotent jobs and queues that recover cleanly"},
        {"module": "M-g-quality", "rank": 2, "must": True, "quote": "Keep quality high with automated tests and code review."},
        {"module": "M-g-identity", "rank": 1, "must": False, "quote": "Experience with OAuth and service-to-service authentication."},
        {"module": "M-g-iac", "rank": 1, "must": False, "quote": "Infrastructure as code with Terraform."}],
        "outside": [{"text": "3+ years of backend work", "quote": "3+ years building production backend services."},
                    {"text": "Fluent English", "quote": "Fluent English."}]},
}


# ---------------------------------------------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------------------------------------------


def _usage(out: int, read: int, write: int) -> dict:
    return {"input_tokens": 6, "output_tokens": out, "cache_read_input_tokens": read, "cache_creation_input_tokens": write,
            "cache_creation": {"ephemeral_5m_input_tokens": write, "ephemeral_1h_input_tokens": 0}}


def filler_turns(f) -> list:
    project, _, _, title, prompt, files, test_cmd, outcome, tags, knowledge = f
    steps = [("say", "Let me look at the relevant code first.")]
    steps += [T("Read", {"file_path": fp}, f"# {fp}\n...") for fp in files]
    if len(files) > 1:
        steps.append(T("Grep", {"pattern": tags[0], "path": "."}, f"{files[0]}:12\n{files[-1]}:40"))
    fail = outcome in ("partial", "abandoned")
    steps.append(T("Bash", {"command": test_cmd}, "1 failed" if fail else "all checks passed", fail))
    steps.append(("say", knowledge[0][2]))
    steps.append(T("Edit", {"file_path": files[0], "old_string": "", "new_string": "…"}, "The file was updated",
                   patch=[f"+# {knowledge[0][1]}", "+...", "+...", "-..."]))
    steps.append(T("Bash", {"command": test_cmd}, "1 failed" if fail else "all checks passed", fail))
    turns = [(prompt, steps)]
    if len(knowledge) > 1:
        turns.append(("also: " + knowledge[1][1][0].lower() + knowledge[1][1][1:], [
            ("say", knowledge[1][2]),
            T("Edit", {"file_path": files[-1], "old_string": "", "new_string": "…"}, "The file was updated", patch=["+...", "+..."]),
        ]))
    return turns


def write_transcript(claude_dir: Path, project: str, sid: str, start: datetime, branch: str, turns: list, rng: random.Random) -> None:
    cwd = f"{ROOT}/{project}"
    folder = claude_dir / "projects" / cwd.replace("/", "-")
    folder.mkdir(parents=True, exist_ok=True)
    t, n, cache = start, 0, rng.randint(40000, 90000)
    lines: list[str] = []

    def line(**kw):
        base = {"isSidechain": False, "userType": "external", "entrypoint": "cli", "cwd": cwd, "sessionId": sid,
                "version": "2.1.290", "gitBranch": branch, "timestamp": t.isoformat(timespec="milliseconds").replace("+00:00", "Z")}
        base.update(kw)
        lines.append(json.dumps(base))

    def assistant(block):
        nonlocal n, cache
        n += 1
        write = rng.randint(2000, 9000)
        line(type="assistant", uuid=f"a{n}", requestId=f"req{n}",
             message={"id": f"msg_{sid[:8]}_{n}", "model": "claude-opus-5-5", "role": "assistant", "content": [block],
                      "usage": _usage(rng.randint(200, 1800), cache, write)})
        cache += write

    for prompt, steps in turns:
        line(type="user", uuid=f"u{n}", origin={"kind": "human"}, message={"role": "user", "content": [{"type": "text", "text": prompt}]})
        for step in steps:
            t += timedelta(seconds=rng.randint(50, 260))
            if step[0] == "say":
                assistant({"type": "text", "text": step[1]})
                continue
            _, name, inp, result, err, patch = step
            tid = f"toolu_{sid[:6]}_{n}"
            assistant({"type": "tool_use", "id": tid, "name": name, "input": inp})
            t += timedelta(seconds=rng.randint(2, 40))
            extra = {}
            if patch:
                extra["toolUseResult"] = {"filePath": inp.get("file_path", ""), "structuredPatch": [{"lines": patch}]}
            line(type="user", uuid=f"r{n}", message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": bool(err)}]}, **extra)
        t += timedelta(seconds=rng.randint(240, 780))
    (folder / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------------------------------------------
# The stand-in `claude`: answers from the scenario above, never calls a model
# ---------------------------------------------------------------------------------------------------------------

FAKE_CLAUDE = r'''#!{python}
import json, re, sys
SCENARIO = json.load(open({scenario!r}))
args = sys.argv[1:]
prompt = sys.stdin.read()
system = args[args.index("--system-prompt") + 1] if "--system-prompt" in args else ""

def items():
    m = re.search(r"<knowledge_items>\n(.*?)\n</knowledge_items>", prompt, re.S)
    out = []
    for ln in (m.group(1).splitlines() if m else []):
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out

if "into themes" in system:
    data = {"themes": []}
elif "links between terms" in system:  # a group's Mindmap: keep the links RELATIONS names
    terms = {t["id"]: t["term"] for t in map(json.loads, re.search(r"<terms>\n(.*?)\n</terms>", prompt, re.S).group(1).splitlines())}
    links = []
    for x in map(json.loads, re.search(r"<links>\n(.*?)\n</links>", prompt, re.S).group(1).splitlines()):
        a, b = x["a"], x["b"]
        label = SCENARIO["relations"].get(terms[a] + "|" + terms[b])
        if label is None and terms[b] + "|" + terms[a] in SCENARIO["relations"]:
            a, b, label = b, a, SCENARIO["relations"][terms[b] + "|" + terms[a]]
        links.append({"id": x["id"], "keep": label is not None, "from": a, "label": label or ""})
    data = {"links": links}
elif "map a job description" in system:
    data = SCENARIO["job"]["mapping"]
elif "examiner at one gate" in system:
    data = {"verdict": "passed", "met": ["Explains why it works and where it stops working"], "missing": [],
            "feedback": "Clear and in your own words. Next, show what came of the work."}
elif "glossary" in system:
    terms = []
    for term, cat, definition, match, related in SCENARIO["terms"]:
        hits = [k for k in items() if any(w in (k["title"] + " " + (k.get("body") or "")).lower() for w in match)]
        if hits:
            terms.append({"term": term, "aliases": [], "category": cat, "definition": definition,
                          "context": hits[0]["title"], "related": related, "sources": [k["id"] for k in hits[:6]]})
    data = {"terms": terms}
elif "weekly engineering review" in system:
    data = {"headline": "Payments got safer and the storefront faster",
            "tldr": ["Webhook retries no longer double-charge", "CI deploys use short-lived OIDC credentials", "Refunds for past double charges still pending"],
            "summary": "Most of the week went into billing reliability: webhook retries and duplicate task runs both came down to missing idempotency. The storefront shed a hydration error and a flaky test, and CI deploys moved to short-lived credentials.",
            "themes": [{"title": "Idempotency everywhere", "detail": "Stripe webhooks, Celery tasks and emails all needed a dedupe key.", "projects": ["billing-api"]},
                       {"title": "Test reliability", "detail": "Web-first assertions and console-error checks in Playwright.", "projects": ["storefront"]}],
            "accomplishments": ["Stopped double charges on webhook retries", "Removed static AWS keys from CI", "Similar-items p95 from 4 s to 7 ms"],
            "learnings": ["Stripe signs the raw body", "OIDC sub claims name the environment"],
            "open_threads": ["Refund customers charged twice", "Delete the old IAM access keys"],
            "friction": ["Two fixes needed a second attempt after a failing test"],
            "suggestions": ["Add a CLAUDE.md note: every external side effect needs an idempotency key"]}
elif "knowledge base" in system or "playbook" in system:
    ks = items()
    groups = [("Gotchas & fixes", ("gotcha", "fix")), ("Decisions", ("decision",)), ("How things work", ("fact", "learning", "pattern")),
              ("Commands", ("command",)), ("Open items", ("todo", "reference", "preference"))]
    project = next((k.get("project") for k in ks if k.get("project")), None)
    overview = SCENARIO["projects"].get(project) or "Habits and rules that hold across projects."
    data = {"overview": overview, "superseded_ids": [],
            "tldr": [k["title"] for k in ks[:3]],
            "sections": [{"title": title, "items": [{"title": k["title"], "text": (k.get("body") or "").split(". ")[0].rstrip(".") + ".", "sources": [k["id"]]}
                                                    for k in ks if k["kind"] in kinds]} for title, kinds in groups]}
    data["sections"] = [s for s in data["sections"] if s["items"]]
else:
    for s in SCENARIO["sessions"]:
        if s["key"] in prompt:
            a = s["analysis"]
            break
    else:
        a = {"title": "Session", "summary": "", "outcome": "unclear", "knowledge": []}
    data = dict(a)
    data["knowledge"] = [{"kind": k, "title": t, "body": b, "tags": a.get("tags", [])[:2], "scope": "global" if k == "preference" else "project",
                          "confidence": "high", "evidence": "tests and output in the session"} for k, t, b in a.get("knowledge", [])]
    data.setdefault("friction", [])
    data.setdefault("sentiment", "positive")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(data),
                  "total_cost_usd": 0.0, "duration_ms": 5, "usage": {"input_tokens": 0, "output_tokens": 0}}))
'''


def main() -> None:
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(paths[0] if paths else "/tmp/interlatch-demo").resolve()
    if out.exists():
        shutil.rmtree(out)
    claude_dir, home, bindir = out / "claude", out / "home", out / "bin"
    for d in (claude_dir, home, bindir):
        d.mkdir(parents=True)
    rng = random.Random(7)
    now = datetime.now(timezone.utc).astimezone()
    sessions = []

    def start_of(days: int, at: str) -> datetime:
        if at.startswith("-"):  # today's sessions: hours ago, so they have ended and count as Today
            return (now - timedelta(hours=float(at[1:-1]))).astimezone(timezone.utc)
        hh, mm = map(int, at.split(":"))
        return (now - timedelta(days=days)).replace(hour=hh, minute=mm, second=0, microsecond=0).astimezone(timezone.utc)

    for r in RICH:
        sid = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        write_transcript(claude_dir, r["project"], sid, start_of(r["days"], r["at"]), r["branch"], r["turns"], rng)
        sessions.append({"key": r["turns"][0][0], "analysis": r["analysis"]})
    for f in FILLERS:
        project, days, at, title, prompt, files, test_cmd, outcome, tags, knowledge = f
        sid = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        write_transcript(claude_dir, project, sid, start_of(days, at), "main", filler_turns(f), rng)
        sessions.append({"key": prompt, "analysis": {
            "title": title, "summary": f"{prompt} {knowledge[0][2]}", "goal": title, "outcome": outcome,
            "outcome_note": "", "work_types": ["feature" if outcome == "completed" else "debugging"], "tags": tags,
            "highlights": [knowledge[0][1]], "open_threads": [], "knowledge": knowledge}})

    scenario = out / "scenario.json"
    scenario.write_text(json.dumps({"sessions": sessions, "terms": TERMS, "projects": PROJECTS, "job": JOB,
                                    "relations": {f"{a}|{b}": label for (a, b), label in RELATIONS.items()}}))
    fake = bindir / "claude"
    fake.write_text(FAKE_CLAUDE.replace("{python}", sys.executable).replace("{scenario!r}", repr(str(scenario))))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (home / "config.toml").write_text(
        f'[sources]\nclaude_dirs = ["{claude_dir}"]\ncodex_dirs = []\ncopilot_dirs = []\nbob_dirs = []\n\n'
        f'[analysis]\nauto = true\nclaude_bin = "{fake}"\nidle_minutes = 0\nmax_per_run = 100\nconcurrency = 4\n\n'
        f'[synthesis]\nmin_new_items = 1\n\n[game]\nenabled = true\n')

    # both names: INTERLATCH_HOME wins over CHRONICLE_HOME, so a home set in your shell under either can't leak in
    env = {**os.environ, "INTERLATCH_HOME": str(home), "CHRONICLE_HOME": str(home), "COPILOT_HOME": str(out / "none"),
           "BOB_HOME": str(out / "none")}
    for name in ("INTERLATCH_CLAUDE_DIRS", "CHRONICLE_CLAUDE_DIRS"):
        env.pop(name, None)

    def chronicle(*args: str) -> None:
        subprocess.run([sys.executable, "-m", "chronicle", *args], env=env, check=True)

    chronicle("sync", "--work")
    chronicle("glossary", "--rebuild", "--all")
    for w in range(1, WEEKS_REVIEWED + 1):
        week = (now - timedelta(weeks=w)).strftime("%G-W%V")
        chronicle("review", week)
    subprocess.run([sys.executable, __file__, "--group-and-hero", str(home)], env=env, check=True)
    if "--hub" in sys.argv:
        subprocess.run([sys.executable, __file__, "--team-hub", str(home)], env=env, check=True)
    print(f"\nDemo ready. Open it with:\n  INTERLATCH_HOME={home} uv run python -m interlatch ui --port 8898 --open")


def group_and_hero(home: Path) -> None:
    """The Shop group with its Mindmap analyzed, and Innerlatch's hero: a fake Google sign-in kept off the
    leaderboard, the path, the lessons studied, the SQL gate passed and the job goal. Run in the demo's environment;
    the stand-in `claude` answers the analysis, the gates and the job mapping."""
    from interlatch.core.config import load_config
    from interlatch.core.machine import local_machine_id
    from interlatch.core.util import utcnow
    from interlatch.knowledge import gates, group_graph, hero, jobs, party, study
    from interlatch.projects import groups
    from interlatch.sharing import leaderboard
    from interlatch.storage.kv import kv_set
    from interlatch.storage.sqlite import connect

    cfg = load_config(home)
    conn = connect(cfg.db_path)
    paths = [f"{ROOT}/{p}" for p in GROUP[1]]
    group_graph.analyze(cfg, conn, groups.save(conn, None, GROUP[0], [], paths), paths)

    leaderboard.write_state(cfg, {"uid": "demo", "refresh_token": "demo", "name": HERO["name"], "board": False})
    hero.save(conn, None, HERO["name"], HERO["look"])
    party.set_goal(conn, None, HERO["path"])
    today, here, cat = utcnow().date(), local_machine_id(cfg), party.load_catalog()
    ids = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM knowledge")}
    day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    log = [{"id": ids[t], "on": day(n), "verdict": "got_it"} for t, ago in STUDIED.items() for n in ago]
    log += [{"id": ids[t], "on": day(3), "verdict": "not_yet"} for t in MISSED]
    kv_set(conn, study.STUDY_KEY + party.scope_key(None), json.dumps(sorted(log, key=lambda x: x["on"])))
    conn.commit()
    prs = [w["id"] for w in gates.candidates(conn, here) if w["kind"] == "pr"]
    for rank, (module, ago, text) in enumerate(GATES, 1):
        gates.attempt(conn, cfg, cat, here, module, prs[:gates.RANK_ASKS[rank]["artifacts"]], text,
                      ready=gates.readiness(conn, cat, here).get(module, 0), here=True, today=today - timedelta(days=ago))
    jobs.add(conn, cfg, cat, None, title=JOB["title"], company=JOB["company"], text=JOB["text"])


TEAM = (("Yuma", "yuma@example.com", ["billing-api", "storefront"]), ("Ueno", "ueno@example.com", None))
SHARED = ("billing-api", "storefront", "infra")  # the projects whose sessions the team splits


def team_hub(home: Path) -> None:
    """The demo computer as a team hub with people (--hub): Ada, an admin, works at the hub; Yuma (limited to two
    projects) and Ueno send from their own computers. The sessions of SHARED are split between the three, a lesson of
    each project is stated again in a teammate's session, and one is replaced by a newer one, so a project's page has
    whose-sessions-taught-what, side-by-side cases and "since your last visit" to show. Prints a sign-in link each."""
    from interlatch.core.config import load_config, set_config_value
    from interlatch.core.machine import local_machine_id
    from interlatch.core.util import to_iso, utcnow
    from interlatch.projects.team import VISIT_KV
    from interlatch.sharing import people
    from interlatch.sharing.identity import new_token
    from interlatch.storage.kv import kv_set
    from interlatch.storage.sqlite import connect

    cfg = load_config(home)
    new_token(cfg)
    set_config_value(cfg, "hub", "name", '"Acme team"')
    conn = connect(cfg.db_path)
    here = local_machine_id(cfg)
    ada = people.add(conn, "Ada Lovelace", "ada@example.com", "admin")
    conn.execute("UPDATE machines SET person_id = ? WHERE id = ?", (ada["id"], here))
    links, machines = {"Ada Lovelace": people.invite(conn, ada["id"])}, [here]
    for i, (name, email, projects) in enumerate(TEAM):
        person = people.add(conn, name, email, "member", by=ada, projects=[f"{ROOT}/{p}" for p in projects] if projects else None)
        mid = str(uuid.UUID(int=0xD3E0 + i, version=4))
        conn.execute("INSERT INTO machines(id, name, role, first_seen, last_seen) VALUES (?, ?, 'spoke', ?, ?)",
                     (mid, f"{name.lower()}-laptop", to_iso(utcnow()), to_iso(utcnow())))
        people.join_computer(conn, people.invite(conn, person["id"], machine=mid), mid, f"{name.lower()}-laptop")
        links[name] = people.invite(conn, person["id"])
        machines.append(mid)
    rng = random.Random(11)
    for project in SHARED:
        sids = [r[0] for r in conn.execute("SELECT id FROM sessions WHERE project_path = ? ORDER BY started_at", (f"{ROOT}/{project}",))]
        owner = {sid: machines[(i + rng.randrange(2)) % len(machines)] for i, sid in enumerate(sids)}
        for sid, mid in owner.items():
            conn.execute("UPDATE sessions SET machine_id = ? WHERE id = ?", (mid, sid))
        lesson = conn.execute("SELECT id, session_id, confirmed_json FROM knowledge WHERE project_path = ? AND status = 'active' "
                              "AND kind IN ('gotcha', 'fix') ORDER BY id LIMIT 1", (f"{ROOT}/{project}",)).fetchone()
        other = next((sid for sid in sids if lesson and owner.get(sid) != owner.get(lesson["session_id"])), None)
        if other:  # a teammate hit the same thing in a session of their own
            confirmed = json.loads(lesson["confirmed_json"] or "[]") or [lesson["session_id"]]
            conn.execute("UPDATE knowledge SET confirmed_json = ?, stage = 'established' WHERE id = ?",
                         (json.dumps([*confirmed, other]), lesson["id"]))
    newest, older = conn.execute("SELECT id FROM knowledge WHERE project_path = ? AND status = 'active' ORDER BY id DESC LIMIT 2",
                                 (f"{ROOT}/billing-api",)).fetchall()
    conn.execute("UPDATE knowledge SET status = 'superseded', superseded_by = ?, superseded_reason = 'outdated', superseded_at = ? "
                 "WHERE id = ?", (newest[0], to_iso(utcnow() - timedelta(hours=20)), older[0]))
    kid = conn.execute("SELECT MAX(id) FROM knowledge").fetchone()[0]
    for (pid,) in conn.execute("SELECT id FROM people").fetchall():  # each last looked two days ago, before the newest lessons
        for project in PROJECTS:
            kv_set(conn, f"{VISIT_KV}p:{pid}:{ROOT}/{project}",
                   json.dumps({"seen": to_iso(utcnow() - timedelta(days=2)), "kid": kid - 4, "prev": None, "prev_kid": None}))
    conn.commit()
    print("\nTeam hub ready. Sign in as (open on the hub's address):")
    for name, code in links.items():
        print(f"  {name}: /signin?code={code}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--group-and-hero"]:
        group_and_hero(Path(sys.argv[2]))
    elif sys.argv[1:2] == ["--team-hub"]:
        team_hub(Path(sys.argv[2]))
    else:
        main()
