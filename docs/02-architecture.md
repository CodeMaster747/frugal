# Frugal — High-Level Architecture

**Version:** 1.0 · **Last updated:** 2026-08-04
**Companion documents:** [SRS](01-srs.md) · [Data model](03-data-model.md) · [API design](04-api-design.md) · [ADRs](adr/)

---

## 1. Architectural drivers

Six forces shape every decision below. Where they conflict, the earlier one wins.

| # | Driver | Consequence |
|---|---|---|
| 1 | **Explainability is the product** | Engines return a shared typed `Explanation`. No engine may emit a score without its factor decomposition. |
| 2 | **1 GB of application RAM** | Postgres and Redis are managed externally. Heavy libraries load lazily, in workers only. |
| 3 | **Solo developer, 12 modules** | Modular monolith. One deployable, one migration history, one test suite. |
| 4 | **Extraction must stay cheap** | Hard module boundaries enforced by tooling, so any module can become a service without a rewrite. |
| 5 | **External sources are unstable and legally constrained** | Every external dependency sits behind a port with a fake. |
| 6 | **CPU-bound AI work must not block the API** | Async API process, synchronous Celery workers, strict separation. |

---

## 2. C4 Level 1 — System context

```mermaid
graph TB
    User["User<br/><i>Salaried / freelance individual</i>"]

    Frugal["<b>Frugal</b><br/>Financial decision platform<br/><i>Tracks, scores, forecasts, advises</i>"]

    Google["Google OAuth<br/><i>Federated sign-in</i>"]
    S3["AWS S3<br/><i>Receipt & export storage</i>"]
    CW["AWS CloudWatch<br/><i>Logs, metrics, alarms</i>"]
    Email["Email provider<br/><i>Alerts & digests (M10)</i>"]
    Prices["Price sources<br/><i>Seeded catalogue in v1;<br/>licensed API later</i>"]

    User -->|"Manages finances,<br/>asks purchase questions"| Frugal
    Frugal -->|"Authenticates"| Google
    Frugal -->|"Stores receipts<br/>(presigned)"| S3
    Frugal -->|"Emits logs & metrics"| CW
    Frugal -.->|"Sends alerts"| Email
    Frugal -->|"Retrieves prices<br/>via PriceProvider port"| Prices

    style Frugal fill:#2563eb,color:#fff
    style User fill:#64748b,color:#fff
```

Dashed edges are v1.1. Note that **Price sources** connects through a port, not directly — v1 satisfies
it with a seeded catalogue, and the system cannot tell the difference.

---

## 3. C4 Level 2 — Containers

```mermaid
graph TB
    subgraph Vercel
        FE["<b>Web App</b><br/>Next.js 16 · TypeScript<br/>App Router · RSC<br/>TanStack Query · Recharts"]
    end

    subgraph EC2["AWS EC2 t3.micro — 1 GB"]
        API["<b>API</b><br/>FastAPI · Python 3.11<br/>async · SQLAlchemy 2<br/><i>~250 MB</i>"]
        Worker["<b>Worker</b><br/>Celery · sync<br/>OCR · ML · Forecast<br/><i>~450 MB, 1 concurrency</i>"]
        Beat["<b>Scheduler</b><br/>Celery Beat<br/><i>~60 MB</i>"]
    end

    subgraph Managed["Managed services — free tier"]
        PG[("<b>Postgres 16</b><br/>Neon<br/>System of record")]
        Redis[("<b>Redis</b><br/>Upstash<br/>Broker · cache · rate limit")]
    end

    subgraph AWS["AWS"]
        S3["<b>S3</b><br/>Private buckets<br/>Presigned access"]
        CW["<b>CloudWatch</b><br/>Logs · metrics · alarms"]
    end

    FE -->|"HTTPS / JSON<br/>Bearer + refresh cookie"| API
    FE -->|"Direct upload<br/>(presigned PUT)"| S3

    API -->|"asyncpg + SSL<br/>pooled endpoint"| PG
    API -->|"enqueue · cache"| Redis
    API -->|"presign"| S3
    API --> CW

    Worker -->|"psycopg sync"| PG
    Worker -->|"consume"| Redis
    Worker -->|"read objects"| S3
    Worker --> CW

    Beat -->|"schedule"| Redis

    style API fill:#2563eb,color:#fff
    style Worker fill:#7c3aed,color:#fff
    style FE fill:#0891b2,color:#fff
```

