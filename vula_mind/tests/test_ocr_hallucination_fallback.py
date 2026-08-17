"""Tests for OCRProcessor's hallucination-detection escalation ladder (vula/ingestion/pipeline.py).

2026-08-17: reproduced live against a real DIGG payment-notification page — GLM-OCR (and, on
retry, even the cloud vision fallback) sometimes doesn't fail or time out, it confidently
fabricates a plausible-looking generic business letter (wrong date, wrong amount, literal
"[Name Redacted]" bracket placeholders) instead of actually reading the image. The old
"non-empty text = success" check had no way to catch this. Fixed with a bracket-placeholder
detector applied to both the local and cloud responses, a bounded cloud retry, and a genuine
non-LLM OCR (pytesseract) last resort.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.ingestion.pipeline import OCRProcessor

_HALLUCINATED = ("Notification of Payment\n\nDear Sir/Madam,\n\nKind regards,\n\n"
                  "[Name Redacted]\n[Company Name Redacted]")
_REAL = ("NOTIFICATION OF PAYMENT\n\nFirst National Bank hereby confirms that the following "
         "payment instruction has been received: ZAR 18198.00 to Solucent (Pty) Ltd.")


@pytest.fixture
def ocr():
    return OCRProcessor()


def _ollama_response(text: str):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": text}
    return resp


def _client_cm(resp=None, raise_exc=None):
    """Fakes `async with httpx.AsyncClient(...) as client: await client.post(...)`."""
    client = AsyncMock()
    if raise_exc:
        client.post = AsyncMock(side_effect=raise_exc)
    else:
        client.post = AsyncMock(return_value=resp)
    cm = AsyncMock()
    cm.__aenter__.return_value = client
    cm.__aexit__.return_value = False
    return cm


# ── _looks_hallucinated ────────────────────────────────────────────────────

def test_looks_hallucinated_detects_bracket_placeholders(ocr):
    assert ocr._looks_hallucinated(_HALLUCINATED) is True


def test_looks_hallucinated_false_for_real_text(ocr):
    assert ocr._looks_hallucinated(_REAL) is False


def test_looks_hallucinated_false_for_empty(ocr):
    assert ocr._looks_hallucinated("") is False


# ── process_image escalation ladder ────────────────────────────────────────

@pytest.mark.asyncio
async def test_local_success_returns_immediately(ocr, tmp_path):
    img = tmp_path / "page.png"
    img.write_bytes(b"fake-png")
    with (
        patch("core.llm_router._ollama_headers", return_value={}),
        patch("httpx.AsyncClient", return_value=_client_cm(_ollama_response(_REAL))),
        patch.object(ocr, "_cloud_vision_fallback", new=AsyncMock()) as mock_cloud,
    ):
        text = await ocr.process_image(img)
    assert text == _REAL
    mock_cloud.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_hallucinated_escalates_to_cloud(ocr, tmp_path):
    img = tmp_path / "page.png"
    img.write_bytes(b"fake-png")
    with (
        patch("core.llm_router._ollama_headers", return_value={}),
        patch("httpx.AsyncClient", return_value=_client_cm(_ollama_response(_HALLUCINATED))),
        patch.object(ocr, "_cloud_vision_fallback", new=AsyncMock(return_value=_REAL)) as mock_cloud,
    ):
        text = await ocr.process_image(img)
    assert text == _REAL
    mock_cloud.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_exception_escalates_to_cloud(ocr, tmp_path):
    img = tmp_path / "page.png"
    img.write_bytes(b"fake-png")
    with (
        patch("core.llm_router._ollama_headers", return_value={}),
        patch("httpx.AsyncClient", return_value=_client_cm(raise_exc=ConnectionError("tunnel down"))),
        patch.object(ocr, "_cloud_vision_fallback", new=AsyncMock(return_value=_REAL)),
    ):
        text = await ocr.process_image(img)
    assert text == _REAL


@pytest.mark.asyncio
async def test_cloud_retries_once_then_succeeds(ocr, tmp_path):
    """Empirically reproduced: the SAME image hallucinated on cloud attempt 1 and returned
    real content on attempt 2 — the bounded retry exists specifically for this."""
    img = tmp_path / "page.png"
    img.write_bytes(b"fake-png")
    with (
        patch("core.llm_router._ollama_headers", return_value={}),
        patch("httpx.AsyncClient", return_value=_client_cm(raise_exc=ConnectionError("down"))),
        patch.object(ocr, "_cloud_vision_fallback",
                     new=AsyncMock(side_effect=[_HALLUCINATED, _REAL])) as mock_cloud,
    ):
        text = await ocr.process_image(img)
    assert text == _REAL
    assert mock_cloud.await_count == 2


@pytest.mark.asyncio
async def test_all_hallucinated_falls_to_pytesseract(ocr, tmp_path):
    img = tmp_path / "page.png"
    img.write_bytes(b"fake-png")
    with (
        patch("core.llm_router._ollama_headers", return_value={}),
        patch("httpx.AsyncClient", return_value=_client_cm(raise_exc=ConnectionError("down"))),
        patch.object(ocr, "_cloud_vision_fallback",
                     new=AsyncMock(side_effect=[_HALLUCINATED, _HALLUCINATED])),
        patch.object(ocr, "_docling_fallback", new=AsyncMock(return_value=_REAL)) as mock_tess,
    ):
        text = await ocr.process_image(img)
    assert text == _REAL
    mock_tess.assert_awaited_once()


@pytest.mark.asyncio
async def test_nothing_clean_returns_flagged_text_rather_than_empty(ocr, tmp_path):
    """Last resort: a flagged-but-real response beats an empty page (which upstream treats as
    a total ingestion failure) — pytesseract itself also came back empty here."""
    img = tmp_path / "page.png"
    img.write_bytes(b"fake-png")
    with (
        patch("core.llm_router._ollama_headers", return_value={}),
        patch("httpx.AsyncClient", return_value=_client_cm(raise_exc=ConnectionError("down"))),
        patch.object(ocr, "_cloud_vision_fallback",
                     new=AsyncMock(side_effect=[_HALLUCINATED, _HALLUCINATED])),
        patch.object(ocr, "_docling_fallback", new=AsyncMock(return_value="")),
    ):
        text = await ocr.process_image(img)
    assert text == _HALLUCINATED  # flagged, but still returned rather than nothing
