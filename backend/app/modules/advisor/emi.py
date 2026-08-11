"""EMI modelling.

Pure arithmetic on the standard reducing-balance formula. The interesting choice
here is not mathematical — it is that **total interest is always shown next to
the monthly figure**.

A monthly instalment is designed to feel small; that is its commercial purpose.
₹6,200 a month reads as affordable and ₹13,900 of interest on a ₹1,34,900 laptop
reads as what it is. A tool that shows only the first is helping the retailer,
not the user, and the advisor's whole claim is that it is on the user's side.

"No-cost EMI" is modelled honestly too: the interest is usually folded into the
price as a forgone discount rather than genuinely absent. The rate can be set to
zero where it truly is, and the caveat says what that assumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
CENTS = Decimal("0.01")

#: Typical Indian consumer-durable EMI rates, by tenure. Longer tenures cost
#: more because the lender's risk window is longer.
DEFAULT_RATES: dict[int, Decimal] = {
    3: Decimal("0.13"),
    6: Decimal("0.14"),
    12: Decimal("0.15"),
    18: Decimal("0.16"),
    24: Decimal("0.16"),
    36: Decimal("0.18"),
}

TENURES: tuple[int, ...] = (3, 6, 12, 18, 24, 36)


@dataclass(frozen=True, slots=True)
class EmiOption:
    tenure_months: int
    monthly: Decimal
    total_payable: Decimal
    total_interest: Decimal
    annual_rate: Decimal
    #: Debt service as a share of income *including* this new instalment.
    new_debt_ratio: Decimal
    #: Whether this plan keeps debt service under the lending ceiling.
    #:
    #: Every tenure is still shown -- seeing that three months would put you at
    #: 91% of income is exactly why twelve is the sensible one, and hiding it
    #: would remove the comparison that makes the choice obvious. But a table of
    #: six rows with no marking reads as six equally available options, which is
    #: the impression a retailer's checkout wants to create and this one does
    #: not.
    is_serviceable: bool = True

    @property
    def interest_share(self) -> Decimal:
        """Interest as a share of the cash price — the number worth comparing."""
        principal = self.total_payable - self.total_interest
        return (self.total_interest / principal).quantize(Decimal("0.0001")) if principal else ZERO


def monthly_instalment(principal: Decimal, annual_rate: Decimal, months: int) -> Decimal:
    """Standard reducing-balance EMI.

    `P * r * (1+r)^n / ((1+r)^n - 1)`, with `r` the monthly rate. A zero rate
    degenerates to simple division, which the formula cannot express.
    """
    if months <= 0:
        return ZERO
    if annual_rate <= 0:
        return (principal / Decimal(months)).quantize(CENTS, rounding=ROUND_HALF_UP)

    rate = annual_rate / Decimal(12)
    growth = (Decimal(1) + rate) ** months
    return (principal * rate * growth / (growth - Decimal(1))).quantize(
        CENTS, rounding=ROUND_HALF_UP
    )


def options(
    price: Decimal,
    *,
    monthly_income: Decimal,
    existing_debt_service: Decimal,
    tenures: tuple[int, ...] = TENURES,
    rates: dict[int, Decimal] | None = None,
    ceiling: Decimal = Decimal("0.43"),
) -> list[EmiOption]:
    """Every tenure worth offering, priced honestly.

    Options whose instalment exceeds the user's entire monthly income are
    dropped: they are arithmetically valid and not a choice anyone has.
    """
    table = rates or DEFAULT_RATES
    built: list[EmiOption] = []

    for months in tenures:
        rate = table.get(months, Decimal("0.16"))
        monthly = monthly_instalment(price, rate, months)
        if monthly_income > 0 and monthly > monthly_income:
            continue

        total = (monthly * Decimal(months)).quantize(CENTS, rounding=ROUND_HALF_UP)
        new_ratio = (
            ((existing_debt_service + monthly) / monthly_income).quantize(Decimal("0.0001"))
            if monthly_income > 0
            else Decimal("999")
        )

        built.append(
            EmiOption(
                tenure_months=months,
                monthly=monthly,
                total_payable=total,
                total_interest=(total - price).quantize(CENTS, rounding=ROUND_HALF_UP),
                annual_rate=rate,
                new_debt_ratio=new_ratio,
                is_serviceable=new_ratio <= ceiling,
            )
        )

    return built


def best_option(candidates: list[EmiOption], *, debt_ceiling: Decimal) -> EmiOption | None:
    """The shortest tenure that keeps debt service under the ceiling.

    Shortest, not cheapest monthly. A 36-month plan always has the smallest
    instalment and the largest total cost; recommending it because the monthly
    number looks best is the exact reasoning error this module exists to
    counter. Among options the user can actually service, less total interest
    wins.
    """
    viable = [option for option in candidates if option.new_debt_ratio <= debt_ceiling]
    if not viable:
        return None
    return min(viable, key=lambda option: (option.total_interest, option.tenure_months))
