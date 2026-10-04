"""The dual-write race, and the fix.

Needs a real session because the whole point is the interaction between a
transaction and a message, which an in-memory fake cannot reproduce.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.queue.celery_queue import (
    PENDING_TASKS_KEY,
    TransactionalTaskQueue,
    dispatch_pending,
)

pytestmark = pytest.mark.integration


class SpyDispatcher:
    def __init__(self, *, fails: bool = False) -> None:
        self.sent: list[tuple[str, dict[str, str]]] = []
        self.fails = fails

    async def enqueue(self, task: str, **kwargs: str) -> None:
        if self.fails:
            raise ConnectionError("redis is down")
        self.sent.append((task, kwargs))


class TestBuffering:
    async def test_enqueue_does_not_dispatch_immediately(self, session: AsyncSession) -> None:
        spy = SpyDispatcher()
        queue = TransactionalTaskQueue(session, spy)  # type: ignore[arg-type]

        await queue.enqueue("process_report", report_id="r-1")

        # The race this closes: Redis is faster than a Postgres commit, so a worker
        # can read a row that is not committed yet and either crash or mark the
        # report failed.
        assert spy.sent == []
        assert session.info[PENDING_TASKS_KEY] == [("process_report", {"report_id": "r-1"})]

    async def test_dispatch_sends_everything_buffered(self, session: AsyncSession) -> None:
        spy = SpyDispatcher()
        queue = TransactionalTaskQueue(session, spy)  # type: ignore[arg-type]
        await queue.enqueue("process_report", report_id="r-1")
        await queue.enqueue("process_report", report_id="r-2")

        await dispatch_pending(session)

        assert spy.sent == [
            ("process_report", {"report_id": "r-1"}),
            ("process_report", {"report_id": "r-2"}),
        ]

    async def test_the_buffer_is_emptied_so_a_task_is_sent_once(
        self, session: AsyncSession
    ) -> None:
        spy = SpyDispatcher()
        queue = TransactionalTaskQueue(session, spy)  # type: ignore[arg-type]
        await queue.enqueue("process_report", report_id="r-1")

        await dispatch_pending(session)
        await dispatch_pending(session)

        # Double dispatch would run the pipeline twice and pay for the same vision
        # calls twice.
        assert len(spy.sent) == 1

    async def test_dispatching_nothing_is_fine(self, session: AsyncSession) -> None:
        spy = SpyDispatcher()
        TransactionalTaskQueue(session, spy)  # type: ignore[arg-type]

        await dispatch_pending(session)

        assert spy.sent == []


class TestFailureHandling:
    async def test_a_broker_outage_does_not_fail_the_request(self, session: AsyncSession) -> None:
        queue = TransactionalTaskQueue(session, SpyDispatcher(fails=True))  # type: ignore[arg-type]
        await queue.enqueue("process_report", report_id="r-1")

        # Must not raise. The data is already committed; turning a successful
        # upload into a 500 because Redis blinked would lose the user's work for
        # no reason. The report sits in QUEUED and a sweeper picks it up.
        await dispatch_pending(session)

    async def test_one_failure_does_not_stop_the_rest(self, session: AsyncSession) -> None:
        class FlakyDispatcher(SpyDispatcher):
            async def enqueue(self, task: str, **kwargs: str) -> None:
                if kwargs.get("report_id") == "r-1":
                    raise ConnectionError("transient")
                self.sent.append((task, kwargs))

        spy = FlakyDispatcher()
        queue = TransactionalTaskQueue(session, spy)  # type: ignore[arg-type]
        await queue.enqueue("process_report", report_id="r-1")
        await queue.enqueue("process_report", report_id="r-2")

        await dispatch_pending(session)

        assert spy.sent == [("process_report", {"report_id": "r-2"})]
