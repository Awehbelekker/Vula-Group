"""Urgent alerts reach someone (2026-10-26): nobody at the three live businesses had subscribed to
the bank-details warning, and an unanswered Off the Hook customer was handed to a dead n8n."""
from unittest.mock import AsyncMock, patch

import pytest

from vula.integrations import notify

MEMBERS = [
    {"whatsapp": "+27 82 000 0001", "role": "owner", "notify": ["new_invoice"]},
    {"whatsapp": "+27 82 000 0002", "role": "staff", "notify": []},
]


def test_an_urgent_alert_nobody_subscribed_to_goes_to_the_owner():
    with patch.object(notify, "_members", return_value=MEMBERS):
        assert notify.recipients_for("oth", "bank_details_changed") == ["27820000001"]
        assert notify.recipients_for("oth", "customer_unanswered") == ["27820000001"]


def test_an_ordinary_event_nobody_subscribed_to_still_goes_to_no_one():
    with patch.object(notify, "_members", return_value=MEMBERS):
        assert notify.recipients_for("oth", "followup_digest") == []


def test_a_subscriber_gets_an_urgent_alert_instead_of_the_owner():
    members = MEMBERS + [{"whatsapp": "27820000003", "role": "bookkeeper", "notify": ["bank_details_changed"]}]
    with patch.object(notify, "_members", return_value=members):
        assert notify.recipients_for("oth", "bank_details_changed") == ["27820000003"]


@pytest.mark.asyncio
async def test_an_unanswered_customer_is_told_honestly_and_the_team_is_told():
    from vula.api import whatsapp as wa
    with patch("vula.integrations.notify.notify_team", new=AsyncMock(return_value=1)) as nt, \
         patch.object(wa, "_send_reply", new=AsyncMock(return_value=True)) as reply:
        await wa._hand_to_team("27821234567", "Do you have kingklip?", "wamid.1", "off-the-hook")
    args, kwargs = nt.await_args
    assert args[1] == "customer_unanswered" and "Do you have kingklip?" in args[2]
    assert "+27821234567" in args[2] and kwargs["idem_key"] == "unanswered:wamid.1"
    assert "passed your message to the team" in reply.await_args.args[1]


def test_no_n8n_left_in_the_settings():
    from config import settings
    assert not hasattr(settings, "n8n_webhook_base")
