"""Units, values and reference ranges.

The most important file in the domain, because this is where a whole class of bug
is made impossible rather than merely unlikely.

Consider the version almost everyone writes first::

    def is_high(value: float, limit: float) -> bool:
        return value > limit

Nothing stops you passing mg/dL where mmol/L was expected. It compiles, it runs, and
it silently reports the wrong band on someone's health report. Here, that same
mistake is a ``TypeError`` at the moment you write it.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import total_ordering
from typing import Self

from app.domain.errors import InvalidInputError
from app.domain.models.enums import Direction, RangeSource


@dataclass(frozen=True, slots=True)
class Unit:
    """A unit of measurement, e.g. ``g/dL``.

    Not an enum: labs report hundreds of units and new assays appear constantly. An
    enum would need editing every time we met one, which is exactly the kind of
    change we do not want in the domain.

    ``frozen=True`` makes it immutable and hashable, so it works as a dict key.
    ``slots=True`` drops the per-instance ``__dict__`` - these are created once per
    extracted row, and a 25-page report has hundreds.
    """

    symbol: str

    def __post_init__(self) -> None:
        # Normalise rather than reject. An empty symbol is the *dimensionless* unit,
        # which is a real clinical thing: A/G ratio, INR, and various indices are
        # printed with no unit at all.
        #
        # The first draft of this class raised on an empty symbol, on the theory that
        # a missing unit is a bug. That was wrong twice over: it made a legitimate
        # value unrepresentable, and it was not buying safety anyway. The property
        # that protects us is that units must *match* to compare - and dimensionless
        # does not match mg/dL, so an accidentally-missing unit still raises loudly
        # the moment anyone tries to judge it against a range.
        #
        # object.__setattr__ because the dataclass is frozen; this is the sanctioned
        # way to normalise a field during construction.
        object.__setattr__(self, "symbol", self.symbol.strip())

    def __str__(self) -> str:
        return self.symbol


DIMENSIONLESS = Unit("")
"""Ratios, indices and counts printed without a unit."""


@total_ordering
@dataclass(frozen=True, slots=True)
class CanonicalValue:
    """A number with its unit attached, after conversion to our canonical unit.

    Two rules are enforced here, and both exist because this is a health product.

    **1. Decimal, never float.**

        >>> 0.1 + 0.2
        0.30000000000000004

    An HbA1c of exactly 5.7 is the prediabetes threshold. A float that lands on
    5.699999999 classifies a person as normal when they are not. Floats are rejected
    at construction rather than quietly converted, because a silent conversion would
    reintroduce the very imprecision we are avoiding.

    **2. Comparisons require matching units.**

    ``value < range.low`` where one is mg/dL and the other mmol/L raises immediately
    instead of returning a confident wrong answer.
    """

    amount: Decimal
    unit: Unit

    def __post_init__(self) -> None:
        if isinstance(self.amount, float):
            raise InvalidInputError(
                field="amount",
                reason="float loses precision; construct from Decimal, str or int",
            )
        if not isinstance(self.amount, Decimal):
            raise InvalidInputError(field="amount", reason="must be a Decimal")
        if not self.amount.is_finite():
            raise InvalidInputError(field="amount", reason="must be finite")

    @classmethod
    def of(cls, amount: str | int | Decimal, unit: Unit | str) -> Self:
        """Build one from whatever the caller has, without ever touching a float.

        ``CanonicalValue.of("6.1", "%")`` reads better at call sites than the
        constructor, and passing a string keeps the decimal exact.
        """
        try:
            value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
        except (InvalidOperation, ValueError) as exc:
            raise InvalidInputError(field="amount", reason="not a number") from exc
        return cls(amount=value, unit=unit if isinstance(unit, Unit) else Unit(unit))

    def _require_same_unit(self, other: "CanonicalValue") -> None:
        if self.unit != other.unit:
            # The bug this exists to prevent: comparing 6.1 % against 39 mmol/mol
            # and reporting a band with total confidence.
            raise InvalidInputError(
                field="unit",
                reason=f"cannot compare {self.unit} with {other.unit}",
            )

    def __lt__(self, other: "CanonicalValue") -> bool:
        self._require_same_unit(other)
        return self.amount < other.amount

    # __eq__ comes from the dataclass and compares (amount, unit), so values in
    # different units are simply unequal rather than raising. That matches Python's
    # contract: __eq__ must never blow up. Ordering is where a mismatch is a bug.

    def __str__(self) -> str:
        return f"{self.amount} {self.unit}".strip()


@dataclass(frozen=True, slots=True)
class ReferenceRange:
    """The interval a value is judged against.

    At least one bound must be present. Some markers are genuinely one-sided - LDL
    and triglycerides have an upper limit and no meaningful lower one - so a range
    with only a ``high`` is valid and must never have "low" logic applied to it.

    ``source`` travels with the range everywhere because a range printed by the lab
    that ran the sample is stronger evidence than a general population table, and
    the user is entitled to know which they are looking at.
    """

    low: CanonicalValue | None
    high: CanonicalValue | None
    source: RangeSource

    def __post_init__(self) -> None:
        if self.low is None and self.high is None:
            raise InvalidInputError(field="range", reason="must bound at least one side")
        if self.low is not None and self.high is not None:
            if self.low.unit != self.high.unit:
                raise InvalidInputError(field="range", reason="bounds have different units")
            if self.low.amount >= self.high.amount:
                raise InvalidInputError(field="range", reason="low must be below high")

    @property
    def unit(self) -> Unit:
        # No assert: asserts vanish under `python -O`. __post_init__ guarantees at
        # least one bound exists, so this is exhaustive, and mypy can see it.
        if self.low is not None:
            return self.low.unit
        if self.high is not None:
            return self.high.unit
        raise InvalidInputError(field="range", reason="no bounds")  # unreachable

    @property
    def is_two_sided(self) -> bool:
        return self.low is not None and self.high is not None

    def direction_of(self, value: CanonicalValue) -> Direction:
        """Which side of the interval a value falls on.

        Note there is no ``band_of`` here. Direction is arithmetic - a fact about two
        numbers. Banding involves borderline width, guideline overrides and critical
        thresholds, which is policy, and policy lives in its own service (CP15).
        """
        if self.low is not None and value < self.low:
            return Direction.LOW
        if self.high is not None and self.high < value:
            return Direction.HIGH
        return Direction.WITHIN

    def contains(self, value: CanonicalValue) -> bool:
        return self.direction_of(value) is Direction.WITHIN

    def position_of(self, value: CanonicalValue) -> Decimal | None:
        """Where in the interval the value sits, 0.0 at ``low`` and 1.0 at ``high``.

        Lets the UI draw a marker on a bar rather than only colouring a row - the
        difference between "normal" and "normal, but sitting at the very top".

        Returns ``None`` for one-sided ranges, where the question is meaningless.
        Returning 0 there would be a lie the UI would happily render.
        """
        low, high = self.low, self.high
        if low is None or high is None:
            return None
        low._require_same_unit(value)
        span = high.amount - low.amount
        return (value.amount - low.amount) / span

    def __str__(self) -> str:
        low = str(self.low.amount) if self.low else ""
        high = str(self.high.amount) if self.high else ""
        return f"{low}-{high} {self.unit}".strip()
