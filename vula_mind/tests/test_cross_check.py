"""Cross-check documents ↔ books ↔ bank, and VAT in vs out (2026-09-28).

Production the same day: digg-demo had 64 and off-the-hook 57 filed bills/quotes with a
verified total that never reached the books; 3 of 372 and 2 of 742 bank lines were matched to
anything; an unregistered business's VAT report said only "not registered". Shapes below are
DIGG's (HPC DOORS paid from the bank with no supplier name on the line, R1.5M of certificates).
"""
import pytest

from tests.test_job_costing import FakeDB as _Base, _Q as _BaseQ
from vula.commerce import cross_check, service

TID = "digg-demo"


class _Lte:
    def __init__(self, v):
        self.v = v

    def __eq__(self, other):
        return other is not None and str(other) <= self.v


class _Q(_BaseQ):
    def lte(self, col, val):
        self.filters.append((col, _Lte(val)))
        return self


class FakeDB(_Base):
    def table(self, name):
        return _Q(self, name)


def _bank(i, date, desc, cents, direction="out", code="cost_of_sales", by="ai", **kw):
    return {"id": f"t{i}", "tenant_id": TID, "txn_date": date, "description": desc, "amount_cents": cents,
            "direction": direction, "account_code": code, "categorized_by": by,
            "match_status": "unmatched", **kw}


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({
        "commerce_suppliers": [{"id": "s1", "tenant_id": TID, "name": "GELMAR", "tax_id": "4123456789"},
                               {"id": "s2", "tenant_id": TID, "name": "Quicktint", "tax_id": None}],
        "commerce_invoices": [
            {"id": "i1", "tenant_id": TID, "direction": "inbound", "doc_type": "invoice", "status": "draft",
             "supplier": "GELMAR", "supplier_id": "s1", "invoice_number": "GEL-88", "issue_date": "2026-07-18",
             "total_cents": 2729248, "vat_cents": 355989},
            {"id": "i2", "tenant_id": TID, "direction": "inbound", "doc_type": "invoice", "status": "draft",
             "supplier": "Quicktint", "supplier_id": "s2", "invoice_number": "QT-1", "issue_date": "2026-07-20",
             "total_cents": 1121363, "vat_cents": 146265},
            {"id": "i3", "tenant_id": TID, "direction": "outbound", "doc_type": "invoice", "status": "sent",
             "customer_name": "Loubser", "invoice_number": "DIGG-INV-0007", "issue_date": "2026-08-01",
             "total_cents": 750000, "vat_cents": 0},
        ],
        "commerce_bank_transactions": [
            _bank(1, "2026-07-20", "HPC DOORS", 2729248, project="HPC Bokaap"),
            _bank(2, "2026-07-21", "WET AND DRY", 437950),
            _bank(3, "2026-07-22", "WET AND DRY", 120000),
            _bank(4, "2026-07-13", "Home", 500000, code="owner_drawings"),
            _bank(5, "2026-07-11", "NELITHO WAGES", 1250000, code="casual_labour"),
            _bank(6, "2026-07-18", "HPC-PC4", 17591613, direction="in", code="sales"),
            _bank(7, "2026-08-04", "Loubser", 750000, direction="in", code="sales"),
            _bank(8, "2026-07-16", "FUJI EXPRESS", 2880, code="fuel"),
            _bank(9, "2026-07-30", "PAY", 150000, code="other_expense", by="default"),
        ],
        "vula_filed_documents": [
            {"id": "d1", "tenant_id": TID, "category": "Invoice", "filename": "Inv_51934.pdf",
             "commerce_invoice_id": None, "project": "HPC Bokaap",
             "fields": {"supplier": "Solid Cape", "date": "2026-08-02", "total_cents": 907833}},
            {"id": "d2", "tenant_id": TID, "category": "Invoice", "commerce_invoice_id": None,
             "fields": {"supplier": "X", "total_cents": 5000, "_unverified_figures": [{"field": "total_cents"}]}},
            {"id": "d3", "tenant_id": TID, "category": "Invoice", "commerce_invoice_id": None, "fields": {}},
            {"id": "d4", "tenant_id": TID, "category": "Invoice", "commerce_invoice_id": "i1",
             "fields": {"total_cents": 2729248}},
        ],
    })
    monkeypatch.setattr(service, "_client", lambda: fake)
    return fake


def test_report_finds_every_gap(db):
    rep = cross_check.report(TID, since="2026-07-01")
    assert [u["id"] for u in rep["unbooked"]] == ["d1"]            # verified total, not booked
    assert rep["unbooked_total_cents"] == 907833
    bills = {b["invoice_number"]: b for b in rep["bills_unpaid"]}
    assert bills["GEL-88"]["likely_payment"]["txn_id"] == "t1"     # HPC DOORS paid GELMAR's bill
    assert bills["QT-1"]["likely_payment"] is None
    groups = {g["counterparty"]: g for g in rep["no_document"]}
    assert set(groups) == {"WET AND DRY", "PAY"}                   # not drawings/wages/fuel<R200/the proposal
    assert groups["WET AND DRY"]["lines"] == 2 and groups["WET AND DRY"]["total_cents"] == 557950
    (sale,) = rep["sales_unpaid"]
    assert sale["likely_payment"]["txn_id"] == "t7"
    assert rep["to_categorise"] == 1 and rep["bank_matched"] == 0
    assert "aren't in the books" in cross_check.summary_text(rep)


def test_vat_if_registered_counts_only_real_tax_invoices(db, monkeypatch):
    monkeypatch.setattr("vula.commerce.accounting.is_vat_registered", lambda tid: False)
    v = cross_check.vat(TID, since="2026-07-01")
    assert v["input_claimable_cents"] == 355989                   # GELMAR has a VAT number
    assert v["input_no_vat_number_cents"] == 146265               # Quicktint doesn't
    assert v["sales_cents"] == 17591613 + 750000
    assert v["output_cents"] == round(17591613 * 0.15) + round(750000 * 0.15)
    assert v["net_cents"] == v["output_cents"] - 355989
    assert "If registered" in v["text"] and "no VAT number on file" in v["text"]
    july = next(m for m in v["months"] if m["month"] == "2026-07")
    assert july["input_claimable"] == 355989 and july["sales"] == 17591613


def test_vat_registered_backs_vat_out_of_receipts_and_flags_the_threshold(db, monkeypatch):
    monkeypatch.setattr("vula.commerce.accounting.is_vat_registered", lambda tid: True)
    v = cross_check.vat(TID, since="2026-07-01")
    assert v["output_cents"] == round(17591613 * 15 / 115) + round(750000 * 15 / 115)
    assert v["over_threshold"] is False                           # registered → no warning
    monkeypatch.setattr("vula.commerce.accounting.is_vat_registered", lambda tid: False)
    monkeypatch.setattr("config.settings.vat_registration_threshold_cents", 10_000_000)
    v = cross_check.vat(TID, since="2026-07-01")
    assert v["over_threshold"] and "compulsory registration threshold" in v["text"]


@pytest.mark.asyncio
async def test_book_unbooked_runs_the_normal_commit_path(db, monkeypatch):
    calls = []

    async def commit(tid, fields, **kw):
        calls.append((fields, kw))
        return {"committed": True}
    monkeypatch.setattr(service, "commit_inbound_document", commit)
    out = await cross_check.book_unbooked(TID)
    assert out == {"booked": 1, "skipped": 0, "candidates": 1}
    fields, kw = calls[0]
    assert fields["doc_type"] == "invoice" and fields["total_cents"] == 907833
    assert kw["filed_document_id"] == "d1" and kw["project"] == "HPC Bokaap"
