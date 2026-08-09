"""Tests for the automated Yoco refund path (backlog item, 2026-08-09).

Covers: the real refund API call (vula.api.yoco.refund_yoco_payment), the security fix that
closes the unauthenticated generic webhook route for the yoco provider, the refund.succeeded/
failed webhook logging branch, and the opt-in auto_refund wiring on the order-status and
credit-note endpoints.
"""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── refund_yoco_payment ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refund_yoco_payment_no_creds():
    from vula.api.yoco import refund_yoco_payment
    with patch("vula.api.yoco._get_tenant_yoco_creds", new=AsyncMock(return_value=None)):
        result = await refund_yoco_payment("off-the-hook", "checkout_123", 5000)
    assert result["ok"] is False
    assert "connected" in result["detail"].lower()


@pytest.mark.asyncio
async def test_refund_yoco_payment_success():
    from vula.api.yoco import refund_yoco_payment
    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.json.return_value = {"id": "refund_abc"}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)

    with (
        patch("vula.api.yoco._get_tenant_yoco_creds", new=AsyncMock(return_value={"secret_key": "sk_test"})),
        patch("vula.api.yoco.httpx.AsyncClient", return_value=mock_client),
    ):
        result = await refund_yoco_payment("off-the-hook", "checkout_123", 5000)

    assert result == {"ok": True, "refund_id": "refund_abc"}
    call = mock_client.post.call_args
    assert call.args[0] == "https://payments.yoco.com/api/checkouts/checkout_123/refund"
    assert call.kwargs["json"] == {"amount": 5000}
    assert call.kwargs["headers"]["Authorization"] == "Bearer sk_test"
    assert "Idempotency-Key" in call.kwargs["headers"]


@pytest.mark.asyncio
async def test_refund_yoco_payment_rejected_by_gateway():
    from vula.api.yoco import refund_yoco_payment
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"message": "Refundable amount exceeded"}
    mock_response.text = '{"message": "Refundable amount exceeded"}'
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)

    with (
        patch("vula.api.yoco._get_tenant_yoco_creds", new=AsyncMock(return_value={"secret_key": "sk_test"})),
        patch("vula.api.yoco.httpx.AsyncClient", return_value=mock_client),
    ):
        result = await refund_yoco_payment("off-the-hook", "checkout_123", 999999)

    assert result == {"ok": False, "detail": "Refundable amount exceeded"}


@pytest.mark.asyncio
async def test_refund_yoco_payment_network_error():
    from vula.api.yoco import refund_yoco_payment
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=Exception("boom"))

    with (
        patch("vula.api.yoco._get_tenant_yoco_creds", new=AsyncMock(return_value={"secret_key": "sk_test"})),
        patch("vula.api.yoco.httpx.AsyncClient", return_value=mock_client),
    ):
        result = await refund_yoco_payment("off-the-hook", "checkout_123", 5000)

    assert result["ok"] is False
    assert "boom" in result["detail"]


# ── generic multi-gateway webhook: yoco must be rejected, others unaffected ──

@pytest.mark.asyncio
async def test_generic_payment_webhook_rejects_yoco():
    """Yoco.verify_webhook() skips HMAC by design (comment: 'handled in the existing yoco
    webhook') — so this generic route must never process yoco events at all, since a forged
    POST here could otherwise mark an invoice paid with no signature check."""
    from vula.api.payments import payment_webhook
    with patch("vula.api.payments.payments.get_provider") as mock_get_provider:
        result = await payment_webhook("off-the-hook", "yoco", None)
    assert result == {"received": True}
    mock_get_provider.assert_not_called()


@pytest.mark.asyncio
async def test_generic_payment_webhook_still_works_for_payfast():
    from vula.api.payments import payment_webhook
    fake_prov = MagicMock()
    fake_prov.verify_webhook = AsyncMock(return_value=None)
    fake_request = MagicMock()
    fake_request.body = AsyncMock(return_value=b"")
    fake_request.headers = {"content-type": "application/x-www-form-urlencoded"}
    fake_request.form = AsyncMock(return_value={})
    with (
        patch("vula.api.payments.payments.get_provider", return_value=fake_prov),
        patch("vula.api.payments.payments._client") as mock_client,
    ):
        mock_client.return_value.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.limit.return_value.execute.return_value.data = []
        result = await payment_webhook("off-the-hook", "payfast", fake_request)
    assert result == {"received": True}
    fake_prov.verify_webhook.assert_awaited_once()


# ── refund.succeeded/failed webhook: best-effort logging only ───────────────

