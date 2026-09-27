"""Abandoned-cart and reorder nudges are automations: staged for owner approval, never sent
directly, recent carts only (2026-09-25 review — the old job endpoints were stubs)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import automations as au

AUTO = {"id": "a1", "action_type": "whatsapp_customer",
        "action_config": {"message": "Hi {{customer_name}}, still want {{items}}?"}}


def _iso(delta):
    return (datetime.now(timezone.utc) - delta).isoformat()


@pytest.mark.asyncio
async def test_abandoned_cart_stages_recent_carts_only():
    carts = [
        {"id": "c1", "customer_phone": "2782", "session_id": "2782", "updated_at": _iso(timedelta(hours=5)),
         "commerce_cart_items": [{"commerce_products": {"name": "Hake"}}]},
        {"id": "c2", "customer_phone": "2783", "session_id": "2783", "updated_at": _iso(timedelta(days=30)),
         "commerce_cart_items": [{"commerce_products": {"name": "Prawns"}}]},
        {"id": "c3", "customer_phone": "2784", "session_id": "manual-x", "updated_at": _iso(timedelta(hours=5)),
         "commerce_cart_items": [{"commerce_products": {"name": "Snoek"}}]},
    ]
    staged = []
    with patch("vula.commerce.service.get_abandoned_carts", AsyncMock(return_value=carts)), \
         patch.object(au, "_already_fired", return_value=False), \
         patch.object(au, "_mark_fired"), \
         patch.object(au, "_stage_firing", side_effect=lambda t, a, ctx: staged.append(ctx) or True):
        n = await au._check_abandoned_cart("t1", AUTO)
    assert n == 1 and staged[0]["customer_phone"] == "2782" and staged[0]["items"] == "Hake"


@pytest.mark.asyncio
async def test_reorder_due_stages_one_per_customer():
    rows = [{"customer_phone": "2782", "customer_name": "Jo Smith",
             "commerce_order_items": [{"product_name": "Hake"}]},
            {"customer_phone": "2782", "customer_name": "Jo Smith", "commerce_order_items": []}]
    staged = []
    with patch("vula.commerce.service.get_reorder_candidates", AsyncMock(return_value=rows)), \
         patch.object(au, "_already_fired", return_value=False), \
         patch.object(au, "_mark_fired"), \
         patch.object(au, "_stage_firing", side_effect=lambda t, a, ctx: staged.append(ctx) or True):
        assert await au._check_reorder_due("t1", AUTO) == 1
    assert staged[0]["customer_name"] == "Jo"


def test_customer_message_allowed_on_new_triggers_not_low_stock():
    ok = au._validate_parsed_rule({"trigger_type": "abandoned_cart", "action_type": "whatsapp_customer",
                                   "action_config": {"message": "Hi"}})
    assert "error" not in ok
    bad = au._validate_parsed_rule({"trigger_type": "low_stock", "action_type": "whatsapp_customer",
                                    "action_config": {"message": "Hi"}})
    assert "error" in bad


@pytest.mark.asyncio
async def test_approving_twice_sends_once():
    from unittest.mock import MagicMock
    firing = {"id": "f1", "status": "pending", "action_type": "whatsapp_customer",
              "action_config": {"message": "hi"}, "trigger_context": {"customer_phone": "2782"}}
    q = MagicMock()
    for m in ("select", "eq", "limit", "update"):
        getattr(q, m).return_value = q
    # read (pending), claim lost to the other click
    q.execute.side_effect = [MagicMock(data=[firing]), MagicMock(data=[])]
    db = MagicMock()
    db.table.return_value = q
    send = AsyncMock(return_value=True)
    with patch.object(au, "_client", return_value=db), patch.object(au, "_run_action", send):
        out = await au.approve_firing("t1", "f1")
    send.assert_not_awaited()
    assert out["status"] == "in_progress"
