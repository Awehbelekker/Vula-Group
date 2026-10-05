"""Fixes from Ian's 5 Oct test chat on Off the Hook, each on the real wording."""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.base import SkillInput, contentless
from core.skills.commerce_assistant import CommerceAssistantSkill

CENTRE_CUTS = {"id": "p2", "name": "Hake Centre Cuts", "slug": "hake-centre-cuts",
               "price_cents": 22000, "sold_by": "kg"}
FILLETS = {"id": "p1", "name": "Hake Fillets", "slug": "hake-fillets", "price_cents": 16000, "sold_by": "kg"}


async def _add(args):
    add = AsyncMock()
    with patch("core.skills.commerce_assistant.service.get_product_by_slug", new=AsyncMock(return_value=None)), \
         patch("core.skills.commerce_assistant.service.list_products",
               new=AsyncMock(return_value=[FILLETS, CENTRE_CUTS])), \
         patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value={"id": "c1"})), \
         patch("core.skills.commerce_assistant.service.add_to_cart", new=add):
        out = await CommerceAssistantSkill()._exec_add_to_cart("off-the-hook", "s1", "2782", args)
    return out, add


# ── 1. "I'd like to order Hake Centre Cuts" — no amount, so nothing is added ──
@pytest.mark.asyncio
async def test_no_quantity_adds_nothing_and_asks_for_kg():
    out, add = await _add({"product": "Hake Centre Cuts"})
    assert out["needs_quantity"] and "how many kg" in out["instruction"]
    add.assert_not_called()


@pytest.mark.asyncio
async def test_a_stated_quantity_is_added():
    out, add = await _add({"product": "Hake Centre Cuts", "quantity": 1.5})
    assert out["added"] == "Hake Centre Cuts" and "note" not in out
    add.assert_called_once()


# ── 2. A bare 👍 is never the whole reply ──────────────────────────────────
@pytest.mark.parametrize("text,empty", [("👍", True), ("👍🙂!", True), ("", True),
                                        ("👍 Added!", False), ("1kg", False)])
def test_contentless(text, empty):
    assert contentless(text) is empty


def _skill(monkeypatch, answer, cart):
    from core.skills import commerce_assistant as ca
    skill = CommerceAssistantSkill()
    monkeypatch.setattr(ca, "_is_booking_focused", lambda t: False)
    monkeypatch.setattr(ca, "_tenant_has_bookings", lambda t: False)
    monkeypatch.setattr(skill, "_retrieve_kb", AsyncMock(return_value=("", [])))
    monkeypatch.setattr(skill, "_agent_loop", AsyncMock(return_value=answer))
    monkeypatch.setattr(skill, "_exec_view_cart", AsyncMock(return_value=cart))
    return skill


CART = {"items": [{"name": "Hake Centre Cuts", "quantity": "1kg", "line_total": "R220.00"}],
        "subtotal": "R220.00", "delivery": "R50.00", "total": "R270.00"}


@pytest.mark.asyncio
async def test_bare_thumbs_up_after_yes_becomes_the_cart_and_next_step(monkeypatch):
    skill = _skill(monkeypatch, "👍", CART)
    out = await skill.run(SkillInput(question="Yes", tenant_id="off-the-hook",
                                     metadata={"customer_phone": "27645755210"}))
    assert "1kg Hake Centre Cuts — R220.00" in out.answer and "R270.00" in out.answer
    assert "place order" in out.answer


@pytest.mark.asyncio
async def test_thumbs_up_to_thanks_is_left_alone(monkeypatch):
    skill = _skill(monkeypatch, "👍", CART)
    out = await skill.run(SkillInput(question="Thanks!", tenant_id="off-the-hook",
                                     metadata={"customer_phone": "27645755210"}))
    assert out.answer == "👍"


@pytest.mark.asyncio
async def test_bare_emoji_with_empty_cart_asks_what_to_order(monkeypatch):
    skill = _skill(monkeypatch, "🙂", {"items": []})
    out = await skill.run(SkillInput(question="Ok", tenant_id="off-the-hook",
                                     metadata={"customer_phone": "27645755210"}))
    assert out.answer.startswith("What would you like to order?")


# ── 3. Recipes build on what's in the cart, orderables listed once ──────────
@pytest.mark.asyncio
async def test_recipe_uses_the_cart_product(monkeypatch):
    seen = {}

    async def fake_completion(**kw):
        seen["prompt"] = kw["messages"][0]["content"]
        msg = type("M", (), {"content": "1. *Pan-fried Hake Centre Cuts* — ...\n2. Baked Hake Centre Cuts — ..."})
        return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    import litellm
    monkeypatch.setattr(litellm, "acompletion", fake_completion)
    monkeypatch.setattr("core.skills.commerce_assistant.resolve_generation_route",
                        AsyncMock(return_value=("m", None, None)))
    with patch("core.skills.commerce_assistant.service.list_products",
               new=AsyncMock(return_value=[FILLETS, CENTRE_CUTS])), \
         patch("core.skills.web_search._ddg_search", new=AsyncMock(return_value=[])), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline.query", new=AsyncMock(return_value=[])):
        out = await CommerceAssistantSkill()._exec_suggest_recipe(
            "off-the-hook", {"dish": "hake", "count": 2}, cart_names=["Hake Centre Cuts"])
    assert "already has in their cart: Hake Centre Cuts" in seen["prompt"]
    assert "Do NOT add an 'Available to order' line to each recipe" in seen["prompt"]
    assert out["available_to_order"][0] == {"name": "Hake Centre Cuts", "slug": "hake-centre-cuts",
                                            "price": "R220.00", "in_cart": True}
    assert "ONCE" in out["tip"]
