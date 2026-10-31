"""The plan reader goes through core/llm_router like every other model call (defect list 31 Oct,
#1 and #8): local-first with the Cloudflare Access headers and a logged cloud fallback, instead of
a raw httpx POST to Ollama that came back empty in production."""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from vula.takeoff import plan_reader
from vula.takeoff.plan_reader import DrawingIntelligence, PDFReader


def _resp(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def test_extraction_uses_the_router_and_its_cloud_fallback():
    route = ("ollama_chat/m", None, "http://x")
    with patch("core.llm_router.resolve_generation_route", AsyncMock(return_value=route)) as r, \
         patch("core.llm_router.complete_local_first",
               AsyncMock(return_value=(_resp('<think>x</think>{"gfa": 120.5}'), route))) as c:
        out = asyncio.run(DrawingIntelligence()._llm_extract("text", "Extract", "{}"))
    assert out == {"gfa": 120.5}
    assert r.await_args.kwargs["task_type"] == "takeoff_extraction"
    assert c.await_args.kwargs["task_type"] == "takeoff_extraction"
    msgs = c.await_args.kwargs["messages"]
    assert msgs[0]["role"] == "system" and "untrusted" in msgs[0]["content"].lower()


def test_a_failed_model_call_returns_nothing_not_an_exception():
    with patch("core.llm_router.resolve_generation_route", AsyncMock(side_effect=RuntimeError("down"))):
        assert asyncio.run(DrawingIntelligence()._llm_extract("t", "i", "{}")) == {}


def test_ocr_uses_the_vision_route(tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG fake")
    with patch("core.llm_router.resolve_vision_route", AsyncMock(return_value=("ollama/ocr", None, "http://x"))), \
         patch("litellm.acompletion", AsyncMock(return_value=_resp("ROOM 1 24 m2"))) as ac:
        assert asyncio.run(PDFReader()._ocr_image(img)) == "ROOM 1 24 m2"
    parts = ac.await_args.kwargs["messages"][-1]["content"]
    assert any(p.get("type") == "image_url" for p in parts)


def test_no_raw_http_to_ollama_left():
    src = inspect.getsource(plan_reader)
    assert "/api/generate" not in src and "import httpx" not in src


def test_page_images_never_share_a_path():
    # Concurrent jobs used to write /tmp/vula_sheet_{i}.png — one job read another's page.
    src = inspect.getsource(PDFReader)
    assert "vula_sheet_{i}" not in src and "vula_ocr_{i}" not in src


def test_a_local_route_is_called_once_without_a_hidden_cloud_retry(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "local_call_timeout_s", 60)
    route = ("ollama_chat/llama3", None, "http://x")
    calls = []

    async def fake(**kw):
        calls.append(kw)
        return _resp('{"gfa": 1}')
    with patch("core.llm_router.resolve_generation_route", AsyncMock(return_value=route)), \
         patch("litellm.acompletion", fake):
        assert asyncio.run(DrawingIntelligence()._llm_extract("t", "i", "{}")) == {"gfa": 1}
    assert len(calls) == 1 and calls[0]["model"] == "ollama_chat/llama3" and calls[0]["timeout"] == 60
