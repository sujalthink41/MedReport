"""Turning what a lab printed into values we can compare.

Pure, and deliberately conservative: anything this cannot parse confidently
becomes ``None``, and a ``None`` value is shown to the user as "we could not
judge this" rather than guessed at.

The hard cases are not the numbers. They are everything else a lab prints in a
result column::

    "11.2"            an ordinary measurement
    "<0.5"            below the assay's limit of detection - NOT 0.5
    ">1000"           above the measurable range
    "Negative"        qualitative
    "DETECTED +++"    qualitative with a magnitude
    "1:40"            a titre
    "2-3"             a microscopy range, not a single value
    "11,200"          thousands separator
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.domain.models.enums import RangeSource
from app.domain.models.measurement import CanonicalValue, ReferenceRange, Unit


class ValueKind(StrEnum):
    NUMERIC = "numeric"
    BOUNDED = "bounded"
    """Below or above the measurable range: "<0.5", ">1000".

    Kept distinct from numeric because the two behave differently. A TSH of
    "<0.01" is not 0.01 - it is "too low to measure", which is clinically
    stronger than the number suggests, and treating it as 0.01 would understate
    it on a chart.
    """

    QUALITATIVE = "qualitative"
    TITRE = "titre"
    RANGE = "range"
    """A printed interval like "2-3" from microscopy. One cell count, not two."""

    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class ParsedValue:
    kind: ValueKind
    value: CanonicalValue | None
    text: str
    bound: str | None = None
    """``lt`` or ``gt`` for a bounded result."""

    @property
    def is_comparable(self) -> bool:
        """Can this be judged against a range, or trended?

        Only plain numbers. A qualitative result is displayed and explained but
        never banded by arithmetic, and a bounded one would mislead a trend line.
        """
        return self.kind is ValueKind.NUMERIC and self.value is not None


_NUMERIC = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_BOUNDED = re.compile(r"^(?P<op>[<>]=?)\s*(?P<num>[+-]?\d+(?:\.\d+)?)$")
_TITRE = re.compile(r"^\d+\s*:\s*\d+$")
_RANGE = re.compile(r"^(?P<low>\d+(?:\.\d+)?)\s*[-–]\s*(?P<high>\d+(?:\.\d+)?)$")
_RANGE_TEXT = re.compile(
    r"(?P<low>[+-]?\d+(?:\.\d+)?)\s*[-–to]+\s*(?P<high>[+-]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_SINGLE_BOUND = re.compile(r"(?P<op>[<>]=?)\s*(?P<num>[+-]?\d+(?:\.\d+)?)")


def parse_value(text: str | None, unit: str | None) -> ParsedValue:
    """Classify and parse one printed result."""
    if text is None or not text.strip():
        return ParsedValue(kind=ValueKind.UNREADABLE, value=None, text=text or "")

    raw = text.strip()
    # Thousands separators only - never a decimal comma. Indian lab reports use
    # a full stop for decimals, and guessing between the two conventions would
    # turn 1,5 into fifteen.
    cleaned = raw.replace(",", "") if re.fullmatch(r"[\d,]+(?:\.\d+)?", raw) else raw

    if _NUMERIC.match(cleaned):
        return ParsedValue(
            kind=ValueKind.NUMERIC,
            value=_as_value(cleaned, unit),
            text=raw,
        )

    bounded = _BOUNDED.match(cleaned)
    if bounded:
        return ParsedValue(
            kind=ValueKind.BOUNDED,
            value=_as_value(bounded.group("num"), unit),
            text=raw,
            bound="lt" if bounded.group("op").startswith("<") else "gt",
        )

    if _TITRE.match(cleaned):
        return ParsedValue(kind=ValueKind.TITRE, value=None, text=raw)

    if _RANGE.match(cleaned):
        return ParsedValue(kind=ValueKind.RANGE, value=None, text=raw)

    return ParsedValue(kind=ValueKind.QUALITATIVE, value=None, text=raw)


def parse_range(
    ref_text: str | None,
    ref_low: float | None,
    ref_high: float | None,
    unit: str | None,
    source: RangeSource = RangeSource.LAB,
) -> ReferenceRange | None:
    """Build a reference range from what the model read.

    ``ref_text`` is preferred over the numeric fields because it is the literal
    string on the page: JSON numbers arrive as floats, and ``0.1`` as a float is
    not exactly ``0.1``. Re-parsing the text keeps the Decimal exact.

    Returns ``None`` whenever the report printed no usable interval - which is a
    fact worth preserving, not a gap to fill. A row with no range is shown
    unjudged rather than measured against something we invented.
    """
    measure_unit = Unit(unit or "")

    if ref_text:
        parsed = _range_from_text(ref_text.strip(), measure_unit, source)
        if parsed is not None:
            return parsed

    low = _decimal(ref_low)
    high = _decimal(ref_high)
    if low is None and high is None:
        return None

    try:
        return ReferenceRange(
            low=CanonicalValue(amount=low, unit=measure_unit) if low is not None else None,
            high=CanonicalValue(amount=high, unit=measure_unit) if high is not None else None,
            source=source,
        )
    except Exception:  # noqa: BLE001
        # An invalid interval - low above high, or both absent - is the lab's or
        # the model's mistake, not something to crash a report over.
        return None


def _range_from_text(text: str, unit: Unit, source: RangeSource) -> ReferenceRange | None:
    both = _RANGE_TEXT.search(text)
    if both:
        try:
            return ReferenceRange(
                low=CanonicalValue(amount=Decimal(both.group("low")), unit=unit),
                high=CanonicalValue(amount=Decimal(both.group("high")), unit=unit),
                source=source,
            )
        except Exception:  # noqa: BLE001
            return None

    single = _SINGLE_BOUND.search(text)
    if single:
        # "< 200" is an upper bound with no lower one. Inventing a lower bound of
        # zero would make every low LDL look like a finding.
        amount = Decimal(single.group("num"))
        upper = single.group("op").startswith("<")
        try:
            return ReferenceRange(
                low=None if upper else CanonicalValue(amount=amount, unit=unit),
                high=CanonicalValue(amount=amount, unit=unit) if upper else None,
                source=source,
            )
        except Exception:  # noqa: BLE001
            return None

    # "Negative", "Non Reactive" - a qualitative expectation, not an interval.
    return None


def _as_value(text: str, unit: str | None) -> CanonicalValue | None:
    try:
        return CanonicalValue(amount=Decimal(text), unit=Unit(unit or ""))
    except (InvalidOperation, ValueError, Exception):  # noqa: BLE001
        return None


def _decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    try:
        # str() first: Decimal(0.1) is 0.1000000000000000055511151231257827,
        # while Decimal(str(0.1)) is exactly 0.1.
        return Decimal(str(value))
    except InvalidOperation:
        return None
