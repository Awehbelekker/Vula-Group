"""Scheduled campaigns are claimed before sending (no double-sends); public reviews can't
overwrite another customer's order rating (2026-09-25 review)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api import commerce


def _chain(data):
    q = MagicMock()
    for m in ("select", "eq", "lte", "limit", "update", "is_", "insert"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=data)
    return q


@pytest.mark.asyncio
async def test_campaign_already_claimed_elsewhere_is_not_sent():
    camp = {"id": "c1", "tenant_id": "t1", "next_run_at": "2026-09-25T08:00:00+00:00",
            "recurrence": "weekly", "template_name": "promo"}
    q = MagicMock()
    for m in ("select", "eq", "lte", "limit", "update"):
        getattr(q, m).return_value = q
    q.execute.side_effect = [MagicMock(data=[camp]), MagicMock(data=[])]   # poll, claim lost
    db = MagicMock()
    db.table.return_value = q
    send = AsyncMock()
    with patch.object(commerce.service, "_client", return_value=db), \
         patch.object(commerce, "admin_send_broadcast", send):
        n = await commerce.process_due_campaigns()
    assert n == 0
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_from_another_number_does_not_touch_the_order():
    q = _chain([{"customer_phone": "27821112222", "review_rating": None}])
    db = MagicMock()
    db.table.return_value = q
    with patch.object(commerce.service, "_client", return_value=db):
        await commerce.create_review("t1", commerce.ReviewIn(rating=1, order_id="o1",
                                                             customer_phone="27830000000"))
    assert not any(c.args and c.args[0] == {"review_rating": 1} for c in q.update.call_args_list)


@pytest.mark.asyncio
async def test_review_from_the_customer_rates_their_order_once():
    q = _chain([{"customer_phone": "27821112222", "review_rating": None}])
    db = MagicMock()
    db.table.return_value = q
    with patch.object(commerce.service, "_client", return_value=db):
        await commerce.create_review("t1", commerce.ReviewIn(rating=5, order_id="o1",
                                                             customer_phone="0821112222"))
    assert any(c.args and c.args[0] == {"review_rating": 5} for c in q.update.call_args_list)
