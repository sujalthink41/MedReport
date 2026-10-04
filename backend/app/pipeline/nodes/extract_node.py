"""Reading every page, in parallel.

The expensive node. A 25-page report is 25 vision calls, and running them
sequentially would make a full-body checkup take five minutes instead of twenty
seconds.

Pages extract **independently** — no shared context between them. The cost of
that independence is duplicate rows where a lab repeats a header on every page,
which the merge node resolves deterministically. The benefit is that one slow or
failing page cannot hold up the other twenty-four.
"""

import asyncio

from app.adapters.llm.prompts import extraction_prompt
from app.adapters.llm.schemas import ExtractedPage
from app.core.logging import get_logger
from app.domain.errors import MedReportError
from app.domain.ports.llm import Purpose
from app.pipeline.context import PipelineContext
from app.pipeline.ingest import render_pages
from app.pipeline.nodes.ingest_node import _suffix_for
from app.pipeline.registry import register_node
from app.pipeline.state import GraphState

log = get_logger(__name__)

MAX_CONCURRENT_PAGES = 6
"""How many pages may be in flight at once.

Not unbounded. Twenty-five simultaneous vision calls will hit a provider rate
limit, and the retry storm that follows takes longer than the work would have.
Six keeps a 25-page report under a minute while staying inside normal limits.
"""


@register_node("extract", after="ingest", fan_out=True)
async def extract(state: GraphState) -> dict[str, object]:
    context = PipelineContext.current()

    data = await context.storage.get(state["storage_key"])
    pages = render_pages(data, _suffix_for(state.get("content_type", "")))

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
    results = await asyncio.gather(
        *[_extract_one(page, semaphore) for page in pages],
        # Gather the failures rather than cancelling the siblings. One unreadable
        # page in a 25-page report must not discard the other twenty-four.
        return_exceptions=True,
    )

    extracted: dict[int, ExtractedPage] = {}
    failed: list[int] = []
    errors: list[str] = []
    cost = 0.0

    for page, outcome in zip(pages, results, strict=True):
        if isinstance(outcome, BaseException):
            failed.append(page.number)
            errors.append(f"page {page.number}: {type(outcome).__name__}")
            log.warning("page_extraction_failed", page=page.number)
            continue
        value, page_cost = outcome
        extracted[page.number] = value
        cost += page_cost

    log.info(
        "extract_complete",
        pages_read=len(extracted),
        pages_failed=len(failed),
        rows=sum(len(p.rows) for p in extracted.values()),
        cost_usd=round(cost, 4),
    )

    return {
        "pages": extracted,
        "failed_pages": failed,
        "errors": errors,
        "cost_usd": cost,
        "lab_name": _first(extracted, "lab_name"),
        "collected_at": _first(extracted, "collected_at"),
    }


async def _extract_one(page: object, semaphore: asyncio.Semaphore) -> tuple[ExtractedPage, float]:
    context = PipelineContext.current()
    async with semaphore:
        result = await context.llm.complete(
            prompt=extraction_prompt(
                page_image=page.image,  # type: ignore[attr-defined]
                text_layer=page.text_layer,  # type: ignore[attr-defined]
                media_type=page.media_type,  # type: ignore[attr-defined]
            ),
            schema=ExtractedPage,
            purpose=Purpose.EXTRACT,
        )
        return result.value, float(result.usage.cost_usd)


def _first(pages: dict[int, ExtractedPage], field: str) -> str | None:
    """The first non-empty value of a report-level field across all pages.

    Labs print the collection date and their own name on every page, but often
    only legibly on some. Taking the first that reads cleanly is better than
    trusting page one, which is as likely as any other to be the cropped one.
    """
    for number in sorted(pages):
        value = getattr(pages[number], field, None)
        if value:
            return str(value)
    return None


__all__ = ["MedReportError", "extract"]
