# Frugal — Project Structure

**Version:** 1.0 · **Last updated:** 2026-08-04
**Companion documents:** [Architecture](02-architecture.md) · [API design](04-api-design.md) · [ADR-001](adr/001-modular-monolith.md)

---

## 1. Repository root

```
frugal/
├── backend/                 FastAPI application + Celery workers
├── frontend/                Next.js 16 application
├── infra/                   Compose files, Dockerfiles, deploy scripts
├── docs/                    This documentation set
├── .github/workflows/       CI/CD pipelines
├── docker-compose.yml       Local development stack
├── docker-compose.prod.yml  Production stack (EC2)
├── Makefile                 Task entry points
├── .env.example             Documented configuration shape (no secrets)
└── README.md
```

A **monorepo** for one deployable backend and one frontend. Split repositories would need version
coordination between the OpenAPI producer and its generated-types consumer for zero benefit at this
size — a contract change and its client update belong in one commit.

---

## 2. Backend

```
backend/
├── app/
│   ├── main.py                    App factory, middleware, composition root
│   ├── core/                      Shared kernel — imports nothing from modules
│   │   ├── config.py              Pydantic Settings, env-validated at boot
│   │   ├── database.py            Async + sync engines, session factories
│   │   ├── security.py            Argon2 hashing, JWT encode/decode
│   │   ├── dependencies.py        get_current_user, get_db, pagination
│   │   ├── errors.py              Domain exceptions → error envelope
│   │   ├── logging.py             Structured JSON logging, request-ID context
│   │   ├── pagination.py          Cursor encode/decode
│   │   ├── money.py               Money value object (Decimal)
│   │   ├── explanation.py         Explanation · Factor · Verdict · DataWindow
│   │   ├── repository.py          BaseRepository — tenant scoping
│   │   └── models.py              Base, UUIDMixin, TimestampMixin, SoftDeleteMixin
│   │
│   ├── modules/                   Domain modules — the boundary units
│   │   ├── auth/
│   │   │   ├── models.py          User, RefreshToken
│   │   │   ├── schemas.py         Request/response contracts
│   │   │   ├── repository.py      Data access
│   │   │   ├── service.py         Public interface — the ONLY cross-module entry
│   │   │   ├── router.py          HTTP layer
│   │   │   └── oauth.py           Google OAuth flow
│   │   ├── finance/               accounts · categories · transactions · budgets · goals · recurring
│   │   │   ├── models.py
│   │   │   ├── schemas.py
│   │   │   ├── repository.py
│   │   │   ├── service.py         FinanceService — consumed by health, forecast, advisor
│   │   │   ├── router.py
│   │   │   ├── importer.py        CSV parsing, column detection, dedupe
│   │   │   ├── seeder.py          Demo-data generator (FR-2.10)
│   │   │   └── recurrence.py      Recurring-item detection
│   │   ├── receipts/
│   │   │   ├── models.py schemas.py repository.py service.py router.py
│   │   │   └── pipeline/
│   │   │       ├── preprocess.py  OpenCV: perspective, deskew, denoise, threshold
│   │   │       ├── extract.py     Tesseract + per-field confidence
│   │   │       └── parse.py       Merchant/date/amount normalisation
│   │   ├── categorization/
│   │   │   ├── normalizer.py      Merchant-string cleaning
│   │   │   ├── rules.py           Deterministic layer (runs first)
│   │   │   ├── classifier.py      TF-IDF + logistic regression
│   │   │   ├── trainer.py         Training + artefact versioning
│   │   │   └── service.py
│   │   ├── analytics/             Aggregation queries only — no writes
│   │   ├── health/
│   │   │   ├── rubric.py          Versioned weights and bands
│   │   │   ├── metrics.py         The six sub-metric calculators
│   │   │   └── service.py         → Explanation
│   │   ├── insights/
│   │   │   ├── detectors/         One module per insight type
│   │   │   ├── ranking.py         Materiality scoring, dedup
│   │   │   └── service.py
│   │   ├── forecasting/
│   │   │   ├── tiers/
│   │   │   │   ├── recurring.py   < 60 days
│   │   │   │   ├── ewma.py        60–180 days
│   │   │   │   └── prophet.py     ≥ 180 days — imports Prophet INSIDE the function
│   │   │   ├── selector.py        Tier selection by observation count
│   │   │   └── service.py
│   │   └── advisor/
│   │       ├── rubric.py          Affordability weights, verdict thresholds
│   │       ├── simulator.py       Before/after state projection
│   │       ├── emi.py             Tenure options, total interest
│   │       └── service.py
│   │
│   ├── adapters/                  Ports + implementations
│   │   ├── ports.py               Protocol definitions
│   │   ├── storage/               S3Store · MinioStore · InMemoryStore
│   │   ├── ocr/                   TesseractEngine · FakeOCREngine
│   │   ├── prices/                SeedCatalogProvider · ManualEntryProvider · FakePriceProvider
│   │   └── notify/                EmailNotifier · NullNotifier
│   │
│   ├── workers/
│   │   ├── celery_app.py          Celery config, queues, routing
│   │   ├── tasks/                 receipts · categorization · forecasting · insights · health
│   │   └── schedules.py           Beat schedule
│   │
│   └── api/
│       ├── v1.py                  Router aggregation
│       └── middleware.py          Request ID, timing, error handling, rate limit
│
├── alembic/versions/              One migration per milestone
├── tests/
│   ├── conftest.py                Fixtures: ephemeral Postgres, fake adapters
│   ├── unit/                      Mirrors app/modules/
│   ├── integration/               Real DB, real HTTP
│   ├── eval/                      AI eval harnesses + fixture datasets
│   └── factories/                 Test data builders
│
├── scripts/                       seed_categories.py · train_categorizer.py · reconcile_balances.py
├── pyproject.toml                 Deps, ruff, mypy, pytest, coverage
├── .importlinter                  Module boundary contracts — CI-enforced
└── Dockerfile
```

