"""The first node: fetch the document and work out how many pages it has.

Deliberately does NOT put page images in state. A 25-page report is around 12MB
of PNG, and state is checkpointed to Postgres after every node — carrying the
images would write hundreds of megabytes per report for no benefit. Pages are
re-rendered from object storage when a node needs them: cheap, deterministic,
and byte-identical every time.
"""

from app.core.logging import get_logger
from app.domain.errors import UnreadableDocumentError
from app.pipeline.context import PipelineContext
from app.pipeline.ingest import render_pages
from app.pipeline.registry import register_node
from app.pipeline.state import GraphState

log = get_logger(__name__)


@register_node("ingest")
async def ingest(state: GraphState) -> dict[str, object]:
    context = PipelineContext.current()

    data = await context.storage.get(state["storage_key"])
    suffix = _suffix_for(state.get("content_type", ""))

    pages = render_pages(data, suffix)
    if not pages:
        raise UnreadableDocumentError(reason="no_pages")

    log.info(
        "ingest_complete",
        pages=len(pages),
        with_text_layer=sum(1 for p in pages if p.has_text_layer),
    )

    return {
        "page_count": len(pages),
        "pages_meta": [
            {
                "number": page.number,
                "has_text_layer": page.has_text_layer,
                "width": page.width,
                "height": page.height,
            }
            for page in pages
        ],
    }


def _suffix_for(content_type: str) -> str:
    return {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/heic": ".heic",
        "image/heif": ".heic",
    }.get(content_type.split(";")[0].strip().lower(), ".pdf")
