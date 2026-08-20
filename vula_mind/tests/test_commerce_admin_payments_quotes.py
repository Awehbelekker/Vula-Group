"""Tests for record_payment, quote lifecycle, manual order entry, and discount-code tools
added to commerce_admin.py."""
import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill

TID = "test-tenant"


@pytest.fixture
def emits(monkeypatch):
    captured = []
    monkeypatch.setattr(ca, "_emit", lambda **kw: captured.append(kw))
    return captured


@pytest.fixture
def skill():
    return CommerceAdminSkill()


def _gate_events(emits):
    return [e for e in emits if e.get("verifier") == "gate.readback"]


class _Q:
    """Minimal chained supabase-style fake returning `rows` from every execute()."""
    def __init__(self, rows):
        self._rows = rows
    def table(self, *a): return self
    def select(self, *a): return self
    def eq(self, *a): return self
    def is_(self, *a): return self
    def ilike(self, *a): return self
    def order(self, *a, **kw): return self
    def limit(self, *a): return self
    def execute(self): return type("R", (), {"data": self._rows})()


# ── record_payment ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_payment_invoice_not_found(skill, monkeypatch):
    monkeypatch.setattr(ca.service, "_client", lambda: _Q([]))
    res = await skill._record_payment(TID, {"invoice_number": "INV-999", "amount_rands": 100})
    assert "error" in res


@pytest.mark.asyncio
async def test_record_payment_rejects_non_positive_amount(skill, monkeypatch):
    monkeypatch.setattr(ca.service, "_client", lambda: _Q([{"id": "i1", "invoice_number": "INV-001"}]))
    res = await skill._record_payment(TID, {"invoice_number": "INV-001", "amount_rands": 0})
    assert "error" in res


@pytest.mark.asyncio
async def test_record_payment_readback_confirmed(skill, emits, monkeypatch):
    monkeypatch.setattr(ca.service, "_client", lambda: _Q(
        [{"id": "i1", "invoice_number": "INV-001", "total_paid_cents": 0}]))

    async def record_invoice_payment(tid, invoice_id, cents, method, note):
        return {"status": "part_paid", "balance_due_cents": 5000}
    monkeypatch.setattr(ca.service, "record_invoice_payment", record_invoice_payment)

    async def list_invoice_payments(tid, invoice_id):
        return [{"amount_cents": 5000}]
    monkeypatch.setattr(ca.service, "list_invoice_payments", list_invoice_payments)

    res = await skill._record_payment(TID, {"invoice_number": "INV-001", "amount_rands": 50, "confirm": True})
    assert res.get("verified") is True
    assert res["new_status"] == "part_paid"
    assert _gate_events(emits)[0]["outcome"] == "confirmed"


@pytest.mark.asyncio
async def test_record_payment_without_confirm_returns_preview_and_does_not_write(skill, monkeypatch):
    monkeypatch.setattr(ca.service, "_client", lambda: _Q(
        [{"id": "i1", "invoice_number": "INV-001", "total_paid_cents": 0}]))
    called = {}
    async def record_invoice_payment(tid, invoice_id, cents, method, note):
        called["yes"] = True
        return {"status": "part_paid", "balance_due_cents": 5000}
    monkeypatch.setattr(ca.service, "record_invoice_payment", record_invoice_payment)
    res = await skill._record_payment(TID, {"invoice_number": "INV-001", "amount_rands": 50})
    assert res.get("preview") is True
    assert "yes" not in called


@pytest.mark.asyncio
async def test_record_payment_readback_mismatch(skill, emits, monkeypatch):
    monkeypatch.setattr(ca.service, "_client", lambda: _Q(
        [{"id": "i1", "invoice_number": "INV-001", "total_paid_cents": 0}]))

    async def record_invoice_payment(tid, invoice_id, cents, method, note):
        return {"status": "part_paid", "balance_due_cents": 5000}
    monkeypatch.setattr(ca.service, "record_invoice_payment", record_invoice_payment)

    async def list_invoice_payments(tid, invoice_id):
        return []  # nothing actually persisted
    monkeypatch.setattr(ca.service, "list_invoice_payments", list_invoice_payments)

    res = await skill._record_payment(TID, {"invoice_number": "INV-001", "amount_rands": 50, "confirm": True})
    assert "error" in res and "Not confirmed" in res["error"]
    assert _gate_events(emits)[0]["outcome"] == "mismatch"


