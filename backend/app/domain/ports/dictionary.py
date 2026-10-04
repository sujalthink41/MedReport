"""Port for the clinical dictionary."""

from datetime import datetime
from typing import Protocol

from app.domain.models.clinical import (
    CanonicalTest,
    CriticalValue,
    ProposedTest,
    UnmappedName,
)
from app.domain.models.identifiers import CanonicalTestId


class DictionaryRepository(Protocol):
    async def resolve(self, keys: list[str]) -> tuple[CanonicalTestId, str] | None:
        """First key that matches an alias, with the id it maps to.

        Takes the whole candidate list rather than one key at a time so the lookup
        is a single query. Resolution happens for every row of every report - at a
        few hundred rows per report, one query per candidate would dominate the
        pipeline's database time.
        """
        ...

    async def get(self, canonical_test_id: CanonicalTestId) -> CanonicalTest | None: ...

    async def record_proposal(
        self, proposal: ProposedTest, *, raw_name: str, keys: list[str], at: datetime
    ) -> CanonicalTestId:
        """Store what the model suggested, and return the id to use.

        Idempotent and race-safe: two reports processed in parallel that mention
        the same new marker must end up with ONE canonical id, not two. Whichever
        writes first wins and the other reads back the winner - otherwise the very
        first thing the dictionary does is split a trend.
        """
        ...

    async def record_unmapped(self, *, key: str, raw_name: str, at: datetime) -> None:
        """Note a name we could not resolve, or bump its count.

        Never silently dropped. The queue is how the dictionary's blind spots stay
        visible and measurable instead of being discovered by a confused user.
        """
        ...

    async def list_unmapped(self, *, limit: int = 100) -> list[UnmappedName]:
        """Most frequent first - the highest-value gaps to close."""
        ...

    async def critical_values_for(self, canonical_test_id: CanonicalTestId) -> list[CriticalValue]:
        """Approved critical thresholds only.

        Proposed entries are deliberately invisible here. A threshold nobody has
        checked must not decide whether someone is told to seek care today.
        """
        ...
