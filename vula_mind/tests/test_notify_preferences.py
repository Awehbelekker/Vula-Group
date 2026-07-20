"""Tests for the self-service WhatsApp notification-preference command
(vula/integrations/notify.py:handle_preference_command) — a team member texting
"stop sending me follow-up emails" (or similar) toggles their own vula_team_members.notify
list without touching the dashboard. This is the WhatsApp half of the tenant-level
opt-in/opt-out ask; the dashboard half (VulaTeam.jsx per-event chips) already existed."""
from unittest.mock import MagicMock, patch

from vula.integrations.notify import handle_preference_command


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
