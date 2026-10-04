"""Request and response shapes for reports."""

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.domain.models.enums import ReportStatus


class ReportResponse(BaseModel):
    id: str
    profile_id: str
    status: ReportStatus
    size_bytes: int
    content_type: str
    page_count: int | None
    lab_name: str | None

    collected_at: date | None = Field(
        default=None,
        description="When the sample was taken, read from the report. Null until "
        "processing extracts it. Everything is ordered by this, not by upload time.",
    )
    created_at: datetime

    is_duplicate: bool = Field(
        default=False,
        description="True when this exact file was already uploaded for this "
        "profile. Not an error - the existing report is returned instead.",
    )


class ReportStatusResponse(BaseModel):
    id: str
    status: ReportStatus
    page_count: int | None
    failure_reason: str | None = None
