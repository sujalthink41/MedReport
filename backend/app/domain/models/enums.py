"""The closed sets of values in the domain.

``StrEnum`` rather than bare strings, for one reason: mypy can check an enum.

    if band == "borderline":     # typo "bordeline" ships silently
    if band is Band.BORDERLINE:  # typo is a type error before you commit

They serialise as their string value, so the wire format stays readable JSON.

**These values are frozen forever.** They appear in the database, in API responses
and in every client. Renaming ``out_of_range`` later breaks every app in the wild.
Adding a new member is safe; changing an existing one is not.
"""

from enum import StrEnum


class Band(StrEnum):
    """How a measured value sits against its reference range.

    Assigned by deterministic code (CP15), never by a model. See ADR 0002.
    """

    NORMAL = "normal"
    BORDERLINE = "borderline"
    """Inside the range but within ~10% of an edge. The 'watch this' zone."""

    OUT_OF_RANGE = "out_of_range"
    NEEDS_ATTENTION = "needs_attention"
    """Past a hard clinical threshold. Triggers templated, code-written copy."""

    UNKNOWN = "unknown"
    """No reference range available. We show the value and say we cannot judge it."""

    UNREADABLE = "unreadable"
    """Extraction could not read this row reliably. Shown honestly, never guessed."""


class Direction(StrEnum):
    """Which way a value sits. Separate from ``Band`` on purpose.

    'Out of range' and 'high' are different facts. A ferritin can be out of range by
    being low; the UI arrow and the explanation both depend on knowing which.
    """

    LOW = "low"
    WITHIN = "within"
    HIGH = "high"
    UNDETERMINED = "undetermined"


class RangeSource(StrEnum):
    """Where the reference range came from. Surfaced to the user.

    A range printed by the lab that ran the sample is stronger evidence than a
    general population table. Hiding that difference would be dishonest, so the
    source travels with the range everywhere.
    """

    LAB = "lab"
    """Printed on the report itself. Layer 1 - preferred."""

    GUIDELINE = "guideline"
    """A clinical guideline threshold that overrides the lab range. Layer 3."""

    FALLBACK = "fallback"
    """Our own reference table, used when the report printed none. Layer 2."""


class Sex(StrEnum):
    """The sex a laboratory uses to select reference intervals.

    Haemoglobin, ferritin and creatinine all have materially different intervals,
    so this is clinically necessary rather than administrative.

    ``UNSPECIFIED`` is a real, supported case, not an error: when we do not know, we
    fall back to the widest applicable interval and mark the result accordingly,
    rather than guessing and quietly flagging someone incorrectly.
    """

    FEMALE = "female"
    MALE = "male"
    UNSPECIFIED = "unspecified"


class Relationship(StrEnum):
    """Who a profile belongs to, relative to the account holder.

    Exists because the primary user manages their parents' reports - see the
    product brief. The caretaker is the main user, not an edge case.
    """

    SELF = "self"
    PARENT = "parent"
    CHILD = "child"
    SPOUSE = "spouse"
    OTHER = "other"


class ReportStatus(StrEnum):
    """Where a report is in the processing pipeline."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"

    PARTIAL = "partial"
    """Some pages failed, most succeeded.

    A first-class outcome, not a failure. A 25-page report where page 7 could not be
    read should give the user 24 pages of results and an honest note - designing this
    all-or-nothing would be simpler and worse.
    """

    FAILED = "failed"
