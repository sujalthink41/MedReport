"""The clinical dictionary, in Postgres."""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    CanonicalTestRow,
    CriticalValueRow,
    TestAliasRow,
    UnmappedNameRow,
)
from app.core.logging import get_logger
from app.domain.models.clinical import (
    CanonicalTest,
    CriticalValue,
    EntryStatus,
    ProposedTest,
    Sidedness,
    UnmappedName,
)
from app.domain.models.identifiers import CanonicalTestId

log = get_logger(__name__)


class SqlDictionaryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, keys: list[str]) -> tuple[CanonicalTestId, str] | None:
        if not keys:
            return None

        result = await self._session.execute(
            select(TestAliasRow.key, TestAliasRow.canonical_test_id).where(
                TestAliasRow.key.in_(keys),
                # A rejected alias stays in the table rather than being deleted, so
                # the same wrong mapping is not re-proposed next week - but it must
                # never resolve.
                TestAliasRow.status != EntryStatus.REJECTED.value,
            )
        )
        found: dict[str, str] = {row[0]: row[1] for row in result.all()}
        if not found:
            return None

        # Caller order is priority order: most specific candidate first. Scanning
        # the caller's list rather than the query result keeps that intact - SQL
        # returns rows in whatever order it likes.
        for key in keys:
            if key in found:
                return CanonicalTestId(found[key]), key
        return None

    async def get(self, canonical_test_id: CanonicalTestId) -> CanonicalTest | None:
        row = await self._session.get(CanonicalTestRow, canonical_test_id)
        return _to_test(row) if row else None

    async def record_proposal(
        self, proposal: ProposedTest, *, raw_name: str, keys: list[str], at: datetime
    ) -> CanonicalTestId:
        """Store a model's suggestion, and return the id that actually won.

        Two reports processed in parallel can mention the same new marker. Both
        would propose, and naive inserts would create two canonical ids - splitting
        a trend the very first time the dictionary is used.

        ``ON CONFLICT DO NOTHING`` plus a read-back makes the first writer win and
        the second adopt it, without either needing a lock.
        """
        canonical_id = proposal.canonical_id.strip().lower()

        await self._session.execute(
            pg_insert(CanonicalTestRow)
            .values(
                id=canonical_id,
                display_name=proposal.display_name,
                panel=proposal.panel,
                sidedness=proposal.sidedness.value,
                status=EntryStatus.PROPOSED.value,
                proposed_by="model",
                created_at=at,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )

        # Every candidate key points at the same marker, so a later report that
        # prints the name differently still resolves. Conflicts are ignored: an
        # existing alias - possibly reviewed and corrected - outranks a fresh guess.
        for key in keys:
            if not key:
                continue
            await self._session.execute(
                pg_insert(TestAliasRow)
                .values(
                    key=key,
                    canonical_test_id=canonical_id,
                    raw_example=raw_name,
                    status=EntryStatus.PROPOSED.value,
                    created_at=at,
                )
                .on_conflict_do_nothing(index_elements=["key"])
            )

        await self._session.flush()

        winner = await self._session.execute(
            select(TestAliasRow.canonical_test_id).where(TestAliasRow.key == keys[0])
        )
        actual = winner.scalars().first() or canonical_id
        if actual != canonical_id:
            log.info("proposal_lost_race", proposed=canonical_id, existing=actual)
        return CanonicalTestId(actual)

    async def record_unmapped(self, *, key: str, raw_name: str, at: datetime) -> None:
        await self._session.execute(
            pg_insert(UnmappedNameRow)
            .values(
                key=key,
                raw_example=raw_name,
                occurrences=1,
                first_seen_at=at,
                last_seen_at=at,
            )
            .on_conflict_do_update(
                index_elements=["key"],
                set_={
                    # Counted in SQL, not read-modify-write in Python: concurrent
                    # reports mentioning the same unknown marker would otherwise
                    # lose increments to a lost update.
                    "occurrences": UnmappedNameRow.__table__.c.occurrences + 1,
                    "last_seen_at": at,
                },
            )
        )
        await self._session.flush()

    async def list_unmapped(self, *, limit: int = 100) -> list[UnmappedName]:
        result = await self._session.execute(
            select(UnmappedNameRow)
            .where(UnmappedNameRow.resolved_to.is_(None))
            .order_by(UnmappedNameRow.occurrences.desc())
            .limit(limit)
        )
        return [
            UnmappedName(
                key=row.key,
                raw_example=row.raw_example,
                occurrences=row.occurrences,
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
                resolved_to=CanonicalTestId(row.resolved_to) if row.resolved_to else None,
            )
            for row in result.scalars()
        ]

    async def critical_values_for(self, canonical_test_id: CanonicalTestId) -> list[CriticalValue]:
        result = await self._session.execute(
            select(CriticalValueRow).where(
                CriticalValueRow.canonical_test_id == canonical_test_id,
                # Approved only. A threshold nobody has checked must not decide
                # whether a person is told to seek care today.
                CriticalValueRow.status == EntryStatus.APPROVED.value,
            )
        )
        return [
            CriticalValue(
                canonical_test_id=CanonicalTestId(row.canonical_test_id),
                comparator=row.comparator,
                threshold=str(row.threshold),
                unit=row.unit,
                message_template=row.message_template,
                source=row.source,
                status=EntryStatus(row.status),
                reviewed_by=row.reviewed_by,
                reviewed_at=row.reviewed_at,
            )
            for row in result.scalars()
        ]

    async def add_critical_value(self, value: CriticalValue) -> None:
        """Used by a reviewer tool, never by the pipeline."""
        self._session.add(
            CriticalValueRow(
                id=uuid4(),
                canonical_test_id=value.canonical_test_id,
                comparator=value.comparator,
                threshold=Decimal(value.threshold),
                unit=value.unit,
                message_template=value.message_template,
                source=value.source,
                status=value.status.value,
                reviewed_by=value.reviewed_by,
                reviewed_at=value.reviewed_at,
            )
        )
        await self._session.flush()


def _to_test(row: CanonicalTestRow) -> CanonicalTest:
    return CanonicalTest(
        id=CanonicalTestId(row.id),
        display_name=row.display_name,
        panel=row.panel,
        canonical_unit=row.canonical_unit,
        sidedness=Sidedness(row.sidedness),
        status=EntryStatus(row.status),
        created_at=row.created_at,
        proposed_by=row.proposed_by,
    )
