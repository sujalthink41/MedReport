"""Recording every model call.

A **decorator** over ``LLMClient``, not code inside the client. Retry, caching,
tracing and cost accounting are four separate concerns, and writing them into one
function makes each untestable without the others. Composed instead::

    llm = TracedLLM(RetryingLLM(CachingLLM(LiteLLMClient(...))), traces)

Each wrapper implements the same port and delegates inward, so any one can be
dropped without touching the rest.
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import LlmTraceRow
from app.core.context import get_request_id
from app.core.logging import get_logger
from app.domain.errors import MedReportError
from app.domain.ports.llm import LLMClient, LLMResult, Prompt, Purpose

log = get_logger(__name__)


class TraceWriter(Protocol):
    async def record(
        self,
        *,
        report_id: UUID | None,
        purpose: str,
        model: str,
        page: int | None,
        prompt: str,
        response: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: object,
        latency_ms: int,
        status: str,
        error: str | None,
        cached: bool,
        request_id: str | None,
        at: datetime | None = None,
    ) -> None: ...


class SqlTraceWriter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, **fields: object) -> None:
        self._session.add(LlmTraceRow(id=uuid4(), **fields))
        await self._session.flush()


class TracedLLM:
    """Writes a row for every call — including the ones that fail.

    Failures matter more than successes here. A successful extraction rarely needs
    investigating; the row that explains a wrong haemoglobin is almost always one
    that errored, timed out, or returned something unparseable.
    """

    def __init__(
        self,
        inner: LLMClient,
        traces: TraceWriter,
        *,
        report_id: UUID | None = None,
        page: int | None = None,
    ) -> None:
        self._inner = inner
        self._traces = traces
        self._report_id = report_id
        self._page = page

    async def complete[T](
        self, *, prompt: Prompt, schema: type[T], purpose: Purpose
    ) -> LLMResult[T]:
        try:
            result = await self._inner.complete(prompt=prompt, schema=schema, purpose=purpose)
        except MedReportError as exc:
            await self._write(prompt, purpose, None, status="error", error=str(exc))
            raise
        else:
            await self._write(prompt, purpose, result, status="ok", error=None)
            return result

    async def _write(
        self,
        prompt: Prompt,
        purpose: Purpose,
        result: LLMResult[object] | None,
        *,
        status: str,
        error: str | None,
    ) -> None:
        usage = result.usage if result else None
        try:
            await self._traces.record(
                report_id=self._report_id,
                purpose=purpose.value,
                model=usage.model if usage else "unknown",
                page=self._page,
                # The full prompt, stored here and nowhere else. The application
                # logs carry identifiers only; this table is the single
                # access-controlled place health data in a prompt can live.
                prompt=_describe(prompt),
                response=result.raw if result else None,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                cost_usd=usage.cost_usd if usage else 0,
                latency_ms=usage.latency_ms if usage else 0,
                status=status,
                error=error,
                cached=usage.cached if usage else False,
                request_id=get_request_id(),
            )
        except Exception:
            # Tracing must never be the thing that fails a report. Losing the
            # audit of one call is bad; losing a user's extracted results because
            # the trace insert hit a constraint is worse.
            log.exception("trace_write_failed", purpose=purpose.value)


def _describe(prompt: Prompt) -> str:
    """Prompt text plus a note of any images.

    Image bytes are deliberately not stored. A 25-page report would put tens of
    megabytes of base64 into Postgres per run, and the original document is
    already in object storage - re-rendering a page is cheap and the bytes are
    identical.
    """
    body = f"[system]\n{prompt.system}\n\n[user]\n{prompt.user}"
    if prompt.images:
        total = sum(len(i.data) for i in prompt.images)
        body += f"\n\n[images] {len(prompt.images)} attached, {total} bytes (not stored)"
    return body
