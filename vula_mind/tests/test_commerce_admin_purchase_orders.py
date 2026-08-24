"""Tests for the purchase-order/supplier tools added to commerce_admin.py."""
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


# ── suppliers ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_suppliers_empty(skill, monkeypatch):
    async def list_suppliers(tid):
        return []
    monkeypatch.setattr(ca.service, "list_suppliers", list_suppliers)
    res = await skill._list_suppliers(TID)
    assert "message" in res


@pytest.mark.asyncio
async def test_list_suppliers(skill, monkeypatch):
    async def list_suppliers(tid):
        return [{"name": "Ocean Basket Wholesale", "contact_email": "supplier@example.com",
                 "contact_phone": "0821234567"}]
    monkeypatch.setattr(ca.service, "list_suppliers", list_suppliers)
    res = await skill._list_suppliers(TID)
    assert res["suppliers"][0]["name"] == "Ocean Basket Wholesale"


@pytest.mark.asyncio
async def test_upsert_supplier_requires_name(skill):
    res = await skill._upsert_supplier(TID, {})
    assert "error" in res


@pytest.mark.asyncio
async def test_upsert_supplier(skill, monkeypatch):
    async def upsert_supplier(tid, data):
        assert data["name"] == "Fresh Fish Co"
        return {"name": "Fresh Fish Co"}
    monkeypatch.setattr(ca.service, "upsert_supplier", upsert_supplier)
    res = await skill._upsert_supplier(TID, {"name": "Fresh Fish Co", "contact_email": "x@y.com", "confirm": True})
    assert res == {"saved": "Fresh Fish Co"}


@pytest.mark.asyncio
async def test_upsert_supplier_without_confirm_returns_preview_and_does_not_write(skill, monkeypatch):
    called = {}
    async def upsert_supplier(tid, data):
        called["yes"] = True
        return {"name": "Fresh Fish Co"}
    monkeypatch.setattr(ca.service, "upsert_supplier", upsert_supplier)
    res = await skill._upsert_supplier(TID, {"name": "Fresh Fish Co", "contact_email": "x@y.com"})
    assert res.get("preview") is True
    assert "yes" not in called


@pytest.mark.asyncio
async def test_delete_supplier_not_found(skill, monkeypatch):
    async def list_suppliers(tid):
        return [{"id": "s1", "name": "Fresh Fish Co"}]
    monkeypatch.setattr(ca.service, "list_suppliers", list_suppliers)
    res = await skill._delete_supplier(TID, "Boxshop")
    assert "error" in res


@pytest.mark.asyncio
async def test_delete_supplier(skill, monkeypatch):
    async def list_suppliers(tid):
        return [{"id": "s1", "name": "Fresh Fish Co"}]
    deleted = {}
    async def delete_supplier(tid, sid):
        deleted["id"] = sid
    monkeypatch.setattr(ca.service, "list_suppliers", list_suppliers)
    monkeypatch.setattr(ca.service, "delete_supplier", delete_supplier)
    res = await skill._delete_supplier(TID, "fresh fish", confirm=True)
    assert res == {"deleted": "Fresh Fish Co"}
    assert deleted["id"] == "s1"


# ── reorder suggestions ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reorder_suggestions_none_low(skill, monkeypatch):
    from vula.commerce import purchase_orders as po_mod
    async def get_reorder_suggestions(tid):
        return {"groups": [], "count": 0}
    monkeypatch.setattr(po_mod, "get_reorder_suggestions", get_reorder_suggestions)
    res = await skill._reorder_suggestions(TID)
    assert "message" in res


@pytest.mark.asyncio
async def test_reorder_suggestions_has_data(skill, monkeypatch):
    from vula.commerce import purchase_orders as po_mod
    async def get_reorder_suggestions(tid):
        return {"groups": [{"supplier_name": "Fresh Fish Co", "items": [{"name": "Hake"}]}], "count": 1}
    monkeypatch.setattr(po_mod, "get_reorder_suggestions", get_reorder_suggestions)
    res = await skill._reorder_suggestions(TID)
    assert res["count"] == 1


# ── create_purchase_order ─────────────────────────────────────────────────

def _po_create_setup(monkeypatch, readback_row):
    async def list_suppliers(tid):
        return [{"id": "s1", "name": "Fresh Fish Co"}]

    async def admin_create_purchase_order(tid, body):
        return {"id": "po123456-abcd", "supplier_name": body["supplier_name"], "total_cents": 5000}

    import vula.api.commerce as api_commerce

    class _Q:
        def __init__(self, rows):
            self._rows = rows
        def table(self, *a): return self
        def select(self, *a): return self
        def eq(self, *a): return self
        def order(self, *a, **kw): return self
        def limit(self, *a): return self
        def execute(self): return type("R", (), {"data": self._rows})()

    monkeypatch.setattr(ca.service, "list_suppliers", list_suppliers)
    monkeypatch.setattr(api_commerce, "admin_create_purchase_order", admin_create_purchase_order)
    monkeypatch.setattr(ca.service, "_client", lambda: _Q(readback_row))


