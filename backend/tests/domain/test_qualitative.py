"""Judging word results against the report's printed expectation.

Every case below is taken from the real report, which is where the gap was
found: urine glucose reading DETECTED +++ against an expectation of Negative,
and landing in "not judged" alongside the colour of the sample.
"""

import pytest

from app.domain.services.qualitative import (
    QualitativeVerdict,
    compare,
    magnitude_of,
    normalize_term,
)


class TestTheFindingThatWasMissed:
    def test_glucose_detected_against_expected_negative_is_a_deviation(self) -> None:
        # The case this module exists for. Printed on page 5 of the real report,
        # and previously dropped into "shown but not judged".
        assert compare("DETECTED +++", "Negative") is QualitativeVerdict.DEVIATES

    def test_protein_not_detected_against_expected_negative_matches(self) -> None:
        # Same page, same expectation, opposite result. Correctly unremarkable.
        assert compare("NOT DETECTED", "Negative") is QualitativeVerdict.MATCHES


class TestSynonyms:
    @pytest.mark.parametrize(
        "result", ["Negative", "NOT DETECTED", "Nil", "Absent", "Non Reactive", "None"]
    )
    def test_ways_of_writing_nothing_found(self, result: str) -> None:
        # Vocabulary, not clinical judgement. No entry here says what any of
        # these means for a patient.
        assert compare(result, "Negative") is QualitativeVerdict.MATCHES

    @pytest.mark.parametrize("result", ["Positive", "DETECTED", "Present", "Reactive"])
    def test_ways_of_writing_found(self, result: str) -> None:
        assert compare(result, "Negative") is QualitativeVerdict.DEVIATES

    def test_a_sentence_result_is_matched_on_its_opening(self) -> None:
        # Verbatim from page 4. Labs write prose in result columns.
        verdict = compare(
            "No growth in culture after 2 days of aerobic incubation at 37C",
            "No growth",
        )

        assert verdict is QualitativeVerdict.MATCHES

    def test_case_and_punctuation_do_not_matter(self) -> None:
        assert compare("non-reactive", "Non Reactive") is QualitativeVerdict.MATCHES


class TestMagnitude:
    @pytest.mark.parametrize(("printed", "expected"), [("DETECTED +++", 3), ("+", 1), ("Trace", 0)])
    def test_plus_signs_are_counted(self, printed: str, expected: int) -> None:
        assert magnitude_of(printed) == expected

    def test_magnitude_does_not_affect_matching(self) -> None:
        # A result is either what was expected or it is not. The magnitude is
        # kept so the explanation can say "+++ is a strong finding", never to
        # decide whether it is a finding at all.
        assert compare("DETECTED +", "Negative") is QualitativeVerdict.DEVIATES
        assert compare("DETECTED +++", "Negative") is QualitativeVerdict.DEVIATES


class TestRefusal:
    def test_no_printed_expectation_is_undecidable(self) -> None:
        # Most qualitative rows print no expectation at all.
        assert compare("PALE YELLOW", None) is QualitativeVerdict.UNDECIDABLE

    def test_an_unrecognised_term_is_undecidable(self) -> None:
        # Colours and descriptive phrases. Guessing either way is wrong: calling
        # CLEAR a deviation from "Clear-slightly hazy" would be noise, and the
        # opposite would be a missed finding.
        assert compare("CLEAR", "Clear-slightly hazy") is QualitativeVerdict.UNDECIDABLE
        assert compare("PALE YELLOW", "Watery-Pale Yellow") is QualitativeVerdict.UNDECIDABLE

    def test_an_empty_result_is_undecidable(self) -> None:
        assert compare(None, "Negative") is QualitativeVerdict.UNDECIDABLE
        assert compare("", "Negative") is QualitativeVerdict.UNDECIDABLE


class TestNormalisation:
    def test_plus_markers_are_stripped(self) -> None:
        assert normalize_term("DETECTED +++") == "detected"

    def test_punctuation_is_stripped(self) -> None:
        assert normalize_term("Non-Reactive") == "non reactive"
