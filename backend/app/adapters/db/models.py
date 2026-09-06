"""ORM models: the shape of the tables.

**These are not the domain models, and that is deliberate.** Put them side by side:

    domain/models/report.py     Report        business truth + invariants
    adapters/db/models.py       ReportRow     the table

They change for different reasons. Rename a column and only this file plus its
mapper move; the API and the domain do not notice. Add a business rule and only the
domain changes. Collapse them into one class and a database migration breaks your
public API.

The suffix ``Row`` is a deliberate reminder: if a ``ReportRow`` ever escapes past the
repository, something has leaked.

Two conventions used throughout, both worth understanding:

**Enums are stored as strings, not as Postgres ENUM types.** A native enum needs a
migration with ``ALTER TYPE`` to add a value, which locks and is awkward to reverse.
A text column plus a Python ``StrEnum`` gives the same type safety in application
code with none of the migration pain. The database is a store; the meaning lives in
the domain.

**Money-like numbers use ``Numeric``, never ``Float``.** Postgres ``float8`` has the
same rounding problem as Python's float, so an HbA1c of 5.7 could come back as
5.699999. ``Numeric`` maps to ``Decimal`` on the way out and is exact.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.db.base import Base, TimestampMixin

# Lab values span from 0.001 (some hormones) to millions (platelet counts), so the
# precision has to be generous. 6 decimal places is more than any assay reports.
VALUE_NUMERIC = Numeric(20, 6)


class UserRow(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)

    google_sub: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    """Google's stable subject id.

    The identity key, not the email. People change their email address; the sub
    never changes. Keying on email would silently create a second account and
    orphan someone's entire medical history.
    """

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProfileRow(Base, TimestampMixin):
    __tablename__ = "profiles"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    display_name: Mapped[str] = mapped_column(String(120), nullable=False)

    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    """Not nullable, and not optional at signup.

    Reference ranges for haemoglobin, creatinine and ferritin all depend on age and
    sex. Without these two columns the product cannot do its core job, so they are
    required rather than "nice to have later".
    """

    sex: Mapped[str] = mapped_column(String(20), nullable=False)
    relationship: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (Index("ix_profiles_owner", "owner_id"),)


class ReportRow(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )

    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    lab_name: Mapped[str | None] = mapped_column(String(255))

    collected_at: Mapped[date | None] = mapped_column(Date)
    """The date the sample was taken - not the upload date.

    Every trend in the product orders by this. People upload three years of reports
    in one sitting, so ordering by ``created_at`` would scramble the history and
    destroy the one signal we exist to show.
    """

    __table_args__ = (
        # Idempotent upload, enforced by the database rather than by a check in
        # application code. Two concurrent requests uploading the same file both
        # pass an application-level "does it exist?" check; only one survives this.
        UniqueConstraint("profile_id", "sha256", name="uq_reports_profile_sha256"),
        # The history screen: newest sample first, for one profile.
        Index("ix_reports_profile_collected", "profile_id", "collected_at"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
    )


class ObservationRow(Base, TimestampMixin):
    __tablename__ = "observations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    profile_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    """Denormalised on purpose.

    It is reachable via ``report_id -> reports.profile_id``, so storing it again is
    redundant. But the trend query - "every haemoglobin for this person, ever" - is
    the most valuable query in the product, and this column lets it run against one
    index instead of joining reports on every read.

    A deliberate, documented denormalisation. The kind to be suspicious of is the
    undocumented kind.
    """

    # --- as printed on the report -----------------------------------------
    raw_test_name: Mapped[str] = mapped_column(Text, nullable=False)
    """Never discarded.

    When the alias dictionary improves next month, every historical report can be
    re-normalised from this column - no re-reading PDFs, no vision-model spend.
    Throwing it away would make every future dictionary improvement retroactive
    only for new uploads.
    """

    page: Mapped[int] = mapped_column(Integer, nullable=False)
    extraction_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    # --- resolved ---------------------------------------------------------
    canonical_test_id: Mapped[str | None] = mapped_column(String(64))
    value_amount: Mapped[Decimal | None] = mapped_column(VALUE_NUMERIC)
    value_unit: Mapped[str | None] = mapped_column(String(40))

    ref_low: Mapped[Decimal | None] = mapped_column(VALUE_NUMERIC)
    ref_high: Mapped[Decimal | None] = mapped_column(VALUE_NUMERIC)
    ref_source: Mapped[str | None] = mapped_column(String(20))

    # --- computed by the classifier (CP15) --------------------------------
    band: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        # Makes re-running a pipeline node safe: a retry updates rows instead of
        # duplicating them. Retry is only ever safe because of a constraint like
        # this one.
        #
        # Subtlety worth knowing: in Postgres, NULLs are distinct in a unique
        # constraint. So many rows on one page may have canonical_test_id = NULL,
        # which is exactly right - a page can contain several tests we could not
        # map, and none of them should collide.
        UniqueConstraint(
            "report_id",
            "page",
            "canonical_test_id",
            name="uq_observations_report_page_test",
        ),
        # The trend query.
        Index(
            "ix_observations_profile_test",
            "profile_id",
            "canonical_test_id",
        ),
        # Loading one report's results.
        Index("ix_observations_report", "report_id"),
    )
