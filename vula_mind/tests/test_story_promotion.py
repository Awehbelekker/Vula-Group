"""Off the Hook, 24 Oct: Vula broadcasts like the business does.

Ian shared Staci's posts so Vula would learn how she broadcasts to her clients: a CAPS headline
between emoji, a short description, a call to action. The old promotion template squashed that
into one line. A shop now gets its own layout template (shop_story), drafts in its own style from
its saved posts, and every price or day in a draft must come from the owner or the catalogue.
"""
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vula.api import commerce as api
from vula.commerce import promo, wa_templates, web_basket

TID = "off-the-hook"
TUNA = [{"id": "t1", "name": "Yellowfin Tuna Steaks", "price_cents": 32000,
         "image_url": "https://x/product-images/off-the-hook/products/1-ai-tuna.jpg"}]
PARTS = {"headline": "🐟 FRESH TUNA – LIMITED STOCK! 🐟",
         "body": "Yellowfin tuna steaks just landed — perfect for the braai.",
         "cta": "Order now while it lasts!"}


@pytest.fixture
def env(monkeypatch):
    out = {"calls": [], "status": {"off-the-hook_shop_promo": "APPROVED", "off-the-hook_shop_story": "APPROVED"}}

    async def products(tid, category=None):
        return TUNA

    async def templates(tid):
        return [{"name": n, "status": s} for n, s in out["status"].items()]

    async def broadcast(tid, body):
        out["calls"].append(body)
        return {"dry_run": body["dry_run"], "recipient_count": 12, "suppressed_count": 0,
                "sent": 0 if body["dry_run"] else 12}

    monkeypatch.setattr(web_basket, "shop_products", products)
    monkeypatch.setattr(web_basket, "shop_url", lambda tid, anchor="": "https://offthehook.co.za/")
    monkeypatch.setattr(wa_templates, "list_templates", templates)
    monkeypatch.setattr(api, "admin_send_broadcast", broadcast)
    monkeypatch.setattr("vula.commerce.job_config.template_name", lambda t, s: f"{t}_{s}")
    monkeypatch.setattr(promo, "uninvited_count", lambda t: 718)
    return out


def test_story_template_meets_metas_rules():
    t = wa_templates.story_template(TID)
    assert t["category"] == "MARKETING" and t["header_type"] == "IMAGE"
    assert wa_templates._placeholder_count(t["body"]) == 4 == len(wa_templates.STORY_EXAMPLE)
    assert not re.match(r"^\s*\{\{", t["body"]) and not re.search(r"\}\}\s*[.!]?\s*$", t["body"])
    assert "\n\n*{{2}}*\n\n{{3}}\n\n👉 {{4}}" in t["body"]          # her layout lives in the template
    assert t["buttons"][0]["text"] == "Shop now"


def test_parts_are_one_line_and_checked():
    out = promo.check_parts({
        "headline": "🐟 FRESH\nTUNA 🐟",
        "body": "Tuna steaks just landed. Only R250/kg this week! Perfect for the braai.",
        "cta": "Order before 12pm for delivery tomorrow."}, brief="promote the tuna", trusted="Yellowfin R320.00")
    assert out["headline"] == "🐟 FRESH TUNA 🐟"
    assert "R250" not in out["body"] and "Perfect for the braai." in out["body"]  # price not in catalogue
    assert out["cta"] == ""                                       # a time and day the owner didn't give
    kept = promo.check_parts({"headline": "TUNA", "body": "Steaks at R320 a kilo.", "cta": "Order by Friday"},
                             brief="tuna special, order by friday", trusted="R320.00")
    assert kept["body"] == "Steaks at R320 a kilo." and kept["cta"] == "Order by Friday"


