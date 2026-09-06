"""Contract suite for ObservationRepository — fake and Postgres, same tests.

The important tests here are the idempotency ones. They pin down the guarantee that
makes every pipeline retry in CP19-CP21 safe.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.models import (
    Band,
    CanonicalValue,
    Direction,
    Observation,
    ObservationId,
    ProfileId,
    RangeSource,
    ReferenceRange,
    ReportId,
    Unit,
)
from app.domain.models.identifiers import CanonicalTestId
from tests.fakes import InMemoryObservationRepository

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
G_DL = Unit("g/dL")


def build_observation(
    report_id: ReportId,
    profile_id: ProfileId,
    *,
    page: int = 1,
    test_id: str | None = "hb",
    amount: str | None = "13.5",
) -> Observation:
    value = CanonicalValue.of(amount, G_DL) if amount else None
    return Observation(
        id=ObservationId(uuid4()),
        report_id=report_id,
        profile_id=profile_id,
        raw_test_name="HAEMOGLOBIN",
        page=page,
        canonical_test_id=CanonicalTestId(test_id) if test_id else None,
        value=value,
        reference_range=ReferenceRange(
            low=CanonicalValue.of("12.0", G_DL),
            high=CanonicalValue.of("15.5", G_DL),
            source=RangeSource.LAB,
        ),
        band=Band.NORMAL,
        direction=Direction.WITHIN,
    )


class ObservationRepositoryContract:
    async def test_written_rows_come_back(self, repo, report_id, profile_id) -> None:  # type: ignore[no-untyped-def]
        rows = [build_observation(report_id, profile_id)]

        await repo.replace_for_page(report_id, 1, rows)

        stored = await repo.list_for_report(report_id)
        assert len(stored) == 1
        assert stored[0].raw_test_name == "HAEMOGLOBIN"

    async def test_value_objects_survive_the_round_trip(self, repo, report_id, profile_id) -> None:  # type: ignore[no-untyped-def]
        await repo.replace_for_page(
            report_id, 1, [build_observation(report_id, profile_id, amount="5.7")]
        )

        stored = (await repo.list_for_report(report_id))[0]

        # 5.7 is the prediabetes threshold. The value, its unit and its range must
        # all reassemble exactly - a Decimal that drifts here is a wrong band later.
        assert stored.value is not None
        assert stored.value.amount == Decimal("5.7")
        assert stored.value.unit == G_DL
        assert stored.reference_range is not None
        assert stored.reference_range.source is RangeSource.LAB

    async def test_rerunning_a_page_does_not_duplicate_mapped_rows(
        self,
        repo,  # type: ignore[no-untyped-def]
        report_id,
        profile_id,
    ) -> None:
        rows = [build_observation(report_id, profile_id, test_id="hb")]

        await repo.replace_for_page(report_id, 1, rows)
        await repo.replace_for_page(report_id, 1, rows)  # worker retried

        assert len(await repo.list_for_report(report_id)) == 1

    async def test_rerunning_a_page_does_not_duplicate_unmapped_rows(
        self,
        repo,  # type: ignore[no-untyped-def]
        report_id,
        profile_id,
    ) -> None:
        # The test that forced the interface change.
        #
        # The unique constraint is (report_id, page, canonical_test_id), and in
        # Postgres NULLs are DISTINCT in a unique constraint. We depend on that so a
        # page can hold several tests we could not map. But it means an upsert never
        # matches an unmapped row - so the original `add_many` would have silently
        # doubled every unmapped row on every retry.
        rows = [
            build_observation(report_id, profile_id, test_id=None),
            build_observation(report_id, profile_id, test_id=None),
        ]

        await repo.replace_for_page(report_id, 1, rows)
        await repo.replace_for_page(report_id, 1, rows)

        assert len(await repo.list_for_report(report_id)) == 2

    async def test_replacing_one_page_leaves_other_pages_alone(
        self,
        repo,  # type: ignore[no-untyped-def]
        report_id,
        profile_id,
    ) -> None:
        await repo.replace_for_page(
            report_id, 1, [build_observation(report_id, profile_id, page=1, test_id="hb")]
        )
        await repo.replace_for_page(
            report_id, 2, [build_observation(report_id, profile_id, page=2, test_id="alt")]
        )

        # Re-run page 2 only, as the parallel per-page extraction does.
        await repo.replace_for_page(
            report_id, 2, [build_observation(report_id, profile_id, page=2, test_id="alt")]
        )

        stored = await repo.list_for_report(report_id)
        assert sorted(o.page for o in stored) == [1, 2]

    async def test_a_page_can_be_emptied(self, repo, report_id, profile_id) -> None:  # type: ignore[no-untyped-def]
        await repo.replace_for_page(report_id, 1, [build_observation(report_id, profile_id)])

        await repo.replace_for_page(report_id, 1, [])

        assert await repo.list_for_report(report_id) == []

    async def test_history_returns_one_marker_for_one_person(
        self,
        repo,  # type: ignore[no-untyped-def]
        report_id,
        profile_id,
    ) -> None:
        await repo.replace_for_page(
            report_id,
            1,
            [
                build_observation(report_id, profile_id, test_id="hb"),
                build_observation(report_id, profile_id, test_id="alt"),
            ],
        )

        history = await repo.history(profile_id, "hb")

        # The trend query. Mixing two markers here would produce a chart that is
        # confidently, invisibly wrong.
        assert len(history) == 1
        assert history[0].canonical_test_id == "hb"


class TestInMemoryObservationRepository(ObservationRepositoryContract):
    @pytest.fixture
    def repo(self) -> InMemoryObservationRepository:
        return InMemoryObservationRepository()

    @pytest.fixture
    def profile_id(self) -> ProfileId:
        return ProfileId(uuid4())

    @pytest.fixture
    def report_id(self) -> ReportId:
        return ReportId(uuid4())


@pytest.mark.integration
class TestSqlObservationRepository(ObservationRepositoryContract):
    @pytest.fixture
    def repo(self, session):  # type: ignore[no-untyped-def]
        from app.adapters.db.repositories import SqlObservationRepository

        return SqlObservationRepository(session)

    @pytest.fixture
    async def profile_id(self, session) -> ProfileId:  # type: ignore[no-untyped-def]
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

    @pytest.fixture
    async def report_id(self, session, profile_id) -> ReportId:  # type: ignore[no-untyped-def]
        from app.adapters.db.models import ReportRow

        report = ReportRow(
            id=uuid4(),
            profile_id=profile_id,
            storage_key=f"reports/{uuid4()}.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            sha256=uuid4().hex + uuid4().hex,
            status="queued",
        )
        session.add(report)
        await session.flush()
        return ReportId(report.id)
