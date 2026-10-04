"""Parsing printed results.

Every input below appeared on the first real report tested, or is a form Indian
labs routinely print. The hard cases are never the plain numbers.
"""

from decimal import Decimal

import pytest

from app.domain.models.enums import RangeSource
from app.domain.services.parsing import ValueKind, parse_range, parse_value


class TestNumbers:
    @pytest.mark.parametrize(
        ("printed", "expected"),
        [("11.2", "11.2"), ("310", "310"), ("0.00", "0.00"), ("134.25", "134.25")],
    )
    def test_ordinary_measurements(self, printed: str, expected: str) -> None:
        parsed = parse_value(printed, "g/dL")

        assert parsed.kind is ValueKind.NUMERIC
        assert parsed.value is not None
        assert parsed.value.amount == Decimal(expected)
        assert parsed.is_comparable

    def test_a_thousands_separator_is_stripped(self) -> None:
        parsed = parse_value("11,200", "cells/cumm")

        assert parsed.value is not None
        assert parsed.value.amount == Decimal("11200")

    def test_a_decimal_comma_is_not_assumed(self) -> None:
        # Indian labs use a full stop for decimals. Guessing between the two
        # conventions would turn 1,5 into fifteen.
        parsed = parse_value("1,5", "g/dL")

        assert parsed.value is not None
        assert parsed.value.amount == Decimal("15")

    def test_a_negative_value_is_valid(self) -> None:
        # Base excess is legitimately negative.
        parsed = parse_value("-2.5", "mmol/L")

        assert parsed.kind is ValueKind.NUMERIC
        assert parsed.value is not None
        assert parsed.value.amount < 0


class TestBounded:
    @pytest.mark.parametrize(
        ("printed", "bound"), [("<0.5", "lt"), ("< 0.01", "lt"), (">1000", "gt"), (">= 10", "gt")]
    )
    def test_limits_of_detection_are_recognised(self, printed: str, bound: str) -> None:
        parsed = parse_value(printed, "mIU/L")

        assert parsed.kind is ValueKind.BOUNDED
        assert parsed.bound == bound

    def test_a_bounded_result_is_not_comparable(self) -> None:
        # A TSH of "<0.01" is not 0.01 - it is "too low to measure", which is
        # clinically stronger than the number suggests. Charting it as 0.01 would
        # understate it.
        parsed = parse_value("<0.01", "mIU/L")

        assert parsed.is_comparable is False
        assert parsed.value is not None  # the number is kept, just not trusted as one


class TestQualitative:
    @pytest.mark.parametrize(
        "printed",
        ["Negative", "Non Reactive", "NOT DETECTED", "DETECTED +++", "NIL", "PALE YELLOW"],
    )
    def test_words_from_the_real_report(self, printed: str) -> None:
        parsed = parse_value(printed, None)

        assert parsed.kind is ValueKind.QUALITATIVE
        assert parsed.is_comparable is False
        # Still shown to the user, with its text intact.
        assert parsed.text == printed

    def test_a_titre_is_not_a_fraction(self) -> None:
        parsed = parse_value("1:40", None)

        assert parsed.kind is ValueKind.TITRE
        assert parsed.is_comparable is False

    def test_a_microscopy_range_is_not_a_value(self) -> None:
        # "2-3 /hpf" pus cells. One observation printed as an interval, not a
        # measurement to band.
        parsed = parse_value("2-3", "/hpf")

        assert parsed.kind is ValueKind.RANGE
        assert parsed.is_comparable is False

    def test_an_empty_result_is_unreadable(self) -> None:
        assert parse_value(None, "g/dL").kind is ValueKind.UNREADABLE
        assert parse_value("   ", "g/dL").kind is ValueKind.UNREADABLE


class TestRanges:
    def test_the_printed_text_is_preferred_over_the_numbers(self) -> None:
        # JSON numbers arrive as floats, and 0.1 as a float is not exactly 0.1.
        # Re-parsing the literal string keeps the Decimal exact.
        parsed = parse_range("[0.10-1.20]", 0.1, 1.2, "mg/dL")

        assert parsed is not None
        assert parsed.low is not None
        assert parsed.low.amount == Decimal("0.10")

    @pytest.mark.parametrize("printed", ["12.0 - 15.5", "[12.0-15.5]", "12.0-15.5", "12.0 to 15.5"])
    def test_range_formats_from_real_reports(self, printed: str) -> None:
        parsed = parse_range(printed, None, None, "g/dL")

        assert parsed is not None
        assert parsed.low is not None
        assert parsed.high is not None
        assert parsed.low.amount == Decimal("12.0")
        assert parsed.high.amount == Decimal("15.5")

    def test_an_upper_only_range_gets_no_invented_floor(self) -> None:
        parsed = parse_range("< 200", None, None, "mg/dL")

        assert parsed is not None
        assert parsed.high is not None
        # A lower bound of zero would make every low LDL look like a finding.
        assert parsed.low is None

    def test_a_lower_only_range_works(self) -> None:
        parsed = parse_range("> 40", None, None, "mg/dL")

        assert parsed is not None
        assert parsed.low is not None
        assert parsed.high is None

    def test_a_qualitative_expectation_is_not_a_range(self) -> None:
        # The urine report prints "Negative" in the reference column.
        assert parse_range("Negative", None, None, None) is None

    def test_no_range_at_all_stays_none(self) -> None:
        # A fact worth preserving. The row is shown unjudged rather than measured
        # against something we invented.
        assert parse_range(None, None, None, "ng/mL") is None

    def test_numeric_fields_are_used_when_there_is_no_text(self) -> None:
        parsed = parse_range(None, 12.0, 15.5, "g/dL")

        assert parsed is not None
        assert parsed.low is not None
        assert parsed.low.amount == Decimal("12.0")

    def test_an_inverted_range_is_refused_not_crashed(self) -> None:
        # low above high is the lab's or the model's mistake, and one bad row
        # must not fail a whole report.
        assert parse_range(None, 15.5, 12.0, "g/dL") is None

    def test_the_source_travels_with_the_range(self) -> None:
        parsed = parse_range("12.0-15.5", None, None, "g/dL", RangeSource.LAB)

        assert parsed is not None
        assert parsed.source is RangeSource.LAB