**Why the frontend uploads directly to S3.** Routing 10 MB receipt images through a 1 GB API process
would consume request-worker memory and stall the event loop. Presigned PUT keeps image bytes entirely
out of the API. The API only ever handles the object key.

**Why the worker runs at concurrency 1.** OpenCV, Tesseract, and Prophet are memory-heavy and
CPU-bound. A second concurrent worker on t3.micro is an OOM kill. Throughput comes from queue
prioritisation, not parallelism, at this tier.

---

## 4. C4 Level 3 — Backend module structure

```mermaid
graph TB
    subgraph Interface["Interface layer"]
        R["Routers<br/><i>HTTP only: parse, authorise, delegate</i>"]
    end

    subgraph Domain["Domain modules — app/modules/"]
        direction LR
        Auth["auth"]
        Fin["finance<br/><i>accounts · transactions<br/>budgets · goals</i>"]
        Rec["receipts"]
        Ana["analytics"]
        Health["health"]
        Ins["insights"]
        Fore["forecasting"]
        Adv["advisor"]
    end

    subgraph Shared["Shared kernel — app/core/"]
        Expl["<b>Explanation</b><br/>Factor · Verdict · Confidence"]
        Money["<b>Money</b><br/>Decimal value object"]
        Repo["<b>BaseRepository</b><br/>tenant scoping"]
        Err["Errors · Config · Logging"]
    end

    subgraph Ports["Ports — app/adapters/"]
        PP["PriceProvider"]
        OCR["OCREngine"]
        FC["Forecaster"]
        OS["ObjectStore"]
        NT["Notifier"]
    end

    R --> Domain
    Adv --> Fore
    Adv --> Health
    Adv --> Fin
    Health --> Fin
    Ins --> Fin
    Fore --> Fin
    Ana --> Fin
    Rec --> Fin

    Domain --> Shared
    Rec --> OCR
    Fore --> FC
    Adv --> PP
    Rec --> OS

    style Expl fill:#16a34a,color:#fff
    style Repo fill:#16a34a,color:#fff
```

### 4.1 The boundary rule

Each module owns `models.py`, `schemas.py`, `service.py`, `repository.py`, `router.py`.

**A module may import:** `app.core.*`, `app.adapters.*` (ports only), and the *service interface* of
another module.

**A module may never import:** another module's `models.py`, `repository.py`, or internal helpers.
No cross-module ORM relationship or JOIN.

This is enforced by an `import-linter` contract in CI. It is a build failure, not a code-review note —
which is the entire point. Boundary rules maintained by discipline decay; boundary rules maintained by
a failing build do not.

**Why this matters concretely:** the advisor consumes forecasting, health, and finance. If those were
joined at the ORM level, extracting forecasting into its own service later would mean rewriting the
advisor. Because they communicate through service calls returning DTOs, extraction means swapping a
local call for an HTTP client behind the same interface.

### 4.2 Dependency direction

Dependencies point **inward**: routers → services → repositories → models, and everything → core.
Core imports nothing from modules. Ports are defined in `core`/`adapters` as protocols; concrete
adapters are wired at composition time in `app/main.py`. Services depend on the protocol, never the
implementation — which is what makes every AI path testable without network, credentials, or a GPU.

---

## 5. The Explanation contract

The one abstraction the entire product hangs on.

```python
class Direction(StrEnum):
    POSITIVE = "positive"    # improves the outcome
    NEGATIVE = "negative"    # worsens it
    NEUTRAL  = "neutral"

class Factor(BaseModel):
    name: str                # "Emergency fund coverage"
    value: str               # "3.2 months"
    raw_value: Decimal
    weight: Decimal          # rubric weight, 0..1
    contribution: Decimal    # signed points contributed to the score
    direction: Direction
    explanation: str         # why this factor matters, in plain language

class Explanation(BaseModel):
    verdict: str | None      # BUY_NOW · WAIT · HEALTHY · AT_RISK ...
    score: Decimal | None    # 0..100
    confidence: Decimal      # 0..1, calibrated
    method: str              # "prophet" · "ewma" · "rubric_v2"
    data_window: DataWindow  # start, end, observation count
    factors: list[Factor]    # MUST be non-empty when score or verdict is set
    caveats: list[str]       # "Only 45 days of history — treat as provisional"
    computed_at: datetime
```

Enforced by a Pydantic model validator: a non-null `score` or `verdict` with an empty `factors` list
raises. An unexplainable recommendation cannot be serialised, so it cannot reach the user.

