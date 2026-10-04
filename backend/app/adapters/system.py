"""Adapters for the non-deterministic things: time and identifiers.

Tiny classes, and the reason the rest of the codebase is testable. Every
``datetime.now()`` and ``uuid4()`` in business logic is a value a test cannot
control, so they all enter through these.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4


class SystemClock:
    def now(self) -> datetime:
        # Always timezone-aware. A naive datetime compared against an aware one
        # raises at runtime, and trend arithmetic silently shifts by hours.
        return datetime.now(UTC)


class Uuid4Generator:
    def new_id(self) -> UUID:
        return uuid4()


class FrozenClock:
    """A clock that does not move unless you move it. For tests.

    This is what makes "a value rose 25% over six months" testable in milliseconds
    instead of untestable in practice.
    """

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._now = at

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(**delta)


class SequentialIdGenerator:
    """Predictable ids, so a test can assert on them without fishing them back out."""

    def __init__(self) -> None:
        self._counter = 0

    def new_id(self) -> UUID:
        self._counter += 1
        return UUID(int=self._counter)
