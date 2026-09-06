"""Tests for units, values and ranges.

Every test here is the edge-case table from the standards doc, made executable:
boundary values, missing bounds, one-sided markers, unit mismatch, zero, negative.

Stop and notice what is NOT here: no Docker, no database, no app, no network, no
mocks. That is what "pure domain" buys, and it is why this suite runs in
milliseconds and will still run in ten years when FastAPI is a memory.
"""

from decimal import Decimal

import pytest

from app.domain.errors import InvalidInputError
from app.domain.models import (
    DIMENSIONLESS,
    CanonicalValue,
    Direction,
    RangeSource,
    ReferenceRange,
    Unit,
)

MG_DL = Unit("mg/dL")
MMOL_L = Unit("mmol/L")
PERCENT = Unit("%")


def value(amount: str, unit: Unit = MG_DL) -> CanonicalValue:
    return CanonicalValue.of(amount, unit)


class TestCanonicalValue:
    def test_floats_are_rejected_not_converted(self) -> None:
        # The single most important test in this file.
        #
        # 0.1 + 0.2 == 0.30000000000000004. An HbA1c of exactly 5.7 is the
        # prediabetes threshold; a float landing on 5.699999999 calls someone
        # normal who is not. We refuse rather than convert, because converting
        # would reintroduce the imprecision we are avoiding.
        with pytest.raises(InvalidInputError):
            CanonicalValue(amount=6.1, unit=PERCENT)  # type: ignore[arg-type]

    def test_strings_keep_their_exact_decimal(self) -> None:
        assert CanonicalValue.of("6.1", PERCENT).amount == Decimal("6.1")

    def test_nonsense_input_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError):
            CanonicalValue.of("not a number", MG_DL)

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_values_are_rejected(self, bad: str) -> None:
        # NaN silently poisons every comparison it touches: NaN < 5 is False and
        # NaN > 5 is also False, so a band would come out "normal" by accident.
        with pytest.raises(InvalidInputError):
            CanonicalValue.of(bad, MG_DL)

    def test_zero_and_negative_are_valid(self) -> None:
        # Base excess is legitimately negative; many counts are legitimately zero.
        # Rejecting them would be us inventing a clinical rule we do not have.
        assert value("0").amount == 0
        assert value("-2.5").amount < 0

    def test_comparing_different_units_raises(self) -> None:
        with pytest.raises(InvalidInputError, match="cannot compare"):
            _ = value("100", MG_DL) < value("5.5", MMOL_L)

    def test_equality_across_units_is_false_and_does_not_raise(self) -> None:
        # __eq__ must never blow up - Python relies on it everywhere, including
        # `in` and dict lookups. Ordering is where a unit mismatch is a real bug.
        assert value("100", MG_DL) != value("100", MMOL_L)

    def test_ordering_works_within_a_unit(self) -> None:
        assert value("5") < value("10")
        assert value("10") > value("5")
        assert value("5") <= value("5")

    def test_values_are_immutable(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 - dataclasses raise FrozenInstanceError
            value("5").amount = Decimal("6")  # type: ignore[misc]


class TestUnit:
    def test_whitespace_is_normalised(self) -> None:
        assert Unit(" mg/dL ") == Unit("mg/dL")

    def test_the_dimensionless_unit_is_legitimate(self) -> None:
        # A/G ratio, INR and various indices are printed with no unit at all.
        ratio = CanonicalValue.of("1.4", DIMENSIONLESS)

        assert ratio.amount == Decimal("1.4")

    def test_dimensionless_still_will_not_compare_against_a_real_unit(self) -> None:
        # The safety property does not depend on rejecting empty units: an
        # accidentally-missing unit still cannot be judged against a real range.
        with pytest.raises(InvalidInputError, match="cannot compare"):
            _ = CanonicalValue.of("1.4", DIMENSIONLESS) < value("5", MG_DL)


class TestReferenceRange:
    def test_a_range_must_bound_at_least_one_side(self) -> None:
        with pytest.raises(InvalidInputError, match="at least one side"):
            ReferenceRange(low=None, high=None, source=RangeSource.LAB)

    def test_low_must_be_below_high(self) -> None:
        with pytest.raises(InvalidInputError, match="low must be below high"):
            ReferenceRange(low=value("15"), high=value("12"), source=RangeSource.LAB)

    def test_bounds_must_share_a_unit(self) -> None:
        with pytest.raises(InvalidInputError, match="different units"):
            ReferenceRange(low=value("12", MG_DL), high=value("15", MMOL_L), source=RangeSource.LAB)

    def test_one_sided_ranges_are_valid(self) -> None:
        # LDL and triglycerides have an upper limit and no meaningful lower one.
        ldl = ReferenceRange(low=None, high=value("100"), source=RangeSource.GUIDELINE)

        assert not ldl.is_two_sided
        assert ldl.direction_of(value("130")) is Direction.HIGH
        # Crucially, a very low LDL is NOT flagged low. Applying two-sided logic to
        # a one-sided marker would alarm people over a good result.
        assert ldl.direction_of(value("40")) is Direction.WITHIN


class TestDirection:
    @pytest.fixture
    def haemoglobin(self) -> ReferenceRange:
        return ReferenceRange(low=value("12.0"), high=value("15.5"), source=RangeSource.LAB)

    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            ("11.9", Direction.LOW),
            ("12.0", Direction.WITHIN),  # exactly on the lower bound - inclusive
            ("13.5", Direction.WITHIN),
            ("15.5", Direction.WITHIN),  # exactly on the upper bound - inclusive
            ("15.6", Direction.HIGH),
        ],
    )
    def test_boundaries_are_inclusive(
        self, haemoglobin: ReferenceRange, amount: str, expected: Direction
    ) -> None:
        # The boundary cases are the whole point. A lab printing "12.0 - 15.5"
        # means 12.0 is normal. Off-by-one here tells a healthy person they are
        # anaemic, at scale, silently.
        assert haemoglobin.direction_of(value(amount)) is expected


class TestPositionInRange:
    @pytest.fixture
    def range_10_to_20(self) -> ReferenceRange:
        return ReferenceRange(low=value("10"), high=value("20"), source=RangeSource.LAB)

    @pytest.mark.parametrize(
        ("amount", "expected"),
        [("10", "0"), ("15", "0.5"), ("20", "1"), ("25", "1.5")],
    )
    def test_position_is_a_fraction_of_the_span(
        self, range_10_to_20: ReferenceRange, amount: str, expected: str
    ) -> None:
        # Lets the UI draw a marker on a bar: "normal, but at the very top" reads
        # very differently from "normal" alone.
        assert range_10_to_20.position_of(value(amount)) == Decimal(expected)

    def test_one_sided_ranges_have_no_position(self) -> None:
        ldl = ReferenceRange(low=None, high=value("100"), source=RangeSource.GUIDELINE)

        # None, not 0. Returning 0 would be a lie the UI would happily render as
        # "at the bottom of the healthy range".
        assert ldl.position_of(value("50")) is None
