"""Categoriser evaluation harness (M5 exit criterion).

Reports accuracy, macro-F1, and per-class confusion for the categoriser.

**Macro-F1, not accuracy, is the number that matters.** Accuracy is dominated by
the frequent classes: a model that answers `shopping` for everything scores
respectably on a corpus where shopping is common, while being useless. Macro-F1
weights every category equally, so a class the model never predicts drags it down
immediately -- which is exactly the failure worth catching.

**Two tiers, because they answer different questions.** A first version of this
harness scored 100%, which was not a good result -- it meant the test was wrong.
Every "held-out" merchant contained a seed brand as a substring (`swiggy
instamart order` contains `swiggy`), so character n-grams were doing dictionary
lookup and the score measured memorisation. Split since into:

* **Tier A — noisy narrations of known merchants.** Real bank narrations, mangled
  the way banks mangle them. Run through the whole pipeline, rules included,
  because that is what production does. This is the number that predicts how the
  product feels day to day, and it should be high.
* **Tier B — merchants absent from the corpus entirely.** Model only; the rules
  layer cannot help by construction. This is the model's actual contribution,
  and it is a much harder problem, so it scores far lower. Reporting it is the
  point -- the alternative is an inflated number that hides the day a real user
  shops somewhere we have never heard of.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.modules.categorization import rules
from app.modules.categorization.classifier import MerchantClassifier
from app.modules.categorization.seed_corpus import training_pairs
from app.modules.finance.service import normalize_merchant

pytestmark = pytest.mark.eval

#: Floors set from the measured baseline with headroom, per tier. Taken from what
#: the categoriser actually does rather than what would be nice -- a threshold
#: nobody can meet gets deleted rather than fixed.
MIN_KNOWN_ACCURACY = 0.85
MIN_UNSEEN_MACRO_F1 = 0.30
#: Every accepted suggestion must be right this often. Precision is the metric
#: that matters for a *suggestion*: it lands unreviewed in the queue, so a wrong
#: one costs a click, but a stream of wrong ones costs trust in the feature.
MIN_UNSEEN_PRECISION = 0.85

#: Tier A: merchants we know, wearing the narration formats a bank actually
#: emits -- UPI handles, POS prefixes, reference numbers, missing spaces, case
#: noise. The brand is in the corpus; the question is whether normalisation and
#: the rules layer can still find it.
KNOWN_NOISY: list[tuple[str, str]] = [
    ("UPI/SWIGGY*ORDER/HDFC0001234", "food-delivery"),
    ("POS 4412 DMART AVENUE SUPERMA", "groceries"),
    ("upi-zomato ltd-zomato@ybl-1234", "food-delivery"),
    ("BIGBASKET  ONLINE  BLR", "groceries"),
    ("NEFT-BLINKIT COMMERCE PVT LTD", "groceries"),
    ("POS/AMAZON SELLER SERVICES/MUM", "shopping"),
    ("FLIPKART INTERNET PVT LTD 9987", "shopping"),
    ("UPI/MYNTRA*DESIGNS/AXIS", "shopping"),
    ("IOCL PETROL PUMP KORAMANGALA", "fuel"),
    ("HPCL-FUEL-STATION-BLR-002", "fuel"),
    ("UPI/UBER INDIA SYSTEMS/ICICI", "ride-hailing"),
    ("OLA*CABS RIDE 20240712", "ride-hailing"),
    ("NETFLIX.COM  SUBSCRIPTION", "subscriptions"),
    ("SPOTIFY  P30ABC123  STOCKHOLM", "subscriptions"),
    ("AMAZON PRIME*MEMBERSHIP JUL", "subscriptions"),
    ("APOLLO PHARMACY LTD BLR 0091", "healthcare"),
    ("upi/pharmeasy*axelia/hdfc", "healthcare"),
    ("BESCOM ELECTRICITY BILL PAYMENT", "utilities"),
    ("TATA POWER DDL PREPAID", "utilities"),
    ("ACT FIBERNET BROADBAND JUL", "internet"),
    ("AIRTEL PREPAID RECHARGE 299", "mobile"),
    ("BOOKMYSHOW TICKETS PVR FORUM", "entertainment"),
    ("MAKEMYTRIP INDIA PVT LTD", "travel"),
    ("INDIGO*6E AIRLINE BOOKING", "travel"),
    ("ZERODHA BROKING LTD ACH DEBIT", "savings-investment"),
    ("GROWW INVEST TECH SIP", "savings-investment"),
    ("HDFC HOME LOAN EMI 041223", "loan-emi"),
    ("BAJAJ FINSERV EMI AUTO DEBIT", "loan-emi"),
    ("STARBUCKS COFFEE INDIA MG RD", "dining-out"),
    ("CROMA RETAIL A TATA ENTERPRISE", "electronics"),
]

#: Tier B: brands genuinely not in the seed corpus. Nothing here can be matched
#: by rules, so this measures generalisation alone -- can the model place a name
#: it has never seen, from character shape and category vocabulary?
UNSEEN: list[tuple[str, str]] = [
    ("jiomart groceries", "groceries"),
    ("vishal mega mart", "groceries"),
    ("ratnadeep super market", "groceries"),
    ("wow momo foods", "food-delivery"),
    ("la pinoz pizza", "food-delivery"),
    ("behrouz biryani", "food-delivery"),
    ("truffles restaurant", "dining-out"),
    ("the bombay canteen", "dining-out"),
    ("essar oil fuel station", "fuel"),
    ("jio bp petrol", "fuel"),
    ("blusmart mobility", "ride-hailing"),
    ("meru cabs", "ride-hailing"),
    ("tata cliq luxury", "shopping"),
    ("snapdeal order", "shopping"),
    ("firstcry baby store", "shopping"),
    ("sangeetha mobiles", "electronics"),
    ("poorvika mobile world", "electronics"),
    ("medplus pharmacy", "healthcare"),
    ("wellness forever medicare", "healthcare"),
    ("cloudnine hospital", "healthcare"),
    ("sonyliv premium", "subscriptions"),
    ("zee5 subscription", "subscriptions"),
    ("torrent power ltd", "utilities"),
    ("kseb electricity", "utilities"),
    ("tikona broadband", "internet"),
    ("spectra fiber internet", "internet"),
    ("bsnl mobile recharge", "mobile"),
    ("carnival cinemas", "entertainment"),
    ("spicejet airlines", "travel"),
    ("treebo hotels booking", "travel"),
    ("angel one broking", "savings-investment"),
    ("smallcase invest", "savings-investment"),
    ("kreditbee loan emi", "loan-emi"),
    ("vedantu learning fees", "education"),
    ("simplilearn course", "education"),
    ("acko general insurance", "insurance"),
    ("digit insurance premium", "insurance"),
    ("msrtc bus ticket", "public-transport"),
    ("ketto fundraiser donation", "gifts-donations"),
    ("monthly remuneration credit", "salary"),
]


def _score(
    cases: list[tuple[str, str]],
    predict: object,
) -> dict[str, object]:
    """Accuracy, macro-F1, per-class F1 and confusion for one tier."""
    per_class: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0, "support": 0}
    )
    confusion: list[tuple[str, str, str]] = []
    correct = 0
    abstained = 0

    for raw, truth in cases:
        merchant = normalize_merchant(raw) or raw.lower()
        predicted = predict(merchant)  # type: ignore[operator]

        per_class[truth]["support"] += 1
        if predicted == truth:
            correct += 1
            per_class[truth]["tp"] += 1
        else:
            per_class[truth]["fn"] += 1
            if predicted:
                per_class[predicted]["fp"] += 1
                confusion.append((raw, truth, predicted))
            else:
                abstained += 1

    f1_by_class: dict[str, float] = {}
    for label, c in per_class.items():
        precision = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 0.0
        recall = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 0.0
        f1_by_class[label] = (
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )

    return {
        "n": len(cases),
        "accuracy": correct / len(cases),
        "macro_f1": sum(f1_by_class.values()) / len(f1_by_class) if f1_by_class else 0.0,
        "f1_by_class": f1_by_class,
        "confusion": confusion,
        "abstained": abstained,
    }


#: The value the application ships, read from the same settings the service uses
#: so the harness can never drift from what production does.
SHIPPED_THRESHOLD = get_settings().categorization_confidence_threshold


@pytest.fixture(scope="module")
def report() -> dict[str, object]:
    classifier = MerchantClassifier.fit(training_pairs())
    threshold = Decimal(str(SHIPPED_THRESHOLD))

    def full_pipeline(merchant: str) -> str | None:
        """Rules first, then the model -- what production actually runs."""
        rule = rules.match(merchant)
        if rule:
            return rule.slug
        prediction = classifier.predict(merchant, min_confidence=threshold)
        return prediction.slug if prediction else None

    def model_only(merchant: str) -> str | None:
        prediction = classifier.predict(merchant, min_confidence=threshold)
        return prediction.slug if prediction else None

    def model_unthresholded(merchant: str) -> str | None:
        raw = classifier.predict_raw(merchant)
        return raw[0] if raw else None

    def curve() -> list[tuple[float, float, float]]:
        """(threshold, coverage, precision) over the unseen set."""
        points: list[tuple[float, float, float]] = []
        for step in (0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60):
            accepted = 0
            correct = 0
            for raw, truth in UNSEEN:
                merchant = normalize_merchant(raw) or raw.lower()
                prediction = classifier.predict(merchant, min_confidence=Decimal(str(step)))
                if prediction is not None:
                    accepted += 1
                    correct += prediction.slug == truth
            points.append((step, accepted / len(UNSEEN), correct / accepted if accepted else 0.0))
        return points

    return {
        "version": classifier.version,
        "train_size": len(training_pairs()),
        "curve": curve(),
        "known": _score(KNOWN_NOISY, full_pipeline),
        "unseen": _score(UNSEEN, model_only),
        # Without the threshold, to separate "the model was wrong" from "the
        # model was correctly unsure". Those two demand opposite fixes.
        "unseen_forced": _score(UNSEEN, model_unthresholded),
    }


def _print_tier(name: str, note: str, tier: dict[str, object]) -> None:
    print(f"\n  {name}  ({tier['n']} cases) — {note}")
    print("  " + "-" * 62)
    print(f"  accuracy   {float(tier['accuracy']):.1%}")  # type: ignore[arg-type]
    print(f"  macro-F1   {float(tier['macro_f1']):.3f}")  # type: ignore[arg-type]
    print(f"  abstained  {tier['abstained']}")

    confusion: list[tuple[str, str, str]] = tier["confusion"]  # type: ignore[assignment]
    if confusion:
        print(f"\n  misclassified ({len(confusion)}):")
        for merchant, truth, predicted in confusion[:10]:
            print(f"    {merchant[:30]:<32} {truth}  ->  {predicted}")


def test_baseline_report(report: dict[str, object]) -> None:
    """Print the baseline. The report is the deliverable; the floors below are
    only there to catch a regression against it."""
    print("\n" + "=" * 68)
    print("  Merchant categorisation baseline")
    print(f"  model {report['version']}  ({report['train_size']} seed pairs)")
    print("=" * 68)

    _print_tier(
        "TIER A · known merchants, noisy narrations",
        "full pipeline (rules -> model)",
        report["known"],  # type: ignore[arg-type]
    )
    _print_tier(
        "TIER B · merchants absent from the corpus",
        "model only; rules cannot help",
        report["unseen"],  # type: ignore[arg-type]
    )

    forced: dict[str, object] = report["unseen_forced"]  # type: ignore[assignment]
    unseen: dict[str, object] = report["unseen"]  # type: ignore[assignment]
    print(
        f"\n  Tier B without the confidence threshold: {float(forced['accuracy']):.1%} "  # type: ignore[arg-type]
        f"(vs {float(unseen['accuracy']):.1%} with it)"  # type: ignore[arg-type]
    )
    print(
        f"  The gap is the threshold's cost: {unseen['abstained']} of {unseen['n']} left "
        "uncategorised rather than guessed."
    )
    print("=" * 68 + "\n")


def test_known_merchants_stay_reliable(report: dict[str, object]) -> None:
    """The everyday case. Mangled narrations of merchants we know must resolve."""
    tier: dict[str, object] = report["known"]  # type: ignore[assignment]
    score = float(tier["accuracy"])  # type: ignore[arg-type]
    assert score >= MIN_KNOWN_ACCURACY, (
        f"accuracy on known merchants fell to {score:.1%}, below {MIN_KNOWN_ACCURACY:.0%}"
    )


def test_unseen_merchants_beat_chance(report: dict[str, object]) -> None:
    """Generalisation to brands we have never seen.

    A far lower bar, honestly set: with 23 classes, chance is ~4%. The floor here
    says the model is doing real work on unseen names, not that it is good at it.
    Raising this is what M5's follow-up work would target.
    """
    tier: dict[str, object] = report["unseen"]  # type: ignore[assignment]
    score = float(tier["macro_f1"])  # type: ignore[arg-type]
    assert score >= MIN_UNSEEN_MACRO_F1, (
        f"macro-F1 on unseen merchants fell to {score:.3f}, below {MIN_UNSEEN_MACRO_F1}"
    )


def test_the_threshold_is_calibrated(report: dict[str, object]) -> None:
    """Sweep the precision/coverage curve and check the shipped threshold sits
    on a defensible point of it.

    This is the test that found the original 0.60 was wrong. It accepted 8% of
    predictions on unseen merchants -- all correct, and almost never fired.
    Printing the curve is the deliverable: it turns "why 0.30?" from taste into
    a table.

    Calibrated on 40 hand-labelled merchants, which is small. It is enough to
    reject an order-of-magnitude error like 0.60, not enough to defend 0.30 over
    0.28; treat the shipped value as the best point on evidence we have.
    """
    curve: list[tuple[float, float, float]] = report["curve"]  # type: ignore[assignment]

    print("\n  threshold calibration (unseen merchants, model only)")
    print("  " + "-" * 44)
    print(f"  {'thresh':>8}{'coverage':>12}{'precision':>13}")
    for threshold, coverage, precision in curve:
        marker = "  <- shipped" if abs(threshold - SHIPPED_THRESHOLD) < 1e-9 else ""
        print(f"  {threshold:>8.2f}{coverage:>11.0%}{precision:>13.0%}{marker}")
    print()

    shipped = next(row for row in curve if abs(row[0] - SHIPPED_THRESHOLD) < 1e-9)
    _, coverage, precision = shipped
    assert precision >= MIN_UNSEEN_PRECISION, (
        f"at the shipped threshold {SHIPPED_THRESHOLD}, precision is {precision:.0%} -- "
        "the suggestions are wrong too often to be worth showing"
    )
    assert coverage >= 0.20, (
        f"at the shipped threshold {SHIPPED_THRESHOLD}, only {coverage:.0%} of unseen "
        "merchants get any suggestion; the model is barely contributing"
    )


def test_the_threshold_prefers_silence_to_a_wrong_answer(report: dict[str, object]) -> None:
    """The confidence threshold must actually suppress low-confidence guesses.

    If it never abstains it is not doing its job, and FR-5.4 -- uncategorised
    beats miscategorised -- is unenforced.
    """
    tier: dict[str, object] = report["unseen"]  # type: ignore[assignment]
    assert int(tier["abstained"]) > 0, (  # type: ignore[arg-type,call-overload]
        "the threshold never abstained on unseen merchants; it is set too low to matter"
    )


def test_the_rules_layer_covers_every_seed_merchant() -> None:
    """Anything in the seed corpus resolves without touching the model.

    A dictionary lookup is exact, instant, and explainable; running inference for
    a merchant we already know would be slower and less certain.
    """
    misses = [merchant for merchant, _ in training_pairs() if rules.match(merchant) is None]
    assert misses == [], f"seed merchants not matched by the rules layer: {misses[:5]}"


def test_a_user_rule_beats_the_seed_corpus() -> None:
    """A correction is definitional. If a user says Swiggy is groceries for them,
    no amount of model confidence should override it."""
    match = rules.match("swiggy", {"swiggy": "groceries"})
    assert match is not None
    assert match.slug == "groceries"
    assert match.is_certain


def test_the_more_specific_pattern_wins() -> None:
    """`amazon prime` is a subscription; a bare `amazon` is shopping."""
    assert rules.match("amazon prime video jul").slug == "subscriptions"  # type: ignore[union-attr]
    assert rules.match("amazon seller services").slug == "shopping"  # type: ignore[union-attr]
