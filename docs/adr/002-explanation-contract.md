# ADR-002 — A single typed Explanation contract for every engine

**Status:** Accepted · **Date:** 2026-08-04

## Context

Frugal's differentiator is not its feature list — competitors have budgets and charts. It is that every
score, verdict, and forecast can be decomposed into the inputs that produced it.

"Explainable AI" stated as a principle decays into marketing copy. Five engines (health, insights,
forecasting, advisor, and later reliability) each producing bespoke, differently-shaped rationale would
give the frontend five renderers and the user five mental models.

## Decision

Every engine returns the **same typed envelope**:

```python
class Factor(BaseModel):
    name: str
    value: str                 # display form: "3.2 months"
    raw_value: Decimal
    weight: Decimal            # rubric weight, 0..1
    contribution: Decimal      # signed points contributed
    direction: Direction       # positive | negative | neutral
    explanation: str           # plain language

class Explanation(BaseModel):
    verdict: str | None
    score: Decimal | None
    confidence: Decimal        # 0..1
    method: str                # "prophet" | "ewma_seasonal" | "rubric_v1"
    data_window: DataWindow
    factors: list[Factor]
    caveats: list[str]
    computed_at: datetime
```

**Enforced invariant:** a model validator raises if `score` or `verdict` is set while `factors` is
empty. An unexplainable recommendation cannot be serialised, therefore cannot reach a user.

A companion test asserts that, for rubric-based engines, weights sum to 1.00 and contributions sum to
the score.

## Consequences

**Positive.** One frontend component (`<ExplanationPanel>`) renders every engine. Adding a factor to a
rubric requires no frontend change. Twelve modules present one mental model. New engines get
explainability by construction — the type will not compile otherwise.

**Negative.** Constrains engine design: any engine must be decomposable into weighted factors. This
rules out models whose output cannot be attributed — which is a deliberate constraint, not an oversight
(see ADR-005).

**Client obligation.** The frontend must render factors **generically**, never switching on known
factor names. This is what makes adding a factor a non-breaking API change, and it is documented in the
OpenAPI schema description.

## Why enforce it in the type rather than by convention

The failure mode this prevents is specific and likely: under time pressure, a new engine ships a score
with a `TODO` where its factors should be. It works, it looks fine, and the product quietly stops being
explainable one engine at a time. Making that state unrepresentable costs one validator.
