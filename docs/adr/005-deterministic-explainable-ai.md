# ADR-005 — Deterministic, explainable AI; no LLM in the decision path

**Status:** Accepted · **Date:** 2026-08-04

## Context

Frugal makes consequential recommendations: whether to spend ₹1,34,900, whether an emergency fund is
adequate, whether cash will run short. An LLM could generate fluent, plausible-sounding advice with far
less engineering than a hand-built rubric.

## Decision

**No LLM participates in computing any score, verdict, or forecast.**

Scores come from **transparent weighted rubrics** with published, versioned weights. ML is confined to
two bounded roles:

- **Categorisation** — TF-IDF + logistic regression. A classifier over a fixed label space, with
  confidence thresholding and an offline eval harness.
- **Forecasting** — statistical time-series methods (recurring projection, EWMA/seasonal-naive,
  Prophet). Each reports its method and calibrated confidence.

`GET /health-score/rubric` publishes the scoring model in-product.

An LLM may later *phrase* an explanation, never compute one. Even then, factors must be generated
first and the phrasing constrained to them.

## Consequences

**Positive.** Every recommendation is reproducible — identical inputs give identical outputs. Rubrics
are unit-testable and versionable, so historical scores stay interpretable. No per-request inference
cost, no external AI dependency, no prompt-injection surface. Users can inspect and disagree with the
model, which is precisely what makes the advice usable.

**Negative.** Rubric weights are hand-designed and initially unvalidated against real outcomes.
Mitigated by versioning them and testing behaviour across a scenario matrix (M8 exit criteria), so
weights can be tuned without invalidating history. Explanations are templated rather than
conversational — a deliberate trade of fluency for verifiability.

**Constrains future models.** Any model added later must be attributable into `Explanation.factors`.
This rules out unattributable architectures, which is the intended effect.

## Why an LLM would specifically fail here

Three reasons, in order of severity:

1. **Non-reproducibility.** The same user asking twice could get different verdicts. For a financial
   decision this destroys trust the first time a user notices.
2. **Unfalsifiable reasoning.** An LLM's stated rationale is generated text, not the actual computation
   — it can be fluent and wrong simultaneously, which is worse than being obviously wrong.
3. **It contradicts the product thesis.** The differentiator is that the user can interrogate the
   number. A black box that also produces prose about itself is not explainable; it is a black box with
   better marketing.

## Alternative rejected

**LLM-generated recommendations with a rubric as a sanity check.** Rejected because whichever component
can veto the other is the one actually making the decision — so this is either the rubric with extra
steps and extra cost, or an LLM with a rubric-shaped disclaimer.
