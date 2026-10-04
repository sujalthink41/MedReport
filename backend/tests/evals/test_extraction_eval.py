"""Extraction scored against the golden set.

Not pass/fail. Run it, read the numbers, decide whether a prompt change helped.

    make eval

Skipped entirely without an API key or without reports, so the ordinary suite
stays fast, free and offline.
"""

import os

import pytest

from tests.golden.loader import load_all
from tests.golden.scoring import combine, report, score_page

pytestmark = pytest.mark.eval


@pytest.mark.skipif(not os.getenv("MEDREPORT_OPENAI_API_KEY"), reason="no API key configured")
async def test_score_the_golden_set(capsys: pytest.CaptureFixture[str]) -> None:
    reports = load_all()
    if not reports:
        pytest.skip(
            "no golden reports - add real lab reports to tests/golden/reports/ "
            "and matching truth files to tests/golden/truth/. See the README there."
        )

    from app.adapters.llm.client import LiteLLMClient
    from app.adapters.llm.prompts import extraction_prompt
    from app.adapters.llm.resilience import RepairingLLM, RetryingLLM
    from app.adapters.llm.router import ModelRouter
    from app.adapters.llm.schemas import ExtractedPage
    from app.core.config import get_settings
    from app.domain.ports.llm import Purpose
    from app.pipeline.ingest import render_pages

    settings = get_settings()
    # No caching layer, deliberately: a re-run must genuinely re-query, or the
    # second run of a changed prompt would score the first one's answers.
    llm = RetryingLLM(RepairingLLM(LiteLLMClient(ModelRouter(settings), settings.openai_api_key)))

    cards = []
    for golden in reports:
        pages = render_pages(golden.path.read_bytes(), golden.path.suffix)
        for expected_page in golden.pages:
            page = next((p for p in pages if p.number == expected_page.page), None)
            if page is None:
                continue
            result = await llm.complete(
                prompt=extraction_prompt(page_image=page.image, text_layer=page.text_layer),
                schema=ExtractedPage,
                purpose=Purpose.EXTRACT,
            )
            cards.append(score_page(expected_page.rows, result.value.rows))

    total = combine(cards)
    with capsys.disabled():
        print(report(total))

    # One hard assertion only: the harness ran. Everything else is read, not
    # enforced - a threshold here would turn a measurement into a gate, and the
    # first thing anyone does with a failing gate is lower it.
    assert total.expected_rows > 0