**System-wide payoff.** Health, insights, forecasting, advisor, and (in v1.1) reliability scoring all
emit this shape. The frontend renders all five with one `<ExplanationPanel>` component. Twelve modules,
one mental model — for the user and for the code.

---

## 6. Key flows

### 6.1 Receipt processing

```mermaid
sequenceDiagram
    actor U as User
    participant FE as Next.js
    participant API as FastAPI
    participant S3
    participant Q as Redis
    participant W as Worker
    participant DB as Postgres

    U->>FE: Select receipt image
    FE->>API: POST /receipts/upload-url
    API->>API: Validate type & size; generate key
    API->>S3: Presign PUT (5 min TTL)
    API-->>FE: {upload_url, receipt_id}
    FE->>S3: PUT image bytes (direct)
    FE->>API: POST /receipts/{id}/process
    API->>DB: status = QUEUED
    API->>Q: enqueue process_receipt
    API-->>FE: 202 Accepted

    Note over FE: Poll status

    W->>Q: consume
    W->>S3: GET object
    W->>W: OpenCV: perspective → deskew → denoise → threshold
    W->>W: Tesseract OCR (image_to_data, per-token confidence)
    W->>W: Extract merchant · date · total · tax · line items
    W->>W: Score confidence per field
    alt All fields above threshold
        W->>DB: status = READY; propose transaction
    else Any field below threshold
        W->>DB: status = NEEDS_REVIEW; flag fields
    end
    W->>DB: Persist extraction + confidences

    FE->>API: GET /receipts/{id}
    API-->>FE: Fields + per-field confidence + flags
    U->>FE: Correct flagged fields, confirm
    FE->>API: POST /receipts/{id}/commit
    API->>API: Duplicate check (content hash)
    API->>DB: Create transaction
```

The confidence score is carried per field, not per receipt. A receipt with a confident total and an
ambiguous date should only ask the user about the date — asking them to re-verify everything is how
human-in-the-loop UX gets abandoned.

### 6.2 Purchase advisor — the flagship path

```mermaid
sequenceDiagram
    actor U as User
    participant API as FastAPI
    participant Adv as AdvisorService
    participant PP as PriceProvider
    participant Fin as FinanceService
    participant Fore as ForecastService
    participant H as HealthService

    U->>API: POST /advisor/evaluate {product, price, horizon}
    API->>Adv: evaluate(user_id, intent)

    par Gather financial state
        Adv->>Fin: liquid_savings, committed_outflows, debt_ratio, goals
    and
        Adv->>Fore: project_balance(90d) → trajectory + trough + method
    and
        Adv->>H: current_score + emergency_fund_months
    and
        Adv->>PP: resolve(product) → price points, sellers
    end

    Adv->>Adv: Simulate post-purchase state (cash & EMI variants)
    Adv->>Adv: Score 7 weighted factors → affordability 0..100
    Adv->>Adv: Map score + hard constraints → verdict
    Adv->>Adv: Compute opportunity cost (goal delay, EF months lost)
    Adv->>Adv: If WAIT → solve affordable-from date against forecast

    Adv-->>API: AdvisorResult{verdict, affordability, simulation, Explanation}
    API-->>U: 200 + full factor decomposition
```

Note the four gathers run concurrently — the advisor's latency budget is dominated by the forecast, so
serialising them would be the difference between 800 ms and 3 s.

**Hard constraints override the score.** A high affordability score still yields `NOT_RECOMMENDED` if
the purchase drives emergency-fund coverage below one month or the forecast trough below zero. A
weighted score alone can average away a disqualifying condition; explicit guard rails cannot.

### 6.3 Tiered forecasting

```mermaid
flowchart TD
    Start["forecast(user, horizon)"] --> Cache{"Cached &<br/>data unchanged?"}
    Cache -->|yes| Return["Return cached"]
    Cache -->|no| Count["Count observation days"]

    Count --> T1{"< 60 days?"}
    T1 -->|yes| M1["<b>RecurringProjection</b><br/>Known salary, rent, EMIs,<br/>subscriptions only<br/>confidence ≤ 0.5<br/>caveat: limited history"]

    T1 -->|no| T2{"60–180 days?"}
    T2 -->|yes| M2["<b>EWMA + seasonal-naive</b><br/>Recurring items +<br/>smoothed discretionary<br/>confidence ≈ 0.65"]

    T2 -->|no| M3["<b>Prophet</b><br/>Weekly + monthly seasonality,<br/>recurring items as regressors<br/>confidence ≈ 0.8"]

    M1 --> Merge["Overlay known future obligations"]
    M2 --> Merge
    M3 --> Merge
    Merge --> Trough["Detect balance trough & shortfall dates"]
    Trough --> Expl["Build Explanation:<br/>method · window · confidence · caveats"]
    Expl --> Store["Cache + persist"]

    style M3 fill:#7c3aed,color:#fff
    style Expl fill:#16a34a,color:#fff
```

