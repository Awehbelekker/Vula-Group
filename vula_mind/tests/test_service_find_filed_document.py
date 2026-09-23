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

from vula.commerce.service import _document_amount, filed_amounts_by_filename, find_filed_document

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
    # Proof of Payment's own key (deterministic FNB parser + LLM extraction schema both use
    # this, never total_cents) — confirmed against real production data same-day follow-up.
    assert _document_amount({"amount_cents": 46350}) == 463.5   # real off-the-hook POP
    assert _document_amount({}) is None


def test_document_amount_prefers_total_cents_over_amount_cents_when_both_present():
    """Invoice/Quote/BOQ's key; shouldn't realistically co-occur with Proof of Payment's, but
    total_cents is the more common real case so it wins if somehow both are set."""
    assert _document_amount({"total_cents": 9200, "amount_cents": 100}) == 92.0


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


# ── status field (2026-09-22, live-mailbox-fallback feature) ────────────────────────
#
# email_admin/commerce_admin's find_document dispatch needs a machine-readable signal to decide
# whether to try a live mailbox search next, rather than only relaying prose and hoping the model
# reads it — see core/skills/email_admin.py::_find_document and commerce_admin.py's twin.

@pytest.mark.asyncio
async def test_sql_hit_carries_found_status():
    rows = [{
        "id": "d1", "filename": "invoice.pdf", "category": "Invoice", "summary": "s",
        "fields": {}, "status": "filed", "created_at": "2026-08-19T10:00:00Z",
        "customer_phone": None,
    }]
    with patch("vula.commerce.service._client", return_value=_mock_filed_documents(rows)):
        res = await find_filed_document(TID, "invoice")
    assert res["status"] == "found"


@pytest.mark.asyncio
async def test_semantic_hit_carries_found_status():
    hit = {"filename": "gardens_handiman_invoice.pdf", "text": "Jackhammer rental", "score": 0.5}
    mock_client = _mock_filed_documents([])
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[hit])
        res = await find_filed_document(TID, "jackhammer")
    assert res["status"] == "found"


@pytest.mark.asyncio
async def test_total_miss_carries_not_found_filed_status():
    mock_client = _mock_filed_documents([])
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[])
        res = await find_filed_document(TID, "nonexistent thing")
    assert res["status"] == "not_found_filed"


# ── category filter on the semantic fallback (2026-09-22) ───────────────────────────
#
# ingest_file (vula/ingestion/pipeline.py) never writes a category payload onto a Qdrant chunk —
# only the unrelated training-KB seeding path does — so a category filter can only be enforced by
# cross-referencing each semantic hit back to its vula_filed_documents row (already fetched for
# the amount enrichment above) and dropping anything that doesn't match, INCLUDING anything with
# no row to check at all (unverifiable against an explicit filter the caller asked for). This is
# deliberately NOT enforced by passing category into VulaIngestionPipeline.query() itself — doing
# that would silently return zero results for every category-scoped call, always, since no real
# chunk ever carries that key.

@pytest.mark.asyncio
async def test_semantic_fallback_drops_hits_whose_filed_category_does_not_match():
    hit = {"filename": "quote.pdf", "text": "a quote, not an invoice", "score": 0.5}
    crossref_rows = [{"filename": "quote.pdf", "category": "Quote / Estimate", "fields": {}}]
    mock_client = _mock_filed_documents_with_crossref(crossref_rows)
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[hit])
        res = await find_filed_document(TID, "quote", category="Invoice")
    assert res["status"] == "not_found_filed"
    assert "matches" not in res


@pytest.mark.asyncio
async def test_semantic_fallback_keeps_hits_whose_filed_category_matches():
    hit = {"filename": "invoice.pdf", "text": "an invoice", "score": 0.5}
    crossref_rows = [{"filename": "invoice.pdf", "category": "Invoice", "fields": {}}]
    mock_client = _mock_filed_documents_with_crossref(crossref_rows)
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[hit])
        res = await find_filed_document(TID, "invoice", category="Invoice")
    assert res["status"] == "found"
    assert res["matches"][0]["filename"] == "invoice.pdf"


@pytest.mark.asyncio
async def test_semantic_fallback_with_category_drops_unverifiable_hits_with_no_filed_row():
    """No cross-referenced vula_filed_documents row at all — can't confirm it matches the
    requested category, so it's dropped rather than assumed to match."""
    hit = {"filename": "kb_only.pdf", "text": "some text", "score": 0.5}
    mock_client = _mock_filed_documents_with_crossref([])
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[hit])
        res = await find_filed_document(TID, "invoice", category="Invoice")
    assert res["status"] == "not_found_filed"


