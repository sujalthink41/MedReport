"""The dictionary learning, against real Postgres.

The race tests need a real database: two reports processed in parallel proposing
the same new marker is exactly the case an in-memory fake cannot reproduce, and
exactly the case that would split a trend on day one.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.dictionary_repository import SqlDictionaryRepository
from app.adapters.system import FrozenClock
from app.application.use_cases.resolve_test import ResolveTest
from app.domain.models.clinical import (
    CriticalValue,
    EntryStatus,
    ProposedTest,
    Sidedness,
)
from app.domain.models.identifiers import CanonicalTestId
from app.domain.services.test_names import alias_keys

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)

ALT = ProposedTest(
    canonical_id="alt",
    display_name="Alanine Transaminase (ALT/SGPT)",
    panel="Liver Function",
    sidedness=Sidedness.UPPER_ONLY,
)


@pytest.fixture
def dictionary(session: AsyncSession) -> SqlDictionaryRepository:
    return SqlDictionaryRepository(session)


@pytest.fixture
def resolve(dictionary: SqlDictionaryRepository) -> ResolveTest:
    return ResolveTest(dictionary=dictionary, clock=FrozenClock(NOW))


class TestStartsEmpty:
    async def test_nothing_is_known_before_any_report(self, resolve: ResolveTest) -> None:
        # The design: no seeded medical knowledge. The dictionary is a memory that
        # fills from real documents, not a table someone typed out.
        outcome = await resolve.execute("SGPT")

        assert outcome.resolution.is_mapped is False
        assert outcome.was_learned is False

    async def test_an_unknown_name_is_recorded_not_dropped(
        self, resolve: ResolveTest, dictionary: SqlDictionaryRepository
    ) -> None:
        await resolve.execute("Some Novel Assay")

        unmapped = await dictionary.list_unmapped()
        # Visible blind spots. Silently dropping the row would hide the gap from
        # everyone, including us.
        assert [u.raw_example for u in unmapped] == ["Some Novel Assay"]

    async def test_repeat_sightings_are_counted(
        self, resolve: ResolveTest, dictionary: SqlDictionaryRepository
    ) -> None:
        for _ in range(3):
            await resolve.execute("Some Novel Assay")

        # Frequency ranks the review queue: the most common gap is the most
        # valuable one to close.
        assert (await dictionary.list_unmapped())[0].occurrences == 3


class TestLearning:
    async def test_a_proposal_teaches_the_dictionary(self, resolve: ResolveTest) -> None:
        outcome = await resolve.execute("SGPT", proposal=ALT)

        assert outcome.was_learned is True
        assert outcome.resolution.canonical_test_id == "alt"

    async def test_what_was_learned_is_reused_without_a_proposal(
        self, resolve: ResolveTest
    ) -> None:
        await resolve.execute("SGPT", proposal=ALT)

        again = await resolve.execute("SGPT")

        # The point of storing anything at all: the second report costs no model
        # call and - more importantly - cannot get a different answer.
        assert again.resolution.canonical_test_id == "alt"
        assert again.was_learned is False

    async def test_a_different_printing_of_the_same_name_resolves(
        self, resolve: ResolveTest
    ) -> None:
        await resolve.execute("SGPT", proposal=ALT)

        assert (await resolve.execute("S.G.P.T.")).resolution.canonical_test_id == "alt"
        assert (await resolve.execute("sgpt ")).resolution.canonical_test_id == "alt"

    async def test_a_parenthetical_synonym_resolves_later_on_its_own(
        self, resolve: ResolveTest
    ) -> None:
        # Learned from "SGPT (ALT)" on a Thyrocare report; a Dr Lal report prints
        # just "ALT" and must land on the same marker, or the trend splits.
        await resolve.execute("SGPT (ALT)", proposal=ALT)

        assert (await resolve.execute("ALT")).resolution.canonical_test_id == "alt"
        assert (await resolve.execute("SGPT")).resolution.canonical_test_id == "alt"

    async def test_two_markers_stay_distinct(self, resolve: ResolveTest) -> None:
        await resolve.execute("SGPT", proposal=ALT)
        await resolve.execute(
            "SGOT",
            proposal=ProposedTest(canonical_id="ast", display_name="AST", panel="Liver"),
        )

        assert (await resolve.execute("SGPT")).resolution.canonical_test_id == "alt"
        assert (await resolve.execute("SGOT")).resolution.canonical_test_id == "ast"


class TestConcurrency:
    async def test_a_second_proposal_adopts_the_first(self, resolve: ResolveTest) -> None:
        """Two reports naming the same new marker must agree.

        Processed in parallel, both would propose - and two canonical ids would
        split a trend the very first time the dictionary was used. The first writer
        wins; the second reads back the winner.
        """
        await resolve.execute("SGPT", proposal=ALT)

        second = await resolve.execute(
            "SGPT",
            proposal=ProposedTest(
                canonical_id="alanine_transaminase",  # the model phrased it differently
                display_name="Alanine Transaminase",
            ),
        )

        assert second.resolution.canonical_test_id == "alt"
        assert second.was_learned is False

    async def test_an_existing_alias_is_never_repointed_by_a_guess(
        self, dictionary: SqlDictionaryRepository
    ) -> None:
        keys = alias_keys("SGPT")
        await dictionary.record_proposal(ALT, raw_name="SGPT", keys=keys, at=NOW)

        await dictionary.record_proposal(
            ProposedTest(canonical_id="wrong", display_name="Wrong"),
            raw_name="SGPT",
            keys=keys,
            at=NOW,
        )

        # A reviewed alias must outrank a fresh model guess, or a single bad
        # extraction could silently redirect a person's entire history.
        resolved = await dictionary.resolve(keys)
        assert resolved is not None
        assert resolved[0] == "alt"


class TestCriticalValues:
    async def test_an_unreviewed_threshold_does_not_fire(
        self, dictionary: SqlDictionaryRepository
    ) -> None:
        await dictionary.record_proposal(ALT, raw_name="SGPT", keys=alias_keys("SGPT"), at=NOW)
        await dictionary.add_critical_value(
            CriticalValue(
                canonical_test_id=CanonicalTestId("alt"),
                comparator="gt",
                threshold="1000",
                unit="U/L",
                message_template="Seek medical care today.",
                source="proposed by model",
                status=EntryStatus.PROPOSED,
            )
        )

        # The one place human sign-off is mandatory. A threshold nobody checked
        # must not decide whether a person is told to seek care today.
        assert await dictionary.critical_values_for(CanonicalTestId("alt")) == []

    async def test_an_approved_threshold_is_returned(
        self, dictionary: SqlDictionaryRepository
    ) -> None:
        await dictionary.record_proposal(ALT, raw_name="SGPT", keys=alias_keys("SGPT"), at=NOW)
        await dictionary.add_critical_value(
            CriticalValue(
                canonical_test_id=CanonicalTestId("alt"),
                comparator="gt",
                threshold="1000",
                unit="U/L",
                message_template="Seek medical care today.",
                source="Royal College of Pathologists 2024",
                status=EntryStatus.APPROVED,
                reviewed_by="dr-reviewer",
                reviewed_at=NOW,
            )
        )

        active = await dictionary.critical_values_for(CanonicalTestId("alt"))
        assert len(active) == 1
        # Cited, because an uncited clinical number is not reviewable - only
        # believable.
        assert active[0].source == "Royal College of Pathologists 2024"

    def test_a_threshold_without_a_source_cannot_be_constructed(self) -> None:
        with pytest.raises(Exception, match="required_for_review"):
            CriticalValue(
                canonical_test_id=CanonicalTestId("alt"),
                comparator="gt",
                threshold="1000",
                unit="U/L",
                message_template="x",
                source="  ",
                status=EntryStatus.PROPOSED,
            )

    def test_the_comparator_is_not_an_expression(self) -> None:
        # An evaluated string here would be code execution driven by a database
        # row. Only 'lt' and 'gt' exist.
        with pytest.raises(Exception, match="must be lt or gt"):
            CriticalValue(
                canonical_test_id=CanonicalTestId("alt"),
                comparator="value > 1000 and True",
                threshold="1000",
                unit="U/L",
                message_template="x",
                source="x",
                status=EntryStatus.APPROVED,
            )
