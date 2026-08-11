"""Detecting recurring transactions from a ledger.

Pure: takes observed transactions, returns detected patterns. No session, no
clock, no I/O.

The problem is not finding repetition -- it is **telling a commitment from a
habit**. Rent on the 1st of every month for ₹28,000 is a commitment: it will
happen again, on a knowable date, for a knowable amount, and a forecast that
misses it is wrong by ₹28,000. Coffee three times a week is a habit: real,
predictable in aggregate, and wrong to model as a scheduled event.

The separation is made on two axes, both reported:

* **Interval regularity** -- how tightly spaced the occurrences are.
* **Amount stability** -- how much the value varies between them.

A pattern needs both to be called recurring. Groceries every 6--9 days at wildly
varying amounts scores well on the first and badly on the second, and belongs in
the discretionary baseline rather than the schedule.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from statistics import median, pstdev

ZERO = Decimal("0")

#: Three occurrences give two intervals -- the minimum needed to distinguish a
#: rhythm from a coincidence. Two transactions define one interval, which is
#: indistinguishable from chance.
MIN_OCCURRENCES = 3

#: Cadences recognised, as (label, expected days, tolerance).
#:
#: Monthly tolerance is wide because calendar months are 28--31 days and a due
#: date landing on a weekend commonly slips by two or three. A narrow window
#: would reject most real salaries.
CADENCES: tuple[tuple[str, int, int], ...] = (
    ("weekly", 7, 2),
    ("fortnightly", 14, 3),
    ("monthly", 30, 5),
    ("quarterly", 91, 10),
    ("yearly", 365, 21),
)

#: Above this coefficient of variation in the gaps, the spacing is not a rhythm.
MAX_INTERVAL_CV = Decimal("0.35")

#: Above this coefficient of variation in the amounts, it is a habit rather than
#: a commitment. Generous, because utility bills genuinely swing seasonally and
#: are still commitments.
MAX_AMOUNT_CV = Decimal("0.45")

#: Below this, the pattern is not offered as recurring at all.
MIN_CONFIDENCE = Decimal("0.50")


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One observed transaction, reduced to what detection needs."""

    transaction_id: uuid.UUID
    merchant: str
    occurred_on: date
    amount: Decimal
    kind: str
    category_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Pattern:
    """A detected recurring commitment."""

    merchant: str
    kind: str
    cadence: str
    #: Typical amount. Median, not mean: one unusual month should not move the
    #: figure a forecast is built on.
    amount: Decimal
    interval_days: int
    occurrences: int
    first_seen_on: date
    last_seen_on: date
    next_due_on: date
    #: Coefficient of variation of the amounts, 0 = identical every time.
    amount_variance: Decimal
    interval_variance: Decimal
    confidence: Decimal
    category_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None

    @property
    def monthly_equivalent(self) -> Decimal:
        """Amount normalised to a month, for comparing across cadences."""
        per_month = Decimal("30.44") / Decimal(self.interval_days)
        return (self.amount * per_month).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def due_dates(self, start: date, end: date) -> list[date]:
        """Every occurrence expected in a window.

        Projected forward from `next_due_on` by the *detected* interval rather
        than the nominal cadence, so a salary that reliably lands on the 2nd
        stays on the 2nd instead of drifting.
        """
        dates: list[date] = []
        cursor = self.next_due_on
        while cursor <= end:
            if cursor >= start:
                dates.append(cursor)
            cursor += timedelta(days=self.interval_days)
        return dates


def _cv(values: list[Decimal]) -> Decimal:
    """Coefficient of variation: spread relative to size.

    Relative rather than absolute so a ₹500 swing on rent and a ₹500 swing on a
    coffee are not treated as equally surprising.
    """
    if len(values) < 2:
        return ZERO
    numbers = [float(v) for v in values]
    mean = sum(numbers) / len(numbers)
    if mean == 0:
        return ZERO
    return Decimal(str(pstdev(numbers) / abs(mean))).quantize(Decimal("0.0001"))


def _classify_cadence(interval: float) -> tuple[str, int] | None:
    """Map a median gap to a named cadence, or None if it matches nothing."""
    for label, expected, tolerance in CADENCES:
        if abs(interval - expected) <= tolerance:
            return label, expected
    return None