**Why tiering rather than Prophet everywhere.** Prophet requires roughly two seasonal cycles to fit
anything meaningful. A three-week-old account fed to Prophet produces a confident-looking curve built
on noise — the worst possible failure mode for a product whose thesis is trustworthy explanation.
Tiering makes the model's limits visible instead of hiding them, and `method` + `caveats` travel with
every response so the UI can say *"based on 45 days, provisional"* rather than implying certainty.

Prophet is imported inside the tier-3 function body, not at module scope — the API process never pays
its ~400 MB.

---

## 7. Data architecture

**Postgres is the single system of record.** Redis holds only regenerable state: Celery broker,
computed-aggregate cache, and rate-limit counters. Losing Redis degrades performance, never data.

**Caching.** Read-through on expensive aggregates (dashboard, health, forecast), keyed by
`user_id + scope + data_version`. `data_version` is bumped on any transaction write, which invalidates
by construction rather than by TTL guesswork — stale financial numbers are worse than slow ones.

**Async and sync coexistence.** The API uses `asyncpg` (async engine); workers use `psycopg` (sync
engine). Both map the same declarative models. Two engines, one schema — the alternative, forcing
async into CPU-bound OCR/ML code, buys nothing and complicates everything.

**Job durability.** Every Celery task is idempotent, keyed by a job row in Postgres. Retries are
exponential with jitter; terminal failures persist the exception to a dead-letter table. Job state
lives in Postgres, not Redis, so an Upstash eviction can't lose a user's receipt.

---

## 8. Deployment

```mermaid
graph LR
    subgraph Dev["Local"]
        DC["docker compose up<br/>api · worker · beat<br/>postgres · redis · minio"]
    end

    subgraph CI["GitHub Actions"]
        L["ruff · mypy<br/>import-linter"] --> T["pytest<br/>+ live Postgres/Redis"] --> B["Build image"] --> SC["Trivy + secret scan"]
    end

    subgraph Prod["Production"]
        V["Vercel<br/>frontend"]
        E["EC2 t3.micro<br/>compose: api · worker · beat<br/>Caddy TLS"]
        N[("Neon PG")]
        UP[("Upstash Redis")]
        S3B["S3 + CloudWatch"]
    end

    DC --> CI
    SC -->|"push :sha"| E
    SC -->|"deploy"| V
    E --> N & UP & S3B
```

Local development uses containerised Postgres, Redis, and **MinIO** as an S3-compatible store, so no
AWS credentials are needed to develop and tests never touch real infrastructure. The `ObjectStore`
port makes MinIO and S3 interchangeable.

Deployment is image-tag based (`:${git_sha}`), so rollback is re-pointing the tag — appropriate for
single-instance hosting and reversible in one command.

### 8.1 Cost safety comes before the deployment target

**AWS has no hard spending cap.** Budgets alert; they do not stop. For a
deployment where an unexpected bill would genuinely hurt, that property decides
the target — not the feature list.

The architecture already makes this a config choice rather than a rewrite: the
`ObjectStore` port (ADR-004) drives any S3-compatible endpoint, and Postgres and
Redis were never on AWS. So the default production stack is chosen for one
property — **it stops serving rather than billing** when a free allowance runs
out:

| Concern | Service | Behaviour at the limit |
|---|---|---|
| Frontend | Vercel Hobby | Bandwidth capped |
| API + worker | Fly.io / Render free | Service stops |
| Postgres | Neon free | Paused |
| Cache / broker | Upstash free | Requests rejected |
| Object storage | Cloudflare R2 | Capped; no egress charges |

AWS (EC2 + S3 + CloudWatch) remains fully supported and is the M11 target, but
it is opted into deliberately once a ten-dollar mistake is affordable. The
runbook, budgets, and a circuit-breaker policy are in
[`infra/aws/COST-SAFETY.md`](../infra/aws/COST-SAFETY.md).

