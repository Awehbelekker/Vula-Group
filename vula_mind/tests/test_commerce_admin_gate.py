"""Tests for the deterministic read-back completion gate on commerce_admin mutating tools.

After a write, the tool re-reads the row and only reports success if the intended change
actually persisted — "verified done", not "reported done". Mismatches surface to the owner
as errors and land in telemetry as verifier="gate.readback" / outcome="mismatch".
"""
import pytest

from config import settings
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


# ── update_order_status ───────────────────────────────────────────────────────

def _order_service(monkeypatch, readback_status):
    async def list_orders(tid, **kw):
        return [{"id": "o1", "display_id": "OTH-00042", "status": "paid"}]

    async def update_order_status(order_id, status):
        return None

    async def get_order(order_id):
        return {"id": "o1", "status": readback_status}

    monkeypatch.setattr(ca.service, "list_orders", list_orders)
    monkeypatch.setattr(ca.service, "update_order_status", update_order_status)
    monkeypatch.setattr(ca.service, "get_order", get_order)


@pytest.mark.asyncio
async def test_update_order_status_readback_confirmed(skill, emits, monkeypatch):
    _order_service(monkeypatch, readback_status="dispatched")
    res = await skill._update_order_status(TID, "OTH-00042", "dispatched")
    assert res == {"updated": "OTH-00042", "new_status": "dispatched", "verified": True}
    events = _gate_events(emits)
    assert len(events) == 1
    e = events[0]
    assert e["task"] == "update_order_status" and e["outcome"] == "confirmed"
    assert e["escalated"] is False and e["extra"]["anchored"] is True


@pytest.mark.asyncio
async def test_update_order_status_readback_mismatch(skill, emits, monkeypatch):
    _order_service(monkeypatch, readback_status="paid")  # write didn't stick
    res = await skill._update_order_status(TID, "OTH-00042", "dispatched")
    assert "error" in res and "Not confirmed" in res["error"]
    e = _gate_events(emits)[0]
    assert e["outcome"] == "mismatch" and e["escalated"] is True
    assert e["extra"]["anchored"] is False
    assert e["extra"]["observed"] == {"status": "paid"}


# ── update_stock ──────────────────────────────────────────────────────────────

def _stock_service(monkeypatch, readback_qty):
    async def get_product_by_slug(tid, slug):
        return None

    async def list_products(tid, **kw):
        return [{"id": "p1", "name": "Hake Fillets"}]

    async def update_product(tid, pid, patch):
        return None

    async def get_product(tid, pid):
        return {"id": "p1", "stock_quantity": readback_qty}

    monkeypatch.setattr(ca.service, "get_product_by_slug", get_product_by_slug)
    monkeypatch.setattr(ca.service, "list_products", list_products)
    monkeypatch.setattr(ca.service, "update_product", update_product)
    monkeypatch.setattr(ca.service, "get_product", get_product)


@pytest.mark.asyncio
async def test_update_stock_readback_confirmed(skill, emits, monkeypatch):
    _stock_service(monkeypatch, readback_qty=20)
    res = await skill._update_stock(TID, "hake", 20, confirm=True)
    assert res.get("verified") is True and res["stock_quantity"] == 20
    assert _gate_events(emits)[0]["outcome"] == "confirmed"


@pytest.mark.asyncio
async def test_update_stock_readback_mismatch(skill, emits, monkeypatch):
    _stock_service(monkeypatch, readback_qty=5)
    res = await skill._update_stock(TID, "hake", 20, confirm=True)
    assert "error" in res and "Not confirmed" in res["error"]
    e = _gate_events(emits)[0]
    assert e["task"] == "update_stock" and e["outcome"] == "mismatch"


@pytest.mark.asyncio
async def test_update_stock_without_confirm_returns_preview_and_does_not_write(skill, emits, monkeypatch):
    _stock_service(monkeypatch, readback_qty=20)
    written = {}
    async def update_product(tid, pid, patch):
        written["called"] = True
    monkeypatch.setattr(ca.service, "update_product", update_product)
    res = await skill._update_stock(TID, "hake", 20)
    assert res.get("preview") is True
    assert "called" not in written


# ── cancel_booking confirm gate ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_booking_without_confirm_returns_preview_and_does_not_write(monkeypatch):
    from vula.bookings import service as bk
    called = {}
    async def set_status(tid, booking_id, status):
        called["yes"] = True
    monkeypatch.setattr(bk, "set_status", set_status)
    skill = CommerceAdminSkill()
    res = await skill._cancel_booking(TID, "b1")
    assert res.get("preview") is True
    assert "yes" not in called


@pytest.mark.asyncio
async def test_cancel_booking_confirmed_applies(monkeypatch):
    from vula.bookings import service as bk
    called = {}
    async def set_status(tid, booking_id, status):
        called["booking_id"] = booking_id
        called["status"] = status
    monkeypatch.setattr(bk, "set_status", set_status)
    skill = CommerceAdminSkill()
    res = await skill._cancel_booking(TID, "b1", confirm=True)
    assert res == {"cancelled": True, "booking_id": "b1"}
    assert called == {"booking_id": "b1", "status": "cancelled"}