@pytest.mark.asyncio
async def test_create_purchase_order_no_supplier_match(skill, monkeypatch):
    async def list_suppliers(tid):
        return [{"id": "s1", "name": "Fresh Fish Co"}]
    monkeypatch.setattr(ca.service, "list_suppliers", list_suppliers)
    res = await skill._create_purchase_order(TID, {"supplier_name": "Nobody", "items": [{"name": "Hake", "quantity": 5}]})
    assert "error" in res


@pytest.mark.asyncio
async def test_create_purchase_order_readback_confirmed(skill, emits, monkeypatch):
    _po_create_setup(monkeypatch, [{"id": "po123456-abcd", "status": "draft"}])
    res = await skill._create_purchase_order(TID, {
        "supplier_name": "fresh fish", "items": [{"name": "Hake", "quantity": 10, "unit_cost_rands": 50}],
        "confirm": True})
    assert res.get("verified") is True
    assert res["po_ref"] == "po123456"
    assert _gate_events(emits)[0]["outcome"] == "confirmed"


@pytest.mark.asyncio
async def test_create_purchase_order_readback_missing(skill, emits, monkeypatch):
    _po_create_setup(monkeypatch, [])  # nothing found on re-read
    res = await skill._create_purchase_order(TID, {
        "supplier_name": "fresh fish", "items": [{"name": "Hake", "quantity": 10, "unit_cost_rands": 50}],
        "confirm": True})
    assert "error" in res and "Not confirmed" in res["error"]
    assert _gate_events(emits)[0]["outcome"] == "mismatch"


@pytest.mark.asyncio
async def test_create_purchase_order_without_confirm_returns_preview_and_does_not_write(skill, monkeypatch):
    called = {}
    async def list_suppliers(tid):
        return [{"id": "s1", "name": "Fresh Fish Co"}]
    import vula.api.commerce as api_commerce
    async def admin_create_purchase_order(tid, body):
        called["yes"] = True
        return {"id": "po123456-abcd", "supplier_name": body["supplier_name"], "total_cents": 5000}
    monkeypatch.setattr(ca.service, "list_suppliers", list_suppliers)
    monkeypatch.setattr(api_commerce, "admin_create_purchase_order", admin_create_purchase_order)
    res = await skill._create_purchase_order(TID, {
        "supplier_name": "fresh fish", "items": [{"name": "Hake", "quantity": 10, "unit_cost_rands": 50}]})
    assert res.get("preview") is True
    assert "yes" not in called


# ── update_po_status ───────────────────────────────────────────────────────

def _po_status_setup(monkeypatch, rows_sequence):
    """rows_sequence: list of row-lists returned on successive _client() calls (resolve, readback)."""
    calls = {"n": 0}

    class _Q:
        def table(self, *a): return self
        def select(self, *a): return self
        def eq(self, *a): return self
        def order(self, *a, **kw): return self
        def limit(self, *a): return self
        def execute(self):
            rows = rows_sequence[min(calls["n"], len(rows_sequence) - 1)]
            calls["n"] += 1
            return type("R", (), {"data": rows})()

    async def admin_update_po_status(tid, po_id, body):
        return {}

    import vula.api.commerce as api_commerce
    monkeypatch.setattr(api_commerce, "admin_update_po_status", admin_update_po_status)
    monkeypatch.setattr(ca.service, "_client", lambda: _Q())


@pytest.mark.asyncio
async def test_update_po_status_invalid_status(skill):
    res = await skill._update_po_status(TID, "abc123", "bogus")
    assert "error" in res


@pytest.mark.asyncio
async def test_update_po_status_not_found(skill, monkeypatch):
    _po_status_setup(monkeypatch, [[]])
    res = await skill._update_po_status(TID, "zzzzzzzz", "sent")
    assert "error" in res


@pytest.mark.asyncio
async def test_update_po_status_readback_confirmed(skill, emits, monkeypatch):
    _po_status_setup(monkeypatch, [
        [{"id": "po123456-abcd", "status": "draft"}],   # initial resolve
        [{"id": "po123456-abcd", "status": "sent"}],    # readback after update
    ])
    res = await skill._update_po_status(TID, "po123456", "sent", confirm=True)
    assert res.get("verified") is True
    assert _gate_events(emits)[0]["outcome"] == "confirmed"


