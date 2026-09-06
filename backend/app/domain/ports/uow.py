"""The transaction boundary, as a port.

A use case does several things — read a profile, insert a report, write observations.
Either all of them happen or none of them do. That guarantee is what a unit of work
provides, and this port lets the domain ask for it without knowing that a database,
let alone SQLAlchemy, exists.

Two reasons this is worth having on top of the per-request session in ``deps.py``:

1. **The worker and the CLI need it too.** ``get_session`` is a FastAPI dependency;
   the Celery task in CP12 has no request to hang a transaction off. A port works
   everywhere.
2. **One dependency instead of four.** A use case takes ``uow`` and reaches
   ``uow.reports``, ``uow.observations``. Passing four repositories separately would
   also make it possible to hand in repositories bound to *different* sessions, which
   silently breaks atomicity.
"""

from types import TracebackType
from typing import Protocol, Self

from app.domain.ports.repositories import (
    ObservationRepository,
    ProfileRepository,
    ReportRepository,
)


class UnitOfWork(Protocol):
    """One transaction, and the repositories that share it.

    Used as an async context manager::

        async with uow:
            report = await uow.reports.find_by_hash(profile_id, digest)
            await uow.reports.add(report)
            await uow.commit()

    **Exiting without committing rolls back.** That default is deliberate: forgetting
    to commit loses work, which is annoying and obvious. Forgetting to roll back
    persists half an operation, which is silent and corrupting. Make the safe thing
    the automatic one.
    """

    profiles: ProfileRepository
    reports: ReportRepository
    observations: ObservationRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
