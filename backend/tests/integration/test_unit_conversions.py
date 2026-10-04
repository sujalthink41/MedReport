"""Learned unit conversions, against real Postgres."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.dictionary_repository import SqlDictionaryRepository
from app.domain.models.clinical import ProposedTest
from app.domain.models.enums import RangeSource
from app.domain.models.identifiers import CanonicalTestId
from app.domain.models.measurement import CanonicalValue, ReferenceRange, Unit
from app.domain.services.test_names import alias_keys
from app.domain.services.units import ConversionFactor, derive_factor

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
GLUCOSE = CanonicalTestId("glucose")
MG_DL = Unit("mg/dL")
MMOL_L = Unit("mmol/L")


def rng(low: str, high: str, unit: Unit) -> ReferenceRange:
    return ReferenceRange(
        low=CanonicalValue.of(low, unit),
        high=CanonicalValue.of(high, unit),
        source=RangeSource.LAB,
    )


@pytest.fixture
async def dictionary(session: AsyncSession) -> SqlDictionaryRepository:
    repo = SqlDictionaryRepository(session)
    await repo.record_proposal(
        ProposedTest(canonical_id="glucose", display_name="Glucose"),
        raw_name="Glucose",
        keys=alias_keys("Glucose"),
        at=NOW,
    )
    return repo


class TestLearning:
    async def test_a_derived_factor_is_stored_and_found(
        self, dictionary: SqlDictionaryRepository
    ) -> None:
        factor = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))
        assert factor is not None

        await dictionary.learn_conversion(GLUCOSE, factor, at=NOW)

        found = await dictionary.find_conversion(GLUCOSE, "mmol/L", "mg/dL")
        assert found is not None
        assert Decimal("17.5") < found.factor < Decimal("18.5")

    async def test_the_reverse_direction_is_answered_too(
        self, dictionary: SqlDictionaryRepository
    ) -> None:
        factor = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))
        assert factor is not None
        await dictionary.learn_conversion(GLUCOSE, factor, at=NOW)

        reverse = await dictionary.find_conversion(GLUCOSE, "mg/dL", "mmol/L")

        # Inverted on read rather than stored as a second row. Two rows would be
        # two facts that could drift apart.
        assert reverse is not None
        assert Decimal("0.054") < reverse.factor < Decimal("0.058")

    async def test_an_unknown_pair_returns_nothing(
        self, dictionary: SqlDictionaryRepository
    ) -> None:
        # Not an error. The trend simply does not join across these units, and the
        # user sees two honest series rather than one fabricated line.
        assert await dictionary.find_conversion(GLUCOSE, "ng/mL", "pmol/L") is None

    async def test_conversions_do_not_leak_between_markers(
        self, dictionary: SqlDictionaryRepository, session: AsyncSession
    ) -> None:
        await dictionary.record_proposal(
            ProposedTest(canonical_id="creatinine", display_name="Creatinine"),
            raw_name="Creatinine",
            keys=alias_keys("Creatinine"),
            at=NOW,
        )
        factor = derive_factor(rng("70", "100", MG_DL), rng("3.9", "5.6", MMOL_L))
        assert factor is not None
        await dictionary.learn_conversion(GLUCOSE, factor, at=NOW)

        # mg/dL to mmol/L depends on molecular weight, so it differs per analyte.
        # Sharing one factor across markers would be confidently wrong.
        assert (
            await dictionary.find_conversion(CanonicalTestId("creatinine"), "mmol/L", "mg/dL")
            is None
        )


class TestImprovement:
    async def test_a_better_derivation_replaces_a_worse_one(
        self, dictionary: SqlDictionaryRepository
    ) -> None:
        sloppy = ConversionFactor(MMOL_L, MG_DL, Decimal("17.0"), agreement=Decimal("0.04"))
        await dictionary.learn_conversion(GLUCOSE, sloppy, at=NOW)

        precise = ConversionFactor(MMOL_L, MG_DL, Decimal("18.0"), agreement=Decimal("0.001"))
        await dictionary.learn_conversion(GLUCOSE, precise, at=NOW)

        # Reports keep arriving, so the same pair gets derived repeatedly from
        # different documents. Keeping the closest agreement means the estimate
        # improves rather than freezing at whatever the first two reports gave.
        found = await dictionary.find_conversion(GLUCOSE, "mmol/L", "mg/dL")
        assert found is not None
        assert found.factor == Decimal("18.0")

    async def test_a_worse_derivation_does_not_replace_a_better_one(
        self, dictionary: SqlDictionaryRepository
    ) -> None:
        precise = ConversionFactor(MMOL_L, MG_DL, Decimal("18.0"), agreement=Decimal("0.001"))
        await dictionary.learn_conversion(GLUCOSE, precise, at=NOW)

        sloppy = ConversionFactor(MMOL_L, MG_DL, Decimal("17.0"), agreement=Decimal("0.04"))
        await dictionary.learn_conversion(GLUCOSE, sloppy, at=NOW)

        found = await dictionary.find_conversion(GLUCOSE, "mmol/L", "mg/dL")
        assert found is not None
        assert found.factor == Decimal("18.0")

    async def test_a_reviewer_entry_is_never_overwritten(
        self, dictionary: SqlDictionaryRepository
    ) -> None:
        reviewed = ConversionFactor(MMOL_L, MG_DL, Decimal("18.016"), agreement=Decimal("0"))
        await dictionary.learn_conversion(GLUCOSE, reviewed, derived_from="reviewer", at=NOW)

        await dictionary.learn_conversion(
            GLUCOSE,
            ConversionFactor(MMOL_L, MG_DL, Decimal("17.0"), agreement=Decimal("0")),
            at=NOW,
        )

        # A human decision outranks any derivation, however good its agreement.
        found = await dictionary.find_conversion(GLUCOSE, "mmol/L", "mg/dL")
        assert found is not None
        assert found.factor == Decimal("18.016")
