"""Tests for vula/integrations/platform_support.py. forward()'s DB-log path was verified live
against off-the-hook during development (logged + cleaned up); this file locks in detect()'s
keyword matching (the part most likely to regress silently — a missed phrase just means a
platform question falls through to normal routing with no error) and forward()'s behavior with
a mocked DB/settings."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.integrations.platform_support import detect


@pytest.mark.parametrize("text", [
    "the vula app is broken, orders aren't coming through",
    "having an issue with vula today",
    "is there a bug in the system?",
    "who do i talk to about vula",
    "VULA SUPPORT - the dashboard wont load",
    "vula feedback: the bank tab is confusing",
])
def test_detect_positive(text):
    assert detect(text) is True


@pytest.mark.parametrize("text", [
    "do you have salmon in stock today?",
    "can I get 2kg of hake delivered tomorrow",
    "whats the total for my order",
    "thanks so much!",
    "",
    None,
])
def test_detect_negative(text):
    assert detect(text) is False


@pytest.mark.asyncio
async def test_forward_logs_even_when_support_phone_unset():
    """No platform_support_phone configured must still log durably (best-effort WhatsApp is
    the part allowed to no-op, not the DB record)."""
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    mock_settings = MagicMock(platform_support_phone="")

    with (
        patch("vula.integrations.platform_support._client", return_value=mock_db),
        patch("config.settings", mock_settings),
    ):
        from vula.integrations.platform_support import forward
        await forward("off-the-hook", "27821234567", "Staci", "the vula app is broken")

    mock_db.table.assert_called_with("vula_platform_feedback")
    mock_table.insert.assert_called_once()
    inserted = mock_table.insert.call_args[0][0]
    assert inserted["tenant_id"] == "off-the-hook"
    assert inserted["phone"] == "27821234567"
    assert inserted["message"] == "the vula app is broken"


@pytest.mark.asyncio
async def test_forward_attempts_whatsapp_when_support_phone_set():
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    mock_settings = MagicMock(platform_support_phone="27829999999")

    with (
        patch("vula.integrations.platform_support._client", return_value=mock_db),
        patch("config.settings", mock_settings),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        from vula.integrations.platform_support import forward
        await forward("off-the-hook", "27821234567", "Staci", "the vula app is broken")

    mock_send.assert_awaited_once()
    args, kwargs = mock_send.call_args
    assert args[0] == "27829999999"
    assert "Staci" in args[1] and "27821234567" in args[1]
    assert kwargs.get("tenant_id") == "off-the-hook"


@pytest.mark.asyncio
async def test_forward_never_raises_if_db_and_whatsapp_both_fail():
    mock_db = MagicMock()
    mock_db.table.side_effect = Exception("db down")
    mock_settings = MagicMock(platform_support_phone="27829999999")

    with (
        patch("vula.integrations.platform_support._client", return_value=mock_db),
        patch("config.settings", mock_settings),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(side_effect=Exception("wa down"))),
    ):
        from vula.integrations.platform_support import forward
        await forward("off-the-hook", "27821234567", "Staci", "the vula app is broken")  # must not raise
