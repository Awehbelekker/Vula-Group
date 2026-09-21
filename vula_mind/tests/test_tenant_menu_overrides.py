"""Tests for per-tenant WhatsApp staff menu copy overrides (migration 170, vula/api/whatsapp.py)
— go-live readiness pass, Phase 3.3. _STAFF_MENU_ALWAYS_ON/_STAFF_MENU_BY_MODULE were hardcoded
Python constants; this adds a lookup-before-fallback so an operator can override a tenant's
title/command via SQL without a code deploy. Backend-only this pass — no dashboard UI."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.whatsapp import (
    _handle_admin_example_reply,
    _send_staff_capability_menu,
    _tenant_menu_overrides,
)

TID = "digg-demo"


def _mock_client(rows):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=rows)
    return db


def test_tenant_menu_overrides_returns_empty_dict_when_none_set():
    with patch("vula.commerce.service._client", return_value=_mock_client([])):
        assert _tenant_menu_overrides(TID) == {}


def test_tenant_menu_overrides_parses_rows_into_dict():
    rows = [{"menu_key": "sales", "title": "See takings", "command": "show me today's takings"}]
    with patch("vula.commerce.service._client", return_value=_mock_client(rows)):
        overrides = _tenant_menu_overrides(TID)
    assert overrides == {"sales": {"title": "See takings", "command": "show me today's takings"}}


def test_tenant_menu_overrides_fails_open_on_db_error():
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        assert _tenant_menu_overrides(TID) == {}


def test_tenant_menu_overrides_fails_open_on_non_list_response():
    db = MagicMock()  # bare MagicMock .execute().data is not a list
    with patch("vula.commerce.service._client", return_value=db):
        assert _tenant_menu_overrides(TID) == {}


@pytest.mark.asyncio
async def test_send_staff_capability_menu_uses_override_title_and_command():
    sent = {}

    async def _fake_send_wa_list(creds, number, header, body, footer, cta, sections):
        sent["sections"] = sections
        return True

    with (
        patch("vula.api.whatsapp._resolve_wa", new=AsyncMock(return_value={"token": "x"})),
        patch("vula.api.whatsapp._wa_number", return_value="+27821234567"),
        patch("vula.api.tenants.enabled_modules", return_value=[]),
        patch("vula.api.whatsapp._tenant_menu_overrides",
              return_value={"sales": {"title": "See takings", "command": "show me today's takings"}}),
        patch("vula.api.whatsapp._send_wa_list", new=_fake_send_wa_list),
    ):
        ok = await _send_staff_capability_menu("+27821234567", TID)

    assert ok is True
    rows = sent["sections"][0]["rows"]
    sales_row = next(r for r in rows if r["id"] == "admin_example:sales")
    assert sales_row["title"] == "See takings"
    assert "show me today's takings" in sales_row["description"]


@pytest.mark.asyncio
async def test_send_staff_capability_menu_falls_back_to_default_when_no_override():
    sent = {}

    async def _fake_send_wa_list(creds, number, header, body, footer, cta, sections):
        sent["sections"] = sections
        return True

    with (
        patch("vula.api.whatsapp._resolve_wa", new=AsyncMock(return_value={"token": "x"})),
        patch("vula.api.whatsapp._wa_number", return_value="+27821234567"),
        patch("vula.api.tenants.enabled_modules", return_value=[]),
        patch("vula.api.whatsapp._tenant_menu_overrides", return_value={}),
        patch("vula.api.whatsapp._send_wa_list", new=_fake_send_wa_list),
    ):
        await _send_staff_capability_menu("+27821234567", TID)

    rows = sent["sections"][0]["rows"]
    sales_row = next(r for r in rows if r["id"] == "admin_example:sales")
    assert sales_row["title"] == "Check today's sales"


@pytest.mark.asyncio
async def test_handle_admin_example_reply_runs_overridden_command():
    with (
        patch("vula.api.whatsapp._tenant_menu_overrides",
              return_value={"sales": {"title": "See takings", "command": "show me today's takings"}}),
        patch("vula.api.whatsapp._run_commerce_admin", new=AsyncMock()) as mock_run,
    ):
        await _handle_admin_example_reply("+27821234567", "admin_example:sales", TID)

    mock_run.assert_called_once_with("+27821234567", "show me today's takings", TID)


@pytest.mark.asyncio
async def test_handle_admin_example_reply_runs_default_command_when_no_override():
    with (
        patch("vula.api.whatsapp._tenant_menu_overrides", return_value={}),
        patch("vula.api.whatsapp._run_commerce_admin", new=AsyncMock()) as mock_run,
    ):
        await _handle_admin_example_reply("+27821234567", "admin_example:sales", TID)

    mock_run.assert_called_once_with("+27821234567", "what were today's sales?", TID)
