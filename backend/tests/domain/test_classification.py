"""The banding edge-case table, made executable.

This is the code that decides what a user is told about their own body, so the
boundaries are tested exhaustively rather than representatively. An off-by-one
here tells a healthy person they are anaemic, at scale, silently.
"""

from decimal import Decimal

import pytest

from app.domain.models.clinical import CriticalValue, EntryStatus
from app.domain.models.enums import Band, Direction, RangeSource
from app.domain.models.identifiers import CanonicalTestId
from app.domain.models.measurement import CanonicalValue, ReferenceRange, Unit
from app.domain.services.classification import PatientContext, classify, resolve_range

G_DL = Unit("g/dL")
MG_DL = Unit("mg/dL")

# Haemoglobin, female: 12.0 - 15.5 g/dL. A 3.5 span, so the 10% margin is 0.35.
HB = ReferenceRange(
    low=CanonicalValue.of("12.0", G_DL),
    high=CanonicalValue.of("15.5", G_DL),
    source=RangeSource.LAB,
)


def hb(amount: str) -> CanonicalValue:
    return CanonicalValue.of(amount, G_DL)


class TestBands:
    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            ("11.9", Band.OUT_OF_RANGE),  # just below
            ("12.0", Band.BORDERLINE),  # exactly on the lower bound
            ("12.34", Band.BORDERLINE),  # inside the 0.35 margin
            ("12.36", Band.NORMAL),  # just past it
            ("13.5", Band.NORMAL),  # comfortably mid-range
            ("15.14", Band.NORMAL),
            ("15.16", Band.BORDERLINE),  # inside the upper margin
            ("15.5", Band.BORDERLINE),  # exactly on the upper bound
            ("15.6", Band.OUT_OF_RANGE),  # just above
        ],
    )
    def test_the_boundaries(self, amount: str, expected: Band) -> None:
        assert classify(hb(amount), HB).band is expected

    def test_sitting_on_a_bound_is_worth_watching(self) -> None:
        # Technically normal, genuinely worth a second look. Treating it as
        # unremarkable would throw away the one signal the number carries.
        assert classify(hb("12.0"), HB).band is Band.BORDERLINE
        assert classify(hb("12.0"), HB).direction is Direction.WITHIN

    @pytest.mark.parametrize(
        ("amount", "expected"),
        [("11.0", Direction.LOW), ("13.5", Direction.WITHIN), ("17.0", Direction.HIGH)],
    )
    def test_direction_is_reported_separately(self, amount: str, expected: Direction) -> None:
        # Out of range and high are different facts: a ferritin can be out of
        # range by being low, and the arrow the UI draws depends on which.
        assert classify(hb(amount), HB).direction is expected

    def test_position_within_the_range_is_reported(self) -> None:
        result = classify(hb("13.75"), HB)

        # Lets the UI draw a marker on a bar: "normal, but at the very top" reads
        # very differently from "normal".
        assert result.position == Decimal("0.5")


class TestIncompleteData:
    def test_an_unreadable_row_is_marked_as_such(self) -> None:
        result = classify(None, HB)

        # "We could not read this row" is a better product than a confident wrong
        # number.
        assert result.band is Band.UNREADABLE
        assert result.direction is Direction.UNDETERMINED

    def test_a_value_with_no_range_is_unknown_not_normal(self) -> None:
        result = classify(hb("13.5"), None)

        # Shown, not judged. Calling it normal would be inventing a verdict.
        assert result.band is Band.UNKNOWN

    def test_mismatched_units_refuse_to_judge(self) -> None:
        # Comparing mg/dL against a g/dL range would produce a confident wrong
        # band. Reaching here means normalisation could not reconcile them.
        result = classify(CanonicalValue.of("13.5", MG_DL), HB)

        assert result.band is Band.UNKNOWN

    def test_classification_never_raises(self) -> None:
        # Total by design: one strange row must not fail a whole report.
        for value in (None, hb("0"), hb("-5"), hb("999999")):
            assert classify(value, HB) is not None


