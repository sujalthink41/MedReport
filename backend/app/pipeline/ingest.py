"""Turning an uploaded document into pages a model can read.

``pypdfium2``, not PyMuPDF. PyMuPDF is excellent and **AGPL-3.0** — running AGPL
code as a network service can oblige you to open-source your application, which
is a trap people hit late and expensively. pypdfium2 wraps Google's PDFium (the
engine inside Chrome) under a permissive licence and does the same two jobs.

Both jobs, for every page:

**The text layer**, when the PDF has one — the exact characters the laboratory's
software wrote, with no OCR in between. This is the authority for *digits*. A
vision model can misread 1 as 7 or lose a decimal point, and on a lab value that
is the difference between normal and critical.

**The page image** — the authority for *layout*. A lab report is a table, and
which column a number sits in is what says whether it is a result or a reference
bound. Flatten it to text and that information is gone.

Sending both is the single biggest accuracy win available here.
"""

from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.domain.errors import UnreadableDocumentError

log = get_logger(__name__)

TARGET_LONG_EDGE_PX = 1600
"""Roughly where vision models stop gaining from more pixels.

Lab report text is small, so resolution matters - but every extra pixel is tokens
and money, and providers downsample above a certain size anyway. 1600 on the long
edge is around 200 DPI for A4, which reads a 6pt footnote comfortably.
"""

MAX_PAGES = 60
"""A guard, not a product limit.

A full-body checkup runs 15-25 pages and must work. Sixty is far past any real
report and stops a malicious or corrupt file from costing hundreds of vision
calls.
"""


@dataclass(frozen=True)
class RenderedPage:
    number: int
    image: bytes
    media_type: str
    text_layer: str | None
    """``None`` for a scan or a photo. Its absence is itself information: it means
    every digit came from OCR, so confidence should be read more sceptically."""

    width: int
    height: int

    @property
    def has_text_layer(self) -> bool:
        return bool(self.text_layer and self.text_layer.strip())


def render_pages(data: bytes, suffix: str = ".pdf") -> list[RenderedPage]:
    """Render a document to page images, with text layers where they exist."""
    if suffix.lower() in {".jpg", ".jpeg", ".png", ".heic", ".heif"}:
        return [_single_image(data, suffix)]
    return _render_pdf(data)


def _render_pdf(data: bytes) -> list[RenderedPage]:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        # Corrupt, encrypted, or not a PDF at all. An honest failure the user can
        # act on, rather than a stack trace.
        raise UnreadableDocumentError(reason="could_not_open") from exc

    page_count = len(document)
    if page_count == 0:
        raise UnreadableDocumentError(reason="no_pages")
    if page_count > MAX_PAGES:
        log.warning("document_truncated", pages=page_count, limit=MAX_PAGES)
        page_count = MAX_PAGES

    pages: list[RenderedPage] = []
    try:
        pages = _render_each(document, page_count)
    finally:
        # PDFium holds native memory that Python's garbage collector does not
        # manage. Without this a worker processing reports all day accumulates
        # document handles until it is killed for memory.
        document.close()

    log.info(
        "document_rendered",
        pages=len(pages),
        with_text_layer=sum(1 for p in pages if p.has_text_layer),
    )
    return pages


def _render_each(document: Any, page_count: int) -> list[RenderedPage]:
    pages: list[RenderedPage] = []
    for index in range(page_count):
        page = document[index]
        width, height = page.get_size()
        scale = TARGET_LONG_EDGE_PX / max(width, height) if max(width, height) else 2.0

        image = page.render(scale=scale).to_pil()

        text: str | None = None
        try:
            text_page = page.get_textpage()
            text = text_page.get_text_range() or None
        except Exception:  # noqa: BLE001
            # A page with no extractable text is normal - a scanned page has
            # none. Not an error, just the image-only path.
            text = None

        pages.append(
            RenderedPage(
                number=index + 1,
                image=_to_png(image),
                media_type="image/png",
                text_layer=text,
                width=image.width,
                height=image.height,
            )
        )
    return pages


def _single_image(data: bytes, suffix: str) -> RenderedPage:  # noqa: ARG001
    """A phone photo. One page, no text layer, re-encoded to PNG.

    Normalised to PNG rather than passed through: HEIC is not universally
    supported by providers, and a 12MP phone photo is mostly wasted tokens.
    """
    import io

    from PIL import Image

    try:
        opened = Image.open(io.BytesIO(data))
        opened.load()
    except Exception as exc:
        raise UnreadableDocumentError(reason="could_not_open_image") from exc

    # Rebound to Image rather than ImageFile: convert() and resize() return a
    # plain Image, and keeping one name for two types confuses the checker.
    image: Image.Image = opened
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    long_edge = max(image.width, image.height)
    if long_edge > TARGET_LONG_EDGE_PX:
        ratio = TARGET_LONG_EDGE_PX / long_edge
        image = image.resize(
            (int(image.width * ratio), int(image.height * ratio)),
            Image.Resampling.LANCZOS,
        )

    return RenderedPage(
        number=1,
        image=_to_png(image),
        media_type="image/png",
        text_layer=None,
        width=image.width,
        height=image.height,
    )


def _to_png(image: object) -> bytes:
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)  # type: ignore[attr-defined]
    return buffer.getvalue()
