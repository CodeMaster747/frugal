# Architecture Decision Records

Each record captures one binding decision, the context that forced it, and what it costs. They are
written to be read by someone who arrives later and wants to know *why*, not *what* — the what is in
the code.

| # | Decision | Summary |
|---|---|---|
| [001](001-modular-monolith.md) | Modular monolith with tool-enforced boundaries | One deployable; `import-linter` makes module boundaries a build failure rather than a review note. |
| [002](002-explanation-contract.md) | A single typed `Explanation` contract | Every engine returns the same envelope; a score with no factors cannot serialise. |
| [003](003-money-as-decimal.md) | Money as `Decimal`, currency from day one | `NUMERIC(18,2)` end to end; JSON amounts are strings so values survive a round trip. |
| [004](004-ports-and-adapters.md) | Ports and adapters at every external boundary | Isolates the price-source risk; the whole suite runs with no network or credentials. |
| [005](005-deterministic-explainable-ai.md) | Deterministic AI, no LLM in the decision path | Reproducible, auditable, inspectable — the product thesis made structural. |
| [006](006-async-api-sync-workers.md) | Async API, synchronous workers | Matches each workload's actual nature; one schema, two engines. |
| [007](007-idempotent-ingestion.md) | Idempotent ingestion at two layers | Content hash protects the data; idempotency keys protect the client's knowledge of it. |

## Format

Context → Decision → Consequences (positive and negative) → why the main alternative was rejected.

Consequences include the negative ones. An ADR listing only benefits is advocacy, not a record, and it
is useless to the person who later hits the cost it failed to mention.

## Changing a decision

Supersede rather than edit. Add a new ADR, mark the old one `Superseded by ADR-NNN`, and leave its text
intact. The reasoning that was valid under earlier constraints is what explains the code written under
them.