def _confidence(*, occurrences: int, interval_cv: Decimal, amount_cv: Decimal) -> Decimal:
    """How much to trust this pattern.

    Three independent signals, multiplied rather than averaged: a pattern that
    fails badly on any one of them is not rescued by the other two. Six
    identical monthly charges is a commitment; six charges at wildly varying
    amounts is not, however regular the dates.
    """
    # Saturates at eight occurrences -- past that, more history barely changes
    # how sure anyone should be.
    by_count = min(Decimal(occurrences) / Decimal(8), Decimal(1))
    by_timing = max(ZERO, Decimal(1) - (interval_cv / MAX_INTERVAL_CV))
    by_amount = max(ZERO, Decimal(1) - (amount_cv / MAX_AMOUNT_CV))

    combined = (
        by_count
        * (Decimal("0.4") + Decimal("0.6") * by_timing)
        * (Decimal("0.4") + Decimal("0.6") * by_amount)
    )
    return combined.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def detect(occurrences: list[Occurrence], *, today: date) -> list[Pattern]:
    """Find recurring commitments in a ledger.

    Grouped by normalised merchant *and* kind: a merchant that both charges and
    refunds is two different rhythms, and merging them would produce a pattern
    that matches neither.
    """
    groups: dict[tuple[str, str], list[Occurrence]] = {}
    for occurrence in occurrences:
        if not occurrence.merchant:
            continue
        groups.setdefault((occurrence.merchant, occurrence.kind), []).append(occurrence)

    patterns: list[Pattern] = []
    for (merchant, kind), members in groups.items():
        pattern = _analyse(merchant, kind, sorted(members, key=lambda o: o.occurred_on), today)
        if pattern is not None:
            patterns.append(pattern)

    # Largest commitments first: a forecast built from the top N patterns should
    # take the ones that move the number most.
    return sorted(patterns, key=lambda p: p.monthly_equivalent, reverse=True)


def _analyse(merchant: str, kind: str, members: list[Occurrence], today: date) -> Pattern | None:
    if len(members) < MIN_OCCURRENCES:
        return None

    dates = [o.occurred_on for o in members]
    # Deduplicate same-day charges before measuring gaps: two coffees on one
    # Tuesday would otherwise register as a zero-day interval and destroy the
    # median.
    unique_dates = sorted(set(dates))
    if len(unique_dates) < MIN_OCCURRENCES:
        return None

    gaps = [float((later - earlier).days) for earlier, later in pairwise(unique_dates)]
    if not gaps:
        return None

    typical_gap = median(gaps)
    cadence = _classify_cadence(typical_gap)
    if cadence is None:
        return None

    label, _nominal = cadence
    interval_cv = _cv([Decimal(str(g)) for g in gaps])
    if interval_cv > MAX_INTERVAL_CV:
        return None

    amounts = [o.amount for o in members]
    amount_cv = _cv(amounts)
    if amount_cv > MAX_AMOUNT_CV:
        return None

    confidence = _confidence(
        occurrences=len(unique_dates), interval_cv=interval_cv, amount_cv=amount_cv
    )
    if confidence < MIN_CONFIDENCE:
        return None

    interval_days = round(typical_gap)
    last_seen = unique_dates[-1]

    # Roll forward past any date already behind us: a pattern last seen four
    # months ago should not project its next occurrence into the past.
    next_due = last_seen + timedelta(days=interval_days)
    while next_due < today:
        next_due += timedelta(days=interval_days)

    return Pattern(
        merchant=merchant,
        kind=kind,
        cadence=label,
        amount=Decimal(str(median([float(a) for a in amounts]))).quantize(Decimal("0.01")),
        interval_days=interval_days,
        occurrences=len(unique_dates),
        first_seen_on=unique_dates[0],
        last_seen_on=last_seen,
        next_due_on=next_due,
        amount_variance=amount_cv,
        interval_variance=interval_cv,
        confidence=confidence,
        category_id=members[-1].category_id,
        account_id=members[-1].account_id,
    )


def is_stale(pattern: Pattern, *, today: date, missed_intervals: int = 2) -> bool:
    """Whether a pattern has stopped happening.

    A cancelled subscription keeps its history forever, so without this it would
    be projected into every future forecast. Two missed intervals rather than
    one, because a single late payment is common and not evidence of anything.
    """
    overdue_by = (today - pattern.last_seen_on).days
    return overdue_by > pattern.interval_days * (missed_intervals + 1)
