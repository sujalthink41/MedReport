"""Atomicity, tested against a real database.

The fake unit of work cannot prove this — it has no transaction to roll back — so
this is one of the guarantees that must be verified against Postgres or not at all.

Knowing which properties your fakes *cannot* prove is as important as having them.
"""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.adapters.db.models import ProfileRow, ReportRow, UserRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.models import ProfileId, Report, ReportId, ReportStatus, Sex, UserId
from app.domain.models.enums import Relationship
from app.domain.models.profile import Profile

pytestmark = pytest.mark.integration


@pytest.fixture
def uow_factory(engine: AsyncEngine) -> async_sessionmaker:  # type: ignore[type-arg]

    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def _seed_user(factory) -> UserId:  # type: ignore[no-untyped-def]
    async with factory() as session:
        user = UserRow(id=uuid4(), google_sub=f"sub-{uuid4()}", email="a@b.com")
        session.add(user)
        await session.commit()
        return UserId(user.id)


def build_profile(owner_id: UserId, clock) -> Profile:  # type: ignore[no-untyped-def]
    return Profile(
        id=ProfileId(uuid4()),
        owner_id=owner_id,
        display_name="Amma",
        date_of_birth=date(1962, 4, 11),
        sex=Sex.FEMALE,
        relationship=Relationship.PARENT,
        created_at=clock,
    )


class TestAtomicity:
    async def test_committed_work_is_visible_afterwards(self, uow_factory) -> None:  # type: ignore[no-untyped-def]
        from datetime import UTC, datetime

        owner = await _seed_user(uow_factory)

        async with SqlUnitOfWork(uow_factory) as uow:
            await uow.profiles.add(build_profile(owner, datetime.now(UTC)))
            await uow.commit()

        async with uow_factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(ProfileRow).where(ProfileRow.owner_id == owner)
            )
        assert count == 1

    async def test_leaving_without_committing_discards_everything(self, uow_factory) -> None:  # type: ignore[no-untyped-def]
        from datetime import UTC, datetime

        owner = await _seed_user(uow_factory)

        async with SqlUnitOfWork(uow_factory) as uow:
            await uow.profiles.add(build_profile(owner, datetime.now(UTC)))
            # No commit. Rollback on exit is the default deliberately: forgetting to
            # commit loses work, which is obvious. Forgetting to roll back persists
            # half an operation, which is silent and corrupting.

        async with uow_factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(ProfileRow).where(ProfileRow.owner_id == owner)
            )
        assert count == 0

    async def test_a_failure_midway_undoes_earlier_writes(self, uow_factory) -> None:  # type: ignore[no-untyped-def]
        """The reason a unit of work exists at all.

        Two repositories, one transaction. The profile insert succeeds and the report
        insert then fails. Without a shared transaction the profile would survive as
        an orphan, and the user would see a person in their list who has no reports
        and was never really created.
        """
        from datetime import UTC, datetime

        owner = await _seed_user(uow_factory)
        profile = build_profile(owner, datetime.now(UTC))

        with pytest.raises(Exception):  # noqa: B017 - any DB failure proves the point
            async with SqlUnitOfWork(uow_factory) as uow:
                await uow.profiles.add(profile)
                await uow.reports.add(
                    Report(
                        id=ReportId(uuid4()),
                        # A profile id that does not exist: violates the foreign key.
                        profile_id=ProfileId(uuid4()),
                        storage_key="reports/x.pdf",
                        content_type="application/pdf",
                        size_bytes=10,
                        sha256="a" * 64,
                        status=ReportStatus.QUEUED,
                        created_at=datetime.now(UTC),
                    )
                )
                await uow.commit()

        async with uow_factory() as session:
            profiles = await session.scalar(
                select(func.count()).select_from(ProfileRow).where(ProfileRow.owner_id == owner)
            )
            reports = await session.scalar(select(func.count()).select_from(ReportRow))

        assert profiles == 0  # the earlier write was undone
        assert reports == 0
