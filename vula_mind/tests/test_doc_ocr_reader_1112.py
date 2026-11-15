"""Scanned pages are read by the local document-OCR model when the box has it (12 Nov 2026).

Three pages of a scanned DIGG form each waited the full 90 s for the general vision model and got
nothing back, then went to the cloud. Now: MODEL_DOC_OCR (GLM-OCR) with its own task prompt when
installed; MODEL_OCR with the old prompt when not; neither installed -> no local call at all.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.ingestion import pipeline
from vula.ingestion.pipeline import OCRProcessor

_REAL = "NOTIFICATION OF PAYMENT  ZAR 18198.00 to Solucent (Pty) Ltd."


def _client(text=_REAL):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": text}
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    cm = AsyncMock()
    cm.__aenter__.return_value = client
    cm.__aexit__.return_value = False
    return cm, client


def _box(up=True, installed=()):
    """ollama_available as the box answers it: up or not, and which models are installed."""
    async def _probe(base=None, model=None):
        if not up:
            return False
        return True if model is None else model.split(":")[0] in {m.split(":")[0] for m in installed}
    return _probe


async def _read(tmp_path, probe, text=_REAL):
    img = tmp_path / "page.png"
    img.write_bytes(b"fake-png")
    ocr = OCRProcessor()
    cm, client = _client(text)
    cloud = AsyncMock(return_value="cloud read")
    with (
        patch.object(pipeline, "DOC_OCR_MODEL", "glm-ocr"),
        patch.object(pipeline, "GLM_OCR_MODEL", "llava:7b"),
        patch("core.llm_router.ollama_available", new=probe),
        patch("core.llm_router._ollama_headers", return_value={}),
        patch("httpx.AsyncClient", return_value=cm),
        patch.object(ocr, "_cloud_vision_fallback", cloud),
    ):
        out = await ocr.process_image(img)
    return out, client, cloud


@pytest.mark.asyncio
async def test_glm_ocr_is_used_with_its_own_prompt_when_installed(tmp_path):
    out, client, cloud = await _read(tmp_path, _box(installed=("glm-ocr:latest", "llava:7b")))
    assert out == _REAL
    payload = client.post.await_args.kwargs["json"]
    assert payload["model"] == "glm-ocr"
    assert payload["prompt"] == "Text Recognition:"
    assert payload["keep_alive"]
    assert payload["options"]["num_ctx"] == 8192
    cloud.assert_not_awaited()


@pytest.mark.asyncio
async def test_general_vision_model_when_glm_ocr_is_not_installed(tmp_path):
    out, client, _ = await _read(tmp_path, _box(installed=("llava:7b",)))
    assert out == _REAL
    payload = client.post.await_args.kwargs["json"]
    assert payload["model"] == "llava:7b"
    assert payload["prompt"].startswith("Parse this document to Markdown")


@pytest.mark.asyncio
async def test_no_reader_installed_skips_local_without_waiting(tmp_path):
    out, client, cloud = await _read(tmp_path, _box(installed=("llama3.1:8b",)))
    client.post.assert_not_awaited()
    cloud.assert_awaited()
    assert out == "cloud read"


@pytest.mark.asyncio
async def test_box_down_skips_local(tmp_path):
    out, client, cloud = await _read(tmp_path, _box(up=False, installed=("glm-ocr",)))
    client.post.assert_not_awaited()
    assert out == "cloud read"


@pytest.mark.asyncio
async def test_timeout_is_logged_by_name(tmp_path, caplog):
    """An httpx timeout has an empty message; the log said 'escalating to cloud vision: ' and
    nothing else (12 Nov). The exception type now goes with it."""
    import httpx
    img = tmp_path / "page.png"
    img.write_bytes(b"fake-png")
    ocr = OCRProcessor()
    cm, client = _client()
    client.post = AsyncMock(side_effect=httpx.ReadTimeout(""))
    with (
        patch.object(pipeline, "DOC_OCR_MODEL", "glm-ocr"),
        patch("core.llm_router.ollama_available", new=_box(installed=("glm-ocr",))),
        patch("core.llm_router._ollama_headers", return_value={}),
        patch("httpx.AsyncClient", return_value=cm),
        patch.object(ocr, "_cloud_vision_fallback", AsyncMock(return_value="cloud read")),
        caplog.at_level("WARNING"),
    ):
        assert await ocr.process_image(img) == "cloud read"
    assert "ReadTimeout" in caplog.text and "glm-ocr" in caplog.text


def test_prompt_only_changes_for_glm_ocr():
    assert OCRProcessor._local_prompt("glm-ocr:q8_0", "general") == "Text Recognition:"
    assert OCRProcessor._local_prompt("llava:7b", "general") == "general"
