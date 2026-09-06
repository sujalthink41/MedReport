"""An uploaded lab report and its journey through the pipeline."""

from dataclasses import dataclass
from datetime import date, datetime

from app.domain.errors import InvalidInputError
from app.domain.models.enums import ReportStatus
from app.domain.models.identifiers import ProfileId, ReportId

# Which status may follow which. Everything not listed is forbidden.
#
# Writing this as data rather than as `if` statements has three payoffs: it is
# readable at a glance, it is exhaustively testable, and adding a status is one
# line rather than an audit of every branch in the codebase.
_ALLOWED_TRANSITIONS: dict[ReportStatus, frozenset[ReportStatus]] = {
    ReportStatus.QUEUED: frozenset({ReportStatus.PROCESSING, ReportStatus.FAILED}),
    ReportStatus.PROCESSING: frozenset(
        {
            ReportStatus.COMPLETE,
            ReportStatus.PARTIAL,
            ReportStatus.FAILED,
            # Back to PROCESSING: a worker died and the job was picked up again.
            # Real systems retry, so the state machine must allow it.
            ReportStatus.PROCESSING,
        }
    ),
    # Terminal states. A finished report does not quietly start processing again;
    # reprocessing creates a new attempt rather than mutating history.
    ReportStatus.COMPLETE: frozenset(),
    ReportStatus.PARTIAL: frozenset(),
    ReportStatus.FAILED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Report:
    """One uploaded document.

    ``sha256`` is what makes uploads idempotent: the same bytes for the same profile
    are the same report, so a user tapping upload twice on a flaky connection does
    not get two copies of their blood test.

    ``collected_at`` is the date the *sample was taken*, not the upload date. Every
    trend in the product depends on this. People upload three years of reports in one
    sitting, so ordering by upload date would destroy the very signal we exist for.
    """

    id: ReportId
    profile_id: ProfileId
    storage_key: str
    content_type: str
    size_bytes: int
    sha256: str
    status: ReportStatus
    created_at: datetime
    page_count: int | None = None
    lab_name: str | None = None
    collected_at: date | None = None

    def __post_init__(self) -> None:
        if self.size_bytes <= 0:
            raise InvalidInputError(field="size_bytes", reason="must be positive")
        if len(self.sha256) != 64:
            raise InvalidInputError(field="sha256", reason="expected 64 hex characters")
        if not self.storage_key.strip():
            raise InvalidInputError(field="storage_key", reason="empty")
        if self.created_at.tzinfo is None:
            raise InvalidInputError(field="created_at", reason="must be timezone-aware")
        if self.page_count is not None and self.page_count < 1:
            raise InvalidInputError(field="page_count", reason="must be at least 1")

    @property
    def is_terminal(self) -> bool:
        return not _ALLOWED_TRANSITIONS[self.status]

    @property
    def has_results(self) -> bool:
        """PARTIAL counts. 24 readable pages out of 25 is a useful report."""
        return self.status in (ReportStatus.COMPLETE, ReportStatus.PARTIAL)

    def can_transition_to(self, status: ReportStatus) -> bool:
        return status in _ALLOWED_TRANSITIONS[self.status]

    def with_status(self, status: ReportStatus) -> "Report":
        """Return the same report in a new state, or refuse.

        The refusal is the point. Without it, a bug in a retry path could flip a
        COMPLETE report back to QUEUED and silently wipe a user's results. Here that
        bug cannot compile its way into production - it raises the moment it happens,
        with both states in the error context.
        """
        if not self.can_transition_to(status):
            raise InvalidInputError(
                field="status",
                reason=f"cannot go from {self.status} to {status}",
            )
        return Report(
            id=self.id,
            profile_id=self.profile_id,
            storage_key=self.storage_key,
            content_type=self.content_type,
            size_bytes=self.size_bytes,
            sha256=self.sha256,
            status=status,
            created_at=self.created_at,
            page_count=self.page_count,
            lab_name=self.lab_name,
            collected_at=self.collected_at,
        )
