"""Booking a supplier's invoice, 3 Nov 2026 — three things that went wrong on one invoice.

An electrician's final invoice (R45,852.00, no VAT on it, the client pays the electrician directly)
was sent to the business's WhatsApp, with the project name as a separate message two seconds later:
- the project name ran first and filed an older document that had been waiting since the morning;
- the invoice was booked as a draft bill of R52,729.80 — 15% VAT added for a supplier whose only
  tax number isn't a VAT number;
- it was booked as the business's bill at all, though the client pays that supplier directly.
Made-up suppliers and figures throughout.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import service
from vula.commerce.service import client_pays_directly, vat_inclusive, vat_registered

TID = "seaview-demo"


# ── VAT only for a VAT-registered supplier ──

def test_vat_registration_from_tax_numbers():
    assert vat_registered("4140213523") is True
    assert vat_registered("VAT No: 4140 213 523") is True
    assert vat_registered("IE028901") is False              # a non-VAT tax / registration number
    assert vat_registered("2004/059308/23") is False         # a company registration number
    assert vat_registered("IE028901", "4290114281") is True  # the supplier record has the VAT number
    assert vat_registered(None, "") is None                  # nothing to go on


def test_no_vat_added_for_a_supplier_that_is_not_registered():
    assert vat_inclusive(4585200, 0, True, False) == (4585200, 0)
    assert vat_inclusive(4585200, 0, True, None) == (5272980, 687780)      # unknown: as before
    assert vat_inclusive(208450, 0, True, True) == (239717, 31267)


# ── the client pays this supplier directly ──

class _Q:
    def __init__(self, rows): self.rows, self.f = rows, []
    def select(self, *a): return self
    def limit(self, *a): return self
    def eq(self, k, v): self.f.append((k, v)); return self
    def execute(self): return SimpleNamespace(data=[r for r in self.rows if all(r.get(k) == v for k, v in self.f)])


DIRECT = [
    {"tenant_id": TID, "project": "Seaview", "supplier": "Sam / Brightspark (electrical)", "amount_cents": 1585200, "status": "to_pay"},
    {"tenant_id": TID, "project": "Seaview", "supplier": "Builders Warehouse", "amount_cents": 50000, "status": "paid"},
    {"tenant_id": TID, "project": "Oak Street", "supplier": "Thandi Joinery", "amount_cents": 900000, "status": "to_pay"},
    {"tenant_id": "other", "project": "Seaview", "supplier": "Tile Co", "amount_cents": 1, "status": "to_pay"},
]


@pytest.fixture
def direct(monkeypatch):
    db = SimpleNamespace(table=lambda name: _Q(DIRECT))
    monkeypatch.setattr(service, "_client", lambda: db)


def test_a_supplier_the_client_still_pays_is_matched_by_its_own_name(direct):
    hit = client_pays_directly(TID, "Brightspark Electrical Services")
    assert hit == {"project": "Seaview", "supplier": "Sam / Brightspark (electrical)", "to_pay_cents": 1585200}
    assert client_pays_directly(TID, "Brightspark Electrical", "Seaview")["project"] == "Seaview"
    assert client_pays_directly(TID, "Brightspark Electrical", "Oak Street") is None   # other project


def test_trade_words_paid_rows_and_other_businesses_never_match(direct):
    assert client_pays_directly(TID, "Atlantic Electrical Services") is None   # only "electrical" in common
    assert client_pays_directly(TID, "Builders Warehouse Tableview") is None   # paid once, not still to pay
    assert client_pays_directly(TID, "Tile Co") is None                         # another business's row
    assert client_pays_directly(TID, "") is None


def _commit(extracted, *, direct_hit=None, supplier=None):
    captured = {}

    def table(name):
        t = MagicMock()
        if name == "commerce_invoices":
            def insert(row):
                captured["row"] = row
                m = MagicMock()
                m.execute.return_value = MagicMock(data=[row])
                return m
            t.insert.side_effect = insert
        return t
    db = MagicMock()
    db.table.side_effect = table
    db.rpc.return_value.execute.return_value = MagicMock(data=7)
    pipeline = MagicMock()
    pipeline.ingest_text = AsyncMock(return_value=MagicMock(chunks_stored=0))
    sup = supplier or {"id": "s1", "name": extracted["supplier"], "payment_terms_days": 30}
    with patch("vula.commerce.service._client", return_value=db), \
            patch("vula.commerce.service.match_supplier",
                  new=AsyncMock(return_value={"supplier": sup, "tier": "exact_name", "confidence": 1.0,
                                              "auto_apply": True})), \
            patch("vula.commerce.service.own_business_names", new=AsyncMock(return_value=([], []))), \
            patch("vula.commerce.service.client_pays_directly", return_value=direct_hit), \
            patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipeline):
        import asyncio
        out = asyncio.run(service.commit_inbound_document(TID, extracted, auto_commit=True, source="whatsapp"))
    return out, captured.get("row")


INVOICE = {"doc_type": "invoice", "supplier": "Brightspark Electrical Services", "date": "2026-10-30",
           "document_number": "INV00051", "total_cents": 4585200, "vat_cents": None,
           "amounts_exclude_vat": True, "tax_id": "IE028901", "line_items": []}


def test_an_invoice_the_client_pays_directly_is_not_booked_as_the_business_bill():
    hit = {"project": "Seaview", "supplier": "Sam / Brightspark (electrical)", "to_pay_cents": 1585200}
    out, row = _commit(INVOICE, direct_hit=hit)
    assert row is None and out["committed"] is False
    assert out["client_pays_directly"] == hit and "not booked as your bill" in out["reason"]


def test_a_supplier_without_a_vat_number_is_booked_as_printed():
    out, row = _commit(INVOICE)
    assert (row["subtotal_cents"], row["vat_cents"], row["total_cents"]) == (4585200, 0, 4585200)


def test_a_vat_registered_supplier_still_gets_vat_added():
    out, row = _commit({**INVOICE, "tax_id": "4140213523"})
    assert row["total_cents"] == 5272980


# ── the project name sent right after a document answers THAT document ──

def test_a_project_name_waits_for_the_document_still_being_read():
    from vula.api import whatsapp as wa
    key = (TID, "27820000001")
    wa._SENDER_LOCKS.pop(key, None)
    assert wa._caption_waits_for_document(TID, "27820000001", "Seaview") is False      # nothing in flight
    wa._SENDER_LOCKS[key] = [object(), 1]                                              # a document is being read
    try:
        assert wa._caption_waits_for_document(TID, "27820000001", "Seaview") is True
        assert wa._caption_waits_for_document(TID, "27820000001", "what do we owe Tile Co?") is False
        assert wa._caption_waits_for_document(TID, "27820000002", "Seaview") is False  # someone else
    finally:
        wa._SENDER_LOCKS.pop(key, None)
