# Frugal

**The Intelligent Financial Decision Platform**

Personal finance tools are retrospective. They categorise transactions and render charts of money
already spent — a bookkeeping answer to a bookkeeping question. They leave the user alone with the
question that actually matters: **what should I do next?**

Frugal continuously models a user's financial state and uses it to produce **explained
recommendations**. Its defining property is not the feature list — it is that every score, verdict, and
forecast can be decomposed into the inputs that produced it. A recommendation you cannot interrogate is
one you cannot act on with confidence.

> **Status:** Milestones 0–4 complete and verified. Next: Milestone 5 — transaction categorisation (TF-IDF + logistic regression).

---

## The core idea

Ask *"should I buy this ₹1,34,900 laptop?"* and Frugal answers with a verdict, a date, and its
reasoning:

```
⏸  WAIT                                      Affordability 48/100
    You could afford this by 15 November 2026.        Confidence 77%

    Emergency fund after purchase   0.8 months    −24.0   weight 0.30
      Drops from 3.2 → 0.8 months, below the 3-month floor.
    Forecast trough                 ₹11,200       −8.4    weight 0.20
      Little margin for a surprise expense.
    Savings rate                    51.4%         +13.5   weight 0.15
      Strong savings mean you rebuild fast — which is why this is
      WAIT, not NOT RECOMMENDED.
    Debt-to-income                  18%           +11.2   weight 0.15
    Goal delay                      214 days      −9.6    weight 0.20

    ⓘ Based on 142 days of history. Seasonal spending not yet modelled.
```

Weights sum to 1.00. Contributions sum to the score. This is asserted by a test, because a rubric whose
parts don't reconstruct the whole is decoration, not explanation.

---

## Documentation

Read in order for the full picture; each document links to the next.

| Document | Contents |
|---|---|
| [01 — Software Requirements Specification](docs/01-srs.md) | Problem, personas, functional requirements (FR-1…FR-9), NFRs, constraints, acceptance criteria |
| [02 — Architecture](docs/02-architecture.md) | C4 context and containers, module structure, key flows, deployment, security, rejected alternatives |
| [03 — Data model](docs/03-data-model.md) | ER diagram, every table, index rationale, constraints, tenancy |
| [04 — API design](docs/04-api-design.md) | Endpoint catalogue, error envelope, pagination, idempotency, example payloads |
| [05 — UI design & wireframes](docs/05-ui-wireframes.md) | Design system, validated chart palette, screen wireframes, accessibility |
| [06 — Project structure](docs/06-project-structure.md) | Backend and frontend folder layouts, boundary enforcement, configuration |
| [07 — Roadmap](docs/07-roadmap.md) | Twelve milestones with exit criteria, dependency graph, risk register |
| [08 — Eval baselines](docs/08-eval-baselines.md) | What the AI modules actually score, and where they are weak |
| [09 — Deployment](docs/09-deployment.md) | First deployment end to end: Render · EC2 · Neon · S3, with the four checks that only fail in production |
| [ADRs](docs/adr/) | Seven binding decisions, with their costs |

---

## Architecture at a glance

```
Render (Next.js 16)  →  EC2 t3.micro : Caddy · FastAPI · Celery worker + beat · Redis
   frontend, and a                ├── Neon Postgres   (managed, free)
   same-origin proxy              ├── S3              (receipts, private + presigned)
   for /api                       └── CloudWatch      (logs · metrics · alarms)
```

A **modular monolith** — one deployable, with hard internal boundaries enforced by `import-linter` in
CI so any module can later be extracted without a rewrite. Runs at **$0/month** on free tiers.

Postgres is managed rather than co-hosted, because it is the one dependency whose loss is not
recoverable and whose footprint the 1 GB instance cannot absorb. Redis is co-hosted, capped, and
unpersisted: it holds only regenerable state, and a managed free tier metered by command count is
exhausted by an idle Celery worker's queue polling inside a week.

The frontend proxies `/api` rather than the browser calling the API across hosts. The refresh cookie
is `SameSite=Lax`, and the two are on different sites — so a direct call would carry no cookie and
every session would expire at fifteen minutes. See [09 — Deployment](docs/09-deployment.md).

---