@pytest.mark.asyncio
async def test_record_payment_propagates_value_error(skill, monkeypatch):
    monkeypatch.setattr(ca.service, "_client", lambda: _Q([{"id": "i1", "invoice_number": "INV-001"}]))

    async def record_invoice_payment(tid, invoice_id, cents, method, note):
        raise ValueError("invoice is already paid")
    monkeypatch.setattr(ca.service, "record_invoice_payment", record_invoice_payment)

    res = await skill._record_payment(TID, {"invoice_number": "INV-001", "amount_rands": 50, "confirm": True})
    assert res == {"error": "invoice is already paid"}


# ── quotes ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_quotes_empty(skill, monkeypatch):
    async def list_invoices(tid, **kw):
        return []
    monkeypatch.setattr(ca.service, "list_invoices", list_invoices)
    res = await skill._list_quotes(TID, None)
    assert "message" in res


@pytest.mark.asyncio
async def test_list_quotes(skill, monkeypatch):
    async def list_invoices(tid, **kw):
        assert kw.get("doc_type") == "quote"
        return [{"invoice_number": "QUO-001", "status": "sent", "total_cents": 10000, "customer_name": "Jane"}]
    monkeypatch.setattr(ca.service, "list_invoices", list_invoices)
    res = await skill._list_quotes(TID, None)
    assert res["quotes"][0]["quote"] == "QUO-001"


@pytest.mark.asyncio
async def test_convert_quote_not_found(skill, monkeypatch):
    monkeypatch.setattr(ca.service, "_client", lambda: _Q([]))
    res = await skill._convert_quote_to_invoice(TID, "QUO-999")
    assert "error" in res


@pytest.mark.asyncio
async def test_convert_quote_propagates_value_error(skill, monkeypatch):
    monkeypatch.setattr(ca.service, "_client", lambda: _Q([{"id": "q1", "invoice_number": "QUO-001"}]))

    async def convert_quote_to_invoice(tid, quote_id):
        raise ValueError("quote must be marked accepted before it can be converted to an invoice")
    monkeypatch.setattr(ca.service, "convert_quote_to_invoice", convert_quote_to_invoice)

    res = await skill._convert_quote_to_invoice(TID, "QUO-001")
    assert "accepted" in res["error"]


@pytest.mark.asyncio
async def test_convert_quote_readback_confirmed(skill, emits, monkeypatch):
    monkeypatch.setattr(ca.service, "_client", lambda: _Q([{"id": "q1", "invoice_number": "QUO-001"}]))

    async def convert_quote_to_invoice(tid, quote_id):
        return {"id": "i1", "invoice_number": "INV-050", "total_cents": 10000}
    monkeypatch.setattr(ca.service, "convert_quote_to_invoice", convert_quote_to_invoice)

    async def get_invoice(tid, invoice_id):
        return {"id": "i1", "doc_type": "invoice", "source_quote_id": "q1"}
    monkeypatch.setattr(ca.service, "get_invoice", get_invoice)

    res = await skill._convert_quote_to_invoice(TID, "QUO-001")
    assert res.get("verified") is True
    assert res["invoice_number"] == "INV-050"
    assert _gate_events(emits)[0]["outcome"] == "confirmed"


@pytest.mark.asyncio
async def test_update_quote_status_invalid(skill):
    res = await skill._update_quote_status(TID, "QUO-001", "bogus")
    assert "error" in res


@pytest.mark.asyncio
async def test_update_quote_status(skill, monkeypatch):
    monkeypatch.setattr(ca.service, "_client", lambda: _Q([{"id": "q1", "invoice_number": "QUO-001"}]))

    async def update_invoice_status(tid, invoice_id, status):
        return {}
    monkeypatch.setattr(ca.service, "update_invoice_status", update_invoice_status)

    res = await skill._update_quote_status(TID, "QUO-001", "accepted")
    assert res == {"updated": "QUO-001", "new_status": "accepted"}


