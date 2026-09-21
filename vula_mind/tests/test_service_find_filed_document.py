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

from vula.commerce.service import _document_amount, find_filed_document

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


def _mock_filed_documents_with_crossref(crossref_rows):
    """Like _mock_filed_documents([]) for the initial (empty) SQL search, plus the filename
    cross-reference lookup (select().eq().in_().execute()) the semantic-fallback enrichment
    runs — same select().eq() object the initial search's .order() hangs off, since both go
    through the same table("vula_filed_documents")."""
    m = _mock_filed_documents([])
    eq1 = m.table.return_value.select.return_value.eq.return_value
    eq1.in_.return_value.execute.return_value = MagicMock(data=crossref_rows)
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


# ── amount extraction + semantic cross-reference (2026-09-21 follow-up) ─────────────
#
# Real DIGG transcript, same session: "jackhammer" (the name on a Handiman Centre COD account)
# hit the semantic fallback exactly as designed above, but every one of the real invoices behind
# it wrote its total as total_cents (the standard money-extraction schema per CLAUDE.md's "money
# is always integer cents"), which _document_amount never checked — so the model got a fuzzy
# excerpt with no real number and correctly said "I don't have the specific details", which the
# platform's own verification layer flagged as a defect. Two gaps, fixed together: (1) the SQL
# path's amount extraction ignored total_cents entirely: (2) the semantic fallback never
# cross-referenced its filename hits back to vula_filed_documents at all, so even a document
# that WAS filed normally with a good total_cents never got it attached.

def test_document_amount_checks_every_real_schema():
    assert _document_amount({"amount": "R100"}) == "R100"
    assert _document_amount({"total": 50}) == 50
    assert _document_amount({"amount_rands": 25}) == 25
    assert _document_amount({"total_cents": 9200}) == 92.0   # the exact real jackhammer invoice
    assert _document_amount({}) is None


@pytest.mark.asyncio
async def test_sql_match_surfaces_amount_from_total_cents():
    rows = [{
        "id": "d1", "filename": "POS Account Sale 22-191407.pdf", "category": "Invoice",
        "summary": "drill bits",
        "fields": {"supplier": "Gardens Handiman Centre", "total_cents": 9200},
        "status": "filed", "created_at": "2026-09-14T10:00:00Z", "customer_phone": None,
    }]
    with patch("vula.commerce.service._client", return_value=_mock_filed_documents(rows)):
        res = await find_filed_document(TID, "drill bits")

    assert res["matches"][0]["amount"] == 92.0


@pytest.mark.asyncio
async def test_semantic_fallback_enriches_with_filed_document_amount():
    """The exact 2026-09-21 transcript: a semantic-only hit whose document was also filed
    normally with a real total_cents should come back with that amount attached, not just an
    excerpt — so the model can actually sum/quote it."""
    hit = {"filename": "POS Account Sale 22-191407.pdf",
           "text": "drill bits for Jack Hammer's account", "score": 0.51}
    crossref_rows = [{
        "filename": "POS Account Sale 22-191407.pdf", "category": "Invoice",
        "fields": {"supplier": "Gardens Handiman Centre", "total_cents": 9200},
    }]
    mock_client = _mock_filed_documents_with_crossref(crossref_rows)
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[hit])
        res = await find_filed_document(TID, "jackhammer")

    assert res["match_type"] == "knowledge_base"
    match = res["matches"][0]
    assert match["amount"] == 92.0
    assert match["party"] == "Gardens Handiman Centre"


@pytest.mark.asyncio
async def test_semantic_fallback_without_a_filed_row_stays_excerpt_only():
    """No matching vula_filed_documents row (only ever ingested into the KB) — the original
    excerpt-only, 'confirm with the owner' behaviour must hold, not a fabricated amount."""
    hit = {"filename": "gardens_handiman_invoice.pdf",
           "text": "1x Jackhammer rental, 2 days, R850.00 excl VAT", "score": 0.51}
    mock_client = _mock_filed_documents_with_crossref([])
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[hit])
        res = await find_filed_document(TID, "jackhammer")

    assert "amount" not in res["matches"][0]


@pytest.mark.asyncio
async def test_crossref_lookup_failure_still_returns_semantic_matches():
    """Best-effort, same as the KB query itself — a broken cross-reference lookup must never
    take down the whole tool call, just fall back to excerpt-only matches."""
    hit = {"filename": "gardens_handiman_invoice.pdf", "text": "Jackhammer rental", "score": 0.5}
    mock_client = _mock_filed_documents([])
    eq1 = mock_client.table.return_value.select.return_value.eq.return_value
    eq1.in_.side_effect = RuntimeError("db down")
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[hit])
        res = await find_filed_document(TID, "jackhammer")

    assert res["match_type"] == "knowledge_base"
    assert res["matches"][0]["filename"] == "gardens_handiman_invoice.pdf"
