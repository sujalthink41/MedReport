"""Upload use case, against fakes.

The idempotency tests are the ones that matter: they encode why tapping upload
twice on a bad connection gives one report rather than two.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.adapters.storage.local import LocalDiskStorage
from app.adapters.system import FrozenClock, Uuid4Generator
from app.application.use_cases.upload_report import (
    MAX_BYTES,
    IncomingFile,
    UploadReport,
)
from app.domain.errors import (
    FileTooLargeError,
    InvalidInputError,
    PermissionDeniedError,
    UnsupportedFileTypeError,
)
from app.domain.models.authz import MembershipRole
from app.domain.models.enums import ReportStatus
from app.domain.models.identifiers import ProfileId, UserId
from app.domain.services.policy import AuthorizationContext
from tests.fakes import InMemoryUnitOfWork, RecordingTaskQueue

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
PROFILE = ProfileId(uuid4())

PDF = b"%PDF-1.4\n" + b"x" * 500
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 500
JPG = b"\xff\xd8\xff" + b"x" * 500


def context(role: MembershipRole | None = MembershipRole.OWNER) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=UserId(uuid4()),
        memberships={PROFILE: role} if role else {},
    )


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork()


@pytest.fixture
def queue() -> RecordingTaskQueue:
    return RecordingTaskQueue()


@pytest.fixture
def upload(uow: InMemoryUnitOfWork, queue: RecordingTaskQueue, tmp_path):  # type: ignore[no-untyped-def]
    return UploadReport(
        reports=uow.reports,
        storage=LocalDiskStorage(root=tmp_path, signing_secret="test-secret-32-chars-minimum-here"),
        queue=queue,
        clock=FrozenClock(NOW),
        ids=Uuid4Generator(),
    )


def file(data: bytes = PDF, content_type: str = "application/pdf") -> IncomingFile:
    return IncomingFile(filename="report.pdf", content_type=content_type, data=data)


class TestHappyPath:
    async def test_a_report_is_recorded_and_queued(self, upload, uow, queue) -> None:  # type: ignore[no-untyped-def]
        result = await upload.execute(context(), PROFILE, file())

        assert result.is_duplicate is False
        assert result.report.status is ReportStatus.QUEUED
        assert await uow.reports.get(result.report.id) is not None
        assert queue.dispatched == [("process_report", {"report_id": str(result.report.id)})]

    async def test_the_file_is_stored_before_the_row(self, upload, tmp_path) -> None:  # type: ignore[no-untyped-def]
        result = await upload.execute(context(), PROFILE, file())

        stored = (tmp_path / result.report.storage_key).read_bytes()
        assert stored == PDF

    @pytest.mark.parametrize(
        ("data", "content_type"),
        [(PDF, "application/pdf"), (PNG, "image/png"), (JPG, "image/jpeg")],
    )
    async def test_accepted_formats(self, upload, data, content_type) -> None:  # type: ignore[no-untyped-def]
        result = await upload.execute(context(), PROFILE, file(data, content_type))

        assert result.report.content_type == content_type


class TestIdempotency:
    async def test_the_same_file_twice_returns_the_same_report(self, upload, uow) -> None:  # type: ignore[no-untyped-def]
        first = await upload.execute(context(), PROFILE, file())

        second = await upload.execute(context(), PROFILE, file())

        # Tapping upload twice on a flaky connection must not produce two copies
        # of one blood test.
        assert second.is_duplicate is True
        assert second.report.id == first.report.id
        assert len(uow.reports.items) == 1

    async def test_a_duplicate_is_not_queued_again(self, upload, queue) -> None:  # type: ignore[no-untyped-def]
        await upload.execute(context(), PROFILE, file())
        await upload.execute(context(), PROFILE, file())

        # Re-enqueuing would run the pipeline a second time and charge us twice
        # for the same vision calls.
        assert len(queue.dispatched) == 1

    async def test_different_content_is_a_different_report(self, upload, uow) -> None:  # type: ignore[no-untyped-def]
        await upload.execute(context(), PROFILE, file(PDF))
        await upload.execute(context(), PROFILE, file(PDF + b"different"))

        assert len(uow.reports.items) == 2

    async def test_two_profiles_may_upload_the_same_file(self, upload, uow) -> None:  # type: ignore[no-untyped-def]
        other = ProfileId(uuid4())
        both = AuthorizationContext(
            user_id=UserId(uuid4()),
            memberships={PROFILE: MembershipRole.OWNER, other: MembershipRole.OWNER},
        )

        await upload.execute(both, PROFILE, file())
        await upload.execute(both, other, file())

        # A household scans one PDF for two people; twins get near-identical
        # reports. Uniqueness is per profile, never global.
        assert len(uow.reports.items) == 2


class TestValidation:
    async def test_an_empty_file_is_rejected(self, upload) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(InvalidInputError):
            await upload.execute(context(), PROFILE, file(b""))

    async def test_an_oversized_file_is_rejected(self, upload) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(FileTooLargeError):
            await upload.execute(context(), PROFILE, file(b"%PDF" + b"x" * MAX_BYTES))

    async def test_an_unsupported_type_is_rejected(self, upload) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(UnsupportedFileTypeError):
            await upload.execute(context(), PROFILE, file(b"MZ...", "application/x-msdownload"))

    async def test_a_renamed_file_is_rejected(self, upload) -> None:  # type: ignore[no-untyped-def]
        # Declared as a PDF, but the bytes are a Windows executable. A client can
        # claim anything in a Content-Type header; the magic bytes cannot lie as
        # easily. Trusting the header wastes a vision call at best.
        with pytest.raises(UnsupportedFileTypeError, match="content_mismatch"):
            await upload.execute(context(), PROFILE, file(b"MZ\x90\x00", "application/pdf"))

    async def test_a_content_type_with_parameters_is_accepted(self, upload) -> None:  # type: ignore[no-untyped-def]
        # Browsers send "application/pdf; charset=binary". Rejecting that would
        # break real uploads for no reason.
        result = await upload.execute(
            context(), PROFILE, file(PDF, "application/pdf; charset=binary")
        )

        assert result.report.id is not None

    async def test_nothing_is_stored_when_validation_fails(self, upload, uow, queue) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(UnsupportedFileTypeError):
            await upload.execute(context(), PROFILE, file(b"MZ", "application/x-msdownload"))

        assert len(uow.reports.items) == 0
        assert queue.dispatched == []


class TestAuthorization:
    async def test_a_viewer_cannot_upload(self, upload) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(PermissionDeniedError):
            await upload.execute(context(MembershipRole.VIEWER), PROFILE, file())

    async def test_a_caregiver_can_upload(self, upload) -> None:  # type: ignore[no-untyped-def]
        # The point of the caregiver role: the person doing the day-to-day work
        # of managing a parent's health must be able to add reports.
        result = await upload.execute(context(MembershipRole.CAREGIVER), PROFILE, file())

        assert result.report.id is not None

    async def test_a_stranger_cannot_upload(self, upload, uow) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(PermissionDeniedError):
            await upload.execute(context(None), PROFILE, file())

        assert len(uow.reports.items) == 0
