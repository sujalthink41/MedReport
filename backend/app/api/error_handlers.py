"""The single place exceptions become HTTP responses.

The whole point: **routers contain no try/except.** A use case raises a typed error,
it travels up untouched, and exactly one function decides the status code, the log
level, and what the client is allowed to see.

If you ever find yourself writing try/except in a route, one of two things is true —
the hierarchy is missing a type, or this file is missing a mapping. The fix is
upstream, never in the route.
"""

from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.api.v1.schemas.errors import ErrorDetail, ErrorResponse
from app.core.context import REQUEST_ID_HEADER, get_request_id
from app.core.logging import get_logger
from app.domain.errors import (
    ConflictError,
    DomainError,
    FileTooLargeError,
    InfrastructureError,
    InvalidInputError,
    LLMUnavailableError,
    MedReportError,
    NotFoundError,
    PermissionDeniedError,
    PipelineError,
    StorageUnavailableError,
    UnsupportedFileTypeError,
)

log = get_logger(__name__)


# Only the *categories* need entries. Subclasses resolve through the MRO below, so
# adding `ProfileNotFoundError(NotFoundError)` is automatically a 404 with no edit
# here. That is Open/Closed applied to a lookup table.
STATUS_MAP: dict[type[MedReportError], int] = {
    NotFoundError: 404,
    PermissionDeniedError: 403,
    ConflictError: 409,
    UnsupportedFileTypeError: 415,
    FileTooLargeError: 413,
    InvalidInputError: 422,
    DomainError: 400,  # fallback for any domain error without a specific mapping
    StorageUnavailableError: 503,
    LLMUnavailableError: 503,
    InfrastructureError: 500,
    PipelineError: 500,
}

# Messages are presentation, so they live here rather than on the domain exceptions.
# The domain does not know an HTTP client exists.
#
# Deliberately vague. We never echo an error's `context` to the client: those fields
# can carry identifiers, and in this product could carry PHI.
PUBLIC_MESSAGES: dict[str, str] = {
    "profile_not_found": "That profile could not be found.",
    "report_not_found": "That report could not be found.",
    "not_found": "That resource could not be found.",
    "permission_denied": "You do not have access to this.",
    "duplicate_report": "This report has already been uploaded.",
    "unsupported_file_type": "That file type is not supported. Upload a PDF or an image.",
    "file_too_large": "That file is too large.",
    "invalid_input": "The request was not valid.",
    "storage_unavailable": "Temporarily unable to store files. Please try again.",
    "llm_unavailable": "Report processing is busy. Please try again shortly.",
    "unreadable_document": "We could not read that document.",
}

_GENERIC_MESSAGE = "Something went wrong on our side."


def status_for(exc: MedReportError) -> int:
    """Resolve a status code by walking the exception's inheritance chain.

    ``ProfileNotFoundError`` is not in ``STATUS_MAP``. Its MRO is
    ``ProfileNotFoundError -> NotFoundError -> DomainError -> ...``, so the first
    match found is ``NotFoundError`` and the answer is 404.

    Most specific wins, because ``__mro__`` is ordered most-specific-first.
    """
    for klass in type(exc).__mro__:
        if klass in STATUS_MAP:
            return STATUS_MAP[klass]
    return 500


def _request_id_of(request: Request) -> str | None:
    """Prefer request state over the contextvar, and here is why.

    Starlette's ``ServerErrorMiddleware`` — the thing that invokes our catch-all
    ``Exception`` handler — sits OUTSIDE all user middleware. By the time it runs,
    ``RequestContextMiddleware`` has already executed its ``finally`` block and
    cleared the context, so ``get_request_id()`` returns ``None``.

    That would strip the request id from exactly the responses that need it most:
    unexpected 500s. ``request.state`` survives, because it lives on the request
    object rather than in task-local storage.
    """
    return getattr(request.state, "request_id", None) or get_request_id()


def _envelope(
    request: Request,
    *,
    status: int,
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
) -> JSONResponse:
    request_id = _request_id_of(request)
    body = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=request_id,
            details=details,
        )
    )
    # The header is set here, not only in the middleware. On an unhandled exception
    # the response is produced by ServerErrorMiddleware, which sits outside our
    # middleware — so RequestContextMiddleware never gets to add the header, and it
    # would be missing from exactly the 500s where support most needs it.
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


async def handle_medreport_error(request: Request, exc: Exception) -> JSONResponse:
    """Our own typed errors."""
    # Registered for this exact type, so the cast is safe. `cast` rather than
    # `assert`: asserts vanish under `python -O`, and type narrowing that
    # disappears in production is not type narrowing.
    error = cast("MedReportError", exc)
    status = status_for(error)

    # Level follows fault: their problem is a warning, our problem is an error.
    # If every failure logged at ERROR, the ERROR channel would be worthless.
    event = "request_rejected" if status < 500 else "request_error"
    logger = log.warning if status < 500 else log.error
    logger(
        event,
        code=error.code,
        status=status,
        retryable=error.retryable,
        # `context` holds identifiers; the redaction processor scrubs anything
        # sensitive before this reaches a log sink.
        error_context=error.context,
        exc_info=status >= 500,
    )

    return _envelope(
        request,
        status=status,
        code=error.code,
        message=PUBLIC_MESSAGES.get(error.code, _GENERIC_MESSAGE),
    )


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Pydantic rejected the request body, query or path.

    Field-level detail is safe to return here — it describes the request the caller
    just sent us, so it tells them nothing they did not already know.
    """
    validation = cast("RequestValidationError", exc)
    details = [
        {
            "field": ".".join(str(part) for part in error["loc"][1:]) or "body",
            "problem": error["msg"],
        }
        for error in validation.errors()
    ]
    log.warning("request_invalid", code="validation_error", fields=[d["field"] for d in details])

    return _envelope(
        request,
        status=422,
        code="validation_error",
        message="The request was not valid.",
        details=details,
    )


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Starlette's own errors — unknown route, method not allowed.

    Handled so that a 404 for a bad URL comes back in the same envelope as every
    other failure. Clients should never need two error parsers.
    """
    http_error = cast("StarletteHTTPException", exc)
    codes = {404: "not_found", 405: "method_not_allowed", 401: "unauthenticated"}
    return _envelope(
        request,
        status=http_error.status_code,
        code=codes.get(http_error.status_code, "http_error"),
        message=str(http_error.detail),
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Anything we did not anticipate. A bug.

    Two rules, both non-negotiable:

    1. **Log everything** — full traceback, against the request id.
    2. **Return nothing** — a generic message and that id. A stack trace or an
       exception message in a response body leaks table names, file paths and
       library versions to anyone who can trigger the error.

    The user gets one string to quote to support. An attacker gets one string.
    """
    # Bind explicitly: the context was cleared before this handler was reached.
    log.exception(
        "unhandled_exception",
        error_type=type(exc).__name__,
        request_id=_request_id_of(request),
    )

    return _envelope(request, status=500, code="internal_error", message=_GENERIC_MESSAGE)


def register_error_handlers(app: FastAPI) -> None:
    """Wire the handlers. Called once, from the app factory."""
    app.add_exception_handler(MedReportError, handle_medreport_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)
