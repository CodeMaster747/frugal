"""Money value object.

Money is represented as ``Decimal`` with an explicit currency, never as a float.
See ADR-003 for the reasoning; the short version is that floats cannot exactly
represent most decimal fractions, and the error compounds through aggregation
until balances stop reconciling.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Self

from pydantic_core import CoreSchema, core_schema

CENTS = Decimal("0.01")
"""Quantisation unit. All monetary values carry exactly two decimal places."""

_CURRENCY_LENGTH = 3


class CurrencyMismatchError(ValueError):
    """Raised when an operation combines two different currencies."""

    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"Cannot combine {left} and {right} without an exchange rate")
        self.left = left
        self.right = right


class Money:
    """An exact monetary amount in a single currency.

    Immutable. Arithmetic between different currencies raises rather than
    silently producing a meaningless number.
    """

    __slots__ = ("_amount", "_currency")

    # Declared for the type checker. Annotations alone create no class
    # attributes, so they coexist with __slots__.
    _amount: Decimal
    _currency: str

    def __init__(self, amount: Decimal | int | str, currency: str) -> None:
        if isinstance(amount, float):
            raise TypeError(
                "Money rejects float input; pass Decimal, int, or str to avoid "
                "binary rounding error (ADR-003)"
            )
        if len(currency) != _CURRENCY_LENGTH or not currency.isalpha():
            raise ValueError(f"Currency must be a 3-letter ISO 4217 code, got {currency!r}")

        try:
            value = Decimal(amount)
        except InvalidOperation as exc:
            raise ValueError(f"Not a valid monetary amount: {amount!r}") from exc

        if not value.is_finite():
            raise ValueError(f"Monetary amount must be finite, got {amount!r}")

        object.__setattr__(self, "_amount", value.quantize(CENTS, rounding=ROUND_HALF_UP))
        object.__setattr__(self, "_currency", currency.upper())

    # -- construction ----------------------------------------------------

    @classmethod
    def zero(cls, currency: str) -> Self:
        return cls(Decimal(0), currency)

    # -- accessors -------------------------------------------------------

    @property
    def amount(self) -> Decimal:
        return self._amount

    @property
    def currency(self) -> str:
        return self._currency

    # -- arithmetic ------------------------------------------------------

    def _check(self, other: Money) -> None:
        if self._currency != other._currency:
            raise CurrencyMismatchError(self._currency, other._currency)

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self._amount + other._amount, self._currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self._amount - other._amount, self._currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        if isinstance(factor, float):
            raise TypeError("Multiply Money by Decimal or int, never float (ADR-003)")
        return Money(self._amount * Decimal(factor), self._currency)

    __rmul__ = __mul__

    def __neg__(self) -> Money:
        return Money(-self._amount, self._currency)

    def __abs__(self) -> Money:
        return Money(abs(self._amount), self._currency)

    def ratio_to(self, other: Money) -> Decimal:
        """Dimensionless ratio between two amounts in the same currency."""
        self._check(other)
        if other._amount == 0:
            raise ZeroDivisionError("Cannot compute a ratio against zero")
        return self._amount / other._amount

    # -- comparison ------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._amount == other._amount and self._currency == other._currency

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self._amount < other._amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self._amount <= other._amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self._amount > other._amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self._amount >= other._amount

    def __hash__(self) -> int:
        return hash((self._amount, self._currency))

    @property
    def is_zero(self) -> bool:
        return self._amount == 0

    @property
    def is_positive(self) -> bool:
        return self._amount > 0

    @property
    def is_negative(self) -> bool:
        return self._amount < 0

    # -- representation --------------------------------------------------

    def __repr__(self) -> str:
        return f"Money('{self._amount}', '{self._currency}')"

    def __str__(self) -> str:
        return f"{self._amount} {self._currency}"

    def __setattr__(self, *_: Any) -> None:
        raise AttributeError("Money is immutable")

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict[str, str]:
        """Wire format. The amount is a *string* so it survives JSON round-trip.

        ``JSON.parse`` produces an IEEE-754 double, which would reintroduce
        exactly the error this class exists to prevent.
        """
        return {"amount": str(self._amount), "currency": self._currency}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> Self:
        return cls(data["amount"], data["currency"])

    @classmethod
    def __get_pydantic_core_schema__(cls, *_: Any) -> CoreSchema:
        """Let Pydantic validate and serialise Money natively."""
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda m: m.to_dict(), return_schema=core_schema.dict_schema()
            ),
        )

    @classmethod
    def _validate(cls, value: Any) -> Money:
        if isinstance(value, Money):
            return value
        if isinstance(value, dict):
            return cls.from_dict(value)
        raise ValueError(f"Cannot parse Money from {type(value).__name__}")