# ── create_manual_order ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_manual_order_requires_customer_info(skill):
    res = await skill._create_manual_order(TID, {"items": [{"product": "Hake", "quantity": 1}]})
    assert "error" in res


@pytest.mark.asyncio
async def test_create_manual_order_unknown_product(skill, monkeypatch):
    async def list_products(tid, **kw):
        return [{"id": "p1", "name": "Hake Fillets"}]
    monkeypatch.setattr(ca.service, "list_products", list_products)
    res = await skill._create_manual_order(TID, {
        "customer_name": "Jane", "customer_phone": "0821234567",
        "items": [{"product": "Nonexistent", "quantity": 1}]})
    assert "error" in res


@pytest.mark.asyncio
async def test_create_manual_order_readback_confirmed(skill, emits, monkeypatch):
    async def list_products(tid, **kw):
        return [{"id": "p1", "name": "Hake Fillets"}]
    added = []

    async def get_or_create_cart(tid, session_id, customer_phone=None):
        return {"id": "cart1"}

    async def add_to_cart(tid, cart_id, product_id, qty, variant_id=None):
        added.append((product_id, qty))

    async def create_order(tid, cart, checkout_data):
        return {"id": "o1", "display_id": "OTH-00099", "total_cents": 5000}

    async def get_order(order_id):
        return {"id": "o1", "display_id": "OTH-00099"}

    monkeypatch.setattr(ca.service, "list_products", list_products)
    monkeypatch.setattr(ca.service, "get_or_create_cart", get_or_create_cart)
    monkeypatch.setattr(ca.service, "add_to_cart", add_to_cart)
    monkeypatch.setattr(ca.service, "create_order", create_order)
    monkeypatch.setattr(ca.service, "get_order", get_order)

    res = await skill._create_manual_order(TID, {
        "customer_name": "Jane", "customer_phone": "0821234567",
        "items": [{"product": "Hake", "quantity": 2}]})
    assert res.get("verified") is True
    assert res["order"] == "OTH-00099"
    assert added == [("p1", 2.0)]
    assert _gate_events(emits)[0]["outcome"] == "confirmed"


@pytest.mark.asyncio
async def test_create_manual_order_marks_paid(skill, monkeypatch):
    async def list_products(tid, **kw):
        return [{"id": "p1", "name": "Hake Fillets"}]

    async def get_or_create_cart(tid, session_id, customer_phone=None):
        return {"id": "cart1"}

    async def add_to_cart(tid, cart_id, product_id, qty, variant_id=None):
        pass

    async def create_order(tid, cart, checkout_data):
        return {"id": "o1", "display_id": "OTH-00099", "total_cents": 5000}

    marked = {}
    async def update_order_status(order_id, status):
        marked["order_id"] = order_id
        marked["status"] = status

    async def get_order(order_id):
        return {"id": "o1", "display_id": "OTH-00099"}

    monkeypatch.setattr(ca.service, "list_products", list_products)
    monkeypatch.setattr(ca.service, "get_or_create_cart", get_or_create_cart)
    monkeypatch.setattr(ca.service, "add_to_cart", add_to_cart)
    monkeypatch.setattr(ca.service, "create_order", create_order)
    monkeypatch.setattr(ca.service, "update_order_status", update_order_status)
    monkeypatch.setattr(ca.service, "get_order", get_order)

    await skill._create_manual_order(TID, {
        "customer_name": "Jane", "customer_phone": "0821234567",
        "items": [{"product": "Hake", "quantity": 1}], "mark_paid": True})
    assert marked == {"order_id": "o1", "status": "paid"}


# ── discount codes ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_discount_code_requires_valid_type(skill):
    res = await skill._create_discount_code(TID, {"code": "SAVE10", "discount_type": "bogus"})
    assert "error" in res


