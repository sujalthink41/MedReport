"""Arithmetic verification.

The values below are taken verbatim from the first real report tested — a
scanned six-page haematology and biochemistry panel. The point of using real
numbers rather than invented ones is that invented numbers always reconcile
perfectly, and real ones carry the rounding that makes tolerance the actual
design problem.
"""

from decimal import (
    Decimal as D,  # noqa: N817 - a table of 16 values reads better with a short alias
)

from app.domain.services.cross_checks import implicated, run_checks

# Page 3 of the real report, exactly as extracted.
REAL_HAEMATOLOGY = {
    "wbc": D("15.24"),
    "neutrophils_pct": D("81.0"),
    "lymphocytes_pct": D("11.0"),
    "monocytes_pct": D("7.0"),
    "eosinophils_pct": D("1.0"),
    "basophils_pct": D("0.0"),
    "neutrophils_absolute": D("12.34"),
    "lymphocytes_absolute": D("1.68"),
    "monocytes_absolute": D("1.07"),
    "eosinophils_absolute": D("0.15"),
    "basophils_absolute": D("0.00"),
    "rbc": D("4.45"),
    "mcv": D("88.6"),
    "mch": D("30.1"),
    "mchc": D("34.0"),
    "pcv": D("39.4"),
}

# Page 1 of the same report.
REAL_BIOCHEMISTRY = {
    "total_protein": D("7.00"),
    "albumin": D("3.28"),
    "globulin": D("3.72"),
    "bilirubin_total": D("0.69"),
    "bilirubin_direct": D("0.27"),
    "bilirubin_indirect": D("0.42"),
}


class TestRealReport:
    def test_every_check_passes_on_correctly_extracted_values(self) -> None:
        outcomes = run_checks(REAL_HAEMATOLOGY)

        assert len(outcomes) == 8
        failures = [o.name for o in outcomes if not o.passed]
        assert failures == []

    def test_biochemistry_reconciles_too(self) -> None:
        outcomes = run_checks(REAL_BIOCHEMISTRY)

        assert [o.name for o in outcomes if not o.passed] == []
        assert len(outcomes) == 2

    def test_nothing_is_implicated_when_extraction_was_right(self) -> None:
        assert implicated(run_checks(REAL_HAEMATOLOGY)) == set()


class TestCatchingMisreads:
    def test_a_lost_decimal_point_is_caught(self) -> None:
        # The failure mode the prompt warns about and confidence never flagged:
        # 12.34 read as 123.4. A factor of ten in an absolute neutrophil count
        # is the difference between normal and a haematology referral.
        broken = {**REAL_HAEMATOLOGY, "neutrophils_absolute": D("123.4")}

        outcomes = run_checks(broken)

        failed = [o for o in outcomes if not o.passed]
        assert len(failed) == 1
        assert "neutrophils" in failed[0].name

    def test_a_transposed_digit_is_caught(self) -> None:
        # 81.0 read as 18.0. Plausible-looking, confidently reported, and wrong.
        broken = {**REAL_HAEMATOLOGY, "neutrophils_pct": D("18.0")}

        failed = [o.name for o in run_checks(broken) if not o.passed]

        # Breaks TWO independent checks - the differential no longer sums to 100
        # and the absolute count no longer matches. Independent confirmation.
        assert len(failed) == 2

    def test_a_1_read_as_7_is_caught(self) -> None:
        broken = {**REAL_BIOCHEMISTRY, "albumin": D("7.28")}

        failed = [o.name for o in run_checks(broken) if not o.passed]

        assert len(failed) >= 1

    def test_the_suspects_are_named(self) -> None:
        broken = {**REAL_HAEMATOLOGY, "mchc": D("44.0")}

        suspects = implicated(run_checks(broken))

        # Narrows it to three rows a human should look at, rather than offering
        # a confidence score that was never calibrated.
        assert suspects == {"mch", "mcv", "mchc"}

    def test_correct_rows_are_not_implicated(self) -> None:
        broken = {**REAL_HAEMATOLOGY, "mchc": D("44.0")}

        suspects = implicated(run_checks(broken))

        assert "wbc" not in suspects
        assert "neutrophils_pct" not in suspects


class TestTolerance:
    def test_normal_rounding_passes(self) -> None:
        # 81.0% of 15.24 is 12.3444, printed as 12.34. A tolerance that rejected
        # this would fire on every correctly-extracted report.
        outcomes = run_checks(REAL_HAEMATOLOGY)
        absolute = next(o for o in outcomes if "neutrophils" in o.name)

        assert absolute.passed
        assert absolute.expected != absolute.actual  # genuinely not equal

    def test_the_differential_tolerates_five_roundings(self) -> None:
        # Each percentage is rounded independently, so the sum can drift.
        drifted = {**REAL_HAEMATOLOGY, "basophils_pct": D("0.7")}

        total = next(o for o in run_checks(drifted) if "sums to 100" in o.name)
        assert total.passed

    def test_but_a_real_mistake_still_fails(self) -> None:
        wrong = {**REAL_HAEMATOLOGY, "basophils_pct": D("10.0")}

        total = next(o for o in run_checks(wrong) if "sums to 100" in o.name)
        assert not total.passed


class TestPartialReports:
    def test_a_missing_input_skips_the_check(self) -> None:
        # Most reports carry only a few panels. Treating absence as failure would
        # make the signal useless on the majority of real documents.
        outcomes = run_checks({"wbc": D("15.24")})

        assert outcomes == []

    def test_a_missing_target_skips_the_check(self) -> None:
        # The lab computed the percentages but did not print absolute counts.
        no_absolutes = {k: v for k, v in REAL_HAEMATOLOGY.items() if not k.endswith("_absolute")}

        names = [o.name for o in run_checks(no_absolutes)]

        assert "absolute neutrophils = pct x WBC" not in names
        assert "differential sums to 100" in names

    def test_an_empty_report_is_not_an_error(self) -> None:
        assert run_checks({}) == []

    def test_a_zero_denominator_does_not_raise(self) -> None:
        # A/G ratio with globulin of zero. Skipped, never a crash: one strange
        # row must not fail a whole report.
        values = {"albumin": D("3.28"), "globulin": D("0"), "ag_ratio": D("0.9")}

        assert run_checks(values) is not None
