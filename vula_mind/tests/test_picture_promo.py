"""Picture promotion (vula/commerce/promo.py): a product photo, the offer and a Shop now button,
on the shop's approved promotion template — Caught Online style (Ian, 13 Oct)."""
import re

import pytest

from vula.api import commerce as api
from vula.commerce import promo, wa_templates, web_basket

TID = "seedleme"
BASINS = [{"id": "p-1", "name": "Round Drop-In Basin - Sage", "image_url": "https://cdn.shopify.com/a.jpg"},
          {"id": "p-2", "name": "Round Drop-In Basin", "image_url": "https://cdn.shopify.com/b.jpg"},
          {"id": "p-3", "name": "Countertop Butler", "image_url": None}]


@pytest.fixture
def env(monkeypatch):
    out = {"calls": [], "status": "APPROVED"}

    async def products(tid, category=None):
        return BASINS

    async def templates(tid):
        return [{"name": "seedleme_shop_promo", "status": out["status"]}]

    async def broadcast(tid, body):
        out["calls"].append(body)
        return {"dry_run": body["dry_run"], "recipient_count": 42, "suppressed_count": 3, "sent": 0 if body["dry_run"] else 42}

    monkeypatch.setattr(web_basket, "shop_products", products)
    monkeypatch.setattr(web_basket, "shop_url", lambda tid, anchor="": "https://shop.seedleme.com/")
    monkeypatch.setattr(wa_templates, "list_templates", templates)
    monkeypatch.setattr(api, "admin_send_broadcast", broadcast)
    return out


def test_template_meets_metas_rules():
    t = wa_templates.promo_template(TID)
    assert t["name"] == "seedleme_shop_promo" and t["category"] == "MARKETING" and t["header_type"] == "IMAGE"
    assert wa_templates._placeholder_count(t["body"]) == 2
    assert not re.match(r"^\s*\{\{", t["body"]) and not re.search(r"\}\}\s*[.!]?\s*$", t["body"])
    assert t["buttons"][0]["text"] == "Shop now" and t["buttons"][0]["url"].endswith("/l/{{1}}")


def test_offer_text_is_one_line():
    assert promo.clean("15% off\n\nall basins\tthis weekend   only!") == "15% off all basins this weekend only!"


@pytest.mark.asyncio
async def test_preview_uses_the_named_products_photo(env):
    res = await promo.send(TID, "15% off this weekend!", product_hint="round drop-in basin")
    assert res["product"] == "Round Drop-In Basin"           # the closest name, not a colour variant
    assert res["image_url"] == "https://cdn.shopify.com/b.jpg?width=800"
    body = env["calls"][0]
    assert body["dry_run"] is True and body["template_name"] == "seedleme_shop_promo"
    assert body["template_params"] == ["{name}", "15% off this weekend!"]
    assert body["target_url"] == "https://shop.seedleme.com/" and body["header_image_url"] == res["image_url"]
    assert res["recipient_count"] == 42


@pytest.mark.asyncio
async def test_not_sent_until_meta_approves(env):
    env["status"] = "PENDING"
    res = await promo.send(TID, "Winter special", dry_run=False)
    assert "isn't approved by Meta yet" in res["error"] and not env["calls"]


@pytest.mark.asyncio
async def test_unknown_product_is_said_not_guessed(env):
    res = await promo.send(TID, "Half price", product_hint="bathtub")
    assert "couldn't find a product called 'bathtub'" in res["error"] and not env["calls"]


def test_recipient_first_name_fills_the_greeting():
    params = api._template_body_params({"param_count": 2}, {"name": "Thandi Mokoena"},
                                       ["{name}", "15% off this weekend!"])
    assert [p["text"] for p in params] == ["Thandi", "15% off this weekend!"]


@pytest.mark.asyncio
async def test_owner_on_whatsapp_previews_then_sends(env, monkeypatch):
    import core.skills.commerce_admin as ca
    skill = ca.CommerceAdminSkill()
    prev = await skill._picture_promotion(TID, {"message": "15% off this weekend!", "product": "Round Drop-In Basin"},
                                          "27662545068")
    assert prev["preview"] and prev["reaches"] == 42 and "Round Drop-In Basin" in prev["picture"]
    test = await skill._picture_promotion(TID, {"message": "15% off this weekend!", "test": True}, "27662545068")
    assert env["calls"][-1]["test_phone"] == "27662545068" and "Test sent" in test["reply_verbatim"]
    sent = await skill._picture_promotion(TID, {"message": "15% off this weekend!", "confirm": True}, "27662545068")
    assert sent["reply_verbatim"] == "📣 Promotion sent to 42 of 42 customers."
