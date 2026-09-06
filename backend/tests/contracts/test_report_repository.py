"""One test suite. Two implementations. This file is the point of CP6.

Every test below is written against the *port* — ``ReportRepository`` — and never
against a particular implementation. Two subclasses then run the identical suite:
once against the in-memory fake, once against real Postgres.

Why this matters more than it looks:

**It is the only real proof that swapping an adapter is safe.** Anyone can declare
"our storage is pluggable". This is what makes it true. If the fake sorts NULLs
differently from Postgres, or returns a list where the real one returns None, a test
here goes red — instead of the difference being discovered in production, months
later, by a user.

It is also Liskov Substitution as an executable check rather than a principle you
promise to remember. And it means the fast tests you rely on (CP9 onward, all using
the fake) are trustworthy: the fake has been proven to behave like the real thing.

The pattern generalises. Any time you have a port with more than one implementation,
write the suite against the port and parametrise the implementation.
"""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.domain.models import ProfileId, Report, ReportId, ReportStatus
from tests.fakes import InMemoryReportRepository

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def build_report(profile_id: ProfileId, sha: str, collected: date | None = None) -> Report:
    return Report(
        id=ReportId(uuid4()),
        profile_id=profile_id,
        storage_key=f"reports/{uuid4()}.pdf",
        content_type="application/pdf",
        size_bytes=2048,
        sha256=sha,
        status=ReportStatus.QUEUED,
        created_at=NOW,
        collected_at=collected,
    )


class ReportRepositoryContract:
    """Behaviour every ReportRepository must exhibit.

    Not named ``Test...`` so pytest does not collect it directly. Subclasses supply
    ``repo`` and ``profile_id`` and inherit every test below.
    """

    async def test_a_stored_report_can_be_read_back(self, repo, profile_id) -> None:  # type: ignore[no-untyped-def]
        report = build_report(profile_id, "a" * 64)

        await repo.add(report)

        found = await repo.get(report.id)
        assert found is not None
        assert found.id == report.id
        assert found.sha256 == report.sha256
        assert found.status is ReportStatus.QUEUED

    async def test_a_missing_report_is_none_not_an_error(self, repo) -> None:  # type: ignore[no-untyped-def]
        # Both implementations must agree on this. A repository that raises in one
        # and returns None in the other would make every caller implementation-aware.
        assert await repo.get(ReportId(uuid4())) is None

    async def test_lookup_by_hash_finds_the_report(self, repo, profile_id) -> None:  # type: ignore[no-untyped-def]
        report = build_report(profile_id, "b" * 64)
        await repo.add(report)

        found = await repo.find_by_hash(profile_id, "b" * 64)

        # This is the idempotency lookup: it is why tapping upload twice on a bad
        # connection gives one report rather than two.
        assert found is not None
        assert found.id == report.id

    async def test_lookup_by_hash_is_scoped_to_the_profile(self, repo, profile_id) -> None:  # type: ignore[no-untyped-def]
        await repo.add(build_report(profile_id, "c" * 64))

        # A different person uploading the identical file must not match. Twins get
        # near-identical reports; a household scans one PDF for two people.
        assert await repo.find_by_hash(ProfileId(uuid4()), "c" * 64) is None

    async def test_unknown_hash_is_none(self, repo, profile_id) -> None:  # type: ignore[no-untyped-def]
        assert await repo.find_by_hash(profile_id, "f" * 64) is None

    async def test_status_changes_are_persisted(self, repo, profile_id) -> None:  # type: ignore[no-untyped-def]
        report = build_report(profile_id, "d" * 64)
        await repo.add(report)

        await repo.update(report.with_status(ReportStatus.PROCESSING))

        reloaded = await repo.get(report.id)
        assert reloaded is not None
        assert reloaded.status is ReportStatus.PROCESSING

    async def test_history_is_newest_collection_date_first(self, repo, profile_id) -> None:  # type: ignore[no-untyped-def]
        old = build_report(profile_id, "1" * 64, collected=date(2024, 1, 10))
        recent = build_report(profile_id, "2" * 64, collected=date(2026, 6, 1))
        middle = build_report(profile_id, "3" * 64, collected=date(2025, 3, 5))
        for report in (old, recent, middle):
            await repo.add(report)

        listed = await repo.list_for_profile(profile_id)

        # Ordered by when the sample was TAKEN, not when it was uploaded. All three
        # were uploaded in this one instant, as people really do.
        assert [r.collected_at for r in listed] == [
            date(2026, 6, 1),
            date(2025, 3, 5),
            date(2024, 1, 10),
        ]

    async def test_reports_without_a_collection_date_sort_last(self, repo, profile_id) -> None:  # type: ignore[no-untyped-def]
        undated = build_report(profile_id, "4" * 64, collected=None)
        dated = build_report(profile_id, "5" * 64, collected=date(2024, 1, 1))
        await repo.add(undated)
        await repo.add(dated)

        listed = await repo.list_for_profile(profile_id)

        # Exactly the kind of detail a fake gets wrong: SQL puts NULLs first by
        # default when sorting descending. A report whose date we could not read
        # still belongs in the list — at the bottom, not silently at the top.
        assert listed[-1].id == undated.id

    async def test_listing_is_scoped_to_one_profile(self, repo, profile_id) -> None:  # type: ignore[no-untyped-def]
        await repo.add(build_report(profile_id, "6" * 64))

        assert await repo.list_for_profile(ProfileId(uuid4())) == []


class TestInMemoryReportRepository(ReportRepositoryContract):
    """The fake. Runs in microseconds, with Docker stopped."""

    @pytest.fixture
    def repo(self) -> InMemoryReportRepository:
        return InMemoryReportRepository()

    @pytest.fixture
    def profile_id(self) -> ProfileId:
        return ProfileId(uuid4())


@pytest.mark.integration
class TestSqlReportRepository(ReportRepositoryContract):
    """The real thing. Same tests, real Postgres, real constraints, real SQL."""

    @pytest.fixture
    def repo(self, session):  # type: ignore[no-untyped-def]
        from app.adapters.db.repositories import SqlReportRepository

        return SqlReportRepository(session)

    @pytest.fixture
    async def profile_id(self, session) -> ProfileId:  # type: ignore[no-untyped-def]
        # The fake needs no setup; Postgres has a foreign key, so a real profile and
        # its owner must exist first. That asymmetry is normal — the *contract* is
        # what has to match, not the scaffolding around it.
        from app.adapters.db.models import ProfileRow, UserRow

        user = UserRow(id=uuid4(), google_sub=f"sub-{uuid4()}", email="a@b.com")
        session.add(user)
        await session.flush()

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
        return ProfileId(profile.id)
