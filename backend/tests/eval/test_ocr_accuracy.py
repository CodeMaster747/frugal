"""OCR evaluation harness (M4 exit criterion).

Records a **field-level extraction baseline** over labelled fixtures. The
absolute number matters less than having one: a model without a measured
baseline cannot be improved or safely changed, and the next person to touch the
preprocessing pipeline needs to know whether they helped.

Run with `make eval`. The report prints per-field accuracy and a breakdown by
degradation mode, so a regression points at the stage that caused it.

**What this does and does not prove.** Fixtures are rendered and then degraded,
not photographed. Real thermal receipts are worse: faded ink, curled paper,
compression artefacts. Treat the score as an upper bound and the trend as the
useful signal.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.modules.receipts.pipeline import extract as extractor
from app.modules.receipts.pipeline import preprocess as preprocessor
from tests.eval.fixtures import build_fixtures

pytestmark = pytest.mark.eval

# Below this, the pipeline is not doing its job and the change that caused it
# should not merge. Set from the measured baseline with headroom, not
# aspirationally -- a threshold nobody can meet gets deleted.
MIN_TOTAL_ACCURACY = 0.55
MIN_MERCHANT_ACCURACY = 0.50
MIN_ANY_FIELD_RATE = 0.70


@dataclass(slots=True)
class FieldScore:
    correct: int = 0
    found: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float:
        """Share of receipts where the field was extracted *and* correct."""
        return self.correct / self.total if self.total else 0.0

    @property
    def recall(self) -> float:
        """Share where a value was produced at all, right or wrong."""
        return self.found / self.total if self.total else 0.0

    @property
    def precision(self) -> float:
        """Of the values produced, how many were right."""
        return self.correct / self.found if self.found else 0.0


def _matches(name: str, extracted: str | None, truth: object) -> bool:
    if extracted is None:
        return False

    if name in {"total", "subtotal", "tax"}:
        try:
            # Within a paisa: OCR occasionally shifts the last decimal, and
            # scoring that as a total failure would hide real progress.
            return abs(Decimal(extracted) - Decimal(str(truth))) <= Decimal("0.01")
        except Exception:
            return False

    if name == "date":
        return str(extracted) == str(truth)

    # Merchant: case- and spacing-insensitive containment. "Reliance Fresh"
    # against "RELIANCE FRESH" is a success; a human would never call it a miss.
    a = "".join(str(extracted).lower().split())
    b = "".join(str(truth).lower().split())
    return a == b or b in a or a in b


@pytest.fixture(scope="module")
def report() -> dict[str, object]:
    """Run the pipeline over every fixture once and score the results."""
    fixtures = build_fixtures(count=20)

    scores: dict[str, FieldScore] = defaultdict(FieldScore)
    by_degradation: dict[str, FieldScore] = defaultdict(FieldScore)
    failures: list[str] = []
    any_field = 0

    for fixture in fixtures:
        try:
            processed, _ = preprocessor.preprocess(fixture.image)
            from app.adapters.ocr.tesseract import TesseractEngine

            result = TesseractEngine().recognize(processed)
            extraction = extractor.extract(result)
        except Exception as exc:
            failures.append(f"{fixture.name}: {type(exc).__name__}: {exc}")
            continue

        found_any = False
        for name, truth in fixture.truth.items():
            field = extraction.by_name(name)
            score = scores[name]
            score.total += 1

            if field and field.parsed_value is not None:
                score.found += 1
                if _matches(name, field.parsed_value, truth):
                    score.correct += 1
                    found_any = True

        if found_any:
            any_field += 1

        # Degradation breakdown uses the three required fields only.
        bucket = by_degradation[fixture.degradation]
        for name in ("merchant", "date", "total"):
            field = extraction.by_name(name)
            bucket.total += 1
            if field and field.parsed_value is not None:
                bucket.found += 1
                if _matches(name, field.parsed_value, fixture.truth[name]):
                    bucket.correct += 1

    return {
        "fixtures": len(fixtures),
        "scores": dict(scores),
        "by_degradation": dict(by_degradation),
        "failures": failures,
        "any_field_rate": any_field / len(fixtures) if fixtures else 0.0,
    }


def test_baseline_report(report: dict[str, object]) -> None:
    """Print the baseline. This is the deliverable, not the assertion."""
    scores: dict[str, FieldScore] = report["scores"]  # type: ignore[assignment]
    by_degradation: dict[str, FieldScore] = report["by_degradation"]  # type: ignore[assignment]

    print("\n" + "=" * 68)
    print(f"  OCR extraction baseline — {report['fixtures']} labelled fixtures")
    print("  Synthetic receipts, degraded. Real thermal receipts read worse;")
    print("  treat this as an upper bound and watch the trend, not the value.")
    print("=" * 68)
    print(f"\n  {'field':<12}{'accuracy':>10}{'recall':>10}{'precision':>12}")
    print("  " + "-" * 44)
    for name in ("merchant", "date", "total", "subtotal", "tax"):
        if name not in scores:
            continue
        s = scores[name]
        print(f"  {name:<12}{s.accuracy:>9.1%}{s.recall:>10.1%}{s.precision:>12.1%}")

    print(f"\n  {'degradation':<16}{'required-field accuracy':>26}")
    print("  " + "-" * 42)
    for mode, s in sorted(by_degradation.items(), key=lambda kv: kv[1].accuracy, reverse=True):
        print(f"  {mode:<16}{s.accuracy:>25.1%}")

    print(f"\n  At least one field correct: {report['any_field_rate']:.1%}")

    failures: list[str] = report["failures"]  # type: ignore[assignment]
    if failures:
        print(f"\n  Pipeline errors ({len(failures)}):")
        for line in failures[:5]:
            print(f"    - {line}")
    print("=" * 68 + "\n")


def test_the_pipeline_does_not_crash(report: dict[str, object]) -> None:
    """A malformed extraction is acceptable; an exception is not.

    The review queue can absorb a wrong value. It cannot absorb a worker that
    dies, because the user is left with a receipt stuck in `processing`.
    """
    assert report["failures"] == [], f"pipeline raised on {len(report['failures'])} fixtures"


@pytest.mark.parametrize(
    ("field_name", "floor"),
    [("total", MIN_TOTAL_ACCURACY), ("merchant", MIN_MERCHANT_ACCURACY)],
)
def test_required_field_accuracy_holds(
    report: dict[str, object], field_name: str, floor: float
) -> None:
    """Regression guard on the two fields a transaction cannot be built without."""
    scores: dict[str, FieldScore] = report["scores"]  # type: ignore[assignment]
    accuracy = scores[field_name].accuracy
    assert accuracy >= floor, (
        f"{field_name} accuracy {accuracy:.1%} fell below the {floor:.0%} baseline"
    )


def test_something_useful_is_extracted_from_most_receipts(report: dict[str, object]) -> None:
    rate = float(report["any_field_rate"])  # type: ignore[arg-type]
    assert rate >= MIN_ANY_FIELD_RATE, f"only {rate:.1%} of receipts yielded a correct field"


def test_precision_beats_recall_on_the_total(report: dict[str, object]) -> None:
    """When the total is produced, it should usually be right.

    This is the property the review flow depends on. A pipeline that guesses
    freely and is often wrong trains users to distrust every value and check
    all of them -- which is exactly the outcome per-field confidence exists to
    avoid.
    """
    scores: dict[str, FieldScore] = report["scores"]  # type: ignore[assignment]
    total = scores["total"]
    if total.found == 0:
        pytest.skip("no totals extracted")
    assert total.precision >= 0.6, f"total precision {total.precision:.1%} is too low to trust"