@pytest.mark.asyncio
async def test_draft_uses_her_posts_and_the_catalogue(monkeypatch):
    seen = {}

    async def fake_completion(**kw):
        seen["prompt"] = kw["messages"][0]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
            '{"headline": "🐟 FRESH TUNA – LIMITED STOCK! 🐟", "body": "Yellowfin steaks at R320/kg.", '
            '"cta": "Order now!"}')))])

    monkeypatch.setattr("vula.commerce.promotions.examples", lambda t, n=5: ["🐟 FRESH KINGKLIP – R280/kg 🐟"])
    monkeypatch.setattr("vula.api.tenants.get_config", lambda t, fresh=False: {
        "persona_prompt": "Chat replies: Warm.\nPromotions: CAPS headline between fish emoji."})
    monkeypatch.setattr("core.llm_router.resolve_generation_route", AsyncMock(return_value=("m", None, None)))
    monkeypatch.setattr("litellm.acompletion", fake_completion)
    parts = await promo.draft(TID, "promote the fresh tuna", TUNA[0])
    assert parts == {"headline": "🐟 FRESH TUNA – LIMITED STOCK! 🐟", "body": "Yellowfin steaks at R320/kg.",
                     "cta": "Order now!"}
    assert "CAPS headline between fish emoji" in seen["prompt"] and "PAST_POST_1" in seen["prompt"]
    assert "R280" not in seen["prompt"]                            # old post prices aren't passed on


@pytest.mark.asyncio
async def test_her_layout_goes_out_once_approved(env):
    res = await promo.send(TID, "", parts=PARTS)
    body = env["calls"][-1]
    assert res["story"] and body["template_name"] == "off-the-hook_shop_story"
    assert body["template_params"] == ["{name}", PARTS["headline"], PARTS["body"], PARTS["cta"]]


@pytest.mark.asyncio
async def test_until_approved_it_goes_as_one_line(env):
    env["status"]["off-the-hook_shop_story"] = "PENDING"
    res = await promo.send(TID, "", parts=PARTS)
    body = env["calls"][-1]
    assert not res["story"] and body["template_name"] == "off-the-hook_shop_promo"
    assert body["template_params"] == ["{name}", " ".join(PARTS.values())]


@pytest.mark.asyncio
async def test_owner_preview_shows_her_layout_reach_and_warnings(env, monkeypatch):
    import core.skills.commerce_admin as ca
    monkeypatch.setattr(promo, "draft", AsyncMock(return_value=dict(PARTS)))
    monkeypatch.setattr("vula.commerce.service.list_products", AsyncMock(return_value=TUNA))
    skill = ca.CommerceAdminSkill()
    args = {"brief": "send everyone a promo for the fresh tuna", "product": "tuna"}
    prev = await skill._picture_promotion(TID, args, "27645755210")
    assert prev["preview"] and prev["reaches"] == 12 and all(not c.get("test_phone") for c in env["calls"])
    assert env["calls"][-1]["dry_run"] is True
    assert prev["message_customers_get"].startswith("Hi <name>! 👋\n\n*🐟 FRESH TUNA – LIMITED STOCK! 🐟*")
    assert "718 imported contacts haven't been invited" in prev["notes"]
    assert "still an AI picture" in prev["notes"]
    assert args["headline"] == PARTS["headline"]                   # Confirm sends exactly this draft
    promo.draft.reset_mock()
    sent = await skill._picture_promotion(TID, {**args, "confirm": True}, "27645755210")
    assert not promo.draft.called and env["calls"][-1]["dry_run"] is False
    assert env["calls"][-1]["template_params"][1] == PARTS["headline"]
    assert sent["reply_verbatim"] == "📣 Promotion sent to 12 of 12 customers."


@pytest.mark.asyncio
async def test_story_template_is_submitted_for_shops(monkeypatch):
    created = []
    monkeypatch.setattr(wa_templates, "_waba_creds", AsyncMock(return_value={"waba_id": "w", "token": "t"}))
    monkeypatch.setattr(wa_templates, "_waba_template_names", AsyncMock(return_value=set()))
    monkeypatch.setattr(wa_templates, "_create_raw", AsyncMock(return_value={"id": "x"}))
    monkeypatch.setattr(wa_templates, "_promo_sample_image", lambda t: "https://x/p.jpg")
    monkeypatch.setattr(wa_templates, "create_template",
                        AsyncMock(side_effect=lambda t, name, *a, **k: created.append(name) or {"id": name}))
    monkeypatch.setattr("vula.api.tenants.get_config", lambda t, fresh=False: {"business_type": "food",
                                                                                "modules": ["orders", "products"]})
    monkeypatch.setattr("vula.api.tenants._effective_modules", lambda cfg: cfg["modules"])
    monkeypatch.setattr("vula.commerce.job_config.template_name", lambda t, s: f"oth_{s}")
    res = await wa_templates.submit_standard(TID)
    assert "oth_shop_story" in created and "oth_shop_promo" in created and not res.get("failed")