### 2.1 Why each module has the same five files

`models · schemas · repository · service · router` in every module means a developer opening an
unfamiliar module already knows where everything is, and it makes the boundary rule mechanically
checkable: **`service.py` is the only file another module may import.**

`.importlinter` encodes this:

```ini
[importlinter:contract:module-independence]
name = Domain modules must not reach into each other's internals
type = forbidden
source_modules =
    app.modules.advisor
    app.modules.forecasting
    app.modules.health
forbidden_modules =
    app.modules.finance.models
    app.modules.finance.repository
    app.modules.receipts.models

[importlinter:contract:core-is-independent]
name = Core imports nothing from modules
type = forbidden
source_modules = app.core
forbidden_modules = app.modules
```

A boundary rule maintained by code review decays; one maintained by a failing build does not. That is
the whole reason this file exists.

### 2.2 Two engines, one schema

`core/database.py` exposes both an async engine (`asyncpg`, for the API) and a sync engine (`psycopg`,
for workers) over the same declarative models. Forcing async into CPU-bound OpenCV and Prophet code
buys nothing and complicates everything — see [ADR-006](adr/006-async-api-sync-workers.md).

### 2.3 Composition root

Adapters are selected once in `main.py` from configuration and injected as FastAPI dependencies.
Services depend on the `Protocol`, never the implementation, which is what lets the full test suite run
with `FakeOCREngine` and `InMemoryStore` — no network, no credentials, no Tesseract binary.

---

## 3. Frontend

```
frontend/
├── src/
│   ├── app/                            Next.js App Router
│   │   ├── layout.tsx                  Root layout, theme provider
│   │   ├── (auth)/                     login · register  — unauthenticated shell
│   │   └── (dashboard)/                Authenticated shell (sidebar + topbar)
│   │       ├── layout.tsx
│   │       ├── page.tsx                Dashboard
│   │       ├── transactions/
│   │       ├── budgets/  goals/
│   │       ├── receipts/[id]/review/
│   │       ├── health/  forecast/
│   │       ├── advisor/[evaluationId]/
│   │       └── settings/
│   │
│   ├── components/
│   │   ├── ui/                         shadcn primitives (generated)
│   │   ├── charts/
│   │   │   ├── chart-container.tsx     Enforces the light-mode label obligation
│   │   │   ├── line-chart.tsx  bar-chart.tsx  stacked-bar.tsx
│   │   │   ├── forecast-chart.tsx      p50 line + p10–p90 band
│   │   │   ├── dumbbell-chart.tsx      Before/after
│   │   │   ├── meter.tsx  sparkline.tsx
│   │   │   └── chart-table-view.tsx    Keyboard-reachable data table
│   │   ├── explanation/
│   │   │   ├── explanation-panel.tsx   THE shared component
│   │   │   ├── factor-row.tsx          Diverging contribution bar
│   │   │   ├── verdict-badge.tsx       Status colour + icon + label
│   │   │   └── caveats.tsx
│   │   ├── states/                     loading · empty · insufficient-data · error
│   │   └── layout/                     sidebar · topbar · mobile-nav
│   │
│   ├── features/                       Mirrors backend modules
│   │   ├── auth/         { api.ts, hooks.ts, components/, schemas.ts }
│   │   ├── transactions/ finance/ receipts/ health/ forecast/ advisor/
│   │   └── analytics/
│   │
│   ├── lib/
│   │   ├── api/
│   │   │   ├── client.ts               Fetch wrapper: auth, refresh, errors
│   │   │   ├── schema.d.ts             GENERATED from OpenAPI — never hand-edited
│   │   │   └── query-client.ts         TanStack Query config
│   │   ├── money.ts                    Decimal parsing/formatting
│   │   ├── theme.ts                    Palette tokens, light/dark
│   │   └── format.ts                   Dates, percentages, compact numbers
│   │
│   ├── hooks/                          use-theme · use-media-query · use-job-polling
│   └── types/
│
├── e2e/                                Playwright — one spec per milestone
├── components.json                     shadcn config
└── package.json
```

