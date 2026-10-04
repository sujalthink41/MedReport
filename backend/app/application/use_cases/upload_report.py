"""Upload a lab report.

The sequence: authorize, validate, hash, dedupe, store, record, enqueue.

Read the dependencies in ``__init__``: four ports and zero technologies. This code
does not know it is behind HTTP, that storage is Cloudflare, or that the queue is
Celery. The same class serves a bulk importer or a mobile backend unchanged.
"""

import hashlib
from dataclasses import dataclass

from app.domain.errors import FileTooLargeError, InvalidInputError, UnsupportedFileTypeError
from app.domain.models.authz import Permission
from app.domain.models.enums import ReportStatus
from app.domain.models.identifiers import ProfileId, ReportId
from app.domain.models.report import Report
from app.domain.ports.queue import TaskQueue
from app.domain.ports.repositories import ReportRepository
from app.domain.ports.services import Clock, FileStorage, IdGenerator
from app.domain.services.policy import AuthorizationContext, require

MAX_BYTES = 50 * 1024 * 1024
"""50MB. Abuse protection, not a product limit.

There is deliberately no page limit: the best user is the one who did a full-body
checkup package, and those run 15-25 pages in India. Capping pages would block
exactly the person we most want.
"""

ACCEPTED_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "image/heif": "heic",
}

# Magic bytes, checked against the declared content type. A client can claim
# anything in a Content-Type header; the first few bytes of the file cannot lie as
# easily. This is cheap defence against someone uploading an executable labelled
# as a PDF.
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "pdf": (b"%PDF",),
    "jpg": (b"\xff\xd8\xff",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "heic": (),  # HEIC's marker sits at offset 4; checked separately below.
}


@dataclass(frozen=True)
class IncomingFile:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class UploadResult:
    report: Report
    is_duplicate: bool
    """True when this exact file was already uploaded for this profile.

    Not an error. A user tapping upload twice on a bad connection should get their
    existing report back, not a second copy of the same blood test.
    """


class UploadReport:
    def __init__(
        self,
        *,
        reports: ReportRepository,
        storage: FileStorage,
        queue: TaskQueue,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._reports = reports
        self._storage = storage
        self._queue = queue
        self._clock = clock
        self._ids = ids

    async def execute(
        self, context: AuthorizationContext, profile_id: ProfileId, file: IncomingFile
    ) -> UploadResult:
        require(context, Permission.REPORT_UPLOAD, profile_id=profile_id)

        extension = self._validate(file)
        digest = hashlib.sha256(file.data).hexdigest()

        existing = await self._reports.find_by_hash(profile_id, digest)
        if existing is not None:
            # Idempotent. Note we return BEFORE storing or enqueuing: a retry must
            # not re-upload 20MB or start a second pipeline run that would charge
            # us twice for the same vision calls.
            return UploadResult(report=existing, is_duplicate=True)

        report_id = ReportId(self._ids.new_id())
        # The digest is in the key, so the object store is content-addressed too -
        # the same bytes always land in the same place, and a replayed upload
        # overwrites rather than accumulating orphans.
        key = f"profiles/{profile_id}/reports/{report_id}.{extension}"

        # Store the file BEFORE the database row. If storing fails we have no row,
        # which is a clean "upload failed, try again". The other order would leave
        # a row pointing at a key that does not exist - a report that can never be
        # processed and never goes away.
        await self._storage.put(key, file.data, file.content_type)

        report = Report(
            id=report_id,
            profile_id=profile_id,
            storage_key=key,
            content_type=file.content_type,
            size_bytes=len(file.data),
            sha256=digest,
            status=ReportStatus.QUEUED,
            created_at=self._clock.now(),
        )
        await self._reports.add(report)

        # Enqueued last, and only the id is sent. The worker re-reads the row, so
        # the message stays tiny and cannot carry stale data. The transaction has
        # not committed yet - CP12 handles that ordering so a worker cannot start
        # before the row it needs is visible.
        await self._queue.enqueue("process_report", report_id=str(report_id))

        return UploadResult(report=report, is_duplicate=False)

    def _validate(self, file: IncomingFile) -> str:
        if not file.data:
            raise InvalidInputError(field="file", reason="empty")
        if len(file.data) > MAX_BYTES:
            raise FileTooLargeError(size_bytes=len(file.data), limit=MAX_BYTES)

        extension = ACCEPTED_TYPES.get(file.content_type.split(";")[0].strip().lower())
        if extension is None:
            raise UnsupportedFileTypeError(content_type=file.content_type)

        if not self._looks_like(extension, file.data):
            # The declared type and the actual bytes disagree. Refuse rather than
            # trust the header - a renamed file wastes a vision call at best and
            # is hostile at worst.
            raise UnsupportedFileTypeError(
                content_type=file.content_type, reason="content_mismatch"
            )

        return extension

    @staticmethod
    def _looks_like(extension: str, data: bytes) -> bool:
        if extension == "heic":
            # ISO base media format: "ftyp" at offset 4, then a brand such as
            # heic/heix/mif1. Checking the container is enough here.
            return len(data) > 12 and data[4:8] == b"ftyp"
        signatures = _SIGNATURES.get(extension, ())
        return any(data.startswith(signature) for signature in signatures)
