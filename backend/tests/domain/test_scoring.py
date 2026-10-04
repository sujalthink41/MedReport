"""Tests for the eval scorer.

The harness that judges extraction has to be right before its verdict means
anything. A scorer that counted a miss as a pass would hide exactly the failures
it exists to surface.
"""

from dataclasses import dataclass

from tests.golden.scoring import TruthRow, combine, score_page, values_match


@dataclass
class Row:
    """Stands in for an ExtractedRow. Duck-typed, like the real scorer input."""

    raw_test_name: str
    value_text: str | None = None
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    confidence: float = 1.0


HB = TruthRow(
    raw_test_name="Haemoglobin", value_text="11.2", unit="g/dL", ref_low="12.0", ref_high="15.5"
)


class TestValueMatching:
    def test_trailing_zeros_do_not_count_as_wrong(self) -> None:
        # "11.20" and "11.2" are the same measurement printed differently.
        assert values_match("11.2", "11.20") is True

    def test_different_numbers_are_wrong(self) -> None:
        assert values_match("11.2", "1.12") is False

    def test_words_match_ignoring_case(self) -> None:
        assert values_match("Negative", "negative") is True

    def test_different_words_are_not_equated(self) -> None:
        # "Negative" and "Not Detected" may be clinically equivalent, but deciding
        # that is not the scorer's job.
        assert values_match("Negative", "Not Detected") is False

    def test_a_prefix_is_part_of_the_value(self) -> None:
        # "<0.5" is a different result from "0.5": one is below the assay's limit
        # of detection, the other is a measurement.
        assert values_match("<0.5", "0.5") is False

    def test_both_missing_matches(self) -> None:
        assert values_match(None, None) is True

    def test_one_missing_does_not_match(self) -> None:
        assert values_match("11.2", None) is False


class TestRecall:
    def test_a_found_row_counts(self) -> None:
        card = score_page([HB], [Row("Haemoglobin", "11.2", "g/dL", 12.0, 15.5)])

        assert card.row_recall == 1.0
        assert card.value_accuracy == 1.0

    def test_a_missed_row_is_named(self) -> None:
        card = score_page([HB], [])

        # The worst failure, because nobody knows to look for it.
        assert card.row_recall == 0.0
        assert card.missed == ["Haemoglobin"]

    def test_name_matching_tolerates_printing_differences(self) -> None:
        truth = [TruthRow("S.G.P.T. (ALT)", "45", "U/L")]

        card = score_page(truth, [Row("SGPT (ALT)", "45", "U/L")])

        # Deliberately forgiving. We are measuring whether the right ROW was
        # found; a trailing dot reported as a recall failure would bury the real
        # misses in noise.
        assert card.row_recall == 1.0

    def test_an_invented_row_is_reported(self) -> None:
        card = score_page([HB], [Row("Haemoglobin", "11.2", "g/dL", 12.0, 15.5), Row("Unicorn")])

        assert card.spurious == ["Unicorn"]
        assert card.spurious_rate == 0.5


class TestFalseConfidence:
    def test_wrong_and_confident_is_counted_separately(self) -> None:
        card = score_page([HB], [Row("Haemoglobin", "1.12", "g/dL", confidence=0.95)])

        # The dangerous case: a user shown a wrong value about their own body
        # with no indication anything is uncertain.
        assert card.confidently_wrong == 1
        assert card.uncertainly_wrong == 0
        assert card.false_confidence == 1.0

    def test_wrong_but_hedged_is_a_different_failure(self) -> None:
        card = score_page([HB], [Row("Haemoglobin", "1.12", "g/dL", confidence=0.3)])

        # Still wrong, but it costs a human glance rather than misinforming
        # somebody. Not equivalent, and the scorer must not conflate them.
        assert card.confidently_wrong == 0
        assert card.uncertainly_wrong == 1
        assert card.false_confidence == 0.0

    def test_no_errors_means_no_false_confidence(self) -> None:
        card = score_page([HB], [Row("Haemoglobin", "11.2", "g/dL", 12.0, 15.5)])

        assert card.false_confidence == 0.0


class TestRanges:
    def test_matching_bounds_count(self) -> None:
        card = score_page([HB], [Row("Haemoglobin", "11.2", "g/dL", 12.0, 15.5)])

        assert card.range_accuracy == 1.0

    def test_an_invented_range_is_wrong(self) -> None:
        truth = [TruthRow("Vitamin D", "18", "ng/mL", ref_low=None, ref_high=None)]

        card = score_page(truth, [Row("Vitamin D", "18", "ng/mL", ref_low=30.0, ref_high=100.0)])

        # The report printed no range and the model supplied one from memory.
        # That is fabricating the very thing we judge people against, and only a
        # null in the truth file catches it.
        assert card.range_accuracy == 0.0

    def test_a_missing_range_matches_a_missing_range(self) -> None:
        truth = [TruthRow("Vitamin D", "18", "ng/mL", ref_low=None, ref_high=None)]

        card = score_page(truth, [Row("Vitamin D", "18", "ng/mL")])

        assert card.range_accuracy == 1.0

    def test_a_one_sided_range_is_handled(self) -> None:
        truth = [TruthRow("LDL", "130", "mg/dL", ref_low=None, ref_high="100")]

        card = score_page(truth, [Row("LDL", "130", "mg/dL", ref_low=None, ref_high=100.0)])

        assert card.range_accuracy == 1.0


class TestCombining:
    def test_totals_add_up(self) -> None:
        good = score_page([HB], [Row("Haemoglobin", "11.2", "g/dL", 12.0, 15.5)])
        bad = score_page([HB], [])

        total = combine([good, bad])

        assert total.expected_rows == 2
        assert total.matched_rows == 1
        assert total.row_recall == 0.5
        assert total.missed == ["Haemoglobin"]

    def test_an_empty_set_does_not_divide_by_zero(self) -> None:
        total = combine([])

        assert total.row_recall == 0.0
        assert total.false_confidence == 0.0