@pytest.mark.asyncio
async def test_semantic_fallback_without_category_keeps_unverified_hits():
    """No category filter requested at all — the pre-existing excerpt-only behaviour (no filed
    row needed) must be unchanged."""
    hit = {"filename": "kb_only.pdf", "text": "some text", "score": 0.5}
    mock_client = _mock_filed_documents_with_crossref([])
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=[hit])
        res = await find_filed_document(TID, "invoice")
    assert res["status"] == "found"
    assert res["matches"][0]["filename"] == "kb_only.pdf"


# ── filed_amounts_by_filename (2026-09-21, generalised for reasoning.py/architecture_planning.py) ──
#
# Factored out of find_filed_document's semantic-fallback cross-reference so RAG-grounded skills
# can carry a verified figure in their own prompt context too (core.skills.base.format_kb_chunks
# is the consumer — see tests/test_format_kb_chunks.py).

@pytest.mark.asyncio
async def test_filed_amounts_by_filename_empty_input_short_circuits():
    assert await filed_amounts_by_filename(TID, []) == {}


@pytest.mark.asyncio
async def test_filed_amounts_by_filename_returns_amount_and_party():
    rows = [{"filename": "POS Account Sale 22-191407.pdf", "category": "Invoice",
             "fields": {"supplier": "Gardens Handiman Centre", "total_cents": 9200}}]
    mock_client = MagicMock()
    (mock_client.table.return_value.select.return_value.eq.return_value.in_
     .return_value.execute.return_value) = MagicMock(data=rows)
    with patch("vula.commerce.service._client", return_value=mock_client):
        out = await filed_amounts_by_filename(TID, ["POS Account Sale 22-191407.pdf"])

    assert out == {"POS Account Sale 22-191407.pdf":
                    {"amount": 92.0, "party": "Gardens Handiman Centre"}}


@pytest.mark.asyncio
async def test_filed_amounts_by_filename_skips_rows_with_no_extractable_amount():
    rows = [{"filename": "account_application.pdf", "category": "General Document",
             "fields": {"company_name": "Handiman Centre"}}]
    mock_client = MagicMock()
    (mock_client.table.return_value.select.return_value.eq.return_value.in_
     .return_value.execute.return_value) = MagicMock(data=rows)
    with patch("vula.commerce.service._client", return_value=mock_client):
        out = await filed_amounts_by_filename(TID, ["account_application.pdf"])

    assert out == {}


@pytest.mark.asyncio
async def test_filed_amounts_by_filename_fails_open():
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        out = await filed_amounts_by_filename(TID, ["anything.pdf"])
    assert out == {}


# ── 2026-09-23: completeness for an exhaustive supplier query ────────────────────
# Real DIGG incident: "all Jack Hammer invoices" found 2 of 13 real invoices (R789 of
# R20,278). Every one had fields.supplier = "GARDENS HANDIMAN CENTRE"; nothing searched that
# field, "Jack Hammer" is only the owner's nickname for that supplier, and semantic top-k is
# lossy by design for "give me all of them".

from vula.commerce.service import (  # noqa: E402
    _document_amount_cents, _dominant_party, _resolve_supplier_names,
)

_JACK_HAMMER_CENTS = [18900, 69700, 9200, 189500, 146900, 125200, 436100, 23500, 73300,
                      34000, 630400, 56300, 214800]


def _handiman_rows():
    return [{"id": f"d{i}", "filename": f"POS-{i}.pdf", "category": "Invoice",
             "summary": "POS Account Sale", "created_at": f"2026-09-{i + 1:02d}T08:00:00Z",
             "fields": {"supplier": "GARDENS HANDIMAN CENTRE", "total_cents": c},
             "status": "filed", "customer_phone": None}
            for i, c in enumerate(_JACK_HAMMER_CENTS)]


def _mock_sequential(*results, crossref_rows=None):
    """Like _mock_filed_documents, but each successive SQL search returns the next result."""
    m = _mock_filed_documents([])
    chain = m.table.return_value.select.return_value.eq.return_value.order.return_value
    chain.limit.return_value.execute.side_effect = [MagicMock(data=r) for r in results]
    if crossref_rows is not None:
        eq1 = m.table.return_value.select.return_value.eq.return_value
        eq1.in_.return_value.execute.return_value = MagicMock(data=crossref_rows)
    return m


