# ADR-007 — Idempotent ingestion at two independent layers

**Status:** Accepted · **Date:** 2026-08-04

## Context

Transactions enter Frugal through four paths: manual entry, CSV import, receipt commit, and recurring
instantiation. Duplicates are near-certain without explicit prevention:

- A user re-imports last month's statement, which overlaps the previous import.
- A network timeout causes a client retry after the server already committed.
- A user double-clicks Save.
- A receipt is uploaded twice for one purchase.

A duplicate transaction is not a cosmetic bug. It corrupts every downstream engine — spend totals,
savings rate, health score, forecast, and affordability all shift — and the user's trust does not
survive discovering their finances are wrong.

## Decision

Two independent mechanisms, because they protect against different failures.

**1 — Content hash (duplicate *data*).**

```
content_hash = SHA256(user_id | occurred_on | amount | merchant_normalized | account_id)
UNIQUE (user_id, content_hash) WHERE deleted_at IS NULL
```

Enforced by the **database**, so no code path can bypass it. Re-importing an overlapping statement
conflicts and skips.

**2 — Idempotency keys (duplicate *requests*).**

`Idempotency-Key` required on `POST /transactions`, `/transactions/bulk`, `/imports/csv/commit`,
`/receipts/{id}/commit`, `/goals/{id}/contribute`. The key is stored with a hash of the request body
and the original response, for 24 hours.

- Replay with a matching body → the stored response, plus `Idempotency-Replayed: true`.
- Replay with a **different** body → `409`. That combination indicates a client bug, and silently
  accepting it would corrupt data.

**Additionally:** Celery tasks are idempotent and keyed by a `jobs` row, so a retried task does not
reprocess. Import previews report a `duplicate_estimate` before the user commits.

## Consequences

**Positive.** Re-import is safe and can be encouraged rather than warned against. Client retries are
safe by default. Duplicate prevention survives new ingestion paths, because the database constraint
applies regardless of which code wrote the row.

**Negative.** Legitimate identical transactions — two ₹50 coffees at the same shop on the same day from
the same account — collide. Resolved by an explicit "this is a separate transaction" flag that adds a
discriminator to the hash. Surfacing this to the user is better than silently merging, and far better
than silently duplicating.

The `merchant_normalized` component means changes to the normaliser change future hashes. Accepted:
normalisation is versioned, and historical rows keep their original hashes.

## Why both layers

They fail in opposite directions and neither subsumes the other.

An idempotency key alone does not stop a *deliberate* re-import — two different requests, two different
keys, identical data. A content hash alone does not make a retried request return the original
response; it returns a `409` the client cannot distinguish from a genuine conflict, so the client
cannot tell whether its write succeeded.

Together: the hash guarantees the data is correct, and the key guarantees the client learns the correct
outcome.