@pytest.mark.asyncio
async def test_yoco_webhook_refund_event_logged_without_order_id():
    """Refund webhook payloads don't reliably carry order/invoice metadata (unlike payment
    events) — this must be handled as pure logging, never raise, and never require order_id."""
    from vula.api.yoco import yoco_webhook
    payload = {"type": "refund.succeeded", "payload": {"id": "refund_1", "amount": 5000, "metadata": {}}}
    fake_request = MagicMock()
    fake_request.headers = {}
    fake_request.body = AsyncMock(return_value=json.dumps(payload).encode())
    fake_request.json = AsyncMock(return_value=payload)
    with patch("vula.api.yoco.settings") as mock_settings:
        mock_settings.yoco_webhook_secret = ""
        result = await yoco_webhook(fake_request)
    assert result == {"received": True}


# ── admin_update_order_status: opt-in auto_refund ────────────────────────────

def _order(**overrides):
    order = {"id": "order1", "tenant_id": "off-the-hook", "display_id": "OTH-00001",
              "total_cents": 15000, "yoco_checkout_id": "co_123"}
    order.update(overrides)
    return order


@pytest.mark.asyncio
async def test_order_refund_not_attempted_without_auto_refund_flag():
    """Default/legacy behaviour is preserved: plain {status: 'refunded'} never calls the
    gateway — matches the existing 'opt-in step' comment this code already carried."""
    from vula.api import commerce as commerce_api
    with (
        patch.object(commerce_api.service, "get_order", new=AsyncMock(return_value=_order())),
        patch.object(commerce_api.service, "update_order_status", new=AsyncMock()),
        patch.object(commerce_api.service, "apply_order_stock", new=AsyncMock()),
        patch("vula.api.yoco.refund_yoco_payment", new=AsyncMock()) as mock_refund,
        patch("vula.integrations.notify.notify_team", new=AsyncMock()),
    ):
        result = await commerce_api.admin_update_order_status(
            "off-the-hook", "order1", {"status": "refunded"})
    assert result["refund"] is None
    mock_refund.assert_not_called()


@pytest.mark.asyncio
async def test_order_refund_auto_refund_success_updates_tracking_and_notifies():
    from vula.api import commerce as commerce_api
    mock_table = MagicMock()
    mock_client = MagicMock()
    mock_client.table.return_value = mock_table
    with (
        patch.object(commerce_api.service, "get_order", new=AsyncMock(return_value=_order())),
        patch.object(commerce_api.service, "update_order_status", new=AsyncMock()),
        patch.object(commerce_api.service, "apply_order_stock", new=AsyncMock()),
        patch.object(commerce_api.service, "_client", return_value=mock_client),
        patch.object(commerce_api.service, "_now", return_value="2026-08-09T00:00:00Z"),
        patch("vula.api.yoco.refund_yoco_payment", new=AsyncMock(return_value={"ok": True, "refund_id": "rf_1"})) as mock_refund,
        patch("vula.integrations.notify.notify_team", new=AsyncMock()) as mock_notify,
    ):
        result = await commerce_api.admin_update_order_status(
            "off-the-hook", "order1", {"status": "refunded", "auto_refund": True})

    assert result["status"] == "refunded"
    assert result["refund"] == {"gateway": "yoco", "status": "pending", "amount_cents": 15000}
    mock_refund.assert_awaited_once_with("off-the-hook", "co_123", 15000)
    patch_body = mock_table.update.call_args.args[0]
    assert patch_body["refund_status"] == "pending"
    assert patch_body["yoco_refund_id"] == "rf_1"
    assert patch_body["refunded_amount_cents"] == 15000
    mock_notify.assert_awaited_once()
    assert "automatically via Yoco" in mock_notify.call_args.args[2]


@pytest.mark.asyncio
async def test_order_refund_auto_refund_gateway_failure_still_marks_refunded():
    """The order IS still refunded (owner's decision, stock restored) even if the automatic
    gateway call fails — failure just changes the team notification, never blocks the status
    change itself."""
    from vula.api import commerce as commerce_api
    mock_client = MagicMock()
    with (
        patch.object(commerce_api.service, "get_order", new=AsyncMock(return_value=_order())),
        patch.object(commerce_api.service, "update_order_status", new=AsyncMock()),
        patch.object(commerce_api.service, "apply_order_stock", new=AsyncMock()),
        patch.object(commerce_api.service, "_client", return_value=mock_client),
        patch.object(commerce_api.service, "_now", return_value="2026-08-09T00:00:00Z"),
        patch("vula.api.yoco.refund_yoco_payment", new=AsyncMock(return_value={"ok": False, "detail": "no funds"})),
        patch("vula.integrations.notify.notify_team", new=AsyncMock()) as mock_notify,
    ):
        result = await commerce_api.admin_update_order_status(
            "off-the-hook", "order1", {"status": "refunded", "auto_refund": True})

    assert result["status"] == "refunded"
    assert result["refund"] == {"gateway": "yoco", "status": "failed", "detail": "no funds"}
    assert "failed" in mock_notify.call_args.args[2]