def _or_filters(mock_client):
    chain = mock_client.table.return_value.select.return_value.eq.return_value.order.return_value
    return [c[0][0] for c in chain.or_.call_args_list]


@pytest.mark.asyncio
async def test_sql_search_also_matches_the_counterparty_fields():
    mock_client = _mock_sequential(_handiman_rows())
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=[])):
        await find_filed_document(TID, "Gardens Handiman")
    flt = _or_filters(mock_client)[0]
    assert "fields->>supplier.ilike.%Gardens Handiman%" in flt
    assert "fields->>payee_name.ilike" in flt and "filename.ilike" in flt


@pytest.mark.asyncio
async def test_known_alias_returns_every_invoice_with_a_server_side_total():
    suppliers = [{"name": "GARDENS HANDIMAN CENTRE", "aliases": ["Jack Hammer"]}]
    mock_client = _mock_sequential([], _handiman_rows())
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=suppliers)):
        res = await find_filed_document(TID, "all jack hammer invoices")

    assert res["status"] == "found"
    assert res["total_matches"] == 13
    assert res["matches_with_amount"] == 13
    assert res["total_amount_cents"] == 2027800
    assert res["total_amount"] == "R20,278.00"
    assert res["resolved_supplier"] == "GARDENS HANDIMAN CENTRE"
    # The alias search is restricted to the counterparty fields, under every name.
    alias_filter = _or_filters(mock_client)[1]
    assert "filename.ilike" not in alias_filter
    assert "fields->>supplier.ilike.%GARDENS HANDIMAN CENTRE%" in alias_filter
    assert "fields->>supplier.ilike.%Jack Hammer%" in alias_filter


@pytest.mark.asyncio
async def test_semantic_hits_agreeing_on_a_party_are_re_searched_exactly():
    chunks = [{"filename": "POS-1.pdf", "text": "Jack Hammer account sale", "score": 0.8},
              {"filename": "POS-2.pdf", "text": "Jack Hammer account sale", "score": 0.7}]
    crossref = [{"filename": "POS-1.pdf", "category": "Invoice",
                 "fields": {"supplier": "GARDENS HANDIMAN CENTRE", "total_cents": 69700}},
                {"filename": "POS-2.pdf", "category": "Invoice",
                 "fields": {"supplier": "GARDENS HANDIMAN CENTRE", "total_cents": 9200}}]
    mock_client = _mock_sequential([], _handiman_rows(), crossref_rows=crossref)
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=[])), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=chunks)
        res = await find_filed_document(TID, "Jack Hammer")

    assert res["match_type"] == "resolved_via_knowledge_base"
    assert res["total_matches"] == 13
    assert res["total_amount"] == "R20,278.00"
    assert res["resolved_supplier"] == "GARDENS HANDIMAN CENTRE"
    assert "confirm" in res["note"]


@pytest.mark.asyncio
async def test_semantic_hits_kept_when_the_party_re_search_finds_nothing():
    chunks = [{"filename": "a.pdf", "text": "excerpt", "score": 0.8}]
    crossref = [{"filename": "a.pdf", "category": "Invoice",
                 "fields": {"supplier": "Acme", "total_cents": 1000}}]
    mock_client = _mock_sequential([], [], crossref_rows=crossref)
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=[])), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=chunks)
        res = await find_filed_document(TID, "something")
    assert res["match_type"] == "knowledge_base"
    assert res["matches"][0]["party"] == "Acme"


@pytest.mark.asyncio
async def test_total_excludes_and_flags_matches_without_an_amount():
    rows = _handiman_rows()[:2]
    rows[1]["fields"] = {"supplier": "GARDENS HANDIMAN CENTRE"}
    with patch("vula.commerce.service._client", return_value=_mock_sequential(rows)), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=[])):
        res = await find_filed_document(TID, "Gardens")
    assert res["total_amount_cents"] == 18900
    assert res["matches_with_amount"] == 1
    assert "NOT in" in res["note"]


@pytest.mark.asyncio
async def test_long_result_lists_are_capped_but_totalled_in_full():
    rows = [{"id": f"d{i}", "filename": f"f{i}.pdf", "category": "Invoice", "summary": "",
             "created_at": "2026-09-01", "fields": {"total_cents": 100}} for i in range(40)]
    with patch("vula.commerce.service._client", return_value=_mock_sequential(rows)), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=[])):
        res = await find_filed_document(TID, "f")
    assert len(res["matches"]) == 30
    assert res["total_matches"] == 40
    assert res["total_amount_cents"] == 4000


