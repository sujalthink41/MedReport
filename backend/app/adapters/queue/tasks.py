"""Celery tasks.

Every task here is a thin shell: open a transaction, call application code, commit.
The real work lives in ``application/`` and ``pipeline/`` so it stays testable
without a broker.

The import graph matters. This module imports worker code; the API process must
never import it, which is why ``CeleryTaskQueue`` dispatches by task *name*.
"""

from uuid import UUID

from app.adapters.db.session import create_engine, create_session_factory
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.queue.celery_app import async_task, celery_app, task_context
from app.core.config import get_settings
from app.core.logging import bind_context, get_logger
from app.domain.errors import ReportNotFoundError
from app.domain.models.enums import ReportStatus
from app.domain.models.identifiers import ReportId

log = get_logger(__name__)


@celery_app.task(
    name="process_report",
    bind=True,
    max_retries=3,
    # Exponential backoff with jitter, for the same reason it exists everywhere
    # else: without jitter every worker retries at the same instant and rebuilds
    # the thundering herd that caused the outage.
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
@task_context("process_report", report_id="report_id")
@async_task
async def process_report(self: object, report_id: str) -> str:  # noqa: ARG001
    """Run a report through the pipeline.

    A stub until CP19 builds the graph: it moves the report QUEUED -> PROCESSING
    so the whole path is wired and observable end to end. Having the real plumbing
    working before the interesting part means a failure later is unambiguous - it
    is the pipeline, not the queue.

    The engine is created HERE, inside the task's event loop. An async engine binds
    to the loop that built it, so a module-level one would raise "attached to a
    different loop" on the second task.
    """
    settings = get_settings()
    bind_context(report_id=report_id)

    engine = create_engine(settings)
    try:
        factory = create_session_factory(engine)

        async with SqlUnitOfWork(factory) as uow:
            report = await uow.reports.get(ReportId(UUID(report_id)))
            if report is None:
                # Should not happen now that dispatch waits for commit, but a
                # replayed or hand-fired message can still arrive for a deleted
                # report. Raising would retry three times and then alert; this is
                # a legitimate outcome, so log it and stop.
                log.warning("report_gone", report_id=report_id)
                raise ReportNotFoundError(report_id=report_id)

            if report.is_terminal:
                # Late acknowledgement means a message can be redelivered after the
                # work finished. Idempotence, not a guard against a bug.
                log.info("report_already_finished", status=report.status.value)
                return report.status.value

            if report.can_transition_to(ReportStatus.PROCESSING):
                await uow.reports.update(report.with_status(ReportStatus.PROCESSING))

            await uow.commit()
            return ReportStatus.PROCESSING.value
    finally:
        # Dispose explicitly. Without it the pool's connections outlive the event
        # loop, and Postgres accumulates idle connections one per completed task
        # until it refuses new ones.
        await engine.dispose()
