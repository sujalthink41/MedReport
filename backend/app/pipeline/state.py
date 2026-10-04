"""The graph's state.

Everything the pipeline knows about one report, carried from node to node and
checkpointed after each.

Two rules shape what goes in here.

**It must survive serialisation.** Checkpoints are written to Postgres between
nodes so a failure resumes at the failed step rather than re-reading 25 pages
through a vision model. A database session or an open file handle cannot be
written to a checkpoint, so nothing like that belongs in state — nodes fetch what
they need from an id.

**Page results must merge, not overwrite.** Pages extract in parallel and each
returns a slice of the whole. A plain dict field would have the last page to
finish silently replace the others; the reducer below is what makes fan-out safe.
"""

import operator
from typing import Annotated, Any, TypedDict

from app.adapters.llm.schemas import ExtractedPage


def merge_pages(
    left: dict[int, ExtractedPage], right: dict[int, ExtractedPage]
) -> dict[int, ExtractedPage]:
    """Combine results from parallel page extractions.

    LangGraph calls this whenever two branches write the same key. Without it,
    twenty-five pages racing to write ``pages`` would leave exactly one survivor -
    and the report would look like it had one page of results.

    Keyed by page number, so a re-run of page 7 replaces page 7 and leaves the
    rest alone. That is the same idempotency the database enforces, expressed in
    memory.
    """
    return {**left, **right}


class GraphState(TypedDict, total=False):
    """Carried through the whole pipeline.

    ``total=False`` because nodes fill it progressively: ``ingest`` adds pages,
    ``extract`` adds rows, ``classify`` adds bands. A node reads what it needs and
    writes only what it produced.
    """

    # --- set at the start, never changed -----------------------------------
    report_id: str
    profile_id: str
    storage_key: str
    content_type: str

    # --- ingest -------------------------------------------------------------
    page_count: int
    pages_meta: list[dict[str, Any]]
    """Per-page number, size and whether a text layer exists.

    The images themselves are deliberately NOT in state. A 25-page report is
    ~12MB of PNG, and checkpointing that after every node would write hundreds of
    megabytes per report to Postgres. Pages are re-rendered from object storage
    when a node needs them - cheap, deterministic, and identical every time.
    """

    # --- extract (fan-out) --------------------------------------------------
    pages: Annotated[dict[int, ExtractedPage], merge_pages]
    failed_pages: Annotated[list[int], operator.add]
    """Pages that could not be read after retries.

    A list rather than a flag: a 25-page report where page 7 failed is still 24
    pages of results. Recording which ones failed is what makes `partial` an
    honest status instead of a vague one.
    """

    # --- merge / normalize / classify ---------------------------------------
    observation_count: int
    unmapped_names: Annotated[list[str], operator.add]
    lab_name: str | None
    collected_at: str | None

    # --- bookkeeping --------------------------------------------------------
    cost_usd: Annotated[float, operator.add]
    """Summed across every model call, so cost per report is a number we have
    rather than one we estimate."""

    errors: Annotated[list[str], operator.add]


def initial_state(
    *, report_id: str, profile_id: str, storage_key: str, content_type: str
) -> GraphState:
    return GraphState(
        report_id=report_id,
        profile_id=profile_id,
        storage_key=storage_key,
        content_type=content_type,
        pages={},
        failed_pages=[],
        unmapped_names=[],
        cost_usd=0.0,
        errors=[],
    )
