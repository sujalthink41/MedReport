"""The Celery application and the async bridge.

Two problems this file exists to solve.

**1. Celery is synchronous; everything we built is async.**

A Celery task is a plain function. Our use cases, repositories and LLM calls are
all ``async def``. The bridge below runs an event loop per task.

**2. One slow report must not block ten quick ones.**

Two queues. A 25-page full-body checkup lands on the slow queue and cannot starve
a user waiting on a two-page thyroid panel.
"""

import asyncio
import functools
from collections.abc import Callable, Coroutine
from typing import Any

from celery import Celery

from app.core.config import Settings, get_settings
from app.core.logging import bind_context, clear_context, configure_logging, get_logger

log = get_logger(__name__)

QUEUE_DEFAULT = "default"
QUEUE_REPORTS = "reports"
"""Report processing: minutes, expensive, vision models.

Separate from `default` so a queue full of 25-page reports does not delay a quick
task. Workers can also be scaled independently - more report workers during the
day, fewer overnight.
"""


def create_celery(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    configure_logging(settings)

    app = Celery("medreport", broker=settings.redis_url, backend=None)
    app.conf.update(
        task_default_queue=QUEUE_DEFAULT,
        task_routes={"process_report": {"queue": QUEUE_REPORTS}},
        # JSON only. Celery's pickle serializer executes arbitrary code on
        # deserialization - anyone who can write to the broker gets remote code
        # execution. JSON cannot do that, and our messages are strings anyway.
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        # Acknowledge AFTER the task finishes, not when it is received. If a worker
        # is killed mid-report, the message returns to the queue and another worker
        # picks it up. Acking early loses the job silently.
        task_acks_late=True,
        # ...which is only safe because every pipeline node is idempotent. Without
        # that, late acking would double-process on every worker restart.
        worker_prefetch_multiplier=1,
        task_reject_on_worker_lost=True,
        # A hard ceiling so a wedged vision call cannot hold a worker forever.
        task_soft_time_limit=settings.task_soft_time_limit_seconds,
        task_time_limit=settings.task_soft_time_limit_seconds + 60,
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = create_celery()


def async_task[T](fn: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., T]:
    """Run an async function inside a synchronous Celery task.

    ``asyncio.run`` creates a fresh event loop per task and tears it down after.
    That matters for SQLAlchemy: an async engine binds to the loop that created it,
    so the engine must be built *inside* this loop rather than shared from module
    scope. A module-level engine would raise "attached to a different loop" on the
    second task.

    Building an engine per task costs one connection handshake. For a job measured
    in tens of seconds that is noise; for a millisecond task it would not be, which
    is another reason the cheap queue stays separate.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def task_context[T](task_name: str, **ids: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Bind logging context for the life of a task, and always clear it.

    Workers reuse processes. Without the clear, one report's id leaks onto the next
    report's log lines - worse than no correlation, because it is believable.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            bind_context(task=task_name, **{k: str(v) for k, v in kwargs.items() if k in ids})
            try:
                log.info("task_started", task=task_name, **kwargs)
                result = fn(*args, **kwargs)
            except Exception:
                log.exception("task_failed", task=task_name)
                raise
            else:
                log.info("task_finished", task=task_name)
                return result
            finally:
                clear_context()

        return wrapper

    return decorator