@pytest.mark.asyncio
async def test_order_refund_auto_refund_skipped_when_no_checkout_id():
    """A cash/manually-marked-paid order has no yoco_checkout_id — auto_refund is silently a
    no-op (no gateway call attempted), same as if auto_refund had never been passed."""
    from vula.api import commerce as commerce_api
    with (
        patch.object(commerce_api.service, "get_order", new=AsyncMock(return_value=_order(yoco_checkout_id=None))),
        patch.object(commerce_api.service, "update_order_status", new=AsyncMock()),
        patch.object(commerce_api.service, "apply_order_stock", new=AsyncMock()),
        patch("vula.api.yoco.refund_yoco_payment", new=AsyncMock()) as mock_refund,
        patch("vula.integrations.notify.notify_team", new=AsyncMock()),
    ):
        result = await commerce_api.admin_update_order_status(
            "off-the-hook", "order1", {"status": "refunded", "auto_refund": True})
    assert result["refund"] is None
    mock_refund.assert_not_called()


# ── admin_create_credit_note: opt-in auto_refund ─────────────────────────────

@pytest.mark.asyncio
async def test_credit_note_auto_refund_success():
    from vula.api import commerce as commerce_api
    cn = {"id": "cn1", "invoice_number": "OTH-CN-00001", "total_cents": 8000}
    src_invoice = {"id": "inv1", "yoco_checkout_id": "co_999"}
    mock_table = MagicMock()
    mock_client = MagicMock()
    mock_client.table.return_value = mock_table
    with (
        patch.object(commerce_api.service, "create_credit_note", new=AsyncMock(return_value=cn)),
        patch.object(commerce_api.service, "get_invoice", new=AsyncMock(return_value=src_invoice)),
        patch.object(commerce_api.service, "_client", return_value=mock_client),
        patch.object(commerce_api.service, "_now", return_value="2026-08-09T00:00:00Z"),
        patch("vula.api.yoco.refund_yoco_payment", new=AsyncMock(return_value={"ok": True, "refund_id": "rf_2"})) as mock_refund,
    ):
        result = await commerce_api.admin_create_credit_note(
            "off-the-hook", "inv1", {"auto_refund": True})

    assert result["credit_note"] == cn
    assert result["refund"] == {"gateway": "yoco", "status": "pending", "amount_cents": 8000}
    mock_refund.assert_awaited_once_with("off-the-hook", "co_999", 8000)


@pytest.mark.asyncio
async def test_credit_note_auto_refund_no_online_payment():
    from vula.api import commerce as commerce_api
    cn = {"id": "cn1", "invoice_number": "OTH-CN-00001", "total_cents": 8000}
    src_invoice = {"id": "inv1", "yoco_checkout_id": None}
    with (
        patch.object(commerce_api.service, "create_credit_note", new=AsyncMock(return_value=cn)),
        patch.object(commerce_api.service, "get_invoice", new=AsyncMock(return_value=src_invoice)),
        patch("vula.api.yoco.refund_yoco_payment", new=AsyncMock()) as mock_refund,
    ):
        result = await commerce_api.admin_create_credit_note(
            "off-the-hook", "inv1", {"auto_refund": True})

    assert result["refund"]["status"] == "failed"
    assert "no online" in result["refund"]["detail"].lower()
    mock_refund.assert_not_called()


@pytest.mark.asyncio
async def test_credit_note_without_auto_refund_flag_unchanged():
    from vula.api import commerce as commerce_api
    cn = {"id": "cn1", "invoice_number": "OTH-CN-00001", "total_cents": 8000}
    with (
        patch.object(commerce_api.service, "create_credit_note", new=AsyncMock(return_value=cn)),
        patch("vula.api.yoco.refund_yoco_payment", new=AsyncMock()) as mock_refund,
    ):
        result = await commerce_api.admin_create_credit_note("off-the-hook", "inv1", {})

    assert result == {"credit_note": cn, "refund": None}
    mock_refund.assert_not_called()
