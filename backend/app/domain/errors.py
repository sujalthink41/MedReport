"""The exception vocabulary for the whole system.

Two ideas make this file worth more than its size.

**1. Errors carry data, not sentences.**

    raise ProfileNotFoundError(profile_id=pid)          # a record
    raise Exception(f"profile {pid} not found")         # a string

The first can be logged as structured fields, filtered in a dashboard, and mapped to
an HTTP status. The second can only be printed.

**2. The hierarchy is organised by WHO IS AT FAULT, not by what went wrong.**

    DomainError          the caller asked for something invalid  -> 4xx, never retry
    InfrastructureError  something we depend on failed           -> 5xx, retry

That single split drives two unrelated systems — the HTTP layer (CP3) and the retry
policy (CP17) — from one taxonomy. Organising by feature instead ("ReportError",
"ProfileError") would give you neither.

Nothing here imports anything. It is pure domain, usable from the API, the worker,
and a future CLI alike.
"""

from typing import ClassVar


class MedReportError(Exception):
    """Root of everything we raise deliberately.

    Catching this catches *our* errors and lets genuine bugs — TypeError,
    AttributeError — keep propagating. That distinction matters: a bug should crash
    loudly in development, not be quietly swallowed as "an error we handle".
    """

    code: ClassVar[str] = "internal_error"
    retryable: ClassVar[bool] = False

    def __init__(self, **context: object) -> None:
        self.context: dict[str, object] = context
        super().__init__(self._describe())

    def _describe(self) -> str:
        if not self.context:
            return self.code
        fields = ", ".join(f"{key}={value!r}" for key, value in sorted(self.context.items()))
        return f"{self.code}({fields})"


# ---------------------------------------------------------------------------
# Domain errors — the caller did something invalid.
# Map to 4xx. Retrying is pointless: they will fail identically forever.
# ---------------------------------------------------------------------------


class DomainError(MedReportError):
    code: ClassVar[str] = "domain_error"
    retryable: ClassVar[bool] = False


class NotFoundError(DomainError):
    code: ClassVar[str] = "not_found"


class ProfileNotFoundError(NotFoundError):
    code: ClassVar[str] = "profile_not_found"


class ReportNotFoundError(NotFoundError):
    code: ClassVar[str] = "report_not_found"


class ConflictError(DomainError):
    code: ClassVar[str] = "conflict"


class DuplicateReportError(ConflictError):
    """The same file was uploaded twice for the same profile.

    Not really a failure — the upload use case returns the existing report instead.
    It exists as a type so the *decision* is explicit rather than a silent branch.
    """

    code: ClassVar[str] = "duplicate_report"


class InvalidInputError(DomainError):
    code: ClassVar[str] = "invalid_input"


class UnsupportedFileTypeError(InvalidInputError):
    code: ClassVar[str] = "unsupported_file_type"


class FileTooLargeError(InvalidInputError):
    code: ClassVar[str] = "file_too_large"


class PermissionDeniedError(DomainError):
    """Deny by default. Raised by the policy layer in CP8.

    Deliberately says nothing about *why*. Telling an attacker "this profile exists
    but you may not read it" leaks the existence of the resource.
    """

    code: ClassVar[str] = "permission_denied"


# ---------------------------------------------------------------------------
# Infrastructure errors — something we depend on failed.
# Map to 5xx. Retrying is usually the right move.
# ---------------------------------------------------------------------------


class InfrastructureError(MedReportError):
    code: ClassVar[str] = "infrastructure_error"
    retryable: ClassVar[bool] = True


class StorageUnavailableError(InfrastructureError):
    code: ClassVar[str] = "storage_unavailable"


class DatabaseUnavailableError(InfrastructureError):
    code: ClassVar[str] = "database_unavailable"


class LLMUnavailableError(InfrastructureError):
    """Rate limited, timed out, provider down. Transient — back off and retry."""

    code: ClassVar[str] = "llm_unavailable"


class LLMInvalidOutputError(InfrastructureError):
    """The model returned something that does not match the schema.

    Note ``retryable = False`` despite being an infrastructure error. Sending the
    identical prompt again will produce the same malformed answer. The correct
    response is *repair* — feed the validation error back and ask for a correction
    (CP17) — not blind retry.

    This override is the point of a per-class flag rather than a rule like
    "all infrastructure errors are retryable".
    """

    code: ClassVar[str] = "llm_invalid_output"
    retryable: ClassVar[bool] = False


# ---------------------------------------------------------------------------
# Pipeline errors — a stage of report processing failed.
# ---------------------------------------------------------------------------


class PipelineError(MedReportError):
    code: ClassVar[str] = "pipeline_error"


class UnreadableDocumentError(PipelineError):
    """We could not read the file at all — corrupt, encrypted, or not a document."""

    code: ClassVar[str] = "unreadable_document"


class NodeExecutionError(PipelineError):
    """A stage failed after exhausting its retries.

    Carries ``node`` so a partially-processed report can report honestly which stage
    gave up, rather than failing the whole report.
    """

    code: ClassVar[str] = "node_failed"
    retryable: ClassVar[bool] = True
