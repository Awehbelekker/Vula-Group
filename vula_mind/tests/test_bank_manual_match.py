"""Matching a bank line Vula couldn't match itself (Ian, 6 Oct: "she should be able to allocate
payment and invoice if Vula couldn't"): money in settles one of our invoices, money out a
supplier's bill, only for the amount on the line, and the owner picks from a short list."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from vula.api import commerce as capi
from vula.commerce import ledger

TID = "digg-demo"


class _DB:
    def __init__(self, tables):
        self.tables, self.updates = tables, []

    def table(self, name):
        return _Q(self, name)


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.patch = db, name, [], None

    def select(self, *_a): return self
    def limit(self, *_a): return self
    def order(self, *_a, **_k): return self

    def eq(self, k, v):
        self.f.append(lambda r: r.get(k) == v)
        return self

    def in_(self, k, vals):
        self.f.append(lambda r: r.get(k) in vals)
        return self

    def update(self, patch_):
        self.patch = patch_
        return self

    def execute(self):
        rows = [r for r in self.db.tables.get(self.name, []) if all(f(r) for f in self.f)]
        if self.patch is not None:
            self.db.updates.append((self.name, self.patch))
            for r in rows:
                r.update(self.patch)
        return SimpleNamespace(data=rows)


def _tables():
    return {
        "commerce_bank_transactions": [
            {"id": "in1", "tenant_id": TID, "direction": "in", "amount_cents": 50_000_00,
             "txn_date": "2026-09-01", "description": "HPC PC10", "match_status": "unmatched"},
            {"id": "out1", "tenant_id": TID, "direction": "out", "amount_cents": 239_717,
             "txn_date": "2026-09-12", "description": "Ste Digg", "match_status": "unmatched"}],
        "commerce_invoices": [
            {"id": "inv1", "tenant_id": TID, "direction": "outbound", "doc_type": "invoice",
             "status": "sent", "total_cents": 80_000_00, "total_paid_cents": 0,
             "invoice_number": "DIG-INV-00050", "customer_name": "HPC", "issue_date": "2026-08-25"},
            {"id": "bill1", "tenant_id": TID, "direction": "inbound", "doc_type": "invoice",
             "status": "draft", "total_cents": 239_717, "total_paid_cents": 0,
             "invoice_number": "DIG-BILL-00085", "supplier": "STE Scaffolding", "issue_date": "2026-09-10"},
            {"id": "bill2", "tenant_id": TID, "direction": "inbound", "doc_type": "invoice",
             "status": "draft", "total_cents": 500_000, "total_paid_cents": 0,
             "invoice_number": "DIG-BILL-00090", "supplier": "Other", "issue_date": "2026-07-01"},
            {"id": "q1", "tenant_id": TID, "direction": "inbound", "doc_type": "quote",
             "status": "draft", "total_cents": 239_717, "total_paid_cents": 0}],
    }


@pytest.mark.asyncio
async def test_candidates_for_money_out_are_supplier_bills_exact_amount_first():
    db = _DB(_tables())
    with patch.object(capi.service, "_client", lambda: db):
        out = await capi.admin_bank_match_candidates(TID, "out1")
    assert out["kind"] == "bill"
    assert [c["id"] for c in out["candidates"]] == ["bill1", "bill2"]          # no quote
    assert out["candidates"][0]["exact"] is True


@pytest.mark.asyncio
async def test_candidates_for_money_in_are_our_invoices():
    db = _DB(_tables())
    with patch.object(capi.service, "_client", lambda: db):
        out = await capi.admin_bank_match_candidates(TID, "in1")
    assert [c["id"] for c in out["candidates"]] == ["inv1"]


@pytest.mark.asyncio
async def test_a_part_payment_books_only_the_amount_on_the_line():
    db = _DB(_tables())
    rec = AsyncMock(return_value={"status": "part_paid", "balance_due_cents": 30_000_00})
    with patch.object(capi.service, "_client", lambda: db), \
            patch.object(capi.service, "record_invoice_payment", rec):
        out = await capi.admin_bank_match(TID, "in1", capi.BankMatchIn(action="match", invoice_id="inv1"))
    assert rec.await_args.args[2] == 50_000_00
    assert out["invoice_status"] == "part_paid" and out["balance_due_cents"] == 30_000_00
    assert db.tables["commerce_bank_transactions"][0]["match_status"] == "matched"


@pytest.mark.asyncio
async def test_money_out_cannot_settle_one_of_our_invoices():
    db = _DB(_tables())
    with patch.object(capi.service, "_client", lambda: db):
        with pytest.raises(capi.HTTPException) as e:
            await capi.admin_bank_match(TID, "out1", capi.BankMatchIn(action="match", invoice_id="inv1"))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_a_supplier_bill_paid_in_full_from_the_bank():
    db = _DB(_tables())
    rec = AsyncMock(return_value={"status": "paid", "balance_due_cents": 0})
    with patch.object(capi.service, "_client", lambda: db), \
            patch.object(capi.service, "record_invoice_payment", rec):
        out = await capi.admin_bank_match(TID, "out1", capi.BankMatchIn(action="match", invoice_id="bill1"))
    assert rec.await_args.args[2] == 239_717 and out["invoice_status"] == "paid"


def test_a_supplier_part_payment_is_booked_as_money_out_not_sales():
    posted = {}
    with patch.object(ledger, "_post", lambda tid, **kw: posted.update(kw)), \
            patch.object(ledger, "_supplier_account_code", lambda *a: "cost_of_sales"):
        ledger.post_supplier_payment(TID, {"id": "b", "total_cents": 115_000, "vat_cents": 15_000,
                                           "supplier": "STE"}, {"id": "p1", "amount_cents": 57_500})
    lines = {l["account_code"]: l for l in posted["lines"]}
    assert lines["bank_cash"]["credit_cents"] == 57_500
    assert lines["vat_input"]["debit_cents"] == 7_500
    assert lines["cost_of_sales"]["debit_cents"] == 50_000
    assert "sales" not in lines and posted["source_type"] == "supplier_payment"
