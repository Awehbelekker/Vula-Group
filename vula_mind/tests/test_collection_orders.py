"""Collection (pickup) orders: opt-in per shop, no address, no delivery fee (migration 177)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.commerce_assistant import CommerceAssistantSkill
from vula.commerce import service

CART = {"id": "c1", "delivery_cents": 8000, "commerce_cart_items": [
    {"product_id": "p1", "quantity": 1, "unit_price_cents": 10000, "commerce_products": {"name": "Hake"}}]}


@pytest.mark.asyncio
async def test_collection_refused_when_shop_does_not_offer_it():
    skill = CommerceAssistantSkill()
    with patch("vula.commerce.order_workflow.get_order_settings", return_value={"collection_enabled": False}), \
         patch.object(service, "get_or_create_cart", AsyncMock(return_value=CART)):
        out = await skill._exec_review_order("t1", "s1", "2782", {"fulfilment": "collection"})
    assert "error" in out and "collection" in out["error"]


@pytest.mark.asyncio
async def test_collection_review_has_no_delivery_fee_or_address_requirement():
    skill = CommerceAssistantSkill()
    with patch("vula.commerce.order_workflow.get_order_settings",
               return_value={"collection_enabled": True, "collection_note": "Shop, 12 Main Rd",
                             "delivery_fee_cents": 8000}), \
         patch.object(service, "get_or_create_cart", AsyncMock(return_value=CART)):
        out = await skill._exec_review_order("t1", "s1", "2782",
                                             {"fulfilment": "collection", "payment_method": "cod"})
    assert "Delivery: R0.00" in out["preview"] and "12 Main Rd" in out["preview"]
    assert "delivery address" not in out["still_needed"]


@pytest.mark.asyncio
async def test_create_order_charges_no_delivery_for_collection():
    db = MagicMock()
    q = db.table.return_value
    for m in ("select", "eq", "gte", "order", "limit", "insert", "update", "delete"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=[{"id": "o1", "total_cents": 10000}])
    db.rpc.return_value.execute.return_value = MagicMock(data=True)
    with patch.object(service, "_client", return_value=db), \
         patch.object(service, "_next_order_display_id", AsyncMock(return_value="OTH-1")), \
         patch.object(service, "_attribute_broadcast", AsyncMock(return_value=None)), \
         patch.object(service, "_reserve_cart_stock", AsyncMock()), \
         patch.object(service, "clear_cart", AsyncMock()):
        await service.create_order("t1", CART, {"customer_phone": "2782", "customer_name": "Jo",
                                                "delivery_address": "Collection", "fulfilment": "collection"})
    inserted = q.insert.call_args_list[0].args[0]
    assert inserted["delivery_cents"] == 0 and inserted["total_cents"] == 10000
