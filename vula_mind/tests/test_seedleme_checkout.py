"""SeedleMe's first WhatsApp order attempt (15 Oct 2026), replayed.

A customer claimed a web basket (1 × The Emerald Dream sink, R1,699.00) and tapped Checkout.
The tap went to the model, which handed "I'd like to check out now" to the owner as a question;
the owner's "Sure how can we help you" was relayed and learned; the customer was then promised
a payment link that SeedleMe (no card gateway, no delivery fee set) could never send; the
owner's typed "Yes" to "reuse this?" got today's sales; and the admin assistant didn't know
which customer the owner meant."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.skills import commerce_assistant as ca
from vula import escalation as esc
from vula.api import whatsapp as wa
from vula.commerce import order_workflow as ow
from vula.commerce import service

TID = "seedleme"
SINK = {"quantity": 1, "unit_price_cents": 169900,
        "commerce_products": {"name": "The Emerald Dream Bespoke Charcoal Concrete Sink 39.5 x 11.5cm",
                              "pricing_mode": "fixed"}}
CART = {"id": "c1", "delivery_cents": 8000, "commerce_cart_items": [SINK]}


@pytest.fixture
def unset_shop(monkeypatch):
    """SeedleMe today: no order settings row, no gateway, no storefront of its own."""
    monkeypatch.setattr(ow, "get_order_settings", lambda tid: dict(ow._DEFAULTS))
    monkeypatch.setattr("vula.payments.default_provider_row", lambda tid: None)
    monkeypatch.setattr(ow, "_card_payments_ready", lambda tid: False)


def test_card_is_only_offered_with_a_gateway(monkeypatch):
    monkeypatch.setattr(ow, "_card_payments_ready", lambda tid: False)
    assert ow.usable_payment_methods(TID, dict(ow._DEFAULTS)) == ["cod", "eft"]
    assert ow.usable_payment_methods(TID, {"payment_methods": ["online"]}) == ["eft"]
    monkeypatch.setattr(ow, "_card_payments_ready", lambda tid: True)
    assert ow.usable_payment_methods(TID, dict(ow._DEFAULTS)) == ["online", "cod", "eft"]
    assert ow.payment_choices_text(["cod", "eft"]) == "Pay on delivery or EFT / bank transfer"


def test_no_made_up_delivery_fee(unset_shop, monkeypatch):
    assert service.delivery_fee_cents(TID, CART, 169900) == 0
    assert service.delivery_fee_known(TID) is False
    monkeypatch.setattr(ow, "get_order_settings", lambda tid: {**ow._DEFAULTS, "delivery_fee_cents": 8000})
    assert service.delivery_fee_cents("off-the-hook", CART, 169900) == 8000
    assert service.delivery_fee_known("off-the-hook") is True


def test_checkout_prompt_asks_for_address_and_a_real_payment_method(unset_shop):
    text = ca.checkout_prompt(TID, CART)
    assert "1 × The Emerald Dream Bespoke Charcoal Concrete Sink" in text
    assert "Subtotal: R1,699.00" in text and "Delivery: R" not in text
    assert "delivery address" in text
    assert "Pay on delivery or EFT / bank transfer" in text and "Card" not in text
    assert ca.checkout_prompt(TID, {"commerce_cart_items": []}) is None


def test_start_checkout_never_promises_a_payment_link(unset_shop):
    out = ca.CommerceAssistantSkill()._exec_start_checkout(TID)
    assert "payment link" not in out["next_step"].lower()
    assert "review_order" in out["instruction_to_assistant"]
    assert out["payment_options"] == ["Pay on delivery", "EFT / bank transfer"]


@pytest.mark.asyncio
async def test_review_shows_delivery_to_be_confirmed_and_refuses_card(unset_shop):
    skill = ca.CommerceAssistantSkill()
    ctx = {"tenant_id": TID, "session_id": "27645755210", "customer_phone": "27645755210"}
    with patch.object(ca.service, "get_or_create_cart", new=AsyncMock(return_value=CART)):
        out = await skill._dispatch_tool("review_order", {
            "payment_method": "eft", "delivery_address": "22 Porterfield, Table View, Cape Town"}, ctx)
        card = await skill._dispatch_tool("review_order", {
            "payment_method": "online", "delivery_address": "22 Porterfield"}, ctx)
    assert "Delivery: the team will confirm the fee" in out["preview"]
    assert "Total: R1699.00 plus delivery" in out["preview"]
    assert "Payment: EFT / bank transfer" in out["preview"]
    assert "payment method" in card["still_needed"]


@pytest.mark.asyncio
async def test_checkout_is_not_a_question_for_the_team(unset_shop, monkeypatch):
    created = []
    monkeypatch.setattr(esc, "create_escalation", lambda *a: created.append(a) or {"helper_phone": "x"})
    monkeypatch.setattr(esc, "find_learned_answer", AsyncMock(return_value=None))
    out = await ca.CommerceAssistantSkill()._exec_ask_team(
        TID, "27645755210", {"question": "I'd like to check out now"})
    assert "review_order" in out["error"] and not created


def test_offer_of_help_is_relayed_but_never_learned():
    assert esc.reply_is_instruction_to_vula("Sure how can we help you")
    assert not esc.reply_is_instruction_to_vula("Delivery in Cape Town takes 5 to 7 working days.")


@pytest.mark.asyncio
async def test_checkout_button_sends_the_prompt_without_the_model(unset_shop, monkeypatch):
    sent, history, model = [], [], []
    monkeypatch.setattr(service, "get_or_create_cart", AsyncMock(return_value=CART))
    monkeypatch.setattr(service, "get_or_create_session", AsyncMock(return_value={"id": "s1"}))

    async def append(tid, sid, role, content):
        history.append((role, content))
    monkeypatch.setattr(service, "append_message", append)

    async def reply(to, text, tenant_id="", **k):
        sent.append(text)
        return True
    monkeypatch.setattr(wa, "_send_reply", reply)
    monkeypatch.setattr(wa, "_handle_commerce_message", AsyncMock(side_effect=lambda *a, **k: model.append(a)))
    await wa._handle_commerce_interactive("27645755210", "cart_checkout", "Checkout", "m1", TID)
    assert not model and sent and sent[0].startswith("🧾 *Checkout*")
    assert history == [("user", "[tapped Checkout]"), ("assistant", sent[0])]


@pytest.mark.asyncio
async def test_typed_yes_answers_the_reuse_question(monkeypatch):
    taps = []
    monkeypatch.setattr(wa, "_handle_learn_review_reply", AsyncMock(side_effect=lambda p, r, t: taps.append(r)))
    wa._LEARN_REVIEW_ASKED.clear()
    assert not await wa._maybe_typed_learn_review("27662545068", "Yes", TID)   # nothing asked
    wa._LEARN_REVIEW_ASKED[(TID, "27662545068")] = ("L1", wa.time.time())
    assert not await wa._maybe_typed_learn_review("27662545068", "Show me the customer", TID)
    assert await wa._maybe_typed_learn_review("+27 66 254 5068", "Yes", TID)
    assert taps == ["learn_keep:L1"]
    assert not await wa._maybe_typed_learn_review("27662545068", "No", TID)   # answered once


def test_admin_knows_which_customer_was_passed_on(monkeypatch):
    rows = [{"customer_phone": "27645755210", "question": "Contact Richard Downing on this number",
             "answer": "Rather for this business contact Matt Wilkinson", "status": "answered",
             "created_at": "2026-10-15T12:21:21+00:00"}]
    monkeypatch.setattr(esc, "recent_for_helper", lambda *a, **k: rows)
    note = esc.handoff_context("27662545068", TID)
    assert "+27645755210" in note and "Contact Richard Downing" in note and "12:21" in note
    monkeypatch.setattr(esc, "recent_for_helper", lambda *a, **k: [])
    assert esc.handoff_context("27662545068", TID) == ""