@pytest.mark.parametrize("fields,expected", [
    ({"total_cents": 18900}, 18900),
    ({"amount_cents": 9200}, 9200),
    ({"amount": "R7,500.00"}, 750000),
    ({"amount": 12.5}, 1250),
    ({"amount": "n/a"}, None),
    ({}, None),
])
def test_document_amount_cents(fields, expected):
    assert _document_amount_cents(fields) == expected


def test_dominant_party_prefers_the_majority_then_the_best_ranked():
    assert _dominant_party([{"party": "B"}, {"party": "A"}, {"party": "a"}]) == "A"
    assert _dominant_party([{"party": "B"}, {"party": "A"}]) == "B"
    assert _dominant_party([{"excerpt": "x"}]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected", [
    ("all jack hammer invoices", True),
    ("Jack Hammer", True),
    ("Gardens Handiman Centre (Pty) Ltd", True),
    ("Gardens Handyman Centre", True),       # fuzzy, above the auto-apply bar
    ("hammer drill price", False),
    ("", False),
])
async def test_resolve_supplier_names(query, expected):
    suppliers = [{"name": "GARDENS HANDIMAN CENTRE", "aliases": ["Jack Hammer"]},
                 {"name": "Builders Warehouse", "aliases": []}]
    with patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=suppliers)):
        names = await _resolve_supplier_names(TID, query)
    assert (names == ["GARDENS HANDIMAN CENTRE", "Jack Hammer"]) is expected


@pytest.mark.asyncio
async def test_resolve_supplier_names_fails_open():
    with patch("vula.commerce.service.list_suppliers", AsyncMock(side_effect=RuntimeError("db"))):
        assert await _resolve_supplier_names(TID, "Jack Hammer") == []


@pytest.mark.asyncio
async def test_refunds_are_subtracted_not_added():
    # Real DIGG row: a refund filed as category Invoice with a POSITIVE total_cents.
    rows = _handiman_rows()[:1] + [{
        "id": "r1", "filename": "POS Account Refund 21-366230.pdf", "category": "Invoice",
        "summary": "", "created_at": "2026-09-22", "status": "pending_project",
        "fields": {"supplier": "GARDENS HANDIMAN CENTRE", "total_cents": 95400}}]
    with patch("vula.commerce.service._client", return_value=_mock_sequential(rows)), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=[])):
        res = await find_filed_document(TID, "Gardens")
    assert res["total_amount_cents"] == 18900 - 95400
    assert res["matches"][1]["is_refund"] is True
    assert "SUBTRACTED" in res["note"]


# ── materials roll-up (2026-09-23) ───────────────────────────────────────────────
# DIGG wants "what materials have we bought from X", not only the Rand total. Line items are
# already extracted on every invoice; this checks they're aggregated server-side.

from vula.commerce.service import _aggregate_line_items  # noqa: E402


def _li(desc, qty, unit, total=None):
    return {"description": desc, "quantity": qty, "unit_price_cents": unit,
            "total_cents": total if total is not None else (None if unit is None else qty * unit)}


def test_materials_merge_the_same_item_across_lines_and_invoices():
    # Real digg-demo shapes: ISOTHERM on two invoices, masonry nails repeated within one.
    rows = [
        {"id": "a", "filename": "POS Account Sale 24-223853.pdf", "fields": {"line_items": [
            _li("ISOTHERM 100MM*1200MM*6M 7.2m2", 5, 74500),
            _li("RUBBLE BAG WOVEN", 20, 750)]}},
        {"id": "b", "filename": "POS Account Sale 23-244698.pdf", "fields": {"line_items": [
            _li("ISOTHERM 100MM*1200MM*6M 7.2m2", 1, 74500),
            _li("MASONRY NAILS 3.0X50 P25", 1, 2300),
            _li("masonry nails 3.0x50 p25", 1, 2300)]}},
    ]
    items, distinct = _aggregate_line_items(rows)
    assert distinct == 3
    top = items[0]
    assert top["description"] == "ISOTHERM 100MM*1200MM*6M 7.2m2"
    assert top["quantity"] == 6 and top["spend_cents"] == 447000 and top["documents"] == 2
    assert top["spend"] == "R4,470.00"
    nails = next(i for i in items if i["description"].startswith("MASONRY"))
    assert nails["quantity"] == 2 and nails["spend_cents"] == 4600 and nails["documents"] == 1