@pytest.mark.asyncio
async def test_update_po_status_without_confirm_returns_preview_and_does_not_write(skill, monkeypatch):
    _po_status_setup(monkeypatch, [[{"id": "po123456-abcd", "status": "draft"}]])
    called = {}
    import vula.api.commerce as api_commerce
    async def admin_update_po_status(tid, po_id, body):
        called["yes"] = True
    monkeypatch.setattr(api_commerce, "admin_update_po_status", admin_update_po_status)
    res = await skill._update_po_status(TID, "po123456", "sent")
    assert res.get("preview") is True
    assert "yes" not in called


@pytest.mark.asyncio
async def test_update_po_status_readback_mismatch(skill, emits, monkeypatch):
    _po_status_setup(monkeypatch, [
        [{"id": "po123456-abcd", "status": "draft"}],   # initial resolve
        [{"id": "po123456-abcd", "status": "draft"}],   # readback shows unchanged
    ])
    res = await skill._update_po_status(TID, "po123456", "sent", confirm=True)
    assert "error" in res and "Not confirmed" in res["error"]
    assert _gate_events(emits)[0]["outcome"] == "mismatch"


# ── send_purchase_order ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_purchase_order_not_found(skill, monkeypatch):
    class _Q:
        def table(self, *a): return self
        def select(self, *a): return self
        def eq(self, *a): return self
        def order(self, *a, **kw): return self
        def limit(self, *a): return self
        def execute(self): return type("R", (), {"data": []})()
    monkeypatch.setattr(ca.service, "_client", lambda: _Q())
    res = await skill._send_purchase_order(TID, "zzzzzzzz", "email")
    assert "error" in res


@pytest.mark.asyncio
async def test_send_purchase_order_success(skill, monkeypatch):
    class _Q:
        def table(self, *a): return self
        def select(self, *a): return self
        def eq(self, *a): return self
        def order(self, *a, **kw): return self
        def limit(self, *a): return self
        def execute(self): return type("R", (), {"data": [{"id": "po123456-abcd", "status": "draft"}]})()
    monkeypatch.setattr(ca.service, "_client", lambda: _Q())

    from vula.commerce import purchase_orders as po_mod
    async def send_purchase_order(tid, po_id, channel):
        return {"ok": True, "sent_via": ["email"]}
    monkeypatch.setattr(po_mod, "send_purchase_order", send_purchase_order)

    res = await skill._send_purchase_order(TID, "po123456", "email", confirm=True)
    assert res == {"sent": True, "po_ref": "po123456", "via": ["email"], "warnings": None}


@pytest.mark.asyncio
async def test_send_purchase_order_without_confirm_returns_preview_and_does_not_send(skill, monkeypatch):
    class _Q:
        def table(self, *a): return self
        def select(self, *a): return self
        def eq(self, *a): return self
        def order(self, *a, **kw): return self
        def limit(self, *a): return self
        def execute(self): return type("R", (), {"data": [{"id": "po123456-abcd", "status": "draft",
                                                              "supplier_name": "Fresh Fish Co", "total_cents": 5000}]})()
    monkeypatch.setattr(ca.service, "_client", lambda: _Q())
    from vula.commerce import purchase_orders as po_mod
    called = {}
    async def send_purchase_order(tid, po_id, channel):
        called["yes"] = True
        return {"ok": True, "sent_via": ["email"]}
    monkeypatch.setattr(po_mod, "send_purchase_order", send_purchase_order)
    res = await skill._send_purchase_order(TID, "po123456", "email")
    assert res.get("preview") is True
    assert "yes" not in called


# ── keyword tool subsetting (_tools_for / _match_groups) ─────────────────

def test_match_groups_confident_hit():
    hits = ca._match_groups("please make a purchase order for the supplier")
    assert hits == {"purchase_orders"}


def test_match_groups_no_hit_returns_none():
    assert ca._match_groups("hey how's it going") is None


def test_tools_for_narrows_on_confident_match(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: [])
    all_tools = ca._tools_for(TID, message="")
    narrowed = ca._tools_for(TID, message="create a discount code for weekend10")
    names_all = {t["function"]["name"] for t in all_tools}
    names_narrowed = {t["function"]["name"] for t in narrowed}
    assert "create_discount_code" in names_narrowed
    assert "booking_availability" not in names_narrowed  # bookings group filtered out
    assert len(names_narrowed) < len(names_all)


def test_tools_for_falls_back_to_show_all_on_no_match(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: [])
    all_tools = ca._tools_for(TID, message="")
    unmatched = ca._tools_for(TID, message="confirm")
    names_all = {t["function"]["name"] for t in all_tools}
    names_unmatched = {t["function"]["name"] for t in unmatched}
    assert names_all == names_unmatched
