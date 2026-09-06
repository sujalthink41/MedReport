"""SQLAlchemy implementations of the repository ports.

Three things every method here does, and nothing more:

1. build a query
2. run it
3. map rows to domain objects, or domain objects to rows

No business rules, no ``commit``. A repository that commits turns one logical
operation into several, so a failure halfway through leaves the database in a state
nobody intended. The transaction belongs to the caller — see ``uow.py``.
"""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db import mappers
from app.adapters.db.models import ObservationRow, ProfileRow, ReportRow
from app.domain.models import (
    Observation,
    ObservationId,
    Profile,
    ProfileId,
    Report,
    ReportId,
    UserId,
)


class SqlProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, profile_id: ProfileId) -> Profile | None:
        row = await self._session.get(ProfileRow, profile_id)
        # None, not an exception. "Not found" is a normal answer to a question; the
        # use case decides whether that means 404, "create one", or "skip".
        return mappers.profile_to_domain(row) if row else None

    async def list_for_owner(self, owner_id: UserId) -> list[Profile]:
        result = await self._session.execute(
            select(ProfileRow)
            .where(ProfileRow.owner_id == owner_id)
            .order_by(ProfileRow.created_at)
        )
        return [mappers.profile_to_domain(row) for row in result.scalars()]

    async def add(self, profile: Profile) -> None:
        self._session.add(mappers.profile_to_row(profile))
        # flush, not commit: sends the INSERT so constraint violations surface here
        # rather than at the end of the request, while still leaving the caller in
        # charge of whether any of it is kept.
        await self._session.flush()

    async def update(self, profile: Profile) -> None:
        row = await self._session.get(ProfileRow, profile.id)
        if row is None:
            return
        mappers.apply_profile(row, profile)
        await self._session.flush()

    async def delete(self, profile_id: ProfileId) -> None:
        await self._session.execute(delete(ProfileRow).where(ProfileRow.id == profile_id))
        await self._session.flush()


class SqlReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, report_id: ReportId) -> Report | None:
        row = await self._session.get(ReportRow, report_id)
        return mappers.report_to_domain(row) if row else None

    async def find_by_hash(self, profile_id: ProfileId, sha256: str) -> Report | None:
        result = await self._session.execute(
            select(ReportRow).where(
                ReportRow.profile_id == profile_id,
                ReportRow.sha256 == sha256,
            )
        )
        row = result.scalars().first()
        return mappers.report_to_domain(row) if row else None

    async def list_for_profile(self, profile_id: ProfileId) -> list[Report]:
        result = await self._session.execute(
            select(ReportRow)
            .where(ReportRow.profile_id == profile_id)
            # Newest sample first. collected_at, never created_at - people upload
            # three years of history in one sitting.
            #
            # nulls_last: a report whose collection date we could not read still
            # belongs in the list, just at the bottom rather than silently first.
            .order_by(ReportRow.collected_at.desc().nulls_last(), ReportRow.created_at.desc())
        )
        return [mappers.report_to_domain(row) for row in result.scalars()]

    async def add(self, report: Report) -> None:
        self._session.add(mappers.report_to_row(report))
        await self._session.flush()

    async def update(self, report: Report) -> None:
        row = await self._session.get(ReportRow, report.id)
        if row is None:
            return
        mappers.apply_report(row, report)
        await self._session.flush()


class SqlObservationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, observation_id: ObservationId) -> Observation | None:
        row = await self._session.get(ObservationRow, observation_id)
        return mappers.observation_to_domain(row) if row else None

    async def list_for_report(self, report_id: ReportId) -> list[Observation]:
        result = await self._session.execute(
            select(ObservationRow)
            .where(ObservationRow.report_id == report_id)
            .order_by(ObservationRow.page, ObservationRow.raw_test_name)
        )
        return [mappers.observation_to_domain(row) for row in result.scalars()]

    async def history(self, profile_id: ProfileId, canonical_test_id: str) -> list[Observation]:
        """The trend query — every reading of one marker for one person.

        Ordered by the report's collection date, which needs the join. The
        denormalised ``profile_id`` on observations still earns its place: it filters
        the big table down to one person before the join happens.
        """
        result = await self._session.execute(
            select(ObservationRow)
            .join(ReportRow, ReportRow.id == ObservationRow.report_id)
            .where(
                ObservationRow.profile_id == profile_id,
                ObservationRow.canonical_test_id == canonical_test_id,
            )
            .order_by(ReportRow.collected_at.asc().nulls_last(), ReportRow.created_at.asc())
        )
        return [mappers.observation_to_domain(row) for row in result.scalars()]

    async def replace_for_page(
        self, report_id: ReportId, page: int, observations: list[Observation]
    ) -> None:
        """Delete this page's rows, then insert the new ones. One transaction.

        Idempotent by construction rather than by constraint, which is what makes a
        pipeline retry safe for unmapped rows too — see the port's docstring.

        Delete is scoped to one page, so the parallel per-page extraction can write
        results as they arrive without any page clobbering another.
        """
        await self._session.execute(
            delete(ObservationRow).where(
                ObservationRow.report_id == report_id,
                ObservationRow.page == page,
            )
        )
        if observations:
            # One statement for the whole page rather than N session.add() calls.
            # A 25-page report is hundreds of rows; the difference is real.
            await self._session.execute(
                pg_insert(ObservationRow),
                [mappers.observation_to_values(o) for o in observations],
            )
        await self._session.flush()

    async def delete_for_report(self, report_id: ReportId) -> None:
        await self._session.execute(
            delete(ObservationRow).where(ObservationRow.report_id == report_id)
        )
        await self._session.flush()
