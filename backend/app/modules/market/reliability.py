"""Seller Reliability Score (FR-9.2).

Pure. No session, no clock, no I/O.

**Read this before changing anything here.** The original brief asked for
"Review Authenticity" and "Scam Risk" scoring. Both were removed in planning and
the reasons are legal as much as technical:

* Fake-review detection is not honestly achievable at this data scale. A model
  that guesses would be wrong often, and confidently.
* Publishing a "scam risk" label about a **named commercial seller** is a
  defamation exposure. Not a hypothetical one: it is an assertion of fact about
  a real business, made by software, with no evidence a court would accept.

What replaces it is deliberately narrower and defensible: a score built only
from **signals the seller themselves publish**, each of which the user could
verify in a minute. The output describes the *offer*, never the seller's
character. There is no "risky seller" band and no accusation anywhere in this
file — the worst thing it says is "fewer protections than usual", which is a
statement about a return window, not about anyone's honesty.

The rubric is published in-product for the same reason the others are: a score
that shapes a purchase decision and cannot be interrogated is an accusation
wearing a number's clothes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

RUBRIC_VERSION = "v1"

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class SignalKey(StrEnum):
    RATING = "rating"
    RATING_VOLUME = "rating_volume"
    RETURN_WINDOW = "return_window"
    WARRANTY = "warranty"
    FULFILMENT = "fulfilment"
    PRICE_DEVIATION = "price_deviation"


@dataclass(frozen=True, slots=True)
class Band:
    threshold: Decimal
    points: Decimal
    label: str


@dataclass(frozen=True, slots=True)
class Signal:
    key: SignalKey
    name: str
    weight: Decimal
    bands: tuple[Band, ...]
    higher_is_better: bool = True

    def score(self, raw: Decimal) -> tuple[Decimal, str]:
        for band in self.bands:
            hit = raw >= band.threshold if self.higher_is_better else raw <= band.threshold
            if hit:
                return band.points, band.label
        return ZERO, self.bands[-1].label


#: Weights sum to 1.00, asserted by test.
#:
#: Return window and warranty carry the most weight because they are the two
#: things that actually protect a buyer when something goes wrong, and they are
#: contractual rather than reputational — a 30-day return policy is a fact, a
#: 4.2-star average is an aggregate of opinions.
SIGNALS: tuple[Signal, ...] = (
    Signal(
        key=SignalKey.RETURN_WINDOW,
        name="Return window",
        weight=Decimal("0.25"),
        bands=(
            Band(Decimal("30"), HUNDRED, "30 days or more"),
            Band(Decimal("14"), Decimal("80"), "two weeks"),
            Band(Decimal("7"), Decimal("55"), "one week"),
            Band(Decimal("1"), Decimal("25"), "very short"),
            Band(ZERO, ZERO, "no returns offered"),
        ),
    ),
    Signal(
        key=SignalKey.WARRANTY,
        name="Warranty",
        weight=Decimal("0.20"),
        bands=(
            Band(Decimal("24"), HUNDRED, "two years or more"),
            Band(Decimal("12"), Decimal("85"), "one year"),
            Band(Decimal("6"), Decimal("55"), "six months"),
            Band(Decimal("1"), Decimal("30"), "limited"),
            Band(ZERO, ZERO, "none stated"),
        ),
    ),
    Signal(
        key=SignalKey.RATING,
        name="Seller rating",
        weight=Decimal("0.18"),
        bands=(
            Band(Decimal("4.5"), HUNDRED, "4.5 and above"),
            Band(Decimal("4.0"), Decimal("80"), "4.0 to 4.5"),
            Band(Decimal("3.5"), Decimal("55"), "3.5 to 4.0"),
            Band(Decimal("3.0"), Decimal("30"), "3.0 to 3.5"),
            Band(ZERO, Decimal("10"), "below 3.0"),
        ),
    ),
    Signal(
        key=SignalKey.RATING_VOLUME,
        name="Number of ratings",
        weight=Decimal("0.15"),
        # Volume, because a 5.0 from nine people says almost nothing. This is
        # the signal that keeps a thin-but-perfect record from outranking a
        # long-but-imperfect one.
        bands=(
            Band(Decimal("5000"), HUNDRED, "thousands of ratings"),
            Band(Decimal("1000"), Decimal("85"), "over a thousand"),
            Band(Decimal("200"), Decimal("60"), "a few hundred"),
            Band(Decimal("50"), Decimal("35"), "a few dozen"),
            Band(ZERO, Decimal("10"), "very few"),
        ),
    ),
    Signal(
        key=SignalKey.FULFILMENT,
        name="Who ships it",
        weight=Decimal("0.12"),
        # Scored as a rank: brand-direct 3, platform-fulfilled 2, third-party 1.
        # Not a judgement about third-party sellers -- it reflects who handles a
        # return, which is a different and observable question.
        bands=(
            Band(Decimal("3"), HUNDRED, "direct from the brand"),
            Band(Decimal("2"), Decimal("80"), "fulfilled by the platform"),
            Band(Decimal("1"), Decimal("50"), "shipped by the seller"),
            Band(ZERO, Decimal("30"), "not stated"),
        ),
    ),
    Signal(
        key=SignalKey.PRICE_DEVIATION,
        name="Price against the market",
        weight=Decimal("0.10"),
        higher_is_better=False,
        # How far *below* the median across sellers this offer sits, as a
        # fraction. A genuine sale is a few percent; 60% below every other
        # seller is the honest proxy for the risk the original "scam score" was
        # reaching for -- and it is a statement about a number, not a person.
        bands=(
            Band(Decimal("0.10"), HUNDRED, "in line with the market"),
            Band(Decimal("0.25"), Decimal("75"), "a genuine-looking discount"),
            Band(Decimal("0.40"), Decimal("45"), "well below other sellers"),
            Band(Decimal("0.60"), Decimal("20"), "far below other sellers"),
            Band(Decimal("99"), Decimal("5"), "implausibly low"),
        ),
    ),
)

BY_KEY: dict[SignalKey, Signal] = {s.key: s for s in SIGNALS}

FULFILMENT_RANK: dict[str, Decimal] = {
    "brand_direct": Decimal("3"),
    "platform": Decimal("2"),
    "third_party": Decimal("1"),
}


class Confidence(StrEnum):
    """How much of the rubric could actually be measured."""

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class ScoredSignal:
    key: SignalKey
    name: str
    value: str
    raw_value: Decimal
    weight: Decimal
    contribution: Decimal
    points: Decimal
    label: str
    detail: str


@dataclass(frozen=True, slots=True)
class Reliability:
    score: Decimal
    band: str
    confidence: Confidence
    signals: tuple[ScoredSignal, ...]
    caveats: tuple[str, ...]
    rubric_version: str = RUBRIC_VERSION


#: Score bands. Deliberately worded about the *offer*, never the seller.
#:
#: "Fewer protections than usual" is a statement about a return window. "Risky
#: seller" would be a statement about a business, and this product does not make
#: those.
BANDS: tuple[tuple[Decimal, str], ...] = (
    (Decimal("80"), "well protected"),
    (Decimal("60"), "reasonably protected"),
    (Decimal("40"), "fewer protections than usual"),
    (ZERO, "few protections — check the listing carefully"),
)


def band_for(score: Decimal, confidence: Confidence = Confidence.HIGH) -> str:
    """The band, capped by how much of the rubric could be measured.

    A listing that states only a return window can score 80 on that one signal,
    and calling it "well protected" on that basis is the same overclaim the
    health engine makes when it calls a six-week-old account low-risk. The
    errors are asymmetric in the same way: unearned reassurance about a purchase
    invites someone to skip a check they would otherwise have made.
    """
    label = next((label for threshold, label in BANDS if score >= threshold), BANDS[-1][1])

    if confidence is Confidence.LOW and label == BANDS[0][1]:
        return BANDS[1][1]
    return label


def total_weight() -> Decimal:
    return sum((s.weight for s in SIGNALS), ZERO)


def _describe(key: SignalKey, raw: Decimal, label: str) -> tuple[str, str]:
    """Display value and a plain-language reason.

    Every string here describes the listing. None of them characterises the
    seller.
    """
    if key is SignalKey.RETURN_WINDOW:
        days = int(raw)
        return (
            f"{days} days" if days else "none",
            f"Returns accepted for {days} days."
            if days
            else "This listing states no return window.",
        )
    if key is SignalKey.WARRANTY:
        months = int(raw)
        return (
            f"{months} months" if months else "none stated",
            f"Covered by a {months}-month warranty."
            if months
            else "No warranty is stated on this listing.",
        )
    if key is SignalKey.RATING:
        return f"{raw}", f"Rated {raw} by buyers ({label})."
    if key is SignalKey.RATING_VOLUME:
        return f"{int(raw):,}", f"Based on {int(raw):,} ratings — {label}."
    if key is SignalKey.FULFILMENT:
        return label, f"Order handling: {label}. This is who deals with a return."
    return (
        f"{(raw * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}% below median",
        f"Priced {(raw * 100).quantize(Decimal('0.1'))}% under the median across sellers "
        f"— {label}.",
    )


def score_offer(
    *,
    seller_rating: Decimal | None,
    rating_count: int | None,
    return_window_days: int | None,
    warranty_months: int | None,
    fulfillment_type: str | None,
    price: Decimal,
    market_median: Decimal | None,
) -> Reliability:
    """Score one offer on the signals it publishes.

    A missing signal is **excluded**, not scored zero. A listing that does not
    state a warranty has not told us there is none; treating silence as a
    failing would penalise sellers for the platform's data quality and would
    make the score say something it cannot support. The weight is redistributed
    and the omission becomes a caveat, exactly as in the health rubric.
    """
    raws: dict[SignalKey, Decimal | None] = {
        SignalKey.RATING: seller_rating,
        SignalKey.RATING_VOLUME: Decimal(rating_count) if rating_count is not None else None,
        SignalKey.RETURN_WINDOW: (
            Decimal(return_window_days) if return_window_days is not None else None
        ),
        SignalKey.WARRANTY: Decimal(warranty_months) if warranty_months is not None else None,
        SignalKey.FULFILMENT: (
            FULFILMENT_RANK.get(fulfillment_type or "") if fulfillment_type else None
        ),
        SignalKey.PRICE_DEVIATION: (
            max(ZERO, (market_median - price) / market_median)
            if market_median and market_median > 0
            else None
        ),
    }

    available = [(signal, raws[signal.key]) for signal in SIGNALS if raws[signal.key] is not None]
    missing = [signal for signal in SIGNALS if raws[signal.key] is None]

    caveats = [
        f"{signal.name} is not stated on this listing, so it was left out of the score."
        for signal in missing
    ]

    if not available:
        return Reliability(
            score=ZERO,
            band="not enough information",
            confidence=Confidence.LOW,
            signals=(),
            caveats=("This listing publishes none of the signals this score is built from.",),
        )

    measured_weight = sum((signal.weight for signal, _ in available), ZERO)
    scale = Decimal(1) / measured_weight

    # Redistributed weights are irrational in general, so the remainder goes to
    # the heaviest -- the same fix as the health rubric, for the same reason: a
    # published "weights sum to 1.00" must be true for every offer, not most.
    weights = [
        (signal.weight * scale).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        for signal, _ in available
    ]
    drift = Decimal(1) - sum(weights, ZERO)
    if drift:
        weights[weights.index(max(weights))] += drift

    scored: list[ScoredSignal] = []
    total = ZERO
    for (signal, raw), weight in zip(available, weights, strict=True):
        assert raw is not None
        points, label = signal.score(raw)
        contribution = (points * weight).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        display, detail = _describe(signal.key, raw, label)

        total += contribution
        scored.append(
            ScoredSignal(
                key=signal.key,
                name=signal.name,
                value=display,
                raw_value=raw,
                weight=weight,
                contribution=contribution,
                points=points,
                label=label,
                detail=detail,
            )
        )

    score = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    remainder = score - sum((s.contribution for s in scored), ZERO)
    if remainder and scored:
        biggest = max(range(len(scored)), key=lambda i: scored[i].contribution)
        original = scored[biggest]
        scored[biggest] = _replace_contribution(original, original.contribution + remainder)

    confidence = (
        Confidence.HIGH
        if len(missing) == 0
        else Confidence.MODERATE
        if len(missing) <= 2
        else Confidence.LOW
    )

    if confidence is Confidence.LOW:
        caveats.append(
            "Most of this score's inputs are missing from the listing, so it is shown "
            "conservatively. Treat it as a prompt to read the listing, not a verdict."
        )

    caveats.append(
        "Built only from what this listing states. It describes the protections on this "
        "offer, not the seller's character or trustworthiness."
    )

    return Reliability(
        score=score,
        band=band_for(score, confidence),
        confidence=confidence,
        signals=tuple(scored),
        caveats=tuple(caveats),
    )


def _replace_contribution(signal: ScoredSignal, contribution: Decimal) -> ScoredSignal:
    return ScoredSignal(
        key=signal.key,
        name=signal.name,
        value=signal.value,
        raw_value=signal.raw_value,
        weight=signal.weight,
        contribution=contribution,
        points=signal.points,
        label=signal.label,
        detail=signal.detail,
    )


def published() -> dict[str, object]:
    """The rubric as data, for `GET /market/reliability/rubric`.

    Published in-product per FR-9.2. A score that shapes a purchase decision and
    cannot be interrogated is an accusation wearing a number's clothes.
    """
    return {
        "version": RUBRIC_VERSION,
        "total_weight": format(total_weight(), "f"),
        "what_this_is": (
            "A score for how well *this listing* protects you if something goes wrong. "
            "It is built only from signals the seller publishes, each of which you can "
            "check yourself in a minute."
        ),
        "what_this_is_not": (
            "It is not a judgement about the seller, their honesty, or the authenticity of "
            "their reviews. Frugal does not attempt either — neither is measurable from "
            "public data, and asserting them about a named business would be irresponsible."
        ),
        "signals": [
            {
                "key": s.key.value,
                "name": s.name,
                "weight": format(s.weight, "f"),
                "higher_is_better": s.higher_is_better,
                "bands": [
                    {
                        "at_least" if s.higher_is_better else "at_most": format(b.threshold, "f"),
                        "points": format(b.points, "f"),
                        "label": b.label,
                    }
                    for b in s.bands
                ],
            }
            for s in SIGNALS
        ],
        "bands": [
            {"at_least": format(threshold, "f"), "label": label} for threshold, label in BANDS
        ],
        "missing_signals": (
            "A signal a listing does not state is excluded and its weight redistributed, "
            "not scored as zero. Silence is not evidence of absence."
        ),
    }