def test_materials_subtract_refunded_items():
    rows = [
        {"id": "a", "filename": "POS Account Sale 1.pdf",
         "fields": {"line_items": [_li("SAND PER BAG ACC", 20, 3100)]}},
        {"id": "r", "filename": "POS Account Refund 2.pdf",
         "fields": {"line_items": [_li("SAND PER BAG ACC", 5, 3100)]}},
    ]
    items, _ = _aggregate_line_items(rows)
    assert items[0]["quantity"] == 15 and items[0]["spend_cents"] == 15 * 3100


def test_materials_flag_unpriced_or_unquantified_lines():
    rows = [{"id": "a", "fields": {"line_items": [
        {"description": "CUTTING CHARGE WOOD", "quantity": None, "total_cents": 2000},
        {"description": "BRICK TROWEL", "quantity": 1, "unit_price_cents": None,
         "total_cents": None},
        "not-a-dict", {"description": ""}]}}]
    items, distinct = _aggregate_line_items(rows)
    assert distinct == 2
    by = {i["description"]: i for i in items}
    assert by["CUTTING CHARGE WOOD"]["quantity_incomplete"] is True
    assert by["BRICK TROWEL"]["spend_incomplete"] is True


@pytest.mark.asyncio
async def test_search_result_carries_the_materials_roll_up():
    rows = _handiman_rows()[:2]
    rows[0]["fields"]["line_items"] = [_li("PINE ROUGH 38X38 3M", 2, 4700)]
    rows[1]["fields"]["line_items"] = [_li("PINE ROUGH 38X38 3M", 3, 4700)]
    with patch("vula.commerce.service._client", return_value=_mock_sequential(rows)), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=[])):
        res = await find_filed_document(TID, "Gardens")
    assert res["materials"] == [{"description": "PINE ROUGH 38X38 3M", "quantity": 5,
                                 "spend": "R235.00", "spend_cents": 23500, "documents": 2}]
    assert res["materials_distinct"] == 1
    assert "materials" in res["note"]


@pytest.mark.asyncio
async def test_no_materials_key_when_no_line_items():
    with patch("vula.commerce.service._client", return_value=_mock_sequential(_handiman_rows()[:1])), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=[])):
        res = await find_filed_document(TID, "Gardens")
    assert "materials" not in res


def test_materials_flag_a_likely_misread_quantity():
    # Real digg-demo: sand at R31/bag everywhere, but one line read as qty 1 @ R465.
    rows = [{"id": "a", "fields": {"line_items": [_li("SAND PER BAG ACC", 20, 3100)]}},
            {"id": "b", "fields": {"line_items": [_li("SAND PER BAG ACC", 1, 46500)]}},
            {"id": "c", "fields": {"line_items": [_li("CEMENT 50KG", 3, 15900),
                                                  _li("CEMENT 50KG", 1, 15900)]}}]
    items, _ = _aggregate_line_items(rows)
    by = {i["description"]: i for i in items}
    assert by["SAND PER BAG ACC"]["unit_price_varies"] is True
    assert "unit_price_varies" not in by["CEMENT 50KG"]


# ── 2026-09-23 live retest: nickname bridged through the account application ─────
# Deployed #63, then "Need all jack hammer invoices and a summary of what was spent" (routed
# correctly to email_admin) answered "one invoice from Jack Hammer, R7571.44" — a SOLID CAPE
# invoice. "Jack Hammer" is on no invoice; the only link is the filed "ACCOUNT APPLICATION -
# Jack Hammer's COD account.pdf" ("...a COD account with Handiman Centre"), which has no
# supplier field, so neither the SQL hit nor the semantic hits named a party to re-search.

from vula.commerce.service import _bridge_party, _core_search_term  # noqa: E402

_COD_DOC = {"id": "cod", "filename": "ACCOUNT APPLICATION - Jack Hammer's COD account.pdf",
            "category": "General Document", "created_at": "2026-09-01", "fields": {},
            "summary": "This document is an account application form for a COD account with "
                       "Handiman Centre, requesting details about the applicant"}
_PARTIES = ["GARDENS HANDIMAN CENTRE", "Gardens Handiman Centre", "SOLID CAPE (PTY) LTD"]


