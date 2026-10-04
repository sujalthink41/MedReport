"""TaskQueue adapters.

Two of them, and the second exists to fix a race that bites every system built
this way.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

log = get_logger(__name__)

PENDING_TASKS_KEY = "pending_tasks"


class CeleryTaskQueue:
    """Dispatches immediately. Used by the worker and by anything with no transaction."""

    def __init__(self, celery_app: Any) -> None:
        self._celery = celery_app

    async def enqueue(self, task: str, **kwargs: str) -> None:
        # send_task by NAME rather than importing the task function. The API process
        # must not import worker code - it would drag LangGraph, the LLM client and
        # pypdfium2 into every web container for no reason.
        self._celery.send_task(task, kwargs=kwargs)
        log.info("task_enqueued", task=task, **kwargs)


class TransactionalTaskQueue:
    """Buffers tasks until the database transaction commits.

    The race this closes is the classic dual-write problem, and it is not
    theoretical - it fires under normal load.

        upload handler:  INSERT report (uncommitted)
                         enqueue process_report      <- message is live NOW
        worker:          SELECT report WHERE id=...  <- nothing there yet
                         crash, or mark it failed

    Redis is faster than a Postgres commit, so the worker can genuinely win that
    race. Buffering here and flushing after commit means a task is only ever
    dispatched for a row that is already visible to everyone.

    The buffer lives on ``session.info`` - SQLAlchemy's own per-session scratch
    space - so the queue and the session stay decoupled while still sharing a
    lifetime.
    """

    def __init__(self, session: AsyncSession, dispatcher: CeleryTaskQueue) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._session.info.setdefault(PENDING_TASKS_KEY, [])
        self._session.info["task_dispatcher"] = dispatcher

    async def enqueue(self, task: str, **kwargs: str) -> None:
        pending: list[tuple[str, dict[str, str]]] = self._session.info[PENDING_TASKS_KEY]
        pending.append((task, kwargs))
        log.debug("task_buffered", task=task, **kwargs)


async def dispatch_pending(session: AsyncSession) -> None:
    """Send everything buffered during a committed transaction.

    Called by ``session_scope`` AFTER the commit. If the transaction rolled back,
    the buffer is simply discarded along with the session - no task is sent for
    work that never happened.

    A failure here is logged but not re-raised: the data is safely committed, and
    turning a successful upload into a 500 because Redis blinked would be the wrong
    trade. The report sits in QUEUED and a sweeper (CP26) picks it up.
    """
    pending: list[tuple[str, dict[str, str]]] = session.info.get(PENDING_TASKS_KEY, [])
    if not pending:
        return

    dispatcher = session.info.get("task_dispatcher")
    session.info[PENDING_TASKS_KEY] = []

    for task, kwargs in pending:
        try:
            await dispatcher.enqueue(task, **kwargs)
        except Exception:
            log.exception("task_dispatch_failed", task=task, **kwargs)
