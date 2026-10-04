"""Report upload and status."""

from uuid import UUID

from fastapi import APIRouter, File, UploadFile, status

from app.adapters.system import SystemClock, Uuid4Generator
from app.api.deps import AuthzDep, QueueDep, StorageDep, UowDep
from app.api.v1.schemas.reports import ReportResponse
from app.application.use_cases.upload_report import (
    MAX_BYTES,
    IncomingFile,
    UploadReport,
)
from app.domain.errors import FileTooLargeError, ProfileNotFoundError, ReportNotFoundError
from app.domain.models.authz import Permission
from app.domain.models.identifiers import ProfileId, ReportId
from app.domain.models.report import Report
from app.domain.services.policy import require

router = APIRouter(prefix="/profiles/{profile_id}/reports", tags=["reports"])


def _to_response(report: Report, *, is_duplicate: bool = False) -> ReportResponse:
    return ReportResponse(
        id=str(report.id),
        profile_id=str(report.profile_id),
        status=report.status,
        size_bytes=report.size_bytes,
        content_type=report.content_type,
        page_count=report.page_count,
        lab_name=report.lab_name,
        collected_at=report.collected_at,
        created_at=report.created_at,
        is_duplicate=is_duplicate,
    )


@router.post("", response_model=ReportResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_report(
    profile_id: UUID,
    context: AuthzDep,
    uow: UowDep,
    storage: StorageDep,
    queue: QueueDep,
    file: UploadFile = File(description="PDF or photo of a lab report, up to 50MB"),  # noqa: B008
) -> ReportResponse:
    """Accept a report and process it in the background.

    **202, not 201.** Extraction takes 30-90 seconds and involves vision models;
    holding the HTTP connection open for that would tie up a worker, time out
    behind most proxies, and fail the upload on a flaky mobile connection after
    the bytes had already arrived. The client polls the status endpoint instead.
    """
    # Checked before reading the body as well as inside the use case: no point
    # buffering 50MB from someone who was never allowed to upload it.
    require(context, Permission.REPORT_UPLOAD, profile_id=ProfileId(profile_id))

    data = await file.read()
    if len(data) > MAX_BYTES:
        raise FileTooLargeError(size_bytes=len(data), limit=MAX_BYTES)

    result = await UploadReport(
        reports=uow.reports,
        storage=storage,
        queue=queue,
        clock=SystemClock(),
        ids=Uuid4Generator(),
    ).execute(
        context,
        ProfileId(profile_id),
        IncomingFile(
            filename=file.filename or "report",
            content_type=file.content_type or "application/octet-stream",
            data=data,
        ),
    )
    return _to_response(result.report, is_duplicate=result.is_duplicate)


@router.get("", response_model=list[ReportResponse])
async def list_reports(profile_id: UUID, context: AuthzDep, uow: UowDep) -> list[ReportResponse]:
    """A profile's reports, newest sample date first."""
    require(context, Permission.REPORT_READ, profile_id=ProfileId(profile_id))

    reports = await uow.reports.list_for_profile(ProfileId(profile_id))
    return [_to_response(r) for r in reports]


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    profile_id: UUID, report_id: UUID, context: AuthzDep, uow: UowDep
) -> ReportResponse:
    """Status and metadata for one report. Polled by the client after upload."""
    require(context, Permission.REPORT_READ, profile_id=ProfileId(profile_id))

    report = await uow.reports.get(ReportId(report_id))
    if report is None:
        raise ReportNotFoundError(report_id=str(report_id))
    if report.profile_id != ProfileId(profile_id):
        # The report exists but belongs to a different profile. Report it as not
        # found rather than forbidden: a 403 here would confirm that this id is a
        # real report belonging to somebody else.
        raise ReportNotFoundError(report_id=str(report_id))

    return _to_response(report)


@router.get("/{report_id}/file", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
async def download_report(
    profile_id: UUID, report_id: UUID, context: AuthzDep, uow: UowDep, storage: StorageDep
) -> None:
    """Redirect to a short-lived signed URL for the original document.

    The bytes never pass through the API. Streaming every megabyte of every report
    through application servers would cost latency and memory for no benefit, and
    the signed URL expires in minutes so the link cannot be shared onward.
    """
    from fastapi import HTTPException

    require(context, Permission.REPORT_READ, profile_id=ProfileId(profile_id))

    report = await uow.reports.get(ReportId(report_id))
    if report is None or report.profile_id != ProfileId(profile_id):
        raise ReportNotFoundError(report_id=str(report_id))

    url = await storage.signed_url(report.storage_key, ttl_seconds=300)
    raise HTTPException(status_code=307, headers={"Location": url})


__all__ = ["ProfileNotFoundError", "router"]
