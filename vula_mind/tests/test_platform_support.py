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
async def test_forward_prefers_template_and_skips_freeform_fallback():
    """The approved template works regardless of the 24h window — if it sends successfully,
    the free-form fallback (which only works within an open window) must not even be tried."""
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    mock_settings = MagicMock(platform_support_phone="27829999999")

    with (
        patch("vula.integrations.platform_support._client", return_value=mock_db),
        patch("config.settings", mock_settings),
        patch("vula.api.whatsapp._send_wa_template", new=AsyncMock(return_value=True)) as mock_template,
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        from vula.integrations.platform_support import forward, TEMPLATE_NAME
        await forward("off-the-hook", "27821234567", "Staci", "the vula app is broken")

    mock_template.assert_awaited_once()
    args, _ = mock_template.call_args
    assert args[0] == "off-the-hook"
    assert args[1] == "27829999999"
    assert args[2] == TEMPLATE_NAME
    assert "Staci" in args[3] and "27821234567" in args[3]
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_forward_falls_back_to_freeform_when_template_fails():
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    mock_settings = MagicMock(platform_support_phone="27829999999")

    with (
        patch("vula.integrations.platform_support._client", return_value=mock_db),
        patch("config.settings", mock_settings),
        patch("vula.api.whatsapp._send_wa_template", new=AsyncMock(return_value=False)),
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
        patch("vula.api.whatsapp._send_wa_template", new=AsyncMock(side_effect=Exception("template down"))),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(side_effect=Exception("wa down"))),
    ):
        from vula.integrations.platform_support import forward
        await forward("off-the-hook", "27821234567", "Staci", "the vula app is broken")  # must not raise


def _mock_httpx_get(json_data, is_success=True):
    """A patch target for httpx.AsyncClient() used as `async with ... as client: await client.get(...)`."""
    resp = MagicMock(is_success=is_success)
    resp.json.return_value = json_data
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


@pytest.mark.asyncio
async def test_ensure_template_skips_without_waba():
    with patch("vula.commerce.wa_templates._waba_creds", new=AsyncMock(return_value=None)):
        from vula.integrations.platform_support import ensure_template
        result = await ensure_template("kelp-boardbags")
    assert result == {"skipped": "no WABA connected"}


@pytest.mark.asyncio
async def test_ensure_template_reuses_existing_and_upserts_local_row():
    creds = {"waba_id": "waba1", "token": "tok"}
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with (
        patch("vula.commerce.wa_templates._waba_creds", new=AsyncMock(return_value=creds)),
        patch("httpx.AsyncClient", _mock_httpx_get({"data": [{"name": "vula_platform_feedback", "status": "APPROVED"}]})),
        patch("vula.commerce.wa_templates._client", return_value=mock_db),
        patch("vula.commerce.wa_templates.create_template", new=AsyncMock()) as mock_create,
    ):
        from vula.integrations.platform_support import ensure_template
        result = await ensure_template("digg-demo")

    assert result == {"already_exists": True, "status": "APPROVED"}
    mock_create.assert_not_awaited()
    mock_db.table.assert_called_with("commerce_wa_templates")
    mock_table.upsert.assert_called_once()
    upserted = mock_table.upsert.call_args[0][0]
    assert upserted["tenant_id"] == "digg-demo"
    assert upserted["name"] == "vula_platform_feedback"


@pytest.mark.asyncio
async def test_ensure_template_creates_when_missing():
    creds = {"waba_id": "waba1", "token": "tok"}

    with (
        patch("vula.commerce.wa_templates._waba_creds", new=AsyncMock(return_value=creds)),
        patch("httpx.AsyncClient", _mock_httpx_get({"data": []})),
        patch("vula.commerce.wa_templates.create_template",
              new=AsyncMock(return_value={"name": "vula_platform_feedback", "status": "PENDING"})) as mock_create,
    ):
        from vula.integrations.platform_support import ensure_template
        result = await ensure_template("off-the-hook")

    mock_create.assert_awaited_once()
    args, kwargs = mock_create.call_args
    assert args[0] == "off-the-hook"
    assert args[1] == "vula_platform_feedback"
    assert result["status"] == "PENDING"