@pytest.mark.parametrize("query,expected", [
    ("Need all jack hammer invoice and summary of what was spent", "jack hammer"),
    ("jack hammer invoices", "jack hammer"),
    ("Jack Hammer", "Jack Hammer"),
    ("invoice 22-190910", "22-190910"),
    ("all invoices", ""),
])
def test_core_search_term_strips_request_filler(query, expected):
    assert _core_search_term(query) == expected


def test_bridge_party_links_the_nickname_through_the_account_application():
    party, via = _bridge_party("jack hammer", [_COD_DOC], _PARTIES)
    assert _norm(party) == "gardens handiman centre"
    assert via == _COD_DOC["filename"]


def test_bridge_party_needs_the_doc_to_name_the_queried_term():
    other = dict(_COD_DOC, filename="Handiman Centre price list.pdf",
                 summary="Price list from Handiman Centre")
    assert _bridge_party("jack hammer", [other], _PARTIES) is None


def test_bridge_party_refuses_to_guess_between_two_suppliers():
    doc = dict(_COD_DOC, summary="Jack Hammer account with Handiman Centre and Solid Cape")
    assert _bridge_party("jack hammer", [doc], _PARTIES + ["Solid Cape"]) is None


def _norm(s):
    from vula.commerce.service import _norm_name
    return _norm_name(s)


@pytest.mark.asyncio
async def test_sql_hit_on_the_account_application_bridges_to_every_invoice():
    mock_client = _mock_sequential([_COD_DOC], _handiman_rows())
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=[])), \
         patch("vula.commerce.service._known_parties", return_value=_PARTIES):
        res = await find_filed_document(TID, "jack hammer invoices")
    assert res["match_type"] == "resolved_via_knowledge_base"
    assert res["total_matches"] == 13 and res["total_amount"] == "R20,278.00"
    assert "Jack Hammer's COD account" in res["note"] and "confirm" in res["note"]
    # Both the raw query and its core were searched.
    first = _or_filters(mock_client)[0]
    assert "%jack hammer invoices%" in first and "%jack hammer%" in first


@pytest.mark.asyncio
async def test_semantic_hits_bridge_through_the_account_application():
    # The real failure: semantic hits were the COD application + a Solid Cape invoice, none
    # with a party, so the model reported Solid Cape's R7,571.44 as Jack Hammer's.
    chunks = [{"filename": _COD_DOC["filename"], "text": "COD account application", "score": 0.7},
              {"filename": "00090117.pdf", "text": "SOLID CAPE tax invoice 7,571.44", "score": 0.6}]
    crossref = [{"filename": _COD_DOC["filename"], "category": "General Document",
                 "summary": _COD_DOC["summary"], "fields": {}},
                {"filename": "00090117.pdf", "category": "Invoice",
                 "summary": "Tax invoice from SOLID CAPE (PTY) LTD, ZAR 7,571.44", "fields": {}}]
    mock_client = _mock_sequential([], _handiman_rows(), crossref_rows=crossref)
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=[])), \
         patch("vula.commerce.service._known_parties", return_value=_PARTIES), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=chunks)
        res = await find_filed_document(
            TID, "Need all jack hammer invoice and summary of what was spent")
    assert res["match_type"] == "resolved_via_knowledge_base"
    assert res["total_amount"] == "R20,278.00"
    assert res["resolved_supplier"] == "GARDENS HANDIMAN CENTRE"


@pytest.mark.asyncio
async def test_unresolved_knowledge_base_hits_warn_against_misattribution():
    chunks = [{"filename": "00090117.pdf", "text": "SOLID CAPE tax invoice", "score": 0.6}]
    mock_client = _mock_sequential([], crossref_rows=[])
    with patch("vula.commerce.service._client", return_value=mock_client), \
         patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=[])), \
         patch("vula.commerce.service._known_parties", return_value=_PARTIES), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline:
        mock_pipeline.return_value.query = AsyncMock(return_value=chunks)
        res = await find_filed_document(TID, "jack hammer invoices")
    assert res["match_type"] == "knowledge_base"
    assert "NOT confirmed" in res["note"]


@pytest.mark.parametrize("party", ["City of Cape Town", "Caisson (Pty) Ltd T/A Coastal Hire",
                                    "BO-KAAP SERVICE STATION"])
def test_bridge_party_ignores_address_and_trading_as_words(party):
    # Real digg-demo party names: "cape town" / "t a" / "service station" must not link a
    # document that merely mentions an address or a trading-as line.
    doc = dict(_COD_DOC, summary="Jack Hammer account, Cape Town, T/A something, service station")
    assert _bridge_party("jack hammer", [doc], [party]) is None
