"""Tests for vula/commerce/service.py:send_order_invoice — auto-generates an invoice for a
just-placed order and WhatsApps it to the customer (the invoice doubles as the payment
request, not a post-payment receipt). Mocks the collaborator service functions directly
(get_order/create_invoice/update_invoice_status are already tested elsewhere) so these
tests focus on send_order_invoice's own orchestration: order->line-item mapping, the
delivery line, and the create-first-then-best-effort-send/status-update sequencing."""
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce.service import send_order_invoice

_ORDER = {
    "id": "order1", "display_id": "OTH-00042", "tenant_id": "off-the-hook",
    "customer_name": "Jane Client", "customer_phone": "27821234567",
    "customer_email": "jane@example.com", "delivery_address": "12 Main Rd",
    "delivery_cents": 8000,
    "commerce_order_items": [
        {"product_id": "p1", "product_name": "Yellowtail 1kg", "quantity": 2,
         "unit_price_cents": 15000, "total_cents": 30000},
    ],
}
_INVOICE = {"id": "inv1", "invoice_number": "OTH-INV-00001", "total_cents": 38000,
           "customer_name": "Jane Client"}


def _patches(order=_ORDER, invoice=_INVOICE, send_result=True, render_raises=False):
    p = [
        patch("vula.commerce.service.get_order", new=AsyncMock(return_value=order)),
        patch("vula.commerce.service.create_invoice", new=AsyncMock(return_value=invoice)),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.service.update_invoice_status", new=AsyncMock(return_value={})),
        patch("vula.commerce.pdf.merge_branding", return_value={"name": "Off the Hook"}),
        patch("vula.api.whatsapp._send_invoice_document", new=AsyncMock(return_value=send_result)),
    ]
    if render_raises:
        p.append(patch("vula.commerce.pdf.render_invoice_pdf", side_effect=RuntimeError("no weasyprint")))
    else:
        p.append(patch("vula.commerce.pdf.render_invoice_pdf", return_value=b"%PDF-1.4 fake"))
    return p


@pytest.mark.asyncio
async def test_happy_path_creates_invoice_with_delivery_line_and_sends():
    patches = _patches()
    with patches[0], patches[1] as mock_create, patches[2], patches[3] as mock_status, \
         patches[4], patches[5] as mock_send, patches[6]:
        result = await send_order_invoice("off-the-hook", "order1")

    assert result == _INVOICE
    mock_create.assert_awaited_once()
    data = mock_create.call_args[0][1]
    assert data["order_id"] == "order1"
    assert data["customer_phone"] == "27821234567"
    line_items = data["line_items"]
    assert len(line_items) == 2
    assert line_items[0]["description"] == "Yellowtail 1kg"
    assert line_items[0]["quantity"] == 2
    assert line_items[1]["description"] == "Delivery"
    assert line_items[1]["unit_price_cents"] == 8000

    mock_send.assert_awaited_once()
    assert mock_send.call_args[0][0] == "27821234567"
    mock_status.assert_awaited_once_with("off-the-hook", "inv1", "sent")


@pytest.mark.asyncio
async def test_no_delivery_fee_omits_delivery_line():
    order = {**_ORDER, "delivery_cents": 0}
    patches = _patches(order=order)
    with patches[0], patches[1] as mock_create, patches[2], patches[3], \
         patches[4], patches[5], patches[6]:
        await send_order_invoice("off-the-hook", "order1")

    line_items = mock_create.call_args[0][1]["line_items"]
    assert len(line_items) == 1
    assert all(li["description"] != "Delivery" for li in line_items)


@pytest.mark.asyncio
async def test_no_customer_phone_skips_entirely():
    order = {**_ORDER, "customer_phone": ""}
    with (
        patch("vula.commerce.service.get_order", new=AsyncMock(return_value=order)),
        patch("vula.commerce.service.create_invoice", new=AsyncMock()) as mock_create,
    ):
        result = await send_order_invoice("off-the-hook", "order1")

    assert result is None
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_order_not_found_returns_none():
    with patch("vula.commerce.service.get_order", new=AsyncMock(return_value=None)):
        result = await send_order_invoice("off-the-hook", "order1")
    assert result is None


@pytest.mark.asyncio
async def test_pdf_render_failure_leaves_invoice_as_draft_and_does_not_raise():
    patches = _patches(render_raises=True)
    with patches[0], patches[1] as mock_create, patches[2], patches[3] as mock_status, \
         patches[4], patches[5] as mock_send, patches[6]:
        result = await send_order_invoice("off-the-hook", "order1")

    assert result == _INVOICE   # invoice was still created — accounting trail preserved
    mock_create.assert_awaited_once()
    mock_send.assert_not_awaited()
    mock_status.assert_not_awaited()   # never transitioned past draft


@pytest.mark.asyncio
async def test_whatsapp_send_failure_leaves_invoice_as_draft():
    patches = _patches(send_result=False)
    with patches[0], patches[1], patches[2], patches[3] as mock_status, \
         patches[4], patches[5] as mock_send, patches[6]:
        result = await send_order_invoice("off-the-hook", "order1")

    assert result == _INVOICE
    mock_send.assert_awaited_once()
    mock_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_invoice_creation_failure_returns_none_without_raising():
    with (
        patch("vula.commerce.service.get_order", new=AsyncMock(return_value=_ORDER)),
        patch("vula.commerce.service.create_invoice", new=AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        result = await send_order_invoice("off-the-hook", "order1")
    assert result is None