@pytest.mark.asyncio
async def test_create_discount_code_percent_readback_confirmed(skill, emits, monkeypatch):
    async def create_discount_code(tid, data):
        assert data["type"] == "percent" and data["value"] == 10
        return {"code": "WEEKEND10", "type": "percent"}
    async def list_discount_codes(tid):
        return [{"code": "WEEKEND10"}]
    monkeypatch.setattr(ca.service, "create_discount_code", create_discount_code)
    monkeypatch.setattr(ca.service, "list_discount_codes", list_discount_codes)

    res = await skill._create_discount_code(TID, {"code": "weekend10", "discount_type": "percent", "value": 10, "confirm": True})
    assert res.get("verified") is True
    assert _gate_events(emits)[0]["outcome"] == "confirmed"


@pytest.mark.asyncio
async def test_create_discount_code_without_confirm_returns_preview_and_does_not_write(skill, monkeypatch):
    called = {}
    async def create_discount_code(tid, data):
        called["yes"] = True
        return {"code": "WEEKEND10", "type": "percent"}
    monkeypatch.setattr(ca.service, "create_discount_code", create_discount_code)
    res = await skill._create_discount_code(TID, {"code": "weekend10", "discount_type": "percent", "value": 10})
    assert res.get("preview") is True
    assert "yes" not in called


@pytest.mark.asyncio
async def test_create_discount_code_fixed_converts_rands_to_cents(skill, monkeypatch):
    async def create_discount_code(tid, data):
        assert data["value"] == 5000  # R50 -> 5000 cents
        return {"code": "SAVE50", "type": "fixed"}
    async def list_discount_codes(tid):
        return [{"code": "SAVE50"}]
    monkeypatch.setattr(ca.service, "create_discount_code", create_discount_code)
    monkeypatch.setattr(ca.service, "list_discount_codes", list_discount_codes)

    res = await skill._create_discount_code(TID, {"code": "SAVE50", "discount_type": "fixed", "value": 50, "confirm": True})
    assert res["created"] is True


@pytest.mark.asyncio
async def test_create_discount_code_duplicate(skill, monkeypatch):
    async def create_discount_code(tid, data):
        raise Exception("duplicate key value violates unique constraint idx_discount_codes_tenant_code")
    monkeypatch.setattr(ca.service, "create_discount_code", create_discount_code)
    res = await skill._create_discount_code(TID, {"code": "WEEKEND10", "discount_type": "percent", "value": 10, "confirm": True})
    assert "already exists" in res["error"]


@pytest.mark.asyncio
async def test_update_discount_code_not_found(skill, monkeypatch):
    async def list_discount_codes(tid):
        return []
    monkeypatch.setattr(ca.service, "list_discount_codes", list_discount_codes)
    res = await skill._update_discount_code(TID, {"code": "NOPE", "active": False})
    assert "error" in res


@pytest.mark.asyncio
async def test_update_discount_code_deactivate(skill, monkeypatch):
    async def list_discount_codes(tid):
        return [{"id": "d1", "code": "WEEKEND10"}]
    updated = {}
    async def update_discount_code(tid, code_id, patch):
        updated["id"] = code_id
        updated["patch"] = patch
    monkeypatch.setattr(ca.service, "list_discount_codes", list_discount_codes)
    monkeypatch.setattr(ca.service, "update_discount_code", update_discount_code)

    res = await skill._update_discount_code(TID, {"code": "weekend10", "active": False, "confirm": True})
    assert res == {"updated": "WEEKEND10", "active": False}
    assert updated == {"id": "d1", "patch": {"active": False}}


@pytest.mark.asyncio
async def test_delete_discount_code(skill, monkeypatch):
    async def list_discount_codes(tid):
        return [{"id": "d1", "code": "WEEKEND10"}]
    deleted = {}
    async def delete_discount_code(tid, code_id):
        deleted["id"] = code_id
    monkeypatch.setattr(ca.service, "list_discount_codes", list_discount_codes)
    monkeypatch.setattr(ca.service, "delete_discount_code", delete_discount_code)

    res = await skill._delete_discount_code(TID, "weekend10", confirm=True)
    assert res == {"deleted": "WEEKEND10"}
    assert deleted == {"id": "d1"}
