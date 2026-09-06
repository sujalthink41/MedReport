"""Tests for the constraints the product actually depends on.

These are not "does SQLAlchemy work" tests. Each one pins down a guarantee that
something else in the system relies on being true:

* upload idempotency depends on a unique constraint
* pipeline retries depend on a different unique constraint
* the "delete my data" promise depends on cascades
* correct lab values depend on the column being NUMERIC rather than float

Constraints are the last line of defence. Application checks can be raced by two
concurrent requests; the database cannot.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import ObservationRow, ProfileRow, ReportRow, UserRow

pytestmark = pytest.mark.integration


async def make_user(session: AsyncSession) -> UserRow:
    user = UserRow(id=uuid4(), google_sub=f"sub-{uuid4()}", email="a@b.com")
    session.add(user)
    await session.flush()
    return user


async def make_profile(session: AsyncSession) -> ProfileRow:
    user = await make_user(session)
    profile = ProfileRow(
        id=uuid4(),
        owner_id=user.id,
        display_name="Amma",
        date_of_birth=date(1962, 4, 11),
        sex="female",
        relationship="parent",
    )
    session.add(profile)
    await session.flush()
    return profile


def make_report(profile_id: uuid4, sha: str) -> ReportRow:  # type: ignore[valid-type]
    return ReportRow(
        id=uuid4(),
        profile_id=profile_id,
        storage_key=f"reports/{uuid4()}.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        sha256=sha,
        status="queued",
    )


class TestUploadIdempotency:
    async def test_the_same_file_cannot_be_stored_twice_for_one_profile(
        self, session: AsyncSession
    ) -> None:
        profile = await make_profile(session)
        sha = "a" * 64

        session.add(make_report(profile.id, sha))
        await session.flush()

        session.add(make_report(profile.id, sha))
        with pytest.raises(IntegrityError):
            await session.flush()

        # Why this belongs in the database and not only in Python: two concurrent
        # uploads both run "does this hash exist?", both see nothing, and both
        # insert. Only a constraint stops the second one.

    async def test_two_people_may_upload_the_same_file(self, session: AsyncSession) -> None:
        first = await make_profile(session)
        second = await make_profile(session)
        sha = "b" * 64

        session.add(make_report(first.id, sha))
        session.add(make_report(second.id, sha))
        await session.flush()

        # Twins get identical-looking reports; a shared household scans one PDF
        # for two people. Uniqueness is per profile, never global.


class TestObservationConstraints:
    async def _report(self, session: AsyncSession) -> ReportRow:
        profile = await make_profile(session)
        report = make_report(profile.id, "c" * 64)
        session.add(report)
        await session.flush()
        return report

    def _observation(self, report: ReportRow, test_id: str | None, page: int = 1) -> ObservationRow:
        return ObservationRow(
            id=uuid4(),
            report_id=report.id,
            profile_id=report.profile_id,
            raw_test_name="HAEMOGLOBIN",
            page=page,
            canonical_test_id=test_id,
            band="normal",
            direction="within",
        )

    async def test_one_test_appears_once_per_page(self, session: AsyncSession) -> None:
        report = await self._report(session)

        session.add(self._observation(report, "hb"))
        await session.flush()

        session.add(self._observation(report, "hb"))
        with pytest.raises(IntegrityError):
            await session.flush()

        # This is what makes pipeline retries safe. Re-running the extract node
        # after a timeout must not double every row on the page.

    async def test_many_unmapped_rows_may_share_a_page(self, session: AsyncSession) -> None:
        report = await self._report(session)

        session.add(self._observation(report, None))
        session.add(self._observation(report, None))
        await session.flush()

        # In Postgres, NULLs are distinct in a unique constraint, which is exactly
        # what we want: one page can contain several tests we could not map, and
        # none of them should collide with each other.


class TestPrecision:
    async def test_values_survive_a_round_trip_exactly(self, session: AsyncSession) -> None:
        profile = await make_profile(session)
        report = make_report(profile.id, "d" * 64)
        session.add(report)
        await session.flush()

        session.add(
            ObservationRow(
                id=uuid4(),
                report_id=report.id,
                profile_id=profile.id,
                raw_test_name="HbA1c",
                page=1,
                canonical_test_id="hba1c",
                value_amount=Decimal("5.7"),
                value_unit="%",
                band="borderline",
                direction="within",
            )
        )
        await session.flush()
        session.expunge_all()

        stored = (await session.execute(select(ObservationRow))).scalars().first()

        assert stored is not None
        # 5.7 is the prediabetes threshold. A float column could return
        # 5.699999999999999, and the classifier would call this person normal.
        assert stored.value_amount == Decimal("5.7")
        assert isinstance(stored.value_amount, Decimal)


class TestDeletion:
    async def test_deleting_a_profile_removes_its_reports_and_observations(
        self, session: AsyncSession
    ) -> None:
        profile = await make_profile(session)
        report = make_report(profile.id, "e" * 64)
        session.add(report)
        await session.flush()
        session.add(
            ObservationRow(
                id=uuid4(),
                report_id=report.id,
                profile_id=profile.id,
                raw_test_name="X",
                page=1,
                band="unknown",
                direction="undetermined",
            )
        )
        await session.flush()

        await session.execute(text("DELETE FROM profiles WHERE id = :i"), {"i": profile.id})
        await session.flush()

        # "Delete my data" has to mean it. Orphaned observations would be retained
        # health data for a person who asked to be forgotten.
        assert (await session.execute(select(ReportRow))).scalars().all() == []
        assert (await session.execute(select(ObservationRow))).scalars().all() == []


class TestTimestamps:
    async def test_created_at_is_stamped_and_timezone_aware(self, session: AsyncSession) -> None:
        user = await make_user(session)
        await session.refresh(user)

        assert user.created_at is not None
        # Timezone-aware, always. A naive timestamp is a bug waiting for the first
        # deployment in another region.
        assert user.created_at.tzinfo is not None
        # Sanity only, with a wide tolerance - see the next test for why this is
        # NOT asserted tightly against the host clock.
        assert abs((datetime.now(UTC) - user.created_at).total_seconds()) < 300

    async def test_rows_are_ordered_by_one_clock(self, session: AsyncSession) -> None:
        """The reason ``server_default=now()`` exists.

        The first version of this test compared a Postgres timestamp against
        ``datetime.now()`` in Python - and failed, because the database container's
        clock was 1.5 seconds behind the host's.

        That failure *is* the lesson. Two clocks disagree, always, by some amount.
        If application servers stamped rows, two containers writing milliseconds
        apart could produce rows that sort in the wrong order. Letting the database
        stamp them means one clock decides, so comparisons between rows are
        meaningful even when no two machines agree on the time.

        So the correct assertion compares two database-stamped rows to each other,
        never a database row to the host clock.
        """
        first = await make_user(session)
        second = await make_user(session)
        await session.refresh(first)
        await session.refresh(second)

        assert first.created_at <= second.created_at
