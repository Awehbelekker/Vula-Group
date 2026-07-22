"""Tests for the self-service WhatsApp notification-preference command
(vula/integrations/notify.py:handle_preference_command) — a team member texting
"stop sending me follow-up emails" (or similar) toggles their own vula_team_members.notify
list without touching the dashboard. This is the WhatsApp half of the tenant-level
opt-in/opt-out ask; the dashboard half (VulaTeam.jsx per-event chips) already existed.

Also covers a real bug: Judy unsubscribed from followup_digest (both via the dashboard and
via this WhatsApp command) but kept getting pinged anyway. notify_team()'s "nobody
subscribed? fall back to vula_email_accounts.notify_phone" logic didn't account for that
fallback phone being the SAME person who just opted out — every event she wasn't
subscribed to silently fell back to texting her anyway."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.integrations.notify import handle_preference_command, notify_team


def _mock_db_with_member(member):
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[member])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    return mock_db, mock_table


def test_stop_follow_up_emails_removes_only_that_event():
    member = {"id": "m1", "whatsapp": "27827077080",
              "notify": ["which_project", "followup_digest", "payment_received"]}
    mock_db, mock_table = _mock_db_with_member(member)

    with patch("vula.integrations.notify._client", return_value=mock_db):
        reply = handle_preference_command("digg-demo", "27827077080",
                                          "please stop sending me follow-up emails")

    assert reply is not None
    assert "follow up digest" in reply.lower() or "followup digest" in reply.lower()
    mock_table.update.assert_called_once()
    new_notify = mock_table.update.call_args[0][0]["notify"]
    assert "followup_digest" not in new_notify
    assert "which_project" in new_notify and "payment_received" in new_notify


def test_turn_on_low_stock_adds_event():
    member = {"id": "m1", "whatsapp": "27821234567", "notify": ["which_project"]}
    mock_db, mock_table = _mock_db_with_member(member)

    with patch("vula.integrations.notify._client", return_value=mock_db):
        reply = handle_preference_command("off-the-hook", "27821234567", "turn on low stock alerts")

    assert reply is not None
    new_notify = mock_table.update.call_args[0][0]["notify"]
    assert "low_stock" in new_notify
    assert "which_project" in new_notify


def test_non_team_member_is_ignored():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.integrations.notify._client", return_value=mock_db):
        reply = handle_preference_command("digg-demo", "27000000000", "stop follow-up emails")

    assert reply is None
    mock_table.update.assert_not_called()


def test_unrelated_message_from_team_member_is_ignored():
    member = {"id": "m1", "whatsapp": "27827077080", "notify": ["followup_digest"]}
    mock_db, mock_table = _mock_db_with_member(member)

    with patch("vula.integrations.notify._client", return_value=mock_db):
        reply = handle_preference_command("digg-demo", "27827077080", "what's the status on Bokaap?")

    assert reply is None
    mock_table.update.assert_not_called()


def test_bare_stop_is_not_treated_as_a_preference_command():
    """A bare 'stop' is the exact-match POPIA opt-out phrase, handled earlier in the
    WhatsApp pipeline (_DELETE_RE) — this function must not also react to it, since it
    has no event keyword to act on."""
    member = {"id": "m1", "whatsapp": "27827077080", "notify": ["followup_digest"]}
    mock_db, mock_table = _mock_db_with_member(member)

    with patch("vula.integrations.notify._client", return_value=mock_db):
        reply = handle_preference_command("digg-demo", "27827077080", "stop")

    assert reply is None
    mock_table.update.assert_not_called()


def _mock_db_members_and_fallback(members, fallback_phone):
    """Wires up the two distinct queries notify_team makes: vula_team_members (via
    _members) and vula_email_accounts.notify_phone (via _fallback_phone)."""
    members_table = MagicMock()
    members_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=members)
    accounts_table = MagicMock()
    accounts_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[{"notify_phone": fallback_phone}] if fallback_phone else [])

    def _table(name):
        return members_table if name == "vula_team_members" else accounts_table

    mock_db = MagicMock()
    mock_db.table.side_effect = _table
    return mock_db


@pytest.mark.asyncio
async def test_notify_team_does_not_fall_back_when_a_team_member_opted_out():
    """The bug: an unsubscribed member is the tenant's only contact, so the old fallback
    ('nobody subscribed -> text notify_phone') texted her anyway, defeating her opt-out."""
    judy = {"id": "m1", "whatsapp": "27827077080", "active": True,
            "notify": ["which_project", "payment_received"]}   # NOT followup_digest
    mock_db = _mock_db_members_and_fallback([judy], fallback_phone="27827077080")

    with (
        patch("vula.integrations.notify._client", return_value=mock_db),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        sent = await notify_team("digg-demo", "followup_digest", "urgent email digest")

    assert sent == 0
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_team_still_falls_back_when_no_team_configured():
    """Legacy tenants with no team directory at all should still reach someone."""
    mock_db = _mock_db_members_and_fallback([], fallback_phone="27821234567")

    with (
        patch("vula.integrations.notify._client", return_value=mock_db),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        sent = await notify_team("off-the-hook", "payment_received", "a payment came in")

    assert sent == 1
    mock_send.assert_awaited_once_with("27821234567", "a payment came in", tenant_id="off-the-hook")


@pytest.mark.asyncio
async def test_notify_team_sends_to_subscribed_member_not_fallback():
    judy = {"id": "m1", "whatsapp": "27827077080", "active": True,
            "notify": ["payment_received"]}
    mock_db = _mock_db_members_and_fallback([judy], fallback_phone="27827077080")

    with (
        patch("vula.integrations.notify._client", return_value=mock_db),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        sent = await notify_team("digg-demo", "payment_received", "a payment came in")

    assert sent == 1
    mock_send.assert_awaited_once()
