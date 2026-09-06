"""HTTP middleware.

Two middlewares, each with one job — see the ordering note in ``main.py``, which is
the part people get wrong.

Implementation note: these use ``BaseHTTPMiddleware``, which is readable and correct
for our use. Its known limitations involve streaming responses and background tasks;
if we ever stream a large report export, the request-context middleware should be
rewritten as pure ASGI (``async def __call__(self, scope, receive, send)``). Noted
here so the decision is deliberate rather than discovered.
"""

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import REQUEST_ID_HEADER, reset_request_id, set_request_id
from app.core.logging import clear_context, get_logger

log = get_logger(__name__)

# Health probes fire every few seconds. Logging them buries real traffic and costs
# money in any hosted log product.
_UNLOGGED_PATHS = frozenset({"/api/v1/health", "/api/v1/ready"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Establish the request id, bind it for logging, and echo it to the client.

    An inbound ``X-Request-ID`` is honoured rather than replaced. That is what lets a
    trace survive across services: a gateway or the frontend generates the id once,
    and every downstream system reports under the same one.

    The id is returned in the response header so that when a user says "it showed my
    haemoglobin wrong", they can hand you the one string that finds every log line
    for that request.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        set_request_id(request_id)
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        finally:
            # Must run even when the request explodes. Workers and event loops are
            # reused, so a leaked context puts this request's id on the next
            # request's logs -- worse than no correlation, because it is believable.
            reset_request_id()
            clear_context()

        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One structured line per request, with its duration.

    Separate from ``RequestContextMiddleware`` because they change for different
    reasons: one is about correlation, the other about observability. Splitting them
    also means access logging can be turned off without losing request ids.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in _UNLOGGED_PATHS:
            return await call_next(request)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            # No traceback here on purpose. The exception handler logs the full
            # trace against the same request_id; duplicating it would put two
            # multi-screen tracebacks in the log for one failure, and cost real
            # money in any hosted log product. This line contributes only what the
            # handler cannot see: the path and how long it took to fail.
            log.error(  # noqa: TRY400 - traceback is logged once, by the handler
                "request_failed",
                method=request.method,
                path=request.url.path,
                error_type=type(exc).__name__,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            # Re-raise: swallowing here would break error handling entirely.
            raise

        log.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response
