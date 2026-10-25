"""Richer invoice details (2026-10-25): identifiers verified against the page, the business's
own VAT number never reported as a supplier's, and a re-read only ever ADDS details."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import reread
from vula.commerce.extraction_quality import totals_disagree, valid_sa_vat, verify_identifiers

PAGE = """SOLID CAPE (PTY) LTD   VAT No: 4560123456   Tel 021 555 0101
TAX INVOICE  INV07180   Your order: PO-2231   Customer: Aweh Belekker t/a DIGG  VAT 4290114281
Banking details: FNB  Acc: 62001234567  Branch 250655   Terms: 30 days"""


def test_identifiers_on_the_page_are_kept():
    f = verify_identifiers({"document_number": "INV07180", "tax_id": "4560123456",
                            "customer_vat_number": "4290114281", "order_number": "PO-2231",
                            "supplier_bank": {"bank_name": "FNB", "account_number": "6200 1234 567",
                                              "branch_code": "250655"}}, PAGE)
    assert "_dropped_identifiers" not in f
    assert f["supplier_bank"]["account_number"] == "6200 1234 567"


def test_a_number_not_on_the_page_is_dropped_never_stored():
    f = verify_identifiers({"document_number": "INV99999",
                            "supplier_bank": {"bank_name": "FNB", "account_number": "62009999999"}}, PAGE)
    assert "document_number" not in f and "supplier_bank" not in f
    assert {d["field"] for d in f["_dropped_identifiers"]} == {"document_number", "supplier_bank.account_number"}


@pytest.mark.parametrize("v,ok", [("4560123456", True), ("456 012 3456", True), ("41202400", False),
                                  ("5560123456", False), ("", False)])
def test_sa_vat_format(v, ok):
    assert valid_sa_vat(v) is ok


def test_a_malformed_vat_number_is_dropped_and_supplier_equal_customer_is_caught():
    assert "tax_id" not in verify_identifiers({"tax_id": "41202400"}, "VAT 41202400")
    f = verify_identifiers({"tax_id": "4560123456", "customer_vat_number": "4560123456"}, PAGE)
    assert "customer_vat_number" not in f and f["tax_id"] == "4560123456"


def test_subtotal_vat_total_check():
    assert totals_disagree({"subtotal_cents": 100000, "vat_cents": 15000, "total_cents": 125000})
    assert not totals_disagree({"subtotal_cents": 100000, "vat_cents": 15000, "total_cents": 115000})
    assert not totals_disagree({"total_cents": 115000})


# ── Re-reading filed invoices ──────────────────────────────────────────────────────────────

ROW = {"id": "r1", "category": "Invoice", "doc_id": "d1",
       "fields": {"supplier": "SOLID CAPE (PTY) LTD", "date": "2026-10-19", "total_cents": 120461,
                  "line_items": [{"description": "Cross tee"}]}}


def test_a_re_read_adds_only_the_new_details():
    analysis = {"category": "Invoice", "fields": {
        "supplier": "Solid Cape", "date": "2026-10-20", "total_cents": 120461, "line_items": [],
        "document_number": "INV07180", "order_number": "PO-2231", "tax_id": "4560123456",
        "supplier_bank": {"bank_name": "FNB", "account_number": "62001234567"}, "customer": None}}
    add = reread.enrichment(ROW, analysis)
    assert add == {"document_number": "INV07180", "order_number": "PO-2231", "tax_id": "4560123456",
                   "supplier_bank": {"bank_name": "FNB", "account_number": "62001234567"}}


@pytest.mark.parametrize("analysis", [
    {"category": "Quote / Estimate", "fields": {"document_number": "Q1", "total_cents": 120461}},
    {"category": "Invoice", "fields": {"document_number": "X", "total_cents": 99900}},   # another document
    None])
def test_a_different_read_adds_nothing(analysis):
    assert reread.enrichment(ROW, analysis) == {}


@pytest.mark.asyncio
async def test_enrich_details_writes_the_additions_and_relabels(monkeypatch):
    updates = []

    class _T:
        def __init__(self):
            self.payload = None

        def update(self, p):
            self.payload = p
            return self

        def eq(self, *a):
            return self

        def execute(self):
            updates.append(self.payload)

    monkeypatch.setattr(reread, "enrich_candidates", lambda t, n: [{**ROW, "file_url": "u", "filename": "00097750.pdf"}])
    monkeypatch.setattr(reread, "_download", AsyncMock(return_value=b"%PDF"))
    monkeypatch.setattr(reread.service, "_client", lambda: type("C", (), {"table": lambda s, n: _T()})())
    analysis = {"category": "Invoice", "fields": {"total_cents": 120461, "document_number": "INV07180"}}
    with patch("vula.api.whatsapp._analyze_document", new=AsyncMock(return_value=analysis)), \
         patch("vula.ingestion.pipeline.DocumentParser.parse", new=AsyncMock(return_value=[(1, PAGE)])), \
         patch("vula.kb_context.forget_labels", new=AsyncMock()) as forget:
        out = await reread.enrich_details("digg-demo")
    assert out["enriched"] == 1 and out["added"] == {"document_number": 1}
    assert updates[0]["fields"]["document_number"] == "INV07180"
    assert updates[0]["fields"]["total_cents"] == 120461 and "_enriched_at" in updates[0]["fields"]
    forget.assert_awaited_once_with("digg-demo", "d1")


# ── Answers read the new details ───────────────────────────────────────────────────────────

def test_the_business_own_vat_number_is_never_given_as_a_suppliers():
    from vula import kb_records as kr
    rows = [{"fields": {"supplier": s, "tax_id": "4120206471"}} for s in ("A Co", "B Co", "C Co")]
    rows.append({"fields": {"supplier": "Atlantis", "tax_id": "4200315184"}})
    own = kr.own_vat_numbers(rows)
    assert own == {"4120206471"}
    assert kr.field_reply({"category": "Invoice", "fields": {"supplier": "A Co", "tax_id": "4120206471"}},
                          "vat_no", own) is None
    assert "4200315184" in kr.field_reply({"category": "Invoice", "fields": rows[3]["fields"]}, "vat_no", own)


def test_bank_details_from_the_new_shape():
    from vula import kb_records as kr
    row = {"category": "Invoice", "filename": "x.pdf",
           "fields": {"supplier": "Solid Cape", "supplier_bank": {"bank_name": "FNB", "account_name": "Solid Cape",
                                                                 "account_number": "62001234567", "branch_code": "250655"}}}
    out = kr.field_reply(row, "bank")
    assert "FNB, account 62001234567 (Solid Cape), branch code 250655" in out and "confirm bank details" in out


@pytest.mark.asyncio
async def test_nightly_enriches_before_labelling(monkeypatch):
    from config import settings
    from vula import kb_maintenance as km
    monkeypatch.setattr(settings, "kb_context_nightly", True)
    monkeypatch.setattr(settings, "kb_enrich_per_night", 60)
    monkeypatch.setattr(settings, "kb_ctx_baseline", "")
    jobs = []

    async def run_job(job, by="boot"):
        jobs.append(job)
        return {"by_tenant": {}}
    monkeypatch.setattr(km, "run_job", run_job)
    monkeypatch.setattr(km, "_last_done", lambda job: None)
    await km.context_nightly(datetime(2026, 10, 25, 23, tzinfo=timezone.utc))
    assert jobs == ["enrich_documents", "supplier_details", "contextualize"]