## Tech stack

**Frontend** — Next.js 16 · React 19 · TypeScript · Tailwind v4 · shadcn/ui · TanStack Query ·
React Hook Form + Zod · Recharts · Playwright

**Backend** — FastAPI · Python 3.11 · SQLAlchemy 2 (async) · Alembic · Pydantic v2 · Celery · Redis

**Data** — PostgreSQL 16

**AI/ML** — OpenCV · Tesseract · scikit-learn (TF-IDF + logistic regression) · Prophet · statsmodels

**Infrastructure** — Docker · Docker Compose · AWS EC2/S3/IAM/CloudWatch · GitHub Actions · Vercel

---

## Modules

| Module | What it does | Milestone |
|---|---|---|
| Authentication | Rotating refresh tokens with reuse detection, OAuth, tenant isolation | M1 |
| Financial core | Accounts, transactions, budgets, goals, recurring items, CSV import, demo seeder | M2 |
| Analytics | Server-side aggregation, dashboard, validated chart system | M3 |
| Receipt intelligence | OpenCV preprocessing → Tesseract → per-field confidence → human review | M4 |
| Categorisation | Merchant normalisation, rules layer, TF-IDF classifier, feedback loop | M5 |
| Financial health | Six weighted sub-metrics → a published, versioned rubric | M6 |
| Insights | Rule-based detectors, materiality ranking, deduplication | M6 |
| Forecasting | Three tiers selected by data availability, with confidence intervals | M7 |
| **Purchase advisor** | **Affordability scoring, impact simulation, four verdicts — the flagship** | **M8** |
| Market intelligence | Price history, drop alerts, seller reliability | M9 |
| Simulator & notifications | Scenario modelling, alert delivery | M10 |

---

## Decisions worth knowing up front

Several things in the original concept would have broken in contact with reality. The [ADRs](docs/adr/)
carry the full reasoning; the short version:

**No retailer scraping.** Continuously scraping Amazon, Flipkart, and Croma violates their terms, is
defeated by anti-bot defences, and creates liability for a commercial product. Prices come through a
`PriceProvider` port — v1 ships a seeded catalogue, and a compliant adapter plugs in later without
touching product logic. ([ADR-004](docs/adr/004-ports-and-adapters.md))

**No LLM in the decision path.** Recommendations must be reproducible and auditable. An LLM's stated
rationale is generated text, not the actual computation — fluent and wrong simultaneously is worse than
obviously wrong. ([ADR-005](docs/adr/005-deterministic-explainable-ai.md))

**Forecasting is tiered, not Prophet-everywhere.** Prophet needs ~2 seasonal cycles; a three-week-old
account fed to Prophet yields a confident curve built on noise. Tiering by data availability makes the
model's limits visible instead of hiding them, and every response names its method and confidence.

**OCR assumes it will be wrong.** Tesseract reads thermal receipts at roughly 60–70%. Per-field
confidence, region highlighting, and a review queue are the design, not a fallback.

**Cold start is solved structurally.** Every engine is meaningless on an empty database, so CSV import
and a one-click demo seeder are Milestone 2 requirements, not later polish.

**Seller "scam risk" scoring was cut.** Fake-review detection isn't honestly achievable at this data
scale, and publishing a scam label about a named company is a defamation exposure. Replaced with a
Seller *Reliability* Score built only on observable signals, with the rubric published in-product.

---

## The invariant

> No score, verdict, or forecast reaches the user without the reasoning that produced it.

This is enforced in the type system: `Explanation` refuses to serialise a score or verdict with an
empty factor list. An unexplainable recommendation cannot reach a user because it cannot leave the
process. ([ADR-002](docs/adr/002-explanation-contract.md))

---

## Getting started

```bash
cp .env.example .env
make up                # api · worker · beat · postgres · redis · minio
make migrate           # alembic upgrade head
make check-backend     # ruff · mypy · import-linter · pytest

make frontend-install
make frontend-dev      # http://localhost:3000
make e2e               # Playwright smoke tests (needs the stack up)
```

No AWS account is needed for local development: MinIO stands in for S3 behind the `ObjectStore` port,
and every port ships a fake, so the backend suite runs with no network and no credentials.

