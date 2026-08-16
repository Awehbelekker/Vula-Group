"""Tests for the ecommerce chat wire-ups added to commerce_assistant.py: discount-code
preview/redemption, customer subscription self-management, and the reorder-last shortcut."""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.commerce_assistant import CommerceAssistantSkill

TENANT = "off-the-hook"
CTX = {"tenant_id": TENANT, "session_id": "+27821234567", "customer_phone": "+27821234567"}

_CART_ITEM = {"quantity": 2, "unit_price_cents": 5000,
              "commerce_products": {"name": "Hake Fillets", "pricing_mode": "fixed"}}


# ── discount code in review_order ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_order_with_valid_discount_code():
    skill = CommerceAssistantSkill()
    cart = {"commerce_cart_items": [_CART_ITEM], "delivery_cents": 8000}
    resolved = {"code_row": {"code": "WEEKEND10"}, "discount_cents": 1000, "free_shipping": False}
    with (
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value=cart)),
        patch("core.skills.commerce_assistant.service.resolve_discount_code", new=AsyncMock(return_value=resolved)),
    ):
        out = await skill._dispatch_tool("review_order", {
            "payment_method": "cod", "delivery_address": "1 Main Rd", "discount_code": "weekend10"}, CTX)
    assert "Discount (WEEKEND10): -R10.00" in out["preview"]
    assert "Total: R170.00" in out["preview"]  # 100 subtotal + 80 delivery - 10 discount


@pytest.mark.asyncio
async def test_review_order_with_invalid_discount_code():
    skill = CommerceAssistantSkill()
    cart = {"commerce_cart_items": [_CART_ITEM], "delivery_cents": 8000}
    from vula.commerce.service import DiscountError
    with (
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value=cart)),
        patch("core.skills.commerce_assistant.service.resolve_discount_code",
              new=AsyncMock(side_effect=DiscountError("'NOPE' isn't a valid code."))),
    ):
        out = await skill._dispatch_tool("review_order", {
            "payment_method": "cod", "delivery_address": "1 Main Rd", "discount_code": "NOPE"}, CTX)
    assert "isn't a valid code" in out["preview"]
    assert "Total: R180.00" in out["preview"]  # full price, discount not applied


@pytest.mark.asyncio
async def test_review_order_without_discount_code_unaffected():
    skill = CommerceAssistantSkill()
    cart = {"commerce_cart_items": [_CART_ITEM], "delivery_cents": 8000}
    with patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value=cart)):
        out = await skill._dispatch_tool("review_order", {
            "payment_method": "cod", "delivery_address": "1 Main Rd"}, CTX)
    assert "Discount" not in out["preview"]
    assert "Total: R180.00" in out["preview"]


# ── discount code passthrough in place_order ──────────────────────────────

@pytest.mark.asyncio
async def test_place_order_passes_discount_code_to_create_order():
    skill = CommerceAssistantSkill()
    cart = {"id": "c1", "commerce_cart_items": [_CART_ITEM]}
    order = {"id": "o1", "display_id": "OTH-00099", "total_cents": 9800,
              "subtotal_cents": 10000, "delivery_cents": 8000}
    create_order_mock = AsyncMock(return_value=order)
    with (
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value=cart)),
        patch("core.skills.commerce_assistant.service.create_order", new=create_order_mock),
        patch("vula.commerce.onboarding.get_contact", return_value=None),
    ):
        await skill._exec_place_order(TENANT, CTX["session_id"], CTX["customer_phone"], {
            "payment_method": "cod", "delivery_address": "1 Main Rd", "discount_code": "weekend10",
        }, ctx={"current_message": "CONFIRM"})
    _, checkout_data = create_order_mock.call_args.args[0], create_order_mock.call_args.args[2]
    assert checkout_data["discount_code"] == "weekend10"


@pytest.mark.asyncio
async def test_place_order_no_discount_code_passes_none():
    skill = CommerceAssistantSkill()
    cart = {"id": "c1", "commerce_cart_items": [_CART_ITEM]}
    order = {"id": "o1", "display_id": "OTH-00099", "total_cents": 18000,
              "subtotal_cents": 10000, "delivery_cents": 8000}
    create_order_mock = AsyncMock(return_value=order)
    with (
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value=cart)),
        patch("core.skills.commerce_assistant.service.create_order", new=create_order_mock),
        patch("vula.commerce.onboarding.get_contact", return_value=None),
    ):
        await skill._exec_place_order(TENANT, CTX["session_id"], CTX["customer_phone"], {
            "payment_method": "cod", "delivery_address": "1 Main Rd",
        }, ctx={"current_message": "CONFIRM"})
    checkout_data = create_order_mock.call_args.args[2]
    assert checkout_data["discount_code"] is None


# ── subscription self-management ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_my_subscriptions_none_found():
    skill = CommerceAssistantSkill()
    with patch("vula.commerce.subscriptions.list_subs", new=AsyncMock(return_value=[])):
        out = await skill._dispatch_tool("list_my_subscriptions", {}, CTX)
    assert "message" in out