### 8.2 Free-tier budget

| Service | Tier | Limit | Headroom |
|---|---|---|---|
| Vercel Hobby | Free | 100 GB bandwidth/mo | Ample |
| EC2 t3.micro | Free 12 mo | 750 h/mo | Full-time single instance |
| Neon Postgres | Free | 0.5 GB storage | ~500k transactions |
| Upstash Redis | Free | 10k commands/day | Sufficient at low concurrency |
| S3 | Free 12 mo | 5 GB | ~5k receipts |
| CloudWatch | Free | 5 GB logs, 10 alarms | Adequate |

**Memory budget on t3.micro (1 GB):** API ≈ 250 MB + Worker ≈ 450 MB + Beat ≈ 60 MB + Caddy ≈ 20 MB
+ OS ≈ 150 MB ≈ **930 MB**. Tight by design, which is exactly why Postgres and Redis are external and
`torch` is excluded. A 2 GB swap file absorbs Prophet's fit-time spikes.

---

## 9. Security architecture

```mermaid
graph LR
    B["Browser"] -->|"Access token<br/>in memory (15 min)"| API
    B -->|"Refresh token<br/>httpOnly · Secure · SameSite=Lax"| API
    API -->|"Rotate + reuse detection"| DB[("Token families")]
    API -->|"Instance profile<br/>(no static keys)"| AWS["S3 · CloudWatch"]
```

- **Token placement.** Access tokens live in JavaScript memory and die with the tab; refresh tokens
  live in an httpOnly cookie unreadable by script. The common `localStorage` pattern hands both to any
  successful XSS payload.
- **Rotation with reuse detection.** Each refresh issues a new token and invalidates the old. A
  replayed token means theft, so the entire family is revoked.
- **Tenant isolation at the data layer.** `BaseRepository` injects `user_id` into every query. A test
  asserts that no repository method can construct an unscoped query against a user-owned table. This
  is the class of bug that leaks another user's finances, so it is closed structurally rather than
  procedurally.
- **Storage.** Buckets private with public access blocked; presigned URLs scoped to a single key with
  short TTL. Receipts are PII.
- **Least privilege.** EC2 instance profile grants only `s3:GetObject`/`PutObject` on the receipts
  prefix and CloudWatch log writes. No long-lived access keys anywhere.

---

## 10. Rejected alternatives

| Considered | Rejected because |
|---|---|
| **Microservices from day one** | Solo developer. Distributed tracing, network failure modes, and per-service deploys cost more than they return at this scale. ADR-001 buys the extraction path instead. |
| **All services on one EC2 instance** | Postgres + Redis + worker + API on 1 GB OOMs on first Prophet fit. Managed free tiers cost $0 and remove the failure mode entirely. |
| **Prophet as the only forecaster** | Undefined below ~2 seasonal cycles. Confidently wrong output directly contradicts the product thesis. |
| **Direct retailer scraping** | ToS violation, defeated by anti-bot defences, unbounded maintenance, and real liability for a commercial product. Port + adapter defers this decision without blocking the feature. |
| **LLM-generated recommendations** | Non-reproducible and non-auditable. Explainability is the differentiator; a black box would erase it. |
| **Serverless (Lambda)** | Cold starts against a 400 MB Prophet dependency, and 15-minute limits are awkward for batch ML. Poor fit for long CPU-bound jobs. |
| **GraphQL** | Single known client with well-understood access patterns. REST + generated OpenAPI types gives type safety without the resolver complexity. |
| **Storing money as float/integer paise only** | Float is a correctness defect. Integer minor units work but make every read site do conversion; `Numeric(18,2)` + a `Money` object is safer and clearer. |

---

## 11. Evolution path

The architecture is designed so growth is additive rather than corrective:

- **Scale reads** → replica + read-through cache; the repository layer already isolates queries.
- **Scale workers** → separate queues by workload (`ocr`, `ml`, `forecast`) onto a second instance; Celery routing is config, not code.
- **Extract a service** → forecasting or receipts are the natural first candidates. Because callers depend on a service interface returning DTOs, extraction swaps a local call for an HTTP client behind the same signature.
- **Real price data** → implement one `PriceProvider` adapter. No product-logic change.
- **Better categorisation** → the `Categoriser` interface admits an embedding model; the eval harness proves the upgrade before it ships.

---

*Next: [03-data-model.md](03-data-model.md)*
