"""A page whose content is in the pictures must be OCR'd, even when it carries text.

2026-09-07, Ian: "should we not enable OCR if we need to pull data of a product". OCR was
already enabled — but the only trigger was `len(text) < 50`, which asks whether a page has ANY
words rather than whether its CONTENT is readable.

Measured on gerflor's real SFW gym catalogue: 9 of 10 pages were skipped while covering 67-157%
of their area in images, because each carried a 300-550 character marketing paragraph.

    pg  chars  img%   trigger
     3    476   157%  skipped   'ultra floor ...'
     8    558   138%  skipped   'Recreation 4.5mm - vinyl flooring ...'
     4    433   115%  skipped   'Interlocking floor tiles ...'

The prose was captured; every specification, size and colour range — the part a rep actually
needs — stayed locked in the pictures. The three text-based price lists in the same batch have
no image-heavy pages at all, so they must still cost nothing.
"""
from unittest.mock import MagicMock

from vula.ingestion.pipeline import (
    OCR_IMAGE_COVERAGE, OCR_MAX_PAGES_PER_DOC, DocumentParser,
)


def _page(width=595, height=842, images=()):
    p = MagicMock()
    p.width, p.height = width, height
    p.images = [{"x0": x0, "x1": x1, "top": t, "bottom": b} for x0, x1, t, b in images]
    return p


def test_a_page_covered_in_pictures_is_recognised():
    """SFW gym page 3: one big image over most of an A4 page."""
    page = _page(images=[(0, 595, 0, 700)])
    assert DocumentParser._image_coverage(page) >= OCR_IMAGE_COVERAGE


def test_overlapping_images_do_not_break_the_measure():
    """Real catalogues stack images, so coverage can exceed 100%. That only ever means "this
    page is a picture"."""
    page = _page(images=[(0, 595, 0, 842), (0, 595, 0, 842)])
    assert DocumentParser._image_coverage(page) > 1.0


def test_a_text_page_with_a_logo_is_not_image_heavy():
    """The DT and SPM price lists are text with small marks on them — OCR'ing those would be
    pure cost for nothing."""
    page = _page(images=[(20, 120, 20, 80)])
    assert DocumentParser._image_coverage(page) < OCR_IMAGE_COVERAGE


def test_a_page_with_no_images_scores_zero():
    assert DocumentParser._image_coverage(_page()) == 0.0


def test_a_broken_page_object_does_not_raise():
    """Ingestion must never fail because one page's geometry is unreadable."""
    bad = MagicMock()
    bad.width = "not a number"
    assert DocumentParser._image_coverage(bad) == 0.0


def test_a_zero_area_page_does_not_divide_by_zero():
    assert DocumentParser._image_coverage(_page(width=0, height=0)) == 0.0


def test_the_native_text_is_kept_alongside_the_ocr():
    """The extracted text is real and exact; OCR only adds what was in the picture. Replacing
    it would trade a precise price table for a model's reading of one."""
    import inspect
    src = inspect.getsource(DocumentParser._parse_pdf_native)
    assert 'f"{text}\\n\\n{ocr_text}"' in src
    assert "if bare:" in src, "a page with no text at all still gets OCR alone"


def test_image_heavy_ocr_is_capped_per_document():
    """A 200-page brochure must not quietly run up 200 vision calls."""
    import inspect
    src = inspect.getsource(DocumentParser._parse_pdf_native)
    assert "ocr_budget" in src
    assert OCR_MAX_PAGES_PER_DOC > 0


def test_a_page_with_no_text_ignores_the_budget():
    """A scanned page is unreadable without OCR, so it is never rationed — only the
    already-has-text case is."""
    import inspect
    src = inspect.getsource(DocumentParser._parse_pdf_native)
    i_bare = src.index("bare = ")
    i_heavy = src.index("image_heavy = ")
    assert "ocr_budget > 0" in src[i_heavy:i_heavy + 200]
    assert "ocr_budget" not in src[i_bare:i_bare + 60]


# ── a local model that never answers must stop being asked ──────────────────────

def test_local_ocr_is_abandoned_after_repeated_failure():
    """2026-09-07: re-ingesting the 10-page gym catalogue, local OCR failed on all 7 pages it
    reached and each failure cost the full 90-second timeout before escalating to cloud vision,
    which then read the page correctly every time. Ten minutes spent waiting for a model that
    never answers is worse than not calling it."""
    from vula.ingestion.pipeline import OCRProcessor
    proc = OCRProcessor()
    assert proc._local_ocr_disabled() is False
    proc._local_ocr_failures = OCRProcessor._LOCAL_OCR_FAILURE_LIMIT
    assert proc._local_ocr_disabled() is True


def test_the_failure_counter_is_scoped_per_instance_not_shared_globally():
    """2026-09-08: this used to be a CLASS attribute mutated via type(self)._local_ocr_failures
    — shared across every OCRProcessor in the process. A new instance is created per document
    (VulaIngestionPipeline.ingest_file -> DocumentParser -> OCRProcessor), so 3 failures on one
    tenant's document must never disable local OCR for a completely separate instance/document,
    let alone another tenant's concurrent ingest."""
    from vula.ingestion.pipeline import OCRProcessor
    poisoned = OCRProcessor()
    poisoned._local_ocr_failures = OCRProcessor._LOCAL_OCR_FAILURE_LIMIT
    assert poisoned._local_ocr_disabled() is True

    fresh = OCRProcessor()
    assert fresh._local_ocr_disabled() is False, \
        "a fresh instance must not inherit another instance's failure count"


def test_one_success_forgives_earlier_failures():
    """Consecutive, not cumulative — a healthy box that blips once is not written off."""
    import inspect
    from vula.ingestion.pipeline import OCRProcessor
    src = inspect.getsource(OCRProcessor.process_image)
    assert "_local_ocr_failures = 0" in src, "a good read resets the counter"
    assert "_local_ocr_failures += 1" in src


def test_the_cloud_path_is_never_short_circuited():
    """Only the LOCAL step is skipped. Cloud vision reads these pages correctly and must always
    be tried, or an image-heavy page would silently yield nothing."""
    import inspect
    from vula.ingestion.pipeline import OCRProcessor
    src = inspect.getsource(OCRProcessor.process_image)
    i = src.index("_local_ocr_disabled")
    assert "_cloud_vision_fallback" in src[i:], "cloud still runs when local is skipped"
