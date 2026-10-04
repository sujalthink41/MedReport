"""Unit conversion, derived from the documents rather than from a lookup table.

The usual approach is a table of molecular weights: glucose mg/dL to mmol/L is
divide by 18.016, creatinine by 88.4, and so on. That is medical knowledge typed
in by hand, and a single wrong digit silently produces wrong numbers forever.

There is a better source, and it is already on the page.

**A lab prints its own reference range in its own units.** So if one report says

    Glucose   100 mg/dL    (70 - 100)

and another says

    Glucose   5.5 mmol/L   (3.9 - 5.6)

then the conversion factor is sitting right there: ``70 / 3.9 = 17.95`` and
``100 / 5.6 = 17.86``. Both bounds agree to within a rounding error, so the factor
is about 17.9 — and the published value is 18.016. Derived from two documents,
with no knowledge asserted by us.

Better still, it is **self-checking**. If the two bounds disagree, the two reports
are not measuring the same thing the same way, and we refuse to convert rather than
produce a confident wrong number.

When no factor can be derived, a trend simply does not join across those units. The
user sees two honest series instead of one fabricated one.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.domain.models.measurement import CanonicalValue, ReferenceRange, Unit

AGREEMENT_TOLERANCE = Decimal("0.05")
"""How far the two derived ratios may differ, as a fraction.

Lab ranges are printed rounded - 3.9 rather than 3.886 - so the low and high
ratios never match exactly. 5% absorbs that while still rejecting a genuine
mismatch, where the ratios differ by a factor rather than a few percent.
"""

MIN_PLAUSIBLE_FACTOR = Decimal("0.000001")
MAX_PLAUSIBLE_FACTOR = Decimal("1000000")


@dataclass(frozen=True)
class ConversionFactor:
    """Multiply a value in ``from_unit`` by ``factor`` to get ``to_unit``."""

    from_unit: Unit
    to_unit: Unit
    factor: Decimal
    agreement: Decimal | None = None
    """How closely the two bounds agreed when this was derived, as a fraction.

    Kept because it is the quality signal: a factor derived from ranges that
    agreed to 0.2% is far more trustworthy than one that scraped in at 4.9%.
    """

    def apply(self, value: CanonicalValue) -> CanonicalValue:
        if value.unit != self.from_unit:
            from app.domain.errors import InvalidInputError

            raise InvalidInputError(
                field="unit",
                reason=f"factor converts from {self.from_unit}, got {value.unit}",
            )
        return CanonicalValue(amount=value.amount * self.factor, unit=self.to_unit)

    def inverse(self) -> "ConversionFactor":
        return ConversionFactor(
            from_unit=self.to_unit,
            to_unit=self.from_unit,
            factor=Decimal(1) / self.factor,
            agreement=self.agreement,
        )


def derive_factor(source: ReferenceRange, target: ReferenceRange) -> ConversionFactor | None:
    """Work out how to convert between two units, from two printed ranges.

    Returns ``None`` - refusing to guess - whenever the evidence is insufficient:

    * either range is one-sided, so there is only one ratio and nothing to check
      it against
    * a bound is zero, which makes the ratio meaningless
    * the two ratios disagree by more than the tolerance, which means these are
      not the same measurement and converting would invent a number

    Refusing is the right outcome. An unconvertible pair shows the user two honest
    series; a wrong factor shows them one confident, fabricated line.
    """
    if not source.is_two_sided or not target.is_two_sided:
        return None
    if source.unit == target.unit:
        return None

    s_low, s_high = source.low, source.high
    t_low, t_high = target.low, target.high
    if s_low is None or s_high is None or t_low is None or t_high is None:
        return None
    if t_low.amount == 0 or t_high.amount == 0:
        return None

    try:
        low_ratio = s_low.amount / t_low.amount
        high_ratio = s_high.amount / t_high.amount
    except (InvalidOperation, ZeroDivisionError):
        return None

    if low_ratio <= 0 or high_ratio <= 0:
        return None

    # The self-check. Two independent estimates of the same constant, so if they
    # disagree the premise is wrong - these are not the same assay, or one report
    # was misread.
    spread = abs(low_ratio - high_ratio) / min(low_ratio, high_ratio)
    if spread > AGREEMENT_TOLERANCE:
        return None

    factor = (low_ratio + high_ratio) / 2
    if not (MIN_PLAUSIBLE_FACTOR <= factor <= MAX_PLAUSIBLE_FACTOR):
        # A factor of 10^9 is an extraction error, not a unit system.
        return None

    return ConversionFactor(
        from_unit=target.unit,
        to_unit=source.unit,
        factor=factor,
        agreement=spread,
    )


def agrees_with(proposed: Decimal, derived: ConversionFactor) -> bool:
    """Does a factor suggested by the model match one derived from documents?

    This is how a model proposal earns trust. The model may well know that glucose
    is 18.016, and that is useful - but we check it against evidence from the
    actual reports before believing it, rather than taking a recalled constant on
    faith.
    """
    if derived.factor <= 0 or proposed <= 0:
        return False
    spread = abs(proposed - derived.factor) / derived.factor
    return spread <= AGREEMENT_TOLERANCE


def convert_range(reference: ReferenceRange, factor: ConversionFactor) -> ReferenceRange:
    """Convert a whole range, so a value and its bounds stay comparable.

    Both ends move by the same factor, which is why converting never changes
    whether a value sits inside its range - only which numbers are displayed.
    """
    return ReferenceRange(
        low=factor.apply(reference.low) if reference.low else None,
        high=factor.apply(reference.high) if reference.high else None,
        source=reference.source,
    )
