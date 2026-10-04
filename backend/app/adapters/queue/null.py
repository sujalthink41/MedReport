"""A queue that records instead of dispatching.

Used until CP12 wires Celery, and in tests. Keeping a real implementation of the
port from the start means the upload path is complete and testable now, rather
than carrying a TODO that someone has to remember to replace.
"""

from app.core.logging import get_logger

log = get_logger(__name__)


class NullTaskQueue:
    """Logs the task and drops it.

    Deliberately visible in the logs rather than silent, so "my report is stuck in
    queued" is one grep away from an obvious answer.
    """

    def __init__(self) -> None:
        self.dispatched: list[tuple[str, dict[str, str]]] = []

    async def enqueue(self, task: str, **kwargs: str) -> None:
        self.dispatched.append((task, kwargs))
        log.warning("task_not_dispatched", task=task, reason="no_worker_configured", **kwargs)
