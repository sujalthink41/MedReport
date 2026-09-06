"""In-memory repositories for fast tests.

**Fakes, not mocks.** The difference matters:

    mock.reports.add.assert_called_once_with(report)   # asserts a call happened
    assert await fake.reports.get(report.id) == report # asserts a fact is true

A mock tests that your code called a method in a particular way, so it breaks every
time you refactor — even when behaviour is unchanged. A fake has real behaviour, so
tests assert outcomes and survive refactoring.

These are held to the same contract as the real repositories: the suite in
``tests/contracts/`` runs against both. If a fake behaves differently from Postgres,
that is a bug in the fake, and it is caught rather than discovered in production.
"""

from datetime import date
from types import TracebackType
from typing import Self

from app.domain.models import (
    Observation,
    ObservationId,
    Profile,
    ProfileId,
    Report,
    ReportId,
    UserId,
)


class InMemoryProfileRepository:
    def __init__(self) -> None:
        self.items: dict[ProfileId, Profile] = {}

    async def get(self, profile_id: ProfileId) -> Profile | None:
        return self.items.get(profile_id)

    async def list_for_owner(self, owner_id: UserId) -> list[Profile]:
        return sorted(
            (p for p in self.items.values() if p.owner_id == owner_id),
            key=lambda p: p.created_at,
        )

    async def add(self, profile: Profile) -> None:
        self.items[profile.id] = profile

    async def update(self, profile: Profile) -> None:
        if profile.id in self.items:
            self.items[profile.id] = profile

    async def delete(self, profile_id: ProfileId) -> None:
        self.items.pop(profile_id, None)


class InMemoryReportRepository:
    def __init__(self) -> None:
        self.items: dict[ReportId, Report] = {}

    async def get(self, report_id: ReportId) -> Report | None:
        return self.items.get(report_id)

    async def find_by_hash(self, profile_id: ProfileId, sha256: str) -> Report | None:
        for report in self.items.values():
            if report.profile_id == profile_id and report.sha256 == sha256:
                return report
        return None

    async def list_for_profile(self, profile_id: ProfileId) -> list[Report]:
        # Must match the real repository's ordering, including where NULLs go.
        #
        # The first version of this used `r.collected_at is None` with reverse=True,
        # which sorts undated reports FIRST. Postgres, with .nulls_last(), puts them
        # last. The contract suite caught the divergence immediately — which is the
        # entire reason that suite exists. Without it, every fast test written from
        # CP9 onward would have been trusting a fake that lies about ordering.
        #
        # `date.min` as a sentinel keeps None out of the comparison entirely.
        reports = [r for r in self.items.values() if r.profile_id == profile_id]
        return sorted(
            reports,
            key=lambda r: (
                r.collected_at is not None,
                r.collected_at or date.min,
                r.created_at,
            ),
            reverse=True,
        )

    async def add(self, report: Report) -> None:
        self.items[report.id] = report

    async def update(self, report: Report) -> None:
        if report.id in self.items:
            self.items[report.id] = report


class InMemoryObservationRepository:
    def __init__(self) -> None:
        self.items: dict[ObservationId, Observation] = {}
        self._report_dates: dict[ReportId, object] = {}

    async def get(self, observation_id: ObservationId) -> Observation | None:
        return self.items.get(observation_id)

    async def list_for_report(self, report_id: ReportId) -> list[Observation]:
        return sorted(
            (o for o in self.items.values() if o.report_id == report_id),
            key=lambda o: (o.page, o.raw_test_name),
        )

    async def history(self, profile_id: ProfileId, canonical_test_id: str) -> list[Observation]:
        return [
            o
            for o in self.items.values()
            if o.profile_id == profile_id and o.canonical_test_id == canonical_test_id
        ]

    async def replace_for_page(
        self, report_id: ReportId, page: int, observations: list[Observation]
    ) -> None:
        for key in [
            k for k, o in self.items.items() if o.report_id == report_id and o.page == page
        ]:
            del self.items[key]
        for observation in observations:
            self.items[observation.id] = observation

    async def delete_for_report(self, report_id: ReportId) -> None:
        for key in [k for k, o in self.items.items() if o.report_id == report_id]:
            del self.items[key]


class InMemoryUnitOfWork:
    """A unit of work that records whether it was committed.

    Deliberately does NOT undo writes on rollback. Real atomicity needs a database,
    and pretending otherwise in a fake would let a test prove something the fake
    cannot actually guarantee. Instead it exposes ``committed`` so a test can assert
    the use case reached its commit, and transactional behaviour is verified against
    real Postgres in the integration suite.

    Knowing the limits of your own test double is part of using one honestly.
    """

    def __init__(self) -> None:
        self.profiles = InMemoryProfileRepository()
        self.reports = InMemoryReportRepository()
        self.observations = InMemoryObservationRepository()
        self.committed = False
        self.rolled_back = False

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
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
