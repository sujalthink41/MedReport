"""Translation between domain objects and table rows.

Boring functions that nobody enjoys writing, and the reason a schema change does not
page you at 2am. This is the shock absorber between two things that change for
different reasons.

Notice what happens to ``CanonicalValue`` and ``ReferenceRange``: in the domain they
are single objects with rules attached; in the database they are flat columns
(``value_amount``, ``value_unit``, ``ref_low``...). Neither shape is wrong. A
database is good at flat columns; a domain is good at objects that cannot be built
invalid. **The mapper is where those two truths meet**, and its existence is what
lets each side be shaped for its own job.

One rule: **nothing here leaks.** A ``ReportRow`` never travels past the repository,
and a domain object never reaches SQLAlchemy except through these functions.
"""

from decimal import Decimal
from uuid import UUID

from app.adapters.db.models import ObservationRow, ProfileRow, ReportRow
from app.domain.models import (
    Band,
    CanonicalValue,
    Direction,
    Observation,
    ObservationId,
    Profile,
    ProfileId,
    RangeSource,
    ReferenceRange,
    Relationship,
    Report,
    ReportId,
    ReportStatus,
    Sex,
    Unit,
    UserId,
)
from app.domain.models.identifiers import CanonicalTestId

# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def profile_to_domain(row: ProfileRow) -> Profile:
    return Profile(
        id=ProfileId(row.id),
        owner_id=UserId(row.owner_id),
        display_name=row.display_name,
        date_of_birth=row.date_of_birth,
        # Strings come back from the database and become enums here. If a row holds
        # a value the enum does not know, this raises at the boundary rather than
        # letting an unknown string wander into business logic.
        sex=Sex(row.sex),
        relationship=Relationship(row.relationship),
        created_at=row.created_at,
    )


def profile_to_row(profile: Profile) -> ProfileRow:
    return ProfileRow(
        id=profile.id,
        owner_id=profile.owner_id,
        display_name=profile.display_name,
        date_of_birth=profile.date_of_birth,
        sex=profile.sex.value,
        relationship=profile.relationship.value,
        created_at=profile.created_at,
    )


def apply_profile(row: ProfileRow, profile: Profile) -> None:
    """Copy changes onto a row already loaded in this session.

    Updates need this rather than ``profile_to_row``: building a fresh object would
    give SQLAlchemy a second instance with the same primary key, and the session
    would not know it is meant to be an UPDATE. Mutating the loaded row is what
    makes the unit of work emit the right statement.

    ``created_at`` is deliberately not copied. It is set once, by the database.
    """
    row.display_name = profile.display_name
    row.date_of_birth = profile.date_of_birth
    row.sex = profile.sex.value
    row.relationship = profile.relationship.value


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def report_to_domain(row: ReportRow) -> Report:
    return Report(
        id=ReportId(row.id),
        profile_id=ProfileId(row.profile_id),
        storage_key=row.storage_key,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        status=ReportStatus(row.status),
        created_at=row.created_at,
        page_count=row.page_count,
        lab_name=row.lab_name,
        collected_at=row.collected_at,
    )


def report_to_row(report: Report) -> ReportRow:
    return ReportRow(
        id=report.id,
        profile_id=report.profile_id,
        storage_key=report.storage_key,
        content_type=report.content_type,
        size_bytes=report.size_bytes,
        sha256=report.sha256,
        status=report.status.value,
        created_at=report.created_at,
        page_count=report.page_count,
        lab_name=report.lab_name,
        collected_at=report.collected_at,
    )


def apply_report(row: ReportRow, report: Report) -> None:
    row.status = report.status.value
    row.page_count = report.page_count
    row.lab_name = report.lab_name
    row.collected_at = report.collected_at


# ---------------------------------------------------------------------------
# Observation — the interesting one
# ---------------------------------------------------------------------------


def _value_to_domain(amount: Decimal | None, unit: str | None) -> CanonicalValue | None:
    """Two nullable columns become one optional value object, or nothing.

    A row with an amount but no unit is meaningless - it is a number nobody can
    judge - so it maps to ``None`` rather than to a value object with a guessed
    unit. Guessing here would be inventing data about someone's health.
    """
    if amount is None or unit is None:
        return None
    return CanonicalValue(amount=amount, unit=Unit(unit))


def _range_to_domain(
    low: Decimal | None, high: Decimal | None, unit: str | None, source: str | None
) -> ReferenceRange | None:
    if source is None or unit is None or (low is None and high is None):
        return None
    measure_unit = Unit(unit)
    return ReferenceRange(
        low=CanonicalValue(amount=low, unit=measure_unit) if low is not None else None,
        high=CanonicalValue(amount=high, unit=measure_unit) if high is not None else None,
        source=RangeSource(source),
    )


def observation_to_domain(row: ObservationRow) -> Observation:
    return Observation(
        id=ObservationId(row.id),
        report_id=ReportId(row.report_id),
        profile_id=ProfileId(row.profile_id),
        raw_test_name=row.raw_test_name,
        page=row.page,
        canonical_test_id=(
            CanonicalTestId(row.canonical_test_id) if row.canonical_test_id else None
        ),
        value=_value_to_domain(row.value_amount, row.value_unit),
        reference_range=_range_to_domain(row.ref_low, row.ref_high, row.value_unit, row.ref_source),
        band=Band(row.band),
        direction=Direction(row.direction),
        extraction_confidence=row.extraction_confidence,
    )


def observation_to_values(observation: Observation) -> dict[str, object]:
    """A plain dict rather than an ORM object.

    Observations are written in bulk - hundreds per report - through a single
    INSERT ... ON CONFLICT statement. That takes dicts, not mapped instances, and it
    is dramatically faster than adding hundreds of objects to a session one by one.
    """
    value = observation.value
    reference = observation.reference_range
    return {
        "id": observation.id,
        "report_id": observation.report_id,
        "profile_id": observation.profile_id,
        "raw_test_name": observation.raw_test_name,
        "page": observation.page,
        "canonical_test_id": observation.canonical_test_id,
        "value_amount": value.amount if value else None,
        # The unit is stored once and serves both the value and its range - they are
        # guaranteed to match, because ReferenceRange refuses to be built otherwise.
        "value_unit": str(value.unit) if value else (str(reference.unit) if reference else None),
        "ref_low": reference.low.amount if reference and reference.low else None,
        "ref_high": reference.high.amount if reference and reference.high else None,
        "ref_source": reference.source.value if reference else None,
        "band": observation.band.value,
        "direction": observation.direction.value,
        "extraction_confidence": observation.extraction_confidence,
    }


def as_uuid(value: UUID) -> UUID:
    """Strip a NewType back to a plain UUID for the query layer."""
    return value
