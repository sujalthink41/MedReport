"""Ports for persistence.

Look at what is absent: no ``session``, no ``select()``, no ``commit()``, no
SQLAlchemy anywhere. The domain says *what it needs* - "give me the report with this
hash" - and never *how it is stored*.

That absence is the whole value. It means the use cases in CP9 onward can be tested
against a dictionary in memory, and it means swapping Postgres for anything else
touches only ``adapters/db/``.

**Ports are deliberately small** (Interface Segregation). A ``ReportReader`` with two
methods is easier to fake in a test than an ``EverythingRepository`` with thirty, and
a small port tells you honestly how much power a piece of code actually needs.
"""

from typing import Protocol

from app.domain.models.identifiers import ObservationId, ProfileId, ReportId, UserId
from app.domain.models.observation import Observation
from app.domain.models.profile import Profile
from app.domain.models.report import Report


class ProfileRepository(Protocol):
    async def get(self, profile_id: ProfileId) -> Profile | None:
        """``None`` rather than raising.

        "Not found" is a normal answer to a question, not an exceptional event. The
        *use case* decides whether that means 404, or "create one", or "skip". A
        repository that raises has made that decision for every caller.
        """
        ...

    async def list_for_owner(self, owner_id: UserId) -> list[Profile]: ...

    async def add(self, profile: Profile) -> None:
        """No return value, and no ``commit``.

        The id was generated before this call, so there is nothing to hand back. The
        transaction belongs to the use case (CP6), not here - one use case, one
        commit, decided in one place.
        """
        ...

    async def update(self, profile: Profile) -> None: ...

    async def delete(self, profile_id: ProfileId) -> None: ...


class ReportRepository(Protocol):
    async def get(self, report_id: ReportId) -> Report | None: ...

    async def find_by_hash(self, profile_id: ProfileId, sha256: str) -> Report | None:
        """The idempotency lookup: same bytes, same profile, same report.

        This one method is why tapping "upload" twice on a bad connection does not
        produce two copies of the same blood test.
        """
        ...

    async def list_for_profile(self, profile_id: ProfileId) -> list[Report]:
        """Newest collection date first - the order a history screen wants."""
        ...

    async def add(self, report: Report) -> None: ...

    async def update(self, report: Report) -> None: ...


class ObservationRepository(Protocol):
    async def list_for_report(self, report_id: ReportId) -> list[Observation]: ...

    async def history(self, profile_id: ProfileId, canonical_test_id: str) -> list[Observation]:
        """Every reading of one marker for one person, oldest first.

        This is the trend query, and it is the single most valuable thing the product
        can do. It is also the reason ``canonical_test_id`` and unit normalisation
        have to be right: get either wrong and this silently returns an incoherent
        mix of two different tests.
        """
        ...

    async def replace_for_page(
        self, report_id: ReportId, page: int, observations: list[Observation]
    ) -> None:
        """Set the observations for one page, replacing whatever was there.

        This method started life as ``add_many`` with an upsert behind it, and that
        was wrong. The unique constraint is
        ``(report_id, page, canonical_test_id)`` — and in Postgres, NULLs are
        *distinct* in a unique constraint. We rely on that so a page can hold several
        tests we could not map. But it also means an upsert never matches an unmapped
        row, so re-running extraction would silently duplicate every one of them.

        "Replace this page" is idempotent by construction instead of by constraint,
        so it holds for mapped and unmapped rows alike. It is also the right shape
        for the pipeline, which extracts page by page in parallel: re-running page 7
        must not disturb pages 1-6.

        Worth remembering as a general lesson — an interface that needs a subtle
        database behaviour to be correct is usually the wrong interface.
        """
        ...

    async def delete_for_report(self, report_id: ReportId) -> None: ...

    async def get(self, observation_id: ObservationId) -> Observation | None: ...