@pytest.mark.asyncio
async def test_list_my_subscriptions_found():
    skill = CommerceAssistantSkill()
    rows = [{"cadence": "weekly", "status": "active", "next_run": "2026-08-20",
             "items": [{"product_name": "Hake Fillets"}]}]
    with patch("vula.commerce.subscriptions.list_subs", new=AsyncMock(return_value=rows)):
        out = await skill._dispatch_tool("list_my_subscriptions", {}, CTX)
    assert out["subscriptions"][0]["cadence"] == "weekly"
    assert out["subscriptions"][0]["items"] == ["Hake Fillets"]


@pytest.mark.asyncio
async def test_cancel_subscription_none_found():
    skill = CommerceAssistantSkill()
    with patch("vula.commerce.subscriptions.list_subs", new=AsyncMock(return_value=[])):
        out = await skill._dispatch_tool("cancel_subscription", {}, CTX)
    assert "error" in out


@pytest.mark.asyncio
async def test_cancel_subscription_success():
    skill = CommerceAssistantSkill()
    rows = [{"id": "s1", "cadence": "weekly", "status": "active"}]
    set_status_mock = AsyncMock()
    with (
        patch("vula.commerce.subscriptions.list_subs", new=AsyncMock(return_value=rows)),
        patch("vula.commerce.subscriptions.set_status", new=set_status_mock),
    ):
        out = await skill._dispatch_tool("cancel_subscription", {}, CTX)
    assert out["updated"] is True and out["status"] == "cancelled"
    set_status_mock.assert_awaited_once_with(TENANT, "s1", "cancelled")


@pytest.mark.asyncio
async def test_pause_subscription_success():
    skill = CommerceAssistantSkill()
    rows = [{"id": "s1", "cadence": "weekly", "status": "active"}]
    set_status_mock = AsyncMock()
    with (
        patch("vula.commerce.subscriptions.list_subs", new=AsyncMock(return_value=rows)),
        patch("vula.commerce.subscriptions.set_status", new=set_status_mock),
    ):
        out = await skill._dispatch_tool("pause_subscription", {}, CTX)
    assert out["status"] == "paused"
    set_status_mock.assert_awaited_once_with(TENANT, "s1", "paused")


@pytest.mark.asyncio
async def test_cancel_subscription_ambiguous_multiple():
    skill = CommerceAssistantSkill()
    rows = [{"id": "s1", "cadence": "weekly", "status": "active"},
            {"id": "s2", "cadence": "monthly", "status": "paused"}]
    with patch("vula.commerce.subscriptions.list_subs", new=AsyncMock(return_value=rows)):
        out = await skill._dispatch_tool("cancel_subscription", {}, CTX)
    assert "error" in out and "more than one" in out["error"]


# ── reorder_last ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reorder_last_no_prior_order():
    skill = CommerceAssistantSkill()
    with patch("core.skills.commerce_assistant.service.reorder_from_last_order",
               new=AsyncMock(side_effect=ValueError("no previous order found to repeat"))):
        out = await skill._dispatch_tool("reorder_last", {}, CTX)
    assert "error" in out


@pytest.mark.asyncio
async def test_reorder_last_adds_available_items_and_skips_unavailable():
    skill = CommerceAssistantSkill()
    last = {"display_id": "OTH-00050", "items": [
        {"product_id": "p1", "product_name": "Hake Fillets", "quantity": 2, "variant_id": None},
        {"product_id": "p2", "product_name": "Discontinued Fish", "quantity": 1, "variant_id": None},
    ]}
    products = {"p1": {"id": "p1", "name": "Hake Fillets", "in_stock": True},
                "p2": None}
    add_mock = AsyncMock()
    with (
        patch("core.skills.commerce_assistant.service.reorder_from_last_order", new=AsyncMock(return_value=last)),
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value={"id": "c1"})),
        patch("core.skills.commerce_assistant.service.get_product",
              new=AsyncMock(side_effect=lambda tid, pid: products.get(pid))),
        patch("core.skills.commerce_assistant.service.add_to_cart", new=add_mock),
    ):
        out = await skill._dispatch_tool("reorder_last", {}, CTX)
    assert out["repeated_order"] == "OTH-00050"
    assert out["items_added"] == ["Hake Fillets"]
    assert out["skipped"] == ["Discontinued Fish"]
    add_mock.assert_awaited_once_with(TENANT, "c1", "p1", 2, variant_id=None)


@pytest.mark.asyncio
async def test_reorder_last_nothing_available():
    skill = CommerceAssistantSkill()
    last = {"display_id": "OTH-00050", "items": [
        {"product_id": "p2", "product_name": "Discontinued Fish", "quantity": 1, "variant_id": None}]}
    with (
        patch("core.skills.commerce_assistant.service.reorder_from_last_order", new=AsyncMock(return_value=last)),
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value={"id": "c1"})),
        patch("core.skills.commerce_assistant.service.get_product", new=AsyncMock(return_value=None)),
    ):
        out = await skill._dispatch_tool("reorder_last", {}, CTX)
    assert "error" in out
