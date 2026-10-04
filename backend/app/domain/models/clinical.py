"""The clinical dictionary.

**Nothing here is seeded with medical knowledge.** The tables start empty and fill
from what the model reads out of real reports: it proposes a canonical id and a
display name, code stores it, and the next report that mentions the same marker is
a cache hit.

Why bother storing anything at all, if the model could map names every time?

**Because a trend needs a key that never changes.** If the model maps ``SGPT`` to
``alt`` in January and to ``alanine_transaminase`` in March, that person's liver
chart silently splits into two lines and shows nothing. The dictionary is not
knowledge we are asserting — it is a memory that keeps one answer stable.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.domain.errors import InvalidInputError
from app.domain.models.identifiers import CanonicalTestId


class Sidedness(StrEnum):
    """Which directions are meaningful for a marker.

    LDL and triglycerides have an upper limit and no meaningful lower one, so
    "low LDL" must never be flagged. Proposed by the model from the printed range
    and corrected on review if wrong.
    """

    TWO_SIDED = "two_sided"
    UPPER_ONLY = "upper_only"
    LOWER_ONLY = "lower_only"


class EntryStatus(StrEnum):
    """How much a dictionary entry has been checked.

    PROPOSED entries are used - refusing them would mean the product does nothing
    until a human catches up - but they are listed for review, and anything that
    affects what a user is *told* (critical values especially) is gated on
    APPROVED.
    """

    PROPOSED = "proposed"
    """Written by the extraction pipeline. Good enough to group and trend."""

    APPROVED = "approved"
    """A person has checked it."""

    REJECTED = "rejected"
    """Wrong. Kept rather than deleted so the same mistake is not re-proposed."""


@dataclass(frozen=True)
class CanonicalTest:
    """One marker, as this system knows it."""

    id: CanonicalTestId
    display_name: str
    panel: str | None
    canonical_unit: str | None
    """The unit trends are expressed in — whatever the first report used.

    Not a standard we impose. Picking the first unit we saw and converting later
    arrivals to match is enough for a coherent chart, and avoids asserting that
    one unit is correct.
    """

    sidedness: Sidedness
    status: EntryStatus
    created_at: datetime
    proposed_by: str | None = None
    """``model`` or a reviewer's id. Makes "what did the model decide on its own?"
    a query rather than an archaeology exercise."""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise InvalidInputError(field="canonical_test_id", reason="empty")
        if not self.display_name.strip():
            raise InvalidInputError(field="display_name", reason="empty")


@dataclass(frozen=True)
class TestAlias:
    """A printed name, and the marker it resolves to."""

    key: str
    """Output of ``test_names.alias_keys`` — already normalised."""

    canonical_test_id: CanonicalTestId
    raw_example: str
    """One real printed form, kept for review. "sgpt alt" is hard to judge;
    "SGPT (ALT)" as it appeared on a Thyrocare report is not."""

    status: EntryStatus
    created_at: datetime


@dataclass(frozen=True)
class UnmappedName:
    """A printed name we could not resolve. The dictionary's growth queue.

    Never silently dropped. An unmapped row is still shown to the user — we just
    cannot trend or judge it — and it lands here so the gap is visible instead of
    being a blind spot nobody measures.
    """

    key: str
    raw_example: str
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_to: CanonicalTestId | None = None


@dataclass(frozen=True)
class Resolution:
    """The answer to "what marker is this?"."""

    canonical_test_id: CanonicalTestId | None
    matched_key: str | None = None
    test: CanonicalTest | None = None

    @property
    def is_mapped(self) -> bool:
        return self.canonical_test_id is not None


@dataclass(frozen=True)
class CriticalValue:
    """A result that needs care today, not a trend line.

    **The one table where a human must sign off**, and the one place generated
    copy is forbidden. If a model phrases this softly on a bad day, someone with a
    genuinely dangerous result reads "you may want to discuss this" instead of
    "seek care today".

    So: the threshold is data, the message is a fixed template, and nothing fires
    unless ``status`` is APPROVED. A PROPOSED entry sits in the review queue and
    affects nobody.
    """

    canonical_test_id: CanonicalTestId
    comparator: str
    """``lt`` or ``gt``. Deliberately not an expression - an evaluated string in
    this table would be code execution driven by a database row."""

    threshold: str
    unit: str
    message_template: str
    source: str
    """Where the threshold came from, e.g. "ADA 2025". Review is impossible
    without it, and an uncited clinical number is not reviewable, only believable."""

    status: EntryStatus
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.comparator not in {"lt", "gt"}:
            raise InvalidInputError(field="comparator", reason="must be lt or gt")
        if not self.source.strip():
            raise InvalidInputError(field="source", reason="required_for_review")

    @property
    def is_active(self) -> bool:
        return self.status is EntryStatus.APPROVED


@dataclass(frozen=True)
class ProposedTest:
    """What the extraction model suggests for a name it has not seen before."""

    canonical_id: str
    display_name: str
    panel: str | None = None
    sidedness: Sidedness = Sidedness.TWO_SIDED
    synonyms: list[str] = field(default_factory=list)
