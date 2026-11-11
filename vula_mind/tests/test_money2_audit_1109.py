"""The 9 Nov 2026 go-live audit's remaining money items (docs/golive-audit-2026-11-09.md),
each as the case that went wrong before the fix.

- a part refund reversed the whole sale; a never-paid order "refunded" posted a refund; a paid
  order could be cancelled with nothing in the books
- credit notes: no cap, earlier credits counted as cash received, lines re-priced on the
  business's current VAT setting instead of the invoice's
- a business with no invoice settings was VAT-registered by default (code and DB default)
- rounding was half-to-even on the server, half-up everywhere else
- the general ledger could be edited or deleted
(Overpayment: tests/test_invoicing.py::test_record_payment_never_records_more_than_is_owed.)
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vula.commerce import ledger, service

TID = "shop-a"


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.patch = db, name, [], None

    def select(self, *_a):
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def insert(self, row):
        self.db.tables.setdefault(self.name, []).append(dict(row))
        self.patch = "inserted"
        return self

    def eq(self, c, v):
        self.filters.append(lambda r, c=c, v=v: r.get(c) == v)
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.patch == "inserted":
            return SimpleNamespace(data=[self.db.tables[self.name][-1]])
        rows = [r for r in self.db.tables.get(self.name, []) if all(f(r) for f in self.filters)]
        for r in rows if self.patch else []:
            r.update(self.patch)
        return SimpleNamespace(data=[dict(r) for r in rows])


class _DB:
    def __init__(self, **tables):
        self.tables = tables

    def table(self, name):
        return _Q(self, name)


@pytest.fixture
def books(monkeypatch):
    posted = []
    monkeypatch.setattr(ledger, "post_order_refund",
                        lambda tid, order, amount_cents=None, source_id=None: posted.append((amount_cents, source_id)))
    monkeypatch.setattr(ledger, "post_order_paid", lambda *a, **k: None)
    return posted


def _order(**kw):
    return {"id": "o1", "tenant_id": TID, "display_id": "SHA-0001", "status": "delivered",
            "total_cents": 100000, "refunded_amount_cents": None, **kw}


# ── refunds and cancellations ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_part_refund_reverses_what_was_refunded_not_the_whole_sale(monkeypatch, books):
    db = _DB(commerce_orders=[_order()])
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(ledger, "was_posted", lambda *a: True)
    await service.update_order_status("o1", "refunded", refund_cents=20000)
    await service.update_order_status("o1", "refunded", refund_cents=30000)
    await service.update_order_status("o1", "refunded", refund_cents=90000)      # only 50000 left
    assert books == [(20000, "o1:refund:0"), (30000, "o1:refund:20000"), (50000, "o1:refund:50000")]
    assert db.tables["commerce_orders"][0]["refunded_amount_cents"] == 100000
    await service.update_order_status("o1", "refunded")                          # nothing left: no-op
    assert len(books) == 3


@pytest.mark.asyncio
async def test_a_full_refund_keeps_its_one_ledger_key(monkeypatch, books):
    db = _DB(commerce_orders=[_order()])
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(ledger, "was_posted", lambda *a: True)
    await service.update_order_status("o1", "refunded")
    assert books == [(100000, "o1")]


@pytest.mark.asyncio
async def test_an_order_never_booked_as_paid_posts_no_refund(monkeypatch, books):
    db = _DB(commerce_orders=[_order(status="confirmed")])      # cash on delivery, not paid yet
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(ledger, "was_posted", lambda *a: False)
    await service.update_order_status("o1", "refunded")
    assert books == [] and db.tables["commerce_orders"][0]["status"] == "refunded"


@pytest.mark.asyncio
async def test_a_paid_order_is_refunded_not_cancelled(monkeypatch, books):
    db = _DB(commerce_orders=[_order()])
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(ledger, "was_posted", lambda *a: True)
    with pytest.raises(service.OrderStatusRefused, match="refund it instead"):
        await service.update_order_status("o1", "cancelled")
    assert db.tables["commerce_orders"][0]["status"] == "delivered"


@pytest.mark.asyncio
async def test_an_unpaid_order_still_cancels(monkeypatch, books):
    db = _DB(commerce_orders=[_order(status="pending_payment")])
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(ledger, "was_posted", lambda *a: False)
    await service.update_order_status("o1", "cancelled")
    assert db.tables["commerce_orders"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_the_dashboard_and_the_agent_say_why_a_paid_order_cant_be_cancelled(monkeypatch):
    from fastapi import HTTPException
    from vula.api import commerce as api
    refuse = AsyncMock(side_effect=service.OrderStatusRefused("Order SHA-0001 is paid, refund it instead"))
    monkeypatch.setattr(api.service, "get_order", AsyncMock(return_value=_order()))
    monkeypatch.setattr(api.service, "update_order_status", refuse)
    stock = AsyncMock()
    monkeypatch.setattr(api.service, "apply_order_stock", stock)
    with pytest.raises(HTTPException) as e:
        await api.admin_update_order_status(TID, "o1", {"status": "cancelled"})
    assert e.value.status_code == 409 and "refund" in e.value.detail
    stock.assert_not_awaited()                                   # nothing restored either


# ── credit notes ─────────────────────────────────────────────────────────────────────────────

def _inv(**kw):
    return {"id": "inv-1", "tenant_id": TID, "invoice_number": "SHA-INV-00001", "doc_type": "invoice",
            "status": "sent", "total_cents": 100000, "total_paid_cents": 0, "vat_rate": 0,
            "subtotal_cents": 100000, "line_items": [], **kw}


def _credit_db(credit_notes=(), payments=()):
    return _DB(commerce_invoices=[{"tenant_id": TID, "credited_invoice_id": "inv-1", "total_cents": c}
                                  for c in credit_notes],
               commerce_invoice_payments=[{"tenant_id": TID, "invoice_id": "inv-1", "amount_cents": a,
                                           "payment_method": m} for a, m in payments])


def test_a_second_credit_cant_go_past_the_invoice(monkeypatch):
    """Two R800 credits on an unpaid R1,000 invoice used to ask for a "R600 already paid" refund."""
    monkeypatch.setattr(service, "_client", lambda: _credit_db([80000], [(80000, "credit_note")]))
    with pytest.raises(ValueError, match="R200.00"):
        service.plan_credit(TID, _inv(total_paid_cents=80000), 80000)
    plan = service.plan_credit(TID, _inv(total_paid_cents=80000))
    assert plan == {"credit": 20000, "against_owed": 20000, "refund": 0, "left_to_credit": 20000, "cash_paid": 0}


def test_only_money_actually_received_can_be_refunded(monkeypatch):
    monkeypatch.setattr(service, "_client", lambda: _credit_db([], [(30000, "eft")]))
    plan = service.plan_credit(TID, _inv(total_paid_cents=30000), 100000)
    assert plan["against_owed"] == 70000 and plan["refund"] == 30000


def test_nothing_left_to_credit(monkeypatch):
    monkeypatch.setattr(service, "_client", lambda: _credit_db([100000], [(100000, "credit_note")]))
    with pytest.raises(ValueError, match="credited in full"):
        service.plan_credit(TID, _inv(total_paid_cents=100000, status="cancelled"))


@pytest.mark.asyncio
async def test_credited_lines_keep_the_invoices_vat_basis(monkeypatch):
    """A VAT-inclusive invoice credited line by line used to be credited at 1.15x."""
    lines = [{"description": "Hake", "quantity": 2, "unit_price_cents": 11500, "total_cents": 23000}]
    src = _inv(total_cents=23000, subtotal_cents=20000, vat_cents=3000, vat_rate=15, line_items=lines)
    made = {}

    async def create(tid, data):
        made.update(data)
        *_, total, _ = service._compute_totals(data["line_items"], data["vat_rate"], data["prices_include_vat"])
        return {"id": "cn-1", "invoice_number": "SHA-CN-00001", "total_cents": total}
    monkeypatch.setattr(service, "get_invoice", AsyncMock(return_value=src))
    monkeypatch.setattr(service, "create_invoice", create)
    monkeypatch.setattr(service, "_client", lambda: _credit_db())
    res = await service.credit_invoice(TID, "inv-1", line_items=[dict(lines[0], quantity=1)])
    assert made["prices_include_vat"] is True
    assert res["credited_cents"] == 11500 and res["credit_note"]["total_cents"] == 11500


# ── VAT default, rounding, ledger ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_settings_means_no_vat(monkeypatch):
    made = {}
    db = _DB()
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(service, "get_invoice_settings", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_next_invoice_number", AsyncMock(return_value="SHA-INV-00001"))

    def insert(row):
        made.update(row)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[row]))
    monkeypatch.setattr(db, "table", lambda n: SimpleNamespace(insert=insert))
    out = await service.create_invoice(TID, {"customer_name": "Sam", "line_items": [
        {"description": "Tiles", "quantity": 1, "unit_price_cents": 100000}]})
    assert out["vat_cents"] == 0 and out["total_cents"] == 100000


def test_the_db_default_is_not_registered_and_the_ledger_is_append_only():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "253_vat_default_and_ledger_append_only.sql").read_text()
    assert "alter column vat_registered set default false" in sql
    for table in ("journal_entries", "journal_lines"):
        assert f"before update or delete on {table}" in sql
    assert "set search_path = public, pg_temp" in sql


@pytest.mark.parametrize("cents,vat", [(30, 5), (10, 2), (50, 8), (70, 11), (100, 15)])
def test_vat_rounds_half_up_like_the_dashboard(cents, vat):
    _, _, got, total, _ = service._compute_totals([{"description": "x", "quantity": 1, "unit_price_cents": cents}], 15.0)
    assert got == vat and total == cents + vat
