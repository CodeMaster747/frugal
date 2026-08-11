# Evaluation baselines

Numbers the AI modules actually produce, measured by `make eval`. Recorded here
so regressions are *visible* rather than felt, and so every claim in the README
has a run behind it.

Rerun with `make eval`. If a number here disagrees with the harness, the harness
is right and this file is stale.

---

## Merchant categorisation (M5)

`backend/tests/eval/test_categorization_accuracy.py`
Model `tfidf-lr-v1`, 167 seed pairs, measured 2026-08-05.

### Two tiers, because they answer different questions

An earlier version of this harness reported **100% accuracy**, which was not a
good result — it meant the test was wrong. Every "held-out" merchant contained a
seed brand as a substring (`swiggy instamart order` contains `swiggy`), so
character n-grams were performing dictionary lookup and the score measured
memorisation. The harness now separates:

| Tier | What it measures | Pipeline | Accuracy | Macro-F1 |
|---|---|---|---|---|
| **A — known merchants, noisy narrations** | the everyday case: real bank narration formats (`UPI/SWIGGY*ORDER/HDFC0001234`, `POS 4412 DMART AVENUE SUPERMA`) for merchants in the corpus | rules → model | **100%** (30/30) | 1.000 |
| **B — merchants absent from the corpus** | generalisation to a brand never seen (`medplus pharmacy`, `spicejet airlines`, `torrent power ltd`) | model only | **25%** (10/40) | 0.349 |

Tier A is the number that predicts how the product feels day to day. Tier B is
the model's own contribution, and it is a much harder problem — publishing it is
the point, because the alternative is an inflated figure that hides the day a
real user shops somewhere we have never heard of.

**Tier B, threshold removed: 62.5%.** The model has the right answer far more
often than it is willing to say so. That gap is the threshold's cost, paid
deliberately.

### The confidence threshold is calibrated, not chosen

The original value was `0.60`, picked by intuition. It was wrong by roughly an
order of magnitude: with 23 classes, softmax mass spreads thin and a *correct*
top class routinely sits near 0.35, so 0.60 rejected almost everything.

| Threshold | Coverage | Precision |
|---|---|---|
| 0.20 | 45% | 83% |
| 0.25 | 38% | 87% |
| **0.30 (shipped)** | **28%** | **91%** |
| 0.35 | 18% | 100% |
| 0.40 | 12% | 100% |
| 0.60 (was) | 8% | 100% |

`0.30` is where the curve stops being free. Precision stays at 91% while
coverage more than triples against the old value. The trade is favourable
because a model suggestion lands *unreviewed* in the review queue: a wrong one
costs one click to fix, a missing one costs choosing from 23 categories
unaided. 100% precision at 8% coverage is a model that is never wrong because
it never speaks.

Calibrated on 40 hand-labelled merchants — enough to reject a value that is
wrong by an order of magnitude, not enough to defend 0.30 over 0.28.

### Regression floors

Guards, not targets: `MIN_KNOWN_ACCURACY = 0.85`, `MIN_UNSEEN_MACRO_F1 = 0.30`,
`MIN_UNSEEN_PRECISION = 0.85`. They exist to catch a change that makes things
worse, and they are set from measurement rather than aspiration.

### Where it is weak, and why that is survivable

Tier B abstains on 29 of 40 unseen merchants. The recovery path is not a better
model — it is the feedback loop: the first time a user categorises `medplus
pharmacy`, it becomes a personal rule that fires immediately, and a training
label for the next run. The model only has to carry the *first* encounter, and
the rules layer carries every one after.

> **Roadmap note.** The roadmap's original target was macro-F1 ≥ 0.75, written
> before anything was measured, and it anticipated a single number. Tier A
> clears it; Tier B does not, and would not without a corpus several times
> larger. The target is superseded by the two-tier figures above, which are
> what the roadmap asked for in substance: *a recorded, defensible number*.

---

## Purchase advisor (M8)

`backend/tests/unit/test_advisor_matrix.py`
Twenty user/price combinations across eight personas.

Not an accuracy measurement — there is no ground truth for "should this person
buy a laptop". What the matrix pins is that the rubric's verdict matches what a
competent adviser would say, on cases chosen to span the space: wealthy/cheap,
broke/expensive, moderate/moderate, over-indebted, in drawdown, and new.

### It disagreed with the rubric on 8 of 20 rows

That is the entire value of writing it. The disagreements split three ways:

| Kind | Count | Outcome |
|---|---|---|
| Expectation too cautious | 3 | Rows updated, with the reasoning recorded inline |
| **Genuine rubric fault** | 2 | New constraints added |
| Fault in the *new* constraints | 3 | Constraints gated on materiality |

The two genuine faults:

* A ₹95,000 laptop on a ₹95,000 monthly income was advised as a **cash**
  purchase leaving 2.2 months of cover. The emergency-fund band moves only six
  points between two and three months, and at weight 0.25 that is not enough to
  change a verdict. Dropping below three months of cover is precisely when
  spreading the cost is the better route — now a constraint.
* Someone **spending more than they earn** was told to buy. The savings-rate
  factor is weighted 0.13, so a catastrophic rate costs thirteen points and
  loses to everything else. Now a constraint.

Then the new constraints were themselves too blunt — a ₹8,000 purchase against
₹400,000 of reserves was capped for "accelerating a decline" it could not
measurably accelerate — and the matrix caught that in turn. Constraints are now
gated on the purchase being at least half a month of expenses.

