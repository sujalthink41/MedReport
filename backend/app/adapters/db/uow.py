"""SQLAlchemy unit of work."""

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.repositories import (
    SqlObservationRepository,
    SqlProfileRepository,
    SqlReportRepository,
)


class SqlUnitOfWork:
    """Owns a session, and the repositories bound to it.

    All three repositories share **one** session, which is the entire point: work
    done through ``uow.reports`` and ``uow.observations`` lands in the same
    transaction and commits or rolls back together.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self.profiles = SqlProfileRepository(self._session)
        self.reports = SqlReportRepository(self._session)
        self.observations = SqlObservationRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is None:
            return
        try:
            # Roll back unconditionally. On the success path commit() has already
            # run, so this is a no-op; on any other path it undoes half-finished
            # work. Making rollback the default means the dangerous mistake -
            # persisting a partial operation - cannot happen by omission.
            await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None

    async def commit(self) -> None:
        if self._session is not None:
            await self._session.commit()

    async def rollback(self) -> None:
        if self._session is not None:
            await self._session.rollback()


class SessionUnitOfWork:
    """A unit of work over a session someone else owns.

    Used from HTTP requests, where ``deps.get_session`` already created the session
    and will commit it at the end of the request. Here ``commit`` is a flush: the
    work is made visible to the rest of the request, but the decision to keep it
    stays with the request boundary.

    Without this, a use case called from a route would open a *second* session and a
    second transaction — so a later failure in the same request could not undo it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.profiles = SqlProfileRepository(session)
        self.reports = SqlReportRepository(session)
        self.observations = SqlObservationRepository(session)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return

    async def commit(self) -> None:
        await self._session.flush()

    async def rollback(self) -> None:
        await self._session.rollback()
