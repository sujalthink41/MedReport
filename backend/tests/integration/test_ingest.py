"""Rendering real documents.

Skipped unless a report is present in tests/golden/reports/ - the suite must stay
green on a machine with no health data on it.
"""

import pytest

from app.domain.errors import UnreadableDocumentError
from app.pipeline.ingest import TARGET_LONG_EDGE_PX, render_pages
from tests.golden.loader import REPORTS_DIR

pytestmark = pytest.mark.integration


def _any_pdf():  # type: ignore[no-untyped-def]
    return next(iter(sorted(REPORTS_DIR.glob("*.pdf"))), None)


class TestRendering:
    @pytest.mark.skipif(_any_pdf() is None, reason="no golden PDF present")
    def test_a_real_report_renders_to_pages(self) -> None:
        pages = render_pages(_any_pdf().read_bytes(), ".pdf")  # type: ignore[union-attr]

        assert len(pages) >= 1
        for page in pages:
            assert page.image.startswith(b"\x89PNG")
            assert max(page.width, page.height) <= TARGET_LONG_EDGE_PX
            # Lab report text is small; too few pixels and digits become
            # unreadable regardless of which model reads them.
            assert max(page.width, page.height) >= 1000

    @pytest.mark.skipif(_any_pdf() is None, reason="no golden PDF present")
    def test_pages_are_numbered_from_one(self) -> None:
        pages = render_pages(_any_pdf().read_bytes(), ".pdf")  # type: ignore[union-attr]

        assert [p.number for p in pages] == list(range(1, len(pages) + 1))

    def test_a_png_is_treated_as_one_page(self) -> None:
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (3000, 4000), "white").save(buffer, format="PNG")

        pages = render_pages(buffer.getvalue(), ".png")

        assert len(pages) == 1
        # A phone photo has no text layer, and its absence is information: every
        # digit came from pixels.
        assert pages[0].has_text_layer is False
        # Downscaled - a 12MP photo is mostly wasted tokens.
        assert max(pages[0].width, pages[0].height) == TARGET_LONG_EDGE_PX

    def test_a_small_image_is_not_upscaled(self) -> None:
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (800, 600), "white").save(buffer, format="PNG")

        pages = render_pages(buffer.getvalue(), ".png")

        # Upscaling invents pixels without adding information, and costs tokens
        # for the privilege.
        assert pages[0].width == 800


class TestFailures:
    def test_a_corrupt_pdf_fails_honestly(self) -> None:
        with pytest.raises(UnreadableDocumentError):
            render_pages(b"%PDF-1.4 this is not actually a pdf", ".pdf")

    def test_a_corrupt_image_fails_honestly(self) -> None:
        with pytest.raises(UnreadableDocumentError):
            render_pages(b"\x89PNG\r\n\x1a\n garbage", ".png")
