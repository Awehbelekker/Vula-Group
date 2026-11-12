"""The 9 Nov 2026 go-live audit's WhatsApp findings (docs/golive-audit-2026-11-09.md), each as
the case that went wrong before the fix.

H7  the apology for an unanswered question was retried every hour, forever
H8  the 24-hour template fallback had no cooldown (8-12 nudges a morning)
H9  a disconnected business's messages went out from the server's line; so did any business's
    on a database blip
H10 a shop set up on Vula's number had its owner answered by the customer shopping assistant
--  a double tap on a checkout pay button placed two orders
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from vula import escalation as esc
from vula.api import whatsapp as wa

TID = "shop-a"


# ── H7 ───────────────────────────────────────────────────────────────────────────────────────

class _EscQ:
    def __init__(self, db):
        self.db, self.filters, self.patch = db, [], None

    def select(self, *_a):
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def eq(self, c, v):
        self.filters.append(lambda r: r.get(c) == v)
        return self

    def in_(self, c, vs):
        self.filters.append(lambda r: r.get(c) in vs)
        return self

    def lt(self, c, v):
        self.filters.append(lambda r: str(r.get(c)) < v)
        return self

    def gt(self, c, v):
        self.filters.append(lambda r: str(r.get(c)) > v)
        return self

    def is_(self, c, _null):
        self.filters.append(lambda r: r.get(c) is None)
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        rows = [r for r in self.db if all(f(r) for f in self.filters)]
        for r in rows if self.patch else []:
            r.update(self.patch)
        return SimpleNamespace(data=[dict(r) for r in rows])


def _escalation(hours_old, **kw):
    when = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat()
    return {"id": f"e{hours_old}", "tenant_id": TID, "customer_phone": "27820000001", "question": "Hake?",
            "status": "open", "answered_at": None, "customer_notified_at": None, "created_at": when, **kw}


@pytest.mark.asyncio
async def test_h7_an_undeliverable_apology_is_tried_once_then_closed(monkeypatch):
    rows = [_escalation(72)]
    monkeypatch.setattr(esc, "_client", lambda: SimpleNamespace(table=lambda n: _EscQ(rows)))
    send = AsyncMock(return_value=False)       # 131047: outside the 24-hour window
    for _ in range(5):                          # five hourly ticks
        await esc.apologise_abandoned(TID, send)
    assert send.await_count == 1
    assert rows[0]["status"] == "closed" and rows[0]["customer_notified_at"] is None


@pytest.mark.asyncio
async def test_h7_a_delivered_apology_is_recorded_and_a_week_old_question_is_left_alone(monkeypatch):
    rows = [_escalation(72), _escalation(24 * 9)]
    monkeypatch.setattr(esc, "_client", lambda: SimpleNamespace(table=lambda n: _EscQ(rows)))
    send = AsyncMock(return_value=True)
    await esc.apologise_abandoned(TID, send)
    await esc.apologise_abandoned(TID, send)
    assert send.await_count == 1
    assert rows[0]["customer_notified_at"] and rows[1]["status"] == "open"


# ── H8 ───────────────────────────────────────────────────────────────────────────────────────

class _Http:
    def __init__(self, sink):
        self.sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.sink.append(url)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"messages": [{"id": "w"}]})


@pytest.mark.asyncio
async def test_h8_the_template_nudge_goes_once_a_day_per_number(monkeypatch):
    sink = []
    monkeypatch.setattr(wa.settings, "whatsapp_notify_template", "vula_new_message")
    monkeypatch.setattr(wa, "_record_outbound", lambda *a, **k: None)
    monkeypatch.setattr(wa.httpx, "AsyncClient", lambda **k: _Http(sink))
    monkeypatch.setattr("vula.commerce.service._client", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    wa._sent_keys.clear()
    creds = {"token": "t", "phone_id": "P"}
    for _ in range(8):                          # the digest's retries over one morning
        await wa._send_notify_template(creds, "27820000009", TID)
    await wa._send_notify_template(creds, "27820000010", TID)
    assert len(sink) == 2


# ── H9 ───────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_h9_a_disconnected_business_sends_nothing_from_the_servers_line(monkeypatch):
    sink = []
    monkeypatch.setattr(wa, "_get_tenant_wa_creds", AsyncMock(return_value=None))
    monkeypatch.setattr(wa.settings, "whatsapp_token", "env-tok")
    monkeypatch.setattr(wa.settings, "whatsapp_phone_id", "SERVER-LINE")
    monkeypatch.setattr(wa.httpx, "AsyncClient", lambda **k: _Http(sink))
    assert await wa._send_reply("27820000001", "Your order is ready", TID) is False
    assert await wa.send_template(TID, "27820000001", "order_ready", ["x"]) is False
    assert await wa._send_wa_template(TID, "27820000001", "order_ready", "x") is False
    assert await wa._resolve_wa(TID) is None
    assert sink == []


@pytest.mark.asyncio
async def test_h9_without_a_business_the_servers_line_still_answers(monkeypatch):
    monkeypatch.setattr(wa.settings, "whatsapp_token", "env-tok")
    monkeypatch.setattr(wa.settings, "whatsapp_phone_id", "SERVER-LINE")
    assert (await wa._send_creds(""))["phone_id"] == "SERVER-LINE"


@pytest.mark.asyncio
async def test_h9_a_database_blip_uses_the_last_known_number_or_sends_nothing(monkeypatch):
    import supabase
    monkeypatch.undo()       # conftest stubs _get_tenant_wa_creds; this test is about the real one
    monkeypatch.setattr(wa.settings, "whatsapp_token", "env-tok")
    monkeypatch.setattr(wa.settings, "whatsapp_phone_id", "SERVER-LINE")
    monkeypatch.setattr(supabase, "create_client", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blip")))
    monkeypatch.setattr("vula.integrations.platform_support.platform_creds", lambda: None)
    wa._wa_creds_cache.pop(TID, None)
    wa._WA_CREDS_AT.pop(TID, None)
    assert await wa._get_tenant_wa_creds(TID) is None                    # no guess

    wa._wa_creds_cache[TID] = [{"tenant_id": TID, "phone_number_id": "SHOP-A-LINE", "status": "connected",
                                "is_default": True, "access_token": None}]
    wa._WA_CREDS_AT[TID] = 0                                              # stale
    try:
        assert (await wa._get_tenant_wa_creds(TID))["phone_id"] == "SHOP-A-LINE"
    finally:
        wa._wa_creds_cache.pop(TID, None)
        wa._WA_CREDS_AT.pop(TID, None)


# ── H10 ──────────────────────────────────────────────────────────────────────────────────────

def test_h10_a_shop_on_vulas_number_is_handled_as_a_shop(monkeypatch):
    monkeypatch.setattr("vula.onboard_wa._sells", lambda t: t == "fish-shop")
    assert wa._platform_route_mode("fish-shop") == "commerce"      # owner -> admin agent
    assert wa._platform_route_mode("architects") == "knowledge"


def test_h10_the_webhook_uses_it():
    import inspect
    src = inspect.getsource(wa)
    assert 'route_mode = await _aio2.to_thread(_platform_route_mode, _as_tenant)' in src
    assert 'route_tenant, route_mode = _as_tenant, "knowledge"' not in src


# ── checkout: one order per cart ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_double_tap_on_pay_places_one_order(monkeypatch):
    from vula.commerce import button_checkout as bc
    cart = {"id": "cart-1", "delivery_address": bc.COLLECTION,
            "commerce_cart_items": [{"id": "i1", "quantity": 1}]}
    placed, said = [], []

    async def place(tid, phone, cust, args, ctx=None):
        placed.append(args["payment_method"])
        await asyncio.sleep(0.05)               # Meta + the database take a moment
        return {"confirmation": "Order SHA-0001 placed"}
    monkeypatch.setattr(bc, "_cart", AsyncMock(return_value=cart))
    monkeypatch.setattr(bc, "_text", AsyncMock(side_effect=lambda p, t, m: said.append(m)))
    monkeypatch.setattr(bc, "_remember", AsyncMock())
    monkeypatch.setattr("core.skills.loader.get_skill", lambda n: SimpleNamespace(_exec_place_order=place))
    monkeypatch.setattr("vula.commerce.service._client", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    wa._sent_keys.clear()
    await asyncio.gather(bc._place("27820000001", TID, "eft"), bc._place("27820000001", TID, "card"))
    assert len(placed) == 1
    assert any("already being placed" in m for m in said)


@pytest.mark.asyncio
async def test_a_placement_that_didnt_go_through_can_be_tried_again(monkeypatch):
    from vula.commerce import button_checkout as bc
    cart = {"id": "cart-2", "delivery_address": bc.COLLECTION, "commerce_cart_items": [{"id": "i1"}]}
    results = [{"error": "Minimum order is R200"}, {"confirmation": "Order placed"}]
    place = AsyncMock(side_effect=lambda *a, **k: results.pop(0))
    monkeypatch.setattr(bc, "_cart", AsyncMock(return_value=cart))
    monkeypatch.setattr(bc, "_text", AsyncMock())
    monkeypatch.setattr(bc, "_remember", AsyncMock())
    monkeypatch.setattr("core.skills.loader.get_skill", lambda n: SimpleNamespace(_exec_place_order=place))
    monkeypatch.setattr("vula.commerce.service._client", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    wa._sent_keys.clear()
    await bc._place("27820000001", TID, "eft")
    await bc._place("27820000001", TID, "eft")
    assert place.await_count == 2


@pytest.mark.asyncio
async def test_two_claims_at_once_get_one_even_when_the_database_is_slow_and_down(monkeypatch):
    """The checkout's double-tap guard failed on CI (12 Nov 2026): callers claim in threads, and two
    at once both found the key free while the first was still waiting on a database that then
    failed. The key is now reserved in memory, under a lock, before the database is asked."""
    import time as _time

    def slow_then_down():
        _time.sleep(0.05)
        raise RuntimeError("no db")
    monkeypatch.setattr("vula.commerce.service._client", slow_then_down)
    wa._sent_keys.clear()
    got = await asyncio.gather(*[asyncio.to_thread(wa._claim_outbound, TID, "place-order:cart-9") for _ in range(4)])
    assert sorted(got) == [False, False, False, True]
