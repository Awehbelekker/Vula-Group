"""Smart Scanner reads documents with the document reader + the local text model (12 Nov 2026).

Was: the general local vision model (llava) was asked for the schema straight from the photo —
slow, and it invents figures on documents. Now, when GLM-OCR and the local text model are both on
the box: GLM-OCR reads the text, the text model fills the schema from it, and every amount (R10+)
must be printed in that text, or the scan goes to cloud vision as before. Without them: unchanged.
"""
import json
import types
from unittest.mock import AsyncMock, patch

import pytest

from vula.api import commerce

_PAGE = ("MUGG & BEAN CANAL WALK\nTax invoice  VAT 4123456789\n2026-11-10\n"
         "Cappuccino x2   R 64.00\nToasted sandwich x1   R 121.00\nTOTAL R 185.00")
_GOOD = {"doc_type": "receipt", "supplier": "Mugg & Bean", "date": "2026-11-10", "total_cents": 18500,
         "line_items": [{"description": "Cappuccino", "quantity": 2, "total_cents": 6400},
                        {"description": "Toasted sandwich", "quantity": 1, "total_cents": 12100}],
         "confidence": "high"}
_INVENTED = {**_GOOD, "total_cents": 19500,
             "line_items": [{"description": "Cappuccino", "quantity": 2, "total_cents": 7400},
                            {"description": "Toasted sandwich", "quantity": 1, "total_cents": 12100}]}
_CLOUD = {**_GOOD, "supplier": "Mugg & Bean (cloud)"}


def _reply(obj):
    msg = types.SimpleNamespace(content=json.dumps(obj))
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


async def _scan(text_reply, read=(_PAGE, True), cloud=("openrouter/google/gemini-2.5-flash", "k", "b"),
                box_has_text_model=True):
    calls = []

    async def acompletion(**kw):
        calls.append(kw)
        if kw["model"].startswith("openrouter/"):
            return _reply(_CLOUD)
        if isinstance(kw["messages"][-1]["content"], list):      # image sent: the vision path
            return _reply({**_GOOD, "supplier": "vision"})
        return _reply(text_reply)

    async def available(base=None, model=None):
        return box_has_text_model if model else True

    with (patch("litellm.acompletion", new=acompletion),
          patch("core.llm_router.ollama_available", new=available),
          patch("vula.ingestion.pipeline.OCRProcessor.read_local", new=AsyncMock(return_value=read)),
          patch("core.llm_router.resolve_vision_route",
                new=AsyncMock(return_value=("ollama/llava:7b", None, "http://localhost:11434"))),
          patch("core.llm_router.resolve_cloud_vision_route", return_value=cloud),
          patch("vula.api.tenants.get_config", return_value={"business_type": "food"})):
        out = await commerce.admin_smart_scan("t1", commerce.ScanRequest(image_base64="aGVsbG8="))
    return out, calls


@pytest.mark.asyncio
async def test_text_path_reads_locally_and_books_nothing_unseen():
    out, calls = await _scan(_GOOD)
    assert out["read_with"] == "document reader" and not out["escalated"]
    from config import settings
    from core.llm_router import _local_prefix
    assert out["model"] == f"{_local_prefix()}/{settings.model_worker_cheap_local}"
    assert out["extracted"]["total_cents"] == 18500
    assert len(calls) == 1                                  # no vision call, no cloud call
    sent = calls[0]["messages"][-1]["content"]
    assert "TOTAL R 185.00" in sent and "DOCUMENT_TEXT" in sent      # the page, fenced as untrusted
    assert "Read the document text below" in calls[0]["messages"][0]["content"]
    assert calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_an_amount_not_on_the_page_goes_to_cloud_vision():
    out, calls = await _scan(_INVENTED)     # R195 / R74: add up, but aren't printed
    assert out["escalated"] and out["extracted"]["supplier"] == "Mugg & Bean (cloud)"
    assert out["read_with"] == "vision"
    assert len(calls) == 2 and calls[0]["model"].startswith(("ollama/", "ollama_chat/"))
    assert calls[1]["model"] == "openrouter/google/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_without_a_cloud_route_unseen_amounts_are_flagged_not_trusted():
    out, _ = await _scan(_INVENTED, cloud=None)
    ex = out["extracted"]
    assert not out["escalated"] and ex["confidence"] == "low"
    assert {f["cents"] for f in ex["_unverified_figures"]} == {19500, 7400}


@pytest.mark.asyncio
async def test_no_document_reader_on_the_box_keeps_the_vision_path():
    out, calls = await _scan(_GOOD, read=("", False))
    assert out["extracted"]["supplier"] == "vision" and out["model"] == "ollama/llava:7b"
    assert out["read_with"] == "vision"
    assert isinstance(calls[0]["messages"][-1]["content"], list)


@pytest.mark.asyncio
async def test_no_text_model_on_the_box_keeps_the_vision_path():
    out, calls = await _scan(_GOOD, box_has_text_model=False)
    assert out["extracted"]["supplier"] == "vision"


@pytest.mark.asyncio
async def test_doc_reader_only_never_uses_the_general_vision_model(tmp_path):
    from vula.ingestion import pipeline
    ocr = pipeline.OCRProcessor()
    with (patch.object(pipeline, "DOC_OCR_MODEL", "glm-ocr"), patch.object(pipeline, "GLM_OCR_MODEL", "llava:7b"),
          patch.object(ocr, "_local_reader", new=AsyncMock(return_value="llava:7b")),
          patch("httpx.AsyncClient") as client):
        assert await ocr.read_local("aGVsbG8=", "read", doc_reader_only=True) == ("", False)
    client.assert_not_called()


def test_scan_json_parsing_is_forgiving():
    assert commerce._parse_scan_json('```json\n{"total_cents": 100}\n```') == {"total_cents": 100}
    assert commerce._parse_scan_json('<think>x</think>Here: {"a": 1} done') == {"a": 1}
    assert commerce._parse_scan_json("no json")["confidence"] == "low"
    assert commerce._parse_scan_json("[1, 2]")["confidence"] == "low"
