"""docling_extract must never break the document pipeline: a non-PDF returns None without
touching Docling at all, and Docling genuinely not being installed in this environment (it's
deliberately kept out of requirements.txt — see requirements-docling.txt) degrades to None
rather than raising. 2026-09-11."""
import pytest

from vula.ingestion.docling_extract import extract_markdown


@pytest.mark.asyncio
async def test_non_pdf_returns_none_without_touching_docling(tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"not a pdf")
    assert await extract_markdown(f) is None


@pytest.mark.asyncio
async def test_docling_not_installed_here_degrades_to_none(tmp_path):
    # docling is genuinely absent from this dev/test environment (requirements-docling.txt is
    # only installed into the Docker image, never requirements.txt) — this exercises the real
    # fail-open path, not a mock of it. If docling ever IS present, conversion still fails
    # cleanly on these non-PDF bytes, so the assertion holds either way.
    f = tmp_path / "invoice.pdf"
    f.write_bytes(b"%PDF-1.4 not a real pdf body")
    assert await extract_markdown(f) is None
