"""Tests for vula/api/data_export.py — the POPIA "send me my data" export (go-live readiness
pass, Phase 2.1). Erasure (_handle_data_deletion) was already automated; a data *access* request
was entirely manual (email hello@vula.co.za) before this. Scope is deliberately tight: the
requester's own chat history + their own orders/invoices for one tenant, tenant- and phone-
scoped, never the tenant's full filed-document library or other customers' data. Delivery is
email (PDF attachment), never WhatsApp, per the confirmed product decision."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.data_export import (
    _digits,
    _matches_phone,
    _render_export_markdown,
    assemble_export,
    send_data_export,
)
from vula.chat.history import ChatMessage

TID = "digg-demo"
PHONE = "+27821234567"


def test_digits_strips_non_numeric():
    assert _digits("+27 82 123 4567") == "27821234567"
    assert _digits("") == ""


def test_matches_phone_suffix_matching():
    assert _matches_phone("+27821234567", "27821234567") is True
    assert _matches_phone("0821234567", "27821234567") is True  # last-9-digit suffix match
    assert _matches_phone("+27829999999", "27821234567") is False
    assert _matches_phone("", "27821234567") is False


@pytest.mark.asyncio
async def test_own_orders_scoped_to_matching_phone_only():
    from vula.api import data_export

    rows = [
        {"display_id": "ORD-1", "customer_phone": "+27821234567", "customer_email": "a@x.com",
         "total_cents": 1000, "status": "paid", "created_at": "2026-09-01T00:00:00Z"},
        {"display_id": "ORD-2", "customer_phone": "+27829999999", "customer_email": "b@x.com",
         "total_cents": 2000, "status": "paid", "created_at": "2026-09-02T00:00:00Z"},
    ]
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=rows)
    with patch("vula.commerce.service._client", return_value=mock_db):
        orders = await data_export._own_orders(TID, PHONE)

    assert len(orders) == 1
    assert orders[0]["display_id"] == "ORD-1"


@pytest.mark.asyncio
async def test_own_orders_fails_open_to_empty_list_on_db_error():
    from vula.api import data_export
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        assert await data_export._own_orders(TID, PHONE) == []


@pytest.mark.asyncio
async def test_own_orders_empty_phone_never_hits_db():
    from vula.api import data_export
    with patch("vula.commerce.service._client") as mock_client:
        assert await data_export._own_orders(TID, "") == []
    mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_own_orders_non_list_response_treated_as_empty():
    from vula.api import data_export
    mock_db = MagicMock()  # bare MagicMock .execute().data is not a list
    with patch("vula.commerce.service._client", return_value=mock_db):
        assert await data_export._own_orders(TID, PHONE) == []


@pytest.mark.asyncio
async def test_assemble_export_pulls_email_from_orders_or_invoices():
    messages = [ChatMessage(role="user", text="hi", created_at="2026-09-01T10:00:00Z", phone=PHONE, tenant_id=TID)]
    with (
        patch("vula.chat.history.get_db") as mock_get_db,
        patch("vula.api.data_export._own_orders", new=AsyncMock(return_value=[])),
        patch("vula.api.data_export._own_invoices", new=AsyncMock(return_value=[
            {"invoice_number": "INV-1", "customer_email": "client@example.com"},
        ])),
    ):
        mock_get_db.return_value.get.return_value = messages
        data = await assemble_export(TID, PHONE)

    assert data["email"] == "client@example.com"
    assert data["messages"] == messages


@pytest.mark.asyncio
async def test_assemble_export_no_email_when_none_on_orders_or_invoices():
    with (
        patch("vula.chat.history.get_db") as mock_get_db,
        patch("vula.api.data_export._own_orders", new=AsyncMock(return_value=[])),
        patch("vula.api.data_export._own_invoices", new=AsyncMock(return_value=[])),
    ):
        mock_get_db.return_value.get.return_value = []
        data = await assemble_export(TID, PHONE)

    assert data["email"] is None


def test_render_export_markdown_includes_all_sections():
    data = {
        "messages": [ChatMessage(role="user", text="hello", created_at="2026-09-01T10:00:00Z")],
        "orders": [{"display_id": "ORD-1", "total_cents": 1000, "status": "paid", "created_at": "2026-09-01"}],
        "invoices": [{"invoice_number": "INV-1", "doc_type": "invoice", "total_cents": 5000,
                     "status": "sent", "created_at": "2026-09-01"}],
    }
    md = _render_export_markdown(PHONE, data)
    assert PHONE in md
    assert "hello" in md
    assert "ORD-1" in md
    assert "INV-1" in md


def test_render_export_markdown_handles_empty_data():
    data = {"messages": [], "orders": [], "invoices": []}
    md = _render_export_markdown(PHONE, data)
    assert "No conversation history" in md
    assert "No orders" in md
    assert "No invoices" in md


@pytest.mark.asyncio
async def test_send_data_export_fails_closed_with_no_email():
    with patch("vula.api.data_export.assemble_export", new=AsyncMock(return_value={
        "messages": [], "orders": [], "invoices": [], "email": None,
    })):
        result = await send_data_export(TID, PHONE)
    assert result == {"error": "no_email_on_file"}


@pytest.mark.asyncio
async def test_send_data_export_fails_closed_on_assembly_error():
    with patch("vula.api.data_export.assemble_export", new=AsyncMock(side_effect=RuntimeError("db down"))):
        result = await send_data_export(TID, PHONE)
    assert result == {"error": "assembly_failed"}


@pytest.mark.asyncio
async def test_send_data_export_fails_closed_on_render_error():
    with (
        patch("vula.api.data_export.assemble_export", new=AsyncMock(return_value={
            "messages": [], "orders": [], "invoices": [], "email": "client@example.com",
        })),
        patch("vula.commerce.pdf.merge_branding", return_value={"name": "DIGG"}),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.pdf.render_letter_pdf", side_effect=RuntimeError("weasyprint missing")),
    ):
        result = await send_data_export(TID, PHONE)
    assert result == {"error": "render_failed"}


@pytest.mark.asyncio
async def test_send_data_export_fails_closed_when_email_send_fails():
    with (
        patch("vula.api.data_export.assemble_export", new=AsyncMock(return_value={
            "messages": [], "orders": [], "invoices": [], "email": "client@example.com",
        })),
        patch("vula.commerce.pdf.merge_branding", return_value={"name": "DIGG"}),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.pdf.render_letter_pdf", return_value=b"%PDF-1.4"),
        patch("vula.api.email.send_data_export_email", new=AsyncMock(return_value=False)),
    ):
        result = await send_data_export(TID, PHONE)
    assert result == {"error": "email_not_sent"}


@pytest.mark.asyncio
async def test_send_data_export_success():
    with (
        patch("vula.api.data_export.assemble_export", new=AsyncMock(return_value={
            "messages": [], "orders": [], "invoices": [], "email": "client@example.com",
        })),
        patch("vula.commerce.pdf.merge_branding", return_value={"name": "DIGG"}),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.pdf.render_letter_pdf", return_value=b"%PDF-1.4") as mock_render,
        patch("vula.api.email.send_data_export_email", new=AsyncMock(return_value=True)) as mock_send,
    ):
        result = await send_data_export(TID, PHONE)

    assert result == {"sent": True, "email": "client@example.com"}
    mock_render.assert_called_once()
    mock_send.assert_called_once_with("client@example.com", "DIGG", b"%PDF-1.4")
