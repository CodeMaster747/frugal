"""Tests for the Money value object (ADR-003)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.money import CurrencyMismatchError, Money


class TestExactness:
    """The reason this class exists."""

    def test_addition_is_exact_where_float_is_not(self):
        assert 0.1 + 0.2 != 0.3  # the bug being prevented
        assert Money("0.10", "INR") + Money("0.20", "INR") == Money("0.30", "INR")

    def test_float_input_is_rejected(self):
        with pytest.raises(TypeError, match="rejects float"):
            Money(1250.10, "INR")  # type: ignore[arg-type]

    def test_float_multiplication_is_rejected(self):
        with pytest.raises(TypeError, match="never float"):
            Money("100.00", "INR") * 1.5  # type: ignore[operator]

    def test_amounts_quantise_to_two_places(self):
        assert Money("10.005", "INR").amount == Decimal("10.01")  # half-up
        assert Money("10.004", "INR").amount == Decimal("10.00")

    def test_accumulation_does_not_drift(self):
        total = Money.zero("INR")
        for _ in range(1000):
            total += Money("0.01", "INR")
        assert total == Money("10.00", "INR")


class TestCurrencySafety:
    def test_cross_currency_arithmetic_raises(self):
        with pytest.raises(CurrencyMismatchError):
            Money("100.00", "INR") + Money("100.00", "USD")

    def test_cross_currency_comparison_raises(self):
        with pytest.raises(CurrencyMismatchError):
            _ = Money("100.00", "INR") < Money("100.00", "USD")

    def test_different_currencies_are_never_equal(self):
        assert Money("100.00", "INR") != Money("100.00", "USD")

    def test_currency_is_normalised_to_uppercase(self):
        assert Money("1.00", "inr").currency == "INR"

    @pytest.mark.parametrize("bad", ["IN", "INRR", "1NR", ""])
    def test_invalid_currency_codes_are_rejected(self, bad):
        with pytest.raises(ValueError, match="ISO 4217"):
            Money("1.00", bad)


class TestBehaviour:
    def test_ratio_between_amounts(self):
        saved, income = Money("43750.00", "INR"), Money("85000.00", "INR")
        assert round(saved.ratio_to(income), 3) == Decimal("0.515")

    def test_ratio_against_zero_raises(self):
        with pytest.raises(ZeroDivisionError):
            Money("1.00", "INR").ratio_to(Money.zero("INR"))

    def test_sign_predicates(self):
        assert Money("-1.00", "INR").is_negative
        assert Money("1.00", "INR").is_positive
        assert Money.zero("INR").is_zero

    def test_is_immutable(self):
        with pytest.raises(AttributeError, match="immutable"):
            Money("1.00", "INR").amount = Decimal(2)  # type: ignore[misc]

    def test_is_hashable(self):
        assert len({Money("1.00", "INR"), Money("1.00", "INR"), Money("2.00", "INR")}) == 2

    def test_non_finite_amounts_are_rejected(self):
        for bad in ("NaN", "Infinity"):
            with pytest.raises(ValueError, match="finite"):
                Money(bad, "INR")


class TestSerialisation:
    def test_amount_serialises_as_a_string(self):
        """JSON.parse produces an IEEE-754 double, so a numeric amount would
        reintroduce exactly the error NUMERIC(18,2) exists to prevent."""
        assert Money("1250.10", "INR").to_dict() == {"amount": "1250.10", "currency": "INR"}

    def test_round_trips_exactly(self):
        original = Money("134900.00", "INR")
        assert Money.from_dict(original.to_dict()) == original
