"""Phase 1 confirm gates: tools that message a customer, change money records or cancel/refund
an order preview first (2026-09-25 review) — same preview:true contract that raises the
WhatsApp Confirm/Cancel buttons."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill

TID = "t1"


@pytest.fixture
def skill():
    return CommerceAdminSkill()


def _db(rows):
    q = MagicMock()
    for m in ("select", "eq", "limit", "table"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=rows)
    return q


@pytest.mark.asyncio
async def test_send_invoice_previews_then_sends(skill):
    db = _db([{"id": "i1", "invoice_number": "INV-1", "customer_phone": "2782", "customer_name": "Jo",
               "total_cents": 12345}])
    send = AsyncMock()
    with patch.object(ca.service, "_client", return_value=db), \
         patch("vula.api.commerce.admin_send_invoice_whatsapp", send):
        p = await skill._send_invoice(TID, "INV-1")
        assert p["preview"] is True and p["customer"] == "Jo"
        send.assert_not_awaited()
        r = await skill._send_invoice(TID, "INV-1", confirm=True)
    assert r == {"sent": True, "invoice_number": "INV-1"}
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_order_needs_confirm_but_dispatch_does_not(skill, monkeypatch):
    upd = AsyncMock()
    monkeypatch.setattr(ca.service, "list_orders",
                        AsyncMock(return_value=[{"id": "o1", "display_id": "OTH-1", "status": "paid"}]))
    monkeypatch.setattr(ca.service, "update_order_status", upd)
    monkeypatch.setattr(ca.settings, "readback_verify_enabled", False)
    p = await skill._update_order_status(TID, "OTH-1", "cancelled")
    assert p["preview"] is True
    upd.assert_not_awaited()
    await skill._update_order_status(TID, "OTH-1", "cancelled", confirm=True)
    await skill._update_order_status(TID, "OTH-1", "dispatched")
    assert upd.await_count == 2


@pytest.mark.asyncio
async def test_add_expense_previews_then_books_through_create_claim(skill):
    claim = AsyncMock(return_value={"id": "e1", "category": "fuel"})
    with patch("vula.commerce.expenses.create_claim", claim):
        p = await skill._add_expense(TID, {"amount_rands": 250, "description": "Diesel"})
        assert p["preview"] is True and p["amount"]
        claim.assert_not_awaited()
        r = await skill._add_expense(TID, {"amount_rands": 250, "description": "Diesel", "confirm": True})
    assert claim.await_args.kwargs["amount_cents"] == 25000
    assert r["logged"] and r["description"] == "Diesel"


@pytest.mark.asyncio
async def test_add_expense_duplicate_is_reported_not_rebooked(skill):
    with patch("vula.commerce.expenses.create_claim", AsyncMock(return_value={"id": "e0", "duplicate": True})):
        r = await skill._add_expense(TID, {"amount_rands": 250, "description": "Diesel", "confirm": True})
    assert r["duplicate"] is True
