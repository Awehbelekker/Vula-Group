"""Tests for the 2026-08-24 confirm-gate widening (Phase 5): create_product, update_product,
create_booking, create_subscription previously executed on the first tool call with no
confirm=true gate at all. Mirrors the established preview/confirm/readback test style already
proven this session (test_commerce_admin_gate.py, test_commerce_admin_purchase_orders.py, etc.).
"""
from unittest.mock import patch

import pytest

from config import settings
import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill

TID = "test-tenant"


@pytest.fixture
def skill():
    return CommerceAdminSkill()


# ── create_product ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_product_without_confirm_returns_preview_and_does_not_write(skill, monkeypatch):
    called = {}
    async def create_product(tid, data):
        called["yes"] = True
        return {"name": data["name"], "price_cents": data["price_cents"]}
    monkeypatch.setattr(ca.service, "create_product", create_product)

    res = await skill._create_product(TID, {"name": "Hake Fillets", "price_rands": 85})
    assert res.get("preview") is True
    assert "yes" not in called


@pytest.mark.asyncio
async def test_create_product_confirmed_creates(skill, monkeypatch):
    async def create_product(tid, data):
        return {"name": data["name"], "price_cents": data["price_cents"]}
    monkeypatch.setattr(ca.service, "create_product", create_product)

    res = await skill._create_product(TID, {"name": "Hake Fillets", "price_rands": 85, "confirm": True})
    assert res.get("created") == "Hake Fillets"


# ── update_product ─────────────────────────────────────────────────────────────────

def _update_product_service(monkeypatch, readback_price_cents):
    async def list_products(tid, **kw):
        return [{"id": "p1", "name": "Hake Fillets", "price_cents": 8000}]

    async def update_product(tid, pid, patch):
        return None

    async def get_product(tid, pid):
        return {"id": "p1", "price_cents": readback_price_cents}

    monkeypatch.setattr(ca.service, "list_products", list_products)
    monkeypatch.setattr(ca.service, "update_product", update_product)
    monkeypatch.setattr(ca.service, "get_product", get_product)


@pytest.mark.asyncio
async def test_update_product_without_confirm_returns_preview_and_does_not_write(skill, monkeypatch):
    written = {}
    async def list_products(tid, **kw):
        return [{"id": "p1", "name": "Hake Fillets", "price_cents": 8000}]
    async def update_product(tid, pid, patch):
        written["called"] = True
    monkeypatch.setattr(ca.service, "list_products", list_products)
    monkeypatch.setattr(ca.service, "update_product", update_product)

    res = await skill._update_product(TID, {"product": "hake", "price_rands": 95})
    assert res.get("preview") is True
    assert res.get("current_price") == "R80.00"
    assert res.get("new_price") == "R95.00"
    assert "called" not in written


@pytest.mark.asyncio
async def test_update_product_confirmed_readback_confirmed(skill, monkeypatch):
    _update_product_service(monkeypatch, readback_price_cents=9500)
    res = await skill._update_product(TID, {"product": "hake", "price_rands": 95, "confirm": True})
    assert res.get("verified") is True
    assert res.get("new_price") == "R95.00"


@pytest.mark.asyncio
async def test_update_product_confirmed_readback_mismatch(skill, monkeypatch):
    _update_product_service(monkeypatch, readback_price_cents=8000)  # write didn't stick
    res = await skill._update_product(TID, {"product": "hake", "price_rands": 95, "confirm": True})
    assert "error" in res and "Not confirmed" in res["error"]


@pytest.mark.asyncio
async def test_update_product_readback_kill_switch(skill, monkeypatch):
    monkeypatch.setattr(settings, "readback_verify_enabled", False)
    try:
        async def list_products(tid, **kw):
            return [{"id": "p1", "name": "Hake Fillets", "price_cents": 8000}]
        async def update_product(tid, pid, patch):
            return None
        async def get_product(tid, pid):
            raise AssertionError("read-back ran with the kill switch off")
        monkeypatch.setattr(ca.service, "list_products", list_products)
        monkeypatch.setattr(ca.service, "update_product", update_product)
        monkeypatch.setattr(ca.service, "get_product", get_product)

        res = await skill._update_product(TID, {"product": "hake", "price_rands": 95, "confirm": True})
        assert res == {"updated": "Hake Fillets", "new_price": "R95.00"}
    finally:
        monkeypatch.setattr(settings, "readback_verify_enabled", True)


# ── create_booking ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_booking_without_confirm_returns_preview_and_does_not_write(skill, monkeypatch):
    from vula.bookings import service as bk
    called = {}
    async def list_services(tid):
        return []
    async def create_booking(tid, data):
        called["yes"] = True
        return {"booking": {"start_local": "10:00", "service_name": None}}
    monkeypatch.setattr(bk, "list_services", list_services)
    monkeypatch.setattr(bk, "create_booking", create_booking)

    res = await skill._create_booking(TID, {"start": "2026-08-25T10:00", "customer_name": "Jane"})
    assert res.get("preview") is True
    assert "yes" not in called


@pytest.mark.asyncio
async def test_create_booking_confirmed_books(skill, monkeypatch):
    from vula.bookings import service as bk
    async def list_services(tid):
        return []
    async def create_booking(tid, data):
        return {"booking": {"start_local": "10:00", "service_name": None}}
    monkeypatch.setattr(bk, "list_services", list_services)
    monkeypatch.setattr(bk, "create_booking", create_booking)

    res = await skill._create_booking(TID, {"start": "2026-08-25T10:00", "customer_name": "Jane", "confirm": True})
    assert res == {"booked": True, "when": "10:00", "service": None}


# ── create_subscription ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_subscription_without_confirm_returns_preview_and_does_not_write(skill, monkeypatch):
    from vula.commerce import subscriptions as subs
    async def list_products(tid, **kw):
        return [{"id": "p1", "name": "Hake Fillets", "price_cents": 8000}]
    called = {}
    async def create(tid, data):
        called["yes"] = True
        return {"subscription": {"cadence": "weekly", "next_run": "2026-09-01"}}
    monkeypatch.setattr(ca.service, "list_products", list_products)
    monkeypatch.setattr(subs, "create", create)

    res = await skill._create_subscription(TID, {
        "customer_phone": "0821234567", "cadence": "weekly",
        "items": [{"product": "hake", "quantity": 2}]})
    assert res.get("preview") is True
    assert "yes" not in called


@pytest.mark.asyncio
async def test_create_subscription_confirmed_creates(skill, monkeypatch):
    from vula.commerce import subscriptions as subs
    async def list_products(tid, **kw):
        return [{"id": "p1", "name": "Hake Fillets", "price_cents": 8000}]
    async def create(tid, data):
        return {"subscription": {"cadence": "weekly", "next_run": "2026-09-01"}}
    monkeypatch.setattr(ca.service, "list_products", list_products)
    monkeypatch.setattr(subs, "create", create)

    res = await skill._create_subscription(TID, {
        "customer_phone": "0821234567", "cadence": "weekly",
        "items": [{"product": "hake", "quantity": 2}], "confirm": True})
    assert res.get("created") is True
