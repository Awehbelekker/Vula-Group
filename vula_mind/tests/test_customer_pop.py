"""A customer's proof-of-payment screenshot is staged for the BUSINESS to confirm — never
confirmed by the customer, never auto-marked paid (2026-09-25 review)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import vula.api.whatsapp as wa


@pytest.mark.asyncio
async def test_customer_pop_goes_to_the_team_and_customer_is_acknowledged():
    sent = []

    async def fake_send(to, msg, tenant_id=""):
        sent.append((to, msg))
        return True

    stage = MagicMock(return_value="Looks like it matches order *OTH-7*. Reply *yes* to confirm")
    notify = AsyncMock(return_value=1)
    with patch.object(wa, "_download_media_bytes", AsyncMock(return_value=b"img")), \
         patch.object(wa, "_scan_financial_photo",
                      AsyncMock(return_value={"doc_type": "payment_confirmation", "total": "450.00"})), \
         patch("vula.commerce.bank_rec.stage_pop_for_review", stage), \
         patch("vula.integrations.notify.notify_team", notify), \
         patch.object(wa, "_send_reply", fake_send):
        handled = await wa._maybe_customer_pop("27821112222", "m1", "t1")
    assert handled is True
    assert stage.call_args.kwargs["sender_phone"] == "27821112222"
    assert "OTH-7" in notify.await_args.args[2]              # the proposal goes to the team
    assert sent and sent[0][0] == "27821112222"
    assert "Reply *yes*" not in sent[0][1]                   # the customer is never asked to confirm


@pytest.mark.asyncio
async def test_non_pop_photo_is_left_to_normal_handling():
    with patch.object(wa, "_download_media_bytes", AsyncMock(return_value=b"img")), \
         patch.object(wa, "_scan_financial_photo", AsyncMock(return_value={"doc_type": "receipt", "total": 50})):
        assert await wa._maybe_customer_pop("2782", "m1", "t1") is False
