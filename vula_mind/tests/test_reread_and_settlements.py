"""Re-reading documents missing data, and card settlement summaries in bank reconciliation
(2026-09-27).

Production had ~50 PDFs stuck as "Email attachment" (their first analysis failed — Off the
Hook's daily card settlement summaries among them) and ~120 invoices/quotes with no amount or
supplier. reread.py analyses the stored copy again; only a read that adds something replaces
what's filed, and nothing is booked. A "Settlement Statement"'s net payout then explains the
matching bank credit as card sales — allocation only, nothing marked paid.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_stock_movements import FakeDB
from vula.commerce import bank_rec, reread, service

TID = "off-the-hook"


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({"vula_filed_documents": [
        {"id": "d1", "tenant_id": TID, "category": "Email attachment", "filename": "170926_000000103611117_PDF.pdf",
         "file_url": "https://s/d1.pdf", "fields": {}, "summary": "Your settlement summary"},
        {"id": "d2", "tenant_id": TID, "category": "Invoice", "filename": "Inv_51934.pdf",
         "file_url": "https://s/d2.pdf", "fields": {"supplier": None}},
        {"id": "d3", "tenant_id": TID, "category": "Invoice", "filename": "ok.pdf", "file_url": "https://s/d3.pdf",
         "fields": {"supplier": "Sea Harvest", "date": "2026-09-10", "total_cents": 120000}},
        {"id": "d4", "tenant_id": TID, "category": "Email attachment", "filename": "data.xml",
         "file_url": "https://s/d4.xml", "fields": {}},
        {"id": "d5", "tenant_id": TID, "category": "Email attachment", "filename": "nocopy.pdf",
         "file_url": None, "fields": {}},
        {"id": "d6", "tenant_id": "digg-demo", "category": "Email attachment", "filename": "x.pdf",
         "file_url": "https://s/d6.pdf", "fields": {}},
    ]})
    monkeypatch.setattr(service, "_client", lambda: fake)
    return fake


def test_candidates_are_this_tenants_unread_pdfs_with_a_stored_copy(db):
    assert [r["id"] for r in reread.candidates(TID)] == ["d1", "d2"]


@pytest.mark.asyncio
async def test_reread_fills_in_what_was_missing_and_books_nothing(db, monkeypatch):
    reads = {
        "170926_000000103611117_PDF.pdf": {"category": "Settlement Statement", "summary": "Yoco payout",
                                           "fields": {"date": "2026-09-17", "net_cents": 123450}},
        "Inv_51934.pdf": {"category": "Invoice", "summary": "", "fields": {}},   # still nothing
    }

    async def analyze(tid, filename, path, text=None):
        return reads[filename]
    monkeypatch.setattr("vula.api.whatsapp._analyze_document", analyze)
    monkeypatch.setattr(reread, "_download", AsyncMock(return_value=b"%PDF-1.4"))

    class _Parser:
        async def parse(self, path):
            return [(1, "Settlement summary 1 234.50")]

    class _Pipe:
        def __init__(self, tenant_id):
            self.parser = _Parser()
    monkeypatch.setattr("vula.ingestion.pipeline.VulaIngestionPipeline", _Pipe)
    monkeypatch.setattr("vula.commerce.stock_sheet.persist_if_stock_sheet", lambda *a: None)
    commit = AsyncMock()
    monkeypatch.setattr(service, "commit_inbound_document", commit, raising=False)

    out = await reread.reread_missing(TID)
    assert (out["total"], out["fixed"], out["failed"]) == (2, 1, 0)
    assert out["categories"] == {"Settlement Statement": 1}
    rows = {r["id"]: r for r in db.tables["vula_filed_documents"]}
    assert rows["d1"]["category"] == "Settlement Statement" and rows["d1"]["fields"]["net_cents"] == 123450
    # Nothing found: the fields stay as they were, stamped so the daily pass doesn't retry it.
    assert rows["d2"]["category"] == "Invoice" and rows["d2"]["fields"]["supplier"] is None
    assert rows["d2"]["fields"]["_reread_at"]
    assert rows["d1"]["fields"]["_reread_at"]
    commit.assert_not_awaited()
    assert reread.status_for(TID)["running"] is False


# ── settlements in bank rec ───────────────────────────────────────────────────

SETTLEMENTS = [{"id": "s1", "net_cents": 123450, "date": "2026-09-17", "provider": "Yoco"}]


def test_a_payout_matches_the_credit_a_few_days_later_only():
    t = {"date": "2026-09-18", "amount_cents": 123450}
    assert bank_rec._match_settlement(t, SETTLEMENTS)["id"] == "s1"
    assert bank_rec._match_settlement({**t, "date": "2026-09-16"}, SETTLEMENTS) is None   # before it
    assert bank_rec._match_settlement({**t, "date": "2026-09-30"}, SETTLEMENTS) is None   # too late
    assert bank_rec._match_settlement({**t, "amount_cents": 123451}, SETTLEMENTS) is None
    two = SETTLEMENTS + [{"id": "s2", "net_cents": 123450, "date": "2026-09-16", "provider": "Yoco"}]
    assert bank_rec._match_settlement(t, two) is None                                     # ambiguous


def test_settlements_with_unverified_figures_are_not_used():
    rows = [{"id": "a", "fields": {"date": "2026-09-17", "net_cents": 100}},
            {"id": "b", "fields": {"date": "2026-09-17", "net_cents": 200,
                                   "_unverified_figures": [{"field": "net_cents"}]}},
            {"id": "c", "fields": {"net_cents": 300}}]
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.gte.return_value \
        .limit.return_value.execute.return_value.data = rows
    assert [s["id"] for s in bank_rec._load_settlements(db, TID)] == ["a"]


@pytest.mark.asyncio
async def test_reconcile_allocates_the_payout_credit_to_sales_without_marking_anything_paid():
    txns = [{"date": "2026-09-18", "description": "YOCO SETTLEMENT", "amount_cents": 123450,
             "direction": "in", "balance_cents": None, "reference": None}]
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.in_.return_value.limit.return_value \
        .execute.return_value.data = []
    upd_inv = AsyncMock()
    with (
        patch.object(bank_rec, "_client", return_value=db),
        patch.object(bank_rec, "_load_settlements", return_value=SETTLEMENTS),
        patch("vula.commerce.service._client", return_value=db),
        patch("vula.commerce.accounting.ensure_chart", return_value=[{"code": "sales"}, {"code": "other_income"}]),
        patch("vula.commerce.accounting.categorize_batch",
              new=AsyncMock(return_value=[{"account_code": "other_income", "source": "default"}])),
        patch("vula.commerce.accounting.vat_for", return_value=0),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.service.update_invoice_status", new=upd_inv),
        patch("vula.commerce.service.update_order_status", new=AsyncMock()),
    ):
        result = await bank_rec.reconcile(TID, txns)
    assert result["card_settlements"] == 1
    assert result["unmatched_credits"] == 0 and result["needs_input"] == 0
    row = [c.args[0] for c in db.table.return_value.upsert.call_args_list
           if isinstance(c.args[0], dict) and c.args[0].get("amount_cents") == 123450][0]
    assert (row["account_code"], row["categorized_by"]) == ("sales", "settlement")
    upd_inv.assert_not_awaited()
