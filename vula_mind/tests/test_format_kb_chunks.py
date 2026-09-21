"""Tests for core.skills.base.format_kb_chunks — the shared RAG-context builder that
reasoning.py and architecture_planning.py now both call instead of inlining their own
"[filename]: text" join (2026-09-21 generalisation of the find_document amount-cross-reference
fix, see tests/test_service_find_filed_document.py for the original incident/fix at the tool
level). A chunk whose source document was also filed normally with a real extracted amount now
carries that verified figure in its tag, so reasoning.py/architecture_planning.py don't have to
ask the model to read a number off fuzzy chunk text — the exact failure mode behind the real
R70,400 "logged" fabrication incident looks_like_tenant_data_question's guard exists to catch."""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.base import format_kb_chunks

TID = "test-tenant"


@pytest.mark.asyncio
async def test_empty_chunks_gives_empty_string():
    assert await format_kb_chunks(TID, []) == ""


@pytest.mark.asyncio
async def test_no_filed_match_falls_back_to_plain_filename_tag():
    chunks = [{"filename": "notes.pdf", "text": "some general content", "score": 0.4}]
    with patch("vula.commerce.service.filed_amounts_by_filename",
               new=AsyncMock(return_value={})):
        out = await format_kb_chunks(TID, chunks)
    assert out == "[notes.pdf]: some general content"


@pytest.mark.asyncio
async def test_filed_match_carries_the_verified_amount_in_the_tag():
    """The exact real scenario: a semantic-only KB hit whose document was also filed normally
    with a real total_cents-derived amount should surface that figure, not just the excerpt."""
    chunks = [{"filename": "POS Account Sale 22-191407.pdf",
               "text": "drill bits for Jack Hammer's account", "score": 0.51}]
    filed = {"POS Account Sale 22-191407.pdf":
             {"amount": 92.0, "party": "Gardens Handiman Centre"}}
    with patch("vula.commerce.service.filed_amounts_by_filename",
               new=AsyncMock(return_value=filed)):
        out = await format_kb_chunks(TID, chunks)
    assert "R92.00" in out
    assert "Gardens Handiman Centre" in out
    assert "drill bits for Jack Hammer's account" in out


@pytest.mark.asyncio
async def test_filed_match_without_a_party_still_shows_the_amount():
    chunks = [{"filename": "invoice.pdf", "text": "some content", "score": 0.4}]
    filed = {"invoice.pdf": {"amount": 340.0, "party": None}}
    with patch("vula.commerce.service.filed_amounts_by_filename",
               new=AsyncMock(return_value=filed)):
        out = await format_kb_chunks(TID, chunks)
    assert "R340.00" in out
    assert " — None" not in out


@pytest.mark.asyncio
async def test_multiple_chunks_join_with_blank_line():
    chunks = [
        {"filename": "a.pdf", "text": "first", "score": 0.5},
        {"filename": "b.pdf", "text": "second", "score": 0.4},
    ]
    with patch("vula.commerce.service.filed_amounts_by_filename",
               new=AsyncMock(return_value={})):
        out = await format_kb_chunks(TID, chunks)
    assert out == "[a.pdf]: first\n\n[b.pdf]: second"


@pytest.mark.asyncio
async def test_cross_reference_failure_still_returns_plain_chunks():
    """Best-effort, fail-open — a broken cross-reference lookup must never break the whole
    grounding context, just fall back to plain filename tags."""
    chunks = [{"filename": "notes.pdf", "text": "some content", "score": 0.4}]
    with patch("vula.commerce.service.filed_amounts_by_filename",
               new=AsyncMock(side_effect=RuntimeError("db down"))):
        out = await format_kb_chunks(TID, chunks)
    assert out == "[notes.pdf]: some content"
