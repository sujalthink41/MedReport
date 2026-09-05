"""Structured logging.

Logs are **data**, not prose:

    log.info("observation_classified", test="hba1c", band="borderline")   # a record
    log.info(f"Classified hba1c as borderline")                           # a sentence

The first is queryable — ``band=out_of_range AND ref_source=fallback`` answers a real
question in one filter. The second can only be grepped with a regex you invent later.

Three things this module sets up:

1. **One output shape for everything**, including uvicorn's own logs. Third-party
   libraries use stdlib ``logging``; we route those through the same processor chain
   so a log aggregator never sees two formats.
2. **Correlation via contextvars.** Bind ``request_id`` once at the edge and every
   log line in that request carries it automatically — including lines written deep
   inside code that has never heard of a request.
3. **PHI redaction as a processor.** Enforced by the pipeline, not by everyone
   remembering. See ``redact_sensitive``.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from structlog.typing import EventDict, Processor, WrappedLogger

    from app.core.config import Settings

REDACTED = "[redacted]"

# Keys whose VALUES must never reach a log line or an error tracker.
#
# This is a denylist, which is the weaker kind of defence — an unknown key passes
# through. It is chosen deliberately: an allowlist would silently swallow the
# diagnostic fields we actually need. The mitigation is that this list is reviewed
# whenever a new field is introduced, and that we log identifiers by convention.
#
# Actual prompt and response bodies live in the `llm_traces` table (CP16), which is
# on our own infrastructure and access-controlled — not in application logs.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        # lab data
        "value",
        "values",
        "raw_value",
        "raw_value_text",
        "observation",
        "observations",
        "ref_text",
        "result",
        "results",
        # identity
        "email",
        "name",
        "display_name",
        "full_name",
        "patient_name",
        "date_of_birth",
        "dob",
        "phone",
        "address",
        # model traffic
        "prompt",
        "response",
        "completion",
        "content",
        "text",
        "message_body",
        # secrets
        "password",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "secret",
        "authorization",
    }
)

_MAX_DEPTH = 4


def _scrub(value: object, depth: int = 0) -> object:
    """Walk nested structures so PHI cannot hide one level down.

    Depth-capped: a deeply nested or self-referential structure must not turn a log
    call into a hang. Logging is never allowed to be the thing that breaks a request.
    """
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {
            key: (REDACTED if _is_sensitive(key) else _scrub(inner, depth + 1))
            for key, inner in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item, depth + 1) for item in value]
    return value


def _is_sensitive(key: object) -> bool:
    return isinstance(key, str) and key.lower() in SENSITIVE_KEYS


def redact_sensitive(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor: replace sensitive values before anything is rendered.

    Sits in the chain *before* the renderer, so redaction applies to console output,
    JSON output, and any future sink identically. Putting this in one place is the
    whole reason it can be trusted.
    """
    return {
        key: (REDACTED if _is_sensitive(key) else _scrub(value))
        for key, value in event_dict.items()
    }


def configure_logging(settings: Settings) -> None:
    """Configure structlog and the stdlib root logger to share one output shape.

    Call once, at startup, before anything logs.
    """
    shared_processors: list[Processor] = [
        # Must come first: injects request_id, report_id and anything else bound
        # for the current task into every event.
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        # Redaction sits last in the shared chain, so it also scrubs fields added
        # by the processors above.
        redact_sensitive,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.use_json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Applied to records from libraries that never heard of structlog —
        # uvicorn, sqlalchemy, celery. This is what gives us one format everywhere.
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    # Uvicorn installs its own handlers. Strip them and let records propagate to the
    # root handler above, or every request produces two differently-shaped lines.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(noisy)
        logger.handlers = []
        logger.propagate = True


def get_logger(name: str | None = None) -> Any:
    """Module-level logger. Use as ``log = get_logger(__name__)``."""
    return structlog.get_logger(name)


def bind_context(**values: object) -> None:
    """Bind values onto every subsequent log line in this task.

    Bound at the edge (CP3 middleware binds ``request_id``; the worker binds
    ``report_id`` and ``node``). Uses contextvars, so it is safe across concurrent
    async requests — each task gets its own copy rather than sharing global state.
    """
    structlog.contextvars.bind_contextvars(**values)


def clear_context() -> None:
    """Clear bound context. Must run at the end of every request or task.

    Workers reuse threads and event loops; without this, one report's ``report_id``
    would leak into the next one's logs.
    """
    structlog.contextvars.clear_contextvars()