> **Deploying?** Read [`infra/aws/COST-SAFETY.md`](infra/aws/COST-SAFETY.md) first. AWS budgets
> alert but do not stop spending, so the default production stack (Vercel · Fly.io · Neon · Upstash ·
> Cloudflare R2) is chosen because each service *stops serving* rather than billing when a free
> allowance runs out. AWS is supported and is the M11 target — opted into deliberately.

### Milestone status

**M0 — Foundation** ✅

| Exit criterion | Result |
|---|---|
| Six services boot healthy | api · worker · beat · postgres · redis · minio |
| Migrations apply and reverse | up → down → up verified |
| `/health/ready` reports each dependency | 200 when up; 503 naming the failed one, while liveness stays 200 |
| Themed shell, dark and light | verified in-browser; `--series-1` steps `#2a78d6` → `#3987e5` |
| **`Explanation` rejects a score with no factors** | enforced by a model validator, before any engine exists |

**M1 — Authentication & tenancy** ✅

| Exit criterion | Result |
|---|---|
| Register → login → refresh → logout | end to end, in a real browser |
| **Replaying a used refresh token revokes the whole family** | verified, including that the legitimate session dies with it |
| Every repository emits a `user_id` predicate | a sweep over all `BaseRepository` subclasses; new ones are covered automatically |
| Rate limits return `429` with `Retry-After` | per IP *and* per account |
| **Access token never touches `localStorage`/`sessionStorage`** | asserted in Playwright against the live app |
| Account deletion removes all user rows | audit trail survives, carrying only an opaque id |

**M2 — Financial core** ✅

| Exit criterion | Result |
|---|---|
| **Re-importing a CSV creates zero duplicates** | verified at the database level — the unique index on `(user_id, content_hash)` is the mechanism |
| 500-row bulk insert with 3 bad rows | `207 Multi-Status`, 497 created, 3 reported with reasons and indices |
| Demo seeder: 12 months, plausible | 4 accounts, ~470 transactions, <5s; fixed rent, seasonal spend, ~30% savings rate |
| Transfers excluded from income/expense | stored as a linked expense/income pair, excluded on `transfer_pair_id` |
| Balance reconciliation reports zero drift | materialised balances match the ledger after seeding |
| No `Float` column on any model | asserted by a sweep over all mapped tables |
| Playwright: import → see → edit → delete | plus the cold-start empty state and one-click demo |

**M3 — Analytics** ✅

| Exit criterion | Result |
|---|---|
| Aggregates match hand-computed values | a small, fully-enumerated fixture — every expected number is checkable by hand |
| A write bumps `data_version` and invalidates | version-keyed cache, not a TTL: no window where the dashboard disagrees with the ledger |
| Every chart has a keyboard-reachable table | enforced by `ChartContainer`, verified in-browser |
| Every chart carries a screen-reader summary | asserted non-empty for all three |
| **No dual-axis chart exists anywhere** | asserted in the DOM — at most one y-axis per figure |
| Charts correct in both themes | palette swaps `#2a78d6` → `#3987e5`, not inverted |

**M4 — Receipt intelligence** ✅

| Exit criterion | Result |
|---|---|
| Labelled fixtures, accuracy baseline recorded | 20 degraded fixtures — merchant 90%, total 75%, date 55%, tax 60% |
| **A low-confidence field blocks commit** | enforced in the service, `409` even if forced |
| Only the doubtful field is flagged | merchant/date stay unflagged when the total scans badly |
| Duplicate candidate surfaces before commit | same-day near-amount match, dismissible |
| Failed job dead-lettered with its exception | terminal vs. retryable distinguished |
| Review UI highlights the region per field | `bbox` per field drives the overlay |

The baseline is measured on **synthetic receipts degraded** with rotation, blur, noise, uneven
light and perspective. Real thermal receipts read worse — treat it as an upper bound and watch the
trend. `make eval` reprints it.

**Gates:** 233 backend tests (82% coverage) · ruff · `mypy --strict` ·
4 import-linter contracts · 31 Playwright specs · migrations reverse cleanly.

The M0 invariant is the one that matters most. It is enforced before any engine exists, so no
engine can ever ship a conclusion without its reasoning.
