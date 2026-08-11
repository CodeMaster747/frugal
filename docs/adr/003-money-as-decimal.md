# ADR-003 — Money as Decimal, with currency stored from day one

**Status:** Accepted · **Date:** 2026-08-04

## Context

Every meaningful number in Frugal is money. Floating-point representation cannot exactly encode most
decimal fractions: `0.1 + 0.2 == 0.30000000000000004`. In a financial product these errors accumulate
through aggregation and surface as balances that do not reconcile.

Separately, v1 targets a single currency (INR). The tempting simplification is to omit a currency
column and add it "when we need multi-currency."

## Decision

**Representation.** `NUMERIC(18,2)` in Postgres, `Decimal` in Python, a `Money` value object at
boundaries. JSON serialises amounts as **strings**, never numbers.

**Currency.** Every monetary row carries a `currency CHAR(3)` column from the first migration, even
though v1 supports one currency.

**Guards.**
- A test asserts no `Float`/`REAL`/`DOUBLE PRECISION` column exists on any model.
- Aggregations use `SUM(amount)::numeric`, never casting through float.
- `Money` refuses arithmetic between differing currencies.

## Consequences

**Positive.** Exact arithmetic. Balances reconcile. Multi-currency becomes an additive feature rather
than a migration across every financial table.

**Negative.** `Decimal` is slower than float (irrelevant at this data volume). Requires a decimal
library client-side. Amounts as JSON strings surprise developers expecting numbers, so it is documented
in the API contract.

## Why strings in JSON

`JSON.parse` produces an IEEE-754 double. Serialising `1250.10` as a JSON number and parsing it
client-side reintroduces exactly the error the `NUMERIC` column exists to prevent — the value would not
survive a round trip unchanged. Strings preserve it exactly, and the client parses into a decimal type.

## Why currency now rather than later

Adding a currency column later means backfilling every financial table, updating every aggregate to
group by it, revisiting every comparison, and auditing already-computed historical scores. The cost
today is one column per table and one field in a value object. The asymmetry is extreme.

## Alternative rejected

**Integer minor units** (store paise as `BIGINT`) is also exact and is a legitimate choice. Rejected
because every read site must divide and every write site must multiply, and a single forgotten
conversion produces a 100× error — a louder failure than float drift, but far easier to introduce.
`NUMERIC` keeps the stored value identical to the displayed value.