class TestOneSidedMarkers:
    LDL = ReferenceRange(low=None, high=CanonicalValue.of("100", MG_DL), source=RangeSource.LAB)

    def test_a_very_low_value_is_normal(self) -> None:
        # LDL has no meaningful lower bound. Applying two-sided logic would alarm
        # someone over a good result.
        result = classify(CanonicalValue.of("40", MG_DL), self.LDL)

        assert result.band is Band.NORMAL
        assert result.direction is Direction.WITHIN

    def test_approaching_the_upper_limit_is_borderline(self) -> None:
        assert classify(CanonicalValue.of("95", MG_DL), self.LDL).band is Band.BORDERLINE

    def test_above_the_limit_is_out_of_range(self) -> None:
        result = classify(CanonicalValue.of("130", MG_DL), self.LDL)

        assert result.band is Band.OUT_OF_RANGE
        assert result.direction is Direction.HIGH

    def test_a_one_sided_range_has_no_position(self) -> None:
        # None, not 0. Returning 0 would be a lie the UI would render as "at the
        # bottom of the healthy range".
        assert classify(CanonicalValue.of("40", MG_DL), self.LDL).position is None


class TestCriticalValues:
    def _critical(self, status: EntryStatus) -> CriticalValue:
        return CriticalValue(
            canonical_test_id=CanonicalTestId("haemoglobin"),
            comparator="lt",
            threshold="7.0",
            unit="g/dL",
            message_template="This result is very low. Please contact a doctor today.",
            source="Royal College of Pathologists 2024",
            status=status,
        )

    def test_an_approved_threshold_fires(self) -> None:
        result = classify(hb("5.0"), HB, critical_values=[self._critical(EntryStatus.APPROVED)])

        assert result.band is Band.NEEDS_ATTENTION
        # A stored template, never generated. The exact wording is the point.
        assert result.critical_message == (
            "This result is very low. Please contact a doctor today."
        )

    def test_an_unreviewed_threshold_does_not_fire(self) -> None:
        result = classify(hb("5.0"), HB, critical_values=[self._critical(EntryStatus.PROPOSED)])

        # A threshold nobody checked must not tell someone to seek care today.
        assert result.band is Band.OUT_OF_RANGE
        assert result.critical_message is None

    def test_a_value_above_the_threshold_does_not_fire(self) -> None:
        result = classify(hb("11.0"), HB, critical_values=[self._critical(EntryStatus.APPROVED)])

        assert result.band is Band.OUT_OF_RANGE

    def test_a_threshold_in_another_unit_is_ignored(self) -> None:
        critical = CriticalValue(
            canonical_test_id=CanonicalTestId("haemoglobin"),
            comparator="lt",
            threshold="70",
            unit="g/L",  # same clinical meaning, different unit
            message_template="x",
            source="s",
            status=EntryStatus.APPROVED,
        )

        # 5.0 g/dL is below 70 numerically, and comparing them would be nonsense.
        assert classify(hb("5.0"), HB, critical_values=[critical]).band is Band.OUT_OF_RANGE

    def test_critical_outranks_the_lab_range(self) -> None:
        generous = ReferenceRange(
            low=CanonicalValue.of("4.0", G_DL),
            high=CanonicalValue.of("20.0", G_DL),
            source=RangeSource.LAB,
        )

        result = classify(
            hb("5.0"), generous, critical_values=[self._critical(EntryStatus.APPROVED)]
        )

        # Inside this lab's unusually wide interval, and still critical. A critical
        # result is critical whatever the printed range says.
        assert result.band is Band.NEEDS_ATTENTION


class TestRangeResolution:
    def test_the_printed_range_is_used(self) -> None:
        resolved = resolve_range(HB, PatientContext(age_years=64, sex="female"))

        # Laboratories differ in machines and methods, so the same haemoglobin can
        # be normal at one lab and low at another. The lab's own range is the
        # correct answer for its own report.
        assert resolved is HB

    def test_no_printed_range_resolves_to_nothing(self) -> None:
        # No fallback table of our own invention. Nothing to judge against means
        # the row is shown as unknown rather than judged against a guess.
        assert resolve_range(None, PatientContext()) is None