### 3.1 `features/` versus `components/`

`components/` holds presentation reusable anywhere. `features/` holds a domain slice — its API calls,
its query hooks, its Zod schemas, its domain-specific components — mirroring the backend module names
so a change to "advisor" has an obvious home on both sides of the stack.

Each feature owns four files:

```
features/advisor/
├── api.ts          Typed calls against lib/api/client
├── hooks.ts        TanStack Query hooks (keys, invalidation, polling)
├── schemas.ts      Zod schemas for React Hook Form
└── components/     Advisor-specific UI
```

### 3.2 Generated types are the contract

`lib/api/schema.d.ts` is generated from `/openapi.json` by `openapi-typescript` and committed. CI
regenerates and fails on a diff. A backend field rename therefore surfaces as a **TypeScript compile
error**, not a runtime `undefined` discovered by a user.

### 3.3 The chart container enforces accessibility

`chart-container.tsx` is not a styling wrapper. The light-mode palette validation produced a contrast
WARN on the aqua and yellow slots, and the relief rule requires visible labels or a table view. The
container's props make that non-optional:

```tsx
type ChartContainerProps = {
  series: SeriesSpec[]
  directLabels?: boolean
  tableView?: ReactNode
  summary: string        // required — screen-reader text summary
}
// Runtime invariant: if any series uses slot 3 or 4 in light mode,
// directLabels or tableView must be provided.
```

The obligation is encoded where charts are built, so it cannot be forgotten one chart at a time.

---

## 4. Infrastructure

```
infra/
├── docker/
│   ├── backend.Dockerfile      Multi-stage; Tesseract + OpenCV system deps
│   ├── worker.Dockerfile       Backend image + ML extras
│   └── caddy/Caddyfile         TLS termination, automatic certificates
├── deploy/
│   ├── deploy.sh               Pull tagged image, migrate, restart
│   └── bootstrap-ec2.sh        One-time host setup, 2 GB swap
└── aws/
    ├── iam-policy.json         Least-privilege instance profile
    ├── s3-bucket-policy.json   Public access blocked
    └── cloudwatch-alarms.json
```

Local `docker-compose.yml` runs api · worker · beat · postgres · redis · **minio**. MinIO means the
full stack — including receipt upload — runs with no AWS account and no credentials. The `ObjectStore`
port makes MinIO and S3 interchangeable.

`bootstrap-ec2.sh` creates a 2 GB swap file. On a 1 GB instance this is what absorbs Prophet's fit-time
memory spike instead of triggering the OOM killer.

---

## 5. Configuration

One `Settings` class, validated at boot. The process fails fast on a missing or malformed variable
rather than surfacing it as a confusing runtime error later.

```python
class Settings(BaseSettings):
    environment: Literal["local", "ci", "production"]
    database_url: PostgresDsn           # pooled endpoint (app)
    database_direct_url: PostgresDsn    # direct endpoint (Alembic DDL)
    redis_url: RedisDsn
    jwt_secret: SecretStr
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_days: int = 30
    storage_backend: Literal["s3", "minio", "memory"]
    ocr_engine: Literal["tesseract", "fake"]
    price_provider: Literal["seed_catalog", "manual", "fake"]
    ocr_confidence_threshold: float = 0.75
    forecast_min_observation_days: int = 14
```

Adapter selection is configuration, so tests, local development, and production differ by environment
rather than by code path — the code under test is the code that ships.

---

## 6. Task entry points

```make
make up            # Full local stack
make migrate       # alembic upgrade head
make reset-dev-data # Drop accumulated local test users
make test          # pytest with ephemeral Postgres
make eval          # AI eval harnesses, prints metrics
make lint          # ruff + mypy + import-linter
make types         # Regenerate frontend types from OpenAPI
make check         # lint + test + types — what CI runs
```

`make check` passing locally means CI passes. Any divergence between the two is treated as a bug in the
Makefile.

---

*Next: [07-roadmap.md](07-roadmap.md)*
