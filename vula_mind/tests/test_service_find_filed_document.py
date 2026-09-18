"""Tests for vula.commerce.service.find_filed_document — the single implementation shared by
commerce_admin.py's and email_admin.py's find_document tools (see test_commerce_admin_find_document.py
and test_email_admin_find_document.py for the tool-level delegation tests).

Real incident, 2026-09-18 (DIGG tenant): "Can you look up all invoice for jackhammer" and
"Through the emails..." both got a generic "couldn't find" reply even though the invoice had
been correctly ingested — because the SQL match here only checks filename/summary, and
"jackhammer" was a line-item description inside the document, not its filename or summary. The
semantic-KB fallback tested below (`test_sql_miss_falls_back_to_semantic_search`) is the fix:
when the SQL match comes up empty, search the tenant's ingested knowledge base (populated from
both emailed and WhatsApp-sent documents) before reporting "not found".
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce.service import find_filed_document

TID = "test-tenant"


def _mock_filed_documents(rows):
    """Chainable mock matching vula_filed_documents' select().eq().order()[.eq()][.or_()]
    .limit().execute() shape — category/or_ filters are optional so both must return self."""
    m = MagicMock()
    chain = m.table.return_value.select.return_value.eq.return_value.order.return_value
    chain.eq.return_value = chain
    chain.or_.return_value = chain
    chain.limit.return_value.execute.return_value = MagicMock(data=rows)
    return m


@pytest.mark.asyncio
async def test_requires_query():
    res = await find_filed_document(TID, "")
    assert "error" in res


@pytest.mark.asyncio
async def test_sql_match_returns_filed_document():
    rows = [{
        "id": "d1", "filename": "solid-cape-invoice.pdf", "category": "Invoice",
        "summary": "Invoice from Solid Cape for performer costs, R7,500.00",
        "fields": {"supplier": "Solid Cape", "amount": "R7,500.00"},
        "status": "filed", "created_at": "2026-08-19T10:00:00Z", "customer_phone": None,
    }]
    with patch("vula.commerce.service._client", return_value=_mock_filed_documents(rows)):
        res = await find_filed_document(TID, "Solid Cape performer invoice")

    assert res["match_type"] == "filed_document"
    assert len(res["matches"]) == 1
    match = res["matches"][0]
    assert match["filename"] == "solid-cape-invoice.pdf"
    assert match["party"] == "Solid Cape"
    assert match["amount"] == "R7,500.00"


@pytest.mark.asyncio
async def test_sanitizes_filter_breaking_characters():
    mock_client = _mock_filed_documents([])
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[])
        await find_filed_document(TID, "Solid Cape, (urgent)")

    chain = mock_client.table.return_value.select.return_value.eq.return_value.order.return_value
    called_with = chain.or_.call_args[0][0]
    assert "," not in called_with.split("ilike.%")[1].split("%")[0]
    assert "(" not in called_with and ")" not in called_with


@pytest.mark.asyncio
async def test_sql_query_failure_returns_error_not_raise():
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        res = await find_filed_document(TID, "anything")
    assert "error" in res


# ── semantic-KB fallback (2026-09-18 fix) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_sql_miss_falls_back_to_semantic_search():
    """The exact 2026-09-18 incident: 'jackhammer' names an item inside a document's content,
    not its filename or summary, so the SQL match alone misses it — but the document is in the
    tenant's knowledge base (ingested from an emailed attachment), so the semantic fallback
    should still surface it instead of reporting 'not found'."""
    mock_client = _mock_filed_documents([])
    hit = {"filename": "gardens_handiman_invoice.pdf",
           "text": "1x Jackhammer rental, 2 days, R850.00 excl VAT", "score": 0.51}
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[hit])
        res = await find_filed_document(TID, "jackhammer")

    assert res["match_type"] == "knowledge_base"
    assert len(res["matches"]) == 1
    assert res["matches"][0]["filename"] == "gardens_handiman_invoice.pdf"
    assert "Jackhammer" in res["matches"][0]["excerpt"]


@pytest.mark.asyncio
async def test_no_match_in_either_store_gives_actionable_message_not_a_guess():
    mock_client = _mock_filed_documents([])
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[])
        res = await find_filed_document(TID, "nonexistent thing")

    assert "matches" not in res
    assert "message" in res
    assert "invoice/document number" in res["message"] or "resend" in res["message"]


@pytest.mark.asyncio
async def test_semantic_fallback_failure_still_gives_actionable_message():
    """The KB fallback is best-effort — if it errors, the SQL 'no match' outcome still stands
    rather than the whole tool call blowing up."""
    mock_client = _mock_filed_documents([])
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(side_effect=RuntimeError("qdrant down"))
        res = await find_filed_document(TID, "jackhammer")

    assert "matches" not in res
    assert "message" in res


@pytest.mark.asyncio
async def test_sql_match_short_circuits_the_semantic_fallback():
    """A SQL hit is cheap, precise and already-structured — no need to also pay for a KB query."""
    rows = [{
        "id": "d1", "filename": "invoice.pdf", "category": "Invoice", "summary": "s",
        "fields": {}, "status": "filed", "created_at": "2026-08-19T10:00:00Z",
        "customer_phone": None,
    }]
    with patch("vula.commerce.service._client", return_value=_mock_filed_documents(rows)), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        await find_filed_document(TID, "invoice")

    mock_pipeline.return_value.query.assert_not_called()