# ── create_invoice ────────────────────────────────────────────────────────────

def _invoice_service(monkeypatch, readback_row):
    async def create_invoice(tid, data):
        return {"id": "i1", "invoice_number": "INV-001", "total_cents": 10000, "status": "draft"}

    async def get_invoice(tid, invoice_id):
        return readback_row

    monkeypatch.setattr(ca.service, "create_invoice", create_invoice)
    monkeypatch.setattr(ca.service, "get_invoice", get_invoice)


_LINE_ITEMS = {"line_items": [{"description": "Hake", "quantity": 2, "unit_price_rands": 50}],
               "customer_name": "Customer"}


@pytest.mark.asyncio
async def test_create_invoice_readback_confirmed(skill, emits, monkeypatch):
    _invoice_service(monkeypatch, {"id": "i1", "invoice_number": "INV-001", "status": "draft"})
    res = await skill._create_invoice(TID, dict(_LINE_ITEMS))
    assert res.get("verified") is True and res["invoice_number"] == "INV-001"
    assert _gate_events(emits)[0]["outcome"] == "confirmed"


@pytest.mark.asyncio
async def test_create_invoice_readback_missing(skill, emits, monkeypatch):
    _invoice_service(monkeypatch, None)  # draft vanished on re-read
    res = await skill._create_invoice(TID, dict(_LINE_ITEMS))
    assert "error" in res and "Not confirmed" in res["error"]
    e = _gate_events(emits)[0]
    assert e["outcome"] == "mismatch" and e["extra"]["observed"]["found"] is False


# ── send_broadcast ────────────────────────────────────────────────────────────

class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def table(self, *a):
        return self

    def select(self, *a):
        return self

    def eq(self, *a):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


def _broadcast_setup(monkeypatch, log_rows):
    import vula.api.commerce as api_commerce

    async def admin_send_broadcast(tid, body):
        return {"broadcast_id": "b1", "recipient_count": 3, "sent": 3, "failed": 0}

    monkeypatch.setattr(api_commerce, "admin_send_broadcast", admin_send_broadcast)
    monkeypatch.setattr(ca.service, "_client", lambda: _FakeQuery(log_rows))


@pytest.mark.asyncio
async def test_send_broadcast_readback_confirmed(skill, emits, monkeypatch):
    _broadcast_setup(monkeypatch, [{"id": "b1", "status": "sent", "recipient_count": 3,
                                    "sent_count": 3, "failed_count": 0}])
    res = await skill._send_broadcast(TID, {"template_name": "weekly_special", "confirm": True})
    assert res.get("verified") is True
    assert _gate_events(emits)[0]["outcome"] == "confirmed"


@pytest.mark.asyncio
async def test_send_broadcast_readback_failed_send(skill, emits, monkeypatch):
    # admin_send_broadcast returned, but the delivery log shows nothing actually went out —
    # the old code would have reported {"sent": True} here.
    _broadcast_setup(monkeypatch, [{"id": "b1", "status": "failed", "recipient_count": 3,
                                    "sent_count": 0, "failed_count": 3}])
    res = await skill._send_broadcast(TID, {"template_name": "weekly_special", "confirm": True})
    assert "error" in res and "Not confirmed" in res["error"]
    e = _gate_events(emits)[0]
    assert e["outcome"] == "mismatch" and e["extra"]["observed"]["sent_count"] == 0


# ── kill switch + POPIA ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_readback_kill_switch(skill, emits, monkeypatch):
    monkeypatch.setattr(settings, "readback_verify_enabled", False)

    async def list_orders(tid, **kw):
        return [{"id": "o1", "display_id": "OTH-00042"}]

    async def update_order_status(order_id, status):
        return None

    async def get_order(order_id):
        raise AssertionError("read-back ran with the kill switch off")

    monkeypatch.setattr(ca.service, "list_orders", list_orders)
    monkeypatch.setattr(ca.service, "update_order_status", update_order_status)
    monkeypatch.setattr(ca.service, "get_order", get_order)

    res = await skill._update_order_status(TID, "OTH-00042", "dispatched")
    assert res == {"updated": "OTH-00042", "new_status": "dispatched"}  # pre-gate shape
    assert _gate_events(emits) == []


@pytest.mark.asyncio
async def test_gate_emit_popia_safe(skill, emits, monkeypatch):
    _order_service(monkeypatch, readback_status="dispatched")
    await skill._update_order_status(TID, "OTH-00042", "dispatched")
    extra = _gate_events(emits)[0]["extra"]
    flat = set(extra["expected"]) | set(extra["observed"])
    assert not flat & {"phone", "customer_phone", "customer_name", "name", "email", "address"}
