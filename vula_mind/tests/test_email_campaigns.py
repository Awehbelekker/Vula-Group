"""Tests for bulk/campaign email — the fifth gap closed from the marketing capability
audit ("no bulk/campaign sending, no subscriber list... for email anywhere"). Covers the
SMTP batch-send helper (one connection for the whole campaign, vula/email_imap/service.py),
the send/preview endpoint (reusing WhatsApp broadcast's exact audience targeting — patched
here rather than re-mocked, since _aggregate_customers/_filter_audience are already tested
elsewhere), and the public unsubscribe link."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.commerce import admin_send_email_campaign, _suppressed_emails
from vula.api.email_public import unsubscribe
from vula.email_imap.service import _send_batch, send_batch

_CREDS = {"email": "shop@example.com", "password": "secret",
         "smtp_host": "smtp.example.com", "smtp_port": 465, "from_name": "The Shop"}


# ── send_batch (SMTP) ───────────────────────────────────────────────────────────

def test_send_batch_no_smtp_host_fails_all_without_connecting():
    creds = {"email": "a@example.com", "password": "x"}
    results = _send_batch(creds, [{"to": "a@b.com", "subject": "s", "body": "b"}])
    assert results == [{"to": "a@b.com", "sent": False, "error": "no SMTP host configured"}]


def test_send_batch_reuses_one_connection_for_multiple_messages():
    mock_smtp = MagicMock()
    messages = [
        {"to": "a@b.com", "subject": "s1", "body": "b1"},
        {"to": "c@d.com", "subject": "s2", "body": "b2"},
    ]
    with patch("smtplib.SMTP_SSL", return_value=mock_smtp):
        results = _send_batch(_CREDS, messages)

    assert results == [{"to": "a@b.com", "sent": True}, {"to": "c@d.com", "sent": True}]
    mock_smtp.login.assert_called_once_with("shop@example.com", "secret")
    assert mock_smtp.send_message.call_count == 2
    mock_smtp.quit.assert_called_once()


def test_send_batch_one_recipient_failure_does_not_abort_the_rest():
    mock_smtp = MagicMock()
    mock_smtp.send_message.side_effect = [Exception("mailbox full"), None]
    messages = [
        {"to": "bad@b.com", "subject": "s1", "body": "b1"},
        {"to": "ok@d.com", "subject": "s2", "body": "b2"},
    ]
    with patch("smtplib.SMTP_SSL", return_value=mock_smtp):
        results = _send_batch(_CREDS, messages)

    assert results[0]["sent"] is False and "mailbox full" in results[0]["error"]
    assert results[1]["sent"] is True


def test_send_batch_login_failure_fails_all_messages():
    mock_smtp = MagicMock()
    mock_smtp.login.side_effect = Exception("bad password")
    with patch("smtplib.SMTP_SSL", return_value=mock_smtp):
        results = _send_batch(_CREDS, [{"to": "a@b.com", "subject": "s", "body": "b"}])
    assert results == [{"to": "a@b.com", "sent": False, "error": "bad password"}]


@pytest.mark.asyncio
async def test_send_batch_async_wrapper_delegates():
    with patch("vula.email_imap.service._send_batch", return_value=[{"to": "a@b.com", "sent": True}]) as mock_sb:
        result = await send_batch(_CREDS, [{"to": "a@b.com", "subject": "s", "body": "b"}])
    assert result == [{"to": "a@b.com", "sent": True}]
    mock_sb.assert_called_once()


# ── admin_send_email_campaign ───────────────────────────────────────────────────

_CUSTOMERS = {
    "27821111111": {"phone": "27821111111", "email": "jane@example.com", "name": "Jane",
                    "consent": "opted_in", "total_spent_cents": 0, "orders": 0},
    "27822222222": {"phone": "27822222222", "email": "", "name": "No Email",
                    "consent": "opted_in", "total_spent_cents": 0, "orders": 0},
    "27823333333": {"phone": "27823333333", "email": "unsubbed@example.com", "name": "Unsubbed",
                    "consent": "opted_in", "total_spent_cents": 0, "orders": 0},
}


def _consent_mock_db(suppressed_emails):
    consent_table = MagicMock()
    consent_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"email": e} for e in suppressed_emails])
    campaigns_table = MagicMock()
    recipients_table = MagicMock()

    def _table(name):
        if name == "commerce_email_consent":
            return consent_table
        if name == "commerce_email_campaigns":
            return campaigns_table
        return recipients_table

    mock_db = MagicMock()
    mock_db.table.side_effect = _table
    return mock_db, campaigns_table, recipients_table


@pytest.mark.asyncio
async def test_dry_run_excludes_no_email_and_suppressed_customers():
    mock_db, _, _ = _consent_mock_db(["unsubbed@example.com"])
    with (
        patch("vula.api.commerce.service._client", return_value=mock_db),
        patch("vula.api.commerce._aggregate_customers", new=AsyncMock(return_value=_CUSTOMERS)),
    ):
        result = await admin_send_email_campaign("off-the-hook", {
            "subject": "Specials", "body": "Check out this week's catch", "dry_run": True})

    assert result["dry_run"] is True
    assert result["recipient_count"] == 1
    assert result["sample"][0]["email"] == "jane@example.com"
    assert result["suppressed_count"] == 1


@pytest.mark.asyncio
async def test_live_send_appends_unsubscribe_footer_and_logs_campaign():
    mock_db, campaigns_table, recipients_table = _consent_mock_db([])
    with (
        patch("vula.api.commerce.service._client", return_value=mock_db),
        patch("vula.api.commerce._aggregate_customers", new=AsyncMock(return_value={
            "27821111111": _CUSTOMERS["27821111111"]})),
        patch("vula.email_imap.credentials.get_email_creds", return_value=_CREDS),
        patch("vula.email_imap.service.send_batch",
              new=AsyncMock(return_value=[{"to": "jane@example.com", "sent": True}])) as mock_send,
    ):
        result = await admin_send_email_campaign("off-the-hook", {
            "subject": "Specials", "body": "Check out this week's catch", "dry_run": False})

    assert result["sent"] == 1
    assert result["failed"] == 0
    sent_messages = mock_send.call_args[0][1]
    assert "unsubscribe" in sent_messages[0]["body"].lower()
    assert "Check out this week's catch" in sent_messages[0]["body"]
    campaigns_table.insert.assert_called_once()
    recipients_table.insert.assert_called_once()


@pytest.mark.asyncio
async def test_test_email_skips_audience_and_suppression():
    mock_db, _, _ = _consent_mock_db(["unsubbed@example.com"])
    with (
        patch("vula.api.commerce.service._client", return_value=mock_db),
        patch("vula.api.commerce._aggregate_customers", new=AsyncMock(return_value=_CUSTOMERS)),
        patch("vula.email_imap.credentials.get_email_creds", return_value=_CREDS),
        patch("vula.email_imap.service.send_batch",
              new=AsyncMock(return_value=[{"to": "me@test.com", "sent": True}])) as mock_send,
    ):
        result = await admin_send_email_campaign("off-the-hook", {
            "subject": "Specials", "body": "Hello", "test_email": "me@test.com", "dry_run": False})

    assert result["sent"] == 1
    to_addrs = [m["to"] for m in mock_send.call_args[0][1]]
    assert to_addrs == ["me@test.com"]


@pytest.mark.asyncio
async def test_no_email_account_connected_raises_503():
    mock_db, _, _ = _consent_mock_db([])
    with (
        patch("vula.api.commerce.service._client", return_value=mock_db),
        patch("vula.api.commerce._aggregate_customers", new=AsyncMock(return_value=_CUSTOMERS)),
        patch("vula.email_imap.credentials.get_email_creds", return_value=None),
    ):
        with pytest.raises(Exception, match="No email account connected"):
            await admin_send_email_campaign("off-the-hook", {
                "subject": "Specials", "body": "Hello", "dry_run": False})


@pytest.mark.asyncio
async def test_missing_subject_or_body_returns_400():
    with pytest.raises(Exception):
        await admin_send_email_campaign("off-the-hook", {"subject": "", "body": "Hello"})


# ── consent / suppression ───────────────────────────────────────────────────────

def test_suppressed_emails_lowercases_and_normalizes():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"email": "Jane@Example.com"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch("vula.api.commerce.service._client", return_value=mock_db):
        result = _suppressed_emails("off-the-hook")
    assert result == {"jane@example.com"}


def test_suppressed_emails_returns_empty_set_on_error():
    with patch("vula.api.commerce.service._client", side_effect=RuntimeError("down")):
        assert _suppressed_emails("off-the-hook") == set()


# ── public unsubscribe endpoint ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unsubscribe_upserts_opted_out():
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch("vula.api.email_public._client", return_value=mock_db):
        resp = await unsubscribe(tenant="off-the-hook", email="Jane@Example.com")

    assert resp.status_code == 200
    mock_table.upsert.assert_called_once()
    row, kwargs = mock_table.upsert.call_args[0][0], mock_table.upsert.call_args[1]
    assert row["email"] == "jane@example.com"
    assert row["status"] == "opted_out"
    assert kwargs["on_conflict"] == "tenant_id,email"


@pytest.mark.asyncio
async def test_unsubscribe_rejects_invalid_email():
    resp = await unsubscribe(tenant="off-the-hook", email="not-an-email")
    assert resp.status_code == 200   # still renders a page, just an error one
    assert b"Invalid" in resp.body
