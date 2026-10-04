"""Deciding what a measured value means.

**Code decides the band, never the model.** Ask a model twice whether 6.1 is
borderline and you can get two answers — same report, two verdicts. In a health
product that destroys trust permanently, and you cannot write a test for it.
Comparing a number to a range is something computers have done perfectly since
1970. See ADR 0002.

Range resolution is a **chain**: each resolver either answers or passes along.
Adding a source later is a new class appended to a list, not an edit to the
classifier — so the thing that decides what a user is told stays closed to
modification.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.domain.models.clinical import CriticalValue
from app.domain.models.enums import Band, Direction, RangeSource
from app.domain.models.measurement import CanonicalValue, ReferenceRange

BORDERLINE_MARGIN = Decimal("0.10")
"""How close to an edge counts as "watch this", as a fraction of the interval.

A judgement call, not a clinical constant. It is here, named, and testable rather
than scattered as a magic number - so changing it is one edit and one test.
"""


@dataclass(frozen=True)
class PatientContext:
    """What a resolver may need beyond the value itself."""

    age_years: int | None = None
    sex: str | None = None


class RangeResolver(Protocol):
    """One source of a reference range."""

    def resolve(
        self, printed: ReferenceRange | None, context: PatientContext
    ) -> ReferenceRange | None: ...


class LabPrintedRange:
    """The range the lab printed next to the value. The primary source.

    Ranges legitimately differ between laboratories because of different machines
    and methods, so the same haemoglobin can be normal at one lab and low at
    another. The lab's own range is the correct answer *for that report*, and
    nothing we could store would be more authoritative.
    """

    def resolve(
        self,
        printed: ReferenceRange | None,
        context: PatientContext,  # noqa: ARG002
    ) -> ReferenceRange | None:
        # context is part of the RangeResolver contract and unused here by design:
        # the lab already applied age and sex when it printed this interval.
        # A resolver that consults our own tables will need it.
        return printed


# Order is priority order. A guideline resolver - reviewed thresholds that override
# a lab range, such as HbA1c - will be prepended here once entries exist. It is
# deliberately absent rather than stubbed: an empty override layer that silently
# does nothing is worse than no layer at all.
DEFAULT_RESOLVERS: list[RangeResolver] = [LabPrintedRange()]


def resolve_range(
    printed: ReferenceRange | None,
    context: PatientContext,
    resolvers: list[RangeResolver] | None = None,
) -> ReferenceRange | None:
    for resolver in resolvers or DEFAULT_RESOLVERS:
        found = resolver.resolve(printed, context)
        if found is not None:
            return found
    return None


@dataclass(frozen=True)
class Classification:
    band: Band
    direction: Direction
    position: Decimal | None = None
    range_source: RangeSource | None = None
    critical_message: str | None = None
    """Set only when an APPROVED critical threshold fired.

    A stored template, never generated. A model phrasing this softly on a bad day
    means someone with a dangerous result reads "you may want to discuss this".
    """


def classify(
    value: CanonicalValue | None,
    reference: ReferenceRange | None,
    *,
    critical_values: list[CriticalValue] | None = None,
    margin: Decimal = BORDERLINE_MARGIN,
) -> Classification:
    """Decide the band. Pure, total, and deterministic.

    Every input combination yields an answer - there is no path that raises - so a
    strange row produces an honest ``unknown`` rather than failing a whole report.
    """
    if value is None:
        # Extraction could not read a number here. Shown to the user as such,
        # never guessed: "we could not read this row" is a better product than a
        # confident wrong value.
        return Classification(band=Band.UNREADABLE, direction=Direction.UNDETERMINED)

    if reference is None:
        # A value with nothing to judge it against. Displayed, not judged.
        return Classification(band=Band.UNKNOWN, direction=Direction.UNDETERMINED)

    if value.unit != reference.unit:
        # Comparing mg/dL against mmol/L would produce a confident wrong band.
        # Normalisation should have converted or left them apart; reaching here
        # means we genuinely cannot judge.
        return Classification(band=Band.UNKNOWN, direction=Direction.UNDETERMINED)

    direction = reference.direction_of(value)
    position = reference.position_of(value)

    critical = _first_critical(value, critical_values or [])
    if critical is not None:
        # Checked before the range, because a critical result is critical whatever
        # the lab's interval says.
        return Classification(
            band=Band.NEEDS_ATTENTION,
            direction=direction,
            position=position,
            range_source=reference.source,
            critical_message=critical.message_template,
        )

    if direction is not Direction.WITHIN:
        return Classification(
            band=Band.OUT_OF_RANGE,
            direction=direction,
            position=position,
            range_source=reference.source,
        )

    band = Band.BORDERLINE if _near_an_edge(value, reference, margin) else Band.NORMAL
    return Classification(
        band=band,
        direction=direction,
        position=position,
        range_source=reference.source,
    )


def _near_an_edge(value: CanonicalValue, reference: ReferenceRange, margin: Decimal) -> bool:
    """Inside the range, but close enough to an edge to be worth watching.

    The boundary itself counts. A haemoglobin sitting exactly on 12.0 in a
    12.0-15.5 range is technically normal and genuinely worth a second look -
    treating it as unremarkable would waste the one signal the number carries.
    """
    low, high = reference.low, reference.high

    if low is not None and high is not None:
        span = high.amount - low.amount
        if span <= 0:
            return False
        edge = span * margin
        return value.amount <= low.amount + edge or value.amount >= high.amount - edge

    # One-sided markers - LDL, triglycerides, ESR. The margin is a fraction of the
    # single bound, since there is no span to take a fraction of. "Low LDL" is
    # never flagged, here or anywhere.
    if high is not None:
        return value.amount >= high.amount - (abs(high.amount) * margin)
    if low is not None:
        return value.amount <= low.amount + (abs(low.amount) * margin)
    return False


def _first_critical(value: CanonicalValue, criticals: list[CriticalValue]) -> CriticalValue | None:
    for critical in criticals:
        if not critical.is_active:
            # Belt and braces: the repository already filters to approved, and an
            # unreviewed threshold must never tell someone to seek care today.
            continue
        if critical.unit != str(value.unit):
            continue
        threshold = Decimal(critical.threshold)
        if critical.comparator == "lt" and value.amount < threshold:
            return critical
        if critical.comparator == "gt" and value.amount > threshold:
            return critical
    return None
