"""Bringing customers back (vula/commerce/bring_back.py): the WhatsApp cart they left, and
"order again?" — opted-out customers never, each at most once."""
from datetime import datetime, timedelta, timezone

import pytest

import vula.api.whatsapp as wa
from vula.api import commerce as api
from vula.commerce import bring_back, job_config, service

TID = "seedleme"


def _ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _cart(cid, session, updated, phone=None):
    return {"id": cid, "session_id": session, "customer_phone": phone, "updated_at": updated,
            "commerce_cart_items": [{"quantity": 2, "unit_price_cents": 320000,
                                     "commerce_products": {"name": "Round Drop-In Basin",
                                                           "image_url": "https://cdn.shopify.com/b.jpg"}}]}


@pytest.fixture
def env(monkeypatch):
    out = {"buttons": [], "templates": [], "claimed": set(), "opted_out": set(), "carts": [], "orders": [],
           "latest": {}}

    async def resolve(tid):
        return {"phone_id": "1", "token": "t"}

    async def buttons(creds, number, body, btns, image_url=None):
        out["buttons"].append({"to": number, "body": body, "ids": [b["id"] for b in btns], "image": image_url})
        return True

    async def template(tid, phone, name, params, **kw):
        out["templates"].append((phone, name, params))
        return True

    def claim(tid, key):
        if key in out["claimed"]:
            return False
        out["claimed"].add(key)
        return True

    async def carts(tid, hours_old=1):
        return out["carts"]

    async def candidates(tid, days_ago=7):
        return out["orders"]

    monkeypatch.setattr(wa, "_resolve_wa", resolve)
    monkeypatch.setattr(wa, "_send_wa_buttons", buttons)
    monkeypatch.setattr(wa, "send_template", template)
    monkeypatch.setattr(wa, "_claim_outbound", claim)
    monkeypatch.setattr(wa, "_release_outbound", lambda tid, key: out["claimed"].discard(key))
    monkeypatch.setattr(api, "_suppressed_phones", lambda tid: out["opted_out"])
    monkeypatch.setattr(service, "get_abandoned_carts", carts)
    monkeypatch.setattr(service, "get_reorder_candidates", candidates)
    monkeypatch.setattr(service, "orders_for_phone",
                        lambda tid, phone, cols="*", limit=20, **k: out["latest"].get(phone[-9:], []))
    monkeypatch.setattr("vula.api.tenants.display_name", lambda tid: "SeedleMe")
    return out


@pytest.mark.asyncio
async def test_left_cart_gets_one_reminder_with_photo_and_checkout(env):
    env["carts"] = [_cart("c1", "27821234567", _ago(hours=3))]
    assert await bring_back.basket_reminders(TID) == 1
    msg = env["buttons"][0]
    assert msg["to"] == "27821234567" and msg["ids"] == ["cart_checkout", "cart_more"]
    assert "2 × Round Drop-In Basin" in msg["body"] and "Subtotal: R6,400.00" in msg["body"]
    assert msg["image"] == "https://cdn.shopify.com/b.jpg?width=800"
    assert await bring_back.basket_reminders(TID) == 0          # once per cart


@pytest.mark.asyncio
async def test_no_reminder_outside_the_window_for_web_baskets_or_opted_out(env):
    env["opted_out"] = {"27829999999"}
    env["carts"] = [_cart("old", "27821234567", _ago(hours=30)),          # past the 24h window
                    _cart("web", "web-K7Q2MX", _ago(hours=3)),            # no phone to message
                    _cart("man", "manual-ab12", _ago(hours=3), "27821111111"),
                    _cart("out", "27829999999", _ago(hours=3))]           # opted out
    assert await bring_back.basket_reminders(TID) == 0 and not env["buttons"]


@pytest.mark.asyncio
async def test_order_again_nudge_on_the_template(env):
    env["orders"] = [{"id": "o1", "customer_phone": "0821234567", "customer_name": "Thandi Mokoena",
                      "commerce_order_items": [{"product_name": "Round Drop-In Basin"},
                                               {"product_name": "Countertop Butler"}]}]
    env["latest"] = {"821234567": [{"id": "o1"}]}
    assert await bring_back.reorder_nudges(TID, "seedleme_reorder_nudge") == 1
    assert env["templates"] == [("27821234567", "seedleme_reorder_nudge",
                                 ["Thandi", "SeedleMe", "Round Drop-In Basin, Countertop Butler"])]
    assert await bring_back.reorder_nudges(TID, "seedleme_reorder_nudge") == 0


@pytest.mark.asyncio
async def test_no_nudge_after_a_newer_order_or_an_opt_out(env):
    env["orders"] = [{"id": "o1", "customer_phone": "27821234567", "customer_name": "Thandi",
                      "commerce_order_items": []},
                     {"id": "o2", "customer_phone": "27839999999", "customer_name": "Sipho",
                      "commerce_order_items": []}]
    env["latest"] = {"821234567": [{"id": "o9"}], "839999999": [{"id": "o2"}]}
    env["opted_out"] = {"27839999999"}
    assert await bring_back.reorder_nudges(TID, "seedleme_reorder_nudge") == 0


def test_defaults_basket_reminder_for_shops_and_order_again_off(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["orders", "wa_shop"] if tid == TID else ["orders"])
    assert job_config.effective_config(TID, "basket_reminder", None)["enabled"] is True
    assert job_config.effective_config("off-the-hook", "basket_reminder", None)["enabled"] is False
    assert job_config.effective_config(TID, "reorder_nudge", None)["enabled"] is False
    assert job_config.effective_config(TID, "reorder_nudge", {"enabled": True})["enabled"] is True
