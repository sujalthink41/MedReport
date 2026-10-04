"""Trend detection — the product's strongest differentiator.

The scenario these tests encode: someone whose fasting glucose went 88, 96, 103,
109 across four years. Every single reading sat inside the lab's interval, so no
laboratory ever flagged anything. Only something holding all four reports can see
the drift, and telling them about it is the most useful thing we do.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.domain.models.measurement import CanonicalValue, Unit
from app.domain.services.trends import (
    MIN_SPAN_DAYS,
    Reading,
    TrendDirection,
    detect,
    is_monotonic,
)

MG_DL = Unit("mg/dL")
MMOL_L = Unit("mmol/L")


def reading(amount: str, on: date, unit: Unit = MG_DL) -> Reading:
    return Reading(value=CanonicalValue.of(amount, unit), collected_at=on)


class TestTheDriftNobodyFlags:
    def test_four_years_of_normal_readings_still_show_a_rise(self) -> None:
        glucose = [
            reading("88", date(2022, 3, 1)),
            reading("96", date(2023, 4, 1)),
            reading("103", date(2024, 5, 1)),
            reading("109", date(2026, 6, 1)),
        ]

        trend = detect(glucose)

        # Every one of these is inside a 70-100 fasting range at some lab, or close
        # to it. No report flagged anything. The movement is the signal.
        assert trend is not None
        assert trend.direction is TrendDirection.RISING
        assert trend.is_significant
        assert trend.change > Decimal("0.23")
        assert trend.readings == 4

    def test_a_steady_climb_is_distinguishable_from_a_bounce(self) -> None:
        climbing = [
            reading("88", date(2022, 3, 1)),
            reading("96", date(2023, 4, 1)),
            reading("109", date(2024, 5, 1)),
        ]
        bouncing = [
            reading("88", date(2022, 3, 1)),
            reading("120", date(2023, 4, 1)),
            reading("109", date(2024, 5, 1)),
        ]

        # Both end higher than they started. Only the first is worth emphasising;
        # conflating them would cry wolf.
        assert is_monotonic(climbing) is True
        assert is_monotonic(bouncing) is False


class TestDirection:
    def test_a_fall_is_reported_as_a_fall(self) -> None:
        trend = detect([reading("15.0", date(2024, 1, 1)), reading("9.0", date(2025, 1, 1))])

        assert trend is not None
        assert trend.direction is TrendDirection.FALLING
        assert trend.change < 0

    def test_movement_within_noise_is_stable(self) -> None:
        # Biological variation and assay imprecision alone move a repeat by a few
        # percent. Calling that "rising" would read signal into noise.
        trend = detect([reading("100", date(2024, 1, 1)), reading("103", date(2025, 1, 1))])

        assert trend is not None
        assert trend.direction is TrendDirection.STABLE
        assert trend.is_significant is False

    def test_real_movement_is_a_direction_even_if_not_yet_significant(self) -> None:
        # Direction and significance are separate questions. A 10% rise is a rise;
        # whether it is worth putting in front of a user is a different call.
        trend = detect([reading("100", date(2024, 1, 1)), reading("110", date(2025, 1, 1))])

        assert trend is not None
        assert trend.direction is TrendDirection.RISING
        assert trend.is_significant is False

    def test_readings_are_ordered_by_collection_date(self) -> None:
        # People upload three years of reports in one sitting, in whatever order
        # the files happen to be in. Ordering by upload time would scramble this.
        shuffled = [
            reading("109", date(2026, 6, 1)),
            reading("88", date(2022, 3, 1)),
            reading("96", date(2023, 4, 1)),
        ]

        trend = detect(shuffled)

        assert trend is not None
        assert trend.first.collected_at == date(2022, 3, 1)
        assert trend.latest.collected_at == date(2026, 6, 1)
        assert trend.direction is TrendDirection.RISING


class TestRefusal:
    def test_one_reading_is_not_a_trend(self) -> None:
        assert detect([reading("100", date(2024, 1, 1))]) is None

    def test_no_readings_is_not_a_trend(self) -> None:
        assert detect([]) is None

    def test_a_retest_a_week_later_is_not_a_trend(self) -> None:
        # Someone retested because the first result looked odd. Calling that a
        # rise would manufacture alarm out of ordinary clinical follow-up.
        trend = detect([reading("100", date(2024, 1, 1)), reading("140", date(2024, 1, 8))])

        assert trend is None

    @pytest.mark.parametrize("days", [MIN_SPAN_DAYS - 1, MIN_SPAN_DAYS])
    def test_the_minimum_span_boundary(self, days: int) -> None:
        from datetime import timedelta

        start = date(2024, 1, 1)
        trend = detect([reading("100", start), reading("140", start + timedelta(days=days))])

        assert (trend is not None) is (days >= MIN_SPAN_DAYS)

    def test_mixed_units_refuse_to_trend(self) -> None:
        # CP14 refuses to guess a conversion it cannot derive. Rather than compare
        # incomparable numbers, the user sees two honest series.
        trend = detect(
            [
                reading("100", date(2024, 1, 1), MG_DL),
                reading("5.5", date(2025, 1, 1), MMOL_L),
            ]
        )

        assert trend is None

    def test_a_zero_baseline_refuses(self) -> None:
        # Relative change from zero is undefined, and any number we produced would
        # be arbitrary.
        assert detect([reading("0", date(2024, 1, 1)), reading("5", date(2025, 1, 1))]) is None

    def test_monotonic_needs_at_least_three_points(self) -> None:
        # Two points are always monotonic, which makes the question meaningless.
        assert (
            is_monotonic([reading("1", date(2024, 1, 1)), reading("2", date(2025, 1, 1))]) is False
        )


class TestSpan:
    def test_the_span_is_reported_in_days(self) -> None:
        trend = detect([reading("100", date(2024, 1, 1)), reading("150", date(2025, 1, 1))])

        assert trend is not None
        assert trend.span_days == 366  # 2024 is a leap year
        # "24% higher than three years ago" needs both numbers to be true.
        assert trend.change == Decimal("0.5")