### What is asserted

* Every verdict returns 7 factors, weights summing to exactly 1.00, and
  contributions summing to exactly the score.
* Every `WAIT` carries `affordable_from`; the database refuses to store one
  without it, tested by attempting the insert directly.
* Longer EMI tenures cost more in total, and the recommended option is never
  simply the smallest monthly instalment.

Measured latency for a full evaluation: **~30 ms**, against a 2 s target.

---

## Cash-flow forecasting (M7)

`backend/tests/eval/test_forecast_backtest.py`
Walk-forward backtest, 3 synthetic personas × 3 seeds × 10 cut points per tier.

### MAPE on the projected balance path

| Tier | 30d | 60d | 90d |
|---|---|---|---|
| `recurring_projection` (tier 1) | 1.5% | 2.1% | 2.7% |
| `ewma_seasonal` (tier 2) | **1.0%** | **1.1%** | **1.4%** |
| *naive: assume no change* | 20.4% | 23.7% | 26.4% |

### Terminal error (₹ at the end of the horizon)

| Tier | 30d | 60d | 90d |
|---|---|---|---|
| `recurring_projection` | 2,845 | 5,006 | 6,790 |
| `ewma_seasonal` | 2,026 | 2,509 | 4,151 |

**MAPE falls with horizon while terminal error rises**, and both are reported
because neither alone answers "is forecasting further out harder":

* MAPE divides by a balance that *grows* for a saver, so the percentage improves
  while the projection gets worse in rupees.
* Mean-absolute-error, tried next, also falls — it averages over every day in
  the horizon, and the dominant error is lumpy (whether a ₹95,000 salary lands
  inside the window), which a 30-day mean feels far more than a 90-day one.

Terminal error has neither problem and is what a user reads off the chart.

### The bug this harness found

The first honest run — feeding the tiers the scheduled commitments they get in
production — scored **77% MAPE at 90 days**. The cause was a double count: the
tiers lay commitments over a statistical baseline, and that baseline was built
from history still containing salary and rent. The demo persona's projection
implied ₹89,000/month of saving against a ~₹95,000 income.

No unit test would have caught it. Each half was individually correct; only
running them together against a known outcome exposed it. That is the argument
for a backtest existing at all.

### Caveats on these numbers

Synthetic personas, generated from a seeded RNG, and the harness hands the
forecaster exactly the right commitments. Real ledgers are messier and real
detection is imperfect, so **treat these as an upper bound on quality**. What
the harness proves is relative and does hold: tier 2 beats tier 1, both beat
doing nothing by an order of magnitude, and error grows with horizon.

Regression ceilings: `MAX_TIER1_MAPE = 8.0`, `MAX_TIER2_MAPE = 5.0`.

Tier 3 (Prophet) is not backtested here — it is absent from the API image by
design, so the harness cannot import it. Its fit takes ~0.5 s on 339 days in the
worker.

---

## Financial health rubric (M6)

Not an eval in the statistical sense — the rubric is deterministic, so there is
nothing to measure against a held-out set. What is pinned instead is its
*arithmetic* and its *wording*, in `backend/tests/unit/test_health_golden.py`.

| Persona | History | Score | Risk | Factors used |
|---|---|---|---|---|
| Prudent saver | 365d, 420 txns | **87.25** | low | 6 of 6 |
| Stretched borrower | 365d, 380 txns | **29.50** | high | 6 of 6 |
| Newcomer | 44d, 38 txns | **75.00** | moderate | 3 of 6 |

Three properties are asserted rather than observed:

* **Weights sum to 1.00 and contributions sum to the score, exactly.** Not
  "within tolerance" — the score is *defined* as the sum of its parts, and a
  rubric whose parts do not reconstruct the whole is a decoration with a number
  attached (ADR-002).
* **Under 30 days of history there is no score at all.** Not a low score: an
  `Explanation` with no score, no factors, and a caveat saying why.
* **A high score on thin evidence is capped at moderate risk.** The newcomer
  scores 75, which would normally read as low risk, at 12% confidence with half
  the metrics unmeasurable. Unearned reassurance is the one thing a financial
  tool must not overclaim.

The golden tests will fail whenever a weight, band, or user-facing sentence
changes. That is the point — the failure is the review prompt, and updating the
expectation in the same commit is what makes a rubric change legible.

---

## Receipt OCR (M4)

`backend/tests/eval/test_ocr_accuracy.py`
20 labelled synthetic fixtures, seven degradation modes.

| Field | Accuracy | Recall | Precision |
|---|---|---|---|
| merchant | 90.0% | 100.0% | 90.0% |
| date | 55.0% | 55.0% | 100.0% |
| total | 75.0% | 100.0% | 75.0% |
| subtotal | 30.0% | 95.0% | 31.6% |
| tax | 60.0% | 85.0% | 70.6% |

| Degradation | Required-field accuracy |
|---|---|
| clean | 100.0% |
| low_contrast | 88.9% |
| uneven_light | 77.8% |
| blurred | 66.7% |
| noisy | 66.7% |
| perspective | 66.7% |
| rotated | 44.4% |

At least one field correct: **95.0%**.

These are **synthetic** receipts. Real thermal print reads worse; treat this as
an upper bound and watch the trend rather than the absolute value. `rotated` at
44.4% is the clearest known weakness — deskewing is the obvious next
improvement, and the harness is what will prove whether it worked.
