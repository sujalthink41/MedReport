"""Tests for Profile, Report and Observation."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.domain.errors import InvalidInputError
from app.domain.models import (
    Band,
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
    UserId,
)
from app.domain.models.measurement import CanonicalValue, Unit
from app.domain.models.observation import Observation

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
G_DL = Unit("g/dL")


def make_profile(**overrides: object) -> Profile:
    defaults: dict[str, object] = {
        "id": ProfileId(uuid4()),
        "owner_id": UserId(uuid4()),
        "display_name": "Amma",
        "date_of_birth": date(1962, 4, 11),
        "sex": Sex.FEMALE,
        "relationship": Relationship.PARENT,
        "created_at": NOW,
    }
    return Profile(**(defaults | overrides))  # type: ignore[arg-type]


def make_report(**overrides: object) -> Report:
    defaults: dict[str, object] = {
        "id": ReportId(uuid4()),
        "profile_id": ProfileId(uuid4()),
        "storage_key": "reports/abc.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1024,
        "sha256": "a" * 64,
        "status": ReportStatus.QUEUED,
        "created_at": NOW,
    }
    return Report(**(defaults | overrides))  # type: ignore[arg-type]


class TestProfile:
    def test_a_blank_name_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError):
            make_profile(display_name="   ")

    def test_naive_datetimes_are_rejected(self) -> None:
        # A naive datetime compared against an aware one raises at runtime, and
        # trend arithmetic silently shifts by hours. Refuse it at the door rather
        # than debug it six months later.
        with pytest.raises(InvalidInputError, match="timezone-aware"):
            make_profile(created_at=datetime(2026, 9, 6, 12, 0))  # noqa: DTZ001

    def test_age_is_computed_against_a_supplied_date(self) -> None:
        profile = make_profile(date_of_birth=date(1962, 4, 11))

        assert profile.age_years(date(2026, 4, 10)) == 63  # day before birthday
        assert profile.age_years(date(2026, 4, 11)) == 64  # on the birthday
        assert profile.age_years(date(2026, 4, 12)) == 64

    def test_age_before_birth_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError):
            make_profile(date_of_birth=date(2020, 1, 1)).age_years(date(2019, 1, 1))

    def test_renaming_keeps_identity_and_returns_a_new_object(self) -> None:
        original = make_profile(display_name="Amma")

        renamed = original.renamed_to("Mother")

        assert renamed.id == original.id  # same person
        assert renamed.display_name == "Mother"
        assert original.display_name == "Amma"  # the old value is untouched


class TestReportValidation:
    def test_a_truncated_hash_is_rejected(self) -> None:
        # sha256 is what makes upload idempotent. A malformed one silently breaks
        # deduplication, and the user gets two copies of one blood test.
        with pytest.raises(InvalidInputError, match="64 hex"):
            make_report(sha256="abc")

    def test_an_empty_file_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError):
            make_report(size_bytes=0)

    def test_zero_pages_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError):
            make_report(page_count=0)


class TestReportStatusMachine:
    @pytest.mark.parametrize(
        ("start", "target", "allowed"),
        [
            (ReportStatus.QUEUED, ReportStatus.PROCESSING, True),
            (ReportStatus.QUEUED, ReportStatus.FAILED, True),
            (ReportStatus.QUEUED, ReportStatus.COMPLETE, False),  # must be processed
            (ReportStatus.PROCESSING, ReportStatus.COMPLETE, True),
            (ReportStatus.PROCESSING, ReportStatus.PARTIAL, True),
            (ReportStatus.PROCESSING, ReportStatus.PROCESSING, True),  # worker retry
            (ReportStatus.COMPLETE, ReportStatus.QUEUED, False),  # would wipe results
            (ReportStatus.COMPLETE, ReportStatus.PROCESSING, False),
            (ReportStatus.FAILED, ReportStatus.PROCESSING, False),
        ],
    )
    def test_only_sensible_transitions_are_allowed(
        self, start: ReportStatus, target: ReportStatus, allowed: bool
    ) -> None:
        assert make_report(status=start).can_transition_to(target) is allowed

    def test_an_illegal_transition_raises_rather_than_silently_applying(self) -> None:
        # The bug this prevents: a retry path flips a COMPLETE report back to
        # QUEUED and wipes a user's results. That cannot reach production here.
        done = make_report(status=ReportStatus.COMPLETE)

        with pytest.raises(InvalidInputError, match="cannot go from"):
            done.with_status(ReportStatus.QUEUED)

    def test_transitioning_returns_a_new_report(self) -> None:
        queued = make_report(status=ReportStatus.QUEUED)

        processing = queued.with_status(ReportStatus.PROCESSING)

        assert processing.status is ReportStatus.PROCESSING
        assert processing.id == queued.id
        assert queued.status is ReportStatus.QUEUED  # original untouched

    def test_partial_counts_as_having_results(self) -> None:
        # 24 readable pages out of 25 is a useful report, not a failure.
        assert make_report(status=ReportStatus.PARTIAL).has_results
        assert not make_report(status=ReportStatus.FAILED).has_results


class TestObservation:
    def _observation(self, **overrides: object) -> Observation:
        defaults: dict[str, object] = {
            "id": ObservationId(uuid4()),
            "report_id": ReportId(uuid4()),
            "profile_id": ProfileId(uuid4()),
            "raw_test_name": "HAEMOGLOBIN",
            "page": 1,
        }
        return Observation(**(defaults | overrides))  # type: ignore[arg-type]

    def test_an_unmapped_row_is_still_a_valid_observation(self) -> None:
        # We show unmapped rows to the user and log the name for the dictionary
        # queue. Dropping them would hide the product's own blind spots.
        row = self._observation(raw_test_name="SOME NEW ASSAY")

        assert not row.is_mapped
        assert row.band is Band.UNKNOWN

    def test_position_needs_both_a_value_and_a_range(self) -> None:
        assert self._observation().position_in_range is None

        judged = self._observation(
            value=CanonicalValue.of("13.5", G_DL),
            reference_range=ReferenceRange(
                low=CanonicalValue.of("12.0", G_DL),
                high=CanonicalValue.of("15.5", G_DL),
                source=RangeSource.LAB,
            ),
        )
        assert judged.is_judged
        assert judged.position_in_range is not None

    @pytest.mark.parametrize(
        ("band", "expected"),
        [
            (Band.NORMAL, False),
            (Band.BORDERLINE, False),
            (Band.OUT_OF_RANGE, True),
            (Band.NEEDS_ATTENTION, True),
            (Band.UNKNOWN, False),
        ],
    )
    def test_what_the_user_is_shown_first(self, band: Band, expected: bool) -> None:
        assert self._observation(band=band).needs_user_attention is expected
