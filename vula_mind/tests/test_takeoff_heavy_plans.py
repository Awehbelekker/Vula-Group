"""Drawing sets are read in a child process (vula/ingestion/pdf_extract.run), never on the web worker."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from vula.ingestion import pdf_extract
from vula.takeoff.plan_reader import PDFReader, PlanReader


def test_the_pdf_reader_uses_the_child_process(tmp_path):
    pages = [[1, "A101 GROUND FLOOR PLAN SCALE 1:100\\nOffice | 24.5 m2", None]]
    with patch.object(pdf_extract, "run", AsyncMock(return_value=pages)) as run:
        sheets = asyncio.run(PDFReader(ocr=False).read(tmp_path / "p.pdf"))
    assert run.await_count == 1
    assert (sheets[0].sheet_number, sheets[0].scale, sheets[0].scale_ratio) == ("A101", "1:100", 100.0)


def test_a_too_heavy_drawing_set_is_held_with_a_reason_not_a_dead_worker(tmp_path):
    with patch.object(pdf_extract, "run", AsyncMock(side_effect=pdf_extract.TooHeavy("took over 120s"))):
        p = asyncio.run(PlanReader("t1").read(tmp_path / "pack.pdf"))
    assert p.review["status"] == "pending"
    assert any("too heavy" in n for n in p.extraction_notes)


def test_without_pymupdf_it_falls_back_to_pdfplumber(tmp_path):
    with patch.object(pdf_extract, "run", AsyncMock(side_effect=ImportError("No module named 'pymupdf'"))), \
         patch.object(PDFReader, "_read_pdfplumber", AsyncMock(return_value=["fallback"])) as fb:
        assert asyncio.run(PDFReader().read(tmp_path / "p.pdf")) == ["fallback"]
    assert fb.await_count == 1


def test_a_real_pdf_goes_through_the_real_child(tmp_path):
    import fitz
    doc = fitz.open()
    doc.new_page(width=1191, height=842).insert_text((40, 60), "A102 FIRST FLOOR PLAN SCALE 1:50")
    doc.save(tmp_path / "real.pdf")
    sheets = asyncio.run(PDFReader(ocr=False).read(tmp_path / "real.pdf"))
    assert sheets[0].sheet_number == "A102" and sheets[0].scale_ratio == 50.0
