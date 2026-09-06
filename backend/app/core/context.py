"""Per-request context, carried without passing it through every function signature.

A ``ContextVar`` is the async-safe cousin of a thread-local: each task gets its own
copy, so a thousand concurrent requests never overwrite each other's values. That is
exactly what a global variable would do, and why one is not usable here.

The value this buys: code buried five layers deep can log with correlation, and the
exception handler can put a request id in the response, without either of them taking
a ``request_id`` parameter. Threading that argument through every call would couple
the entire codebase to the fact that HTTP exists.
"""

from contextvars import ContextVar

from app.core.logging import bind_context

# The header both middleware and error handlers use. Lives here because it is
# about request identity, not about HTTP plumbing -- and putting it in
# middleware.py forced error_handlers to import from middleware, which is
# backwards: two peers should share a constant, not depend on each other.
REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(request_id: str) -> None:
    """Set the id for this task and bind it to every subsequent log line."""
    _request_id.set(request_id)
    bind_context(request_id=request_id)


def get_request_id() -> str | None:
    """The current request id, or ``None`` outside a request (worker, CLI, tests)."""
    return _request_id.get()


def reset_request_id() -> None:
    _request_id.set(None)
