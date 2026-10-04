"""Detecting movement across reports.

The product's single strongest differentiator, and the one thing a general-purpose
chatbot cannot do for someone: it does not have their history.

Most conditions announce themselves over years while every individual report still
reads "normal". A person whose fasting glucose went 88 -> 96 -> 103 -> 109 across
four years has never once been flagged by a laboratory, because each value sat
inside that lab's interval. That drift is the most useful thing anyone could tell
them, and it is only visible to something holding all four reports.

So this module flags **values inside their range that are moving**, not just values
outside it.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from app.domain.models.measurement import CanonicalValue

SIGNIFICANT_CHANGE = Decimal("0.20")
"""Relative movement worth telling someone about.

Originally 0.25, and the first test written against it failed - on the product's
own founding example. Fasting glucose drifting 88 -> 109 across four years is a
23.9% rise, every reading inside its lab range, nobody ever flagged. That is
precisely the case this product exists to catch, and a 25% cutoff missed it.

A threshold that excludes your central use case is the wrong threshold.
"""

NOISE_THRESHOLD = Decimal("0.05")
"""Below this, call it flat.

Biological variation and assay imprecision alone move a repeat measurement by a
few percent. Describing that as "rising" would be reading signal into noise.
"""

MIN_SPAN_DAYS = 45
"""Below this, two readings are the same episode, not a trend.

Someone retested a week later because the first result looked odd. Calling that a
25% rise would manufacture alarm out of ordinary clinical follow-up.
"""


class TrendDirection(StrEnum):
    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"


@dataclass(frozen=True)
class Reading:
    """One measurement, with when the sample was taken.

    ``collected_at``, never the upload date. People upload three years of reports
    in one sitting, so ordering by upload time would scramble the history and
    destroy the only signal here.
    """

    value: CanonicalValue
    collected_at: date


@dataclass(frozen=True)
class Trend:
    direction: TrendDirection
    change: Decimal
    """Relative change, so 0.25 is a 25% rise. Signed."""

    span_days: int
    first: Reading
    latest: Reading
    readings: int

    @property
    def is_significant(self) -> bool:
        return abs(self.change) >= SIGNIFICANT_CHANGE


def detect(readings: list[Reading]) -> Trend | None:
    """Compare the earliest and latest readings of one marker.

    Returns ``None`` - no trend rather than a weak one - when the evidence is
    insufficient:

    * fewer than two readings
    * mixed units, which would compare incomparable numbers
    * too short a span, which is follow-up testing rather than drift
    * a baseline of zero, where relative change is undefined

    Deliberately earliest-versus-latest rather than a regression line. Four points
    is not enough to fit anything meaningful, and a slope would imply a precision
    the data does not have. "Your glucose is 24% higher than three years ago" is
    both true and useful; a p-value would be neither.
    """
    if len(readings) < 2:
        return None

    ordered = sorted(readings, key=lambda r: r.collected_at)
    first, latest = ordered[0], ordered[-1]

    if first.value.unit != latest.value.unit:
        # Normalisation could not bring these to one unit (CP14 refuses to guess a
        # conversion). Showing two honest series beats one fabricated line.
        return None

    span_days = (latest.collected_at - first.collected_at).days
    if span_days < MIN_SPAN_DAYS:
        return None

    baseline = first.value.amount
    if baseline == 0:
        return None

    change = (latest.value.amount - baseline) / abs(baseline)

    # Direction and significance are separate questions, and conflating them was
    # the original bug. A 23.9% rise IS rising; whether it is worth putting in
    # front of a user is a different judgement, answered by is_significant.
    if abs(change) < NOISE_THRESHOLD:
        direction = TrendDirection.STABLE
    elif change > 0:
        direction = TrendDirection.RISING
    else:
        direction = TrendDirection.FALLING

    return Trend(
        direction=direction,
        change=change,
        span_days=span_days,
        first=first,
        latest=latest,
        readings=len(ordered),
    )


def is_monotonic(readings: list[Reading]) -> bool:
    """Has every reading moved the same way?

    A steady climb across four reports is a different story from a value that
    bounced around and happens to end high. Both may be worth showing; only the
    first is worth emphasising, and conflating them would cry wolf.
    """
    if len(readings) < 3:
        return False

    ordered = sorted(readings, key=lambda r: r.collected_at)
    # pairwise, not zip(xs, xs[1:], strict=True) - the slice is one shorter, so
    # strict mode raises. This is exactly what pairwise exists for.
    if any(a.value.unit != b.value.unit for a, b in pairwise(ordered)):
        return False

    amounts = [r.value.amount for r in ordered]
    rising = all(b > a for a, b in pairwise(amounts))
    falling = all(b < a for a, b in pairwise(amounts))
    return rising or falling
