"""Deriving unit conversions from printed ranges.

No molecular weights anywhere. Every factor here comes out of two documents, and
the published constants only appear in assertions - as a check that the derivation
lands where reality is, never as an input.
"""

from decimal import Decimal

import pytest

from app.domain.models.enums import RangeSource
from app.domain.models.measurement import CanonicalValue, ReferenceRange, Unit
from app.domain.services.units import (
    agrees_with,
    convert_range,
    derive_factor,
)

MG_DL = Unit("mg/dL")
MMOL_L = Unit("mmol/L")
UMOL_L = Unit("umol/L")
NG_ML = Unit("ng/mL")


def rng(low: str | None, high: str | None, unit: Unit) -> ReferenceRange:
    return ReferenceRange(
        low=CanonicalValue.of(low, unit) if low else None,
        high=CanonicalValue.of(high, unit) if high else None,
        source=RangeSource.LAB,
    )


class TestDerivation:
    def test_glucose_factor_falls_out_of_two_reports(self) -> None:
        # Report A (an Indian lab):  70 - 100 mg/dL
        # Report B (a European lab): 3.9 - 5.6 mmol/L
        factor = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))

        assert factor is not None
        # The published constant is 18.016. We never told the system that; it came
        # out of two reference ranges printed on two documents.
        assert Decimal("17.5") < factor.factor < Decimal("18.5")
        assert factor.from_unit == MMOL_L
        assert factor.to_unit == MG_DL

    def test_creatinine_factor_falls_out_too(self) -> None:
        # 0.6 - 1.1 mg/dL  vs  53 - 97 umol/L. Published constant: 88.4.
        factor = derive_factor(rng("0.6", "1.1", MG_DL), rng("53", "97", UMOL_L))

        assert factor is not None
        assert Decimal("0.0108") < factor.factor < Decimal("0.0117")

    def test_the_agreement_quality_is_reported(self) -> None:
        factor = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))

        assert factor is not None
        assert factor.agreement is not None
        # A factor whose two bounds agreed to a fraction of a percent is far more
        # trustworthy than one that scraped past the tolerance.
        assert factor.agreement < Decimal("0.01")

    def test_a_derived_factor_round_trips(self) -> None:
        factor = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))
        assert factor is not None

        original = CanonicalValue.of("5.5", MMOL_L)
        there_and_back = factor.inverse().apply(factor.apply(original))

        assert abs(there_and_back.amount - original.amount) < Decimal("0.0001")


class TestRefusal:
    def test_disagreeing_bounds_are_refused(self) -> None:
        # These two ranges cannot be the same measurement: the low ratio is 10 and
        # the high ratio is 2. Converting would invent a number.
        assert derive_factor(rng("10", "20", MG_DL), rng("1", "10", MMOL_L)) is None

    def test_a_one_sided_range_is_refused(self) -> None:
        # One ratio and nothing to check it against. A single data point cannot
        # validate itself, and a plausible-looking wrong factor is worse than none.
        assert derive_factor(rng(None, "100", MG_DL), rng(None, "5.6", MMOL_L)) is None

    def test_a_zero_bound_is_refused(self) -> None:
        assert derive_factor(rng("70", "100", MG_DL), rng("0", "5.6", MMOL_L)) is None

    def test_identical_units_need_no_factor(self) -> None:
        assert derive_factor(rng("70", "100", MG_DL), rng("70", "100", MG_DL)) is None

    def test_an_absurd_factor_is_refused(self) -> None:
        # 10^9 is an extraction error - a misplaced decimal or a misread column -
        # not a unit system anybody uses.
        assert (
            derive_factor(rng("70", "100", MG_DL), rng("0.00000007", "0.0000001", MMOL_L)) is None
        )

    @pytest.mark.parametrize("spread", ["0.02", "0.04"])
    def test_normal_rounding_is_tolerated(self, spread: str) -> None:
        # Labs print 3.9, not 3.886. The tolerance has to absorb that or almost
        # nothing would ever convert.
        drift = Decimal(1) + Decimal(spread)
        target = rng("3.9", str(Decimal("5.6") * drift), MMOL_L)

        assert derive_factor(rng("70", "100", MG_DL), target) is not None


class TestModelProposals:
    def test_a_correct_proposal_is_accepted(self) -> None:
        derived = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))
        assert derived is not None

        # The model recalls 18.016. We check that against evidence from the actual
        # documents rather than taking a remembered constant on faith.
        assert agrees_with(Decimal("18.016"), derived) is True

    def test_a_hallucinated_proposal_is_rejected(self) -> None:
        derived = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))
        assert derived is not None

        # A plausible-looking but wrong constant - the exact failure mode of
        # trusting recalled numbers.
        assert agrees_with(Decimal("88.4"), derived) is False

    def test_nonsense_proposals_are_rejected(self) -> None:
        derived = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))
        assert derived is not None

        assert agrees_with(Decimal("0"), derived) is False
        assert agrees_with(Decimal("-18"), derived) is False


class TestApplying:
    def test_converting_a_value(self) -> None:
        factor = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))
        assert factor is not None

        converted = factor.apply(CanonicalValue.of("5.5", MMOL_L))

        assert converted.unit == MG_DL
        assert Decimal("96") < converted.amount < Decimal("102")

    def test_applying_to_the_wrong_unit_raises(self) -> None:
        from app.domain.errors import InvalidInputError

        factor = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))
        assert factor is not None

        # Converting a ng/mL value with a mmol/L factor would be silently wrong,
        # so the type of the value is checked rather than assumed.
        with pytest.raises(InvalidInputError):
            factor.apply(CanonicalValue.of("5.5", NG_ML))

    def test_converting_a_range_preserves_membership(self) -> None:
        factor = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))
        assert factor is not None
        original = rng("3.9", "5.6", MMOL_L)
        value = CanonicalValue.of("5.5", MMOL_L)

        converted_range = convert_range(original, factor)
        converted_value = factor.apply(value)

        # Both ends move by the same factor, so conversion changes which numbers
        # are displayed and never whether someone is in range.
        assert original.contains(value)
        assert converted_range.contains(converted_value)
        assert converted_range.unit == MG_DL
