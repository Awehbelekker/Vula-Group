"""Tests for DocumentParser._parse_pdf's OCR fallback (vula/ingestion/pipeline.py).

2026-08-17: reproduced against a real DIGG "Notification of Payment" PDF from FNB — its
embedded font CMap uses a non-compliant ASCII85 stream that pdfminer's strict decoder rejects
outright (pdfplumber.utils.exceptions.PdfminerException: "Non-Ascii85 digit found: ..."),
failing the whole ingestion with no fallback. Fixed by falling back to a pdf2image/poppler
render + OCR pass — an engine independent of pdfminer — whenever native parsing fails, not
just when it succeeds with too little text (the pre-existing scanned-page path).
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.ingestion.pipeline import DocumentParser


@pytest.mark.asyncio
async def test_parse_pdf_native_success_unaffected():
    """The common case — native extraction works — must behave exactly as before."""
    parser = DocumentParser()
    page = MagicMock()
    page.extract_text.return_value = "Real invoice text " * 10  # > 50 chars, no OCR needed
    page.extract_tables.return_value = []
    pdf_ctx = MagicMock()
    pdf_ctx.pages = [page]
    pdf_cm = MagicMock()
    pdf_cm.__enter__.return_value = pdf_ctx
    pdf_cm.__exit__.return_value = False

    with patch("pdfplumber.open", return_value=pdf_cm):
        pages = await parser._parse_pdf(Path("normal.pdf"))

    assert len(pages) == 1
    assert pages[0][0] == 1
    assert "Real invoice text" in pages[0][1]


@pytest.mark.asyncio
async def test_parse_pdf_falls_back_to_ocr_when_pdfminer_throws():
    """The reported bug: pdfminer raises mid-document (malformed ASCII85 stream). Must not
    propagate — must fall back to the independent poppler+OCR path and still return text."""
    parser = DocumentParser()

    def _boom(*a, **kw):
        raise ValueError("Non-Ascii85 digit found: \xee")

    fake_image = MagicMock()
    fake_image.save = MagicMock()

    with (
        patch("pdfplumber.open", side_effect=_boom),
        patch("pdf2image.convert_from_path", return_value=[fake_image]),
        patch.object(parser.ocr, "process_image", new=AsyncMock(
            return_value="# NOTIFICATION OF PAYMENT\nCur/Amount: ZAR 18198.00")),
        patch("pathlib.Path.unlink"),
    ):
        pages = await parser._parse_pdf(Path("Payment Notification.pdf"))

    assert len(pages) == 1
    assert pages[0][0] == 1
    assert "ZAR 18198.00" in pages[0][1]


@pytest.mark.asyncio
async def test_parse_pdf_falls_back_when_page_extract_text_throws_mid_document():
    """Same failure mode, but raised from inside the per-page loop rather than pdfplumber.open
    itself (matches the real traceback: pdfplumber.open() succeeds, page.extract_text() throws)."""
    parser = DocumentParser()
    page = MagicMock()
    page.extract_text.side_effect = ValueError("Non-Ascii85 digit found: \xfd")
    pdf_ctx = MagicMock()
    pdf_ctx.pages = [page]
    pdf_cm = MagicMock()
    pdf_cm.__enter__.return_value = pdf_ctx
    pdf_cm.__exit__.return_value = False

    fake_image = MagicMock()
    fake_image.save = MagicMock()

    with (
        patch("pdfplumber.open", return_value=pdf_cm),
        patch("pdf2image.convert_from_path", return_value=[fake_image]),
        patch.object(parser.ocr, "process_image", new=AsyncMock(return_value="recovered text")),
        patch("pathlib.Path.unlink"),
    ):
        pages = await parser._parse_pdf(Path("Payment Notification.pdf"))

    assert pages == [(1, "recovered text")]


@pytest.mark.asyncio
async def test_parse_pdf_ocr_fallback_multi_page():
    parser = DocumentParser()
    img1, img2 = MagicMock(), MagicMock()

    with (
        patch("pdf2image.convert_from_path", return_value=[img1, img2]),
        patch.object(parser.ocr, "process_image", new=AsyncMock(side_effect=["page one", "page two"])),
        patch("pathlib.Path.unlink"),
    ):
        pages = await parser._parse_pdf_ocr_fallback(Path("multi.pdf"))

    assert pages == [(1, "page one"), (2, "page two")]


@pytest.mark.asyncio
async def test_parse_pdf_ocr_fallback_when_poppler_also_unavailable():
    """If even pdf2image/poppler fails (e.g. not installed), degrade to the old
    whole-document-as-one-image behavior rather than raising."""
    parser = DocumentParser()

    with (
        patch("pdf2image.convert_from_path", side_effect=ImportError("no poppler")),
        patch.object(parser.ocr, "process_image", new=AsyncMock(return_value="last resort text")),
    ):
        pages = await parser._parse_pdf_ocr_fallback(Path("broken.pdf"))

    assert pages == [(1, "last resort text")]


@pytest.mark.asyncio
async def test_parse_pdf_ocr_fallback_skips_blank_pages():
    parser = DocumentParser()
    img = MagicMock()

    with (
        patch("pdf2image.convert_from_path", return_value=[img]),
        patch.object(parser.ocr, "process_image", new=AsyncMock(return_value="")),
        patch("pathlib.Path.unlink"),
    ):
        pages = await parser._parse_pdf_ocr_fallback(Path("blank.pdf"))

    assert pages == []


# ── fitz fast path (2026-09-10) ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fitz_extracts_a_digital_pdf_without_touching_ocr(tmp_path):
    """A PDF with a real text layer is read by PyMuPDF in milliseconds — the OCR path (local
    model, cloud vision, poppler) must never be reached. This is the case that used to take
    30-50s because pdfplumber's pdfminer rejected FNB's malformed ASCII85 stream."""
    import pymupdf
    pdf = tmp_path / "payment.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "NOTIFICATION OF PAYMENT\nZAR 6,579.00 to Sagacity Group\nTrace Id: W1KYP3WQ")
    doc.save(str(pdf)); doc.close()

    parser = DocumentParser()
    with (
        patch.object(parser.ocr, "process_image", new=AsyncMock(side_effect=AssertionError("OCR must not run"))),
        patch("pdfplumber.open", side_effect=AssertionError("pdfplumber must not run")),
        patch("pdf2image.convert_from_path", side_effect=AssertionError("poppler must not run")),
    ):
        pages = await parser._parse_pdf(pdf)

    assert len(pages) == 1
    assert "W1KYP3WQ" in pages[0][1] and "6,579.00" in pages[0][1]


@pytest.mark.asyncio
async def test_fitz_ocrs_a_bare_image_only_page(tmp_path):
    """A page with no text layer but real ink (a scan) still routes to OCR."""
    import pymupdf
    pdf = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 200, 200), fill=(0, 0, 0))  # ink, no text
    doc.save(str(pdf)); doc.close()

    parser = DocumentParser()
    with patch.object(parser.ocr, "process_image", new=AsyncMock(return_value="scanned receipt R42.00")):
        pages = await parser._parse_pdf_fitz(pdf)

    assert pages and "scanned receipt" in pages[0][1]
