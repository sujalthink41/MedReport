"""One measured parameter from a report. The core row of the product."""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.models.enums import Band, Direction
from app.domain.models.identifiers import (
    CanonicalTestId,
    ObservationId,
    ProfileId,
    ReportId,
)
from app.domain.models.measurement import CanonicalValue, ReferenceRange


@dataclass(frozen=True, slots=True)
class Observation:
    """A single row: "Haemoglobin 11.2 g/dL (12.0-15.5)".

    Both the raw and the resolved forms are kept, deliberately.

    ``raw_test_name`` is what the lab printed - "S.G.P.T." - and it is never
    discarded. ``canonical_test_id`` is what we resolved it to - "alt" - and it is
    ``None`` when we could not map it.

    Keeping the raw string means that when the alias dictionary improves next month,
    every old report can be re-normalised from stored data without re-reading a
    single PDF or spending a rupee on a vision model. Throwing it away would make
    every dictionary improvement retroactively useless.
    """

    id: ObservationId
    report_id: ReportId
    profile_id: ProfileId

    raw_test_name: str
    page: int

    canonical_test_id: CanonicalTestId | None = None
    value: CanonicalValue | None = None
    reference_range: ReferenceRange | None = None

    band: Band = Band.UNKNOWN
    direction: Direction = Direction.UNDETERMINED

    extraction_confidence: Decimal | None = None

    @property
    def is_mapped(self) -> bool:
        """Did we recognise which test this is?

        Unmapped rows are still shown to the user - we just cannot judge or trend
        them. They also feed the unmapped-names queue, which is how the dictionary
        grows. Silently dropping them would hide the product's own blind spots.
        """
        return self.canonical_test_id is not None

    @property
    def is_readable(self) -> bool:
        return self.band is not Band.UNREADABLE and self.value is not None

    @property
    def is_judged(self) -> bool:
        """Do we have both a value and something to judge it against?"""
        return self.value is not None and self.reference_range is not None

    @property
    def needs_user_attention(self) -> bool:
        return self.band in (Band.OUT_OF_RANGE, Band.NEEDS_ATTENTION)

    @property
    def position_in_range(self) -> Decimal | None:
        """0.0 at the bottom of the range, 1.0 at the top. ``None`` if not applicable."""
        if self.value is None or self.reference_range is None:
            return None
        return self.reference_range.position_of(self.value)
